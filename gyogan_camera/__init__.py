"""Non-destructive camera-relative fisheye; Blender 5.2 is the primary target."""

bl_info = {
    "name": "魚眼カメラ",
    "author": "Gyogan Camera contributors",
    "version": (0, 1, 21),
    "blender": (5, 0, 0),
    "location": "3Dビュー > サイドバー > 魚眼",
    "description": "可逆的なカメラ基準のメッシュ・アーマチュア魚眼変形",
    "category": "Object",
}

import math
import uuid

import bpy
from bpy.app.handlers import persistent
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                       FloatProperty, IntProperty, PointerProperty, StringProperty)
from bpy.types import Operator, Panel, PropertyGroup, UIList

from . import nodes, armature, dolly, cameras, targets, subdivision, presets

OWNER = "gyogan_owner"


def camera_poll(self, obj):
    return obj.type == 'CAMERA' and obj.name in self.id_data.objects


def center_error(scene, obj, pending_mesh=None):
    """Check transform dependencies without reading evaluated mesh geometry."""
    if obj is None:
        return None
    if obj.name not in scene.objects:
        return "魚眼の中心は、このシーンのオブジェクトを指定してください"
    deformed = {t.source for t in scene.gyogan.targets if t.kind == 'MESH' and t.source}
    if pending_mesh is not None:
        deformed.add(pending_mesh)
    # A rig's object origin does not depend on every pose bone. Track pose
    # requests separately so only an explicitly referenced bone and its parents
    # contribute their constraints and drivers.
    visited, pending = set(), [(obj, False, '')]

    def add_dependency(dependency, geometry=False, bone_name=''):
        if isinstance(dependency, bpy.types.Object):
            pending.append((dependency, geometry,
                            bone_name if dependency.type == 'ARMATURE' else ''))

    def add_constraints(constraints):
        for constraint in constraints:
            if constraint.mute:
                continue
            for prop in constraint.bl_rna.properties:
                if prop.type != 'POINTER':
                    continue
                dependency = getattr(constraint, prop.identifier, None)
                if not isinstance(dependency, bpy.types.Object):
                    continue
                subtarget_property = {
                    'pole_target': 'pole_subtarget',
                    'space_object': 'space_subtarget',
                }.get(prop.identifier, 'subtarget')
                subtarget = getattr(constraint, subtarget_property, '')
                geometry = (constraint.type in {'SHRINKWRAP', 'FOLLOW_PATH', 'CLAMP_TO'}
                            or (dependency.type == 'MESH' and bool(subtarget)))
                add_dependency(dependency, geometry, subtarget)
            for target in getattr(constraint, 'targets', ()):
                dependency = getattr(target, 'target', None)
                subtarget = getattr(target, 'subtarget', '')
                add_dependency(dependency,
                               getattr(dependency, 'type', None) == 'MESH' and bool(subtarget),
                               subtarget)

    def add_drivers(owner, bone_paths, bone_prefix):
        animation = getattr(owner, 'animation_data', None)
        if not animation:
            return
        for curve in animation.drivers:
            if (curve.data_path.startswith(bone_prefix)
                    and not any(curve.data_path == path
                                or curve.data_path.startswith(path + '.')
                                or curve.data_path.startswith(path + '[')
                                for path in bone_paths)):
                continue
            for variable in curve.driver.variables:
                for target in variable.targets:
                    dependency = target.id
                    if not isinstance(dependency, bpy.types.Object):
                        continue
                    bone_name = getattr(target, 'bone_target', '')
                    # SINGLE_PROP variables can address a pose-bone property
                    # without filling the TRANSFORMS variable's bone_target.
                    if not bone_name and dependency.type == 'ARMATURE':
                        for bone in dependency.pose.bones:
                            path = bone.path_from_id()
                            if (target.data_path == path
                                    or target.data_path.startswith(path + '.')
                                    or target.data_path.startswith(path + '[')):
                                bone_name = bone.name
                                break
                    add_dependency(dependency, False, bone_name)

    while pending:
        current, needs_geometry, bone_name = pending.pop()
        if current.get(OWNER):
            return "魚眼の生成物に追従する中心は使用できません。元のオブジェクトか独立したエンプティを指定してください"
        if needs_geometry and current in deformed:
            return "魚眼で変形する面や頂点に中心を追従させると循環します。元のボーンか独立したエンプティを使用してください"
        key = (current, needs_geometry, bone_name)
        if key in visited:
            continue
        visited.add(key)
        if current.parent:
            add_dependency(current.parent,
                           current.parent_type in {'VERTEX', 'VERTEX_3'},
                           current.parent_bone if current.parent_type == 'BONE' else '')
        add_constraints(current.constraints)
        bones = []
        if bone_name and current.type == 'ARMATURE':
            bone = current.pose.bones.get(bone_name)
            while bone:
                bones.append(bone)
                bone = bone.parent
        for bone in bones:
            add_constraints(bone.constraints)
        add_drivers(current, [bone.path_from_id() for bone in bones], 'pose.bones[')
        # Data drivers matter to a bone/geometry request, not to an otherwise
        # independent object's origin. Ignore drivers belonging to other bones.
        if bones or needs_geometry:
            add_drivers(current.data, [bone.bone.path_from_id() for bone in bones], 'bones[')
    return None


def center_poll(self, obj):
    return center_error(self.id_data, obj) is None


def active_center_object(settings):
    return settings.center_object if settings.center_mode == 'OBJECT' else None


def sync_center_object(scene):
    tokens = {target.token for target in scene.gyogan.targets}
    if not tokens:
        return
    for tree in bpy.data.node_groups:
        if tree.get(OWNER) in tokens:
            nodes.upgrade_cursor_group(tree, scene)
            nodes.set_center_object(tree, active_center_object(scene.gyogan))
    scene.update_tag()


