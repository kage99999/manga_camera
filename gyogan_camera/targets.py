"""Automatic effect membership, kept separate from persistent manual choices.

Dependency-graph notifications only queue work. Expensive structural changes are
performed from the main-thread timer or an explicit operator, never from draw.
Animation rendering prepares all possible sources before the first frame, then
changes only the activation of those already prepared targets.
"""

import bpy
import uuid
from bpy.app.handlers import persistent
from mathutils import Vector

from . import framing

OWNER = 'gyogan_owner'
BOUNDS_ROLE = 'camera_bounds'
EVALUATOR_ROLE = 'camera_bounds_evaluator'
_dirty = set()
_busy = set()
_failures = {}
_proxy_states = {}
_cursor_states = {}
_rendering = set()
_registered = False
_suspended = False


def _key(scene):
    return scene.as_pointer()


def _unused(scene):
    settings = scene.gyogan
    return (settings.effect_scope == 'MANUAL' and not settings.manual_targets
            and not settings.targets and not settings.subdivision_pending)


def helper_collection(scene):
    """Use a persistent scene reference, never adopt a collection by its name."""
    settings = scene.gyogan
    collection = settings.helper_collection
    if (not collection or not settings.helper_collection_id
            or collection.get('gyogan_helpers_id') != settings.helper_collection_id):
        collection = bpy.data.collections.new('魚眼用補助オブジェクト')
        settings.helper_collection_id = uuid.uuid4().hex
        collection['gyogan_helpers_id'] = settings.helper_collection_id
        settings.helper_collection = collection
    if collection.name not in scene.collection.children:
        scene.collection.children.link(collection)
    return collection


def organize_helpers(scene):
    """Nest owned helper collections, retaining any user-added links/content."""
    changed = False
    for target in scene.gyogan.targets:
        collection = target.collection
        if not collection or collection.get(OWNER) != target.token:
            continue
        root = helper_collection(scene)
        if collection == root:
            continue
        moved = False
        if collection.name not in root.children:
            root.children.link(collection)
            moved = True
        if collection.name in scene.collection.children:
            scene.collection.children.unlink(collection)
            moved = True
        changed |= moved
        # Moving collections can reset per-layer hide_set on the bounds copy.
        proxy = target.bounds_object
        if proxy and proxy.get(OWNER) == target.token:
            for layer in scene.view_layers:
                if proxy.name in layer.objects and not proxy.hide_get(view_layer=layer):
                    proxy.hide_set(True, view_layer=layer)
    return changed


def cleanup_helper_collection(scene):
    settings = scene.gyogan
    root = settings.helper_collection
    if (root and root.get('gyogan_helpers_id') == settings.helper_collection_id
            and not root.objects and not root.children):
        settings.helper_collection = None
        settings.helper_collection_id = ''
        bpy.data.collections.remove(root)


def update_keep_helpers(settings, _context):
    # Deletion runs on the main-thread timer, outside RNA/depsgraph callbacks.
    request(settings.id_data)


def discard_bounds_helpers(scene):
    """Keep target descriptors, deformation setup and all original data intact."""
    for target in scene.gyogan.targets:
        remove_bounds_proxy(target)
        # Skin evaluators have no bounds_object pointer; locate by owner + role.
        for helper in list(bpy.data.objects):
            if (helper.get(OWNER) == target.token
                    and helper.get('gyogan_role') == EVALUATOR_ROLE):
                _remove_evaluator_object(helper, target.token)
        collection = target.collection
        if (collection and collection.get(OWNER) == target.token
                and not collection.objects and not collection.children):
            target.collection = None
            bpy.data.collections.remove(collection)
    cleanup_helper_collection(scene)


def sync_render_lock(scene, *, release=False):
    """Own render UI locking before Blender starts its render worker.

    render_init is too late: Blender reads this option while starting the job.
    Preserve the user's pre-existing setting and restore only our own change.
    """
    settings = scene.gyogan
    needed = (not release and _registered and (
        (settings.enabled and settings.effect_scope in {'CAMERA', 'SCENE'})
        or settings.subdivision_pending
        or (_key(scene) in _rendering and settings.render_lock_owned)))
    if needed:
        if not settings.render_lock_owned:
            settings.render_lock_original = scene.render.use_lock_interface
            settings.render_lock_owned = True
        if not scene.render.use_lock_interface:
            scene.render.use_lock_interface = True
    elif settings.render_lock_owned:
        if scene.render.use_lock_interface:
            scene.render.use_lock_interface = settings.render_lock_original
        settings.render_lock_owned = False


