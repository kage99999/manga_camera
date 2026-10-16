"""Reversible camera dolly zoom using persistent, simple RNA drivers."""

import math
import textwrap
import uuid

import bpy
from bpy.app.handlers import persistent
from bpy.props import (BoolProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, PointerProperty, StringProperty)
from bpy.types import Operator, PropertyGroup
from mathutils import Vector


PREFIX = 'gyogan_dolly.'
TRANSFORMS = ('location', 'rotation_euler', 'rotation_quaternion',
              'rotation_axis_angle', 'scale', 'delta_location',
              'delta_rotation_euler', 'delta_rotation_quaternion', 'delta_scale')
SNAPSHOTS = ('active', 'camera', 'camera_data', 'base_location', 'base_lens',
             'depth', 'back_axis', 'near_clip', 'lens_max', 'target_position',
             'target_name', 'token', 'status', 'ratio', 'enabled')
ORIGINALS = ('original_location', 'original_delta_location',
             'original_rotation_mode', 'original_rotation_euler',
             'original_rotation_quaternion', 'original_rotation_axis_angle')
SNAPSHOTS += ORIGINALS + ('navigation_version',)
EFFECTIVE_EXPRESSION = 'min(max(r,1/b,n*1.01/d),m/b)'
_navigation_cache = {}
_editing_scenes = set()
_history_change = False
_registered_classes = []


def effective_ratio(settings):
    if not settings.enabled:
        return 1.0
    return min(max(settings.ratio, 1.0 / settings.base_lens,
                   settings.near_clip * 1.01 / settings.depth),
               settings.lens_max / settings.base_lens)


def _factor(settings):
    # This getter is for UI / diagnostics only. Node drivers read the source
    # properties directly: a Scene driver reading its own derived property
    # would create a PARAMETERS_EVAL dependency cycle in Blender 5.2.
    return (effective_ratio(settings)
            if settings.active and settings.compensate_fisheye else 1.0)


def _matches(path, prefixes):
    return any(path == prefix or path.startswith(prefix + '.')
               or path.startswith(prefix + '[') for prefix in prefixes)


def _action_curves(action, slot_handle=None):
    if not action:
        return
    for curve in getattr(action, 'fcurves', ()):
        yield curve
    for layer in getattr(action, 'layers', ()):
        for strip in layer.strips:
            for bag in getattr(strip, 'channelbags', ()):
                if slot_handle is None or bag.slot_handle == slot_handle:
                    yield from bag.fcurves


def _strip_curves(strip):
    yield from _action_curves(getattr(strip, 'action', None),
                             getattr(strip, 'action_slot_handle', None))
    for child in getattr(strip, 'strips', ()):
        yield from _strip_curves(child)


def _has_animation(owner, paths):
    animation = owner.animation_data
    if not animation:
        return False
    curves = list(animation.drivers)
    curves.extend(_action_curves(animation.action,
                                 getattr(animation, 'action_slot_handle', None)))
    for track in animation.nla_tracks:
        for strip in track.strips:
            curves.extend(_strip_curves(strip))
    return any(_matches(curve.data_path, paths) for curve in curves)


def _editable(owner):
    return not owner.library and not owner.override_library and owner.is_editable


def _near(values, expected):
    return all(abs(a - b) <= 1e-6 for a, b in zip(values, expected))


def _preflight(scene, camera=None, *, allow_active=False):
    settings = scene.gyogan_dolly
    legacy_dolly = getattr(scene, 'mpm_dolly_props', None)
    if legacy_dolly and legacy_dolly.enabled:
        raise ValueError('既存のドリーズームをOFFにしてから魚眼ドリーを開始してください')
    if settings.active and not allow_active:
        raise ValueError('ドリーズームは開始済みです。いったん元のカメラに戻してください')
    if camera is None:
        camera = getattr(getattr(scene, 'gyogan', None), 'camera', None) or scene.camera
    if camera is None or camera.type != 'CAMERA' or camera.name not in scene.objects:
        raise ValueError('このシーンの基準カメラを指定してください')
    data = camera.data
    if data.type != 'PERSP':
        raise ValueError('ドリーズームには透視投影カメラが必要です')
    if not all(_editable(item) for item in (scene, camera, data)):
        raise ValueError('リンクやライブラリーオーバーライドのカメラ・シーンには対応していません')
    if camera.parent:
        raise ValueError('親が設定されたカメラには対応していません。独立したカメラを使用してください')
    if any(not constraint.mute for constraint in camera.constraints):
        raise ValueError('制約が有効なカメラには対応していません。制約のないカメラを使用してください')
    if (not _near(camera.delta_location, (0, 0, 0))
            or not _near(camera.delta_rotation_euler, (0, 0, 0))
            or not _near(camera.delta_rotation_quaternion, (1, 0, 0, 0))
            or not _near(camera.delta_scale, (1, 1, 1))):
        raise ValueError('デルタトランスフォームがあるカメラには対応していません')
    if _has_animation(camera, TRANSFORMS) or _has_animation(data, ('lens',)):
        raise ValueError('カメラの移動・回転・拡縮や焦点距離にアニメーション、NLA、ドライバーがあります。独立したカメラを使用してください')
    if len(camera.users_scene) > 1:
        raise ValueError('複数シーンで共有されたカメラには対応していません')
    if any(obj != camera and obj.type == 'CAMERA' and obj.data == data
           for obj in bpy.data.objects):
        raise ValueError('カメラデータが共有されています。カメラデータを独立させてください')
    protected = tuple(PREFIX + name for name in SNAPSHOTS if name not in {'ratio'})
    if _has_animation(scene, protected):
        raise ValueError('ドリーズームの内部設定にアニメーションやドライバーがあるため開始できません')
    target = settings.target
    if target and target.name not in scene.objects:
        raise ValueError('基準の被写体は、このシーンのオブジェクトを指定してください')
    return camera