def update_center(self, context):
    scene = self.id_data
    if presets.busy(scene):
        return
    error = center_error(scene, active_center_object(self))
    if error:
        self.center_object = None
        self.status = error
        return
    sync_center_object(scene)
    targets.request(scene, retry=True)


class GYOGAN_Visibility(PropertyGroup):
    layer_name: StringProperty()
    layer_uid: StringProperty()
    hidden: BoolProperty()


class GYOGAN_Output(PropertyGroup):
    source: PointerProperty(type=bpy.types.Object)
    output: PointerProperty(type=bpy.types.Object)
    hide_render: BoolProperty()
    visibility_owned: BoolProperty(default=False)
    layers: CollectionProperty(type=GYOGAN_Visibility)


class GYOGAN_Modifier(PropertyGroup):
    uid: IntProperty()
    kind: StringProperty()
    enabled: BoolProperty(default=True, options={'HIDDEN'})


class GYOGAN_Target(PropertyGroup):
    source: PointerProperty(type=bpy.types.Object)
    kind: StringProperty()
    token: StringProperty()
    modifiers: CollectionProperty(type=GYOGAN_Modifier)
    outputs: CollectionProperty(type=GYOGAN_Output)
    node_group: PointerProperty(type=bpy.types.GeometryNodeTree)
    output_rig: PointerProperty(type=bpy.types.Object)
    calculator: PointerProperty(type=bpy.types.Object)
    collection: PointerProperty(type=bpy.types.Collection)
    bounds_object: PointerProperty(type=bpy.types.Object)
    active: BoolProperty(default=True)


class GYOGAN_ManualTarget(PropertyGroup):
    source: PointerProperty(type=bpy.types.Object)
    kind: StringProperty()


def find_modifier(obj, record):
    if not obj:
        return None
    return next((m for m in obj.modifiers
                 if m.persistent_uid == record.uid and m.type == record.kind), None)


def set_hidden(obj, hidden, layer):
    if obj and obj.name in layer.objects and obj.hide_get(view_layer=layer) != hidden:
        obj.hide_set(hidden, view_layer=layer)
        return True
    return False


def layer_uid(layer):
    if not layer.get('gyogan_layer_uid'):
        layer['gyogan_layer_uid'] = uuid.uuid4().hex
    return layer['gyogan_layer_uid']


def remember_visibility(record, obj, scene):
    record.source = obj
    record.hide_render = obj.hide_render
    record.layers.clear()
    for layer in scene.view_layers:
        if obj.name in layer.objects:
            state = record.layers.add()
            state.layer_name = layer.name
            state.layer_uid = layer_uid(layer)
            state.hidden = obj.hide_get(view_layer=layer)


def restore_visibility(record, scene):
    if not record.visibility_owned:
        return False
    record.visibility_owned = False
    obj = record.source
    if not obj:
        return False
    changed = obj.hide_render != record.hide_render
    if changed:
        obj.hide_render = record.hide_render
    for state in record.layers:
        layer = next((layer for layer in scene.view_layers
                      if layer.get('gyogan_layer_uid') == state.layer_uid), None)
        if layer:
            changed |= set_hidden(obj, state.hidden, layer)
    return changed


def apply_enabled(scene):
    settings = scene.gyogan
    global_enabled = settings.enabled and cameras.usable(scene, settings.camera)
    changed = False

    def assign(owner, name, value):
        nonlocal changed
        if getattr(owner, name) != value:
            setattr(owner, name, value)
            changed = True

    for target in settings.targets:
        enabled = global_enabled and target.active
        if target.kind == 'MESH':
            for record in target.modifiers:
                modifier = find_modifier(target.source, record)
                if modifier:
                    assign(modifier, 'show_viewport', enabled and record.enabled)
                    assign(modifier, 'show_render', enabled and record.enabled)
        else:
            for record in target.outputs:
                source, output = record.source, record.output
                if enabled and source and output:
                    if not record.visibility_owned:
                        # Inactive originals remain editable by the user. Take
                        # a fresh snapshot only when we hide them again.
                        remember_visibility(record, source, scene)
                        record.visibility_owned = True
                    assign(source, 'hide_render', True)
                    assign(output, 'hide_render', record.hide_render)
                    for layer in scene.view_layers:
                        state = next((s for s in record.layers if s.layer_uid == layer_uid(layer)), None)
                        if state is None and source.name in layer.objects:
                            state = record.layers.add()
                            state.layer_name = layer.name
                            state.layer_uid = layer_uid(layer)
                            state.hidden = source.hide_get(view_layer=layer)
                        changed |= set_hidden(source, True, layer)
                        changed |= set_hidden(output, state.hidden if state else True, layer)
                else:
                    changed |= restore_visibility(record, scene)
                    if output:
                        assign(output, 'hide_render', True)
                        for layer in scene.view_layers:
                            changed |= set_hidden(output, True, layer)
            if target.output_rig:
                assign(target.output_rig, 'hide_render', True)
                for layer in scene.view_layers:
                    changed |= set_hidden(target.output_rig, not enabled, layer)
                for bone in target.output_rig.pose.bones:
                    for constraint in bone.constraints:
                        if (constraint.type == 'GEOMETRY_ATTRIBUTE'
                                and constraint.target == target.calculator):
                            assign(constraint, 'mute', not enabled)
            if target.calculator:
                for modifier in target.calculator.modifiers:
                    if modifier.type == 'NODES' and modifier.node_group == target.node_group:
                        assign(modifier, 'show_viewport', enabled)
                        assign(modifier, 'show_render', enabled)
    if changed:
        scene.update_tag()


def update_enabled(self, context):
    scene = self.id_data
    if isinstance(scene, bpy.types.Scene):
        apply_enabled(scene)
        targets.request(scene)


