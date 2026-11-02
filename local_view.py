# -*- coding: utf-8 -*-
# Version: 1.217
"""Per-viewport local-view snapshots and a non-rendering camera warning."""

import bpy
import blf
from bpy_extras.view3d_utils import location_3d_to_region_2d

WARNING = 'ローカルビューにアクティブカメラが含まれていないので下絵が表示されません'
_draw_handle = None


def viewport(context):
    area = getattr(context, 'area', None)
    if area is None or area.type != 'VIEW_3D':
        return None
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    if region is None:
        return None
    return area, area.spaces.active, region


def normalize(value):
    if not isinstance(value, dict) or not isinstance(value.get('enabled'), bool):
        raise ValueError('ローカルビュー状態の形式が正しくありません')
    names = value.get('objects', [])
    if not isinstance(names, list) or any(not isinstance(n, str) or not n for n in names):
        raise ValueError('ローカルビューのOBJ名一覧が正しくありません')
    return {'enabled': value['enabled'], 'objects': sorted(set(names)) if value['enabled'] else []}


def signature(value):
    if value is None:
        return None
    state = normalize(value)
    return state['enabled'], tuple(state['objects'])


def _is_member(obj, space, view_layer):
    # 4.5 keeps local-view bits on the evaluated object, not the original ID.
    # Read the existing graph; drawing/capture must not force scene reevaluation.
    if bpy.app.version < (5, 0, 0):
        obj = obj.evaluated_get(view_layer.depsgraph)
    return obj.local_view_get(space)


def capture_fields(context, enabled):
    target = viewport(context) if enabled else None
    if target is None:
        return {}
    space = target[1]
    active = space.local_view is not None
    names = sorted(obj.name for obj in context.view_layer.objects
                   if _is_member(obj, space, context.view_layer)) if active else []
    return {'local_view_state': {'enabled': active, 'objects': names}}


def record_enabled(data):
    # Before 1.217, local-view recording shared the view-layer flag.
    return bool(data.get('record_local_view_state',
                         data.get('record_view_layer_exclude_state', False) and 'local_view_state' in data))


def has_recorded_state(data):
    return isinstance(data, dict) and record_enabled(data) and isinstance(data.get('local_view_state'), dict)


def apply(context, data):
    if not has_recorded_state(data):
        return
    target = viewport(context)
    if target is None:
        return
    state = normalize(data['local_view_state'])
    area, space, region = target
    layer = context.view_layer
    wanted = set(state['objects'])
    members = [obj for obj in layer.objects if obj.name in wanted]
    if state['enabled'] and not members:
        print('[CameraPosMgr] ローカルビューの記録OBJが見つからないため、現在の表示を維持します')
        return
    active = space.local_view is not None
    if active != state['enabled']:
        # The native operator owns local-view bits and the previous global view.
        # Only seed its entry when needed, then restore selection without mode changes.
        seed = None
        if not active and not any(obj.select_get() for obj in layer.objects):
            seed = members[0]
            seed.select_set(True)
        try:
            with context.temp_override(area=area, region=region):
                result = bpy.ops.view3d.localview(frame_selected=False)
            if result != {'FINISHED'} or (space.local_view is not None) != state['enabled']:
                raise RuntimeError('ローカルビューを切り替えられませんでした')
        finally:
            if seed is not None:
                seed.select_set(False)
    if state['enabled']:
        if active:
            # Unexcluding collections can add bases to local views. Read current,
            # not cached, membership before deciding which bits need changing.
            layer.update()
        for obj in layer.objects:
            include = obj.name in wanted
            if not active or _is_member(obj, space, layer) != include:
                obj.local_view_set(space, include)
    area.tag_redraw()


def warning_camera(context):
    target = viewport(context)
    if target is None:
        return None
    space = target[1]
    rv3d = getattr(context, 'region_data', None) or space.region_3d
    if space.local_view is None or rv3d is None or rv3d.view_perspective != 'CAMERA':
        return None
    camera = space.camera if space.use_local_camera else context.scene.camera
    if camera is None or camera.type != 'CAMERA' or _is_member(camera, space, context.view_layer):
        return None
    return camera


def _wrap_text(text, width):
    lines, line = [], ''
    for char in text:
        if line and blf.dimensions(0, line + char)[0] > width:
            lines.append(line)
            line = ''
        line += char
    if line:
        lines.append(line)
    return lines


def draw_warning():
    context = bpy.context
    camera = warning_camera(context)
    if camera is None:
        return
    region, area = context.region, context.area
    if region is None or region.type != 'WINDOW':
        return
    space = context.space_data
    scale = context.preferences.system.ui_scale
    size, line_height, margin = 14 * scale, 21 * scale, 12 * scale
    try:
        blf.size(0, size)
    except TypeError:  # Blender 3.x font API.
        blf.size(0, int(size), 72)
    left, right = margin, region.width - margin
    for r in area.regions:
        if r.type in {'UI', 'TOOLS'} and r.width > 1:
            if r.x > region.x:
                right = min(right, r.x - region.x - margin)
            elif r.x + r.width < region.x + region.width:
                left = max(left, r.x + r.width - region.x + margin)
    width = max(1, right - left)
    lines = _wrap_text(WARNING, width)
    block_height = len(lines) * line_height
    rv3d = context.region_data or space.region_3d
    points = [location_3d_to_region_2d(region, rv3d, camera.matrix_world @ corner)
              for corner in camera.data.view_frame(scene=context.scene)]
    y = min((p.y for p in points if p is not None), default=0) - margin - line_height
    if y - block_height + line_height < margin or y > region.height - line_height:
        y = margin + block_height
    blf.enable(0, blf.SHADOW)
    blf.shadow(0, 5, 0, 0, 0, 1)
    blf.shadow_offset(0, 1, -1)
    blf.color(0, 1.0, 0.72, 0.25, 1.0)
    try:
        for line in lines:
            x = left + max(0, (width - blf.dimensions(0, line)[0]) * 0.5)
            blf.position(0, x, y, 0)
            blf.draw(0, line)
            y -= line_height
    finally:
        blf.disable(0, blf.SHADOW)


def register():
    global _draw_handle
    if not bpy.app.background and _draw_handle is None:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(draw_warning, (), 'WINDOW', 'POST_PIXEL')


def unregister():
    global _draw_handle
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


# Version Footer: 1.217
