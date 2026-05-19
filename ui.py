# -*- coding: utf-8 -*-
# ファイル名：ui.py
# 00漫画用Camera Position Manager
# 変更点（1.158）:
# - ラティス管理セクションの見間違い防止用に「選択中OBJ」「登録OBJ」表記を変更

import bpy

from .core import (
    get_camera_data_manager,
    rebuild_enum_cache,
    safe_basename,
    ensure_valid_saved_enum,
    _sync_scene_saved_memo,
    _safe_saved_index,
    _get_saved_item_safe,
    _get_valid_scene_camera,
    _sanitize_view3d_local_cameras,
    PANEL_LABEL,
    _ENUM_CACHE,
    get_addon_preferences,
)

from .dolly import (
    get_dolly_props,
)

from .all_object_data import (
    draw_all_object_data_controls,
)


class MPM_RecordedObjectListItemV130(bpy.types.PropertyGroup):
    object_name: bpy.props.StringProperty(name="オブジェクト名", default="")
    delete_candidate: bpy.props.BoolProperty(name="削除候補", default=False, options={'SKIP_SAVE'})


class MPM_UL_recorded_object_list_v130(bpy.types.UIList):
    bl_idname = "MPM_UL_recorded_object_list_v130"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        name = str(getattr(item, "object_name", "") or "")
        obj = bpy.data.objects.get(name) if name else None
        exists = obj is not None
        is_delete_candidate = bool(getattr(item, "delete_candidate", False))
        is_view_selected = bool(obj.select_get()) if obj is not None else False
        icon_name = 'OBJECT_DATA' if exists else 'ERROR'
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            if exists:
                op = row.operator("camera.select_recorded_object", text=name if name else "名称なし", icon=icon_name, emboss=False)
                op.object_name = name
                op.extend_selection = False
            else:
                row.label(text=name if name else "名称なし", icon=icon_name)
            if is_view_selected:
                selected_mark = row.row(align=True)
                selected_mark.enabled = False
                selected_mark.label(text="選択中", icon='RESTRICT_SELECT_OFF')
            # 追加データ記録では削除候補方式を使わないため、候補表示は行いません。
        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text="", icon=icon_name)


class MPM_SelectedObjectListItemV131(bpy.types.PropertyGroup):
    object_name: bpy.props.StringProperty(name="オブジェクト名", default="")


class MPM_UL_selected_object_list_v131(bpy.types.UIList):
    bl_idname = "MPM_UL_selected_object_list_v131"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        name = str(getattr(item, "object_name", "") or "")
        exists = bool(name) and bpy.data.objects.get(name) is not None
        icon_name = 'RESTRICT_SELECT_OFF' if exists else 'ERROR'
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            row.label(text=name if name else "名称なし", icon=icon_name)
        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text="", icon=icon_name)


class MPM_OT_recorded_object_fallback_scroll(bpy.types.Operator):
    bl_idname = "camera.recorded_object_fallback_scroll"
    bl_label = "記録済みOBJリストをスクロール"

    direction: bpy.props.IntProperty(default=0, options={'SKIP_SAVE'})

    def execute(self, context):
        wm = context.window_manager
        current = int(getattr(wm, "mpm_recorded_object_offset_v130", 0) or 0)
        setattr(wm, "mpm_recorded_object_offset_v130", max(0, current + int(self.direction)))
        return {'FINISHED'}


class MPM_OT_selected_object_fallback_scroll(bpy.types.Operator):
    bl_idname = "camera.selected_object_fallback_scroll"
    bl_label = "選択中OBJリストをスクロール"

    direction: bpy.props.IntProperty(default=0, options={'SKIP_SAVE'})

    def execute(self, context):
        wm = context.window_manager
        current = int(getattr(wm, "mpm_selected_object_offset_v131", 0) or 0)
        setattr(wm, "mpm_selected_object_offset_v131", max(0, current + int(self.direction)))
        return {'FINISHED'}


class MPM_OT_toggle_additional_data_subsection(bpy.types.Operator):
    bl_idname = "camera.toggle_additional_data_subsection"
    bl_label = "追加データ記録の小見出しを折りたたみ"

    target: bpy.props.StringProperty(default="", options={'SKIP_SAVE'})

    def execute(self, context):
        scene = context.scene
        if self.target == "selected":
            attr = "mpm_show_selected_object_data_list"
        elif self.target == "recorded":
            attr = "mpm_show_recorded_object_data_list"
        else:
            return {'CANCELLED'}
        current = bool(getattr(scene, attr, True))
        setattr(scene, attr, not current)
        return {'FINISHED'}


class MPM_OT_mark_recorded_object_delete_candidate(bpy.types.Operator):
    bl_idname = "camera.mark_recorded_object_delete_candidate"
    bl_label = "記録済みOBJから削除"
    bl_description = "選択中または一覧で選択中のOBJを、現在の保存データの記録済みOBJから即時削除します。Blender上のOBJ本体は削除しません"

    def execute(self, context):
        names_to_remove = _get_recorded_object_action_target_names(context)
        if not names_to_remove:
            self.report({'WARNING'}, "削除する記録済みOBJがありません")
            return {'CANCELLED'}
        removed = _remove_recorded_objects_from_current_stock(context, names_to_remove)
        if removed <= 0:
            self.report({'WARNING'}, "記録済みOBJから削除できる対象がありません")
            return {'CANCELLED'}
        self.report({'INFO'}, f"記録済みOBJから削除しました: {removed}")
        return {'FINISHED'}