class GYOGAN_Settings(PropertyGroup):
    schema: IntProperty(default=1)
    keep_bounds_helpers: BoolProperty(
        name='「魚眼・範囲判定」の補助オブジェクトを残す', default=True, options=set(),
        update=targets.update_keep_helpers,
        description='チェックを外すと、魚眼プレビューがOFFのときに範囲判定用の補助オブジェクトを削除します。次のONで必要に応じて再生成します')
    helper_collection: PointerProperty(type=bpy.types.Collection, options={'HIDDEN'})
    helper_collection_id: StringProperty(options={'HIDDEN'})
    scope_schema: IntProperty(default=0)
    render_lock_owned: BoolProperty(default=False, options={'HIDDEN'})
    render_lock_original: BoolProperty(default=False, options={'HIDDEN'})
    effect_scope: EnumProperty(name="適用範囲", items=[
        ('CAMERA', "カメラ範囲（自動）", "基準カメラの魚眼画面に入る対象へ自動で適用"),
        ('MANUAL', "選択オブジェクト", "選択オブジェクト・コレクションから登録した対象へ適用"),
        ('SCENE', "シーン全体（自動）", "登録操作なしでシーン内の対応オブジェクトへ適用")],
        default='MANUAL', update=targets.update_scope,
        description="選んだ範囲だけに変形を適用します。手動登録は切り替えても保持します")
    auto_status: StringProperty(options={'SKIP_SAVE'})
    camera: PointerProperty(name="基準カメラ", type=bpy.types.Object, poll=camera_poll,
                            update=cameras.update_reference,
                            description="シーンのアクティブカメラと連動します。登録済みの対象にも反映されます")
    camera_error: StringProperty(options={'SKIP_SAVE'})
    center_mode: EnumProperty(name="魚眼の中心", items=[
        ('DEFAULT', "標準の中心", "中心を移動せず、従来の魚眼を使用します"),
        ('OBJECT', "オブジェクト", "指定オブジェクトの原点を中心にします。未指定なら標準の中心"),
        ('CURSOR', "3Dカーソル", "3Dカーソルをカメラから見た位置が中心になり、カーソルの移動に追従します")],
        default='OBJECT', options=set(), update=update_center,
        description="魚眼の中心に使う位置を選びます。切り替えても指定したオブジェクトは保持します")
    center_object: PointerProperty(name="魚眼の中心", type=bpy.types.Object,
                                   poll=center_poll, update=update_center,
                                   description="オブジェクトの原点をカメラから見た位置が歪みの中心になります。未指定なら従来の中心。エンプティや元リグのボーンに追従するエンプティを指定できます")
    center_exists: BoolProperty(get=lambda self: self.center_mode == 'OBJECT' and self.center_object is not None,
                               options={'HIDDEN'})
    center_use_cursor: BoolProperty(get=lambda self: self.center_mode == 'CURSOR',
                                   options={'HIDDEN'})
    center_use_object: BoolProperty(get=lambda self: self.center_mode == 'OBJECT',
                               options={'HIDDEN'})
    mode: EnumProperty(name="モード", items=[
        ('MESH', "メッシュ", "頂点を直接、非破壊で魚眼変形"),
        ('ARMATURE', "アーマチュア", "別の出力リグを伸縮しウェイト付きメッシュを変形")],
        update=targets.update_scope)
    scope: EnumProperty(name="登録元", items=[
        ('SELECTED', "選択オブジェクト", "現在の選択を登録し、選択解除後も維持"),
        ('COLLECTION', "コレクション", "指定コレクションと子コレクションから登録"),
        ('SCENE', "シーン全体", "シーン内の対応オブジェクトを登録"),
        ('CAMERA', "カメラ範囲", "魚眼の前方範囲から余白を含めて登録。登録後は固定")])
    target_collection: PointerProperty(name="対象コレクション", type=bpy.types.Collection)
    strength: FloatProperty(name="魚眼の強度", default=1.0, min=-5.0, max=5.0,
                            soft_min=-5.0, soft_max=5.0,
                            description="0は元の形状、プラスは魚眼、マイナスは周辺を広げる逆方向の歪みです。スライダー・数値入力ともに-5〜5まで設定できます")
    fov: FloatProperty(name="魚眼の水平画角", default=math.radians(140),
                       min=math.radians(10), max=math.radians(175), subtype='ANGLE')
    ellipse_x: FloatProperty(name="横の広がり", default=1.0, min=0.1, max=5.0,
                              description="魚眼の横方向の広がり。1が標準、大きくすると横長、小さくすると横方向に狭い歪みになります。縦横とも1で従来の魚眼")
    ellipse_y: FloatProperty(name="縦の広がり", default=1.0, min=0.1, max=5.0,
                              description="魚眼の縦方向の広がり。1が標準、大きくすると縦長、小さくすると縦方向に狭い歪みになります。カメラから見た縦方向です")
    zoom: FloatProperty(name="画面内サイズ", default=1.0, min=0.05, max=10.0)
    near_distance: FloatProperty(name="近接フェード距離", default=0.1, min=0.0001,
                                 max=1000.0, subtype='DISTANCE',
                                 description="カメラ面付近を元の形状へなめらかに戻す距離。初期値は0.1 Blender単位（単位スケール1のメートル設定なら10cm）")
    thickness: BoolProperty(name="ボーンの太さも変形", default=True)
    enabled: BoolProperty(name="魚眼プレビュー", default=True, update=update_enabled, options=set(),
                         description="押すたびに魚眼と元の形状を切り替えます。表示とレンダーの両方に反映され、登録と設定は保持されます")
    subdivision: EnumProperty(name="追加分割", items=[
        ('NONE', "なし", "十分に細かいメッシュ向け"),
        ('SIMPLE', "形状を保って分割", "角を丸めずに分割"),
        ('CATMULL_CLARK', "なめらかに分割", "分割と同時に角を丸める")], default='SIMPLE',
        update=subdivision.update_settings,
        description="登録済みのメッシュにも自動反映します。元からある分割モディファイアは変更しません")
    viewport_levels: IntProperty(name="表示用分割", default=1, min=0, max=3,
                                 update=subdivision.update_settings)
    render_levels: IntProperty(name="レンダー用分割", default=2, min=0, max=4,
                               update=subdivision.update_settings)
    face_limit: IntProperty(name="追加分割の推定面数上限", default=2000000,
                            min=1000, max=20000000, update=subdivision.update_settings,
                            description="各オブジェクトのベース面数から推定。超える対象は前の分割設定を保持します。既存モディファイアで増える分は別途注意")
    subdivision_pending: BoolProperty(default=False, options={'HIDDEN'})
    subdivision_status: StringProperty(options={'SKIP_SAVE'})
    targets: CollectionProperty(type=GYOGAN_Target)
    manual_targets: CollectionProperty(type=GYOGAN_ManualTarget)
    presets: CollectionProperty(type=presets.GYOGAN_Preset)
    active_preset_id: StringProperty(options={'HIDDEN'})
    preset_index: IntProperty(default=0, min=0, options=set(),
                              update=presets.update_index)
    preset_status: StringProperty(options={'SKIP_SAVE'})
    active_index: IntProperty(default=0)
    status: StringProperty(default="対象を登録すると開始できます")