def _specs(scene, legacy=None):
    """Reconstruct exact signatures so altered drivers are never claimed."""
    settings = scene.gyogan_dolly
    if legacy is None:
        legacy = settings.navigation_version < 1
    owner_name = 'owner_' + settings.token
    common = {
        'r': PREFIX + 'ratio', 'e': PREFIX + 'enabled',
        'b': PREFIX + 'base_lens', 'd': PREFIX + 'depth',
        'n': PREFIX + 'near_clip', 'm': PREFIX + 'lens_max',
        owner_name: PREFIX + 'active',
    }
    effective = f'({EFFECTIVE_EXPRESSION} if e else 1)'
    suffix = '+0*' + owner_name
    result = []
    if settings.camera:
        for axis in range(3):
            variables = dict(common, p=f'{PREFIX}base_location[{axis}]',
                             a=f'{PREFIX}back_axis[{axis}]')
            if legacy:
                result.append((settings.camera, 'location', axis, variables,
                               f'p+a*d*({effective}-1){suffix}'))
            else:
                variables.pop('p')
                result.append((settings.camera, 'delta_location', axis, variables,
                               f'a*d*({effective}-1){suffix}'))
    if settings.camera_data:
        result.append((settings.camera_data, 'lens', None, common,
                       f'b*{effective}{suffix}'))
    return result


def _find_curve(owner, path, index):
    animation = owner.animation_data
    if animation:
        return next((curve for curve in animation.drivers
                     if curve.data_path == path
                     and (index is None or curve.array_index == index)), None)
    return None


def _owned(curve, scene, variables, expression):
    if (curve is None or curve.mute or curve.modifiers
            or curve.keyframe_points or curve.sampled_points):
        return False
    driver = curve.driver
    if driver.type != 'SCRIPTED' or driver.expression != expression or driver.use_self:
        return False
    if len(driver.variables) != len(variables):
        return False
    for variable in driver.variables:
        if (variable.type != 'SINGLE_PROP' or variable.name not in variables
                or len(variable.targets) != 1):
            return False
        target = variable.targets[0]
        if (target.id != scene or target.data_path != variables[variable.name]
                or getattr(target, 'use_fallback_value', False)):
            return False
    return True


def _configure_driver(curve, scene, variables, expression):
    # Blender creates two identity-mapping points with each new driver. These
    # freshly owned curves use the expression directly; remove that implicit
    # mapping so any later user-authored mapping is an ownership conflict.
    curve.keyframe_points.clear()
    driver = curve.driver
    driver.type = 'SCRIPTED'
    driver.use_self = False
    for old_variable in list(driver.variables):
        driver.variables.remove(old_variable)
    for name, data_path in variables.items():
        variable = driver.variables.new()
        variable.name = name
        variable.type = 'SINGLE_PROP'
        target = variable.targets[0]
        target.id_type = 'SCENE'
        target.id = scene
        target.data_path = data_path
    driver.expression = expression


def configure_factor_driver(curve, scene):
    """Drive an owned node socket directly, without a Scene self-dependency."""
    variables = {
        'r': PREFIX + 'ratio', 'e': PREFIX + 'enabled',
        'c': PREFIX + 'compensate_fisheye', 't': PREFIX + 'active',
        'b': PREFIX + 'base_lens', 'd': PREFIX + 'depth',
        'n': PREFIX + 'near_clip', 'm': PREFIX + 'lens_max',
    }
    _configure_driver(curve, scene, variables,
                      f'{EFFECTIVE_EXPRESSION} if t and e and c else 1')


def _install_driver(scene, spec, created):
    owner, path, index, variables, expression = spec
    curve = owner.driver_add(path) if index is None else owner.driver_add(path, index)
    created.append((owner, curve))
    _configure_driver(curve, scene, variables, expression)


