# -*- coding: utf-8 -*-
# Version: 1.203
"""Pose snapshots with selectable keyframe priority for camera stocks."""

import json
import math

import bpy

from .transforms import capture_rotation, normalize_rotation, apply_rotation, rotation_signature


def _numeric_properties(values):
    result = {}
    for name, value in values.items():
        if str(name).startswith('_'):
            continue
        if hasattr(value, 'to_list'):
            value = value.to_list()
        if isinstance(value, (bool, int, float)):
            if math.isfinite(value):
                result[str(name)] = value
        elif isinstance(value, (tuple, list)) and value:
            if all(isinstance(v, (bool, int, float)) and math.isfinite(v) for v in value):
                result[str(name)] = list(value)
    return result


def _vector(value, default):
    try:
        result = [float(v) for v in value]
        if len(result) == 3 and all(math.isfinite(v) for v in result):
            return result
    except (TypeError, ValueError):
        pass
    return list(default)


def normalize_pose_bones(values):
    result = []
    if not isinstance(values, list):
        return result
    for value in values:
        if not isinstance(value, dict) or not value.get('name'):
            continue
        props = value.get('custom_properties', {})
        result.append({
            'name': str(value['name']),
            'location': _vector(value.get('location'), (0, 0, 0)),
            **normalize_rotation(value),
            'scale': _vector(value.get('scale'), (1, 1, 1)),
            'custom_properties': _numeric_properties(props) if isinstance(props, dict) else {},
        })
    return sorted(result, key=lambda item: item['name'])


def normalize_pose_fields(record):
    if 'pose_bones' not in record:
        return {}
    props = record.get('pose_custom_properties', {})
    return {
        'pose_bones': normalize_pose_bones(record['pose_bones']),
        'pose_custom_properties': _numeric_properties(props) if isinstance(props, dict) else {},
    }


def _rounded(values):
    return tuple(round(float(value), 6) for value in values)


def _property_signature(values):
    return tuple(sorted((name, _rounded(value) if isinstance(value, list) else round(value, 6))
                        for name, value in values.items()))


def pose_signature(record):
    bones = record.get('pose_bones', ())
    if not bones:
        return ()
    result = []
    for bone in sorted(bones, key=lambda item: item['name']):
        mode, rotation = rotation_signature(bone)
        result.append((bone['name'], _rounded(bone['location']), mode, _rounded(rotation),
                       _rounded(bone['scale']), _property_signature(bone.get('custom_properties', {}))))
    return (tuple(result), _property_signature(record.get('pose_custom_properties', {})))


def stock_pose_signature(item):
    return tuple(sorted((obj['name'], pose_signature(obj))
                        for obj in item.get('selected_objects', ()) if obj.get('pose_bones')))


def recording_objects(context, camera):
    objects = list(getattr(context, 'selected_objects', ()) or ())
    active = getattr(context, 'object', None)
    if getattr(context, 'mode', '') == 'POSE' and active is not None and active not in objects:
        objects.append(active)
    return [obj for obj in objects if obj is not None and obj != camera]


def capture_object_record(obj, context):
    record = {'name': obj.name, 'location': list(obj.location),
              **capture_rotation(obj), 'scale': list(obj.scale)}
    if obj.type != 'ARMATURE' or obj.pose is None:
        return record
    if getattr(context, 'mode', '') == 'POSE' and obj != context.object:
        return record
    record['pose_bones'] = sorted([
        {'name': bone.name, 'location': list(bone.location), **capture_rotation(bone),
         'scale': list(bone.scale), 'custom_properties': _numeric_properties(dict(bone.items()))}
        for bone in obj.pose.bones
    ], key=lambda item: item['name'])
    record['pose_custom_properties'] = _numeric_properties(dict(obj.items()))
    return record


def iter_action_curves(action, slot=None):
    if action is None:
        return
    if getattr(action, 'is_action_layered', False):
        for layer in action.layers:
            for strip in layer.strips:
                for bag in getattr(strip, 'channelbags', ()):
                    if slot is None or bag.slot_handle == slot.handle:
                        yield from bag.fcurves
    else:
        yield from getattr(action, 'fcurves', ())


def _has_active_nla(anim):
    return bool(anim and anim.use_nla and any(
        not track.mute and any(not strip.mute for strip in track.strips) for track in anim.nla_tracks))