def record_modifier(target, modifier):
    record = target.modifiers.add()
    record.uid = modifier.persistent_uid
    record.kind = modifier.type


def cleanup_generated(token):
    """Delete only owned IDs, never source mesh/action/shape-key data."""
    for obj in list(bpy.data.objects):
        if obj.get(OWNER) == token:
            bpy.data.objects.remove(obj, do_unlink=True)
    for collection in list(bpy.data.collections):
        if collection.get(OWNER) == token:
            # User-added objects or nested collections must keep their links.
            if collection.objects or collection.children:
                del collection[OWNER]
            else:
                bpy.data.collections.remove(collection)
    # Removing a parent node group may release an owned nested group.
    while True:
        removed = False
        for datablocks in (bpy.data.node_groups, bpy.data.armatures, bpy.data.meshes):
            for data in list(datablocks):
                if data.get(OWNER) == token and data.users == 0:
                    datablocks.remove(data)
                    removed = True
        if not removed:
            break


def migrate_targets(scene):
    """Existing registrations remain manual; never enable automation on load."""
    settings = scene.gyogan
    if settings.scope_schema >= 1:
        return
    for target in settings.targets:
        if target.source:
            remember_manual(scene, target.source, target.kind)
        if target.kind == 'ARMATURE' and settings.enabled:
            for record in target.outputs:
                record.visibility_owned = True
    if settings.scope not in {'SELECTED', 'COLLECTION'}:
        settings.scope = 'SELECTED'
    settings.scope_schema = 1


def remember_manual(scene, source, kind):
    if not any(item.source == source and item.kind == kind
               for item in scene.gyogan.manual_targets):
        item = scene.gyogan.manual_targets.add()
        item.source, item.kind = source, kind


def remove_target(scene, index, *, forget=True):
    target = scene.gyogan.targets[index]
    if forget:
        for i in reversed(range(len(scene.gyogan.manual_targets))):
            item = scene.gyogan.manual_targets[i]
            if item.source == target.source and item.kind == target.kind:
                scene.gyogan.manual_targets.remove(i)
    token = target.token
    for record in target.outputs:
        restore_visibility(record, scene)
    if target.source:
        for record in target.modifiers:
            modifier = find_modifier(target.source, record)
            if modifier:
                target.source.modifiers.remove(modifier)
    # Drop the RNA pointer before removing tagged datablocks to release its user.
    target.node_group = None
    target.output_rig = None
    target.calculator = None
    target.collection = None
    target.bounds_object = None
    for record in target.outputs:
        record.output = None
    cleanup_generated(token)
    targets.cleanup_helper_collection(scene)
    scene.gyogan.targets.remove(index)
    if not scene.gyogan.targets:
        for layer in scene.view_layers:
            if 'gyogan_layer_uid' in layer:
                del layer['gyogan_layer_uid']
    scene.gyogan.active_index = max(0, min(scene.gyogan.active_index,
                                         len(scene.gyogan.targets) - 1))


def validate_mesh(obj):
    if obj.type != 'MESH':
        raise ValueError("メッシュではありません")
    if obj.library or obj.override_library or obj.data.library:
        raise ValueError("リンク／ライブラリオーバーライドは初期版の対象外です")
    if len(obj.users_scene) > 1:
        raise ValueError("複数シーンで共有するオブジェクトは初期版の対象外です")
    if not obj.data.polygons:
        raise ValueError("面を持つメッシュが必要です")
    if abs(obj.matrix_world.determinant()) < 1e-12:
        raise ValueError("スケールがゼロのオブジェクトは対象外です")


def setup_mesh(scene, obj, *, manual=True):
    migrate_targets(scene)
    existing = next((t for t in scene.gyogan.targets
                     if t.source == obj and t.kind == 'MESH'), None)
    if existing:
        if manual:
            remember_manual(scene, obj, 'MESH')
        return existing
    validate_mesh(obj)
    if manual:
        clear_cached_conflicts(scene, obj, 'MESH')
    settings = scene.gyogan
    error = center_error(scene, active_center_object(settings), pending_mesh=obj)
    if error:
        raise ValueError(error)
    target = settings.targets.add()
    target.source, target.kind, target.token = obj, 'MESH', uuid.uuid4().hex
    index = len(settings.targets) - 1
    try:
        group = nodes.build_mesh_group("魚眼・" + obj.name, scene)
        group[OWNER] = target.token
        target.node_group = group
        modifier = obj.modifiers.new("魚眼・変形", 'NODES')
        record_modifier(target, modifier)
        modifier.node_group = group
        modifier.show_in_editmode = False
        subdivision.apply_to_target(scene, target)
        apply_enabled(scene)
        if manual:
            remember_manual(scene, obj, 'MESH')
        return target
    except Exception:
        # A failed automatic rebuild must preserve an older manual choice.
        remove_target(scene, index, forget=False)
        raise