def _raw_pose(camera):
    return (tuple(camera.location), camera.rotation_mode,
            tuple(camera.rotation_euler), tuple(camera.rotation_quaternion),
            tuple(camera.rotation_axis_angle))


def _prime_navigation(scene):
    settings = scene.gyogan_dolly
    key = scene.as_pointer()
    if settings.active and settings.navigation_version >= 1 and settings.camera:
        _navigation_cache[key] = _raw_pose(settings.camera)
    else:
        _navigation_cache.pop(key, None)


def _capture_original(settings, camera, location=None):
    settings.original_location = camera.location if location is None else location
    settings.original_delta_location = camera.delta_location
    settings.original_rotation_mode = camera.rotation_mode
    settings.original_rotation_euler = camera.rotation_euler
    settings.original_rotation_quaternion = camera.rotation_quaternion
    settings.original_rotation_axis_angle = camera.rotation_axis_angle


def _foreign_animation(owner, paths, scene):
    """Include newly added keys/NLA; only exact add-on drivers are exempt."""
    animation = owner.animation_data
    if not animation:
        return False
    specs = [spec for spec in _specs(scene) if spec[0] == owner]
    for curve in animation.drivers:
        if not _matches(curve.data_path, paths):
            continue
        if not any(curve.data_path == path
                   and (index is None or curve.array_index == index)
                   and _owned(curve, scene, variables, expression)
                   for _, path, index, variables, expression in specs):
            return True
    curves = list(_action_curves(animation.action,
                                 getattr(animation, 'action_slot_handle', None)))
    for track in animation.nla_tracks:
        for strip in track.strips:
            curves.extend(_strip_curves(strip))
    return any(_matches(curve.data_path, paths) for curve in curves)


def _runtime_conflict(scene):
    settings = scene.gyogan_dolly
    camera = settings.camera
    if not camera or not settings.camera_data:
        return 'カメラまたはカメラデータが削除されています'
    if camera.data != settings.camera_data:
        return 'カメラデータが差し替えられています'
    if camera.parent or any(not item.mute for item in camera.constraints):
        return 'カメラに親または制約が追加されています'
    if len(camera.users_scene) > 1 or any(
            obj != camera and obj.type == 'CAMERA' and obj.data == camera.data
            for obj in bpy.data.objects):
        return 'カメラまたはカメラデータが共有されています'
    if (_foreign_animation(camera, TRANSFORMS, scene)
            or _foreign_animation(camera.data, ('lens',), scene)):
        return 'カメラにアニメーションまたは別のドライバーが追加されています'
    if camera.data.type != 'PERSP':
        return 'カメラが透視投影以外に変更されています'
    if (not _near(camera.delta_rotation_euler, (0., 0., 0.))
            or not _near(camera.delta_rotation_quaternion, (1., 0., 0., 0.))
            or not _near(camera.delta_scale, (1., 1., 1.))):
        return 'カメラのデルタ回転またはデルタ拡縮が変更されています'
    for owner, path, index, variables, expression in _specs(scene):
        if not _owned(_find_curve(owner, path, index), scene, variables, expression):
            return '管理ドライバーが変更または削除されています'
    return ''


def _stop_conflicted(scene, reason):
    """Release only owned channels, leaving all user channels and keys intact."""
    settings = scene.gyogan_dolly
    for owner, path, index, variables, expression in _specs(scene):
        curve = _find_curve(owner, path, index)
        if _owned(curve, scene, variables, expression):
            owner.animation_data.drivers.remove(curve)
            owner.update_tag()
    settings.active = False
    settings.status = reason + '。変更を保持してドリーズームを停止しました'
    _navigation_cache.pop(scene.as_pointer(), None)
    scene.update_tag()


