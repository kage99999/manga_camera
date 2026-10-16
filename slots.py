# -*- coding: utf-8 -*-
# Manga Camera 1.210: stock-slot UI and explicit file operations.

import os

import bpy
from bpy.props import EnumProperty, StringProperty

from . import core


_EMPTY_ITEMS = [('NONE', '(スロットなし)', '', 0)]


def _store():
    return core.get_camera_data_manager().slot_library


def refresh_ui(context=None):
    manager = core.get_camera_data_manager()
    core.rebuild_enum_cache(manager)
    # The data manager is application-wide, so stale indices in other scenes must
    # not trigger a stock apply from their next panel draw.
    for scene in bpy.data.scenes:
        core._set_saved_camera_index_safe(scene, manager, 0, apply_saved_data=False)
        core._sync_scene_saved_memo(scene, manager)
    for wm in bpy.data.window_managers:
        if hasattr(wm, 'mpm_recorded_object_items_v130'):
            wm.mpm_recorded_object_items_v130.clear()
            wm.mpm_recorded_object_source_index_v145 = -1
            wm.mpm_recorded_object_index_v130 = 0
    core._tag_redraw_all_areas()


def _items(self, context):
    store = _store()
    return store.enum_items if store else _EMPTY_ITEMS


def _get_index(self):
    store = _store()
    if store:
        for item in store.enum_items:
            if item[0] == store.active_id:
                return item[3]
        return store.enum_items[0][3]
    return 0


def _set_index(self, value):
    store = _store()
    if not store or not value:
        return
    ident = next((item[0] for item in store.enum_items if item[3] == value), None)
    if not ident:
        return
    try:
        store.select(ident)
    except Exception as exc:
        core.get_camera_data_manager().report_error(f'スロットを切り替えられませんでした: {exc}')
        return
    refresh_ui()


class MPM_OT_create_stock_slot(bpy.types.Operator):
    bl_idname = 'camera.create_stock_slot'
    bl_label = 'スロット作成'
    bl_description = '空の記録スロットを作成します。下絵・出力フォルダは現在の設定を引き継ぎます'

    slot_name: StringProperty(name='スロット名', default='', maxlen=80)

    def invoke(self, context, event):
        self.slot_name = ''
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        try:
            _store().create(self.slot_name)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        refresh_ui(context)
        return {'FINISHED'}


def _get_slot_name(self):
    store = _store()
    return store.active['name'] if store and store.active else ''


def _set_slot_name(self, value):
    store = _store()
    if not store or not store.active:
        return
    try:
        store.rename(store.active_id, value)
    except Exception as exc:
        store.manager.report_error(f'スロット名を変更できませんでした: {exc}')
    # Native text editing commits on confirmation, not on every keystroke.
    # Preserve the selected record and any unsaved memo, including on errors.
    core._tag_redraw_all_areas()


class MPM_OT_rename_stock_slot(bpy.types.Operator):
    bl_idname = 'camera.rename_stock_slot'
    bl_label = 'スロット名を変更'
    bl_description = '指定したスロットの名前とJSONファイル名を変更します'
    bl_options = {'INTERNAL'}

    slot_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    slot_folder: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    slot_name: StringProperty(name='スロット名', maxlen=80)

    @classmethod
    def poll(cls, context):
        store = _store()
        return bool(store and store.active and not store.error)

    def execute(self, context):
        try:
            store = _store()
            if self.slot_folder != store.folder:
                raise ValueError('名前の入力中に保存先が変わりました。やり直してください')
            changed = store.rename(self.slot_id, self.slot_name)
        except Exception as exc:
            self.report({'ERROR'}, f'スロット名を変更できませんでした: {exc}')
            return {'CANCELLED'}
        # Do not reset the selected record or discard an unsaved memo on rename.
        core._tag_redraw_all_areas()
        if changed:
            self.report({'INFO'}, 'スロット名を変更しました')
        return {'FINISHED'}


class MPM_OT_delete_stock_slot(bpy.types.Operator):
    bl_idname = 'camera.delete_stock_slot'
    bl_label = 'スロット削除'
    bl_description = '選択中スロットを削除します。JSONは保存先の.trashフォルダへ退避します'

    slot_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    slot_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    count: bpy.props.IntProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        store = _store()
        if not store or not store.active:
            return {'CANCELLED'}
        self.slot_id, self.slot_name = store.active_id, store.active['name']
        self.count = len(store.manager.saved_camera_data)
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        self.layout.label(text=f'「{self.slot_name}」を削除しますか？', icon='ERROR')
        self.layout.label(text=f'記録数: {self.count}件（JSONは退避されます）')

    def execute(self, context):
        try:
            _store().delete(self.slot_id)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        refresh_ui(context)
        return {'FINISHED'}