def request(scene, *, retry=False):
    """Schedule one coalesced update; no Blender ID references are retained."""
    if not isinstance(scene, bpy.types.Scene):
        return
    if hasattr(scene, 'gyogan'):
        sync_render_lock(scene)
    key = _key(scene)
    _dirty.add(key)
    if retry:
        _failures.pop(key, None)


def update_scope(settings, _context):
    from . import presets
    scene = settings.id_data
    if presets.busy(scene):
        return
    request(scene, retry=True)
    # Property callbacks must return before first-time rig construction. Give
    # the panel a chance to display the pending status before the timer runs.
    reconcile(scene, prepare=False)
    request(scene)


def _safe_to_prepare(scene):
    # Rig setup temporarily enters Edit Mode. Never take a user's other object
    # out of Edit/Pose/Sculpt Mode to make automatic preparation possible.
    return (getattr(bpy.context, 'mode', 'OBJECT') == 'OBJECT'
            and all(obj.mode == 'OBJECT' for obj in scene.objects
                    if obj.type in {'MESH', 'ARMATURE'} and not obj.get(OWNER)))


def _identity(value):
    return value.as_pointer() if isinstance(value, bpy.types.ID) else value


def _animated_paths(source):
    animation = source.animation_data
    if not animation:
        return set()
    from .dolly import _action_curves, _strip_curves
    curves = list(animation.drivers)
    curves.extend(_action_curves(animation.action,
                                 getattr(animation, 'action_slot_handle', None)))
    for track in animation.nla_tracks:
        for strip in track.strips:
            curves.extend(_strip_curves(strip))
    return {curve.data_path for curve in curves}


def _animation_state(animation):
    if not animation:
        return None

    def strip_state(strip):
        fields = ('type', 'frame_start', 'frame_end', 'action_frame_start',
                  'action_frame_end', 'scale', 'repeat', 'blend_type',
                  'extrapolation', 'mute', 'use_animated_influence',
                  'use_animated_time', 'use_animated_time_cyclic',
                  'use_reverse', 'use_sync_length', 'blend_in', 'blend_out',
                  'use_auto_blend', 'action_slot_handle')
        return (_identity(getattr(strip, 'action', None)),
                tuple((field, getattr(strip, field)) for field in fields if hasattr(strip, field)),
                tuple(strip_state(child) for child in getattr(strip, 'strips', ())))

    drivers = []
    for curve in animation.drivers:
        driver = curve.driver
        variables = tuple((variable.name, variable.type, tuple(
            (_identity(target.id), target.data_path, target.bone_target,
             target.transform_type, target.transform_space, target.rotation_mode)
            for target in variable.targets)) for variable in driver.variables)
        drivers.append((curve.data_path, curve.array_index, curve.mute,
                        driver.type, driver.expression, driver.use_self, variables))
    return (_identity(animation.action), animation.action_slot_handle,
            animation.use_nla, tuple(drivers),
            tuple((track.mute, track.is_solo, tuple(strip_state(strip) for strip in track.strips))
                  for track in animation.nla_tracks))


def _modifier_state(modifier, animated_paths):
    values = [modifier.type, modifier.persistent_uid]
    for prop in modifier.bl_rna.properties:
        if prop.identifier in {'rna_type', 'name', 'persistent_uid', 'is_active', 'show_expanded'}:
            continue
        if prop.is_readonly:
            continue
        if modifier.path_from_id(prop.identifier) in animated_paths:
            # The copy shares Actions/NLA read-only, and copies drivers. Their
            # evaluated values must not rebuild the copy on every frame.
            continue
        if prop.type not in {'BOOLEAN', 'INT', 'FLOAT', 'STRING', 'ENUM', 'POINTER'}:
            continue
        try:
            value = getattr(modifier, prop.identifier)
            if getattr(prop, 'is_array', False):
                value = tuple(value)
            elif prop.type == 'POINTER':
                value = value.as_pointer() if value else 0
            values.append((prop.identifier, value))
        except (AttributeError, ReferenceError, TypeError):
            continue
    # Geometry Nodes modifier input sockets are ID properties, not RNA fields.
    for name in modifier.keys() if modifier.type == 'NODES' else ():
        if name == '_RNA_UI':
            continue
        path = modifier.path_from_id() + '["' + bpy.utils.escape_identifier(name) + '"]'
        if path in animated_paths:
            continue
        value = modifier[name]
        if isinstance(value, bpy.types.ID):
            value = value.as_pointer()
        elif not isinstance(value, (str, int, float, bool, type(None))):
            try:
                value = tuple(value)
            except TypeError:
                value = repr(value)
        values.append((name, value))
    return tuple(values)