def update_navigation(scene, depsgraph=None):
    """Turn camera navigation into dolly input while preserving target scale.

    Only raw location/rotation are observed. Ratio animation changes the driven
    delta, never these channels. All drivers keep Scene-only dependencies.
    """
    if _history_change or not hasattr(scene, 'gyogan_dolly'):
        return False
    key = scene.as_pointer()
    settings = scene.gyogan_dolly
    if key in _editing_scenes or not settings.active or settings.navigation_version < 1:
        return False
    _editing_scenes.add(key)
    try:
        conflict = _runtime_conflict(scene)
        if conflict:
            _stop_conflicted(scene, conflict)
            return False
        camera = settings.camera
        current = _raw_pose(camera)
        previous = _navigation_cache.get(key)
        if previous is None:
            _navigation_cache[key] = current
            return False
        if current == previous:
            return False
        # Blender's camera-view navigation subtracts the current delta when
        # decomposing its requested matrix into ordinary location/rotation.
        displayed = camera.location + camera.delta_location
        axis = camera.matrix_basis.to_3x3().col[2].normalized()
        ratio = effective_ratio(settings)
        projected = (displayed - Vector(settings.target_position)).dot(axis)
        valid = (math.isfinite(projected) and axis.length > .99
                 and projected > settings.near_clip * 1.01)
        depth = settings.depth
        previous_position = Vector(previous[0]) + camera.delta_location
        previous_axis = Vector(settings.back_axis)
        previous_depth = (previous_position - Vector(settings.target_position)).dot(previous_axis)
        # A forward drag through the target stops at the nearest legal ratio.
        # Turning away instead keeps the requested pose without teleporting.
        forward_drag = ((axis - previous_axis).length < 1.e-5
                        and previous_depth > 0.0
                        and math.isfinite(projected))
        status = '現在の構図を基準にドリーズームを続けます'
        if settings.enabled and (valid or forward_drag):
            requested = projected / depth
            if _has_animation(scene, (PREFIX + 'ratio',)):
                status = '倍率はアニメーションで制御中です。前後移動はドリー倍率のキーやドライバーで調整してください'
            else:
                prop = settings.bl_rna.properties['ratio']
                settings.ratio = min(max(requested, prop.hard_min), prop.hard_max)
                ratio = effective_ratio(settings)
                status = ('倍率・レンズの範囲内で前後移動を止めています'
                          if abs(requested - ratio) > 1.e-5
                          else 'カメラの距離に合わせて倍率と焦点距離を変更しました')
            # The lens limit and the ratio limit must also limit axial motion;
            # otherwise reaching either limit would change the target's size.
            displayed += axis * (depth * ratio - projected)
        elif valid:
            # OFF is ordinary framing. Re-ON uses the stored ratio from here.
            depth = projected / ratio
        else:
            status = '基準の被写体がカメラ前方から外れたため、直前の倍率と基準距離を保持しています'
        offset = axis * depth * (ratio - 1.)
        settings.base_location = displayed - offset
        settings.back_axis = axis
        settings.depth = depth
        camera.location = settings.base_location
        _navigation_cache[key] = _raw_pose(camera)
        settings.status = status
        camera.update_tag()
        camera.data.update_tag()
        scene.update_tag()
        return True
    finally:
        _editing_scenes.discard(key)


def upgrade_saved(scene):
    """Move untouched 0.1.9 location drivers to free-camera delta drivers."""
    settings = scene.gyogan_dolly
    if not settings.active or settings.navigation_version >= 1:
        _prime_navigation(scene)
        return False
    conflict = _runtime_conflict(scene)
    if conflict:
        settings.status = conflict + '。変更を保持し、自由なカメラ操作への更新を見送りました'
        return False
    old_specs = _specs(scene, legacy=True)
    if len(old_specs) != 4 or any(
            not _owned(_find_curve(owner, path, index), scene, variables, expression)
            for owner, path, index, variables, expression in old_specs):
        settings.status = '以前の管理ドライバーが変更されています。変更を保持し、自由なカメラ操作への更新を見送りました'
        return False
    camera = settings.camera
    if (camera is None or camera.data != settings.camera_data
            or any(_find_curve(camera, 'delta_location', i) for i in range(3))
            or not _near(camera.delta_location, (0., 0., 0.))):
        settings.status = 'カメラの変更を保持し、自由なカメラ操作への更新を見送りました'
        return False
    key = scene.as_pointer()
    _editing_scenes.add(key)
    previous = {name: getattr(settings, name) for name in ORIGINALS}
    previous = {name: value if isinstance(value, str) else tuple(value)
                for name, value in previous.items()}
    old_location = camera.location.copy()
    created = []
    try:
        _capture_original(settings, camera, settings.base_location)
        settings.navigation_version = 1
        for spec in _specs(scene):
            if spec[1] == 'delta_location':
                _install_driver(scene, spec, created)
        for owner, path, index, variables, expression in old_specs:
            if path == 'location':
                owner.animation_data.drivers.remove(_find_curve(owner, path, index))
        camera.location = settings.base_location
        camera.delta_location = (Vector(settings.back_axis) * settings.depth
                                 * (effective_ratio(settings) - 1.))
        settings.status = 'カメラビューの移動・回転に対応しました。現在の構図と倍率を保持しています'
        _prime_navigation(scene)
        camera.update_tag()
        scene.update_tag()
        return True
    except Exception:
        for owner, curve in reversed(created):
            owner.animation_data.drivers.remove(curve)
        settings.navigation_version = 0
        for name, value in previous.items():
            setattr(settings, name, value)
        camera.location = old_location
        camera.delta_location = (0., 0., 0.)
        for spec in old_specs:
            if spec[1] == 'location' and _find_curve(spec[0], spec[1], spec[2]) is None:
                _install_driver(scene, spec, [])
        camera.update_tag()
        scene.update_tag()
        raise
    finally:
        _editing_scenes.discard(key)