class MPM_OT_cancel_recorded_object_delete_candidate(bpy.types.Operator):
    bl_idname = "camera.cancel_recorded_object_delete_candidate"
    bl_label = "削除取消"
    bl_description = "3Dビュー上で選択中の削除候補だけを通常状態に戻します"

    def execute(self, context):
        wm = context.window_manager
        if not hasattr(wm, "mpm_recorded_object_items_v130"):
            self.report({'WARNING'}, "記録済みOBJリストがありません")
            return {'CANCELLED'}
        collection = wm.mpm_recorded_object_items_v130
        selected_names = {
            str(obj.name)
            for obj in (getattr(context, "selected_objects", []) or [])
            if obj is not None
        }
        cancelled = 0
        for item in collection:
            name = str(getattr(item, "object_name", "") or "")
            if name and name in selected_names and bool(getattr(item, "delete_candidate", False)):
                item.delete_candidate = False
                cancelled += 1
        if cancelled <= 0:
            self.report({'WARNING'}, "選択中の削除候補OBJがありません")
            return {'CANCELLED'}
        self.report({'INFO'}, f"削除候補を取り消しました: {cancelled}")
        return {'FINISHED'}


class MPM_OT_select_all_recorded_objects(bpy.types.Operator):
    bl_idname = "camera.delete_all_recorded_objects"
    bl_label = "記録済みOBJを全削除"
    bl_description = "現在の保存データの記録済みOBJデータを空にします。摘要メモとBlender上のOBJ本体は削除しません"

    def execute(self, context):
        removed = _clear_recorded_objects_from_current_stock(context)
        if removed <= 0:
            self.report({'WARNING'}, "削除できる記録済みOBJデータがありません")
            return {'CANCELLED'}
        self.report({'INFO'}, f"記録済みOBJデータを全削除しました: {removed}")
        return {'FINISHED'}


def _get_scene_camera(context):
    camera = _get_valid_scene_camera(context.scene, repair=True)
    _sanitize_view3d_local_cameras(context, camera)
    return camera


def _draw_active_camera_warning(layout, context):
    if _get_scene_camera(context) is not None:
        return
    alert_box = layout.box()
    alert_box.alert = True
    warn = alert_box.row()
    warn.scale_y = 2.0
    warn.label(text="アクティブカメラが設定されていません", icon='ERROR')
    sub = alert_box.row()
    sub.scale_y = 1.4
    sub.label(text="カメラ一覧のカメラマークで設定してください")


def _draw_lens_control(layout, context, camera):
    layout.label(text="焦点距離")
    lens_row = layout.row()
    dz = get_dolly_props(context.scene)
    if dz is not None and dz.enabled:
        lens_row.enabled = False
    if camera and getattr(camera, "data", None):
        lens_row.prop(camera.data, 'lens', text="mm")
    else:
        lens_row.enabled = False
        lens_row.label(text="アクティブカメラが設定されていません")


def _draw_lock_camera_toggle(layout, context):
    view = getattr(context, "space_data", None)
    if view and hasattr(view, "lock_camera"):
        row = layout.row()
        row.prop(view, "lock_camera", text="カメラをビューに")


def _draw_transform_controls(layout, camera):
    if not camera:
        return
    layout.label(text="位置")
    for axis_index, axis_name in enumerate(("X", "Y", "Z")):
        row = layout.row(align=True)
        row.prop(camera, 'location', index=axis_index, text=axis_name)
        lock_row = row.row(align=True)
        lock_row.ui_units_x = 1.3
        lock_row.prop(camera, 'lock_location', index=axis_index, text="")
        zero_wrap = row.row(align=True)
        zero_wrap.alignment = 'RIGHT'
        btn = zero_wrap.row(align=True)
        btn.ui_units_x = 0.9
        op = btn.operator("camera.set_location_zero", text="0")
        op.axis = axis_name

    layout.label(text="回転")
    for axis_index, axis_name in enumerate(("X", "Y", "Z")):
        row = layout.row(align=True)
        row.prop(camera, 'rotation_euler', index=axis_index, text=axis_name)
        lock_row = row.row(align=True)
        lock_row.ui_units_x = 1.3
        lock_row.prop(camera, 'lock_rotation', index=axis_index, text="")
        snap_wrap = row.row(align=True)
        snap_wrap.alignment = 'RIGHT'
        for angle in (0, 90, 180, 270):
            btn = snap_wrap.row(align=True)
            btn.ui_units_x = 2.0
            op = btn.operator("camera.set_rotation_snap", text=str(angle))
            op.axis = axis_name
            op.angle = angle


