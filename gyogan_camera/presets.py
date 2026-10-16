"""Scene-local presets with a live working copy and transactional switching."""

import json
import math
import uuid
from contextlib import contextmanager

import bpy
from bpy.app.handlers import persistent
from bpy.props import CollectionProperty, PointerProperty, StringProperty
from bpy.types import Operator, PropertyGroup, UIList

FIELDS = ('strength', 'fov', 'zoom', 'ellipse_x', 'ellipse_y', 'near_distance',
          'thickness', 'center_mode', 'center_object', 'subdivision', 'viewport_levels',
          'render_levels', 'face_limit', 'mode', 'effect_scope', 'scope',
          'target_collection')
POINTERS = ('center_object', 'target_collection')
_busy = set()


def busy(scene):
    return scene.as_pointer() in _busy


@contextmanager
def _batch(scene):
    key = scene.as_pointer()
    already_busy = key in _busy
    _busy.add(key)
    try:
        yield
    finally:
        if not already_busy:
            _busy.discard(key)


class GYOGAN_PresetEntry(PropertyGroup):
    source: PointerProperty(type=bpy.types.Object)
    kind: StringProperty()


def _update_name(preset, _context):
    if not preset.name.strip():
        preset.name = 'プリセット'


class GYOGAN_Preset(PropertyGroup):
    uuid: StringProperty(options={'HIDDEN'})
    name: StringProperty(name='プリセット名', default='プリセット', options=set(),
                         update=_update_name)
    settings_json: StringProperty(options={'HIDDEN'})
    center_object: PointerProperty(type=bpy.types.Object)
    target_collection: PointerProperty(type=bpy.types.Collection)
    entries: CollectionProperty(type=GYOGAN_PresetEntry)


def snapshot(scene):
    settings = scene.gyogan
    result = {'values': {name: getattr(settings, name) for name in FIELDS
                         if name not in POINTERS},
              'entries': [(entry.source, entry.kind) for entry in settings.manual_targets]}
    result.update({name: getattr(settings, name) for name in POINTERS})
    return result


def _store(preset, state):
    preset.settings_json = json.dumps(state['values'], ensure_ascii=False, allow_nan=False)
    for name in POINTERS:
        setattr(preset, name, state[name])
    preset.entries.clear()
    for source, kind in state['entries']:
        entry = preset.entries.add()
        entry.source, entry.kind = source, kind


def _read(preset):
    try:
        values = json.loads(preset.settings_json)
    except (TypeError, ValueError) as exc:
        raise ValueError('プリセットの設定データを読み込めません。現在の状態を保持しました') from exc
    # 0.1.18 presets used an optional object for the center. Migrate only that
    # exact schema; unrelated missing or extra fields must still be rejected.
    if isinstance(values, dict) and set(values) == set(FIELDS) - set(POINTERS) - {'center_mode'}:
        values['center_mode'] = 'OBJECT'
    return {'values': values, 'entries': [(entry.source, entry.kind)
                                         for entry in preset.entries],
            **{name: getattr(preset, name) for name in POINTERS}}


def _find(settings, preset_id):
    return next((preset for preset in settings.presets if preset.uuid == preset_id), None)


def _index(settings, preset_id):
    return next((index for index, preset in enumerate(settings.presets)
                 if preset.uuid == preset_id), 0)


def ensure(scene):
    """Migrate once; the live working values remain authoritative after Undo/load."""
    settings = scene.gyogan
    active = _find(settings, settings.active_preset_id)
    with _batch(scene):
        if active is None:
            # A stale identifier must not write live values into another preset.
            active = settings.presets.add()
            active.name = ('復元したプリセット' if len(settings.presets) > 1 else 'プリセット1')
            active.uuid = uuid.uuid4().hex
            _store(active, snapshot(scene))
            settings.active_preset_id = active.uuid
        settings.preset_index = _index(settings, active.uuid)
    return active


def save_active(scene):
    active = ensure(scene)
    _store(active, snapshot(scene))
    return active


