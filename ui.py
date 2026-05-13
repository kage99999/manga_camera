# -*- coding: utf-8 -*-
# ファイル名：ui.py
# 00漫画用Camera Position Manager
# 変更点（1.131）:
# - 選択中オブジェクト一覧もWindowManager管理のUIListへ変更
# - 選択OBJデータの全件縦表示を固定高さのスクロール表示へ変更
# - 記録済みOBJデータ一覧のUIList表示は維持

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
)

from .dolly import (
    get_dolly_props,
)

from .all_object_data import (
    draw_all_object_data_controls,
)


class MPM_RecordedObjectListItemV130(bpy.types.PropertyGroup):
    object_name: bpy.props.StringProperty(name="オブジェクト名", default="")


class MPM_UL_recorded_object_list_v130(bpy.types.UIList):
    bl_idname = "MPM_UL_recorded_object_list_v130"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        name = str(getattr(item, "object_name", "") or "")
        exists = bool(name) and bpy.data.objects.get(name) is not None
        icon_name = 'OBJECT_DATA' if exists else 'ERROR'
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            row.label(text=name if name else "名称なし", icon=icon_name)
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
        layout.label(text="保存された位置と回転と解像度と下絵")
        ensure_valid_saved_enum(scene, manager)
        try:
            layout.prop(scene, 'saved_camera_index', text="")
        except Exception:
            rebuild_enum_cache(manager)
            layout.prop(scene, 'saved_camera_index', text="")

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
        list_box = box.box()
        if names:
            _draw_current_selected_object_data_list(list_box, context, names)
        else:
            list_box.label(text="選択中オブジェクトはありません")

    current_data = _get_saved_item_safe(manager, _safe_saved_index(scene, manager), default={}) or {}
    recorded_objects = current_data.get('selected_objects', []) if isinstance(current_data, dict) else []
    if bool(current_data.get('record_selected_objects', False)) and isinstance(recorded_objects, list):
        _draw_recorded_object_data_list(box, context, scene, recorded_objects)

    row = box.row()
    row.scale_y = 1.4
    row.operator("camera.save_selected_stock_memo", text="追加データ記録")


def _draw_current_selected_object_data_list(layout, context, names):
    layout.label(text="選択中オブジェクト")
    if not names:
        layout.label(text="選択中オブジェクトはありません")
        return

    wm = context.window_manager
    if (
        hasattr(wm, "mpm_selected_object_items_v131")
        and hasattr(wm, "mpm_selected_object_index_v131")
    ):
        try:
            _sync_current_selected_object_display_items(wm, names)
            list_row = layout.row(align=True)
            list_row.template_list(
                "MPM_UL_selected_object_list_v131", "",
                wm, "mpm_selected_object_items_v131",
                wm, "mpm_selected_object_index_v131",
                rows=4, maxrows=8,
            )
            _draw_current_selected_object_list_select_button(layout, wm)
            return
        except Exception:
            pass

    _draw_current_selected_object_compact_fallback(layout, context, names)


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
        op = row.operator("camera.select_recorded_object", text=name, icon=icon_name)
        op.object_name = name
        op.extend_selection = False

    side = list_row.column(align=True)
    up = side.operator("camera.selected_object_fallback_scroll", text="", icon='TRIA_UP')
    up.direction = -1
    down = side.operator("camera.selected_object_fallback_scroll", text="", icon='TRIA_DOWN')
    down.direction = 1
    if total > visible_rows:
        layout.label(text=f"{offset + 1}-{min(offset + visible_rows, total)} / {total}")


def _draw_recorded_object_data_list(layout, context, scene, recorded_objects):
    record_box = layout.box()
    record_box.label(text="記録済みOBJデータ")
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
            _sync_recorded_object_display_items(wm, names)
            list_row = record_box.row(align=True)
            list_row.template_list(
                "MPM_UL_recorded_object_list_v130", "",
                wm, "mpm_recorded_object_items_v130",
                wm, "mpm_recorded_object_index_v130",
                rows=7, maxrows=12,
            )
            _draw_recorded_object_list_select_buttons(record_box, wm)
            return
        except Exception:
            pass

    _draw_recorded_object_compact_fallback(record_box, context, names)

def _draw_recorded_object_list_select_buttons(layout, wm):
    name = _get_selected_recorded_object_name(wm)
    row = layout.row(align=True)
    row.enabled = bool(name) and bpy.data.objects.get(name) is not None
    op = row.operator("camera.select_recorded_object", text="選択", icon='RESTRICT_SELECT_OFF')
    op.object_name = name
    op.extend_selection = False
    op = row.operator("camera.select_recorded_object", text="追加選択", icon='ADD')
    op.object_name = name
    op.extend_selection = True


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


def _sync_recorded_object_display_items(wm, names):
    if not hasattr(wm, "mpm_recorded_object_items_v130"):
        return
    collection = wm.mpm_recorded_object_items_v130
    current_names = [str(getattr(item, "object_name", "") or "") for item in collection]
    if current_names != names:
        while len(collection) > 0:
            collection.remove(0)
        for name in names:
            item = collection.add()
            item.object_name = name
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
    bpy.types.WindowManager.mpm_selected_object_items_v131 = bpy.props.CollectionProperty(type=MPM_SelectedObjectListItemV131)
    bpy.types.WindowManager.mpm_selected_object_index_v131 = bpy.props.IntProperty(default=0, options={'SKIP_SAVE'})
    bpy.types.WindowManager.mpm_selected_object_offset_v131 = bpy.props.IntProperty(default=0, min=0, options={'SKIP_SAVE'})
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
# Version Footer: 1.131
# -------------------------------