def _draw_record_read_controls(layout, context):
    scene = context.scene
    camera = _get_scene_camera(context)
    manager = get_camera_data_manager()

    if len(_ENUM_CACHE) != len(manager.saved_camera_data):
        rebuild_enum_cache(manager)

    layout.prop(scene, "frame_current", text="現在のフレーム")

    bg_image = "No File"
    if camera and getattr(camera, "data", None) and camera.data.background_images:
        for b in camera.data.background_images:
            if b.image:
                bg_image = safe_basename(b.image.filepath)
                break
    layout.label(text=f"下絵: {bg_image}")

    layout.prop(scene.render, 'resolution_x', text="解像度 X")
    layout.prop(scene.render, 'resolution_y', text="解像度 Y")

    row = layout.row()
    row.scale_y = 2.0
    row.operator("camera.save_position", text="記録")
    row.operator("camera.delete_position", text="削除")

    row = layout.row(align=True)
    row.scale_y = 2.0
    row.operator("camera.load_background_image", text="現在のカメラへ下絵を読込")
    row.operator("camera.reload_background_image", text="再読込")

    if manager.saved_camera_data:
        layout.label(text="下絵のファイル名")
        ensure_valid_saved_enum(scene, manager)
        try:
            layout.prop(scene, 'saved_camera_index', text="")
        except Exception:
            rebuild_enum_cache(manager)
            layout.prop(scene, 'saved_camera_index', text="")

        current_data = _get_saved_item_safe(manager, _safe_saved_index(scene, manager), default={}) or {}
        _draw_saved_data_status_labels(layout, current_data)

        box = layout.box()
        row = box.row(align=True)
        row.scale_y = 1.2
        row.operator("camera.prev_saved_stock", text="<")
        sub = row.row()
        sub.alignment = 'CENTER'
        cur_idx = int(getattr(scene, "saved_camera_index", "0") or 0)
        total = len(manager.saved_camera_data)
        sub.label(text=f"{cur_idx + 1} / {total}")
        row.operator("camera.next_saved_stock", text=">")

        layout.label(text=f"ストック数: {len(manager.saved_camera_data)}")


def _saved_data_has_recorded_objects(data):
    if not isinstance(data, dict):
        return False
    if not bool(data.get('record_selected_objects', False)):
        return False
    recorded_objects = data.get('selected_objects', [])
    if not isinstance(recorded_objects, list):
        return False
    for obj_data in recorded_objects:
        name = _recorded_object_name_from_data(obj_data)
        if name:
            return True
    return False


def _saved_data_has_memo(data):
    if not isinstance(data, dict):
        return False
    memo = str(data.get('memo', '') or '').strip()
    return bool(memo)


def _draw_saved_data_status_labels(layout, current_data):
    has_obj = _saved_data_has_recorded_objects(current_data)
    has_memo = _saved_data_has_memo(current_data)
    row = layout.row(align=True)
    row.alignment = 'LEFT'

    prefix_row = row.row(align=True)
    prefix_row.alignment = 'LEFT'
    prefix_row.ui_units_x = 4.2
    prefix_row.label(text="付随データ：")

    obj_row = row.row(align=True)
    obj_row.alignment = 'LEFT'
    obj_row.ui_units_x = 6.0
    obj_row.enabled = has_obj
    obj_row.label(text="[OBJデータ有]" if has_obj else "[OBJデータ無]")

    slash_row = row.row(align=True)
    slash_row.alignment = 'LEFT'
    slash_row.ui_units_x = 0.6
    slash_row.label(text="/")

    memo_row = row.row(align=True)
    memo_row.alignment = 'LEFT'
    memo_row.ui_units_x = 4.5
    memo_row.enabled = has_memo
    memo_row.label(text="[摘要有]" if has_memo else "[摘要無]")


def _draw_saved_memo_controls(layout, context):
    scene = context.scene
    manager = get_camera_data_manager()
    saved_items = manager.saved_camera_data
    box = layout.column()
    if not saved_items:
        box.enabled = False
        box.label(text="記録データがありません")
        box.prop(scene, 'saved_memo_text', text="")
        return

    _sync_scene_saved_memo(scene, manager)
    box.prop(scene, 'saved_memo_text', text="摘要メモ")
    box.prop(scene, 'record_selected_objects', text="選択OBJデータ")
    if getattr(scene, 'record_selected_objects', False):
        names = []
        camera = _get_scene_camera(context)
        for obj in getattr(context, "selected_objects", []) or []:
            if obj == camera:
                continue
            names.append(str(obj.name))
        if names:
            _draw_current_selected_object_data_list(box, context, names)
        else:
            list_box = box.box()
            list_box.label(text="選択中OBJはありません")

    current_data = _get_saved_item_safe(manager, _safe_saved_index(scene, manager), default={}) or {}
    recorded_objects = current_data.get('selected_objects', []) if isinstance(current_data, dict) else []
    if bool(current_data.get('record_selected_objects', False)) and isinstance(recorded_objects, list):
        _draw_recorded_object_data_list(box, context, scene, recorded_objects)

    row = box.row()
    row.scale_y = 1.4
    row.operator("camera.save_selected_stock_memo", text="追加データ記録")



def _draw_additional_data_collapsible_body(layout, context, prop_name, target, text, panel_id):
    # Blender標準のPanel折りたたみと同じ見た目に寄せるため、利用可能ならUILayout.panelを使います。
    try:
        header, body = layout.panel(panel_id, default_closed=False)
        if header is not None:
            header.label(text=text)
        return body
    except Exception:
        pass

    # 古いBlenderなどでUILayout.panelが使えない場合のみ、標準の開閉アイコン付きoperatorに退避します。
    scene = context.scene
    is_open = bool(getattr(scene, prop_name, True))
    icon_name = 'DISCLOSURE_TRI_DOWN' if is_open else 'DISCLOSURE_TRI_RIGHT'
    row = layout.row(align=True)
    row.alignment = 'LEFT'
    toggle_op = row.operator(
        "camera.toggle_additional_data_subsection",
        text=text,
        icon=icon_name,
        emboss=False,
    )
    toggle_op.target = target
    return layout if is_open else None

