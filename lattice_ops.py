# -*- coding: utf-8 -*-
# ファイル名：lattice_ops.py
# 00漫画用Camera Position Manager
# ラティス管理セクション オペレーター
# 変更点（1.175）:
# - lattice_manager.py からOperator群を分離

import bpy
from . import lattice_manager as _lm

_cleanup_duplicate_managed_lattice_modifiers = _lm._cleanup_duplicate_managed_lattice_modifiers
_cleanup_duplicate_managed_subdivision_modifiers = _lm._cleanup_duplicate_managed_subdivision_modifiers
_cleanup_missing_registered_objects = _lm._cleanup_missing_registered_objects
_clear_registered_object_checks = _lm._clear_registered_object_checks
_configure_subdivision_modifier = _lm._configure_subdivision_modifier
_delete_managed_modifiers_for_set = _lm._delete_managed_modifiers_for_set
_ensure_active_lattice_index = _lm._ensure_active_lattice_index
_ensure_object_mode = _lm._ensure_object_mode
_ensure_set_uid = _lm._ensure_set_uid
_ensure_subdivision_before_lattice = _lm._ensure_subdivision_before_lattice
_find_managed_lattice_modifier = _lm._find_managed_lattice_modifier
_find_managed_subdivision_modifier = _lm._find_managed_subdivision_modifier
_fit_lattice_object_to_registered_objects = _lm._fit_lattice_object_to_registered_objects
_get_active_lattice_set = _lm._get_active_lattice_set
_get_lattice_sets = _lm._get_lattice_sets
_is_modifier_area_supported = _lm._is_modifier_area_supported
_iter_managed_modifiers_for_set_on_object = _lm._iter_managed_modifiers_for_set_on_object
_iter_pending_delete_candidate_names = _lm._iter_pending_delete_candidate_names
_iter_registered_existing_objects = _lm._iter_registered_existing_objects
_modifier_name_for_set = _lm._modifier_name_for_set
_new_uid = _lm._new_uid
_next_lattice_set_name = _lm._next_lattice_set_name
_object_exists = _lm._object_exists
_registered_name_set = _lm._registered_name_set
_remove_current_set_subdivision_modifier_from_object = _lm._remove_current_set_subdivision_modifier_from_object
_rename_managed_modifiers_for_all_sets = _lm._rename_managed_modifiers_for_all_sets
_safe_name = _lm._safe_name
_set_managed_modifiers_enabled = _lm._set_managed_modifiers_enabled
_set_modifier_lattice_object = _lm._set_modifier_lattice_object
_set_single_modifier_enabled_from_set = _lm._set_single_modifier_enabled_from_set
_subdivision_modifier_name_for_set = _lm._subdivision_modifier_name_for_set
_tag_lattice_modifier_for_set = _lm._tag_lattice_modifier_for_set
_tag_subdivision_modifier_for_set = _lm._tag_subdivision_modifier_for_set
_unique_set_name = _lm._unique_set_name
_is_delete_candidate_item = _lm._is_delete_candidate_item

# =========================
# オペレーター
# =========================
class MPM_OT_lattice_select_registered_object(bpy.types.Operator):
    bl_idname = "camera.lattice_select_registered_object"
    bl_label = "ラティス登録OBJを選択"
    bl_description = "ラティス登録OBJ一覧でクリックしたOBJを3Dビュー上で選択します。Shift+クリックで複数選択します"

    object_name: bpy.props.StringProperty(default="")
    object_index: bpy.props.IntProperty(default=-1)
    extend_selection: bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'})

    def invoke(self, context, event):
        try:
            self.extend_selection = bool(getattr(event, "shift", False))
        except Exception:
            self.extend_selection = False
        return self.execute(context)

    def execute(self, context):
        lattice_set = _get_active_lattice_set(context.scene)
        if lattice_set is not None and 0 <= int(self.object_index) < len(lattice_set.objects):
            try:
                lattice_set.object_index = int(self.object_index)
            except Exception:
                pass
        obj = bpy.data.objects.get(str(self.object_name or ""))
        if obj is None:
            self.report({'WARNING'}, "対象OBJが現在のシーンにありません")
            return {'CANCELLED'}
        try:
            if bpy.ops.object.mode_set.poll():
                bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass
        try:
            if bool(getattr(self, "extend_selection", False)):
                # Blender標準のShift選択に近く、既に選択中なら解除、未選択なら追加選択する。
                currently_selected = bool(obj.select_get())
                obj.select_set(not currently_selected)
                if not currently_selected:
                    context.view_layer.objects.active = obj
                else:
                    selected_after = [candidate for candidate in (getattr(context, "selected_objects", []) or []) if candidate is not None]
                    if selected_after:
                        context.view_layer.objects.active = selected_after[-1]
            else:
                for selected_obj in list(getattr(context, "selected_objects", []) or []):
                    if selected_obj is not None:
                        selected_obj.select_set(False)
                obj.select_set(True)
                context.view_layer.objects.active = obj
        except Exception as exc:
            self.report({'WARNING'}, f"OBJ選択に失敗しました: {exc}")
            return {'CANCELLED'}
        return {'FINISHED'}


