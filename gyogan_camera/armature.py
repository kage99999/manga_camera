"""Non-destructive armature output, with Blender 5.2 as the primary target.

The original rig remains the animation/IK input. A separate rig receives world
matrices through Geometry Attribute constraints. Only output object copies point
their Armature modifiers at the generated rig; mesh and shape-key data are shared
read-only with the originals.
"""

import bpy
from mathutils import Matrix

from .nodes import CameraWarp, Graph


OWNER_KEY = "gyogan_owner"
_POSE_DRIVER_SCALE = 1000.0
_PHYSICS_MODIFIERS = {
    "CLOTH", "SOFT_BODY", "FLUID", "PARTICLE_SYSTEM", "DYNAMIC_PAINT",
}


def _local_editable(block):
    return block.library is None and block.override_library is None


def validate_rig(rig, scene):
    """Return all scene meshes skinned to *rig*, or raise a useful ValueError.

    Preflight is intentionally conservative: reject known cases where one matrix
    per deform bone cannot reproduce the original skin at strength zero.
    """
    if rig.type != 'ARMATURE':
        raise ValueError(f"{rig.name}: アーマチュアを指定してください")
    if not _local_editable(rig) or not _local_editable(rig.data):
        raise ValueError(f"{rig.name}: リンク／ライブラリオーバーライドは未対応です")
    if len(rig.users_scene) > 1:
        raise ValueError(f"{rig.name}: 複数シーンで共有するリグは未対応です")
    if abs(rig.matrix_world.determinant()) < 1.0e-10:
        raise ValueError(f"{rig.name}: スケールが 0 の変換は使用できません")
    if rig.mode != 'OBJECT':
        raise ValueError(f"{rig.name}: オブジェクトモードで登録してください")
    deform = [bone for bone in rig.data.bones if bone.use_deform]
    if not deform:
        raise ValueError(f"{rig.name}: 「変形」が有効なボーンがありません")
    b_bones = [bone.name for bone in deform if bone.bbone_segments > 1]
    if b_bones:
        raise ValueError(
            f"{rig.name}: 複数セグメントのベンディボーンは未対応です（{', '.join(b_bones[:4])}）。"
            "メッシュモードを使用してください"
        )
    if any(bone.length < 1.0e-6 for bone in deform):
        raise ValueError(f"{rig.name}: 長さが極端に短いボーンがあります")
    meshes = []
    for obj in scene.objects:
        if obj.get(OWNER_KEY) or obj.type != 'MESH':
            continue
        modifiers = [mod for mod in obj.modifiers if mod.type == 'ARMATURE']
        matching = [mod for mod in modifiers if mod.object == rig]
        if not matching:
            continue
        if not _local_editable(obj) or not _local_editable(obj.data):
            raise ValueError(f"{obj.name}: リンク／ライブラリオーバーライドは未対応です")
        if len(obj.users_scene) > 1:
            raise ValueError(f"{obj.name}: 複数シーンで共有するメッシュは未対応です")
        if abs(obj.matrix_world.determinant()) < 1.0e-10:
            raise ValueError(f"{obj.name}: スケールが 0 の変換は使用できません")
        if len(modifiers) != 1:
            raise ValueError(f"{obj.name}: 複数のアーマチュアモディファイアは未対応です")
        modifier = matching[0]
        if modifier.use_bone_envelopes or not modifier.use_vertex_groups:
            raise ValueError(f"{obj.name}: 頂点グループのウェイトのみ対応します（エンベロープは未対応）")
        if modifier.use_multi_modifier:
            raise ValueError(f"{obj.name}: アーマチュアの「複数モディファイア」設定は未対応です")
        if obj.parent_type in {'BONE', 'BONE_RELATIVE'}:
            raise ValueError(f"{obj.name}: ボーン親子のメッシュは未対応です")
        if any(mod.type in _PHYSICS_MODIFIERS for mod in obj.modifiers):
            raise ValueError(f"{obj.name}: 物理モディファイアは人物モードでは未対応です")
        if obj.rigid_body or obj.rigid_body_constraint:
            raise ValueError(f"{obj.name}: 剛体は人物モードでは未対応です")
        meshes.append(obj)
    if not meshes:
        raise ValueError(f"{rig.name}: ウェイト付きメッシュがシーン内にありません")
    return meshes


def _tag(block, token, role):
    block[OWNER_KEY] = token
    block['gyogan_role'] = role
    return block