class MPM_OT_set_stock_slot_folder(bpy.types.Operator):
    bl_idname = 'camera.set_stock_slot_folder'
    bl_label = 'スロット保存先を指定'
    bl_description = '目録のないフォルダには全スロットをコピーします。既存の目録がある場合はその一覧へ切り替えます。元データは残します'

    directory: StringProperty(subtype='DIR_PATH')

    def invoke(self, context, event):
        store = _store()
        self.directory = store.folder if store else ''
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            _store().set_folder(self.directory)
        except Exception as exc:
            self.report({'ERROR'}, f'保存先を変更できませんでした: {exc}')
            return {'CANCELLED'}
        refresh_ui(context)
        self.report({'INFO'}, 'スロット保存先を変更しました。元の保存先は残しています')
        return {'FINISHED'}


class MPM_OT_export_all_stock_slots(bpy.types.Operator):
    bl_idname = 'camera.export_all_stock_slots'
    bl_label = '全スロットを書出し'
    bl_description = '全スロットのJSONをZIPへ書き出します。画像やblend本体は含めず、自動保存先も変更しません'

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default='*.zip', options={'HIDDEN'})
    check_existing: bpy.props.BoolProperty(default=True, options={'HIDDEN'})

    def invoke(self, context, event):
        self.filepath = os.path.join(core.get_camera_data_manager().output_folder_path, '全スロット.zip')
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        path = self.filepath if self.filepath.lower().endswith('.zip') else self.filepath + '.zip'
        try:
            _store().export_all(path)
        except Exception as exc:
            self.report({'ERROR'}, f'全スロットを書き出せませんでした: {exc}')
            return {'CANCELLED'}
        self.report({'INFO'}, '全スロットを書き出しました')
        return {'FINISHED'}


class MPM_OT_import_all_stock_slots(bpy.types.Operator):
    bl_idname = 'camera.import_all_stock_slots'
    bl_label = '全スロットを読込む'
    bl_description = '全スロットZIPを追加読込します。同名スロットは別名で追加し、カメラ等は動かしません'

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default='*.zip', options={'HIDDEN'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            _store().import_all(self.filepath)
        except Exception as exc:
            self.report({'ERROR'}, f'全スロットを読み込めませんでした: {exc}')
            return {'CANCELLED'}
        refresh_ui(context)
        self.report({'INFO'}, 'スロットを追加しました。既存スロットは上書きしていません')
        return {'FINISHED'}


def draw_selector(layout, context):
    store = _store()
    row = layout.row(align=True)
    label = row.row(align=True)
    label.alignment = 'LEFT'
    label.label(text='スロット')
    # Share one native background without borders between the editable name and menu.
    field = row.box().row(align=True)
    field.scale_y = 0.8
    field.emboss = 'NONE_OR_STATUS'
    name = field.row(align=True)
    # NONE_OR_STATUS on text fields requires Ctrl-click; keep plain-click editing.
    name.emboss = 'PULLDOWN_MENU'
    name.enabled = bool(store and store.active and not store.error)
    if store and store.active:
        name.prop(context.window_manager, 'mpm_stock_slot_name', text='')
    else:
        name.label(text='(スロットなし)')
    field.prop_menu_enum(context.window_manager, 'mpm_stock_slot', text='', icon='DOWNARROW_HLT')
    row = layout.row(align=True)
    create = row.row(align=True)
    create.enabled = bool(store and store.catalog is not None)
    create.operator('camera.create_stock_slot', text='スロット作成')
    delete = row.row(align=True)
    delete.enabled = bool(store and store.active and not store.error)
    delete.operator('camera.delete_stock_slot', text='スロット削除')
    if store and store.error:
        layout.label(text='保存先の読込エラー: 設定で確認してください', icon='ERROR')
    elif store and not store.entries:
        layout.label(text='スロットを作成してください', icon='INFO')


def draw_folder(layout):
    store = _store()
    layout.label(text='スロット保存先')
    layout.label(text=store.folder if store and store.folder else '(未設定)')
    layout.operator('camera.set_stock_slot_folder')


CLASSES = (MPM_OT_create_stock_slot, MPM_OT_rename_stock_slot, MPM_OT_delete_stock_slot, MPM_OT_set_stock_slot_folder,
           MPM_OT_export_all_stock_slots, MPM_OT_import_all_stock_slots)


def register_slots():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.mpm_stock_slot = EnumProperty(
        name='スロット', description='記録一覧と下絵・出力フォルダを切り替えます。カメラやポーズは変更しません',
        items=_items, get=_get_index, set=_set_index, options={'SKIP_SAVE'})
    bpy.types.WindowManager.mpm_stock_slot_name = StringProperty(
        name='スロット名', description='この欄で直接名前を編集します。Enterで確定、Escで取消。右端の▼でスロット切替',
        get=_get_slot_name, set=_set_slot_name, maxlen=80, options={'SKIP_SAVE'})


def unregister_slots():
    if hasattr(bpy.types.WindowManager, 'mpm_stock_slot_name'):
        del bpy.types.WindowManager.mpm_stock_slot_name
    if hasattr(bpy.types.WindowManager, 'mpm_stock_slot'):
        del bpy.types.WindowManager.mpm_stock_slot
    for cls in reversed(CLASSES):
        if cls.is_registered:
            bpy.utils.unregister_class(cls)


# Version Footer: 1.210