class MPM_OT_lattice_add_set(bpy.types.Operator):
    bl_idname = "camera.lattice_add_set"
    bl_label = "新規セット"
    bl_description = "ラティス管理用の登録セットを新規作成します"

    def execute(self, context):
        scene = context.scene
        sets = _get_lattice_sets(scene)
        if sets is None:
            self.report({'ERROR'}, "ラティス管理データを取得できません")
            return {'CANCELLED'}
        item = sets.add()
        item.set_uid = _new_uid()
        item.set_name = _next_lattice_set_name(scene)
        index = len(sets) - 1
        scene.mpm_lattice_active_set_index = index
        scene.mpm_lattice_active_set_enum = item.set_uid
        self.report({'INFO'}, "ラティス管理セットを作成しました")
        return {'FINISHED'}


class MPM_OT_lattice_duplicate_set(bpy.types.Operator):
    bl_idname = "camera.lattice_duplicate_set"
    bl_label = "複製"
    bl_description = "現在のラティス管理セットを複製します"

    def execute(self, context):
        scene = context.scene
        sets = _get_lattice_sets(scene)
        source = _get_active_lattice_set(scene)
        if sets is None or source is None:
            self.report({'WARNING'}, "複製するセットがありません")
            return {'CANCELLED'}
        duplicate = sets.add()
        duplicate.set_uid = _new_uid()
        duplicate.set_name = _unique_set_name(scene, f"{_safe_name(source.set_name)} コピー")
        duplicate.lattice_obj = getattr(source, "lattice_obj", None)
        duplicate.use_subdivision = bool(getattr(source, "use_subdivision", False))
        duplicate.subdivision_levels = int(getattr(source, "subdivision_levels", 2) or 2)
        duplicate.modifiers_enabled = bool(getattr(source, "modifiers_enabled", True))
        for src_obj in getattr(source, "objects", []) or []:
            copied = duplicate.objects.add()
            copied.object_name = str(getattr(src_obj, "object_name", "") or "")
        index = len(sets) - 1
        scene.mpm_lattice_active_set_index = index
        scene.mpm_lattice_active_set_enum = duplicate.set_uid
        self.report({'INFO'}, "ラティス管理セットを複製しました")
        return {'FINISHED'}


class MPM_OT_lattice_delete_set(bpy.types.Operator):
    bl_idname = "camera.lattice_delete_set"
    bl_label = "削除"
    bl_description = "現在のラティス管理セットを削除します。対象セットの管理MODも削除します"

    def execute(self, context):
        scene = context.scene
        sets = _get_lattice_sets(scene)
        index = _ensure_active_lattice_index(scene)
        if sets is None or not (0 <= index < len(sets)):
            self.report({'WARNING'}, "削除するセットがありません")
            return {'CANCELLED'}
        lattice_set = sets[index]
        removed_mods = _delete_managed_modifiers_for_set(lattice_set)
        sets.remove(index)
        renamed_mods = 0
        if len(sets) == 0:
            scene.mpm_lattice_active_set_index = -1
            scene.mpm_lattice_active_set_enum = "__none__"
        else:
            new_index = max(0, min(index, len(sets) - 1))
            scene.mpm_lattice_active_set_index = new_index
            scene.mpm_lattice_active_set_enum = _ensure_set_uid(sets[new_index])
            renamed_mods = _rename_managed_modifiers_for_all_sets(scene)
        self.report({'INFO'}, f"セットを削除しました / モディファイア削除: {removed_mods} / 名称整理: {renamed_mods}")
        return {'FINISHED'}


