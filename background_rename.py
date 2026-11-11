# -*- coding: utf-8 -*-
# Version: 1.223
"""Rename one stock label or a background and its current-slot references."""

import copy
import json
import os
import re
import shutil
import unicodedata

import bpy


def validate_name(value):
    name = unicodedata.normalize('NFC', str(value)).strip()
    if (not name or name in {'.', '..', 'No File'} or name.endswith('.')
            or re.search(r'[\x00-\x1f<>:"/\\|?*]', name)
            or name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                 *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}):
        raise ValueError('使用できないファイル名です')
    return name


def rename_stock(manager, index, new_name, *, file_and_all):
    from . import core
    store = manager.slot_library
    if store:
        store.check_write()
    items = manager.saved_camera_data
    if not 0 <= index < len(items):
        raise ValueError('選択した記録がありません')
    old_name = str(items[index].get('bg_image', 'No File'))
    name = validate_name(new_name)
    if name == old_name:
        return False, ''
    folder = os.path.abspath(bpy.path.abspath(manager.background_image_folder_path))
    source = os.path.join(folder, old_name)
    target = os.path.join(folder, name)
    exists = old_name != 'No File' and os.path.isfile(source)
    if old_name != 'No File' and (os.path.basename(old_name) != old_name or os.path.islink(source)):
        raise ValueError('元の画像パスが不正か、シンボリックリンクです')
    do_file = bool(exists and file_and_all)
    if do_file and os.path.splitext(old_name)[1].casefold() != os.path.splitext(name)[1].casefold():
        raise ValueError('画像の拡張子は変更できません')
    updated = copy.deepcopy(items)
    for i, item in enumerate(updated):
        if (do_file and str(item.get('bg_image', '')).casefold() == old_name.casefold()) or i == index:
            item['bg_image'] = name
    created = False
    try:
        if do_file:
            before = os.stat(source)
            with open(source, 'rb') as inp, open(target, 'xb') as out:
                created = True
                shutil.copyfileobj(inp, out, length=1024 * 1024)
                out.flush()
                os.fsync(out.fileno())
            after = os.stat(source)
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('コピー中に画像が変更されました')
        manager.save_data(camera_data=updated)
    except Exception:
        # A failed save can occur after the atomic JSON replacement. Keep both
        # images in that ambiguous case, so neither old nor new references break.
        if created:
            try:
                with open(manager.save_file_path, encoding='utf-8-sig') as handle:
                    disk = json.load(handle)
                if disk.get('camera_data') == items:
                    os.unlink(target)
            except Exception:
                pass
        raise
    warning = ''
    if do_file:
        for image in bpy.data.images:
            try:
                if os.path.normcase(os.path.abspath(bpy.path.abspath(image.filepath))) == os.path.normcase(source):
                    image.filepath = target
            except (AttributeError, ReferenceError, RuntimeError):
                continue
        try:
            current = os.stat(source)
            if (before.st_size, before.st_mtime_ns) != (current.st_size, current.st_mtime_ns):
                raise OSError('元画像が更新されたため削除しません')
            os.unlink(source)
        except OSError as exc:
            warning = f'旧画像も残っています: {exc}'
    core.rebuild_enum_cache(manager)
    core._tag_redraw_all_areas()
    return True, warning


class MPM_OT_rename_background_stock(bpy.types.Operator):
    bl_idname = 'camera.rename_background_stock'
    bl_label = '下絵ファイル名を変更'
    bl_description = '選択中の下絵ファイル名を変更します'

    new_name: bpy.props.StringProperty(name='新しい名前')
    mode: bpy.props.EnumProperty(name='変更方法', items=(
        ('FILE', '画像と現在スロットの同名記録', '実画像を改名し現在スロットの同名記録を更新'),
        ('STOCK', '選択中の記録名のみ', '画像ファイルを変更しない')))
    index: bpy.props.IntProperty(options={'HIDDEN', 'SKIP_SAVE'})
    slot_path: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    snapshot: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    has_file: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})
    duplicate_count: bpy.props.IntProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        from . import core
        manager = core.get_camera_data_manager()
        self.index = core._safe_saved_index(context.scene, manager)
        item = core._get_saved_item_safe(manager, self.index)
        if not item:
            return {'CANCELLED'}
        self.slot_path = manager.save_file_path
        self.snapshot = json.dumps(item, sort_keys=True, ensure_ascii=False)
        old = item.get('bg_image', 'No File')
        self.new_name = '' if old == 'No File' else old
        self.has_file = old != 'No File' and os.path.isfile(os.path.join(manager.background_image_folder_path, old))
        self.duplicate_count = sum(1 for x in manager.saved_camera_data if str(x.get('bg_image', '')).casefold() == old.casefold())
        self.mode = 'FILE' if self.has_file else 'STOCK'
        return context.window_manager.invoke_props_dialog(self, width=480)

    def draw(self, context):
        self.layout.prop(self, 'new_name')
        if self.has_file:
            self.layout.label(text='実画像の変更は別スロットや他のblendに影響します', icon='ERROR')
            self.layout.label(text=f'現在スロット内の同名記録: {self.duplicate_count}件')
            self.layout.prop(self, 'mode', expand=True)
        else:
            self.layout.label(text='画像がないため、この記録名のみ変更します')

    def execute(self, context):
        from . import core
        manager = core.get_camera_data_manager()
        item = core._get_saved_item_safe(manager, self.index)
        if (manager.save_file_path != self.slot_path or core._safe_saved_index(context.scene, manager) != self.index
                or not item or json.dumps(item, sort_keys=True, ensure_ascii=False) != self.snapshot):
            self.report({'ERROR'}, '記録が変更されたため中止しました')
            return {'CANCELLED'}
        if self.has_file and self.mode == 'FILE' and not os.path.isfile(os.path.join(
                bpy.path.abspath(manager.background_image_folder_path), item.get('bg_image', ''))):
            self.report({'ERROR'}, '元の画像が見つからないため中止しました')
            return {'CANCELLED'}
        try:
            changed, warning = rename_stock(manager, self.index, self.new_name, file_and_all=self.mode == 'FILE')
        except Exception as exc:
            self.report({'ERROR'}, f'名前を変更できません: {exc}')
            return {'CANCELLED'}
        if warning:
            self.report({'WARNING'}, warning)
        else:
            self.report({'INFO'}, '名前を変更しました' if changed else '名前は変更されていません')
        return {'FINISHED'}


# Version Footer: 1.223