def setup_armature(scene, rig, *, manual=True):
    migrate_targets(scene)
    existing = next((t for t in scene.gyogan.targets
                     if t.source == rig and t.kind == 'ARMATURE'), None)
    if existing:
        if manual:
            remember_manual(scene, rig, 'ARMATURE')
        return existing
    meshes = armature.validate_rig(rig, scene)
    if manual:
        clear_cached_conflicts(scene, rig, 'ARMATURE', meshes)
    error = center_error(scene, active_center_object(scene.gyogan))
    if error:
        raise ValueError(error)
    # Already deformed sources cannot be reused through a second mode.
    used = {t.source for t in scene.gyogan.targets}
    if any(obj in used for obj in meshes):
        raise ValueError("このリグのメッシュが既に登録されています。先に取り外してください")
    target = scene.gyogan.targets.add()
    target.source, target.kind, target.token = rig, 'ARMATURE', uuid.uuid4().hex
    index = len(scene.gyogan.targets) - 1
    try:
        collection = bpy.data.collections.new("魚眼・" + rig.name)
        collection[OWNER] = target.token
        targets.helper_collection(scene).children.link(collection)
        target.collection = collection
        result = armature.create_rig(rig, meshes, scene, collection, target.token)
        target.output_rig = result['output_rig']
        target.calculator = result['calculator']
        target.node_group = result['node_group']
        for source, output in result['outputs']:
            record = target.outputs.add()
            remember_visibility(record, source, scene)
            record.output = output
        apply_enabled(scene)
        if manual:
            remember_manual(scene, rig, 'ARMATURE')
        return target
    except Exception:
        remove_target(scene, index, forget=False)
        raise


def clear_cached_conflicts(scene, obj, kind, meshes=()):
    """A preserved manual registration may reuse an inactive automatic cache."""
    for index in reversed(range(len(scene.gyogan.targets))):
        target = scene.gyogan.targets[index]
        conflict = (kind == 'MESH' and target.kind == 'ARMATURE'
                    and any(item.source == obj for item in target.outputs))
        conflict |= (kind == 'ARMATURE' and target.kind == 'MESH'
                     and target.source in meshes)
        if not conflict:
            continue
        if any(item.source == target.source and item.kind == target.kind
               for item in scene.gyogan.manual_targets):
            raise ValueError('同じ人物が別のモードで手動登録されています。先にその登録を取り外してください')
        remove_target(scene, index, forget=False)


def within_camera(obj, scene):
    """Conservative angular bounding-sphere registration, no visibility culling."""
    from mathutils import Vector
    camera = scene.gyogan.camera
    inverse = camera.matrix_world.normalized().inverted_safe()
    corners = [inverse @ (obj.matrix_world @ Vector(p)) for p in obj.bound_box]
    center = sum(corners, Vector()) / 8
    radius = max((p-center).length for p in corners)
    distance = center.length
    if distance <= radius or distance < 1e-8:
        return True
    angular_radius = math.asin(min(1.0, radius / distance))
    theta = math.atan2(math.hypot(center.x, center.y), -center.z)
    if (scene.gyogan.strength < 0 or scene.gyogan.ellipse_x != 1.0
            or scene.gyogan.ellipse_y != 1.0):
        # Signed and elliptical distortion have different frame boundaries.
        # Include forward candidates conservatively when registering.
        return theta - angular_radius <= math.pi / 2
    anchor = active_center_object(scene.gyogan)
    if anchor or scene.gyogan.center_mode == 'CURSOR':
        anchor_point = inverse @ (scene.cursor.location if scene.gyogan.center_mode == 'CURSOR'
                                  else anchor.matrix_world.translation)
        if -anchor_point.z > max(scene.gyogan.near_distance, nodes.EPSILON):
            # Moving the image center changes which peripheral points can enter
            # the frame. Keep the forward hemisphere as conservative candidates.
            return theta - angular_radius <= math.pi / 2
    frame = camera.data.view_frame(scene=scene)
    aspect = max(p.y for p in frame) - min(p.y for p in frame)
    width = max(p.x for p in frame) - min(p.x for p in frame)
    diagonal = math.sqrt(1 + (aspect / max(width, 1e-8)) ** 2)
    effective_zoom = scene.gyogan.zoom * scene.gyogan_dolly.factor
    half_angle = scene.gyogan.fov * .5 * diagonal / max(effective_zoom, 1.0e-8)
    angular_strength = max(scene.gyogan.strength, 1.0)
    if angular_strength > 1.0:
        # Invert atan(q*tan(theta))/q: stronger compression can bring objects
        # outside the standard fisheye angle into the camera's diagonal frame.
        compressed_limit = angular_strength * half_angle
        half_angle = (math.pi / 2 if compressed_limit >= math.pi / 2
                      else math.atan(math.tan(compressed_limit) / angular_strength))
    return theta - angular_radius <= min(math.pi / 2, half_angle + math.radians(5))