def _source_modifiers(target):
    owned = {(record.uid, record.kind) for record in target.modifiers}
    return [mod for mod in target.source.modifiers
            if (mod.persistent_uid, mod.type) not in owned]


def remove_bounds_proxy(target):
    proxy = target.bounds_object
    if proxy:
        target.bounds_object = None
    _proxy_states.pop(target.token, None)
    if (proxy and proxy.get(OWNER) == target.token
            and proxy.get('gyogan_role') == BOUNDS_ROLE):
        _remove_bounds_evaluator(proxy)
        bpy.data.objects.remove(proxy, do_unlink=True)


def _remove_bounds_evaluator(proxy):
    helper = proxy.get('gyogan_bounds_evaluator')
    if (not isinstance(helper, bpy.types.Object) or helper.get(OWNER) != proxy.get(OWNER)
            or helper.get('gyogan_role') != EVALUATOR_ROLE):
        return
    proxy['gyogan_bounds_evaluator'] = None
    _remove_evaluator_object(helper, proxy.get(OWNER))


def _remove_evaluator_object(helper, token):
    data = helper.data
    groups = [mod.node_group for mod in helper.modifiers if mod.type == 'NODES'
              and mod.node_group and mod.node_group.get(OWNER) == token]
    bpy.data.objects.remove(helper, do_unlink=True)
    if data and data.get(OWNER) == token and data.users == 0:
        bpy.data.meshes.remove(data)
    for group in groups:
        if group.users == 0:
            bpy.data.node_groups.remove(group)


def _make_bounds_evaluator(proxy, collection, token, *, link_proxy=True):
    """Keep the hidden source geometry evaluated in the render dependency graph.

    Render-hidden objects otherwise retain stale bounds when their modifiers
    animate. This object's output is always empty, so it has no pixels, shadows
    or reflection contribution; its input establishes a geometry dependency.
    """
    mesh = helper = group = None
    try:
        mesh = bpy.data.meshes.new('魚眼・範囲判定の評価')
        mesh[OWNER] = token
        helper = bpy.data.objects.new('魚眼・範囲判定の評価', mesh)
        helper[OWNER] = token
        helper['gyogan_role'] = EVALUATOR_ROLE
        helper['gyogan_bounds_source'] = proxy
        if link_proxy:
            proxy['gyogan_bounds_evaluator'] = helper
        collection.objects.link(helper)
        helper.hide_select = True
        group = bpy.data.node_groups.new('魚眼・範囲判定の評価', 'GeometryNodeTree')
        group[OWNER] = token
        modifier = helper.modifiers.new('魚眼・空の評価結果', 'NODES')
        modifier.node_group = group
        group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
        info = group.nodes.new('GeometryNodeObjectInfo')
        info.inputs['Object'].default_value = proxy
        info.inputs['As Instance'].default_value = False
        delete = group.nodes.new('GeometryNodeDeleteGeometry')
        delete.domain = 'POINT'
        delete.inputs['Selection'].default_value = True
        output = group.nodes.new('NodeGroupOutput')
        group.links.new(info.outputs['Geometry'], delete.inputs['Geometry'])
        group.links.new(delete.outputs['Geometry'], output.inputs['Geometry'])
        return helper
    except Exception:
        if link_proxy and 'gyogan_bounds_evaluator' in proxy:
            del proxy['gyogan_bounds_evaluator']
        if helper:
            bpy.data.objects.remove(helper, do_unlink=True)
        if mesh and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        if group and group.users == 0:
            bpy.data.node_groups.remove(group)
        raise


