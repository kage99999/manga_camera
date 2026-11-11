# -*- coding: utf-8 -*-
# Manga Camera 1.220: portable fisheye stock settings and safe restoration.
"""Serialize working settings, never generated geometry or Blender RNA pointers."""

import json
import math

NUMBERS = {
    'strength': (-5., 5.), 'fov': (math.radians(10), math.radians(175)),
    'zoom': (.05, 10.), 'ellipse_x': (.1, 5.), 'ellipse_y': (.1, 5.),
    'near_distance': (.0001, 1000.), 'viewport_levels': (0, 3),
    'render_levels': (0, 4), 'face_limit': (1000, 20000000),
}
INTEGERS = {'viewport_levels', 'render_levels', 'face_limit'}
ENUMS = {
    'center_mode': {'DEFAULT', 'OBJECT', 'CURSOR'},
    'subdivision': {'NONE', 'SIMPLE', 'CATMULL_CLARK'},
    'mode': {'MESH', 'ARMATURE'}, 'effect_scope': {'CAMERA', 'MANUAL', 'SCENE'},
    'scope': {'SELECTED', 'COLLECTION', 'SCENE', 'CAMERA'},
}
VALUES = tuple(NUMBERS) + tuple(ENUMS) + ('thickness',)
DOLLY_VECTORS = ('back_axis', 'target_position', 'original_location',
                 'original_delta_location', 'original_rotation_euler',
                 'original_rotation_quaternion', 'original_rotation_axis_angle')
DOLLY_NUMBERS = ('base_lens', 'depth', 'near_clip', 'lens_max')


def _number(value, low, high, *, integer=False):
    if (type(value) not in ({int} if integer else {int, float})
            or not math.isfinite(value) or not low - 1e-6 <= value <= high + 1e-6):
        raise ValueError('魚眼記録の数値が不正です')
    return int(value) if integer else float(value)


def _bool(value):
    if type(value) is not bool:
        raise ValueError('魚眼記録のON/OFFが不正です')
    return value


def _name(value):
    if not isinstance(value, str):
        raise ValueError('魚眼記録の参照名が不正です')
    return value


def _vector(value, size=3):
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise ValueError('魚眼記録の座標が不正です')
    return [_number(v, -1e30, 1e30) for v in value]


def normalize(state):
    if not isinstance(state, dict) or type(state.get('version')) is not int or state['version'] != 1:
        raise ValueError('魚眼記録の形式またはバージョンに対応していません')
    out = {'version': 1, 'enabled': _bool(state.get('enabled'))}
    if not out['enabled']:
        return out
    values = state.get('values')
    if not isinstance(values, dict) or set(values) != set(VALUES):
        raise ValueError('魚眼記録の設定項目が不正です')
    out['values'] = {}
    for name, value in values.items():
        if name in NUMBERS:
            value = _number(value, *NUMBERS[name], integer=name in INTEGERS)
        elif name in ENUMS:
            if not isinstance(value, str) or value not in ENUMS[name]:
                raise ValueError('魚眼記録の選択項目が不正です: ' + name)
        else:
            value = _bool(value)
        out['values'][name] = value
    for name in ('center_object', 'target_collection'):
        out[name] = _name(state.get(name))
    out['keep_bounds_helpers'] = _bool(state.get('keep_bounds_helpers'))
    if values['center_mode'] == 'CURSOR':
        out['cursor_location'] = _vector(state.get('cursor_location'))
    entries = state.get('entries')
    if not isinstance(entries, list):
        raise ValueError('魚眼記録の対象一覧が不正です')
    unique = set()
    for item in entries:
        if not isinstance(item, dict) or item.get('kind') not in ('MESH', 'ARMATURE'):
            raise ValueError('魚眼記録の対象が不正です')
        name = _name(item.get('name'))
        if not name:
            raise ValueError('魚眼記録の対象名が空です')
        unique.add((name, item['kind']))
    out['entries'] = [{'name': name, 'kind': kind} for name, kind in sorted(unique)]
    dolly = state.get('dolly')
    if not isinstance(dolly, dict):
        raise ValueError('魚眼ドリー記録が不正です')
    saved = {name: _bool(dolly.get(name)) for name in ('active', 'enabled', 'compensate_fisheye')}
    saved['ratio'] = _number(dolly.get('ratio'), .1, 5.)
    saved['target'] = _name(dolly.get('target'))
    if saved['active']:
        for name in DOLLY_NUMBERS:
            saved[name] = _number(dolly.get(name), 0., 3.5e38)
            if saved[name] <= 0:
                raise ValueError('魚眼ドリー記録の基準値は正数が必要です')
        for name in DOLLY_VECTORS:
            saved[name] = _vector(dolly.get(name), 4 if name.endswith(('quaternion', 'axis_angle')) else 3)
        saved['original_rotation_mode'] = _name(dolly.get('original_rotation_mode'))
        if saved['original_rotation_mode'] not in {'XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX', 'QUATERNION', 'AXIS_ANGLE'}:
            raise ValueError('魚眼ドリー記録の回転方式が不正です')
        saved['target_name'] = _name(dolly.get('target_name'))
        if not .99 <= sum(v * v for v in saved['back_axis']) <= 1.01:
            raise ValueError('魚眼ドリー記録の方向が不正です')
    out['dolly'] = saved
    return out