class MPM_OT_lattice_register_selected_objects(bpy.types.Operator):
    bl_idname = "camera.lattice_register_selected_objects"
    bl_label = "ラティス登録OBJに追加"
    bl_description = "3Dビューで選択中のOBJを現在のセットへ追加します"

    def execute(self, context):
        lattice_set = _get_active_lattice_set(context.scene)
        if lattice_set is None:
            self.report({'WARNING'}, "登録セットがありません")
            return {'CANCELLED'}
        existing_names = _registered_name_set(lattice_set)
        lattice_obj = getattr(lattice_set, "lattice_obj", None)
        added = 0
        skipped = 0
        for obj in getattr(context, "selected_objects", []) or []:
            if obj is None:
                continue
            if lattice_obj is not None and obj == lattice_obj:
                skipped += 1
                continue
            if getattr(obj, "type", "") == 'LATTICE':
                skipped += 1
                continue
            if not _is_modifier_area_supported(obj):
                skipped += 1
                continue
            name = str(getattr(obj, "name", "") or "")
            if not name or name in existing_names:
                skipped += 1
                continue
            item = lattice_set.objects.add()
            item.object_name = name
            existing_names.add(name)
            added += 1
        if added == 0:
            self.report({'WARNING'}, f"登録できる選択OBJがありません / 除外: {skipped}")
            return {'CANCELLED'}
        lattice_set.object_index = max(0, len(lattice_set.objects) - 1)
        self.report({'INFO'}, f"ラティス登録OBJに追加しました: {added}")
        return {'FINISHED'}


class MPM_OT_lattice_remove_selected_objects(bpy.types.Operator):
    bl_idname = "camera.lattice_remove_selected_objects"
    bl_label = "登録削除"
    bl_description = "3Dビュー上で選択中のラティス登録OBJを削除候補にします"

    def execute(self, context):
        lattice_set = _get_active_lattice_set(context.scene)
        if lattice_set is None or len(lattice_set.objects) == 0:
            self.report({'WARNING'}, "削除候補にするラティス登録OBJがありません")
            return {'CANCELLED'}
        selected_names = {str(obj.name) for obj in getattr(context, "selected_objects", []) or [] if obj is not None}
        if not selected_names:
            self.report({'WARNING'}, "登録削除するOBJを3Dビュー上で選択してください")
            return {'CANCELLED'}
        marked = 0
        for item in getattr(lattice_set, "objects", []) or []:
            name = str(getattr(item, "object_name", "") or "")
            if name in selected_names and not _is_delete_candidate_item(item):
                try:
                    item.delete_candidate = True
                    marked += 1
                except Exception:
                    pass
        if marked == 0:
            self.report({'WARNING'}, "削除候補にできるラティス対象OBJがありません")
            return {'CANCELLED'}
        self.report({'INFO'}, f"削除候補にしました: {marked} / 追加・更新で確定")
        return {'FINISHED'}


class MPM_OT_lattice_cancel_remove_selected_objects(bpy.types.Operator):
    bl_idname = "camera.lattice_cancel_remove_selected_objects"
    bl_label = "削除取消"
    bl_description = "3Dビュー上で選択中の削除候補OBJだけを取り消します"

    def execute(self, context):
        lattice_set = _get_active_lattice_set(context.scene)
        if lattice_set is None or len(lattice_set.objects) == 0:
            self.report({'WARNING'}, "削除取消できるラティス登録OBJがありません")
            return {'CANCELLED'}
        selected_names = {str(obj.name) for obj in getattr(context, "selected_objects", []) or [] if obj is not None}
        if not selected_names:
            self.report({'WARNING'}, "削除取消するOBJを3Dビュー上で選択してください")
            return {'CANCELLED'}
        cancelled = 0
        for item in getattr(lattice_set, "objects", []) or []:
            name = str(getattr(item, "object_name", "") or "")
            if name in selected_names and _is_delete_candidate_item(item):
                try:
                    item.delete_candidate = False
                    cancelled += 1
                except Exception:
                    pass
        if cancelled == 0:
            self.report({'WARNING'}, "選択中の削除候補OBJがありません")
            return {'CANCELLED'}
        self.report({'INFO'}, f"削除候補を取り消しました: {cancelled}")
        return {'FINISHED'}


class MPM_OT_lattice_fit_to_registered_objects(bpy.types.Operator):
    bl_idname = "camera.lattice_fit_to_registered_objects"
    bl_label = "ラティスをラティス登録OBJに合わせる"
    bl_description = "現在セットのラティスOBJをラティス登録OBJ群のバウンディングBOXに合わせます"

    def execute(self, context):
        lattice_set = _get_active_lattice_set(context.scene)
        success, message = _fit_lattice_object_to_registered_objects(lattice_set, margin=1.05)
        if not success:
            self.report({'WARNING'}, message)
            return {'CANCELLED'}
        self.report({'INFO'}, message)
        return {'FINISHED'}