def _copy_transforms(output, source):
    constraint = output.constraints.new('COPY_TRANSFORMS')
    constraint.name = "魚眼 · 元オブジェクトの変換に追従"
    constraint.target = source
    constraint.owner_space = 'WORLD'
    constraint.target_space = 'WORLD'
    constraint.mix_mode = 'REPLACE'
    return constraint


def _flatten_output_bones(output):
    """Edit only the copied armature and restore the caller's active object."""
    view_layer = bpy.context.view_layer
    old_active = view_layer.objects.active
    old_selected = output.select_get()
    view_layer.objects.active = output
    output.select_set(True)
    try:
        with bpy.context.temp_override(
            object=output, active_object=output,
            selected_objects=[output], selected_editable_objects=[output],
        ):
            bpy.ops.object.mode_set(mode='EDIT')
            try:
                for bone in output.data.edit_bones:
                    # Clearing a connected parent must not move the bind pose.
                    matrix = bone.matrix.copy()
                    length = bone.length
                    bone.use_connect = False
                    bone.parent = None
                    bone.matrix = matrix
                    bone.length = length
                for bone in list(output.data.edit_bones):
                    if not bone.use_deform:
                        output.data.edit_bones.remove(bone)
            finally:
                bpy.ops.object.mode_set(mode='OBJECT')
    finally:
        output.select_set(old_selected)
        view_layer.objects.active = old_active


def _columns_matrix(graph, x, y, z, origin):
    matrix = graph.node('FunctionNodeCombineMatrix', '変形後のボーンのワールド行列')
    for column_index, vector in enumerate((x, y, z, origin), start=1):
        components = graph.node('ShaderNodeSeparateXYZ')
        graph.connect(vector, components, 'Vector')
        for row_index, axis in enumerate(('X', 'Y', 'Z'), start=1):
            graph.connect(
                components.outputs[axis], matrix,
                f'Column {column_index} Row {row_index}',
            )
        matrix.inputs[f'Column {column_index} Row 4'].default_value = (
            1.0 if column_index == 4 else 0.0
        )
    return matrix.outputs['Matrix']


def _thickness_driver(graph, scene):
    return graph.driven_value(scene, 'gyogan.thickness', 'ボーンの太さを追従')


def _bone_pose(graph, rig, bone):
    """Read the evaluated world pose without writing to the source armature.

    Bone Info is unavailable in Blender 5.0. Simple RNA drivers still provide
    pose dependencies there, including constraints and IK. Reading all matrix
    components preserves affine/shear information, unlike decomposing a pose
    into location, rotation, and scale. No Python frame handler is required.
    """
    if hasattr(bpy.types, 'GeometryNodeBoneInfo'):
        info = graph.node('GeometryNodeBoneInfo', bone.name)
        # The calculator has identity world transform, so RELATIVE is world space.
        info.transform_space = 'RELATIVE'
        info.inputs['Armature'].default_value = rig
        info.inputs['Bone Name'].default_value = bone.name
        return info.outputs['Pose']

    pose_bone = rig.pose.bones[bone.name]
    data_path = pose_bone.path_from_id('matrix')
    matrix = graph.node('FunctionNodeCombineMatrix', '元ボーンのポーズ行列')
    for column in range(4):
        for row in range(4):
            # RNA driver array addressing is column-major. mathutils' Python
            # indexing is row-major, including the initial un-evaluated value.
            value = graph.driven_value(
                rig, f'{data_path}[{column}][{row}]',
                f'{bone.name} · 行列 {row + 1},{column + 1}',
            )
            # Blender 5.0 can keep a near-zero driven socket at its saved zero
            # value for component changes around 1e-5. Scale around the driver
            # boundary so tiny evaluated IK rotations survive that tolerance.
            # This remains a simple expression evaluated without Python code.
            driver = value.driver_add('default_value').driver
            driver.type = 'SCRIPTED'
            driver.expression = f'value*{_POSE_DRIVER_SCALE}'
            value.default_value = pose_bone.matrix[row][column] * _POSE_DRIVER_SCALE
            component = graph.math('DIVIDE', value, _POSE_DRIVER_SCALE)
            graph.connect(component, matrix, f'Column {column + 1} Row {row + 1}')
    info = graph.node('GeometryNodeObjectInfo', '元リグのワールド変換')
    info.transform_space = 'ORIGINAL'
    info.inputs['Object'].default_value = rig
    world = graph.node('FunctionNodeMatrixMultiply', '元ボーンのワールド行列')
    world['gyogan_role'] = 'bone_pose_matrix'
    world['gyogan_bone'] = bone.name
    graph.connect(info.outputs['Transform'], world, 0)
    graph.connect(matrix.outputs['Matrix'], world, 1)
    return world.outputs[0]