def signature(state):
    # Missing data denotes a legacy record, not an explicit preview-OFF record.
    if state is None:
        return None
    def rounded(value):
        if isinstance(value, float):
            return round(value, 6)
        if isinstance(value, dict):
            return {key: rounded(val) for key, val in value.items()}
        if isinstance(value, list):
            return [rounded(val) for val in value]
        return value
    return json.dumps(rounded(state), sort_keys=True, ensure_ascii=False, allow_nan=False)


def capture(scene):
    from . import fisheye
    if not fisheye.enabled(scene):
        return {'version': 1, 'enabled': False}
    if scene.mpm_lattice_management_enabled:
        raise ValueError('魚眼とラティスは同時に記録できません。片方をOFFにしてください')
    from .gyogan_camera import dolly
    settings, controller = scene.gyogan, scene.gyogan_dolly
    if controller.active and dolly._runtime_conflict(scene):
        raise ValueError('魚眼ドリーの制御が変更されているため記録できません')
    saved = {name: getattr(controller, name) for name in ('active', 'enabled', 'compensate_fisheye', 'ratio')}
    saved['target'] = controller.target.name if controller.target else ''
    if controller.active:
        saved.update({name: getattr(controller, name) for name in DOLLY_NUMBERS})
        saved.update({name: list(getattr(controller, name)) for name in DOLLY_VECTORS})
        saved.update(original_rotation_mode=controller.original_rotation_mode, target_name=controller.target_name)
    state = {
        'version': 1, 'enabled': True, 'values': {name: getattr(settings, name) for name in VALUES},
        'center_object': settings.center_object.name if settings.center_object else '',
        'target_collection': settings.target_collection.name if settings.target_collection else '',
        'keep_bounds_helpers': settings.keep_bounds_helpers,
        'entries': [{'name': item.source.name, 'kind': item.kind}
                    for item in settings.manual_targets if item.source],
        'dolly': saved,
    }
    if settings.center_mode == 'CURSOR':
        state['cursor_location'] = list(scene.cursor.location)
    return normalize(state)