def _draw_current_selected_object_data_list(layout, context, names):
    body = _draw_additional_data_collapsible_body(
        layout,
        context,
        "mpm_show_selected_object_data_list",
        "selected",
        "選択中OBJ",
        "mpm_selected_object_data_subpanel",
    )
    if body is None:
        return
    if not names:
        body.label(text="選択中OBJはありません")
        return

    wm = context.window_manager
    if (
        hasattr(wm, "mpm_selected_object_items_v131")
        and hasattr(wm, "mpm_selected_object_index_v131")
    ):
        try:
            _sync_current_selected_object_display_items(wm, names)
            list_row = body.row(align=True)
            list_row.template_list(
                "MPM_UL_selected_object_list_v131", "",
                wm, "mpm_selected_object_items_v131",
                wm, "mpm_selected_object_index_v131",
                rows=4, maxrows=8,
            )
            return
        except Exception:
            pass

    _draw_current_selected_object_compact_fallback(body, context, names)


def _draw_current_selected_object_list_select_button(layout, wm):
    name = _get_current_selected_object_name_from_list(wm)
    row = layout.row(align=True)
    row.enabled = bool(name) and bpy.data.objects.get(name) is not None
    op = row.operator("camera.select_recorded_object", text="選択", icon='RESTRICT_SELECT_OFF')
    op.object_name = name
    op.extend_selection = False
    op = row.operator("camera.select_recorded_object", text="追加選択", icon='ADD')
    op.object_name = name
    op.extend_selection = True


def _get_current_selected_object_name_from_list(wm):
    if not hasattr(wm, "mpm_selected_object_items_v131"):
        return ""
    collection = wm.mpm_selected_object_items_v131
    if len(collection) == 0:
        return ""
    try:
        index = int(getattr(wm, "mpm_selected_object_index_v131", 0) or 0)
    except Exception:
        index = 0
    index = max(0, min(index, len(collection) - 1))
    try:
        setattr(wm, "mpm_selected_object_index_v131", index)
    except Exception:
        pass
    item = collection[index]
    return str(getattr(item, "object_name", "") or "")


def _sync_current_selected_object_display_items(wm, names):
    if not hasattr(wm, "mpm_selected_object_items_v131"):
        return
    collection = wm.mpm_selected_object_items_v131
    current_names = [str(getattr(item, "object_name", "") or "") for item in collection]
    if current_names != names:
        while len(collection) > 0:
            collection.remove(0)
        for name in names:
            item = collection.add()
            item.object_name = name
    try:
        index = int(getattr(wm, "mpm_selected_object_index_v131", 0) or 0)
    except Exception:
        index = 0
    if names:
        index = max(0, min(index, len(names) - 1))
    else:
        index = 0
    try:
        setattr(wm, "mpm_selected_object_index_v131", index)
    except Exception:
        pass


def _draw_current_selected_object_compact_fallback(layout, context, names):
    wm = context.window_manager
    visible_rows = 4
    total = len(names)
    try:
        offset = int(getattr(wm, "mpm_selected_object_offset_v131", 0) or 0)
    except Exception:
        offset = 0
    max_offset = max(0, total - visible_rows)
    offset = max(0, min(offset, max_offset))
    try:
        setattr(wm, "mpm_selected_object_offset_v131", offset)
    except Exception:
        pass

    list_row = layout.row(align=True)
    col = list_row.column(align=True)
    for name in names[offset:offset + visible_rows]:
        row = col.row(align=True)
        exists = bpy.data.objects.get(name) is not None
        row.enabled = exists
        icon_name = 'RESTRICT_SELECT_OFF' if exists else 'ERROR'
        row.label(text=name, icon=icon_name)

    side = list_row.column(align=True)
    up = side.operator("camera.selected_object_fallback_scroll", text="", icon='TRIA_UP')
    up.direction = -1
    down = side.operator("camera.selected_object_fallback_scroll", text="", icon='TRIA_DOWN')
    down.direction = 1
    if total > visible_rows:
        layout.label(text=f"{offset + 1}-{min(offset + visible_rows, total)} / {total}")


def _draw_recorded_object_data_list(layout, context, scene, recorded_objects):
    record_box = _draw_additional_data_collapsible_body(
        layout,
        context,
        "mpm_show_recorded_object_data_list",
        "recorded",
        "記録済みOBJデータ",
        "mpm_recorded_object_data_subpanel",
    )
    if record_box is None:
        return
    if not recorded_objects:
        record_box.label(text="記録済みOBJデータはありません")
        return

    wm = context.window_manager
    names = _recorded_object_names_from_data(recorded_objects)
    if not names:
        record_box.label(text="表示できるOBJ名がありません")
        return

    if (
        hasattr(wm, "mpm_recorded_object_items_v130")
        and hasattr(wm, "mpm_recorded_object_index_v130")
    ):
        try:
            _sync_recorded_object_display_items(wm, names, _safe_saved_index(scene, get_camera_data_manager()))
            list_row = record_box.row(align=True)
            list_row.template_list(
                "MPM_UL_recorded_object_list_v130", "",
                wm, "mpm_recorded_object_items_v130",
                wm, "mpm_recorded_object_index_v130",
                rows=7, maxrows=12,
            )
            _draw_recorded_object_delete_button(record_box, wm)
            return
        except Exception:
            pass

    _draw_recorded_object_compact_fallback(record_box, context, names)


