# -*- coding: utf-8 -*-
# Manga Camera 1.210: boundary to the imported Gyogan Camera 0.1.21 engine.
"""Keep the optional fisheye engine isolated from legacy camera workflows."""

import bpy
from mathutils import Vector
from types import SimpleNamespace

_registered = False
_unavailable_reason = ''


def available():
    return _registered


def managed_dolly(scene):
    if not _registered or scene is None:
        return None
    settings = getattr(scene, 'gyogan_dolly', None)
    return settings if settings and settings.active else None


def camera_values(camera, scene=None):
    """Capture driven displacement without frame_set or pose reevaluation."""
    settings = managed_dolly(scene)
    if settings is None or settings.camera != camera:
        return list(camera.location), float(camera.data.lens)
    from .gyogan_camera import dolly
    if dolly._runtime_conflict(scene):
        # User-modified drivers are not ours to interpret or overwrite.
        return list(camera.location + camera.delta_location), float(camera.data.lens)
    ratio = dolly.effective_ratio(settings)
    # The engine only owns delta_location and lens, not the camera rotation.
    offset = Vector(settings.back_axis) * settings.depth * (ratio - 1.0)
    base = Vector(settings.base_location) if settings.navigation_version < 1 else camera.location
    position = base + offset
    return list(position), float(settings.base_lens * ratio)


def object_location(obj):
    if _registered and obj.type == 'CAMERA':
        for scene in obj.users_scene:
            settings = managed_dolly(scene)
            if settings and settings.camera == obj:
                return camera_values(obj, scene)[0]
    return list(obj.location)


def release_for_camera_write(camera, *, preserve_lens=False):
    """Remove owned dolly drivers before an explicit stock transform restore."""
    if not _registered:
        return
    from .gyogan_camera import dolly
    for scene in tuple(camera.users_scene):
        settings = managed_dolly(scene)
        if settings and settings.camera == camera:
            lens = camera_values(camera, scene)[1] if preserve_lens else None
            warnings = dolly.restore(scene)
            if lens is not None:
                camera.data.lens = lens
            settings.status = ('記録座標を読み込むため魚眼ドリーを解除しました'
                               + ('。' + '／'.join(warnings) if warnings else ''))


def draw(layout, context):
    if _registered:
        from .gyogan_camera import GYOGAN_PT_main
        GYOGAN_PT_main.draw(SimpleNamespace(layout=layout), context)
        return
    column = layout.column(align=True)
    column.alert = True
    for index, line in enumerate(_unavailable_reason.splitlines()):
        column.label(text=line, icon='ERROR' if index == 0 else 'NONE')


def _registration_conflicts(engine):
    conflicts = [name for name in ('gyogan', 'gyogan_dolly')
                 if hasattr(bpy.types.Scene, name)]
    for cls in engine.CLASSES + engine.dolly.CLASSES:
        base = next((base for base in (bpy.types.Operator, bpy.types.Panel,
                    bpy.types.UIList, bpy.types.PropertyGroup) if issubclass(cls, base)), None)
        if base is not None and base.bl_rna_get_subclass_py(cls.__name__) is not None:
            conflicts.append(cls.__name__)
        if issubclass(cls, bpy.types.Operator):
            category, name = cls.bl_idname.split('.')
            try:
                getattr(getattr(bpy.ops, category), name).get_rna_type()
            except KeyError:
                continue
            conflicts.append(cls.bl_idname)
    return conflicts


def register_fisheye():
    global _registered, _unavailable_reason
    if _registered:
        return
    if bpy.app.version < (5, 0, 0):
        _unavailable_reason = '魚眼機能はBlender 5.0以上に対応します。\n既存のカメラ機能はそのまま使えます。'
        return
    from . import gyogan_camera
    conflicts = _registration_conflicts(gyogan_camera)
    if conflicts:
        _unavailable_reason = ('単体版の魚眼機能と登録が重複しています。\n単体版を無効にした後、漫画カメラを\n無効化・再有効化してください。')
        print('漫画カメラの魚眼登録を保留: ' + ', '.join(conflicts))
        return
    _registered = True
    try:
        gyogan_camera.register(register_panel=False)
    except Exception:
        _registered = False
        raise
    _unavailable_reason = ''


def unregister_fisheye():
    global _registered, _unavailable_reason
    if _registered:
        from . import gyogan_camera
        try:
            gyogan_camera.unregister()
        finally:
            _registered = False
    _unavailable_reason = ''

# Version Footer: 1.210
