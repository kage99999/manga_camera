# -*- coding: utf-8 -*-
# Version: 1.217
"""Build background stocks without evaluating frames or changing the live scene."""

import math
import os
import re
import time

import bpy

from .storage import _normalize_saved_item, safe_basename


def image_size(path):
    # Private temporary datablock: never reload, remove or edit a user's image.
    image = bpy.data.images.load(path, check_existing=False)
    try:
        width, height = image.size
        if width <= 0 or height <= 0 or image.file_format != 'PNG':
            raise ValueError('有効なPNG画像ではありません')
        return int(width), int(height)
    finally:
        bpy.data.images.remove(image)


def append_folder(scene, manager, directory):
    from . import core

    if not directory:
        raise ValueError('読込フォルダを指定してください')
    folder = os.path.abspath(bpy.path.abspath(directory))
    if not os.path.isdir(folder):
        raise ValueError('指定フォルダが見つかりません')
    if manager.slot_library is not None:
        manager.slot_library.check_write()
    with os.scandir(folder) as entries:
        paths = sorted(entry.path for entry in entries
                       if re.fullmatch(r'[0-9]{6}\.png', entry.name, re.IGNORECASE)
                       and entry.is_file())
    existing = manager.saved_camera_data
    names = {safe_basename(item.get('bg_image', '')).casefold()
             for item in existing if item.get('bg_image') not in ('', 'No File', None)}
    pending = []
    seen = set(names)
    for path in paths:
        name = os.path.basename(path).casefold()
        if name not in seen:
            pending.append(path)
            seen.add(name)
    skipped = len(paths) - len(pending)
    if not pending:
        return 0, skipped

    old_folder = os.path.abspath(bpy.path.abspath(manager.background_image_folder_path))
    if os.path.normcase(folder) != os.path.normcase(old_folder):
        for item in existing:
            name = safe_basename(item.get('bg_image', ''))
            if name and name != 'No File' and not os.path.isfile(os.path.join(folder, name)):
                raise ValueError('既存記録の下絵が指定フォルダにありません。新しいスロットで一括読込してください')

    camera = core._get_valid_scene_camera(scene, repair=False)
    lens = float(camera.data.lens) if camera else 50.0
    position = list(scene.mpm_batch_camera_position)
    rotation = list(scene.mpm_batch_camera_rotation)
    if not all(math.isfinite(value) for value in position + rotation + [lens]):
        raise ValueError('初期位置・回転・焦点距離が不正です')
    added = []
    for path in pending:
        try:
            width, height = image_size(path)
        except Exception as exc:
            raise ValueError(f'{os.path.basename(path)} を読み込めません: {exc}') from exc
        added.append(_normalize_saved_item({
            'position': position,
            'rotation': rotation,
            'rotation_mode': 'XYZ',
            'focal_length': lens,
            'resolution_x': width,
            'resolution_y': height,
            'bg_image': os.path.basename(path),
            'bg_opacity': 1.0,
            'bg_depth': 'FRONT',
            'bg_visible': True,
            'frame_current': int(os.path.splitext(os.path.basename(path))[0]),
            'fisheye_state': {'version': 1, 'enabled': False},
            'record_local_view_state': False,
            'created_at': time.time(),
        }))
    manager.save_data(camera_data=existing + added, background_image_folder_path=folder)
    return len(added), skipped


class OBJECT_OT_batch_load_background_images(bpy.types.Operator):
    bl_idname = 'camera.batch_load_background_images'
    bl_label = '一括読込'
    bl_description = 'フォルダ直下の数字6桁のPNGを初期位置で記録します。同名画像はスキップし、付随データは保存しません'

    directory: bpy.props.StringProperty(subtype='DIR_PATH')
    filter_folder: bpy.props.BoolProperty(default=True, options={'HIDDEN'})
    _slot_id = None

    def invoke(self, context, event):
        from . import core
        manager = core.get_camera_data_manager()
        store = manager.slot_library
        if store is not None and not store.active:
            self.report({'WARNING'}, 'スロットを作成してから一括読込してください')
            return {'CANCELLED'}
        self._slot_id = (store.folder, store.active_id) if store else None
        self.directory = manager.background_image_folder_path
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        self.layout.label(text='未登録の6桁PNGのみ追加します')
        self.layout.label(text='付随データなし・現在のシーンは変更しません')

    def execute(self, context):
        from . import core
        manager = core.get_camera_data_manager()
        store = manager.slot_library
        if self._slot_id is not None and (store is None or self._slot_id != (store.folder, store.active_id)):
            self.report({'ERROR'}, '選択スロットが変更されました。一括読込を開き直してください')
            return {'CANCELLED'}
        index = len(manager.saved_camera_data)
        try:
            added, skipped = append_folder(context.scene, manager, self.directory)
        except Exception as exc:
            self.report({'ERROR'}, f'一括読込を中止しました: {exc}')
            return {'CANCELLED'}
        if not added:
            self.report({'INFO'}, f'追加対象がありません（既存画像 {skipped} 件）')
            return {'CANCELLED'}
        memo = context.scene.saved_memo_text
        core.rebuild_enum_cache(manager)
        core._set_saved_camera_index_safe(context.scene, manager, index, apply_saved_data=False)
        context.scene.saved_memo_text = memo
        self.report({'INFO'}, f'{added} 件を記録しました（既存画像 {skipped} 件スキップ）。記録を選択して読み込めます')
        return {'FINISHED'}


def register_properties():
    bpy.types.Scene.mpm_batch_camera_position = bpy.props.FloatVectorProperty(
        name='初期位置', description='一括読込で作成する記録のカメラ位置',
        size=3, subtype='TRANSLATION', unit='LENGTH', default=(0.0, -1.0, 0.5))
    bpy.types.Scene.mpm_batch_camera_rotation = bpy.props.FloatVectorProperty(
        name='初期回転', description='一括読込で作成する記録のXYZオイラー回転',
        size=3, subtype='EULER', unit='ROTATION', default=(math.pi / 2, 0.0, 0.0))


def unregister_properties():
    for name in ('mpm_batch_camera_position', 'mpm_batch_camera_rotation'):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)


# Version Footer: 1.217