def _is_original_bone_pose(socket):
    """Recognize both owned pose readers when upgrading old signed gates."""
    node = socket.node
    if node.bl_idname == 'GeometryNodeBoneInfo':
        return socket.name == 'Pose'
    if (node.bl_idname != 'FunctionNodeMatrixMultiply'
            or node.get('gyogan_role') != 'bone_pose_matrix'
            or not all(item.is_linked for item in node.inputs)):
        return False
    info = node.inputs[0].links[0].from_node
    matrix = node.inputs[1].links[0].from_node
    if (info.bl_idname != 'GeometryNodeObjectInfo'
            or info.transform_space != 'ORIGINAL'
            or info.inputs['Object'].is_linked
            or matrix.bl_idname != 'FunctionNodeCombineMatrix'):
        return False
    rig = info.inputs['Object'].default_value
    bone_name = node.get('gyogan_bone', '')
    if not rig or rig.type != 'ARMATURE' or bone_name not in rig.pose.bones:
        return False
    data_path = rig.pose.bones[bone_name].path_from_id('matrix')
    tree = node.id_data
    drivers = tree.animation_data.drivers if tree.animation_data else ()
    for column in range(4):
        for row in range(4):
            item = matrix.inputs[f'Column {column + 1} Row {row + 1}']
            if not item.is_linked:
                return False
            normalize = item.links[0].from_node
            if (normalize.bl_idname != 'ShaderNodeMath' or normalize.operation != 'DIVIDE'
                    or not normalize.inputs[0].is_linked or normalize.inputs[1].is_linked
                    or normalize.inputs[1].default_value != _POSE_DRIVER_SCALE):
                return False
            source = normalize.inputs[0].links[0].from_socket
            if source.node.bl_idname != 'ShaderNodeValue':
                return False
            curve = next((curve for curve in drivers
                          if curve.data_path == source.path_from_id('default_value')), None)
            if (not curve or curve.driver.type != 'SCRIPTED'
                    or curve.driver.expression != f'value*{_POSE_DRIVER_SCALE}'
                    or len(curve.driver.variables) != 1):
                return False
            variable = curve.driver.variables[0]
            if (variable.type != 'SINGLE_PROP' or variable.targets[0].id != rig
                    or variable.targets[0].data_path != f'{data_path}[{column}][{row}]'):
                return False
    return True


class _RigWarp:
    """Share the projection graph across all four samples of every bone."""

    def __init__(self, graph, scene, token, name):
        self.graph = graph
        self.strength = graph.driven_value(scene, 'gyogan.strength', '魚眼の強度')
        self.magnitude = graph.math('ABSOLUTE', self.strength)
        self.magnitude.node.label = '強度の絶対値（人物の変形切替）'
        self.magnitude.node['gyogan_role'] = 'signed_gate'
        self.tree = _tag(
            bpy.data.node_groups.new(name, 'GeometryNodeTree'),
            token, 'armature_warp',
        )
        self.tree.interface.new_socket(name='Point', in_out='INPUT', socket_type='NodeSocketVector')
        self.tree.interface.new_socket(name='Point', in_out='OUTPUT', socket_type='NodeSocketVector')
        local_graph = Graph(self.tree)
        point = local_graph.node('NodeGroupInput').outputs['Point']
        warp = CameraWarp(local_graph, scene, space='ORIGINAL')
        result = warp.apply(point)
        local_graph.connect(result, local_graph.node('NodeGroupOutput'), 'Point')

    def apply(self, point):
        node = self.graph.node('GeometryNodeGroup', 'カメラ基準の魚眼変形')
        node.node_tree = self.tree
        self.graph.connect(point, node, 'Point')
        return node.outputs['Point']