def _validate(scene, state):
    from . import center_error, dolly, OWNER
    settings = scene.gyogan
    values = state['values']
    expected = set(FIELDS) - set(POINTERS)
    if not isinstance(values, dict) or set(values) != expected:
        raise ValueError('プリセットの設定項目が不正です。現在の状態を保持しました')
    for name, value in values.items():
        prop = settings.bl_rna.properties[name]
        valid = True
        if prop.type == 'BOOLEAN':
            valid = isinstance(value, bool)
        elif prop.type == 'ENUM':
            valid = isinstance(value, str) and value in prop.enum_items.keys()
        elif prop.type in {'INT', 'FLOAT'}:
            valid = (type(value) in ({int} if prop.type == 'INT' else {int, float})
                     and prop.hard_min <= value <= prop.hard_max
                     and math.isfinite(value))
        if not valid:
            raise ValueError('プリセットの「' + prop.name + '」が不正です。現在の状態を保持しました')
    blocked = [settings.bl_rna.properties[name].name for name in FIELDS
               if dolly._has_animation(scene, ('gyogan.' + name,))
               and getattr(settings, name) != (state[name] if name in POINTERS else values[name])]
    if blocked:
        raise ValueError('アニメーション付きの設定は切り替えできません：' + '、'.join(blocked))
    entries, seen = [], set()
    for source, kind in state['entries']:
        # Deleted objects leave null ID pointers, including in inactive presets.
        if source is None:
            continue
        if (kind not in {'MESH', 'ARMATURE'} or source.type != kind
                or source.name not in scene.objects or source.get(OWNER)):
            raise ValueError('プリセットの登録対象を使用できません：' + source.name)
        key = source.as_pointer(), kind
        if key not in seen:
            entries.append((source, kind))
            seen.add(key)
    rigs = {source for source, kind in entries if kind == 'ARMATURE'}
    for source, kind in entries:
        if kind == 'MESH' and any(mod.type == 'ARMATURE' and mod.object in rigs
                                  for mod in source.modifiers):
            raise ValueError('同じプリセットに人物のアーマチュアと対応メッシュは重複登録できません')
    center = state['center_object'] if values['center_mode'] == 'OBJECT' else None
    error = center_error(scene, center)
    if not error:
        pending_meshes = ([source for source, kind in entries if kind == 'MESH']
                          if values['effect_scope'] == 'MANUAL' else
                          [obj for obj in scene.objects if obj.type == 'MESH' and not obj.get(OWNER)]
                          if values['mode'] == 'MESH' else [])
        for source in pending_meshes:
            error = center_error(scene, center, pending_mesh=source)
            if error:
                break
    if error:
        raise ValueError(error)
    return {**state, 'entries': entries}


def _assign(scene, state):
    settings = scene.gyogan
    settings.manual_targets.clear()
    for source, kind in state['entries']:
        entry = settings.manual_targets.add()
        entry.source, entry.kind = source, kind
    for name, value in state['values'].items():
        setattr(settings, name, value)
    for name in POINTERS:
        setattr(settings, name, state[name])
    settings.active_index = 0


def _refresh(scene):
    from . import sync_center_object, targets
    sync_center_object(scene)
    settings = scene.gyogan
    settings.subdivision_pending = True
    settings.subdivision_status = 'プリセットの追加分割を反映待ちです'
    targets.reconcile(scene, prepare=False)
    targets.request(scene, retry=True)
    scene.update_tag()


def _check_editable(scene):
    from . import targets
    if not scene.is_editable:
        raise ValueError('読み取り専用のシーンではプリセットを変更できません')
    if scene.as_pointer() in targets._rendering:
        raise ValueError('レンダー中はプリセットを変更できません')


def activate(scene, preset_id):
    from . import targets, apply_enabled, sync_center_object
    _check_editable(scene)
    settings = scene.gyogan
    old = ensure(scene)
    destination = _find(settings, preset_id)
    if destination is None:
        raise ValueError('切り替え先のプリセットがありません')
    if destination.uuid == old.uuid:
        with _batch(scene):
            settings.preset_index = _index(settings, old.uuid)
        return destination
    state = _validate(scene, _read(destination))
    previous, old_id = snapshot(scene), old.uuid
    old_index = settings.active_index
    old_flags = {target.token: target.active for target in settings.targets}
    pending = settings.subdivision_pending
    status = settings.subdivision_status
    _store(old, previous)
    try:
        with _batch(scene):
            _assign(scene, state)
            settings.active_preset_id = destination.uuid
            settings.preset_index = _index(settings, destination.uuid)
        _refresh(scene)
    except Exception:
        with _batch(scene):
            _assign(scene, previous)
            settings.active_preset_id = old_id
            settings.preset_index = _index(settings, old_id)
            settings.active_index = old_index
            settings.subdivision_pending = pending
            settings.subdivision_status = status
        sync_center_object(scene)
        for target in settings.targets:
            target.active = old_flags.get(target.token, False)
        apply_enabled(scene)
        targets.request(scene, retry=True)
        raise
    settings.preset_status = ''
    return destination


def update_index(settings, _context):
    scene = settings.id_data
    if busy(scene) or not settings.presets:
        return
    requested = settings.preset_index
    try:
        if requested < 0 or requested >= len(settings.presets):
            raise ValueError('切り替え先のプリセットがありません')
        activate(scene, settings.presets[requested].uuid)
    except Exception as exc:
        with _batch(scene):
            settings.preset_index = _index(settings, settings.active_preset_id)
        settings.preset_status = str(exc)


def _unique_name(settings, base):
    existing = {preset.name for preset in settings.presets}
    if base not in existing:
        return base
    suffix = 2
    while base + str(suffix) in existing:
        suffix += 1
    return base + str(suffix)


