"""Keep the scene camera, generated warps and reversible dolly in agreement."""

import textwrap

import bpy
from bpy.app.handlers import persistent

from . import dolly, nodes


_busy = set()
_states = {}
_suspended = False
_owner = object()
_UNSET = object()


def usable(scene, camera):
    return bool(camera and camera.type == 'CAMERA'
                and camera.name in scene.objects and camera.data.type == 'PERSP')


def warning(scene):
    camera = scene.camera
    if camera is None:
        if any(obj.type == 'CAMERA' for obj in scene.objects):
            return 'アクティブカメラが未設定です。\n基準カメラを選択してください'
        return 'シーンにカメラがありません。\nカメラを追加してください'
    if camera.name not in scene.objects:
        return 'このシーンのカメラを選択してください'
    if camera.type != 'CAMERA':
        return 'アクティブカメラにカメラ以外が設定されています'
    if camera.data.type != 'PERSP':
        return '魚眼には透視投影カメラを使用してください'
    return scene.gyogan.camera_error


def draw_warning(layout, scene):
    message = warning(scene)
    if message:
        column = layout.column(align=True)
        column.alert = True
        column.use_property_split = False
        lines = [line for paragraph in message.splitlines()
                 for line in textwrap.wrap(paragraph, width=24)]
        for index, line in enumerate(lines):
            column.label(text=line, icon='ERROR' if index == 0 else 'NONE')


def _state(scene):
    camera = scene.camera
    return (camera.as_pointer() if camera else 0, usable(scene, camera),
            len(scene.gyogan.targets))


def _refresh_views(scene):
    # Respect explicitly independent local-camera views. Normal camera views
    # follow the scene camera, including views left over from old addon versions.
    for window in bpy.context.window_manager.windows:
        if window.scene != scene:
            continue
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                space = area.spaces.active
                if not space.use_local_camera:
                    space.camera = scene.camera
                area.tag_redraw()


def synchronize(scene, camera=_UNSET, previous=_UNSET):
    """Apply one camera transition; reject failures without losing old setup.

    Scene.camera is authoritative. The explicit form is used only by the
    compatibility RNA callback for scripts assigning gyogan.camera.
    """
    if (_suspended or not hasattr(scene, 'gyogan')
            or not hasattr(scene, 'gyogan_dolly')):
        return False
    key = scene.as_pointer()
    if key in _busy:
        return False
    settings = scene.gyogan
    if camera is _UNSET:
        camera = scene.camera
    if previous is _UNSET:
        previous = settings.camera
    changed = camera != previous or settings.camera != scene.camera
    # A chained assignment can set both RNA pointers before the callback runs.
    # Rebind on initial synchronization too, validating saved references once.
    rebind = changed or _states.get(key, ())[:2] != _state(scene)[:2]
    stale_dolly = scene.gyogan_dolly.active and scene.gyogan_dolly.camera != camera
    missing_dolly = scene.gyogan_dolly.active and camera is None
    if not changed and not stale_dolly and not missing_dolly and _states.get(key) == _state(scene):
        return True
    _busy.add(key)
    plan = []
    committed = False
    try:
        if camera and (camera.type != 'CAMERA' or camera.name not in scene.objects):
            raise ValueError('このシーンのカメラを選択してください')
        if rebind:
            plan = nodes.camera_retarget_plan(scene, camera if usable(scene, camera) else None)
            nodes.apply_camera_plan(plan)
        try:
            if changed or stale_dolly or missing_dolly:
                dolly.switch_camera(scene, camera)
            elif scene.gyogan_dolly.active and not usable(scene, camera):
                dolly.switch_camera(scene, None)
        except Exception:
            nodes.apply_camera_plan(plan, rollback=True)
            raise
        settings.camera = camera
        scene.camera = camera
        settings.camera_error = ''
        committed = True
        from . import apply_enabled
        apply_enabled(scene)
        _states[key] = _state(scene)
        _refresh_views(scene)
        return True
    except (ValueError, RuntimeError, ReferenceError) as exc:
        if committed:
            # Camera/driver changes already succeeded. A redraw or visibility
            # error must not roll back just the two selection pointers.
            settings.camera_error = 'カメラ切り替え後の表示更新を再試行します：' + str(exc)
            _states.pop(key, None)
            return False
        # The dolly switch and node writer each roll back their own changes.
        settings.camera = previous
        scene.camera = previous
        settings.camera_error = 'カメラを切り替えられません：' + str(exc)
        settings.status = settings.camera_error
        # Deleting a camera clears Blender ID pointers before this callback.
        # Even a hand-edited graph must not prevent restoring original display.
        if not usable(scene, previous):
            dolly.switch_camera(scene, None)
        from . import apply_enabled
        apply_enabled(scene)
        _states[key] = _state(scene)
        _refresh_views(scene)
        return False
    finally:
        _busy.discard(key)


def update_reference(settings, _context):
    scene = settings.id_data
    synchronize(scene, settings.camera, previous=scene.camera)


@persistent
def _update(scene, *_args):
    synchronize(scene)


def _notify():
    for scene in bpy.data.scenes:
        synchronize(scene)


def _subscribe():
    bpy.msgbus.clear_by_owner(_owner)
    bpy.msgbus.subscribe_rna(key=(bpy.types.Scene, 'camera'), owner=_owner,
                            args=(), notify=_notify, options={'PERSISTENT'})


@persistent
def _history_pre(*_args):
    global _suspended
    _suspended = True
    _states.clear()


@persistent
def _load_post(*_args):
    global _suspended
    _suspended = False
    _states.clear()
    _subscribe()
    # Main load_post upgrades legacy graphs before calling synchronize.


@persistent
def _history_post(*_args):
    _load_post()
    _notify()


HANDLERS = (('depsgraph_update_pre', _update), ('frame_change_post', _update),
            ('render_pre', _update), ('load_pre', _history_pre),
            ('load_post', _load_post), ('undo_pre', _history_pre),
            ('undo_post', _history_post), ('redo_pre', _history_pre),
            ('redo_post', _history_post))


def register(*, initialize=True):
    _history_pre()
    if initialize and hasattr(bpy.data, 'scenes'):
        _load_post()
    for name, callback in HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if callback not in handlers:
            if name == 'depsgraph_update_pre':
                handlers.insert(0, callback)
            else:
                handlers.append(callback)


def unregister():
    _history_pre()
    bpy.msgbus.clear_by_owner(_owner)
    for name, callback in HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if callback in handlers:
            handlers.remove(callback)
    _busy.clear()