def _ensure_skin_evaluators(target):
    # Active character output hides the original skin. Keep that original skin
    # evaluated too, so animated poses use fresh, pre-fisheye render bounds.
    collection = target.collection
    existing = {obj.get('gyogan_bounds_source') for obj in collection.objects
                if obj.get(OWNER) == target.token and obj.get('gyogan_role') == EVALUATOR_ROLE}
    for record in target.outputs:
        if record.source and record.source not in existing:
            _make_bounds_evaluator(record.source, collection, target.token, link_proxy=False)


def _bounds_signature(source, modifiers):
    animated_paths = _animated_paths(source)
    return (2, source.data.as_pointer(), _animation_state(source.animation_data),
            tuple(sorted(animated_paths)),
            tuple(_modifier_state(mod, animated_paths) for mod in modifiers))


def _restored_bounds_match(source, proxy, signature):
    """Revalidate restored data, not pre-Undo RNA handles or pointer caches."""
    if proxy.type != 'MESH' or proxy.data != source.data:
        return False
    constraints = list(proxy.constraints)
    if (len(constraints) != 1 or constraints[0].type != 'COPY_TRANSFORMS'
            or constraints[0].target != source or proxy.parent is not None
            or constraints[0].owner_space != 'WORLD' or constraints[0].target_space != 'WORLD'
            or constraints[0].mix_mode != 'REPLACE' or constraints[0].mute
            or constraints[0].influence != 1.0):
        return False
    helper = proxy.get('gyogan_bounds_evaluator')
    if (not isinstance(helper, bpy.types.Object)
            or helper.get(OWNER) != proxy.get(OWNER)
            or helper.get('gyogan_role') != EVALUATOR_ROLE
            or helper.get('gyogan_bounds_source') != proxy):
        return False
    if not any(mod.type == 'NODES' and mod.node_group
               and mod.node_group.get(OWNER) == proxy.get(OWNER)
               and any(node.bl_idname == 'GeometryNodeObjectInfo'
                       and node.inputs['Object'].default_value == proxy
                       for node in mod.node_group.nodes)
               for mod in helper.modifiers):
        return False
    return _bounds_signature(proxy, proxy.modifiers) == signature


def ensure_bounds_proxy(scene, target):
    """An invisible, owned copy evaluates the stack before our deformation.

    Mesh and shape-key data and Actions are shared read-only. Only the copied
    object's modifiers/constraints are changed. A world transform constraint
    follows the original, including parents, animation and interactive motion.
    """
    if target.kind != 'MESH' or not target.source:
        return None
    source = target.source
    original_modifiers = _source_modifiers(target)
    signature = _bounds_signature(source, original_modifiers)
    proxy = target.bounds_object
    if (proxy and proxy.get(OWNER) == target.token
            and proxy.get('gyogan_role') == BOUNDS_ROLE):
        if _proxy_states.get(target.token) == signature:
            return proxy
        if _restored_bounds_match(source, proxy, signature):
            # Undo/load invalidates our pointer cache, not necessarily the proxy.
            # Replacing a valid restored ID would mutate the history being read.
            _proxy_states[target.token] = signature
            return proxy
    # Rebuild only when the original stack changes. The source modifier list is
    # never toggled even briefly, avoiding flicker and dependency-graph races.
    replacement = source.copy()
    previous_collection = target.collection
    created_collection = None
    try:
        replacement.name = '魚眼・範囲判定・' + source.name
        replacement[OWNER] = target.token
        replacement['gyogan_role'] = BOUNDS_ROLE
        original_indices = {index for index, mod in enumerate(source.modifiers)
                            if mod in original_modifiers}
        for index in reversed(range(len(replacement.modifiers))):
            if index not in original_indices:
                replacement.modifiers.remove(replacement.modifiers[index])
        for constraint in list(replacement.constraints):
            replacement.constraints.remove(constraint)
        replacement.parent = None
        constraint = replacement.constraints.new('COPY_TRANSFORMS')
        constraint.name = '魚眼・元オブジェクトの位置に追従'
        constraint.target = source
        constraint.owner_space = constraint.target_space = 'WORLD'
        constraint.mix_mode = 'REPLACE'
        replacement.hide_render = True
        replacement.hide_select = True
        replacement.hide_viewport = False
        replacement.display_type = 'BOUNDS'
        collection = target.collection
        if not collection or collection.get(OWNER) != target.token:
            collection = bpy.data.collections.new('魚眼・範囲判定・' + source.name)
            created_collection = collection
            collection[OWNER] = target.token
            helper_collection(scene).children.link(collection)
            target.collection = collection
        collection.objects.link(replacement)
        _make_bounds_evaluator(replacement, collection, target.token)
        for layer in scene.view_layers:
            if replacement.name in layer.objects:
                replacement.hide_set(True, view_layer=layer)
        target.bounds_object = replacement
        _proxy_states[target.token] = signature
    except Exception:
        _remove_bounds_evaluator(replacement)
        bpy.data.objects.remove(replacement, do_unlink=True)
        if created_collection:
            target.collection = previous_collection
            bpy.data.collections.remove(created_collection)
            cleanup_helper_collection(scene)
        raise
    if (proxy and proxy.get(OWNER) == target.token
            and proxy.get('gyogan_role') == BOUNDS_ROLE):
        _remove_bounds_evaluator(proxy)
        bpy.data.objects.remove(proxy, do_unlink=True)
    return replacement