def _measure_setup(scene, camera, target_position=None):
    """Validate the geometry before changing settings or either camera."""
    settings = scene.gyogan_dolly
    with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
        bpy.context.view_layer.update()
        graph = bpy.context.evaluated_depsgraph_get()
        target = (Vector(target_position) if target_position is not None else
                  settings.target.evaluated_get(graph).matrix_world.translation.copy()
                  if settings.target else scene.cursor.location.copy())
    # No parent, constraints or transform animation: location equals world location.
    location = camera.location.copy()
    axis = camera.matrix_world.to_3x3().col[2].normalized()
    depth = (location - target).dot(axis)
    if (not math.isfinite(depth) or axis.length < 0.99
            or depth <= camera.data.clip_start * 1.01):
        raise ValueError('基準の被写体をカメラ前方の表示範囲に置いてください。未指定の場合は3Dカーソルを使用します')
    return target, location, axis, depth


def _settings_snapshot(settings):
    previous = {name: getattr(settings, name) for name in SNAPSHOTS}
    for name in ('base_location', 'back_axis', 'target_position') + ORIGINALS:
        if not isinstance(previous[name], str):
            previous[name] = tuple(previous[name])
    return previous


def _write_settings(settings, values):
    for name, value in values.items():
        setattr(settings, name, value)


def _setup_camera(scene, camera, measured, *, target_name=None, enabled=True):
    settings = scene.gyogan_dolly
    target, location, axis, depth = measured
    previous = _settings_snapshot(settings)
    created = []
    key = scene.as_pointer()
    already_editing = key in _editing_scenes
    _editing_scenes.add(key)
    try:
        settings.camera = camera
        settings.camera_data = camera.data
        settings.base_location = location
        settings.base_lens = camera.data.lens
        _capture_original(settings, camera)
        settings.navigation_version = 1
        settings.back_axis = axis
        settings.depth = depth
        settings.near_clip = camera.data.clip_start
        settings.lens_max = camera.data.bl_rna.properties['lens'].hard_max
        settings.target_position = target
        settings.target_name = (target_name if target_name is not None else
                                settings.target.name if settings.target else '3Dカーソル')
        settings.token = uuid.uuid4().hex
        settings.ratio = 1.0
        settings.enabled = enabled
        settings.active = True
        for spec in _specs(scene):
            _install_driver(scene, spec, created)
        settings.status = 'カメラビューで構図を調整できます。ドリー倍率で距離と焦点距離を連動します'
        _prime_navigation(scene)
        camera.update_tag()
        camera.data.update_tag()
        scene.update_tag()
    except Exception:
        for owner, curve in reversed(created):
            owner.animation_data.drivers.remove(curve)
        camera.location = location
        camera.delta_location = settings.original_delta_location
        camera.data.lens = settings.base_lens
        _write_settings(settings, previous)
        _prime_navigation(scene)
        camera.update_tag()
        camera.data.update_tag()
        scene.update_tag()
        raise
    finally:
        if not already_editing:
            _editing_scenes.discard(key)
    return camera


def setup(scene, camera=None):
    """Capture a static camera and target, then attach four owned drivers."""
    camera = _preflight(scene, camera)
    return _setup_camera(scene, camera, _measure_setup(scene, camera))


