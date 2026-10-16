"""Conservative camera membership from pre-fisheye world-space bounds.

This module never changes the scene or reads evaluated mesh geometry. Callers
with a registered source must supply its bounds *before* this add-on's modifier;
reading the visible, already warped bound_box would create membership feedback.
The test encloses an oriented box in camera coordinates, then bounds the signed
radial mapping with intervals. It includes partially visible/enclosing volumes
without scanning vertices. Empty parts of a bounding volume, occlusion, and
reflections are intentionally not tested, so false positives are possible.
"""

import math

from mathutils import Vector

from . import nodes


def _interval_product(first, second):
    products = tuple(a * b for a in first for b in second)
    return min(products), max(products)


def _radius_interval(x, y, ellipse_x, ellipse_y):
    def distance(interval):
        low, high = interval
        return 0.0 if low <= 0.0 <= high else min(abs(low), abs(high))

    minimum = math.hypot(distance(x) / ellipse_x, distance(y) / ellipse_y)
    maximum = math.hypot(max(abs(v) for v in x) / ellipse_x,
                         max(abs(v) for v in y) / ellipse_y)
    return minimum, maximum


def _scale(radius, strength, calibration):
    angular_radius = radius * max(strength, 1.0)
    scale = (calibration if radius < 1.0e-6 else
             calibration * math.atan(angular_radius) / angular_radius)
    if strength < 0.0:
        return calibration + max(abs(strength), 1.0) * (calibration - scale)
    return scale


def _warped_interval(x, y, weight, center, strength, calibration, ellipse):
    local_x = x[0] - center[0], x[1] - center[0]
    local_y = y[0] - center[1], y[1] - center[1]
    radii = _radius_interval(local_x, local_y, *ellipse)
    scales = tuple(_scale(radius, strength, calibration) for radius in radii)
    # The positive radial scale decreases monotonically; the negative scale
    # increases monotonically. Blending is affine in each of weight and scale.
    blends = tuple(1.0 + amount * (scale - 1.0)
                   for amount in weight for scale in scales)
    blend = min(blends), max(blends)
    mapped_x = _interval_product(local_x, blend)
    mapped_y = _interval_product(local_y, blend)
    return ((mapped_x[0] + center[0], mapped_x[1] + center[0]),
            (mapped_y[0] + center[1], mapped_y[1] + center[1]))


def _overlap(first, second):
    # Tolerance covers camera float precision and the oracle's tiny axial limit.
    tolerance = 1.0e-7 * max(1.0, *(abs(v) for v in first + second))
    return first[1] + tolerance >= second[0] and second[1] + tolerance >= first[0]


def _weight(depth, near, strength):
    fade = min(max((depth - near) / near, 0.0), 1.0)
    return min(abs(strength), 1.0) * fade * fade * (3.0 - 2.0 * fade)