def _get_recorded_object_action_target_names(context):
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "mpm_recorded_object_items_v130"):
        return []
    collection = wm.mpm_recorded_object_items_v130
    recorded_names = [str(getattr(item, "object_name", "") or "") for item in collection]
    recorded_name_set = {name for name in recorded_names if name}

    selected_names = []
    seen = set()
    for obj in (getattr(context, "selected_objects", []) or []):
        name = str(getattr(obj, "name", "") or "")
        if name and name in recorded_name_set and name not in seen:
            selected_names.append(name)
            seen.add(name)
    if selected_names:
        return selected_names

    current_name = _get_selected_recorded_object_name(wm)
    return [current_name] if current_name else []


def _get_current_saved_item_for_recorded_edit(context):
    scene = context.scene
    manager = get_camera_data_manager()
    saved_items = getattr(manager, "saved_camera_data", []) or []
    if not saved_items:
        return None, None, -1, None
    index = _safe_saved_index(scene, manager)
    if not (0 <= index < len(saved_items)):
        return manager, saved_items, index, None
    item = saved_items[index]
    if not isinstance(item, dict):
        item = dict(item or {})
    else:
        item = dict(item)
    return manager, saved_items, index, item


def _apply_recorded_object_edit_to_current_stock(context, new_recorded_objects):
    scene = context.scene
    manager, saved_items, index, item = _get_current_saved_item_for_recorded_edit(context)
    if manager is None or item is None or not (0 <= index < len(saved_items)):
        return False
    item["selected_objects"] = list(new_recorded_objects or [])
    item["record_selected_objects"] = bool(item["selected_objects"])
    item["memo"] = str(getattr(scene, "saved_memo_text", "") or item.get("memo", "") or "")
    saved_items[index] = item
    manager.saved_camera_data = saved_items
    manager.save_data()
    rebuild_enum_cache(manager)
    ensure_valid_saved_enum(scene, manager)
    _sync_scene_saved_memo(scene, manager)
    try:
        wm = context.window_manager
        if hasattr(wm, "mpm_recorded_object_items_v130"):
            while len(wm.mpm_recorded_object_items_v130) > 0:
                wm.mpm_recorded_object_items_v130.remove(0)
        if hasattr(wm, "mpm_recorded_object_source_index_v145"):
            wm.mpm_recorded_object_source_index_v145 = -1
        if hasattr(wm, "mpm_recorded_object_index_v130"):
            wm.mpm_recorded_object_index_v130 = 0
    except Exception:
        pass
    try:
        if context.area:
            context.area.tag_redraw()
    except Exception:
        pass
    return True


def _remove_recorded_objects_from_current_stock(context, names_to_remove):
    remove_set = {str(name) for name in (names_to_remove or []) if str(name)}
    if not remove_set:
        return 0
    manager, saved_items, index, item = _get_current_saved_item_for_recorded_edit(context)
    if item is None:
        return 0
    current_objects = item.get("selected_objects", [])
    if not isinstance(current_objects, list):
        return 0
    remaining = []
    removed = 0
    for obj_data in current_objects:
        name = _recorded_object_name_from_data(obj_data)
        if name and name in remove_set:
            removed += 1
            continue
        remaining.append(obj_data)
    if removed <= 0:
        return 0
    if not _apply_recorded_object_edit_to_current_stock(context, remaining):
        return 0
    return removed


def _clear_recorded_objects_from_current_stock(context):
    manager, saved_items, index, item = _get_current_saved_item_for_recorded_edit(context)
    if item is None:
        return 0
    current_objects = item.get("selected_objects", [])
    removed = len(current_objects) if isinstance(current_objects, list) else 0
    if removed <= 0:
        return 0
    if not _apply_recorded_object_edit_to_current_stock(context, []):
        return 0
    return removed

def _draw_recorded_object_delete_button(layout, wm):
    name = _get_selected_recorded_object_name(wm)
    delete_row = layout.row(align=True)
    delete_row.enabled = bool(name)
    delete_row.operator("camera.mark_recorded_object_delete_candidate", text="記録済みOBJから削除", icon='TRASH')

    clear_row = layout.row(align=True)
    clear_row.enabled = bool(name)
    clear_row.operator("camera.delete_all_recorded_objects", text="記録済みOBJを全削除", icon='X')


def _get_selected_recorded_object_name(wm):
    if not hasattr(wm, "mpm_recorded_object_items_v130"):
        return ""
    collection = wm.mpm_recorded_object_items_v130
    if len(collection) == 0:
        return ""
    try:
        index = int(getattr(wm, "mpm_recorded_object_index_v130", 0) or 0)
    except Exception:
        index = 0
    index = max(0, min(index, len(collection) - 1))
    try:
        setattr(wm, "mpm_recorded_object_index_v130", index)
    except Exception:
        pass
    item = collection[index]
    return str(getattr(item, "object_name", "") or "")