def collect_candidates(context):
    scene, settings = context.scene, context.scene.gyogan
    if settings.scope == 'SELECTED':
        candidates = list(context.selected_objects)
    elif settings.scope == 'COLLECTION':
        if not settings.target_collection:
            raise ValueError("対象コレクションを指定してください")
        candidates = list(settings.target_collection.all_objects)
    else:
        candidates = list(scene.objects)
    candidates = [o for o in candidates if not o.get(OWNER) and o.name in scene.objects]
    if settings.mode == 'ARMATURE':
        rigs = set(o for o in candidates if o.type == 'ARMATURE')
        for obj in candidates:
            if obj.type == 'MESH':
                rigs.update(m.object for m in obj.modifiers if m.type == 'ARMATURE' and m.object)
        candidates = list(rigs)
    else:
        candidates = [o for o in candidates if o.type == 'MESH']
    if settings.scope == 'CAMERA':
        if settings.mode == 'ARMATURE':
            candidates = [r for r in candidates if any(
                o.type == 'MESH' and any(m.type == 'ARMATURE' and m.object == r for m in o.modifiers)
                and within_camera(o, scene) for o in scene.objects if not o.get(OWNER))]
        else:
            candidates = [o for o in candidates if within_camera(o, scene)]
    used = {t.source for t in settings.manual_targets}
    manual_rigs = {t.source for t in settings.manual_targets if t.kind == 'ARMATURE'}
    used.update(o for o in scene.objects if o.type == 'MESH' and any(
        m.type == 'ARMATURE' and m.object in manual_rigs for m in o.modifiers))
    return sorted((o for o in candidates if o not in used), key=lambda o: o.name)


class GYOGAN_OT_add(Operator):
    bl_idname = "gyogan.add_targets"
    bl_label = "対象を登録 / 追加"
    bl_description = "設定した対象を非破壊で登録。既存のデータやアニメーションは保持します"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.scene.gyogan.effect_scope == 'MANUAL'

    def execute(self, context):
        scene, settings = context.scene, context.scene.gyogan
        if not settings.camera:
            settings.camera = scene.camera
        if not settings.camera or settings.camera.data.type != 'PERSP':
            self.report({'ERROR'}, "基準カメラには透視投影のカメラを指定してください")
            return {'CANCELLED'}
        if settings.camera.name not in scene.objects:
            self.report({'ERROR'}, "基準カメラはこのシーンに必要です")
            return {'CANCELLED'}
        try:
            candidates = collect_candidates(context)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        count, errors = 0, []
        for obj in candidates:
            try:
                if settings.mode == 'MESH':
                    setup_mesh(scene, obj)
                else:
                    setup_armature(scene, obj)
                count += 1
            except Exception as exc:
                import traceback
                traceback.print_exc()
                message = str(exc) if isinstance(exc, ValueError) else (
                    "内部エラーが発生しました。詳細はシステムコンソールを確認してください")
                errors.append(f"{obj.name}: {message}")
        settings.status = f"{count} 件を追加" + (" / " + " / ".join(errors) if errors else "")
        if not candidates:
            settings.status = "未登録の対応オブジェクトがありません"
        targets.reconcile(scene)
        self.report({'WARNING'} if errors or not candidates else {'INFO'}, settings.status)
        # A partial success remains undoable; every failed target was rolled back.
        return {'FINISHED'} if count else {'CANCELLED'}


class GYOGAN_OT_remove(Operator):
    bl_idname = "gyogan.remove_target"
    bl_label = "選択行を取り外す"
    bl_description = "この登録の生成物だけを取り除き、元の表示へ戻します"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.gyogan
        if settings.effect_scope != 'MANUAL':
            return {'CANCELLED'}
        if settings.manual_targets:
            index = min(settings.active_index, len(settings.manual_targets) - 1)
            item = settings.manual_targets[index]
            source, kind = item.source, item.kind
            settings.manual_targets.remove(index)
            for i in reversed(range(len(settings.targets))):
                target = settings.targets[i]
                if target.source == source and target.kind == kind:
                    remove_target(context.scene, i, forget=False)
            settings.active_index = max(0, min(index, len(settings.manual_targets) - 1))
        settings.status = "登録を取り外しました"
        return {'FINISHED'}


class GYOGAN_OT_toggle_preview(Operator):
    bl_idname = "gyogan.toggle_preview"
    bl_label = "魚眼プレビュー"
    bl_description = "押すたびに魚眼と元の形状を切り替えます。表示とレンダーの両方に反映され、登録と設定は保持されます"
    bl_options = {'UNDO', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return (context.scene is not None and context.scene.is_editable
                and context.scene.as_pointer() not in targets._rendering)

    def execute(self, context):
        scene = context.scene
        scene.gyogan.enabled = not scene.gyogan.enabled
        # Include helpers/temporary rig mode switches in this same Undo step.
        # Prepare offscreen candidates too, before later camera movement.
        targets.reconcile(scene, preflight=True)
        return {'FINISHED'}


class GYOGAN_OT_remove_all(Operator):
    bl_idname = "gyogan.remove_all"
    bl_label = "すべて取り外して元に戻す"
    bl_description = ("現在の手動登録と生成した仕組みを取り外します。"
                      "他のプリセット・元メッシュ・リグ・アニメーションは保持されます")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        context.scene.gyogan.enabled = False
        while context.scene.gyogan.targets:
            remove_target(context.scene, len(context.scene.gyogan.targets)-1)
        context.scene.gyogan.manual_targets.clear()
        presets.save_active(context.scene)
        context.scene.gyogan.auto_status = ''
        context.scene.gyogan.status = "現在の登録と生成物を取り外しました。他のプリセットは保持しています"
        return {'FINISHED'}


class GYOGAN_OT_reset_ellipse(Operator):
    bl_idname = "gyogan.reset_ellipse"
    bl_label = "縦横を標準に戻す"
    bl_description = "横の広がりと縦の広がりを両方1に戻します。強度や中心などの設定は保持します"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        context.scene.gyogan.ellipse_x = 1.0
        context.scene.gyogan.ellipse_y = 1.0
        return {'FINISHED'}


class GYOGAN_OT_reset_defaults(Operator):
    bl_idname = "gyogan.reset_defaults"
    bl_label = "調整値をすべて初期値に戻す"
    bl_description = ("魚眼・登録済みメッシュの追加分割・ドリー倍率を初期値に戻し、魚眼の中心指定を解除します。"
                      "登録対象・オン／オフ状態・アニメーション付きの設定は保持します。"
                      "ドリー中は倍率1の位置と焦点距離へ戻ります。Ctrl+Zで取り消せます")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene is not None and context.scene.is_editable

    def execute(self, context):
        scene = context.scene
        fields = (
            (scene.gyogan, 'gyogan.', (
                'strength', 'fov', 'zoom', 'ellipse_x', 'ellipse_y',
                'near_distance', 'thickness', 'center_object', 'center_mode', 'subdivision',
                'viewport_levels', 'render_levels', 'face_limit')),
            (scene.gyogan_dolly, 'gyogan_dolly.', ('ratio', 'compensate_fisheye')),
        )
        skipped = []
        for settings, prefix, names in fields:
            for name in names:
                prop = settings.bl_rna.properties[name]
                if dolly._has_animation(scene, (prefix + name,)):
                    skipped.append(prop.name)
                    continue
                setattr(settings, name, None if prop.type == 'POINTER' else prop.default)
        scene.update_tag()
        scene.gyogan.status = '調整値を初期値に戻しました。追加分割は登録済みメッシュにも自動反映します'
        if skipped:
            scene.gyogan.status += '。アニメーション付きの設定は保持：' + '、'.join(skipped)
        self.report({'INFO'}, scene.gyogan.status)
        return {'FINISHED'}


class GYOGAN_UL_targets(UIList):
    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        flags = [self.bitflag_filter_item if propname == 'manual_targets' or item.active else 0
                 for item in items]
        return flags, []

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.source.name if item.source else "元オブジェクトがありません",
                     icon='ARMATURE_DATA' if item.kind == 'ARMATURE' else 'MESH_DATA')


