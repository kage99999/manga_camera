"""Coalesced, reversible updates of the add-on's mesh subdivision modifiers."""

FIELDS = ('subdivision_type', 'levels', 'render_levels', 'show_viewport', 'show_render')


def update_settings(settings, _context):
    """Queue changes without rebuilding geometry inside an RNA callback."""
    from . import targets, presets
    if presets.busy(settings.id_data):
        return
    settings.subdivision_pending = True
    settings.subdivision_status = '追加分割の変更を反映待ちです'
    targets.request(settings.id_data, retry=True)


def defer(scene, *, rendering=False):
    scene.gyogan.subdivision_status = (
        '追加分割の変更はレンダー終了後に反映します' if rendering else
        '追加分割の変更はオブジェクトモードに戻ると反映します')


def _move_before(obj, modifier, following):
    old = list(obj.modifiers).index(modifier)
    destination = list(obj.modifiers).index(following)
    if old < destination:
        destination -= 1
    if old != destination:
        obj.modifiers.move(old, destination)


def apply_to_target(scene, target):
    """Apply current settings to one owned modifier, rolling back on failure.

    Existing user subdivision modifiers are read-only. NONE keeps the owned
    modifier and its identity but stops its contribution. A target's old values
    are retained if the requested level exceeds the budget or is animated.
    """
    from . import cameras, find_modifier, record_modifier, targets
    settings, obj = scene.gyogan, target.source
    if target.kind != 'MESH' or not obj or obj.type != 'MESH':
        return 'UNCHANGED'
    owned = [(record, find_modifier(obj, record)) for record in target.modifiers]
    subs = [(record, modifier) for record, modifier in owned
            if modifier and modifier.type == 'SUBSURF']
    if len(subs) > 1:
        raise ValueError('追加分割が複数あるため変更できません。現在の設定を保持しました')
    record, modifier = subs[0] if subs else (None, None)
    if not modifier and settings.subdivision == 'NONE':
        return 'UNCHANGED'
    if not modifier and any(mod.type == 'SUBSURF' and (mod.show_viewport or mod.show_render)
                            for mod in obj.modifiers):
        return 'EXISTING'
    if modifier:
        animated = targets._animated_paths(obj)
        if any(modifier.path_from_id(field) in animated for field in FIELDS):
            raise ValueError('追加分割にアニメーションがあるため、現在の設定を保持しました')
    if settings.subdivision != 'NONE':
        estimate = len(obj.data.polygons) * (4 ** max(settings.viewport_levels,
                                                    settings.render_levels))
        if estimate > settings.face_limit:
            raise ValueError(f'分割後の推定 {estimate:,} 面が上限 {settings.face_limit:,} 面を超えます。現在の設定を保持しました')
    following = next((mod for _, mod in owned if mod and mod.type == 'NODES'
                      and mod.node_group == target.node_group), None)
    if following is None:
        raise ValueError('魚眼の変形モディファイアがないため、追加分割を変更できません')
    snapshot = (tuple(getattr(modifier, field) for field in FIELDS), record.enabled,
                list(obj.modifiers).index(modifier)) if modifier else None
    created_uid = None
    try:
        if modifier is None:
            modifier = obj.modifiers.new('魚眼・追加分割', 'SUBSURF')
            created_uid = modifier.persistent_uid
            record_modifier(target, modifier)
            record = target.modifiers[-1]
            modifier.show_viewport = False
            modifier.show_render = False
            animated = targets._animated_paths(obj)
            if any(modifier.path_from_id(field) in animated for field in FIELDS):
                raise ValueError('同名の追加分割にアニメーションがあるため変更できません')
        record.enabled = settings.subdivision != 'NONE'
        if record.enabled:
            modifier.subdivision_type = settings.subdivision
            modifier.levels = settings.viewport_levels
            modifier.render_levels = settings.render_levels
            _move_before(obj, modifier, following)
        visible = (record.enabled and settings.enabled and target.active
                   and cameras.usable(scene, settings.camera))
        modifier.show_viewport = bool(visible)
        modifier.show_render = bool(visible)
        return 'UPDATED'
    except Exception:
        if created_uid is not None:
            if modifier and modifier in list(obj.modifiers):
                obj.modifiers.remove(modifier)
            for index in reversed(range(len(target.modifiers))):
                if target.modifiers[index].uid == created_uid:
                    target.modifiers.remove(index)
        elif snapshot is not None:
            values, enabled, position = snapshot
            for field, value in zip(FIELDS, values):
                setattr(modifier, field, value)
            record.enabled = enabled
            current = list(obj.modifiers).index(modifier)
            if current != position:
                obj.modifiers.move(current, position)
        raise


def sync(scene):
    """Flush a saved pending change once; failures are reported per object."""
    settings = scene.gyogan
    if not settings.subdivision_pending:
        return
    errors, updated, existing = [], 0, 0
    for target in settings.targets:
        if target.kind != 'MESH' or not target.source:
            continue
        try:
            result = apply_to_target(scene, target)
            updated += result == 'UPDATED'
            existing += result == 'EXISTING'
        except Exception as exc:
            errors.append(target.source.name + '：' + str(exc))
    settings.subdivision_pending = False
    status = f'追加分割を {updated} 件に反映しました'
    if existing:
        status += f'。既存の分割を持つ {existing} 件は保持しました'
    if errors:
        status += f'。{len(errors)} 件は変更できません：' + ' / '.join(errors)
    settings.subdivision_status = status