def _remove_current_set_modifier_from_object_name(lattice_set, object_name):
    """指定OBJ名から現在セット用の管理ラティスMODとサブディビジョンMODだけを削除する。"""
    obj = bpy.data.objects.get(str(object_name or ""))
    if obj is None or not _is_modifier_area_supported(obj):
        return 0
    removed = 0
    for mod in list(_iter_managed_modifiers_for_set_on_object(obj, lattice_set)):
        try:
            obj.modifiers.remove(mod)
            removed += 1
        except Exception:
            pass
    return removed


def _finalize_lattice_delete_candidates(lattice_set):
    """削除候補を確定し、登録一覧と現在セット用MODから外す。"""
    if lattice_set is None:
        return 0, 0
    target_names = set(_iter_pending_delete_candidate_names(lattice_set))
    if not target_names:
        return 0, 0
    removed_mods = 0
    for name in target_names:
        removed_mods += _remove_current_set_modifier_from_object_name(lattice_set, name)
    removed_items = 0
    for index in range(len(lattice_set.objects) - 1, -1, -1):
        item = lattice_set.objects[index]
        name = str(getattr(item, "object_name", "") or "")
        if name in target_names and _is_delete_candidate_item(item):
            try:
                lattice_set.objects.remove(index)
                removed_items += 1
            except Exception:
                pass
    _clear_registered_object_checks(lattice_set)
    if len(lattice_set.objects) == 0:
        lattice_set.object_index = 0
    else:
        try:
            lattice_set.object_index = max(0, min(int(getattr(lattice_set, "object_index", 0) or 0), len(lattice_set.objects) - 1))
        except Exception:
            lattice_set.object_index = 0
    return removed_items, removed_mods


class MPM_OT_lattice_apply_or_update_modifiers(bpy.types.Operator):
    bl_idname = "camera.lattice_apply_or_update_modifiers"
    bl_label = "追加 / 更新"
    bl_description = "ラティス登録OBJへ指定ラティスのラティス管理用モディファイアを追加または更新します"

    def execute(self, context):
        lattice_set = _get_active_lattice_set(context.scene)
        if lattice_set is None:
            self.report({'WARNING'}, "登録セットがありません")
            return {'CANCELLED'}
        cleaned_missing = _cleanup_missing_registered_objects(lattice_set)
        removed_items, removed_mods = _finalize_lattice_delete_candidates(lattice_set)
        lattice_obj = getattr(lattice_set, "lattice_obj", None)
        if lattice_obj is None:
            if cleaned_missing > 0 or removed_items > 0:
                self.report({'INFO'}, f"整理:{cleaned_missing} 登録削除:{removed_items} MOD削除:{removed_mods}")
                return {'FINISHED'}
            self.report({'WARNING'}, "ラティスOBJが未指定です")
            return {'CANCELLED'}
        if len(lattice_set.objects) == 0:
            if cleaned_missing > 0 or removed_items > 0:
                self.report({'INFO'}, f"整理:{cleaned_missing} 登録削除:{removed_items} MOD削除:{removed_mods}")
                return {'FINISHED'}
            self.report({'WARNING'}, "ラティス登録OBJがありません")
            return {'CANCELLED'}
        _ensure_set_uid(lattice_set)
        _ensure_object_mode(context)
        added = 0
        updated = 0
        skipped = 0
        mod_name = _modifier_name_for_set(lattice_set)
        subd_name = _subdivision_modifier_name_for_set(lattice_set)
        use_subdivision = bool(getattr(lattice_set, "use_subdivision", False))
        subdivision_level = int(getattr(lattice_set, "subdivision_levels", 2) or 0)
        subd_added = 0
        subd_updated = 0
        subd_removed = 0
        for obj in _iter_registered_existing_objects(lattice_set):
            if obj == lattice_obj:
                skipped += 1
                continue
            if not _is_modifier_area_supported(obj):
                skipped += 1
                continue
            mod = _find_managed_lattice_modifier(obj, lattice_set)
            if mod is None:
                try:
                    mod = obj.modifiers.new(name=mod_name, type='LATTICE')
                    _tag_lattice_modifier_for_set(mod, lattice_set)
                    added += 1
                except Exception:
                    skipped += 1
                    continue
            else:
                updated += 1
            _tag_lattice_modifier_for_set(mod, lattice_set)
            _cleanup_duplicate_managed_lattice_modifiers(obj, lattice_set, mod)
            if not _set_modifier_lattice_object(mod, lattice_obj):
                skipped += 1
            subdivision_mod = _find_managed_subdivision_modifier(obj, lattice_set)
            if use_subdivision:
                if subdivision_mod is None:
                    try:
                        subdivision_mod = obj.modifiers.new(name=subd_name, type='SUBSURF')
                        _tag_subdivision_modifier_for_set(subdivision_mod, lattice_set)
                        subd_added += 1
                    except Exception:
                        skipped += 1
                        subdivision_mod = None
                else:
                    subd_updated += 1
                if subdivision_mod is not None:
                    _tag_subdivision_modifier_for_set(subdivision_mod, lattice_set)
                    subd_removed += _cleanup_duplicate_managed_subdivision_modifiers(obj, lattice_set, subdivision_mod)
                    _configure_subdivision_modifier(subdivision_mod, subdivision_level)
                    _ensure_subdivision_before_lattice(obj, subdivision_mod, mod)
                    _set_single_modifier_enabled_from_set(context.scene, lattice_set, subdivision_mod)
            else:
                subd_removed += _remove_current_set_subdivision_modifier_from_object(obj, lattice_set)
            _set_single_modifier_enabled_from_set(context.scene, lattice_set, mod)
        self.report({'INFO'}, f"モディファイア処理 完了 / 整理:{cleaned_missing} 登録削除:{removed_items} MOD削除:{removed_mods} ラティス追加:{added} 更新:{updated} サブD追加:{subd_added} 更新:{subd_updated} 削除:{subd_removed} 除外:{skipped}")
        return {'FINISHED'}