class GYOGAN_PT_main(Panel):
    bl_label = bl_info["name"] + " v" + ".".join(map(str, bl_info["version"]))
    bl_idname = "GYOGAN_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "魚眼"

    def draw(self, context):
        layout, settings = self.layout, context.scene.gyogan
        layout.use_property_split = True
        preview = layout.column()
        preview.use_property_split = False
        preview.use_property_decorate = False
        preview.scale_y = 1.7
        preview.operator("gyogan.toggle_preview", depress=settings.enabled,
                     text="プレビュー：オン（魚眼）" if settings.enabled else "プレビュー：オフ（元の形状）",
                     icon='HIDE_OFF' if settings.enabled else 'HIDE_ON')
        row = layout.row()
        row.prop(context.scene, "camera", text="基準カメラ")
        cameras.draw_warning(layout, context.scene)
        camera = context.scene.camera
        if camera and camera.type == 'CAMERA':
            lens = layout.row()
            legacy_dolly = getattr(context.scene, 'mpm_dolly_props', None)
            lens.enabled = (camera.data.type == 'PERSP' and not context.scene.gyogan_dolly.active
                            and not (legacy_dolly and legacy_dolly.enabled))
            lens.prop(camera.data, "lens", text="焦点距離 (mm)")
            if camera.data.type != 'PERSP':
                layout.label(text="焦点距離は透視投影カメラで調整", icon='INFO')
        else:
            layout.label(text="焦点距離：カメラを指定してください", icon='INFO')
        dolly.draw(layout, context)
        layout.prop(settings, "center_mode")
        if settings.center_mode == 'OBJECT':
            layout.prop(settings, "center_object", text="中心オブジェクト")
            if not settings.center_object:
                layout.label(text="中心は未指定：従来の魚眼", icon='PIVOT_CURSOR')
        col = layout.column()
        col.active = settings.enabled
        col.prop(settings, "strength", slider=True)
        if settings.strength < 0.0:
            col.label(text="逆方向の歪み：周辺を広げます", icon='INFO')
        elif settings.strength > 1.0:
            col.label(text="標準より強い魚眼で表示しています", icon='INFO')
        col.prop(settings, "fov")
        col.prop(settings, "zoom")
        ellipse = col.box()
        ellipse.label(text="魚眼の広がり（楕円）")
        ellipse.prop(settings, "ellipse_x", slider=True)
        ellipse.prop(settings, "ellipse_y", slider=True)
        ellipse.operator("gyogan.reset_ellipse", icon='LOOP_BACK')
        col.prop(settings, "near_distance")
        if any(t.kind == 'ARMATURE' for t in settings.targets) or settings.mode == 'ARMATURE':
            col.prop(settings, "thickness")
        layout.operator("gyogan.reset_defaults", icon='LOOP_BACK')
        box = layout.box()
        box.label(text="変形する対象")
        box.prop(settings, "effect_scope")
        box.prop(settings, "mode")
        if settings.effect_scope == 'MANUAL':
            row = box.row(align=True)
            row.use_property_split = False
            row.use_property_decorate = False
            row.prop_enum(settings, 'scope', 'SELECTED', text='選択から追加')
            row.prop_enum(settings, 'scope', 'COLLECTION', text='コレクションから追加')
            if settings.scope == 'COLLECTION':
                box.prop(settings, "target_collection")
        elif settings.effect_scope == 'CAMERA':
            box.label(text="基準カメラに合わせて自動更新", icon='VIEW_CAMERA')
            box.label(text="枠に一部が入る物体を対象にします")
        else:
            box.label(text="対応する物体すべてへ自動適用", icon='SCENE_DATA')
        if settings.mode == 'MESH':
            box.prop(settings, "subdivision")
            if settings.subdivision != 'NONE':
                box.prop(settings, "viewport_levels")
                box.prop(settings, "render_levels")
                box.prop(settings, "face_limit")
        else:
            box.label(text="通常ボーン・頂点ウェイトに対応", icon='INFO')
        presets.draw(box, context)
        if settings.effect_scope == 'MANUAL':
            box.operator("gyogan.add_targets", icon='ADD')
            layout.template_list("GYOGAN_UL_targets", "", settings, "manual_targets", settings, "active_index", rows=3)
            row = layout.row()
            row.enabled = bool(settings.manual_targets)
            row.operator("gyogan.remove_target", icon='REMOVE')
            layout.label(text=f"手動登録: {len(settings.manual_targets)} 件")
        else:
            layout.template_list("GYOGAN_UL_targets", "", settings, "targets", settings, "active_index", rows=3)
            layout.label(text=f"現在の対象: {sum(t.active for t in settings.targets)} 件")
            layout.label(text=f"保持している手動登録: {len(settings.manual_targets)} 件")
        row = layout.row()
        row.enabled = bool(settings.targets or settings.manual_targets)
        row.operator("gyogan.remove_all", icon='LOOP_BACK')
        options = layout.box()
        options.use_property_split = False
        options.use_property_decorate = False
        options.label(text='設定', icon='PREFERENCES')
        options.prop(settings, 'keep_bounds_helpers')
        options.label(text='※OFFにするとON時の待ち時間が増えます')
        # Reports carry full errors; panel gives readable wrapped status.
        import textwrap
        messages = [settings.auto_status, settings.subdivision_status]
        if settings.effect_scope == 'MANUAL':
            messages.append(settings.status)
        for status in dict.fromkeys(message for message in messages if message):
            for line in textwrap.wrap(status, width=26):
                layout.label(text=line)
        layout.label(text="プレビューをオフで元の形状に戻ります", icon='INFO')