def switch_camera(scene, camera):
    """Move an active dolly to a new camera without changing its saved target.

    New drivers are prepared while the old four drivers are still intact. The
    old camera is released only after that succeeds, so setup failures preserve
    both the original camera's current pose and its existing driver identities.
    """
    settings = scene.gyogan_dolly
    if not settings.active:
        return
    if camera is None:
        restore(scene)
        return
    if camera == settings.camera:
        return
    if _has_animation(scene, (PREFIX + 'ratio',)):
        raise ValueError('ドリー倍率にアニメーションやドライバーがあります。元のカメラへ戻してから基準カメラを切り替えてください')
    _preflight(scene, camera, allow_active=True)
    if settings.camera:
        conflict = _runtime_conflict(scene)
        if conflict:
            raise ValueError(conflict + '。元のカメラへ戻してから基準カメラを切り替えてください')
    else:
        # A deleted object can leave its camera data and its lens driver alive.
        # Release that driver only if the complete ownership signature matches.
        for owner, path, index, variables, expression in _specs(scene):
            if not _editable(owner) or not _owned(
                    _find_curve(owner, path, index), scene, variables, expression):
                raise ValueError('削除されたカメラの焦点距離に変更があります。元のカメラへ戻してから基準カメラを切り替えてください')
    key = scene.as_pointer()
    already_editing = key in _editing_scenes
    _editing_scenes.add(key)
    try:
        measured = _measure_setup(scene, camera, settings.target_position)
    finally:
        if not already_editing:
            _editing_scenes.discard(key)
    previous = _settings_snapshot(settings)
    old_specs = _specs(scene)
    old_camera = settings.camera
    old_pose = _raw_pose(old_camera) if old_camera else None
    old_delta = tuple(old_camera.delta_location) if old_camera else None
    old_lens = settings.camera_data.lens if settings.camera_data else None
    new_delta, new_lens = tuple(camera.delta_location), camera.data.lens
    new_animation = tuple(owner.animation_data is not None for owner in (camera, camera.data))
    old_cache = _navigation_cache.get(key)
    _editing_scenes.add(key)
    installed = None
    try:
        _setup_camera(scene, camera, measured, target_name=previous['target_name'],
                      enabled=previous['enabled'])
        installed = _settings_snapshot(settings)
        _write_settings(settings, previous)
        restore(scene)
        _editing_scenes.add(key)
        _write_settings(settings, installed)
        settings.status = '基準カメラを切り替えました。新しいカメラの構図をドリー倍率1の基準にしました'
        _prime_navigation(scene)
        camera.update_tag()
        camera.data.update_tag()
        scene.update_tag()
    except Exception:
        if installed is not None:
            _write_settings(settings, installed)
            for owner, path, index, variables, expression in _specs(scene):
                curve = _find_curve(owner, path, index)
                if _owned(curve, scene, variables, expression):
                    owner.animation_data.drivers.remove(curve)
        _write_settings(settings, previous)
        if old_camera:
            (location, mode, euler, quaternion, axis_angle) = old_pose
            old_camera.location = location
            old_camera.delta_location = old_delta
            old_camera.rotation_mode = mode
            old_camera.rotation_euler = euler
            old_camera.rotation_quaternion = quaternion
            old_camera.rotation_axis_angle = axis_angle
            old_camera.update_tag()
        if settings.camera_data:
            settings.camera_data.lens = old_lens
            settings.camera_data.update_tag()
        # Usually no old driver has been removed yet. This also rolls back an
        # exceptional failure while releasing the old camera's owned channels.
        for owner, path, index, variables, expression in old_specs:
            if _find_curve(owner, path, index) is None:
                curve = owner.driver_add(path) if index is None else owner.driver_add(path, index)
                _configure_driver(curve, scene, variables, expression)
        camera.location = measured[1]
        camera.delta_location = new_delta
        camera.data.lens = new_lens
        for owner, had_animation in zip((camera, camera.data), new_animation):
            animation = owner.animation_data
            if (not had_animation and animation and not animation.drivers
                    and not animation.action and not animation.nla_tracks):
                owner.animation_data_clear()
            owner.update_tag()
        if old_cache is None:
            _navigation_cache.pop(key, None)
        else:
            _navigation_cache[key] = old_cache
        scene.update_tag()
        raise
    finally:
        if not already_editing:
            _editing_scenes.discard(key)


def restore(scene):
    """Restore only unchanged owned channels; preserve edits and explain them."""
    settings = scene.gyogan_dolly
    if not settings.active:
        return []
    key = scene.as_pointer()
    _editing_scenes.add(key)
    warnings = []
    if settings.camera is None:
        warnings.append('元のカメラが削除されています')
    elif settings.camera.data != settings.camera_data:
        warnings.append('カメラデータの差し替えを保持しました')
    try:
        camera = settings.camera
        protected = bool(camera and (camera.parent
                         or any(not item.mute for item in camera.constraints)
                         or _foreign_animation(camera, tuple(
                             name for name in TRANSFORMS if name != 'delta_location'), scene)))
        lens_protected = bool(settings.camera_data and _foreign_animation(
            settings.camera_data, ('lens',), scene))
        if protected:
            warnings.append('カメラに追加されたアニメーション・親・制約を保持しました')
        for owner, path, index, variables, expression in _specs(scene):
            curve = _find_curve(owner, path, index)
            if not _owned(curve, scene, variables, expression):
                label = (f'カメラ位置{"XYZ"[index]}' if path in {'location', 'delta_location'}
                         else '焦点距離' if path == 'lens' else '魚眼の補正')
                warnings.append(f'{label}の変更を保持しました（管理ドライバーが変更または削除されています）')
                continue
            owner.animation_data.drivers.remove(curve)
            if path == 'location':
                if not protected:
                    owner.location[index] = settings.base_location[index]
            elif path == 'delta_location':
                if not protected:
                    owner.delta_location[index] = settings.original_delta_location[index]
                    owner.location[index] = settings.original_location[index]
            elif path == 'lens':
                if not lens_protected:
                    owner.lens = settings.base_lens
                else:
                    warnings.append('焦点距離に追加されたアニメーションを保持しました')
            owner.update_tag()
        if camera and settings.navigation_version >= 1 and not protected:
            camera.rotation_mode = settings.original_rotation_mode
            camera.rotation_euler = settings.original_rotation_euler
            camera.rotation_quaternion = settings.original_rotation_quaternion
            camera.rotation_axis_angle = settings.original_rotation_axis_angle
        settings.active = False
        settings.status = '／'.join(warnings) if warnings else '開始前のカメラ位置・向き・焦点距離に戻しました'
        _navigation_cache.pop(key, None)
        # Keep snapshots and ratio animation: actions belong to the user.
        scene.update_tag()
        return warnings
    finally:
        _editing_scenes.discard(key)