def _recorded_object_name_from_data(obj_data):
    if isinstance(obj_data, dict):
        return str(obj_data.get('name', '') or obj_data.get('object_name', '') or '')
    if isinstance(obj_data, str):
        return obj_data
    return str(getattr(obj_data, "name", "") or '')


def _recorded_object_names_from_data(recorded_objects):
    names = []
    for obj_data in recorded_objects:
        name = _recorded_object_name_from_data(obj_data)
        if name:
            names.append(name)
    return names


def _sync_recorded_object_display_items(wm, names, source_index=None):
    if not hasattr(wm, "mpm_recorded_object_items_v130"):
        return
    collection = wm.mpm_recorded_object_items_v130
    current_names = [str(getattr(item, "object_name", "") or "") for item in collection]
    try:
        current_source_index = int(getattr(wm, "mpm_recorded_object_source_index_v145", -1) or -1)
    except Exception:
        current_source_index = -1
    try:
        next_source_index = int(source_index if source_index is not None else -1)
    except Exception:
        next_source_index = -1
    if current_names != names or current_source_index != next_source_index:
        previous_candidates = set()
        while len(collection) > 0:
            collection.remove(0)
        for name in names:
            item = collection.add()
            item.object_name = name
            item.delete_candidate = name in previous_candidates
        try:
            setattr(wm, "mpm_recorded_object_source_index_v145", next_source_index)
        except Exception:
            pass
    try:
        index = int(getattr(wm, "mpm_recorded_object_index_v130", 0) or 0)
    except Exception:
        index = 0
    if names:
        index = max(0, min(index, len(names) - 1))
    else:
        index = 0
    try:
        setattr(wm, "mpm_recorded_object_index_v130", index)
    except Exception:
        pass


def _draw_recorded_object_compact_fallback(layout, context, names):
    wm = context.window_manager
    visible_rows = 7
    total = len(names)
    try:
        offset = int(getattr(wm, "mpm_recorded_object_offset_v130", 0) or 0)
    except Exception:
        offset = 0
    max_offset = max(0, total - visible_rows)
    offset = max(0, min(offset, max_offset))
    try:
        setattr(wm, "mpm_recorded_object_offset_v130", offset)
    except Exception:
        pass

    list_row = layout.row(align=True)
    col = list_row.column(align=True)
    for name in names[offset:offset + visible_rows]:
        row = col.row(align=True)
        exists = bpy.data.objects.get(name) is not None
        row.enabled = exists
        icon_name = 'OBJECT_DATA' if exists else 'ERROR'
        op = row.operator("camera.select_recorded_object", text=name, icon=icon_name)
        op.object_name = name
        op.extend_selection = False
        obj = bpy.data.objects.get(name)
        if obj is not None and bool(obj.select_get()):
            row.label(text="選択中", icon='RESTRICT_SELECT_OFF')

    side = list_row.column(align=True)
    up = side.operator("camera.recorded_object_fallback_scroll", text="", icon='TRIA_UP')
    up.direction = -1
    down = side.operator("camera.recorded_object_fallback_scroll", text="", icon='TRIA_DOWN')
    down.direction = 1
    if total > visible_rows:
        layout.label(text=f"{offset + 1}-{min(offset + visible_rows, total)} / {total}")

def _draw_cycle_controls(layout, context):
    scene = context.scene
    manager = get_camera_data_manager()
    folder_path = getattr(manager, 'background_image_folder_path', '')
    folder_name = folder_path if folder_path else "未設定"
    layout.label(text=f"対象: {folder_name}")
    layout.prop(scene, 'bg_cycle_skip_stocked', text="ストック済は読み込まない")
    row = layout.row(align=True)
    row.scale_y = 1.2
    row.operator("camera.prev_folder_image", text="<")
    row.operator("camera.next_folder_image", text=">")


def _draw_background_controls(layout, context):
    camera = _get_scene_camera(context)
    if not (camera and getattr(camera, "data", None) and camera.data.background_images):
        layout.label(text="下絵がありません")
        return

    row = layout.row()
    row.prop(camera.data, "mpm_bg_visible", text="表示")
    bg = camera.data.background_images[0]

    row = layout.row()
    if hasattr(bg, "opacity"):
        row.prop(bg, "opacity", text="不透明度")
    elif hasattr(bg, "alpha"):
        row.prop(bg, "alpha", text="不透明度")
    else:
        row.label(text="不透明度プロパティが見つかりません")

    row = layout.row(align=True)
    row.prop(bg, "display_depth", expand=True, text="深度")

    row = layout.row()
    row.prop(camera.data, "passepartout_alpha", text="外枠の濃さ")


def _draw_dolly_controls(layout, context):
    dz = get_dolly_props(context.scene)
    if dz is None:
        layout.label(text="ドリーズーム設定を取得できません")
        return

    row = layout.row(align=True)
    row.prop(dz, "enabled", text="有効", toggle=True)
    layout.prop(dz, "target_obj", text="ターゲット")

    row = layout.row(align=True)
    row.prop(dz, "use_name_filter", text="候補フィルタ")
    if dz.use_name_filter:
        layout.prop(dz, "name_filter_text", text="含む文字")

    if dz.enabled:
        layout.prop(dz, "lens_min", text="レンズ最小(mm)")
        layout.prop(dz, "lens_max", text="レンズ最大(mm)")
        layout.prop(dz, "min_distance", text="最小距離")