def _pose_animation_state(obj, record, *, prefer_keyframes=True):
    """Use the same protected paths for restoration and its UI explanation."""
    anim = obj.animation_data
    if not anim:
        return {}, set()
    if _has_active_nla(anim) or anim.use_tweak_mode:
        return None, {'NLA'}
    paths = set()
    for saved in record.get('pose_bones', ()):
        bone = obj.pose.bones.get(saved['name'])
        if bone is None:
            continue
        prefix = bone.path_from_id()
        paths.update(prefix + '.' + prop for prop in (
            'location', 'rotation_mode', 'rotation_euler', 'rotation_quaternion',
            'rotation_axis_angle', 'scale'))
        paths.update(prefix + _property_path(name) for name in saved.get('custom_properties', {}) if name in bone)
    paths.update(_property_path(name) for name in record.get('pose_custom_properties', {}) if name in obj)
    protected, reasons = {}, set()
    if prefer_keyframes and anim.action and anim.action_influence > 0:
        for curve in iter_action_curves(anim.action, getattr(anim, 'action_slot', None)):
            if curve.data_path not in paths or curve.mute or (curve.group and curve.group.mute):
                continue
            if curve.keyframe_points or curve.sampled_points:
                protected.setdefault(curve.data_path, set()).add(curve.array_index)
                reasons.add('KEYFRAME')
            elif any(not modifier.mute for modifier in curve.modifiers):
                protected.setdefault(curve.data_path, set()).add(curve.array_index)
                reasons.add('ANIMATION')
    for curve in anim.drivers:
        if curve.data_path in paths and not curve.mute:
            # Drivers retain the existing whole-property protection.
            protected[curve.data_path] = None
            reasons.add('DRIVER')
    return protected, reasons


def stock_pose_priority_reasons(item):
    reasons = set()
    if not item.get('record_selected_objects'):
        return reasons
    for record in item.get('selected_objects', ()):
        obj = bpy.data.objects.get(record.get('name', ''))
        if obj is not None and obj.type == 'ARMATURE' and record.get('pose_bones'):
            reasons.update(_pose_animation_state(obj, record)[1])
    return reasons


def _property_path(name):
    return '[' + json.dumps(name, ensure_ascii=False) + ']'


def _owner_path(owner):
    return '' if isinstance(owner, bpy.types.ID) else owner.path_from_id()


def _apply_custom(owner, values, animated):
    for name, value in values.items():
        path = _property_path(name)
        full_path = _owner_path(owner) + path
        if name not in owner:
            continue
        if full_path not in animated:
            owner[name] = value
        elif isinstance(value, list) and animated[full_path] is not None:
            current = owner[name]
            if hasattr(current, 'to_list') and len(current) == len(value):
                owner[name] = [current[i] if i in animated[full_path] else v
                               for i, v in enumerate(value)]


def _apply_pose_vector(bone, prop, values, animated):
    protected = animated.get(bone.path_from_id() + '.' + prop, set())
    if protected is None:
        return
    current = getattr(bone, prop)
    setattr(bone, prop, [current[i] if i in protected else value
                         for i, value in enumerate(values)])


def _apply_pose_rotation(bone, saved, animated):
    prefix = bone.path_from_id() + '.'
    rotation_props = ('rotation_mode', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle')
    protected = {prop for prop in rotation_props if prefix + prop in animated}
    if not protected:
        apply_rotation(bone, saved)
        return
    mode = saved.get('rotation_mode', bone.rotation_mode)
    # Keyed components cannot safely be combined across different rotation representations.
    if mode != bone.rotation_mode:
        return
    prop = {'QUATERNION': 'rotation_quaternion', 'AXIS_ANGLE': 'rotation_axis_angle'}.get(mode, 'rotation_euler')
    if protected - {prop, 'rotation_mode'}:
        return
    values = saved['rotation'] if prop == 'rotation_euler' else saved[prop]
    _apply_pose_vector(bone, prop, values, animated)


def apply_pose_record(obj, record, *, prefer_keyframes=True):
    if obj.type != 'ARMATURE' or not record.get('pose_bones'):
        return
    animated, _ = _pose_animation_state(obj, record, prefer_keyframes=prefer_keyframes)
    if animated is None:
        return
    for saved in record['pose_bones']:
        bone = obj.pose.bones.get(saved['name'])
        if bone is None:
            continue
        _apply_pose_vector(bone, 'location', saved['location'], animated)
        _apply_pose_rotation(bone, saved, animated)
        _apply_pose_vector(bone, 'scale', saved['scale'], animated)
        _apply_custom(bone, saved.get('custom_properties', {}), animated)
    _apply_custom(obj, record.get('pose_custom_properties', {}), animated)




def current_pose_records(scene, item):
    if not scene.record_selected_objects:
        return []
    context = bpy.context
    names = {obj.name for obj in recording_objects(context, scene.camera)
             if obj.type == 'ARMATURE' and (context.mode != 'POSE' or obj == context.object)}
    return [record for record in item.get('selected_objects', ())
            if record['name'] in names and record.get('pose_bones')]




# Version Footer: 1.203
