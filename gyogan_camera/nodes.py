"""Non-destructive Geometry Nodes implementation of the camera-space warp.

The functions in this module create and update owned node groups. They never
modify source mesh coordinates, apply modifiers, or install frame handlers.
"""

from __future__ import annotations

import math

try:
    import bpy
except ModuleNotFoundError:  # The reference function can be used outside Blender.
    bpy = None


EPSILON = 1.0e-8
PROJECTION_VERSION = 2
CENTER_VERSION = 1
CURSOR_VERSION = 1
SIGNED_VERSION = 1
ELLIPSE_VERSION = 1
DOLLY_VERSION = 1


class Graph:
    """Small socket-oriented graph builder, also used by the armature engine."""

    def __init__(self, tree):
        self.tree = tree
        self._index = 0

    def node(self, idname, label=None):
        node = self.tree.nodes.new(idname)
        if label:
            node.label = label
        # Keep generated graphs inspectable without an expensive layout pass.
        node.location = ((self._index % 8) * 205, -(self._index // 8) * 175)
        self._index += 1
        return node

    def connect(self, value, node, socket=0):
        """Connect an output socket or set an input's constant value."""
        target = node.inputs[socket]
        if isinstance(value, bpy.types.NodeSocket):
            self.tree.links.new(value, target)
        else:
            target.default_value = value
        return node

    def math(self, operation, a, b=None, c=None):
        node = self.node("ShaderNodeMath")
        node.operation = operation
        self.connect(a, node, 0)
        if b is not None:
            self.connect(b, node, 1)
        if c is not None:
            self.connect(c, node, 2)
        return node.outputs[0]

    def vector(self, operation, a, b=None, scale=None):
        node = self.node("ShaderNodeVectorMath")
        node.operation = operation
        self.connect(a, node, 0)
        if b is not None:
            self.connect(b, node, 1)
        if scale is not None:
            self.connect(scale, node, "Scale")
        if operation in {"LENGTH", "DISTANCE", "DOT_PRODUCT"}:
            return node.outputs["Value"]
        return node.outputs["Vector"]

    def xyz(self, x, y, z):
        node = self.node("ShaderNodeCombineXYZ")
        for name, value in zip(("X", "Y", "Z"), (x, y, z)):
            self.connect(value, node, name)
        return node.outputs["Vector"]

    def separate(self, vector):
        node = self.node("ShaderNodeSeparateXYZ")
        self.connect(vector, node, "Vector")
        return tuple(node.outputs[name] for name in ("X", "Y", "Z"))

    def transform(self, matrix, point):
        node = self.node("FunctionNodeTransformPoint")
        self.connect(point, node, "Vector")
        self.connect(matrix, node, "Transform")
        return node.outputs["Vector"]

    def project(self, matrix, point):
        node = self.node("FunctionNodeProjectPoint")
        self.connect(point, node, "Vector")
        self.connect(matrix, node, "Transform")
        return node.outputs["Vector"]

    def inverse(self, matrix):
        node = self.node("FunctionNodeInvertMatrix")
        self.connect(matrix, node, 0)
        return node.outputs[0]

    def switch(self, condition, false_value, true_value, input_type="FLOAT"):
        node = self.node("GeometryNodeSwitch")
        node.input_type = input_type
        self.connect(condition, node, "Switch")
        self.connect(false_value, node, "False")
        self.connect(true_value, node, "True")
        return node.outputs["Output"]

    def driven_value(self, owner, data_path, label=None):
        """Use a simple property driver: no Python driver namespace or handler."""
        node = self.node("ShaderNodeValue", label or data_path)
        output = node.outputs[0]
        output.default_value = float(owner.path_resolve(data_path))
        fcurve = output.driver_add("default_value")
        driver = fcurve.driver
        driver.type = "AVERAGE"
        variable = driver.variables.new()
        variable.name = "value"
        variable.type = "SINGLE_PROP"
        target = variable.targets[0]
        target.id_type = owner.id_type
        target.id = owner
        target.data_path = data_path
        return output


def supports_object_compare():
    # Static RNA enums include OBJECT even in 5.0/5.1, where the node's
    # dynamic setter rejects it. Object comparison became usable in 5.2.
    return bpy.app.version >= (5, 2, 0)


def _center_selection(graph, scene, object_location, object_exists, settings_path='gyogan'):
    """Select a world-space center without an auxiliary object or Python timer."""
    if scene is None:
        raise ValueError('魚眼の中心の選択に必要なシーンが指定されていません')
    g = graph
    cursor = []
    for index, axis in enumerate(('x', 'y', 'z')):
        value = g.driven_value(scene, 'cursor.location[%d]' % index,
                               '3Dカーソルのワールド位置 ' + axis.upper())
        value.node['gyogan_role'] = 'cursor_location_' + axis
        cursor.append(value)
    use_cursor = g.driven_value(scene, settings_path + '.center_use_cursor',
                                '3Dカーソルを魚眼の中心に使用')
    use_cursor.node['gyogan_role'] = 'center_use_cursor'
    use_object = g.driven_value(scene, settings_path + '.center_use_object',
                                'オブジェクトを魚眼の中心に使用')
    use_object.node['gyogan_role'] = 'center_use_object'
    position = g.switch(use_cursor, object_location, g.xyz(*cursor), input_type='VECTOR')
    position.node['gyogan_role'] = 'center_location'
    enabled_object = g.node('FunctionNodeBooleanMath', '指定オブジェクトの中心が有効')
    enabled_object.operation = 'AND'
    g.connect(use_object, enabled_object, 0)
    g.connect(object_exists, enabled_object, 1)
    enabled = g.node('FunctionNodeBooleanMath', '選択した魚眼の中心が有効')
    enabled.operation = 'OR'
    g.connect(use_cursor, enabled, 0)
    g.connect(enabled_object.outputs[0], enabled, 1)
    return position, enabled.outputs[0]


def _center_slope(graph, camera_inverse, near_distance, center_object,
                  scene=None, settings_path='gyogan'):
    """Return the selected center ray's XY slopes, using transforms only.

    An explicit object comparison keeps a deleted/cleared center at the optical
    axis even when an empty Object Info would otherwise evaluate at world zero.
    """
    g = graph
    # Use a socket-backed constant: Blender 5.2's Input Object RNA setter does
    # not increment ID users although freeing that node decrements them. Its
    # direct ID also adds an unnecessary geometry dependency for self-centers.
    # An Object Switch's constant False input owns its reference correctly and
    # lets dependency analysis follow only the consuming transform operation.
    source = g.node("GeometryNodeSwitch", "魚眼の中心オブジェクト")
    source.input_type = "OBJECT"
    source.inputs["Switch"].default_value = False
    source.inputs["False"].default_value = center_object
    source["gyogan_role"] = "center_object"
    info = g.node("GeometryNodeObjectInfo", "中心のワールド位置（形状を参照しない）")
    info.transform_space = "ORIGINAL"
    g.connect(source.outputs[0], info, "Object")
    if supports_object_compare():
        exists = g.node("FunctionNodeCompare", "中心オブジェクトの存在確認")
        exists.data_type = "OBJECT"
        exists.operation = "NOT_EQUAL"
        g.connect(source.outputs[0], exists, 0)
        exists.inputs[1].default_value = None
        exists_output = exists.outputs[0]
    else:
        # Older Blender cannot compare Object sockets. Read only whether the
        # Scene pointer exists; transforms still come from Object Info above.
        # No Scene-to-Scene driver or geometry dependency is introduced.
        if scene is None:
            raise ValueError('中心の存在確認に必要なシーンが指定されていません')
        exists_output = g.driven_value(scene, settings_path + '.center_exists',
                                      '中心オブジェクトの存在確認（互換）')
        exists_output.node['gyogan_role'] = 'center_exists'
    world_position, center_enabled = _center_selection(
        g, scene, info.outputs['Location'], exists_output, settings_path)
    center = g.transform(camera_inverse, world_position)
    x, y, z = g.separate(center)
    depth = g.math("MULTIPLY", z, -1.0)
    threshold = g.math("MAXIMUM", near_distance, EPSILON)
    in_front = g.math("GREATER_THAN", depth, threshold)
    valid = g.node("FunctionNodeBooleanMath", "存在する前方の中心のみ有効")
    valid.operation = "AND"
    g.connect(center_enabled, valid, 0)
    g.connect(in_front, valid, 1)
    safe_depth = g.math("MAXIMUM", depth, EPSILON)
    fade = g.math("DIVIDE", g.math("SUBTRACT", depth, threshold), threshold)
    fade = g.math("MINIMUM", g.math("MAXIMUM", fade, 0.0), 1.0)
    fade = g.math("MULTIPLY", g.math("MULTIPLY", fade, fade),
                  g.math("SUBTRACT", 3.0, g.math("MULTIPLY", fade, 2.0)))
    slope = g.xyz(g.math("MULTIPLY", g.math("DIVIDE", x, safe_depth), fade),
                  g.math("MULTIPLY", g.math("DIVIDE", y, safe_depth), fade), 0.0)
    slope = g.switch(valid.outputs[0], (0.0, 0.0, 0.0), slope, input_type="VECTOR")
    sx, sy, _ = g.separate(slope)
    return sx, sy


def set_center_object(tree, obj):
    """Update tagged center inputs without rebuilding any generated geometry."""
    if (not tree.get("gyogan_owner")
            or tree.get("gyogan_role") not in {"mesh_warp", "armature_warp"}):
        return False
    changed = False
    for node in tree.nodes:
        if (node.bl_idname == "GeometryNodeSwitch" and node.input_type == "OBJECT"
                and node.get("gyogan_role") == "center_object"
                and node.inputs["False"].default_value != obj):
            node.inputs["False"].default_value = obj
            changed = True
    if changed:
        tree.update_tag()
    return changed


def _camera_inputs(tree):
    """Find the two camera inputs, including untagged, saved legacy graphs.

    Object identity alone is insufficient: the user may also use that camera
    as the center. Recognize the camera's pose and projection subgraphs, never
    node names, labels, or all references to the previous camera.
    """
    message = "既存の魚眼ノードのカメラ接続が変更されているため、基準カメラを切り替えられません"

    def source(socket):
        return socket.links[0].from_socket if len(socket.links) == 1 else None

    def consumers(output, node_type, input_name):
        return [link.to_node for link in output.links
                if link.to_node.bl_idname == node_type
                and link.to_socket == link.to_node.inputs[input_name]]

    def camera_pose(info):
        if info.bl_idname != "GeometryNodeObjectInfo" or info.transform_space != "ORIGINAL":
            return False
        poses = consumers(info.outputs["Location"], "FunctionNodeCombineTransform", "Translation")
        if len(poses) != 1:
            return False
        pose = poses[0]
        if (source(pose.inputs["Rotation"]) != info.outputs["Rotation"]
                or pose.inputs["Scale"].is_linked
                or tuple(pose.inputs["Scale"].default_value) != (1.0, 1.0, 1.0)):
            return False
        inverses = consumers(pose.outputs["Transform"], "FunctionNodeInvertMatrix", 0)
        return (len(inverses) == 1
                and bool(consumers(inverses[0].outputs[0], "FunctionNodeTransformPoint", "Transform"))
                and bool(consumers(pose.outputs["Transform"], "FunctionNodeTransformPoint", "Transform")))

    def camera_projection(info):
        if info.bl_idname != "GeometryNodeCameraInfo":
            return False
        inverses = consumers(info.outputs["Projection Matrix"], "FunctionNodeInvertMatrix", 0)
        return (len(inverses) == 1
                and len(consumers(inverses[0].outputs[0], "FunctionNodeProjectPoint", "Transform")) == 2)

    tagged_pose = [node for node in tree.nodes if node.get("gyogan_role") == "camera_transform"]
    tagged_projection = [node for node in tree.nodes if node.get("gyogan_role") == "camera_projection"]
    if tagged_pose or tagged_projection:
        if (len(tagged_pose) != 1 or len(tagged_projection) != 1
                or not camera_pose(tagged_pose[0]) or not camera_projection(tagged_projection[0])):
            raise ValueError(message)
        pose, projection = tagged_pose[0], tagged_projection[0]
    else:
        poses = [node for node in tree.nodes if camera_pose(node)]
        projections = [node for node in tree.nodes if camera_projection(node)]
        if len(poses) != 1 or len(projections) != 1:
            raise ValueError(message)
        pose, projection = poses[0], projections[0]
    sockets = pose.inputs["Object"], projection.inputs["Camera"]
    if (any(socket.is_linked for socket in sockets)
            or sockets[0].default_value != sockets[1].default_value):
        raise ValueError(message)
    if tree.animation_data:
        paths = {socket.path_from_id("default_value") for socket in sockets}
        if any(curve.data_path in paths for curve in tree.animation_data.drivers):
            raise ValueError(message)
    return sockets


def camera_retarget_plan(scene, camera):
    """Validate registered, owned warp groups without changing any references.

    None deliberately clears the dependency when the scene has no active
    camera; the caller suspends the preview until a valid camera is selected.
    The plan retains every old ID so the caller can roll back a larger change.
    """
    if camera is not None:
        if camera.type != "CAMERA" or camera.data.type != "PERSP":
            raise ValueError("魚眼変形には透視投影カメラを使用してください")
        if scene.objects.get(camera.name) != camera:
            raise ValueError("基準カメラは現在のシーンにあるカメラを指定してください")
    tokens = {target.token for target in scene.gyogan.targets if target.token}
    plan = []
    for tree in bpy.data.node_groups:
        if (tree.get("gyogan_owner") not in tokens
                or tree.get("gyogan_role") not in {"mesh_warp", "armature_warp"}):
            continue
        if not tree.is_editable:
            raise ValueError("魚眼ノードが読み取り専用のため、基準カメラを切り替えられません")
        plan.extend((socket, socket.default_value, camera) for socket in _camera_inputs(tree))
    return plan


def apply_camera_plan(plan, rollback=False):
    """Apply a validated plan atomically, or restore all its original IDs."""
    changes = [(socket, socket.default_value, old if rollback else new)
               for socket, old, new in plan]
    for (socket, current, _), (_, old, new) in zip(changes, plan):
        if socket.is_linked or current not in ((old, new) if rollback else (old,)):
            raise ValueError("魚眼ノードのカメラ接続が更新中に変更されたため、切り替えを中止しました")
    applied = []
    trees = {socket.id_data for socket, _, _ in changes}
    try:
        for socket, current, replacement in changes:
            if current != replacement:
                socket.default_value = replacement
                applied.append((socket, current))
        for tree in trees:
            tree.update_tag()
    except Exception:
        for socket, original in reversed(applied):
            socket.default_value = original
        for tree in trees:
            tree.update_tag()
        raise


def _signed_scale(graph, standard_scale, central_scale, reverse_amount, is_negative):
    """Select bounded peripheral expansion while retaining the positive branch."""
    difference = graph.math("SUBTRACT", central_scale, standard_scale)
    reverse_scale = graph.math("ADD", central_scale,
                               graph.math("MULTIPLY", reverse_amount, difference))
    return graph.switch(is_negative, standard_scale, reverse_scale)


def _dolly_factor(graph, scene):
    # Read the independent controls in this node tree, not a Scene property
    # driven by that same Scene: the latter forms a PARAMETERS_EVAL cycle.
    from . import dolly
    value = graph.node('ShaderNodeValue', 'ドリーズームの魚眼補正').outputs[0]
    value.default_value = 1.0
    dolly.configure_factor_driver(value.driver_add('default_value'), scene)
    return value


class CameraWarp:
    """Reusable live warp from a point to a point in the same coordinate space.

    ``space='RELATIVE'`` means the geometry modifier owner's local space;
    ``space='ORIGINAL'`` means world space. Reuse one instance for all points in
    a graph, so camera information, projection calibration and setting drivers
    are shared. Tagged camera inputs can be retargeted without rebuilding the
    graph. Camera transforms, lens, shift, render aspect and the numerical
    settings remain live dependencies.
    """

    def __init__(self, graph, scene, settings_path="gyogan", space="RELATIVE"):
        if space not in {"ORIGINAL", "RELATIVE"}:
            raise ValueError("変形に使用する座標系の指定が正しくありません")
        self.graph = graph
        self.space = space
        settings = scene.path_resolve(settings_path)
        camera = settings.camera
        if camera is None or camera.type != "CAMERA":
            raise ValueError("魚眼変形の基準カメラを指定してください")
        if camera.data.type != "PERSP":
            raise ValueError("魚眼変形には透視投影カメラを使用してください")
        g = graph

        self.strength = g.driven_value(scene, settings_path + ".strength", "魚眼の強度")
        self.absolute_strength = g.math("ABSOLUTE", self.strength)
        self.reverse_amount = g.math("MAXIMUM", self.absolute_strength, 1.0)
        self.is_negative = g.math("GREATER_THAN", 0.0, self.strength)
        self.angular_strength = g.math("MAXIMUM", self.strength, 1.0)
        self.angular_strength.node.label = "標準を超える歪みの強調"
        g.tree["gyogan_projection_version"] = PROJECTION_VERSION
        g.tree["gyogan_signed_version"] = SIGNED_VERSION
        self.fov = g.driven_value(scene, settings_path + ".fov", "魚眼画角 (rad)")
        self.zoom = g.driven_value(scene, settings_path + ".zoom", "画面内サイズ")
        self.near = g.driven_value(scene, settings_path + ".near_distance", "カメラ面の保護範囲")
        self.ellipse_x = g.math("MAXIMUM", g.driven_value(
            scene, settings_path + ".ellipse_x", "魚眼の横幅"), 0.1)
        self.ellipse_y = g.math("MAXIMUM", g.driven_value(
            scene, settings_path + ".ellipse_y", "魚眼の縦幅"), 0.1)
        g.tree["gyogan_ellipse_version"] = ELLIPSE_VERSION

        camera_info = g.node("GeometryNodeObjectInfo", "基準カメラのワールド姿勢")
        camera_info["gyogan_role"] = "camera_transform"
        camera_info.transform_space = "ORIGINAL"
        camera_info.inputs["Object"].default_value = camera

        # Camera scale does not alter a perspective camera's projection. Strip
        # it in world space, before the owner's possibly nonuniform transform.
        camera_pose = g.node("FunctionNodeCombineTransform", "カメラの位置・回転（スケールを除外）")
        g.connect(camera_info.outputs["Location"], camera_pose, "Translation")
        g.connect(camera_info.outputs["Rotation"], camera_pose, "Rotation")
        camera_pose.inputs["Scale"].default_value = (1.0, 1.0, 1.0)
        self.camera_matrix = camera_pose.outputs["Transform"]
        self.camera_inverse = g.inverse(self.camera_matrix)
        self.center_slope = _center_slope(
            g, self.camera_inverse, self.near,
            getattr(settings, "center_object", None)
            if getattr(settings, 'center_mode', 'OBJECT') == 'OBJECT' else None,
            scene=scene, settings_path=settings_path
        )
        g.tree["gyogan_center_version"] = CENTER_VERSION
        g.tree["gyogan_cursor_version"] = CURSOR_VERSION
        self.owner_matrix = None
        self.owner_inverse = None
        if space == "RELATIVE":
            owner = g.node("GeometryNodeSelfObject", "変形対象")
            owner_info = g.node("GeometryNodeObjectInfo", "対象の全変換（非一様スケール対応）")
            owner_info.transform_space = "ORIGINAL"
            g.connect(owner.outputs[0], owner_info, "Object")
            self.owner_matrix = owner_info.outputs["Transform"]
            self.owner_inverse = g.inverse(self.owner_matrix)

        projection_info = g.node("GeometryNodeCameraInfo", "レンズ・シフト・出力アスペクト")
        projection_info["gyogan_role"] = "camera_projection"
        projection_info.inputs["Camera"].default_value = camera
        inverse_projection = g.inverse(projection_info.outputs["Projection Matrix"])
        center = g.project(inverse_projection, (0.0, 0.0, 0.0))
        right = g.project(inverse_projection, (1.0, 0.0, 0.0))
        cx, cy, cz = g.separate(center)
        rx, _, rz = g.separate(right)
        self.shift_x = g.math("DIVIDE", cx, g.math("MULTIPLY", cz, -1.0))
        self.shift_y = g.math("DIVIDE", cy, g.math("MULTIPLY", cz, -1.0))
        right_slope = g.math("DIVIDE", rx, g.math("MULTIPLY", rz, -1.0))
        self.horizontal_half_width = g.math(
            "ABSOLUTE", g.math("SUBTRACT", right_slope, self.shift_x)
        )
        # k = h / (horizontal fisheye FOV / 2). Lens shift remains the
        # perspective camera's principal-point offset; it is not cancelled.
        safe_fov = g.math("MAXIMUM", self.fov, 1.0e-4)
        safe_zoom = g.math("MAXIMUM", self.zoom, 0.0)
        self.k = g.math(
            "DIVIDE",
            g.math("MULTIPLY", g.math("MULTIPLY", self.horizontal_half_width, 2.0), safe_zoom),
            safe_fov,
        )
        self.k = g.math("MULTIPLY", self.k, _dolly_factor(g, scene))
        g.tree["gyogan_dolly_version"] = DOLLY_VERSION

    def apply(self, position):
        """Return a deformed point socket, preserving depth in camera space."""
        g = self.graph
        world = g.transform(self.owner_matrix, position) if self.owner_matrix is not None else position
        camera_point = g.transform(self.camera_inverse, world)
        x, y, z = g.separate(camera_point)
        depth = g.math("MULTIPLY", z, -1.0)
        center_x = g.math("MULTIPLY", depth, self.center_slope[0])
        center_y = g.math("MULTIPLY", depth, self.center_slope[1])
        x = g.math("SUBTRACT", x, center_x)
        y = g.math("SUBTRACT", y, center_y)
        # Evaluate radial falloff in an elliptical metric, then apply its scale
        # to the original centered XY. This is D * F(D^-1 * p), so the optical
        # center and on-axis magnification remain unchanged, including s<0.
        radius = g.vector("LENGTH", g.xyz(g.math("DIVIDE", x, self.ellipse_x),
                                          g.math("DIVIDE", y, self.ellipse_y), 0.0))
        angular_radius = g.math("MULTIPLY", radius, self.angular_strength)
        positive_depth = g.math("MAXIMUM", depth, EPSILON)
        theta = g.math("ARCTAN2", angular_radius, positive_depth)
        radial_scale = g.math(
            "DIVIDE",
            g.math("MULTIPLY", g.math("MULTIPLY", positive_depth, self.k), theta),
            g.math("MAXIMUM", angular_radius, EPSILON),
        )
        # For s>1: R=d*k*atan(q*r/d)/q, q=max(s,1). Its radial derivative is
        # k/(1+(q*r/d)^2)>0: amplify peripheral compression without extrapolating
        # through zero or reversing radial order. 0<=s<=1 retains the old mapping.
        # Analytic on-axis limit: d*k*atan(q*r/d)/(q*r) -> k. A relative threshold
        # avoids flattening microscopic but otherwise valid scenes.
        on_axis = g.math("LESS_THAN", radius, g.math("MULTIPLY", positive_depth, 1.0e-6))
        radial_scale = g.switch(on_axis, radial_scale, self.k)
        # For s<0, angular_strength is 1 and this becomes
        # k*(1+q*(1-atan(rho)/rho)), q=max(abs(s),1), rho=r/d.
        # Its radius derivative is k*(1+q-q/(1+rho^2))>0. Central scale stays k;
        # peripheral gain approaches k*(1+q) without a tangent inverse's poles.
        radial_scale = _signed_scale(g, radial_scale, self.k,
                                      self.reverse_amount, self.is_negative)
        warped_camera = g.xyz(
            g.math("ADD", g.math("MULTIPLY", x, radial_scale), center_x),
            g.math("ADD", g.math("MULTIPLY", y, radial_scale), center_y),
            z,
        )
        warped_world = g.transform(self.camera_matrix, warped_camera)
        warped = g.transform(self.owner_inverse, warped_world) if self.owner_inverse is not None else warped_world

        # Fade continuously to identity around the camera plane. Behind the
        # plane and up to near_distance are unchanged; the warp reaches full
        # strength at 2*near_distance. No clipping or vertex deletion occurs.
        safe_near = g.math("MAXIMUM", self.near, EPSILON)
        fade = g.math("DIVIDE", g.math("SUBTRACT", depth, safe_near), safe_near)
        fade = g.math("MINIMUM", g.math("MAXIMUM", fade, 0.0), 1.0)
        smooth_fade = g.math(
            "MULTIPLY", g.math("MULTIPLY", fade, fade),
            g.math("SUBTRACT", 3.0, g.math("MULTIPLY", fade, 2.0)),
        )
        strength = g.math("MINIMUM", g.math("MAXIMUM", self.absolute_strength, 0.0), 1.0)
        weight = g.math("MULTIPLY", strength, smooth_fade)
        offset = g.vector("SUBTRACT", warped, position)
        offset = g.vector("SCALE", offset, scale=weight)
        # Interpolate in input space so strength=0 yields the original point
        # exactly, without a matrix round-trip tolerance.
        return g.vector("ADD", position, offset)


def build_mesh_group(name, scene, settings_path="gyogan"):
    """Build one group shared by all of a scene's registered mesh modifiers."""
    tree = bpy.data.node_groups.new(name, "GeometryNodeTree")
    try:
        tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        tree.is_modifier = True
        g = Graph(tree)
        group_input = g.node("NodeGroupInput", "入力形状")
        group_output = g.node("NodeGroupOutput", "魚眼変形（元データは保持）")
        group_output.is_active_output = True
        realize = g.node("GeometryNodeRealizeInstances", "既存のインスタンスを変形用に実体化")
        g.connect(group_input.outputs["Geometry"], realize, "Geometry")
        position = g.node("GeometryNodeInputPosition")
        warp = CameraWarp(g, scene, settings_path=settings_path)
        set_position = g.node("GeometryNodeSetPosition", "深度を保持した等距離魚眼")
        g.connect(realize.outputs["Geometry"], set_position, "Geometry")
        g.connect(warp.apply(position.outputs["Position"]), set_position, "Position")
        g.connect(set_position.outputs["Geometry"], group_output, "Geometry")
        tree["gyogan_schema"] = 1
        tree["gyogan_role"] = "mesh_warp"
        return tree
    except Exception:
        bpy.data.node_groups.remove(tree)
        raise


def upgrade_strength_group(tree):
    """Upgrade an owned legacy warp in place, preserving references and IDs.

    Only the angular radius subgraph changes. The original blend, near fade,
    interface, drivers and armature matrix consumers are preserved.
    """
    if (not tree.get("gyogan_owner")
            or tree.get("gyogan_role") not in {"mesh_warp", "armature_warp"}
            or tree.get("gyogan_projection_version", 1) >= PROJECTION_VERSION):
        return False
    drivers = tree.animation_data.drivers if tree.animation_data else ()
    strength_paths = {curve.data_path for curve in drivers
                      if any(target.data_path.endswith(".strength")
                             for variable in curve.driver.variables
                             for target in variable.targets)}
    strength_nodes = [node for node in tree.nodes
                      if node.bl_idname == "ShaderNodeValue"
                      and node.outputs[0].path_from_id("default_value") in strength_paths]
    plan = []
    for theta in tree.nodes:
        if theta.bl_idname != "ShaderNodeMath" or theta.operation != "ARCTAN2":
            continue
        if not theta.inputs[0].is_linked:
            continue
        radius = theta.inputs[0].links[0].from_socket
        if radius.node.bl_idname != "ShaderNodeVectorMath" or radius.node.operation != "LENGTH":
            continue
        denominators = [node for node in tree.nodes
                        if node.bl_idname == "ShaderNodeMath" and node.operation == "MAXIMUM"
                        and node.inputs[0].is_linked
                        and node.inputs[0].links[0].from_socket == radius
                        and not node.inputs[1].is_linked
                        and abs(node.inputs[1].default_value - EPSILON) < 1e-12]
        if len(denominators) != 1:
            raise ValueError("既存の魚眼ノード構成が変更されているため、自動更新できません")
        plan.append((theta, radius, denominators[0]))
    if len(strength_nodes) != 1 or not plan:
        raise ValueError("既存の魚眼ノード構成が変更されているため、自動更新できません")
    graph = Graph(tree)
    graph._index = len(tree.nodes)
    angular_strength = graph.math("MAXIMUM", strength_nodes[0].outputs[0], 1.0)
    angular_strength.node.label = "標準を超える歪みの強調"
    for theta, radius, denominator in plan:
        angular_radius = graph.math("MULTIPLY", radius, angular_strength)
        graph.connect(angular_radius, theta, 0)
        graph.connect(angular_radius, denominator, 0)
    tree["gyogan_projection_version"] = PROJECTION_VERSION
    return True


def upgrade_center_group(tree, scene):
    """Add center-ray support to an owned legacy leaf group in place.

    Validate every existing connection before mutation. The interface, original
    nodes, drivers, group identity and every caller remain intact. A failed
    construction removes only the newly added nodes and restores old links.
    """
    if (not tree.get("gyogan_owner")
            or tree.get("gyogan_role") not in {"mesh_warp", "armature_warp"}
            or tree.get("gyogan_center_version", 0) >= CENTER_VERSION):
        return False

    def source(socket):
        return socket.links[0].from_socket if socket.is_linked else None

    def math_node(node, operation):
        return node.bl_idname == "ShaderNodeMath" and node.operation == operation

    drivers = tree.animation_data.drivers if tree.animation_data else ()
    near_paths = {curve.data_path for curve in drivers
                  if any(target.id == scene and target.data_path.endswith(".near_distance")
                         for variable in curve.driver.variables for target in variable.targets)}
    near_nodes = [node for node in tree.nodes if node.bl_idname == "ShaderNodeValue"
                  and node.outputs[0].path_from_id("default_value") in near_paths]
    plan = []
    candidates = []
    for separate in tree.nodes:
        if separate.bl_idname != "ShaderNodeSeparateXYZ":
            continue
        point = source(separate.inputs["Vector"])
        if point is None or point.node.bl_idname != "FunctionNodeTransformPoint":
            continue
        inverse = source(point.node.inputs["Transform"])
        if inverse is None or inverse.node.bl_idname != "FunctionNodeInvertMatrix":
            continue
        candidates.append(inverse)
        x, y, z = (separate.outputs[axis] for axis in ("X", "Y", "Z"))
        depths = [link.to_node for link in z.links
                  if math_node(link.to_node, "MULTIPLY")
                  and link.to_socket == link.to_node.inputs[0]
                  and not link.to_node.inputs[1].is_linked
                  and link.to_node.inputs[1].default_value == -1.0]
        combines = []
        for link in z.links:
            combine = link.to_node
            if (combine.bl_idname != "ShaderNodeCombineXYZ"
                    or link.to_socket != combine.inputs["Z"]):
                continue
            scaled_x, scaled_y = source(combine.inputs["X"]), source(combine.inputs["Y"])
            if (scaled_x is None or scaled_y is None
                    or not math_node(scaled_x.node, "MULTIPLY")
                    or not math_node(scaled_y.node, "MULTIPLY")
                    or source(scaled_x.node.inputs[0]) != x
                    or source(scaled_y.node.inputs[0]) != y
                    or source(scaled_x.node.inputs[1]) is None
                    or source(scaled_x.node.inputs[1]) != source(scaled_y.node.inputs[1])):
                continue
            combines.append((combine, scaled_x, scaled_y))
        if len(depths) == 1 and len(combines) == 1:
            plan.append({
                "inverse": inverse, "depth": depths[0].outputs[0],
                "x": x, "y": y,
                "x_targets": tuple(link.to_socket for link in x.links),
                "y_targets": tuple(link.to_socket for link in y.links),
                "combine": combines[0][0], "scaled_x": combines[0][1], "scaled_y": combines[0][2],
            })
    if (len(near_nodes) != 1 or not plan
            or any(item["inverse"] != plan[0]["inverse"] for item in plan)
            or sum(inverse == plan[0]["inverse"] for inverse in candidates) != len(plan)):
        raise ValueError("既存の魚眼ノード構成が変更されているため、中心機能を自動更新できません")

    g = Graph(tree)
    g._index = len(tree.nodes)
    old_nodes = {node.as_pointer() for node in tree.nodes}
    old_drivers = {curve.as_pointer() for curve in drivers}
    rewires = []
    try:
        sx, sy = _center_slope(g, plan[0]["inverse"], near_nodes[0].outputs[0],
                               getattr(scene.gyogan, "center_object", None)
                               if getattr(scene.gyogan, 'center_mode', 'OBJECT') == 'OBJECT'
                               else None, scene=scene)
        for item in plan:
            ox = g.math("MULTIPLY", item["depth"], sx)
            oy = g.math("MULTIPLY", item["depth"], sy)
            centered_x = g.math("SUBTRACT", item["x"], ox)
            centered_y = g.math("SUBTRACT", item["y"], oy)
            restored_x = g.math("ADD", item["scaled_x"], ox)
            restored_y = g.math("ADD", item["scaled_y"], oy)
            rewires.extend((socket, centered_x, item["x"]) for socket in item["x_targets"])
            rewires.extend((socket, centered_y, item["y"]) for socket in item["y_targets"])
            rewires.append((item["combine"].inputs["X"], restored_x, item["scaled_x"]))
            rewires.append((item["combine"].inputs["Y"], restored_y, item["scaled_y"]))
        for socket, replacement, _ in rewires:
            tree.links.new(replacement, socket)
        tree.update_tag()
        tree["gyogan_center_version"] = CENTER_VERSION
        tree["gyogan_cursor_version"] = CURSOR_VERSION
    except Exception:
        for socket, _, original in rewires:
            tree.links.new(original, socket)
        if tree.animation_data:
            for curve in list(tree.animation_data.drivers):
                if curve.as_pointer() not in old_drivers:
                    tree.animation_data.drivers.remove(curve)
        for node in list(tree.nodes):
            if node.as_pointer() not in old_nodes:
                tree.nodes.remove(node)
        raise
    return True


def upgrade_cursor_group(tree, scene):
    """Extend an owned object-center graph in place, retaining all callers.

    Only the selected position and enable condition change. Validate the
    original center path before mutation and restore its links and drivers if
    construction fails. This also accepts the older Boolean existence driver
    when opening a 5.0/5.1 file in 5.2.
    """
    if (not tree.get('gyogan_owner')
            or tree.get('gyogan_role') not in {'mesh_warp', 'armature_warp'}
            or tree.get('gyogan_center_version', 0) < CENTER_VERSION
            or tree.get('gyogan_cursor_version', 0) >= CURSOR_VERSION):
        return False
    error = '既存の魚眼ノードの中心接続が変更されているため、3Dカーソルへ自動更新できません'

    def source(socket):
        return socket.links[0].from_socket if len(socket.links) == 1 else None

    def consumers(output, node_type, input_key):
        return [link.to_node for link in output.links
                if link.to_node.bl_idname == node_type
                and link.to_socket == link.to_node.inputs[input_key]]

    centers = [node for node in tree.nodes
               if node.get('gyogan_role') == 'center_object']
    if len(centers) != 1:
        raise ValueError(error)
    center = centers[0]
    if (center.bl_idname != 'GeometryNodeSwitch' or center.input_type != 'OBJECT'
            or center.inputs['Switch'].is_linked or center.inputs['Switch'].default_value
            or center.inputs['False'].is_linked or center.inputs['True'].is_linked
            or center.inputs['True'].default_value is not None):
        raise ValueError(error)
    infos = consumers(center.outputs[0], 'GeometryNodeObjectInfo', 'Object')
    if len(infos) != 1 or infos[0].transform_space != 'ORIGINAL':
        raise ValueError(error)
    location = infos[0].outputs['Location']
    transforms = consumers(location, 'FunctionNodeTransformPoint', 'Vector')
    if len(transforms) != 1 or len(location.links) != 1:
        raise ValueError(error)
    transform = transforms[0]
    inverse = source(transform.inputs['Transform'])
    if inverse is None or inverse.node.bl_idname != 'FunctionNodeInvertMatrix':
        raise ValueError(error)
    separates = consumers(transform.outputs[0], 'ShaderNodeSeparateXYZ', 'Vector')
    if len(separates) != 1 or len(transform.outputs[0].links) != 1:
        raise ValueError(error)
    depths = [node for node in consumers(separates[0].outputs['Z'], 'ShaderNodeMath', 0)
              if node.operation == 'MULTIPLY' and not node.inputs[1].is_linked
              and node.inputs[1].default_value == -1.0]
    if len(depths) != 1:
        raise ValueError(error)
    fronts = [node for node in consumers(depths[0].outputs[0], 'ShaderNodeMath', 0)
              if node.operation == 'GREATER_THAN']
    if len(fronts) != 1:
        raise ValueError(error)
    valids = [node for node in consumers(fronts[0].outputs[0], 'FunctionNodeBooleanMath', 1)
              if node.operation == 'AND']
    if len(valids) != 1:
        raise ValueError(error)
    valid = valids[0]
    exists = source(valid.inputs[0])
    if exists is None or len(exists.links) != 1:
        raise ValueError(error)
    drivers = tree.animation_data.drivers if tree.animation_data else ()
    exists_node = exists.node
    if exists_node.bl_idname == 'FunctionNodeCompare':
        if (exists_node.data_type != 'OBJECT' or exists_node.operation != 'NOT_EQUAL'
                or source(exists_node.inputs[0]) != center.outputs[0]
                or exists_node.inputs[1].is_linked
                or exists_node.inputs[1].default_value is not None):
            raise ValueError(error)
    elif (exists_node.bl_idname == 'ShaderNodeValue'
          and exists_node.get('gyogan_role') == 'center_exists'):
        curves = [curve for curve in drivers
                  if curve.data_path == exists.path_from_id('default_value')]
        if (len(curves) != 1 or curves[0].driver.type != 'AVERAGE'
                or len(curves[0].driver.variables) != 1):
            raise ValueError(error)
        variable = curves[0].driver.variables[0]
        if (variable.type != 'SINGLE_PROP' or variable.targets[0].id != scene
                or variable.targets[0].data_path != 'gyogan.center_exists'):
            raise ValueError(error)
    else:
        raise ValueError(error)

    g = Graph(tree)
    g._index = len(tree.nodes)
    old_nodes = {node.as_pointer() for node in tree.nodes}
    old_drivers = {curve.as_pointer() for curve in drivers}
    old_object = center.inputs['False'].default_value
    try:
        position, enabled = _center_selection(g, scene, location, exists)
        tree.links.new(position, transform.inputs['Vector'])
        tree.links.new(enabled, valid.inputs[0])
        if getattr(scene.gyogan, 'center_mode', 'OBJECT') != 'OBJECT':
            center.inputs['False'].default_value = None
        tree.update_tag()
        tree['gyogan_cursor_version'] = CURSOR_VERSION
    except Exception:
        tree.links.new(location, transform.inputs['Vector'])
        tree.links.new(exists, valid.inputs[0])
        center.inputs['False'].default_value = old_object
        if tree.animation_data:
            for curve in list(tree.animation_data.drivers):
                if curve.as_pointer() not in old_drivers:
                    tree.animation_data.drivers.remove(curve)
        for node in list(tree.nodes):
            if node.as_pointer() not in old_nodes:
                tree.nodes.remove(node)
        raise
    return True


def upgrade_signed_group(tree, scene):
    """Add signed strength to an owned legacy leaf without rebuilding its tree.

    Call after the strength and center migrations. Validate both the original
    on-axis scale switches and the strength blend clamps before adding nodes;
    preserve existing interfaces, drivers, nodes and all ID references.
    """
    if (not tree.get("gyogan_owner")
            or tree.get("gyogan_role") not in {"mesh_warp", "armature_warp"}
            or tree.get("gyogan_signed_version", 0) >= SIGNED_VERSION):
        return False

    def source(socket):
        return socket.links[0].from_socket if socket.is_linked else None

    def is_math(node, operation):
        return node.bl_idname == "ShaderNodeMath" and node.operation == operation

    drivers = tree.animation_data.drivers if tree.animation_data else ()
    strength_paths = {curve.data_path for curve in drivers
                      if any(target.id == scene and target.data_path.endswith(".strength")
                             for variable in curve.driver.variables for target in variable.targets)}
    strength_nodes = [node for node in tree.nodes if node.bl_idname == "ShaderNodeValue"
                      and node.outputs[0].path_from_id("default_value") in strength_paths]
    if len(strength_nodes) != 1:
        raise ValueError("既存の魚眼強度ドライバーが変更されているため、負の強度へ自動更新できません")
    strength = strength_nodes[0].outputs[0]

    scales = []
    for node in tree.nodes:
        if node.bl_idname != "GeometryNodeSwitch" or node.input_type != "FLOAT":
            continue
        condition, standard, central = (source(node.inputs[name]) for name in ("Switch", "False", "True"))
        if (condition is None or standard is None or central is None
                or not is_math(condition.node, "LESS_THAN")
                or not is_math(standard.node, "DIVIDE")):
            continue
        radius = source(condition.node.inputs[0])
        if (radius is None or radius.node.bl_idname != "ShaderNodeVectorMath"
                or radius.node.operation != "LENGTH"):
            continue
        targets = tuple(link.to_socket for link in node.outputs[0].links)
        if (len(targets) != 2 or any(not is_math(socket.node, "MULTIPLY")
                                    or socket != socket.node.inputs[1] for socket in targets)):
            raise ValueError("既存の魚眼スケール接続が変更されているため、負の強度へ自動更新できません")
        scales.append((node.outputs[0], central, targets))

    clamps = []
    for node in tree.nodes:
        if (not is_math(node, "MAXIMUM") or source(node.inputs[0]) != strength
                or node.inputs[1].is_linked or node.inputs[1].default_value != 0.0):
            continue
        consumers = tuple(node.outputs[0].links)
        if (len(consumers) != 1 or not is_math(consumers[0].to_node, "MINIMUM")
                or consumers[0].to_socket != consumers[0].to_node.inputs[0]
                or consumers[0].to_node.inputs[1].is_linked
                or consumers[0].to_node.inputs[1].default_value != 1.0):
            raise ValueError("既存の魚眼強度の混合が変更されているため、負の強度へ自動更新できません")
        clamps.append(node.inputs[0])
    if not scales or len(clamps) != len(scales):
        raise ValueError("既存の魚眼ノード構成が変更されているため、負の強度へ自動更新できません")

    g = Graph(tree)
    g._index = len(tree.nodes)
    old_nodes = {node.as_pointer() for node in tree.nodes}
    rewires = []
    try:
        magnitude = g.math("ABSOLUTE", strength)
        reverse_amount = g.math("MAXIMUM", magnitude, 1.0)
        is_negative = g.math("GREATER_THAN", 0.0, strength)
        for standard, central, targets in scales:
            signed = _signed_scale(g, standard, central, reverse_amount, is_negative)
            rewires.extend((socket, signed, standard) for socket in targets)
        rewires.extend((socket, magnitude, strength) for socket in clamps)
        for socket, replacement, _ in rewires:
            tree.links.new(replacement, socket)
        tree.update_tag()
        tree["gyogan_signed_version"] = SIGNED_VERSION
    except Exception:
        for socket, _, original in rewires:
            tree.links.new(original, socket)
        for node in list(tree.nodes):
            if node.as_pointer() not in old_nodes:
                tree.nodes.remove(node)
        raise
    return True


def upgrade_ellipse_group(tree, scene):
    """Add elliptical radius inputs to an owned legacy leaf, preserving IDs.

    Run after the strength, center and signed migrations. Validate every legacy
    radial leaf before touching links; preserve the original nodes, sockets and
    drivers. A failed construction also removes new drivers, which are owned by
    the node tree rather than by the newly created Value nodes.
    """
    if (not tree.get("gyogan_owner")
            or tree.get("gyogan_role") not in {"mesh_warp", "armature_warp"}
            or tree.get("gyogan_ellipse_version", 0) >= ELLIPSE_VERSION):
        return False

    error = "既存の魚眼ノード構成が変更されているため、縦横の調整機能を自動更新できません"

    def source(socket):
        return socket.links[0].from_socket if socket.is_linked else None

    def is_math(output, operation):
        return (output is not None and output.node.bl_idname == "ShaderNodeMath"
                and output.node.operation == operation)

    def constant(socket, value):
        return not socket.is_linked and abs(socket.default_value - value) < 1.0e-12

    def targets(output):
        return {link.to_socket.as_pointer() for link in output.links}

    drivers = tree.animation_data.drivers if tree.animation_data else ()
    strength_paths = {}
    for curve in drivers:
        paths = {target.data_path for variable in curve.driver.variables
                 for target in variable.targets
                 if target.id == scene and target.data_path.endswith(".strength")}
        if paths:
            strength_paths[curve.data_path] = paths
    strength_nodes = [node for node in tree.nodes if node.bl_idname == "ShaderNodeValue"
                      and node.outputs[0].path_from_id("default_value") in strength_paths]
    if (len(strength_nodes) != 1
            or tree.get("gyogan_projection_version", 0) < PROJECTION_VERSION
            or tree.get("gyogan_center_version", 0) < CENTER_VERSION
            or tree.get("gyogan_signed_version", 0) < SIGNED_VERSION):
        raise ValueError(error)
    strength = strength_nodes[0].outputs[0]
    settings_paths = strength_paths[strength.path_from_id("default_value")]
    if len(settings_paths) != 1:
        raise ValueError(error)
    settings_path = next(iter(settings_paths)).removesuffix(".strength")
    # Fail before adding anything if the scene's settings have not been updated.
    scene.path_resolve(settings_path + ".ellipse_x")
    scene.path_resolve(settings_path + ".ellipse_y")

    plan = []
    for theta in tree.nodes:
        if theta.bl_idname != "ShaderNodeMath" or theta.operation != "ARCTAN2":
            continue
        angular_radius = source(theta.inputs[0])
        positive_depth = source(theta.inputs[1])
        if not is_math(angular_radius, "MULTIPLY") or not is_math(positive_depth, "MAXIMUM"):
            raise ValueError(error)
        radius = source(angular_radius.node.inputs[0])
        angular_strength = source(angular_radius.node.inputs[1])
        if (radius is None or radius.node.bl_idname != "ShaderNodeVectorMath"
                or radius.node.operation != "LENGTH"
                or not is_math(angular_strength, "MAXIMUM")
                or source(angular_strength.node.inputs[0]) != strength
                or not constant(angular_strength.node.inputs[1], 1.0)
                or not constant(positive_depth.node.inputs[1], EPSILON)):
            raise ValueError(error)
        vector = source(radius.node.inputs[0])
        if (vector is None or vector.node.bl_idname != "ShaderNodeCombineXYZ"
                or not constant(vector.node.inputs["Z"], 0.0)
                or targets(vector) != {radius.node.inputs[0].as_pointer()}):
            raise ValueError(error)
        x, y = (source(vector.node.inputs[axis]) for axis in ("X", "Y"))
        if not is_math(x, "SUBTRACT") or not is_math(y, "SUBTRACT"):
            raise ValueError(error)
        raw_x, raw_y = source(x.node.inputs[0]), source(y.node.inputs[0])
        if (raw_x is None or raw_y is None or raw_x.node != raw_y.node
                or raw_x.node.bl_idname != "ShaderNodeSeparateXYZ"
                or raw_x != raw_x.node.outputs["X"] or raw_y != raw_y.node.outputs["Y"]):
            raise ValueError(error)
        depth = source(positive_depth.node.inputs[0])
        if (not is_math(depth, "MULTIPLY")
                or source(depth.node.inputs[0]) != raw_x.node.outputs["Z"]
                or not constant(depth.node.inputs[1], -1.0)):
            raise ValueError(error)
        for coordinate in (x, y):
            offset = source(coordinate.node.inputs[1])
            if not is_math(offset, "MULTIPLY") or source(offset.node.inputs[0]) != depth:
                raise ValueError(error)
        axis_tests = [link.to_node for link in radius.links
                      if link.to_node.bl_idname == "ShaderNodeMath"
                      and link.to_node.operation == "LESS_THAN"
                      and link.to_socket == link.to_node.inputs[0]]
        if len(axis_tests) != 1:
            raise ValueError(error)
        axis = axis_tests[0]
        threshold = source(axis.inputs[1])
        if (not is_math(threshold, "MULTIPLY")
                or source(threshold.node.inputs[0]) != positive_depth
                or not constant(threshold.node.inputs[1], 1.0e-6)
                or targets(radius) != {angular_radius.node.inputs[0].as_pointer(),
                                       axis.inputs[0].as_pointer()}):
            raise ValueError(error)
        # Original centered coordinates must still feed one common XY scale;
        # only their links to the radius vector are replaced by this migration.
        xy_scales = []
        for coordinate, axis_name in ((x, "X"), (y, "Y")):
            scales = [link.to_node for link in coordinate.links
                      if link.to_node.bl_idname == "ShaderNodeMath"
                      and link.to_node.operation == "MULTIPLY"
                      and link.to_socket == link.to_node.inputs[0]]
            if (len(scales) != 1
                    or targets(coordinate) != {vector.node.inputs[axis_name].as_pointer(),
                                               scales[0].inputs[0].as_pointer()}):
                raise ValueError(error)
            xy_scales.append(source(scales[0].inputs[1]))
        if (xy_scales[0] is None or xy_scales[0] != xy_scales[1]
                or xy_scales[0].node.bl_idname != "GeometryNodeSwitch"
                or xy_scales[0].node.input_type != "FLOAT"):
            raise ValueError(error)
        plan.append((vector.node, x, y))
    if not plan or len({item[0].as_pointer() for item in plan}) != len(plan):
        raise ValueError(error)

    g = Graph(tree)
    g._index = len(tree.nodes)
    old_nodes = {node.as_pointer() for node in tree.nodes}
    old_drivers = {curve.as_pointer() for curve in drivers}
    rewires = []
    try:
        ellipse_x = g.math("MAXIMUM", g.driven_value(
            scene, settings_path + ".ellipse_x", "魚眼の横幅"), 0.1)
        ellipse_y = g.math("MAXIMUM", g.driven_value(
            scene, settings_path + ".ellipse_y", "魚眼の縦幅"), 0.1)
        for vector, x, y in plan:
            normalized_x = g.math("DIVIDE", x, ellipse_x)
            normalized_y = g.math("DIVIDE", y, ellipse_y)
            rewires.append((vector.inputs["X"], normalized_x, x))
            rewires.append((vector.inputs["Y"], normalized_y, y))
        for socket, replacement, _ in rewires:
            tree.links.new(replacement, socket)
        tree.update_tag()
        tree["gyogan_ellipse_version"] = ELLIPSE_VERSION
    except Exception:
        for socket, _, original in rewires:
            tree.links.new(original, socket)
        if tree.animation_data:
            for curve in list(tree.animation_data.drivers):
                if curve.as_pointer() not in old_drivers:
                    tree.animation_data.drivers.remove(curve)
        for node in list(tree.nodes):
            if node.as_pointer() not in old_nodes:
                tree.nodes.remove(node)
        raise
    return True


def upgrade_dolly_group(tree, scene):
    """Wrap the legacy projection calibration with an independent dolly factor."""
    if (not tree.get("gyogan_owner")
            or tree.get("gyogan_role") not in {"mesh_warp", "armature_warp"}
            or tree.get("gyogan_dolly_version", 0) >= DOLLY_VERSION):
        return False
    message = "既存の魚眼ノード構成が変更されているため、ドリーズームへ自動更新できません"

    def source(socket):
        return socket.links[0].from_socket if socket.is_linked else None

    def is_math(socket, operation):
        return socket is not None and socket.node.bl_idname == 'ShaderNodeMath' and socket.node.operation == operation

    drivers = tree.animation_data.drivers if tree.animation_data else ()
    zoom_paths = {curve.data_path for curve in drivers
                  if any(target.id == scene and target.data_path.endswith('.zoom')
                         for variable in curve.driver.variables for target in variable.targets)}
    zoom_values = [node.outputs[0] for node in tree.nodes if node.bl_idname == 'ShaderNodeValue'
                   and node.outputs[0].path_from_id('default_value') in zoom_paths]
    if len(zoom_values) != 1:
        raise ValueError(message)
    calibrations = []
    for node in tree.nodes:
        if node.bl_idname != 'ShaderNodeMath' or node.operation != 'DIVIDE':
            continue
        numerator, denominator = source(node.inputs[0]), source(node.inputs[1])
        if not is_math(numerator, 'MULTIPLY') or not is_math(denominator, 'MAXIMUM'):
            continue
        zoom = source(numerator.node.inputs[1])
        if (not is_math(zoom, 'MAXIMUM') or source(zoom.node.inputs[0]) != zoom_values[0]
                or zoom.node.inputs[1].is_linked or zoom.node.inputs[1].default_value != 0.0):
            continue
        if (denominator.node.inputs[1].is_linked
                or abs(denominator.node.inputs[1].default_value - 1.0e-4) > 1.0e-10):
            raise ValueError(message)
        calibrations.append(node.outputs[0])
    if len(calibrations) != 1 or not calibrations[0].is_linked:
        raise ValueError(message)
    original = calibrations[0]
    destinations = [link.to_socket for link in original.links]
    old_nodes = {node.as_pointer() for node in tree.nodes}
    old_drivers = {curve.as_pointer() for curve in drivers}
    graph = Graph(tree)
    graph._index = len(tree.nodes)
    try:
        factor = _dolly_factor(graph, scene)
        calibrated = graph.math('MULTIPLY', original, factor)
        for socket in destinations:
            tree.links.new(calibrated, socket)
        tree.update_tag()
        tree['gyogan_dolly_version'] = DOLLY_VERSION
    except Exception:
        for socket in destinations:
            tree.links.new(original, socket)
        if tree.animation_data:
            for curve in list(tree.animation_data.drivers):
                if curve.as_pointer() not in old_drivers:
                    tree.animation_data.drivers.remove(curve)
        for node in list(tree.nodes):
            if node.as_pointer() not in old_nodes:
                tree.nodes.remove(node)
        raise
    return True


def camera_space_reference(point, strength, fov, zoom, near_distance, horizontal_half_width,
                           center_point=None, ellipse_x=1.0, ellipse_y=1.0,
                           dolly_factor=1.0):
    """Pure Python oracle matching CameraWarp, for isolated numerical tests.

    ``point`` is a camera-space XYZ tuple, FOV is horizontal and in radians,
    and horizontal_half_width is the camera frame's half width at depth 1.
    Optional ``center_point`` is also in camera space; its ray is anchored when
    the center is at least twice near_distance in front of the camera. Between
    near_distance and twice that value, its effect fades smoothly from zero.
    Invalid/behind-camera centers use the optical axis. Lens shift is applied
    later by the actual camera projection. ``ellipse_x`` and ``ellipse_y`` are
    positive horizontal/vertical widths: (1, 1) keeps the circular mapping;
    larger widths spread its falloff along the corresponding camera axis.
    """
    x, y, z = point
    depth = -z
    near = max(float(near_distance), EPSILON)
    fade = min(max((depth - near) / near, 0.0), 1.0)
    weight = min(abs(float(strength)), 1.0) * fade * fade * (3.0 - 2.0 * fade)
    if weight == 0.0:
        return tuple(point)
    offset_x = offset_y = 0.0
    if center_point is not None and -center_point[2] > near:
        center_depth = -center_point[2]
        center_fade = min(max((center_depth - near) / near, 0.0), 1.0)
        center_fade = center_fade * center_fade * (3.0 - 2.0 * center_fade)
        offset_x = depth * (center_point[0] / center_depth * center_fade)
        offset_y = depth * (center_point[1] / center_depth * center_fade)
    local_x, local_y = x - offset_x, y - offset_y
    radius = math.hypot(local_x / max(float(ellipse_x), 0.1),
                        local_y / max(float(ellipse_y), 0.1))
    k = 2.0 * abs(horizontal_half_width) * max(zoom, 0.0) * dolly_factor / max(fov, 1.0e-4)
    positive_depth = max(depth, EPSILON)
    if radius < positive_depth * 1.0e-6:
        scale = k
    else:
        angular_radius = radius * max(float(strength), 1.0)
        scale = positive_depth * k * math.atan2(angular_radius, positive_depth) / max(angular_radius, EPSILON)
    if strength < 0.0:
        scale = k + max(abs(float(strength)), 1.0) * (k - scale)
    return (x + weight * (local_x * scale + offset_x - x),
            y + weight * (local_y * scale + offset_y - y), z)