def _draw_shortcut_settings_controls(layout, context):
    scene = context.scene
    prefs = get_addon_preferences()

    box = layout.box()
    header = box.row(align=True)
    header.prop(scene, "show_shortcut_settings_section", toggle=True, text="ショートカット項目")

    if not bool(getattr(scene, "show_shortcut_settings_section", False)):
        return

    sub = box.column(align=True)
    if prefs is not None:
        sub.prop(prefs, "disable_shift_arrow_conflicts", text="Shift+矢印の既存割り当てを一時無効化")
    else:
        disabled = sub.row()
        disabled.enabled = False
        disabled.label(text="Shift+矢印の既存割り当てを一時無効化")

    sub.separator()
    sub.label(text="ショートカット一覧")
    sub.label(text="Insert：最新のカメラ位置を呼び出し")
    sub.label(text="Ctrl + Insert：カメラ位置を保存")
    sub.label(text="Ctrl + Shift + Insert：下絵を読み込む")
    sub.label(text="Shift + ←：前のストックデータへ")
    sub.label(text="Shift + →：次のストックデータへ")


def _draw_misc_controls(layout, context):
    scene = context.scene
    layout.prop(scene, "open_output_after_render", text="レンダリング後出力フォルダ開く")

    row = layout.row()
    row.scale_y = 2.0
    row.operator("camera.open_output_folder", text="出力フォルダを開く")

    row = layout.row()
    row.operator("camera.select_data", text="カメラデータタブへ移動")

    row = layout.row()
    row.prop(scene, "show_settings", toggle=True, text="設定")
    if scene.show_settings:
        box = layout.box()
        box.operator("camera.reset_saved_data", text="記録データの初期化")
        box.operator("camera.save_stock_data", text="ストックデータを保存")
        box.operator("camera.load_stock_data", text="ストックデータを読込む")
        box.operator("camera.append_stock_data", text="ストックデータ追加読込")
        box.operator("camera.set_output_folder", text="出力フォルダを指定")
        box.operator("camera.set_background_image_folder", text="読込場所を設定")
        box.separator()
        _draw_shortcut_settings_controls(box, context)
        box.separator()
        box.label(text="保存データの管理")
        box.operator("camera.manage_saved_data", text="保存データを管理")
        box.separator()
        row = box.row(align=True)
        op = row.operator("camera.sort_saved_data", text="ソート（降順）", icon='SORTSIZE')
        op.reverse = True
        op = row.operator("camera.sort_saved_data", text="ソート（昇順）", icon='SORTSIZE')
        op.reverse = False
        box.separator()
        row = box.row()
        row.prop(scene, "show_all_object_data_section", toggle=True, text="全OBJデータ記録")
        if getattr(scene, "show_all_object_data_section", False):
            draw_all_object_data_controls(box, context)



def draw_header_buttons(self, context):
    layout = self.layout
    layout.separator()
    row = layout.row(align=True)
    row.operator("camera.save_position", text="記録", icon='FILE_TICK')
    row.operator("camera.load_background_image", text="画像読込", icon='IMAGE_DATA')


class VIEW3D_PT_custom_panel(bpy.types.Panel):
    bl_label = PANEL_LABEL
    bl_idname = "VIEW3D_PT_custom_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'カメラ'
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        camera = _get_scene_camera(context)
        _draw_active_camera_warning(layout, context)
        _draw_lens_control(layout, context, camera)
        _draw_lock_camera_toggle(layout, context)
        _draw_transform_controls(layout, camera)


class VIEW3D_PT_custom_panel_record_read(bpy.types.Panel):
    bl_label = "記録・読込"
    bl_idname = "VIEW3D_PT_custom_panel_record_read"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'カメラ'
    bl_order = 10

    def draw(self, context):
        _draw_record_read_controls(self.layout, context)


class VIEW3D_PT_custom_panel_saved_memo(bpy.types.Panel):
    bl_label = "追加データ記録"
    bl_idname = "VIEW3D_PT_custom_panel_saved_memo"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'カメラ'
    bl_order = 15

    def draw(self, context):
        _draw_saved_memo_controls(self.layout, context)


class VIEW3D_PT_custom_panel_image_cycle(bpy.types.Panel):
    bl_label = "画像送り"
    bl_idname = "VIEW3D_PT_custom_panel_image_cycle"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'カメラ'
    bl_order = 20

    def draw(self, context):
        _draw_cycle_controls(self.layout, context)


class VIEW3D_PT_custom_panel_background(bpy.types.Panel):
    bl_label = "下絵"
    bl_idname = "VIEW3D_PT_custom_panel_background"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'カメラ'
    bl_order = 30

    def draw(self, context):
        _draw_background_controls(self.layout, context)


class VIEW3D_PT_custom_panel_dolly(bpy.types.Panel):
    bl_label = "ドリーズーム"
    bl_idname = "VIEW3D_PT_custom_panel_dolly"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'カメラ'
    bl_order = 40

    def draw(self, context):
        _draw_dolly_controls(self.layout, context)