class GYOGAN_DollySettings(PropertyGroup):
    active: BoolProperty(default=False, options=set())
    camera: PointerProperty(type=bpy.types.Object, options=set())
    camera_data: PointerProperty(type=bpy.types.Camera, options=set())
    target: PointerProperty(name='基準の被写体', type=bpy.types.Object,
                            description='開始時の原点位置を使用します。未指定の場合は3Dカーソルの位置を使用します。開始後の移動には追従しません',
                            options=set())
    ratio: FloatProperty(name='ドリー倍率', default=1., min=.1, max=5.,
                         soft_min=.1, soft_max=5., precision=3,
                         description='1で現在の構図の基準位置。小さくするとカメラが近づき広角、大きくすると離れて望遠になります。キーフレームを設定できます')
    enabled: BoolProperty(name='ドリーズーム', default=True, options=set(),
                          description='オフで現在の構図の基準位置と焦点距離に戻します。倍率とキーフレームは保持します')
    compensate_fisheye: BoolProperty(name='魚眼の大きさも補正', default=True,
                                     options=set(),
                                     description='魚眼による焦点距離の打ち消しを補正します。魚眼の強い周辺まで大きさが一致するとは限りません')
    factor: FloatProperty(default=1., get=_factor, options={'HIDDEN'})
    base_location: FloatVectorProperty(size=3, options={'HIDDEN'})
    base_lens: FloatProperty(default=50., options={'HIDDEN'})
    depth: FloatProperty(default=1., options={'HIDDEN'})
    back_axis: FloatVectorProperty(size=3, default=(0., 0., 1.), options={'HIDDEN'})
    near_clip: FloatProperty(default=.1, options={'HIDDEN'})
    lens_max: FloatProperty(default=5000., options={'HIDDEN'})
    target_position: FloatVectorProperty(size=3, options={'HIDDEN'})
    target_name: StringProperty(options={'HIDDEN'})
    token: StringProperty(options={'HIDDEN'})
    navigation_version: IntProperty(default=0, options={'HIDDEN'})
    original_location: FloatVectorProperty(size=3, options={'HIDDEN'})
    original_delta_location: FloatVectorProperty(size=3, options={'HIDDEN'})
    original_rotation_mode: StringProperty(default='XYZ', options={'HIDDEN'})
    original_rotation_euler: FloatVectorProperty(size=3, options={'HIDDEN'})
    original_rotation_quaternion: FloatVectorProperty(size=4, default=(1., 0., 0., 0.), options={'HIDDEN'})
    original_rotation_axis_angle: FloatVectorProperty(size=4, default=(0., 0., 1., 0.), options={'HIDDEN'})
    status: StringProperty(options=set())


class GYOGAN_OT_dolly_start(Operator):
    bl_idname = 'gyogan.dolly_start'
    bl_label = '基準を登録'
    bl_description = '現在の位置・向き・焦点距離を保存して、ドリーズームを開始します'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            setup(context.scene)
        except Exception as exc:
            context.scene.gyogan_dolly.status = f'開始できません：{exc}'
            self.report({'ERROR'}, context.scene.gyogan_dolly.status)
            return {'CANCELLED'}
        self.report({'INFO'}, 'ドリーズームを開始しました。倍率を調整してください')
        return {'FINISHED'}


class GYOGAN_OT_dolly_toggle(Operator):
    bl_idname = 'gyogan.dolly_toggle'
    bl_label = 'ドリーズームを切り替え'
    bl_description = ('初回のオンで現在のカメラと被写体の基準を自動保存します。'
                      'オフで倍率1へ戻し、再度オンにすると前の倍率で再開します')
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        settings = scene.gyogan_dolly
        if not settings.active:
            return GYOGAN_OT_dolly_start.execute(self, context)
        settings.enabled = not settings.enabled
        settings.status = ('ドリーズームをオンにしました。前の倍率で再開します'
                           if settings.enabled else 'ドリーズームをオフにしました。倍率と基準は保持しています')
        scene.update_tag()
        self.report({'INFO'}, settings.status)
        return {'FINISHED'}


