# -*- coding: utf-8 -*-
# ファイル名：ui.py
# 00漫画用Camera Position Manager
# 変更点（1.106）:
# - Nパネルをセクション分割し、折りたたみと並び替えに対応
# - 位置 / 記録・読込 / 画像送り / 下絵 / ドリーズーム / 設定他 の既定順へ再編成

import bpy

from .core import (
    get_camera_data_manager,
    rebuild_enum_cache,
    safe_basename,
    ensure_valid_saved_enum,
    _sync_scene_saved_memo,
    PANEL_LABEL,
    _ENUM_CACHE,
)

from .dolly import (
    get_dolly_props,
)


def _get_scene_camera(context):
    scene = context.scene
    return getattr(scene, "camera", None)


def _draw_active_camera_warning(layout, context):
    scene = context.scene
    if getattr(scene, "camera", None) is not None:
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
    box.prop(scene, 'saved_memo_text', text="")


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
    bl_label = "摘要メモ"
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
    for cls in UI_CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_HT_header.append(draw_header_buttons)


def unregister_ui():
    try:
        bpy.types.VIEW3D_HT_header.remove(draw_header_buttons)
    except Exception:
        pass
    for cls in reversed(UI_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass

# -------------------------------
# ファイル名：ui.py
# Version Footer: 1.106
# -------------------------------