def upgrade_saved_warps(scene):
    error = center_error(scene, active_center_object(scene.gyogan))
    if error:
        scene.gyogan.center_object = None
        scene.gyogan.status = error
    tokens = {target.token for target in scene.gyogan.targets}
    for tree in bpy.data.node_groups:
        if tree.get(OWNER) in tokens:
            try:
                nodes.upgrade_strength_group(tree)
                nodes.upgrade_center_group(tree, scene)
                nodes.upgrade_signed_group(tree, scene)
                nodes.upgrade_ellipse_group(tree, scene)
                nodes.upgrade_dolly_group(tree, scene)
                nodes.upgrade_cursor_group(tree, scene)
                armature.upgrade_signed_group(tree)
                nodes.set_center_object(tree, active_center_object(scene.gyogan))
            except ValueError as exc:
                # A hand-edited generated graph must not break add-on loading.
                scene.gyogan.status = f"{tree.name}: {exc}。取り外して登録し直してください"
                print(scene.gyogan.status)


@persistent
def load_post(_):
    if _registration_pending:
        _cancel_registration_timer()
        _finish_registration()
    for scene in bpy.data.scenes:
        if hasattr(scene, 'gyogan'):
            migrate_targets(scene)
            presets.ensure(scene)
            dolly.upgrade_saved(scene)
            upgrade_saved_warps(scene)
            cameras.synchronize(scene)
            apply_enabled(scene)
            targets.request(scene)


CLASSES = presets.CLASSES + (GYOGAN_Visibility, GYOGAN_Output, GYOGAN_Modifier, GYOGAN_Target, GYOGAN_ManualTarget,
           GYOGAN_Settings, GYOGAN_OT_add, GYOGAN_OT_remove,
           GYOGAN_OT_toggle_preview, GYOGAN_OT_remove_all,
           GYOGAN_OT_reset_ellipse, GYOGAN_OT_reset_defaults,
           GYOGAN_UL_targets, GYOGAN_PT_main)


_registration_pending = False
_registered_classes = []
_registered_modules = []


def _cancel_registration_timer():
    if bpy.app.timers.is_registered(_finish_registration):
        bpy.app.timers.unregister(_finish_registration)


def _finish_registration():
    """Defer data access blocked by Preferences / extension registration.

    Direct imports in normal Blender contexts still initialize immediately.
    The callback owns no Blender ID references, so file loading while pending
    cannot leave it holding a previous file's scene or camera.
    """
    global _registration_pending
    if not _registration_pending:
        return None
    if not hasattr(bpy.data, 'scenes'):
        return .1
    _registration_pending = False
    dolly.initialize_scenes()
    cameras._load_post()
    for scene in bpy.data.scenes:
        migrate_targets(scene)
        presets.ensure(scene)
        upgrade_saved_warps(scene)
        cameras.synchronize(scene)
        targets.request(scene)
    return None


def register(*, register_panel=True):
    global _registration_pending
    try:
        for cls in CLASSES:
            if cls is GYOGAN_PT_main and not register_panel:
                continue
            bpy.utils.register_class(cls)
            _registered_classes.append(cls)
        bpy.types.Scene.gyogan = PointerProperty(type=GYOGAN_Settings)
        for module in (dolly, cameras, targets, presets):
            _registered_modules.append(module)
            if module is presets:
                module.register()
            else:
                module.register(initialize=False)
        if load_post not in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.append(load_post)
        _registration_pending = True
        if _finish_registration() is not None:
            bpy.app.timers.register(_finish_registration, first_interval=0., persistent=True)
    except Exception:
        _unregister(save_presets=False)
        raise


def _unregister(*, save_presets=True):
    global _registration_pending
    _cancel_registration_timer()
    _registration_pending = False
    # Disabling the add-on must never leave originals hidden.
    scenes = tuple(getattr(bpy.data, 'scenes', ()))
    for scene in scenes:
        if save_presets and hasattr(scene, 'gyogan'):
            presets.save_active(scene)
    for module in reversed(_registered_modules):
        module.unregister()
    _registered_modules.clear()
    for scene in scenes:
        if hasattr(scene, 'gyogan'):
            scene.gyogan.enabled = False
    if load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(load_post)
    if hasattr(bpy.types.Scene, 'gyogan'):
        del bpy.types.Scene.gyogan
    for cls in reversed(_registered_classes):
        bpy.utils.unregister_class(cls)
    _registered_classes.clear()


def unregister():
    _unregister()