def _skin_meshes(scene, rig):
    return [obj for obj in scene.objects if obj.type == 'MESH' and not obj.get(OWNER)
            and any(mod.type == 'ARMATURE' and mod.object == rig
                    for mod in obj.modifiers)]


def _skin_cache_valid(scene, target):
    return (target.kind != 'ARMATURE'
            or {record.source for record in target.outputs}
            == set(_skin_meshes(scene, target.source)))


def _candidates(scene):
    kind = scene.gyogan.mode
    object_type = 'ARMATURE' if kind == 'ARMATURE' else 'MESH'
    return [(kind, obj) for obj in scene.objects
            if obj.type == object_type and not obj.get(OWNER)]


def _target_key(kind, source):
    return kind, source.as_pointer()


def _visible_source(scene, obj):
    """Read the saved visibility while a generated character hides originals."""
    layer = (bpy.context.view_layer if bpy.context.scene == scene
             else scene.view_layers[0])
    rendering = _key(scene) in _rendering
    for target in scene.gyogan.targets:
        for record in target.outputs:
            if record.source != obj or not record.visibility_owned:
                continue
            if rendering:
                return not record.hide_render
            state = next((state for state in record.layers
                          if state.layer_uid == layer.get('gyogan_layer_uid')
                          or state.layer_name == layer.name), None)
            return (obj.name in layer.objects and not obj.hide_viewport
                    and not (state.hidden if state else True))
    if rendering:
        return not obj.hide_render
    return obj.name in layer.objects and obj.visible_get(view_layer=layer)


def _bounds(obj, depsgraph):
    evaluated = obj.evaluated_get(depsgraph) if depsgraph else obj
    return [evaluated.matrix_world @ Vector(corner) for corner in evaluated.bound_box]


def _in_frame(scene, kind, source, cached, depsgraph):
    # During animation rendering Blender does not copy evaluated transforms or
    # animated settings back to the original IDs. Use the same graph that
    # evaluated the bounds for the camera, center and projection settings too.
    projection_scene = depsgraph.scene_eval if depsgraph else scene
    margin = .04 if cached and cached.active else .02
    if kind == 'ARMATURE':
        # A single warped bone matrix approximates the skin between bones.
        # Keep a wider boundary for that approximation than for mesh vertices.
        margin += .08
        proxies = {target.source: target.bounds_object for target in scene.gyogan.targets
                   if target.kind == 'MESH' and target.bounds_object}
        return any(_visible_source(scene, mesh)
                   and framing.in_camera(projection_scene, mesh,
                                         bounds=_bounds(proxies.get(mesh) or mesh, depsgraph),
                                         margin=margin)
                   for mesh in _skin_meshes(scene, source))
    if not _visible_source(scene, source):
        return False
    bounds_source = cached.bounds_object if cached and cached.bounds_object else source
    return framing.in_camera(projection_scene, source, bounds=_bounds(bounds_source, depsgraph),
                             margin=margin)