class VIEW3D_PT_custom_panel_misc(bpy.types.Panel):
    bl_label = "設定他"
    bl_idname = "VIEW3D_PT_custom_panel_misc"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'カメラ'
    bl_order = 50

    def draw(self, context):
        _draw_misc_controls(self.layout, context)


UI_CLASSES = (
    MPM_RecordedObjectListItemV130,
    MPM_UL_recorded_object_list_v130,
    MPM_SelectedObjectListItemV131,
    MPM_UL_selected_object_list_v131,
    MPM_OT_recorded_object_fallback_scroll,
    MPM_OT_selected_object_fallback_scroll,
    MPM_OT_toggle_additional_data_subsection,
    MPM_OT_mark_recorded_object_delete_candidate,
    MPM_OT_cancel_recorded_object_delete_candidate,
    MPM_OT_select_all_recorded_objects,
    VIEW3D_PT_custom_panel,
    VIEW3D_PT_custom_panel_record_read,
    VIEW3D_PT_custom_panel_saved_memo,
    VIEW3D_PT_custom_panel_image_cycle,
    VIEW3D_PT_custom_panel_background,
    VIEW3D_PT_custom_panel_dolly,
    VIEW3D_PT_custom_panel_misc,
)


def register_ui():
    try:
        VIEW3D_PT_custom_panel.bl_label = PANEL_LABEL
    except Exception:
        pass
    for attr in (
        "recorded_object_display_items",
        "recorded_object_display_index",
        "recorded_object_list_rows",
        "recorded_object_list_width",
        "mpm_recorded_object_items_v129",
        "mpm_recorded_object_index_v129",
        "mpm_show_selected_object_data_list",
        "mpm_show_recorded_object_data_list",
    ):
        if hasattr(bpy.types.Scene, attr):
            try:
                delattr(bpy.types.Scene, attr)
            except Exception:
                pass
    for attr in (
        "mpm_recorded_object_items_v130",
        "mpm_recorded_object_index_v130",
        "mpm_recorded_object_offset_v130",
        "mpm_recorded_object_source_index_v145",
        "mpm_selected_object_items_v131",
        "mpm_selected_object_index_v131",
        "mpm_selected_object_offset_v131",
    ):
        if hasattr(bpy.types.WindowManager, attr):
            try:
                delattr(bpy.types.WindowManager, attr)
            except Exception:
                pass
    for cls in UI_CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.mpm_recorded_object_items_v130 = bpy.props.CollectionProperty(type=MPM_RecordedObjectListItemV130)
    bpy.types.WindowManager.mpm_recorded_object_index_v130 = bpy.props.IntProperty(default=0, options={'SKIP_SAVE'})
    bpy.types.WindowManager.mpm_recorded_object_offset_v130 = bpy.props.IntProperty(default=0, min=0, options={'SKIP_SAVE'})
    bpy.types.WindowManager.mpm_recorded_object_source_index_v145 = bpy.props.IntProperty(default=-1, options={'SKIP_SAVE'})
    bpy.types.WindowManager.mpm_selected_object_items_v131 = bpy.props.CollectionProperty(type=MPM_SelectedObjectListItemV131)
    bpy.types.WindowManager.mpm_selected_object_index_v131 = bpy.props.IntProperty(default=0, options={'SKIP_SAVE'})
    bpy.types.WindowManager.mpm_selected_object_offset_v131 = bpy.props.IntProperty(default=0, min=0, options={'SKIP_SAVE'})
    bpy.types.Scene.mpm_show_selected_object_data_list = bpy.props.BoolProperty(name="選択中OBJ", default=True, options={'SKIP_SAVE'})
    bpy.types.Scene.mpm_show_recorded_object_data_list = bpy.props.BoolProperty(name="記録済みOBJデータ", default=True, options={'SKIP_SAVE'})
    bpy.types.VIEW3D_HT_header.append(draw_header_buttons)


def unregister_ui():
    try:
        bpy.types.VIEW3D_HT_header.remove(draw_header_buttons)
    except Exception:
        pass
    for attr in (
        "recorded_object_display_items",
        "recorded_object_display_index",
        "recorded_object_list_rows",
        "recorded_object_list_width",
        "mpm_recorded_object_items_v129",
        "mpm_recorded_object_index_v129",
        "mpm_show_selected_object_data_list",
        "mpm_show_recorded_object_data_list",
    ):
        if hasattr(bpy.types.Scene, attr):
            try:
                delattr(bpy.types.Scene, attr)
            except Exception:
                pass
    for attr in (
        "mpm_recorded_object_items_v130",
        "mpm_recorded_object_index_v130",
        "mpm_recorded_object_offset_v130",
        "mpm_recorded_object_source_index_v145",
        "mpm_selected_object_items_v131",
        "mpm_selected_object_index_v131",
        "mpm_selected_object_offset_v131",
    ):
        if hasattr(bpy.types.WindowManager, attr):
            try:
                delattr(bpy.types.WindowManager, attr)
            except Exception:
                pass
    for cls in reversed(UI_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass

# -------------------------------
# ファイル名：ui.py
# Version Footer: 1.158
# -------------------------------