def _warped_bone_matrix(graph, warp, rig, bone, thickness):
    pose = _bone_pose(graph, rig, bone)
    head = graph.transform(pose, (0.0, 0.0, 0.0))
    tail = graph.transform(pose, (0.0, bone.length, 0.0))
    new_head, new_tail = warp.apply(head), warp.apply(tail)
    y_axis = graph.vector('SCALE', graph.vector('SUBTRACT', new_tail, new_head), scale=1.0 / bone.length)
    # Transverse samples retain roll and affine/shear information. Sampling at a
    # fraction of bone length avoids using an arbitrary one-world-unit width.
    step = max(1.0e-4, bone.length * 0.05)
    axes = []
    for offset in ((step, 0.0, 0.0), (0.0, 0.0, step)):
        sample = graph.transform(pose, offset)
        original_axis = graph.vector('SCALE', graph.vector('SUBTRACT', sample, head), scale=1.0 / step)
        warped_axis = graph.vector('SCALE', graph.vector('SUBTRACT', warp.apply(sample), new_head), scale=1.0 / step)
        original_length = graph.vector('LENGTH', original_axis)
        fixed_width = graph.vector('SCALE', graph.vector('NORMALIZE', warped_axis), scale=original_length)
        choose_width = graph.node('GeometryNodeSwitch', '太さの変形を切り替え')
        choose_width.input_type = 'VECTOR'
        graph.connect(thickness, choose_width, 'Switch')
        graph.connect(fixed_width, choose_width, 'False')
        graph.connect(warped_axis, choose_width, 'True')
        axes.append(choose_width.outputs['Output'])
    transformed = _columns_matrix(graph, axes[0], y_axis, axes[1], new_head)
    identity = graph.node('GeometryNodeSwitch', '強度ゼロでは元のポーズを使用')
    identity.input_type = 'MATRIX'
    graph.connect(graph.math('GREATER_THAN', warp.magnitude, 0.0), identity, 'Switch')
    graph.connect(pose, identity, 'False')
    graph.connect(transformed, identity, 'True')
    return identity.outputs['Output']


def create_rig(rig, meshes, scene, collection, token):
    """Create a reversible output rig; source visibility is managed by the caller.

    The caller must preflight every rig before creating any of them and perform
    token-based cleanup if any later setup step fails.
    """
    output_rig = _tag(rig.copy(), token, 'output_rig')
    output_rig.name = f"魚眼 · {rig.name}"
    output_rig.data = _tag(rig.data.copy(), token, 'output_armature')
    output_rig.data.name = f"魚眼 · {rig.data.name}"
    output_rig.animation_data_clear()
    output_rig.data.animation_data_clear()
    output_rig.parent = None
    output_rig.matrix_world = rig.matrix_world.copy()
    output_rig.hide_viewport = False
    output_rig.hide_render = rig.hide_render
    output_rig.hide_select = False
    for constraint in list(output_rig.constraints):
        output_rig.constraints.remove(constraint)
    collection.objects.link(output_rig)
    output_rig.hide_set(False)
    _copy_transforms(output_rig, rig)
    _flatten_output_bones(output_rig)
    for pose_bone in output_rig.pose.bones:
        for constraint in list(pose_bone.constraints):
            pose_bone.constraints.remove(constraint)
        pose_bone.matrix_basis = Matrix.Identity(4)
        pose_bone.custom_shape = None
    output_rig.data.pose_position = 'POSE'

    calc_mesh = _tag(bpy.data.meshes.new(f"魚眼 · {rig.name} · 計算用"), token, 'calculator_mesh')
    calc_mesh.from_pydata([(0.0, 0.0, 0.0)], [], [])
    calc_mesh.update()
    calculator = _tag(bpy.data.objects.new(calc_mesh.name, calc_mesh), token, 'calculator')
    collection.objects.link(calculator)
    calculator.hide_render = True
    calculator.hide_select = True
    calculator.display_type = 'WIRE'

    tree = _tag(bpy.data.node_groups.new(f"魚眼 · {rig.name} · ボーン行列", 'GeometryNodeTree'), token, 'armature_nodes')
    tree.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    tree.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    tree.is_modifier = True
    graph = Graph(tree)
    geometry = graph.node('NodeGroupInput').outputs['Geometry']
    warp = _RigWarp(graph, scene, token, f"魚眼 · {rig.name} · 座標変形")
    thickness = _thickness_driver(graph, scene)
    for index, bone in enumerate(output_rig.data.bones):
        attribute_name = f'gyogan_bone_{index:04d}'
        bone_matrix = _warped_bone_matrix(graph, warp, rig, bone, thickness)
        store = graph.node('GeometryNodeStoreNamedAttribute', bone.name)
        store.domain = 'POINT'
        store.data_type = 'FLOAT4X4'
        store.inputs['Name'].default_value = attribute_name
        graph.connect(geometry, store, 'Geometry')
        graph.connect(bone_matrix, store, 'Value')
        geometry = store.outputs['Geometry']
        constraint = output_rig.pose.bones[bone.name].constraints.new('GEOMETRY_ATTRIBUTE')
        constraint.name = '魚眼 · 変形後のポーズ'
        constraint.target = calculator
        constraint.data_type = 'FLOAT4X4'
        constraint.domain = 'POINT'
        constraint.attribute_name = attribute_name
        constraint.sample_index = 0
        constraint.mix_mode = 'REPLACE'
        constraint.owner_space = 'WORLD'
        constraint.target_space = 'WORLD'
        constraint.apply_target_transform = False
        constraint.mix_loc = True
        constraint.mix_rot = True
        constraint.mix_scl = True
    graph.connect(geometry, graph.node('NodeGroupOutput'), 'Geometry')
    modifier = calculator.modifiers.new('魚眼 · ボーン変形の計算', 'NODES')
    modifier.node_group = tree

    outputs = []
    for source in meshes:
        output = _tag(source.copy(), token, 'output_mesh')
        output.name = f"魚眼 · {source.name}"
        # source.copy() shares Mesh/Key data, preserving vertex groups and live
        # shape-key drivers without ever editing those original data blocks.
        # Preserve collection-level visibility/exclusion exactly. Linking these
        # copies to the generated scene-level collection would reveal hidden
        # clothes or meshes excluded from a particular View Layer.
        for source_collection in source.users_collection:
            source_collection.objects.link(output)
        if output.name in bpy.context.view_layer.objects:
            output.hide_set(False)
        for modifier in output.modifiers:
            if modifier.type == 'ARMATURE' and modifier.object == rig:
                modifier.object = output_rig
        _copy_transforms(output, source)
        outputs.append((source, output))
    tree['gyogan_signed_version'] = 1
    return {
        'output_rig': output_rig,
        'calculator': calculator,
        'outputs': outputs,
        'node_group': tree,
    }