def _failure_signature(scene, kind, source):
    settings = scene.gyogan
    data = source.data
    geometry = (len(data.polygons) if kind == 'MESH' else
                tuple((bone.name, bone.use_deform, bone.bbone_segments)
                      for bone in data.bones))
    meshes = [source] if kind == 'MESH' else _skin_meshes(scene, source)
    return (data.as_pointer(), geometry, _identity(source.library),
            bool(source.override_library),
            len(source.users_scene), source.mode,
            abs(source.matrix_world.determinant()) < 1.e-10,
            tuple((obj.as_pointer(), obj.data.as_pointer(),
                   tuple((mod.type, _identity(getattr(mod, 'object', None)),
                          getattr(mod, 'use_vertex_groups', None),
                          getattr(mod, 'use_bone_envelopes', None))
                         for mod in obj.modifiers if mod.type != 'NODES'))
                  for obj in meshes),
            settings.subdivision, settings.viewport_levels, settings.render_levels,
            settings.face_limit, _identity(settings.center_object))


def _conflicts(scene, cached, kind, source):
    if cached.kind == kind and cached.source == source:
        return False
    if kind == 'MESH':
        return cached.kind == 'ARMATURE' and any(
            record.source == source for record in cached.outputs)
    return cached.kind == 'MESH' and cached.source in _skin_meshes(scene, source)


def _depsgraph(scene):
    if bpy.context.scene == scene:
        return bpy.context.evaluated_depsgraph_get()
    with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
        return bpy.context.evaluated_depsgraph_get()


