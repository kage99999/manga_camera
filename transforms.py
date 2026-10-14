# -*- coding: utf-8 -*-
# Version: 1.203
"""Rotation serialization shared by camera and object stocks."""

import math

_EULER_MODES = {'XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX'}


def _rotation_vector(value, fallback):
    try:
        values = list(value)[:len(fallback)]
        values.extend(fallback[len(values):])
        result = [float(component) for component in values]
        if all(math.isfinite(v) for v in result):
            return result
    except (TypeError, ValueError, OverflowError):
        pass
    return list(fallback)


def capture_rotation(obj):
    mode = obj.rotation_mode
    data = {'rotation': list(obj.rotation_euler), 'rotation_mode': mode}
    if mode == 'QUATERNION':
        data['rotation_quaternion'] = list(obj.rotation_quaternion)
    elif mode == 'AXIS_ANGLE':
        data['rotation_axis_angle'] = list(obj.rotation_axis_angle)
    return data


def normalize_rotation(data):
    result = {'rotation': _rotation_vector(data.get('rotation', ()), (0.0, 0.0, 0.0))}
    mode = data.get('rotation_mode')
    if mode in _EULER_MODES:
        result['rotation_mode'] = mode
    elif mode == 'QUATERNION':
        result['rotation_mode'] = mode
        result['rotation_quaternion'] = _rotation_vector(
            data.get('rotation_quaternion', ()), (1.0, 0.0, 0.0, 0.0))
    elif mode == 'AXIS_ANGLE':
        result['rotation_mode'] = mode
        result['rotation_axis_angle'] = _rotation_vector(
            data.get('rotation_axis_angle', ()), (0.0, 0.0, 1.0, 0.0))
    return result


def apply_rotation(obj, data):
    mode = data.get('rotation_mode')
    # Missing mode is a legacy record; preserve its original Euler behavior.
    if mode in _EULER_MODES or mode in {'QUATERNION', 'AXIS_ANGLE'}:
        obj.rotation_mode = mode
    obj.rotation_euler = data.get('rotation', list(obj.rotation_euler))
    if mode == 'QUATERNION':
        obj.rotation_quaternion = data['rotation_quaternion']
    elif mode == 'AXIS_ANGLE':
        obj.rotation_axis_angle = data['rotation_axis_angle']


def rotation_signature(data):
    mode = data.get('rotation_mode', 'XYZ')
    if mode == 'QUATERNION':
        return (mode, tuple(data.get('rotation_quaternion', (1.0, 0.0, 0.0, 0.0))))
    if mode == 'AXIS_ANGLE':
        return (mode, tuple(data.get('rotation_axis_angle', (0.0, 0.0, 1.0, 0.0))))
    return (mode, tuple(data.get('rotation', ())))


# Version Footer: 1.203