def prepare(scene, camera, data):
    """Validate before frame/camera/lattice changes; missing references never widen scope."""
    if 'fisheye_state' not in data:
        return None
    state = normalize(data['fisheye_state'])
    if not state['enabled']:
        return state
    import bpy
    from . import fisheye
    if not fisheye.available():
        raise ValueError('この環境では魚眼機能を利用できません')
    if data.get('lattice_enabled') or data.get('lattice_state', {}).get('lattice_enabled'):
        raise ValueError('魚眼とラティスの同時記録は読み込めません')
    from .gyogan_camera import presets, dolly
    presets._check_editable(scene)
    if camera.data.type != 'PERSP':
        raise ValueError('魚眼記録には透視投影カメラが必要です')
    def resolve(name, items):
        obj = items.get(name) if name else None
        if name and obj is None:
            raise ValueError('魚眼の記録対象が見つかりません: ' + name)
        return obj
    resolved = {'values': state['values'],
                'entries': [(resolve(item['name'], scene.objects), item['kind']) for item in state['entries']],
                'center_object': resolve(state['center_object'], scene.objects),
                'target_collection': resolve(state['target_collection'], bpy.data.collections)}
    resolved = presets._validate(scene, resolved)
    target = resolve(state['dolly']['target'], scene.objects)
    if scene.gyogan_dolly.active and dolly._runtime_conflict(scene):
        raise ValueError('現在の魚眼ドリー制御が変更されています。先に解除してください')
    if state['dolly']['active']:
        if scene.mpm_dolly_props.enabled:
            raise ValueError('既存のドリーズームをOFFにしてから読み込んでください')
        # Existing owned camera drivers are released by the normal stock reader.
        if (camera.parent or not dolly._editable(camera) or not dolly._editable(camera.data)
                or any(not c.mute for c in camera.constraints) or len(camera.users_scene) != 1
                or any(obj != camera and obj.type == 'CAMERA' and obj.data == camera.data for obj in bpy.data.objects)):
            raise ValueError('魚眼ドリー記録は親・制約・共有・リンクのないカメラで読み込んでください')
        if (dolly._foreign_animation(camera, dolly.TRANSFORMS, scene)
                or dolly._foreign_animation(camera.data, ('lens',), scene)):
            raise ValueError('カメラのアニメーションと魚眼ドリー記録が競合しています')
        if (not dolly._near(camera.delta_rotation_euler, (0, 0, 0))
                or not dolly._near(camera.delta_rotation_quaternion, (1, 0, 0, 0))
                or not dolly._near(camera.delta_scale, (1, 1, 1))
                or (not scene.gyogan_dolly.active and not dolly._near(camera.delta_location, (0, 0, 0)))):
            raise ValueError('カメラのデルタ変換と魚眼ドリー記録が競合しています')
        protected = tuple('gyogan_dolly.' + name for name in dolly.SNAPSHOTS)
        if dolly._has_animation(scene, protected):
            raise ValueError('魚眼ドリーの内部設定にアニメーションがあります')
    for name in ('ratio', 'enabled', 'compensate_fisheye'):
        if dolly._has_animation(scene, ('gyogan_dolly.' + name,)):
            raise ValueError('魚眼ドリーのアニメーションと記録が競合しています: ' + name)
    return {**state, 'resolved': resolved, 'target': target}


def disable(scene):
    from . import fisheye
    if fisheye.available():
        scene.gyogan.enabled = False


def apply(scene, camera, plan):
    if plan is None or not plan['enabled']:
        return
    from mathutils import Vector
    from .gyogan_camera import presets, dolly, targets, cameras, sync_center_object, apply_enabled
    settings, controller = scene.gyogan, scene.gyogan_dolly
    # Retain the previous preset's working values; never replace the preset library.
    presets.save_active(scene)
    with presets._batch(scene):
        presets._assign(scene, plan['resolved'])
        settings.keep_bounds_helpers = plan['keep_bounds_helpers']
        if plan['values']['center_mode'] == 'CURSOR':
            scene.cursor.location = plan['cursor_location']
    cameras.synchronize(scene)
    saved = plan['dolly']
    controller.target = plan['target']
    if saved['active']:
        dolly._preflight(scene, camera)
        ratio = (min(max(saved['ratio'], 1. / saved['base_lens'],
                         saved['near_clip'] * 1.01 / saved['depth']), saved['lens_max'] / saved['base_lens'])
                 if saved['enabled'] else 1.)
        camera.location -= Vector(saved['back_axis']) * saved['depth'] * (ratio - 1.)
        camera.data.lens = saved['base_lens']
        measured = (Vector(saved['target_position']), camera.location.copy(),
                    Vector(saved['back_axis']), saved['depth'])
        dolly._setup_camera(scene, camera, measured, target_name=saved['target_name'], enabled=saved['enabled'])
        for name in DOLLY_NUMBERS + DOLLY_VECTORS + ('original_rotation_mode',):
            setattr(controller, name, saved[name])
    for name in ('ratio', 'enabled', 'compensate_fisheye'):
        setattr(controller, name, saved[name])
    if saved['active']:
        dolly._prime_navigation(scene)
    sync_center_object(scene)
    settings.subdivision_pending = True
    settings.enabled = True
    # Stock selection can be an RNA callback. Structural work stays on the safe timer.
    targets.reconcile(scene, prepare=False)
    targets.request(scene, retry=True)
    apply_enabled(scene)
    scene.update_tag()


# Version Footer: 1.220