def reconcile(scene, *, prepare=True, depsgraph=None, preflight=False):
    """Reconcile active membership; explicit callers can synchronously prepare.

    ``preflight`` caches every auto candidate for subsequent render frames. It
    does not make offscreen objects active, or alter saved manual descriptors.
    """
    key = _key(scene)
    if _suspended or key in _busy or not hasattr(scene, 'gyogan'):
        return
    _busy.add(key)
    try:
        from . import apply_enabled, setup_mesh, setup_armature, remove_target, subdivision
        settings = scene.gyogan
        _dirty.discard(key)
        if _unused(scene):
            # An unused embedded section must not scan the entire manga scene.
            settings.auto_status = '手動登録：0 件に適用'
            return
        can_prepare = prepare and _safe_to_prepare(scene)
        if key in _rendering and not preflight:
            can_prepare = False
        if can_prepare and organize_helpers(scene):
            apply_enabled(scene)
        # Subdivision settings also apply to inactive cached targets. Flush
        # before the preview/camera early return, but never while evaluating.
        if settings.subdivision_pending:
            if can_prepare:
                subdivision.sync(scene)
            else:
                subdivision.defer(scene, rendering=key in _rendering)
                _dirty.add(key)
        enabled = (settings.enabled and scene.camera and scene.camera.type == 'CAMERA'
                   and scene.camera.data.type == 'PERSP')
        if not enabled:
            changed = any(target.active for target in settings.targets)
            for target in settings.targets:
                target.active = False
            if changed:
                apply_enabled(scene)
            if not settings.enabled and not settings.keep_bounds_helpers:
                # Never delete IDs from frame/depsgraph/Undo evaluation or render.
                if can_prepare and key not in _rendering:
                    discard_bounds_helpers(scene)
                else:
                    _dirty.add(key)
            settings.auto_status = ('プレビューはオフです。自動追加も停止しています'
                                    if not settings.enabled else '基準カメラを指定してください')
            return
        if can_prepare:
            for index in reversed(range(len(settings.targets))):
                target = settings.targets[index]
                if (not target.source or target.source.name not in scene.objects
                        or not _skin_cache_valid(scene, target)):
                    remove_target(scene, index, forget=False)
        # During evaluation we may only stop an obsolete output, not rebuild
        # its rig. This also hides orphan copies immediately after skin removal.
        invalid = {_target_key(target.kind, target.source) for target in settings.targets
                   if target.source and not _skin_cache_valid(scene, target)}
        removed_source_active = False
        for target in settings.targets:
            if not target.source and target.active:
                target.active = False
                removed_source_active = True
        if removed_source_active:
            apply_enabled(scene)
        cached = {_target_key(target.kind, target.source): target
                  for target in settings.targets if target.source}
        if settings.effect_scope == 'MANUAL':
            candidates = [(item.kind, item.source) for item in settings.manual_targets
                          if item.source and item.source.name in scene.objects]
        else:
            candidates = _candidates(scene)
        # Proxies are created before querying evaluated bounds; source bounds
        # after the addon modifier must never drive their own membership.
        if settings.effect_scope == 'CAMERA' and can_prepare:
            for target in settings.targets:
                if target.kind == 'MESH' and target.source:
                    ensure_bounds_proxy(scene, target)
                elif target.kind == 'ARMATURE' and target.source:
                    _ensure_skin_evaluators(target)
        depsgraph = depsgraph or _depsgraph(scene)
        desired = {}
        for kind, source in candidates:
            item_key = _target_key(kind, source)
            target = cached.get(item_key)
            if item_key in invalid:
                continue
            if settings.effect_scope != 'CAMERA' or _in_frame(
                    scene, kind, source, target, depsgraph):
                desired[item_key] = (kind, source)
        # Restore old scope before preparing a new one, including hidden source
        # character meshes and changes between mesh and armature modes.
        changed = False
        for item_key, target in cached.items():
            active = item_key in desired
            if target.active != active:
                target.active = active
                changed = True
        if changed:
            apply_enabled(scene)
        preparations = ({_target_key(kind, source): (kind, source)
                         for kind, source in candidates} if preflight else desired)
        failed = _failures.setdefault(key, {})
        errors = []
        pending = len(invalid)
        for item_key, (kind, source) in preparations.items():
            if item_key in cached:
                continue
            signature = _failure_signature(scene, kind, source)
            previous = failed.get(item_key)
            if previous and previous[0] == signature:
                errors.append(previous[1])
                continue
            if not can_prepare:
                pending += 1
                continue
            # Mesh and armature outputs cannot both own the same source skin.
            # Inactive caches are disposable; manual choices remain recorded.
            conflict_indices = [index for index, target in enumerate(settings.targets)
                                if _conflicts(scene, target, kind, source)]
            if any(settings.targets[index].active for index in conflict_indices):
                message = source.name + '：人物とメッシュの重複登録は同時に適用できません'
                failed[item_key] = (signature, message)
                errors.append(message)
                continue
            for index in reversed(conflict_indices):
                old = settings.targets[index]
                cached.pop(_target_key(old.kind, old.source), None)
                remove_target(scene, index, forget=False)
            new_token = None
            try:
                settings.auto_status = '初回準備中：' + source.name
                layer = (bpy.context.view_layer if bpy.context.scene == scene
                         else scene.view_layers[0])
                with bpy.context.temp_override(scene=scene, view_layer=layer):
                    target = (setup_mesh(scene, source, manual=False) if kind == 'MESH'
                              else setup_armature(scene, source, manual=False))
                new_token = target.token
                target.active = item_key in desired
                if settings.effect_scope == 'CAMERA' and kind == 'MESH':
                    ensure_bounds_proxy(scene, target)
                elif settings.effect_scope == 'CAMERA' and kind == 'ARMATURE':
                    _ensure_skin_evaluators(target)
                cached[item_key] = target
                failed.pop(item_key, None)
                changed = True
            except Exception as exc:
                # Setup and its bounds proxy form one transaction. A failing
                # proxy must not leave a deformed source or a half-ready cache.
                if new_token:
                    for index in reversed(range(len(settings.targets))):
                        if settings.targets[index].token == new_token:
                            remove_target(scene, index, forget=False)
                    _proxy_states.pop(new_token, None)
                message = source.name + '：' + str(exc)
                failed[item_key] = (signature, message)
                errors.append(message)
        if changed:
            apply_enabled(scene)
        count = sum(target.active for target in settings.targets)
        label = {'CAMERA': 'カメラ範囲', 'SCENE': 'シーン全体', 'MANUAL': '手動登録'}[
            settings.effect_scope]
        status = f'{label}：{count} 件に適用'
        if pending:
            if key in _rendering:
                status += f' / レンダー開始後に追加された {pending} 件は未準備です。停止して再実行してください'
            else:
                status += (f' / {pending} 件は次の更新で準備します' if can_prepare else
                           f' / {pending} 件はオブジェクトモードで自動準備します')
                _dirty.add(key)
        if errors:
            status += f' / {len(errors)} 件は未対応：' + ' / '.join(errors[:3])
        settings.auto_status = status
    finally:
        sync_render_lock(scene)
        _busy.discard(key)