def in_camera(scene, obj, *, bounds=None, margin=0.02):
    """Whether an original bounding volume can intersect the warped frame.

    ``bounds`` is a sequence of world-space corners enclosing the unmodified
    source. Without it, the source object's own bound_box and matrix_world are
    used; this shortcut is only appropriate before an add-on modifier exists.
    ``margin`` expands each edge by a fraction of the full frame width/height.
    Call after transforms have updated. Camera and center transforms are read
    directly, without evaluating their geometry or changing preview state.
    Preview OFF does not change membership: this predicts the ON deformation.
    """
    camera = scene.camera
    if camera is None or camera.type != 'CAMERA' or camera.data.type != 'PERSP':
        return False
    if bounds is None:
        if obj is None:
            return False
        bounds = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    else:
        bounds = tuple(bounds)
    if not bounds:
        return False

    inverse = camera.matrix_world.normalized().inverted_safe()
    points = [inverse @ Vector(point) for point in bounds]
    if not all(math.isfinite(component) for point in points for component in point):
        # Non-finite upstream geometry cannot safely be culled by bounds.
        return True
    depths = tuple(-point.z for point in points)
    depth_min = max(min(depths), camera.data.clip_start, nodes.EPSILON)
    depth_max = min(max(depths), camera.data.clip_end)
    if depth_max < depth_min:
        return False
    depth_interval = depth_min, depth_max
    xs = min(point.x for point in points), max(point.x for point in points)
    ys = min(point.y for point in points), max(point.y for point in points)
    slopes_x = _interval_product(xs, (1.0 / depth_max, 1.0 / depth_min))
    slopes_y = _interval_product(ys, (1.0 / depth_max, 1.0 / depth_min))

    frame = camera.data.view_frame(scene=scene)
    frame_x = min(point.x / -point.z for point in frame), max(point.x / -point.z for point in frame)
    frame_y = min(point.y / -point.z for point in frame), max(point.y / -point.z for point in frame)
    half_width = (frame_x[1] - frame_x[0]) * 0.5
    amount = max(float(margin), 0.0)
    pad_x, pad_y = (frame_x[1] - frame_x[0]) * amount, (frame_y[1] - frame_y[0]) * amount
    frame_x = frame_x[0] - pad_x, frame_x[1] + pad_x
    frame_y = frame_y[0] - pad_y, frame_y[1] + pad_y

    settings = scene.gyogan
    strength = float(settings.strength)
    near = max(float(settings.near_distance), nodes.EPSILON)
    ellipse = max(float(settings.ellipse_x), 0.1), max(float(settings.ellipse_y), 0.1)
    factor = scene.gyogan_dolly.factor if hasattr(scene, 'gyogan_dolly') else 1.0
    calibration = (2.0 * half_width * max(settings.zoom, 0.0) * factor
                   / max(settings.fov, 1.0e-4))
    center_mode = getattr(settings, 'center_mode', 'OBJECT')
    if center_mode == 'CURSOR':
        center_point = inverse @ scene.cursor.location
    elif center_mode == 'OBJECT' and settings.center_object:
        center_point = inverse @ settings.center_object.matrix_world.translation
    else:
        center_point = None
    center = (0.0, 0.0)
    if center_point is not None and -center_point.z > near:
        center_depth = -center_point.z
        fade = _weight(center_depth, near, 1.0)
        center = center_point.x / center_depth * fade, center_point.y / center_depth * fade
    weight = tuple(_weight(depth, near, strength) for depth in depth_interval)

    # An interval bound always encloses every point, including nonlinear edge
    # extrema between box corners. Refinement only rejects empty intervals;
    # exhausting the budget keeps the object, never excludes visible geometry.
    pending = [(slopes_x, slopes_y, 0)]
    visited = 0
    while pending:
        x, y, level = pending.pop()
        output_x, output_y = _warped_interval(
            x, y, weight, center, strength, calibration, ellipse)
        if not (_overlap(output_x, frame_x) and _overlap(output_y, frame_y)):
            continue
        visited += 1
        if level >= 8 or visited >= 128:
            return True
        # Fast positive result uses the same projection oracle as regression
        # tests. The interval enclosure, rather than samples, decides exclusion.
        depth = (depth_min + depth_max) * 0.5
        sample = nodes.camera_space_reference(
            ((x[0] + x[1]) * depth * 0.5, (y[0] + y[1]) * depth * 0.5, -depth),
            strength, settings.fov, settings.zoom, settings.near_distance, half_width,
            center_point=center_point, ellipse_x=ellipse[0], ellipse_y=ellipse[1],
            dolly_factor=factor)
        sample_x, sample_y = sample[0] / depth, sample[1] / depth
        if frame_x[0] <= sample_x <= frame_x[1] and frame_y[0] <= sample_y <= frame_y[1]:
            return True
        if (x[1] - x[0]) / ellipse[0] >= (y[1] - y[0]) / ellipse[1]:
            middle = (x[0] + x[1]) * 0.5
            pending.extend((((x[0], middle), y, level + 1), ((middle, x[1]), y, level + 1)))
        else:
            middle = (y[0] + y[1]) * 0.5
            pending.extend(((x, (y[0], middle), level + 1), (x, (middle, y[1]), level + 1)))
    return False