def upgrade_signed_group(tree):
    """Enable negative strength on owned output-rig identity gates in place.

    The point-warp subgroup is migrated independently by nodes.py. Validate all
    existing matrix gates before changing links, so a hand-edited graph cannot
    be left halfway migrated.
    """
    if (not tree.get(OWNER_KEY) or tree.get('gyogan_role') != 'armature_nodes'
            or tree.get('gyogan_signed_version', 0) >= 1):
        return False
    drivers = tree.animation_data.drivers if tree.animation_data else ()
    paths = {curve.data_path for curve in drivers
             if any(target.data_path.endswith('.strength')
                    for variable in curve.driver.variables for target in variable.targets)}
    sources = [node.outputs[0] for node in tree.nodes
               if node.bl_idname == 'ShaderNodeValue'
               and node.outputs[0].path_from_id('default_value') in paths]
    if len(sources) != 1:
        raise ValueError('人物の強度ノードが変更されているため、自動更新できません')
    source = sources[0]
    gates, existing_magnitude = [], None
    switches = [node for node in tree.nodes
                if node.bl_idname == 'GeometryNodeSwitch' and node.input_type == 'MATRIX']
    for switch in switches:
        condition, original = switch.inputs['Switch'], switch.inputs['False']
        if not condition.is_linked or not original.is_linked:
            raise ValueError('人物の復元ノードが変更されているため、自動更新できません')
        gate = condition.links[0].from_node
        original_socket = original.links[0].from_socket
        if (gate.bl_idname != 'ShaderNodeMath' or gate.operation != 'GREATER_THAN'
                or not gate.inputs[0].is_linked or gate.inputs[1].is_linked
                or gate.inputs[1].default_value != 0.0
                or not _is_original_bone_pose(original_socket)):
            raise ValueError('人物の復元ノードが変更されているため、自動更新できません')
        input_socket = gate.inputs[0].links[0].from_socket
        if input_socket == source:
            gates.append(gate)
        elif (input_socket.node.bl_idname == 'ShaderNodeMath'
              and input_socket.node.operation == 'ABSOLUTE'
              and input_socket.node.inputs[0].is_linked
              and input_socket.node.inputs[0].links[0].from_socket == source):
            existing_magnitude = input_socket
        else:
            raise ValueError('人物の強度切替が変更されているため、自動更新できません')
    if not switches:
        raise ValueError('人物の復元ノードが見つからないため、自動更新できません')
    graph = Graph(tree)
    graph._index = len(tree.nodes)
    magnitude = existing_magnitude
    if gates and magnitude is None:
        magnitude = graph.math('ABSOLUTE', source)
        magnitude.node.label = '強度の絶対値（人物の変形切替）'
        magnitude.node['gyogan_role'] = 'signed_gate'
    for gate in gates:
        graph.connect(magnitude, gate, 0)
    tree['gyogan_signed_version'] = 1
    tree.update_tag()
    return True