def create(scene, *, duplicate=False):
    _check_editable(scene)
    old = save_active(scene)
    settings = scene.gyogan
    state = snapshot(scene)
    if not duplicate:
        state['entries'] = []
    name = (_unique_name(settings, old.name + ' の複製') if duplicate
            else next('プリセット' + str(index) for index in range(1, len(settings.presets) + 2)
                      if not any(preset.name == 'プリセット' + str(index)
                                 for preset in settings.presets)))
    preset = settings.presets.add()
    preset.uuid, preset.name = uuid.uuid4().hex, name
    _store(preset, state)
    preset_id = preset.uuid
    try:
        return activate(scene, preset_id)
    except Exception:
        settings.presets.remove(_index(settings, preset_id))
        raise


def delete(scene):
    _check_editable(scene)
    settings = scene.gyogan
    current = ensure(scene)
    current_id = current.uuid
    index = _index(settings, current_id)
    if len(settings.presets) == 1:
        state = snapshot(scene)
        state['entries'] = []
        state['values']['effect_scope'] = 'MANUAL'
        _validate(scene, state)
        replacement = settings.presets.add()
        replacement.uuid, replacement.name = uuid.uuid4().hex, 'プリセット1'
        _store(replacement, state)
        replacement_id = replacement.uuid
        try:
            replacement = activate(scene, replacement_id)
        except Exception:
            settings.presets.remove(_index(settings, replacement_id))
            raise
    else:
        following = settings.presets[index - 1 if index else 1]
        replacement = activate(scene, following.uuid)
    replacement_id = replacement.uuid
    with _batch(scene):
        settings.presets.remove(_index(settings, current_id))
        settings.preset_index = _index(settings, replacement_id)
    settings.preset_status = ''
    return _find(settings, replacement_id)


class _PresetOperator:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        from . import targets
        return (context.scene is not None and context.scene.is_editable
                and context.scene.as_pointer() not in targets._rendering)

    def execute(self, context):
        try:
            self.perform(context.scene)
        except Exception as exc:
            context.scene.gyogan.preset_status = str(exc)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class GYOGAN_OT_preset_add(_PresetOperator, Operator):
    bl_idname = 'gyogan.preset_add'
    bl_label = '新規'
    bl_description = '現在の調整値を引き継ぎ、手動登録が空のプリセットを追加します'

    def perform(self, scene):
        create(scene)


class GYOGAN_OT_preset_duplicate(_PresetOperator, Operator):
    bl_idname = 'gyogan.preset_duplicate'
    bl_label = '複製'
    bl_description = '現在の登録対象と魚眼の設定を複製します'

    def perform(self, scene):
        create(scene, duplicate=True)


class GYOGAN_OT_preset_delete(_PresetOperator, Operator):
    bl_idname = 'gyogan.preset_delete'
    bl_label = '削除'
    bl_description = '選択中のプリセットだけを削除します。元オブジェクトは削除しません'

    def perform(self, scene):
        delete(scene)


class GYOGAN_UL_presets(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.prop(item, 'name', text='', emboss=False, icon='PRESET')


def draw(layout, context):
    from . import targets
    settings = context.scene.gyogan
    box = layout.box()
    box.enabled = context.scene.as_pointer() not in targets._rendering
    box.label(text='対象と魚眼設定のプリセット', icon='PRESET')
    box.template_list('GYOGAN_UL_presets', '', settings, 'presets', settings,
                      'preset_index', rows=2, maxrows=4)
    active = _find(settings, settings.active_preset_id)
    if active:
        box.prop(active, 'name', text='名前')
    row = box.row(align=True)
    row.operator('gyogan.preset_add', icon='ADD')
    row.operator('gyogan.preset_duplicate', icon='DUPLICATE')
    row.operator('gyogan.preset_delete', icon='REMOVE')
    if settings.preset_status:
        import textwrap
        for line in textwrap.wrap(settings.preset_status, width=26):
            box.label(text=line)


@persistent
def _save_pre(*_args):
    for scene in bpy.data.scenes:
        if hasattr(scene, 'gyogan'):
            save_active(scene)


@persistent
def _history_post(*_args):
    _busy.clear()
    for scene in bpy.data.scenes:
        if hasattr(scene, 'gyogan'):
            ensure(scene)


HANDLERS = (('save_pre', _save_pre), ('undo_post', _history_post),
            ('redo_post', _history_post))
CLASSES = (GYOGAN_PresetEntry, GYOGAN_Preset, GYOGAN_OT_preset_add,
           GYOGAN_OT_preset_duplicate, GYOGAN_OT_preset_delete, GYOGAN_UL_presets)


def register():
    _busy.clear()
    for name, callback in HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if callback not in handlers:
            handlers.append(callback)


def unregister():
    for name, callback in HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if callback in handlers:
            handlers.remove(callback)
    _busy.clear()