class GYOGAN_OT_dolly_restore(Operator):
    bl_idname = 'gyogan.dolly_restore'
    bl_label = '元のカメラへ戻す'
    bl_description = 'ドリーズームを取り外し、開始前の位置・向き・焦点距離に戻します。倍率のアニメーションは保持します'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.gyogan_dolly.active

    def execute(self, context):
        warnings = restore(context.scene)
        self.report({'WARNING'} if warnings else {'INFO'}, context.scene.gyogan_dolly.status)
        return {'FINISHED'}


def draw(layout, context):
    settings = context.scene.gyogan_dolly
    box = layout.box()
    box.label(text='ドリーズーム', icon='CAMERA_DATA')
    target = box.row()
    target.enabled = not settings.active
    target.prop(settings, 'target')
    if not settings.active and settings.target is None:
        box.label(text='未指定：3Dカーソルの位置を使用', icon='PIVOT_CURSOR')
    is_on = settings.active and settings.enabled
    switch = box.row()
    switch.use_property_split = False
    switch.scale_y = 1.4
    switch.operator('gyogan.dolly_toggle',
                    text='ドリーズーム：オン' if is_on else 'ドリーズーム：オフ',
                    icon='HIDE_OFF' if is_on else 'HIDE_ON', depress=is_on)
    if settings.active:
        controls = box.column()
        controls.enabled = settings.enabled
        controls.prop(settings, 'ratio', slider=True)
        controls.prop(settings, 'compensate_fisheye')
        if settings.navigation_version >= 1:
            space = context.space_data
            if space and space.type == 'VIEW_3D':
                box.prop(space, 'lock_camera', text='ビューにカメラをロック')
            box.label(text='前後移動で倍率・焦点距離が連動します')
            if (context.scene.gyogan.enabled and abs(context.scene.gyogan.strength) > 1.e-6
                    and not settings.compensate_fisheye):
                box.label(text='魚眼の大きさを保つには補正をオン', icon='INFO')
            if context.scene.tool_settings.use_keyframe_insert_auto:
                box.label(text='構図変更時は自動キーをオフにしてください', icon='INFO')
        if settings.enabled and abs(effective_ratio(settings) - settings.ratio) > 1e-5:
            box.label(text=f'カメラの限界で実際の倍率：{effective_ratio(settings):.3f}', icon='INFO')
        box.label(text=f'開始時の基準：{settings.target_name}')
        box.label(text='小さく：近づいて広角')
        box.label(text='大きく：離れて望遠')
        box.operator('gyogan.dolly_restore', icon='LOOP_BACK')
    else:
        box.prop(settings, 'compensate_fisheye')
    for line in textwrap.wrap(settings.status, width=26):
        box.label(text=line)


CLASSES = (GYOGAN_DollySettings, GYOGAN_OT_dolly_start,
           GYOGAN_OT_dolly_toggle, GYOGAN_OT_dolly_restore)


@persistent
def _navigation_pre(scene, depsgraph=None):
    try:
        update_navigation(scene, depsgraph)
    except (ReferenceError, RuntimeError) as exc:
        print('ドリーズームのカメラ更新：' + str(exc))


@persistent
def _history_pre(*_args):
    global _history_change
    _history_change = True
    _navigation_cache.clear()


@persistent
def _history_post(*_args):
    global _history_change
    _history_change = False
    _navigation_cache.clear()
    for scene in bpy.data.scenes:
        if hasattr(scene, 'gyogan_dolly'):
            _prime_navigation(scene)


HANDLERS = (('depsgraph_update_pre', _navigation_pre),
            ('load_pre', _history_pre), ('load_post', _history_post),
            ('undo_pre', _history_pre), ('undo_post', _history_post),
            ('redo_pre', _history_pre), ('redo_post', _history_post))


def initialize_scenes():
    """Initialize only after Blender leaves add-on registration restrictions."""
    _history_post()
    for scene in bpy.data.scenes:
        upgrade_saved(scene)


def register(*, initialize=True):
    for cls in CLASSES:
        bpy.utils.register_class(cls)
        _registered_classes.append(cls)
    bpy.types.Scene.gyogan_dolly = PointerProperty(type=GYOGAN_DollySettings)
    for name, callback in HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if callback not in handlers:
            handlers.append(callback)
    _history_pre()
    if initialize and hasattr(bpy.data, 'scenes'):
        initialize_scenes()


def unregister():
    for name, callback in HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if callback in handlers:
            handlers.remove(callback)
    for scene in getattr(bpy.data, 'scenes', ()):
        if hasattr(scene, 'gyogan_dolly'):
            warnings = restore(scene)
            for warning in warnings:
                print('ドリーズームの復元：' + warning)
    if hasattr(bpy.types.Scene, 'gyogan_dolly'):
        del bpy.types.Scene.gyogan_dolly
    _navigation_cache.clear()
    for cls in reversed(_registered_classes):
        bpy.utils.unregister_class(cls)
    _registered_classes.clear()