class MPM_OT_lattice_enable_modifiers(bpy.types.Operator):
    bl_idname = "camera.lattice_enable_modifiers"
    bl_label = "有効"
    bl_description = "このアドオンが作成した現在セットのラティスモディファイアだけを有効にします"

    def execute(self, context):
        lattice_set = _get_active_lattice_set(context.scene)
        count = _set_managed_modifiers_enabled(lattice_set, True)
        if count == 0:
            self.report({'WARNING'}, "有効化できるモディファイアがありません")
            return {'CANCELLED'}
        self.report({'INFO'}, f"モディファイアを有効化しました: {count}")
        return {'FINISHED'}


class MPM_OT_lattice_disable_modifiers(bpy.types.Operator):
    bl_idname = "camera.lattice_disable_modifiers"
    bl_label = "無効"
    bl_description = "このアドオンが作成した現在セットのラティスモディファイアだけを無効にします"

    def execute(self, context):
        lattice_set = _get_active_lattice_set(context.scene)
        count = _set_managed_modifiers_enabled(lattice_set, False)
        if count == 0:
            self.report({'WARNING'}, "無効化できるモディファイアがありません")
            return {'CANCELLED'}
        self.report({'INFO'}, f"モディファイアを無効化しました: {count}")
        return {'FINISHED'}


class MPM_OT_lattice_delete_modifiers(bpy.types.Operator):
    bl_idname = "camera.lattice_delete_modifiers"
    bl_label = "削除"
    bl_description = "このアドオンが作成した現在セットのラティスモディファイアだけを削除します"

    def execute(self, context):
        lattice_set = _get_active_lattice_set(context.scene)
        count = _delete_managed_modifiers_for_set(lattice_set)
        if count == 0:
            self.report({'WARNING'}, "削除できるモディファイアがありません")
            return {'CANCELLED'}
        self.report({'INFO'}, f"モディファイアを削除しました: {count}")
        return {'FINISHED'}




__all__ = [
    "MPM_OT_lattice_select_registered_object",
    "MPM_OT_lattice_add_set",
    "MPM_OT_lattice_duplicate_set",
    "MPM_OT_lattice_delete_set",
    "MPM_OT_lattice_register_selected_objects",
    "MPM_OT_lattice_remove_selected_objects",
    "MPM_OT_lattice_cancel_remove_selected_objects",
    "MPM_OT_lattice_fit_to_registered_objects",
    "MPM_OT_lattice_apply_or_update_modifiers",
    "MPM_OT_lattice_enable_modifiers",
    "MPM_OT_lattice_disable_modifiers",
    "MPM_OT_lattice_delete_modifiers",
]

# -------------------------------
# ファイル名：lattice_ops.py
# Version Footer: 1.175
# -------------------------------