def refresh_cursor(scene):
    """Tag only changed cursor centers, including native cursor operators.

    Cursor operators notify the viewport without tagging Scene parameters.
    Poll three coordinates on the existing timer; never retain RNA handles or
    create geometry here. This also flushes the last movement before rendering.
    """
    key = _key(scene)
    settings = scene.gyogan
    if not settings.enabled or settings.center_mode != 'CURSOR':
        _cursor_states.pop(key, None)
        return False
    location = tuple(scene.cursor.location)
    if _cursor_states.get(key) == location:
        return False
    _cursor_states[key] = location
    scene.update_tag()
    request(scene)
    return True


def _tick():
    if not _registered:
        return None
    if _suspended or not hasattr(bpy.data, 'scenes'):
        return .15
    for scene in bpy.data.scenes:
        key = _key(scene)
        if key in _rendering or not hasattr(scene, 'gyogan'):
            continue
        try:
            refresh_cursor(scene)
            if key not in _dirty:
                continue
            reconcile(scene)
        except Exception as exc:
            _dirty.discard(key)
            scene.gyogan.auto_status = '適用範囲の更新を中断しました：' + str(exc)
            import traceback
            traceback.print_exc()
    return .15


@persistent
def _update(scene, depsgraph):
    if _suspended or _key(scene) in _busy or _unused(scene):
        return
    for update in depsgraph.updates:
        owner = update.id.original
        if isinstance(owner, bpy.types.Object) and owner.get(OWNER):
            continue
        if isinstance(owner, (bpy.types.Object, bpy.types.Mesh, bpy.types.Armature,
                              bpy.types.Camera, bpy.types.Scene, bpy.types.Collection)):
            request(scene)
            break


@persistent
def _frame(scene, depsgraph=None):
    if _unused(scene):
        return
    request(scene)
    reconcile(scene, prepare=False, depsgraph=depsgraph)


@persistent
def _render_init(scene):
    # All sources are cached, even if a later animation frame is the first time
    # they enter the camera. Never build an armature inside frame evaluation.
    refresh_cursor(scene)
    _rendering.add(_key(scene))
    reconcile(scene, prepare=True, preflight=True)


@persistent
def _render_pre(scene):
    reconcile(scene, prepare=False)


@persistent
def _render_done(scene):
    _rendering.discard(_key(scene))
    request(scene)


@persistent
def _history_pre(*_args):
    global _suspended
    _suspended = True
    _dirty.clear()
    _busy.clear()
    _failures.clear()
    _proxy_states.clear()
    _cursor_states.clear()
    _rendering.clear()


@persistent
def _history_post(*_args):
    global _suspended
    _suspended = False
    for scene in bpy.data.scenes:
        if hasattr(scene, 'gyogan'):
            request(scene)


HANDLERS = (('depsgraph_update_post', _update), ('frame_change_post', _frame),
            ('render_init', _render_init), ('render_pre', _render_pre),
            ('render_complete', _render_done), ('render_cancel', _render_done),
            ('load_pre', _history_pre), ('load_post', _history_post),
            ('undo_pre', _history_pre), ('undo_post', _history_post),
            ('redo_pre', _history_pre), ('redo_post', _history_post))


def register(*, initialize=True):
    global _registered, _suspended
    _registered = True
    _suspended = False
    for name, callback in HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if callback not in handlers:
            handlers.append(callback)
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=.15, persistent=True)
    if initialize and hasattr(bpy.data, 'scenes'):
        _history_post()


def unregister():
    global _registered
    _registered = False
    if hasattr(bpy.data, 'scenes'):
        for scene in bpy.data.scenes:
            if hasattr(scene, 'gyogan'):
                sync_render_lock(scene, release=True)
    _history_pre()
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    for name, callback in HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if callback in handlers:
            handlers.remove(callback)
