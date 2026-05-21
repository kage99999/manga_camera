# -*- coding: utf-8 -*-
# ファイル名：lattice_manager.py
# 00漫画用Camera Position Manager
# ラティス管理セクション
# 変更点（1.175）:
# - ラティス管理セクションを機能追加なしでモジュール分割

import bpy

from .lattice_common import *


# =========================
# ラティス管理の主要処理
# =========================

def _lattice_manager_scene_relation_sets(scene):
    """管理セットから、登録OBJ名とラティスOBJ名を集める。"""
    registered_names = set()
    lattice_names = set()
    sets = _get_lattice_sets(scene) if scene is not None else None
    if sets is None:
        return registered_names, lattice_names
    for lattice_set in sets:
        for name in _iter_registered_object_names(lattice_set, include_delete_candidates=True):
            if name:
                registered_names.add(str(name))
        lattice_obj = getattr(lattice_set, "lattice_obj", None)
        lattice_name = str(getattr(lattice_obj, "name", "") or "") if lattice_obj is not None else ""
        if lattice_name:
            lattice_names.add(lattice_name)
    return registered_names, lattice_names


def _modifier_links_to_lattice_manager_sets(modifier, scene):
    """名前やタグが無い旧MODでも、登録OBJ+指定ラティスの組み合わせなら管理対象候補にする。"""
    if scene is None or getattr(modifier, "type", "") != 'LATTICE':
        return False
    try:
        owner = getattr(modifier, "id_data", None)
        owner_name = str(getattr(owner, "name", "") or "")
        lattice_obj = getattr(modifier, "object", None)
        lattice_name = str(getattr(lattice_obj, "name", "") or "") if lattice_obj is not None else ""
    except Exception:
        return False
    if not owner_name or not lattice_name:
        return False
    registered_names, lattice_names = _lattice_manager_scene_relation_sets(scene)
    return owner_name in registered_names and lattice_name in lattice_names


def _iter_all_lattice_manager_modifiers(scene=None):
    """全オブジェクトから、このアドオン由来のラティスMODを列挙する。"""
    yielded = set()
    if scene is None:
        try:
            scene = bpy.context.scene
        except Exception:
            scene = None
    sets = _get_lattice_sets(scene) if scene is not None else None
    if sets is not None:
        for lattice_set in sets:
            for obj in _iter_registered_existing_objects(lattice_set, include_delete_candidates=True):
                if not _is_modifier_area_supported(obj):
                    continue
                for mod in list(getattr(obj, "modifiers", []) or []):
                    is_current_set_mod = (
                        _is_managed_lattice_modifier_for_set(mod, lattice_set)
                        or _is_managed_subdivision_modifier_for_set(mod, lattice_set)
                    )
                    is_linked_legacy_mod = _modifier_links_to_lattice_manager_sets(mod, scene)
                    is_named_or_tagged_mod = _is_lattice_manager_candidate_modifier(mod, scene)
                    if not (is_current_set_mod or is_linked_legacy_mod or is_named_or_tagged_mod):
                        continue
                    key = _modifier_unique_key(mod)
                    if key in yielded:
                        continue
                    yielded.add(key)
                    yield mod
    for obj in getattr(bpy.data, "objects", []) or []:
        if not _is_modifier_area_supported(obj):
            continue
        for mod in list(getattr(obj, "modifiers", []) or []):
            if not _is_lattice_manager_candidate_modifier(mod, scene):
                continue
            key = _modifier_unique_key(mod)
            if key in yielded:
                continue
            yielded.add(key)
            yield mod


def _force_disable_modifier_preserve(modifier):
    """全体OFF用に、現在の有効状態を保存してからMODを無効化する。"""
    if modifier is None:
        return False
    try:
        forced = bool(_modifier_custom_get(modifier, LATTICE_GLOBAL_FORCED_KEY, False))
        if not forced:
            _modifier_custom_set(modifier, LATTICE_GLOBAL_PREV_VIEWPORT_KEY, bool(getattr(modifier, "show_viewport", True)))
            _modifier_custom_set(modifier, LATTICE_GLOBAL_PREV_RENDER_KEY, bool(getattr(modifier, "show_render", True)))
            _modifier_custom_set(modifier, LATTICE_GLOBAL_FORCED_KEY, True)
        modifier.show_viewport = False
        modifier.show_render = False
        _tag_modifier_owner_for_update(modifier)
        return True
    except Exception:
        return False


def _restore_modifier_from_global_disable(modifier):
    """全体ON時に、全体OFFで無効化した管理MODを確実に有効化する。"""
    if modifier is None:
        return False
    try:
        # 以前はOFF前の状態へ復元していたが、UI上の「ラティス管理有効」は親スイッチなので、
        # ONへ戻した時点で管理ラティスMODを有効状態にそろえる。
        _modifier_custom_delete(modifier, LATTICE_GLOBAL_FORCED_KEY)
        _modifier_custom_delete(modifier, LATTICE_GLOBAL_PREV_VIEWPORT_KEY)
        _modifier_custom_delete(modifier, LATTICE_GLOBAL_PREV_RENDER_KEY)
        modifier.show_viewport = True
        modifier.show_render = True
        _tag_modifier_owner_for_update(modifier)
        return True
    except Exception:
        return False



def _iter_registered_lattice_objects(scene=None):
    """全登録セットで指定されているラティスOBJを重複なしで列挙する。"""
    yielded = set()
    if scene is None:
        try:
            scene = bpy.context.scene
        except Exception:
            scene = None
    sets = _get_lattice_sets(scene) if scene is not None else None
    if sets is None:
        return
    for lattice_set in sets:
        lattice_obj = getattr(lattice_set, "lattice_obj", None)
        if lattice_obj is None or getattr(lattice_obj, "type", "") != 'LATTICE':
            continue
        name = str(getattr(lattice_obj, "name", "") or "")
        if not name or name in yielded:
            continue
        yielded.add(name)
        yield lattice_obj


def _object_custom_get(obj, key, default=None):
    """Objectのカスタムプロパティを安全に読む。"""
    try:
        return obj.get(key, default)
    except Exception:
        try:
            return obj[key]
        except Exception:
            return default


def _object_custom_set(obj, key, value):
    """Objectのカスタムプロパティを安全に書く。"""
    try:
        obj[key] = value
        return True
    except Exception:
        return False


def _object_custom_delete(obj, key):
    """Objectのカスタムプロパティを安全に削除する。"""
    try:
        if key in obj.keys():
            del obj[key]
        return True
    except Exception:
        return False


def _force_hide_lattice_object_preserve(lattice_obj):
    """全体OFF用に、登録ラティスOBJの表示状態を保存してから非表示にする。"""
    if lattice_obj is None:
        return False
    try:
        forced = bool(_object_custom_get(lattice_obj, LATTICE_OBJECT_FORCED_HIDE_KEY, False))
        if not forced:
            _object_custom_set(lattice_obj, LATTICE_OBJECT_PREV_HIDE_VIEWPORT_KEY, bool(getattr(lattice_obj, "hide_viewport", False)))
            _object_custom_set(lattice_obj, LATTICE_OBJECT_PREV_HIDE_RENDER_KEY, bool(getattr(lattice_obj, "hide_render", False)))
            _object_custom_set(lattice_obj, LATTICE_OBJECT_FORCED_HIDE_KEY, True)
        lattice_obj.hide_viewport = True
        try:
            lattice_obj.hide_render = True
        except Exception:
            pass
        try:
            lattice_obj.update_tag(refresh={'OBJECT'})
        except TypeError:
            lattice_obj.update_tag()
        except Exception:
            pass
        return True
    except Exception:
        return False


def _restore_lattice_object_from_global_hide(lattice_obj):
    """全体ON時に、OFF前の登録ラティスOBJ表示状態へ戻す。"""
    if lattice_obj is None:
        return False
    try:
        forced = bool(_object_custom_get(lattice_obj, LATTICE_OBJECT_FORCED_HIDE_KEY, False))
        prev_viewport = bool(_object_custom_get(lattice_obj, LATTICE_OBJECT_PREV_HIDE_VIEWPORT_KEY, False))
        prev_render = bool(_object_custom_get(lattice_obj, LATTICE_OBJECT_PREV_HIDE_RENDER_KEY, False))
        if forced:
            lattice_obj.hide_viewport = prev_viewport
            try:
                lattice_obj.hide_render = prev_render
            except Exception:
                pass
        _object_custom_delete(lattice_obj, LATTICE_OBJECT_FORCED_HIDE_KEY)
        _object_custom_delete(lattice_obj, LATTICE_OBJECT_PREV_HIDE_VIEWPORT_KEY)
        _object_custom_delete(lattice_obj, LATTICE_OBJECT_PREV_HIDE_RENDER_KEY)
        try:
            lattice_obj.update_tag(refresh={'OBJECT'})
        except TypeError:
            lattice_obj.update_tag()
        except Exception:
            pass
        return True
    except Exception:
        return False


def _apply_registered_lattice_object_visibility(scene, enabled):
    """ラティス管理全体ON/OFFに合わせて登録ラティスOBJの表示状態を切り替える。"""
    changed = 0
    for lattice_obj in _iter_registered_lattice_objects(scene):
        if enabled:
            if _restore_lattice_object_from_global_hide(lattice_obj):
                changed += 1
        else:
            if _force_hide_lattice_object_preserve(lattice_obj):
                changed += 1
    return changed

def apply_lattice_management_enabled(scene, enabled):
    """ラティス管理全体のON/OFFを、全管理MODと登録ラティスOBJへ反映する。"""
    enabled = bool(enabled)
    changed = 0
    for mod in _iter_all_lattice_manager_modifiers(scene):
        if enabled:
            if _restore_modifier_from_global_disable(mod):
                changed += 1
        else:
            if _force_disable_modifier_preserve(mod):
                changed += 1
    if enabled:
        for lattice_obj in _iter_registered_lattice_objects(scene):
            if _restore_lattice_object_from_global_hide(lattice_obj):
                changed += 1
        changed += apply_lattice_set_activation_state(scene)
    else:
        changed += _apply_registered_lattice_object_visibility(scene, False)
    try:
        if bpy.context is not None and bpy.context.view_layer is not None:
            bpy.context.view_layer.update()
    except Exception:
        pass
    return changed


def _is_lattice_management_enabled(scene):
    """ラティス管理全体スイッチの現在値を返す。"""
    if scene is None or not hasattr(scene, "mpm_lattice_management_enabled"):
        return True
    try:
        return bool(getattr(scene, "mpm_lattice_management_enabled", True))
    except Exception:
        return True


def _is_lattice_multi_set_enabled(scene):
    """複数登録セット使用スイッチの現在値を返す。"""
    if scene is None or not hasattr(scene, "mpm_lattice_multi_set_enabled"):
        return False
    try:
        return bool(getattr(scene, "mpm_lattice_multi_set_enabled", False))
    except Exception:
        return False


def _active_lattice_set_uid(scene):
    """現在の登録セットUIDを返す。"""
    active = _get_active_lattice_set(scene) if scene is not None else None
    return _ensure_set_uid(active) if active is not None else ""


def _set_lattice_object_visible_direct(lattice_obj, visible):
    """単独／複数セット運用に合わせて、登録ラティスOBJの表示を直接切り替える。"""
    if lattice_obj is None:
        return False
    try:
        lattice_obj.hide_viewport = not bool(visible)
        try:
            lattice_obj.hide_render = not bool(visible)
        except Exception:
            pass
        try:
            lattice_obj.update_tag(refresh={'OBJECT'})
        except TypeError:
            lattice_obj.update_tag()
        except Exception:
            pass
        return True
    except Exception:
        return False


def _set_single_lattice_set_effective_enabled(scene, lattice_set, enabled):
    """指定セットに属する管理MODだけを実効ON/OFFする。"""
    if lattice_set is None:
        return 0
    count = 0
    for obj in _iter_registered_existing_objects(lattice_set):
        for mod in _iter_managed_modifiers_for_set_on_object(obj, lattice_set):
            if _set_single_modifier_enabled_from_set(scene, lattice_set, mod, enabled_override=bool(enabled)):
                count += 1
    return count


def _effective_lattice_set_enabled(scene, lattice_set):
    """現在の運用モードにおいて、指定セットが有効扱いか返す。"""
    if scene is None or lattice_set is None:
        return False
    if not _is_lattice_management_enabled(scene):
        return False
    if _is_lattice_multi_set_enabled(scene):
        return bool(getattr(lattice_set, "modifiers_enabled", True))
    return _ensure_set_uid(lattice_set) == _active_lattice_set_uid(scene)


def apply_lattice_set_activation_state(scene):
    """複数登録セット使用のON/OFFに合わせて、各セットのMODとラティスOBJ表示を同期する。"""
    if scene is None or not _is_lattice_management_enabled(scene):
        return 0
    sets = _get_lattice_sets(scene)
    if sets is None:
        return 0
    changed = 0
    lattice_visibility = {}
    for lattice_set in sets:
        set_enabled = _effective_lattice_set_enabled(scene, lattice_set)
        changed += _set_single_lattice_set_effective_enabled(scene, lattice_set, set_enabled)
        lattice_obj = getattr(lattice_set, "lattice_obj", None)
        if lattice_obj is not None and getattr(lattice_obj, "type", "") == 'LATTICE':
            name = str(getattr(lattice_obj, "name", "") or "")
            if name:
                lattice_visibility[name] = bool(lattice_visibility.get(name, False) or set_enabled)
    for lattice_obj in _iter_registered_lattice_objects(scene):
        name = str(getattr(lattice_obj, "name", "") or "")
        if not name:
            continue
        if _set_lattice_object_visible_direct(lattice_obj, bool(lattice_visibility.get(name, False))):
            changed += 1
    try:
        if getattr(bpy.context, "view_layer", None) is not None:
            bpy.context.view_layer.update()
    except Exception:
        pass
    return changed


def _on_lattice_management_enabled_update(self, context):
    """ラティス管理全体スイッチ変更時の反映処理。"""
    try:
        scene = context.scene if context is not None else self
    except Exception:
        scene = self
    try:
        apply_lattice_management_enabled(scene, bool(getattr(scene, "mpm_lattice_management_enabled", True)))
    except Exception:
        pass


def _respect_global_lattice_management_state(scene, modifier):
    """全体OFF中に新規作成・更新された管理MODも即OFFにそろえる。"""
    if modifier is None:
        return
    if not _is_lattice_management_enabled(scene):
        _force_disable_modifier_preserve(modifier)


def _modifier_name_is_lattice_manager_style(modifier):
    """このアドオンが作った可能性が高い管理MOD名か判定する。"""
    name = str(getattr(modifier, "name", "") or "")
    parts = name.split("_", 3)
    has_numbered_lattice_prefix = len(parts) >= 4 and parts[0].isdigit() and parts[1] == "ラティ" and parts[2] in {"ラティ", "サブディ"}
    return (
        has_numbered_lattice_prefix
        or name == LATTICE_MODIFIER_NAME
        or name.startswith(LATTICE_MODIFIER_NAME + ".")
        or name.startswith(LATTICE_MODIFIER_NAME + "_")
        or name.startswith(SUBDIVISION_MODIFIER_NAME + ".")
        or name.startswith(SUBDIVISION_MODIFIER_NAME + "_")
        or name == SUBDIVISION_MODIFIER_NAME
        or name.startswith("ラティス_アドオンセット")
        or name.startswith("サブディビジョン_アドオンセット")
        or name.startswith(LATTICE_LEGACY_MODIFIER_PREFIX)
    )


def _is_managed_lattice_modifier(modifier, set_uid):
    """このアドオンが指定セットID用に作ったラティスMODか判定する。"""
    if getattr(modifier, "type", "") != 'LATTICE':
        return False
    created_by = _modifier_custom_get(modifier, "created_by")
    stored_uid = _modifier_custom_get(modifier, "set_uid")
    if created_by == LATTICE_MANAGER_TAG and stored_uid == set_uid:
        return True
    return False


def _is_managed_lattice_modifier_for_set(modifier, lattice_set):
    """現在セット用の管理ラティスMODか、ID優先・名前補助で判定する。"""
    if lattice_set is None or getattr(modifier, "type", "") != 'LATTICE':
        return False
    set_uid = _ensure_set_uid(lattice_set)
    created_by = _modifier_custom_get(modifier, "created_by")
    stored_uid = _modifier_custom_get(modifier, "set_uid")
    stored_role = _modifier_custom_get(modifier, "modifier_role")
    if created_by == LATTICE_MANAGER_TAG and stored_uid == set_uid and (not stored_role or stored_role == LATTICE_MODIFIER_ROLE):
        return True
    if stored_uid and stored_uid != set_uid:
        return False
    if created_by == LATTICE_MANAGER_TAG and not stored_uid and _modifier_name_matches_set(modifier, lattice_set):
        return True
    if not stored_uid and _modifier_name_matches_set(modifier, lattice_set):
        return True
    return False


def _is_managed_subdivision_modifier_for_set(modifier, lattice_set):
    """現在セット用の管理サブディビジョンMODか、ID優先・名前補助で判定する。"""
    if lattice_set is None or getattr(modifier, "type", "") != 'SUBSURF':
        return False
    set_uid = _ensure_set_uid(lattice_set)
    created_by = _modifier_custom_get(modifier, "created_by")
    stored_uid = str(_modifier_custom_get(modifier, "set_uid", "") or "")
    stored_role = str(_modifier_custom_get(modifier, "modifier_role", "") or "")
    name_matches = _subdivision_modifier_name_matches_set(modifier, lattice_set)
    if created_by == LATTICE_MANAGER_TAG and stored_uid == set_uid and (not stored_role or stored_role == SUBDIVISION_MODIFIER_ROLE):
        return True
    if name_matches:
        return True
    if created_by == LATTICE_MANAGER_TAG and (not stored_uid or stored_uid == set_uid) and (not stored_role or stored_role == SUBDIVISION_MODIFIER_ROLE):
        return True
    if stored_uid and stored_uid != set_uid:
        return False
    return False


def _subdivision_modifier_has_manager_style_name(modifier):
    """このアドオンが作った可能性が高いサブディビジョンMOD名か判定する。"""
    name = str(getattr(modifier, "name", "") or "")
    return any(name == prefix or name.startswith(prefix + ".") or name.startswith(prefix + "_") for prefix in SUBDIVISION_LEGACY_MODIFIER_PREFIXES)


def _looks_like_current_set_subdivision_modifier(obj, modifier, lattice_set, lattice_modifier=None):
    """タグ不足の旧サブディビジョンMODを現在セット用として回収できるか判定する。"""
    if lattice_set is None or getattr(modifier, "type", "") != 'SUBSURF':
        return False
    if _is_managed_subdivision_modifier_for_set(modifier, lattice_set):
        return True
    set_uid = _ensure_set_uid(lattice_set)
    created_by = _modifier_custom_get(modifier, "created_by")
    stored_uid = str(_modifier_custom_get(modifier, "set_uid", "") or "")
    stored_role = str(_modifier_custom_get(modifier, "modifier_role", "") or "")
    if stored_uid and stored_uid != set_uid:
        return False
    if created_by == LATTICE_MANAGER_TAG and (not stored_role or stored_role == SUBDIVISION_MODIFIER_ROLE):
        return True
    if not _subdivision_modifier_has_manager_style_name(modifier):
        return False
    if lattice_modifier is None and obj is not None:
        lattice_modifier = _find_managed_lattice_modifier(obj, lattice_set)
    if lattice_modifier is None:
        return _subdivision_modifier_name_matches_set(modifier, lattice_set)
    mod_index = _modifier_index(obj, modifier)
    lattice_index = _modifier_index(obj, lattice_modifier)
    if mod_index < 0 or lattice_index < 0:
        return _subdivision_modifier_name_matches_set(modifier, lattice_set)
    if mod_index <= lattice_index:
        return True
    return _subdivision_modifier_name_matches_set(modifier, lattice_set)


def _collect_managed_subdivision_modifiers_for_set(obj, lattice_set, lattice_modifier=None):
    """指定OBJ上の現在セット用サブディビジョン管理MOD候補をすべて集める。"""
    if not _is_modifier_area_supported(obj) or lattice_set is None:
        return []
    if lattice_modifier is None:
        lattice_modifier = _find_managed_lattice_modifier(obj, lattice_set)
    result = []
    for mod in list(getattr(obj, "modifiers", []) or []):
        if _looks_like_current_set_subdivision_modifier(obj, mod, lattice_set, lattice_modifier):
            result.append(mod)
    return result


def _is_lattice_manager_candidate_modifier(modifier, scene=None):
    """旧版を含め、このアドオン由来とみなせる管理MODか判定する。"""
    mod_type = getattr(modifier, "type", "")
    if mod_type not in {'LATTICE', 'SUBSURF'}:
        return False
    created_by = _modifier_custom_get(modifier, "created_by")
    if created_by == LATTICE_MANAGER_TAG:
        return True
    if _modifier_name_is_lattice_manager_style(modifier):
        return True
    if mod_type == 'LATTICE' and _modifier_links_to_lattice_manager_sets(modifier, scene):
        return True
    return False


def _tag_lattice_modifier_for_set(modifier, lattice_set):
    """管理対象ラティスMODに現在セットの識別情報を付ける。"""
    set_uid = _ensure_set_uid(lattice_set)
    _modifier_custom_set(modifier, "created_by", LATTICE_MANAGER_TAG)
    _modifier_custom_set(modifier, "set_uid", set_uid)
    _modifier_custom_set(modifier, "modifier_role", LATTICE_MODIFIER_ROLE)
    try:
        modifier.name = _modifier_name_for_set(lattice_set)
    except Exception:
        pass


def _tag_subdivision_modifier_for_set(modifier, lattice_set):
    """管理対象サブディビジョンMODに現在セットの識別情報を付ける。"""
    set_uid = _ensure_set_uid(lattice_set)
    _modifier_custom_set(modifier, "created_by", LATTICE_MANAGER_TAG)
    _modifier_custom_set(modifier, "set_uid", set_uid)
    _modifier_custom_set(modifier, "modifier_role", SUBDIVISION_MODIFIER_ROLE)
    try:
        modifier.name = _subdivision_modifier_name_for_set(lattice_set)
    except Exception:
        pass


def _find_managed_lattice_modifier(obj, lattice_set):
    """指定OBJから現在の登録セット用の管理MODを1つ探す。別セットのMODは触らない。"""
    if not _is_modifier_area_supported(obj) or lattice_set is None:
        return None
    modifiers = list(getattr(obj, "modifiers", []) or [])
    for mod in modifiers:
        if _is_managed_lattice_modifier_for_set(mod, lattice_set):
            _tag_lattice_modifier_for_set(mod, lattice_set)
            return mod
    return None


def _cleanup_duplicate_managed_lattice_modifiers(obj, lattice_set, keep_modifier):
    """同じOBJ・同じ登録セット用の管理ラティスMODが複数ある場合、1つだけ残す。"""
    if not _is_modifier_area_supported(obj) or keep_modifier is None or lattice_set is None:
        return 0
    removed = 0
    for mod in list(getattr(obj, "modifiers", []) or []):
        if mod == keep_modifier:
            continue
        if _is_managed_lattice_modifier_for_set(mod, lattice_set):
            try:
                obj.modifiers.remove(mod)
                removed += 1
            except Exception:
                pass
    return removed


def _find_managed_subdivision_modifier(obj, lattice_set):
    """指定OBJから現在の登録セット用の管理サブディビジョンMODを1つ探す。"""
    candidates = _collect_managed_subdivision_modifiers_for_set(obj, lattice_set)
    if not candidates:
        return None
    keep = candidates[0]
    _tag_subdivision_modifier_for_set(keep, lattice_set)
    return keep


def _cleanup_duplicate_managed_subdivision_modifiers(obj, lattice_set, keep_modifier):
    """同じOBJ・同じ登録セット用の管理サブディビジョンMODが複数ある場合、1つだけ残す。"""
    if not _is_modifier_area_supported(obj) or keep_modifier is None or lattice_set is None:
        return 0
    removed = 0
    for mod in _collect_managed_subdivision_modifiers_for_set(obj, lattice_set):
        if mod == keep_modifier:
            continue
        try:
            obj.modifiers.remove(mod)
            removed += 1
        except Exception:
            pass
    return removed


def _iter_managed_modifiers_for_set_on_object(obj, lattice_set):
    """指定OBJ上の現在セット用管理MODを、ラティスとサブディビジョン両方返す。"""
    if not _is_modifier_area_supported(obj) or lattice_set is None:
        return []
    result = []
    seen = set()
    lattice_mod = _find_managed_lattice_modifier(obj, lattice_set)
    if lattice_mod is not None:
        result.append(lattice_mod)
        seen.add(_modifier_unique_key(lattice_mod))
    for mod in _collect_managed_subdivision_modifiers_for_set(obj, lattice_set, lattice_mod):
        key = _modifier_unique_key(mod)
        if key in seen:
            continue
        result.append(mod)
        seen.add(key)
    return result


def _iter_registered_existing_objects(lattice_set, include_delete_candidates=False):
    """ラティス登録OBJのうち現在存在するOBJだけを返す。未確定の削除候補は標準では除外する。"""
    for name in _iter_registered_object_names(lattice_set, include_delete_candidates=include_delete_candidates):
        obj = bpy.data.objects.get(name)
        if obj is not None:
            yield obj


def _count_managed_lattice_modifiers(lattice_set):
    """ラティス登録OBJのうち管理ラティスMODが付いている数を数える。"""
    count = 0
    for obj in _iter_registered_existing_objects(lattice_set):
        if _find_managed_lattice_modifier(obj, lattice_set) is not None:
            count += 1
    return count


def _count_managed_subdivision_modifiers(lattice_set):
    """ラティス登録OBJのうち管理サブディビジョンMODが付いている数を数える。"""
    count = 0
    for obj in _iter_registered_existing_objects(lattice_set):
        if _find_managed_subdivision_modifier(obj, lattice_set) is not None:
            count += 1
    return count


def _count_managed_modifiers(lattice_set):
    """現在設定上必要な管理MODが揃っている登録OBJ数を数える。"""
    count = 0
    use_subdivision = bool(getattr(lattice_set, "use_subdivision", False)) if lattice_set is not None else False
    for obj in _iter_registered_existing_objects(lattice_set):
        has_lattice = _find_managed_lattice_modifier(obj, lattice_set) is not None
        has_subdivision = (not use_subdivision) or (_find_managed_subdivision_modifier(obj, lattice_set) is not None)
        if has_lattice and has_subdivision:
            count += 1
    return count


def _count_any_managed_modifiers_for_set(lattice_set):
    """現在セットに属する管理MODが1つ以上あるOBJ数を数える。"""
    count = 0
    for obj in _iter_registered_existing_objects(lattice_set, include_delete_candidates=True):
        if _iter_managed_modifiers_for_set_on_object(obj, lattice_set):
            count += 1
    return count


def _valid_registered_object_count(lattice_set):
    """ラティス登録OBJのうち現在存在する数を数える。"""
    return sum(1 for _obj in _iter_registered_existing_objects(lattice_set))


def _world_bbox_corners_for_object(obj):
    """OBJのワールド座標バウンディングBOXの8点を返す。"""
    if obj is None:
        return []
    try:
        import mathutils
        matrix = obj.matrix_world.copy()
        return [matrix @ mathutils.Vector(corner) for corner in (getattr(obj, "bound_box", []) or [])]
    except Exception:
        return []


def _registered_objects_world_bbox(lattice_set):
    """ラティス登録OBJ群全体のワールド座標バウンディングBOXを返す。"""
    try:
        import mathutils
    except Exception:
        return None
    points = []
    for obj in _iter_registered_existing_objects(lattice_set):
        points.extend(_world_bbox_corners_for_object(obj))
    if not points:
        return None
    min_v = mathutils.Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    max_v = mathutils.Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return min_v, max_v


def _local_bbox_size_for_object_data(obj):
    """OBJローカル形状の基準サイズを返す。ゼロ割れしないよう補正する。"""
    try:
        import mathutils
    except Exception:
        return None
    local_points = []
    try:
        for corner in getattr(obj, "bound_box", []) or []:
            local_points.append(mathutils.Vector(corner))
    except Exception:
        local_points = []
    if not local_points:
        return mathutils.Vector((1.0, 1.0, 1.0))
    min_v = mathutils.Vector((min(p.x for p in local_points), min(p.y for p in local_points), min(p.z for p in local_points)))
    max_v = mathutils.Vector((max(p.x for p in local_points), max(p.y for p in local_points), max(p.z for p in local_points)))
    size = max_v - min_v
    return mathutils.Vector((size.x if abs(size.x) > 0.000001 else 1.0, size.y if abs(size.y) > 0.000001 else 1.0, size.z if abs(size.z) > 0.000001 else 1.0))


def _fit_lattice_object_to_registered_objects(lattice_set, margin=1.05):
    """指定ラティスOBJをラティス登録OBJ群全体の大きさへ合わせる。"""
    try:
        import mathutils
    except Exception:
        return False, "mathutilsを読み込めません"
    if lattice_set is None:
        return False, "登録セットがありません"
    lattice_obj = getattr(lattice_set, "lattice_obj", None)
    if lattice_obj is None or getattr(lattice_obj, "type", "") != 'LATTICE':
        return False, "ラティスOBJが未指定です"
    bbox = _registered_objects_world_bbox(lattice_set)
    if bbox is None:
        return False, "合わせるラティス登録OBJがありません"
    min_v, max_v = bbox
    center = (min_v + max_v) * 0.5
    world_corners = [
        mathutils.Vector((x, y, z))
        for x in (min_v.x, max_v.x)
        for y in (min_v.y, max_v.y)
        for z in (min_v.z, max_v.z)
    ]
    try:
        rotation = lattice_obj.matrix_world.to_quaternion().to_matrix()
        inv_rotation = rotation.inverted()
    except Exception:
        inv_rotation = mathutils.Matrix.Identity(3)
    local_points = []
    for corner in world_corners:
        local_points.append(inv_rotation @ (corner - center))
    local_min = mathutils.Vector((min(p.x for p in local_points), min(p.y for p in local_points), min(p.z for p in local_points)))
    local_max = mathutils.Vector((max(p.x for p in local_points), max(p.y for p in local_points), max(p.z for p in local_points)))
    target_size = (local_max - local_min) * float(margin)
    target_size = mathutils.Vector((max(target_size.x, 0.0001), max(target_size.y, 0.0001), max(target_size.z, 0.0001)))
    base_size = _local_bbox_size_for_object_data(lattice_obj)
    if base_size is None:
        return False, "ラティスの基準サイズを取得できません"
    try:
        lattice_obj.location = center
        lattice_obj.scale = (target_size.x / base_size.x, target_size.y / base_size.y, target_size.z / base_size.z)
        lattice_obj.update_tag(refresh={'OBJECT'})
    except TypeError:
        try:
            lattice_obj.location = center
            lattice_obj.scale = (target_size.x / base_size.x, target_size.y / base_size.y, target_size.z / base_size.z)
            lattice_obj.update_tag()
        except Exception as exc:
            return False, f"ラティス調整に失敗しました: {exc}"
    except Exception as exc:
        return False, f"ラティス調整に失敗しました: {exc}"
    try:
        if bpy.context is not None and bpy.context.view_layer is not None:
            bpy.context.view_layer.update()
    except Exception:
        pass
    return True, "ラティスをラティス登録OBJに合わせました"


def _set_modifier_lattice_object(modifier, lattice_obj):
    """ラティスMODのラティスOBJを安全に差し替えて、反映できたか確認する。"""
    if modifier is None or lattice_obj is None:
        return False
    if getattr(modifier, "type", "") != 'LATTICE':
        return False
    lattice_name = str(getattr(lattice_obj, "name", "") or "")
    real_lattice = bpy.data.objects.get(lattice_name) if lattice_name else None
    if real_lattice is None or getattr(real_lattice, "type", "") != 'LATTICE':
        return False

    candidates = [modifier]
    try:
        owner = getattr(modifier, "id_data", None)
        if owner is not None and hasattr(owner, "modifiers"):
            owner_mod = owner.modifiers.get(str(getattr(modifier, "name", "") or ""))
            if owner_mod is not None and owner_mod not in candidates:
                candidates.append(owner_mod)
    except Exception:
        pass

    assigned = False
    for target_mod in candidates:
        try:
            target_mod.object = real_lattice
            assigned = True
            modifier = target_mod
            break
        except Exception:
            try:
                setattr(target_mod, "object", real_lattice)
                assigned = True
                modifier = target_mod
                break
            except Exception:
                continue
    if not assigned:
        return False

    try:
        owner = getattr(modifier, "id_data", None)
        if owner is not None:
            owner.update_tag(refresh={'OBJECT'})
    except TypeError:
        try:
            owner = getattr(modifier, "id_data", None)
            if owner is not None:
                owner.update_tag()
        except Exception:
            pass
    except Exception:
        pass
    try:
        if bpy.context is not None and bpy.context.view_layer is not None:
            bpy.context.view_layer.update()
    except Exception:
        pass
    try:
        return getattr(modifier, "object", None) == real_lattice
    except Exception:
        return True

def _configure_subdivision_modifier(modifier, level):
    """管理サブディビジョンMODをシンプル方式・指定レベルへ設定する。"""
    if modifier is None or getattr(modifier, "type", "") != 'SUBSURF':
        return False
    try:
        modifier.subdivision_type = 'SIMPLE'
    except Exception:
        pass
    try:
        modifier.levels = int(level)
        modifier.render_levels = int(level)
        return True
    except Exception:
        return False


def _remove_current_set_subdivision_modifier_from_object(obj, lattice_set):
    """指定OBJから現在セット用の管理サブディビジョンMODだけを削除する。"""
    if obj is None or not _is_modifier_area_supported(obj):
        return 0
    removed = 0
    for mod in _collect_managed_subdivision_modifiers_for_set(obj, lattice_set):
        try:
            obj.modifiers.remove(mod)
            removed += 1
        except Exception:
            pass
    return removed


def _modifier_index(obj, modifier):
    """指定MODの現在インデックスを返す。"""
    try:
        for index, candidate in enumerate(list(getattr(obj, "modifiers", []) or [])):
            if candidate == modifier:
                return index
    except Exception:
        pass
    return -1


def _move_modifier_to_index(obj, modifier, target_index):
    """モディファイアを指定インデックスへ移動する。"""
    if obj is None or modifier is None:
        return False
    try:
        current_index = _modifier_index(obj, modifier)
        if current_index < 0:
            return False
        target_index = max(0, int(target_index))
        if current_index == target_index:
            return True
        modifiers = getattr(obj, "modifiers", None)
        if modifiers is not None and hasattr(modifiers, "move"):
            modifiers.move(current_index, target_index)
            return True
    except Exception:
        pass
    try:
        _ensure_object_mode(bpy.context)
        previous_active = getattr(bpy.context.view_layer.objects, "active", None)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_move_to_index(modifier=str(getattr(modifier, "name", "") or ""), index=int(target_index))
        if previous_active is not None:
            bpy.context.view_layer.objects.active = previous_active
        return True
    except Exception:
        return False


def _ensure_subdivision_before_lattice(obj, subdivision_mod, lattice_mod):
    """サブディビジョンMODをラティスMODの前に並べる。"""
    if obj is None or subdivision_mod is None or lattice_mod is None:
        return False
    sub_index = _modifier_index(obj, subdivision_mod)
    lat_index = _modifier_index(obj, lattice_mod)
    if sub_index < 0 or lat_index < 0:
        return False
    if sub_index < lat_index:
        return True
    return _move_modifier_to_index(obj, subdivision_mod, lat_index)


def _set_single_modifier_enabled_from_set(scene, lattice_set, modifier, enabled_override=None):
    """現在セットのモディファイア有効チェックと全体スイッチを単一MODへ反映する。"""
    if modifier is None:
        return False
    enabled = bool(enabled_override) if enabled_override is not None else (bool(getattr(lattice_set, "modifiers_enabled", True)) if lattice_set is not None else True)
    try:
        if _is_lattice_management_enabled(scene):
            _modifier_custom_delete(modifier, LATTICE_GLOBAL_FORCED_KEY)
            _modifier_custom_delete(modifier, LATTICE_GLOBAL_PREV_VIEWPORT_KEY)
            _modifier_custom_delete(modifier, LATTICE_GLOBAL_PREV_RENDER_KEY)
            modifier.show_viewport = enabled
            modifier.show_render = enabled
        else:
            _modifier_custom_set(modifier, LATTICE_GLOBAL_PREV_VIEWPORT_KEY, enabled)
            _modifier_custom_set(modifier, LATTICE_GLOBAL_PREV_RENDER_KEY, enabled)
            _modifier_custom_set(modifier, LATTICE_GLOBAL_FORCED_KEY, True)
            modifier.show_viewport = False
            modifier.show_render = False
        _tag_modifier_owner_for_update(modifier)
        return True
    except Exception:
        return False


def _refresh_lattice_modifiers_for_set(context, lattice_set):
    """ラティスOBJ変更時に既存管理MODだけへ反映する。"""
    if lattice_set is None:
        return 0
    lattice_obj = getattr(lattice_set, "lattice_obj", None)
    if lattice_obj is None:
        return 0
    updated = 0
    for obj in _iter_registered_existing_objects(lattice_set):
        mod = _find_managed_lattice_modifier(obj, lattice_set)
        if mod is None:
            continue
        if _set_modifier_lattice_object(mod, lattice_obj):
            updated += 1
    return updated


def _modifier_belongs_to_set_uid(modifier, lattice_set, modifier_role, modifier_type):
    """set_uidを基準に、指定セットの管理MODか判定する。"""
    if modifier is None or lattice_set is None:
        return False
    if getattr(modifier, "type", "") != modifier_type:
        return False
    set_uid = _ensure_set_uid(lattice_set)
    created_by = _modifier_custom_get(modifier, "created_by")
    stored_uid = str(_modifier_custom_get(modifier, "set_uid", "") or "")
    stored_role = str(_modifier_custom_get(modifier, "modifier_role", "") or "")
    if created_by != LATTICE_MANAGER_TAG and stored_uid != set_uid:
        return False
    if stored_uid != set_uid:
        return False
    if stored_role and stored_role != modifier_role:
        return False
    return True


def _modifier_name_contains_set_name_for_role(modifier, lattice_set, role_prefix):
    """UIDが無い／弱い管理MODを、名前に含まれるセット名と役割名から拾う。"""
    if modifier is None or lattice_set is None:
        return False
    name = _modifier_base_name_without_numeric_suffix(str(getattr(modifier, "name", "") or ""))
    set_name = _safe_name(getattr(lattice_set, "set_name", "") if lattice_set is not None else "", "")
    if not name or not set_name:
        return False
    return (role_prefix in name) and name.endswith("_" + set_name)


def _modifier_belongs_to_set_for_rename(modifier, lattice_set, modifier_role, modifier_type):
    """登録セットの順番変更時にリネームしてよい管理MODか判定する。

    通常は set_uid を優先する。旧版や途中版でタグが弱い場合だけ、
    新形式名に含まれる役割名＋登録セット名で補足する。
    """
    if modifier is None or lattice_set is None:
        return False
    if getattr(modifier, "type", "") != modifier_type:
        return False
    if _modifier_belongs_to_set_uid(modifier, lattice_set, modifier_role, modifier_type):
        return True
    stored_uid = str(_modifier_custom_get(modifier, "set_uid", "") or "")
    if stored_uid:
        return False
    if modifier_type == 'LATTICE':
        return _modifier_name_contains_set_name_for_role(modifier, lattice_set, LATTICE_MODIFIER_NAME)
    if modifier_type == 'SUBSURF':
        return _modifier_name_contains_set_name_for_role(modifier, lattice_set, SUBDIVISION_MODIFIER_NAME)
    return False


def _iter_all_modifier_owner_objects():
    """全OBJからモディファイアを持つ可能性のあるOBJだけを返す。"""
    try:
        objects = list(getattr(bpy.data, "objects", []) or [])
    except Exception:
        objects = []
    for obj in objects:
        if _is_modifier_area_supported(obj):
            yield obj


def _collect_rename_targets_for_sets(scene, target_sets=None):
    """現在の登録セット順に合わせてリネーム対象MODを集める。"""
    if target_sets is None:
        target_sets = _get_lattice_sets(scene)
    if target_sets is None:
        return []
    entries = []
    seen = set()
    for lattice_set in list(target_sets):
        if lattice_set is None:
            continue
        lattice_name = _modifier_name_for_set(lattice_set)
        subdivision_name = _subdivision_modifier_name_for_set(lattice_set)
        for obj in _iter_all_modifier_owner_objects():
            for mod in list(getattr(obj, "modifiers", []) or []):
                key = _modifier_unique_key(mod)
                if key in seen:
                    continue
                if _modifier_belongs_to_set_for_rename(mod, lattice_set, LATTICE_MODIFIER_ROLE, 'LATTICE'):
                    entries.append((obj, mod, lattice_set, LATTICE_MODIFIER_ROLE, lattice_name))
                    seen.add(key)
                elif _modifier_belongs_to_set_for_rename(mod, lattice_set, SUBDIVISION_MODIFIER_ROLE, 'SUBSURF'):
                    entries.append((obj, mod, lattice_set, SUBDIVISION_MODIFIER_ROLE, subdivision_name))
                    seen.add(key)
    return entries


def _apply_modifier_rename_entries(entries):
    """同名衝突を避けるため一度一時名へ逃がしてから正式リネームする。"""
    if not entries:
        return 0
    renamed = 0
    temp_entries = []
    for index, (obj, mod, lattice_set, role, target_name) in enumerate(entries):
        try:
            current_name = str(getattr(mod, "name", "") or "")
        except Exception:
            current_name = ""
        temp_name = f"__mpm_lattice_tmp_{index:04d}__"
        try:
            if current_name != target_name:
                mod.name = temp_name
                temp_entries.append((obj, mod, lattice_set, role, target_name, current_name))
            else:
                temp_entries.append((obj, mod, lattice_set, role, target_name, current_name))
        except Exception:
            temp_entries.append((obj, mod, lattice_set, role, target_name, current_name))
    for obj, mod, lattice_set, role, target_name, previous_name in temp_entries:
        try:
            _modifier_custom_set(mod, "created_by", LATTICE_MANAGER_TAG)
            _modifier_custom_set(mod, "set_uid", _ensure_set_uid(lattice_set))
            _modifier_custom_set(mod, "modifier_role", role)
            if str(getattr(mod, "name", "") or "") != target_name:
                mod.name = target_name
            if previous_name != str(getattr(mod, "name", "") or ""):
                renamed += 1
        except Exception:
            pass
        try:
            obj.update_tag(refresh={'OBJECT'})
        except TypeError:
            try:
                obj.update_tag()
            except Exception:
                pass
        except Exception:
            pass
    return renamed


def _rename_managed_modifiers_for_set(context, lattice_set):
    """セット名変更・番号変更を単一セットの管理MOD名へ反映する。"""
    if lattice_set is None:
        return 0
    scene = getattr(lattice_set, "id_data", None)
    entries = _collect_rename_targets_for_sets(scene, [lattice_set])
    return _apply_modifier_rename_entries(entries)


def _rename_managed_modifiers_for_all_sets(scene):
    """登録セット削除などで番号が変わった時に、管理MOD名だけ現在順へ整える。"""
    entries = _collect_rename_targets_for_sets(scene)
    renamed = _apply_modifier_rename_entries(entries)
    try:
        if getattr(bpy.context, "view_layer", None) is not None:
            bpy.context.view_layer.update()
    except Exception:
        pass
    return renamed


def _on_lattice_object_update(self, context):
    """ラティスOBJ欄の変更を既存管理MODへ反映する。"""
    try:
        _ensure_set_uid(self)
        _refresh_lattice_modifiers_for_set(context, self)
    except Exception:
        pass


def _on_set_name_update(self, context):
    """登録名の変更を管理MOD名へ反映する。"""
    try:
        _ensure_set_uid(self)
        _rename_managed_modifiers_for_set(context, self)
    except Exception:
        pass


def _on_lattice_set_modifier_enabled_update(self, context):
    """現在セットのモディファイア有効チェックを既存管理MODへ反映する。"""
    try:
        _ensure_set_uid(self)
        scene = context.scene if context is not None else getattr(self, "id_data", None)
        apply_lattice_set_activation_state(scene)
    except Exception:
        pass


def _on_lattice_multi_set_update(self, context):
    """複数登録セット使用の切り替えを管理MODとラティスOBJ表示へ反映する。"""
    try:
        scene = context.scene if context is not None else self
        apply_lattice_set_activation_state(scene)
    except Exception:
        pass


# =========================
# Enum用ヘルパー
# =========================
def _lattice_set_enum_items(self, context):
    """登録セットプルダウンの項目を作る。文字化け対策のため項目をグローバルに保持する。"""
    scene = context.scene if context is not None else self
    sets = _get_lattice_sets(scene)
    LATTICE_SET_ENUM_CACHE.clear()
    if sets is None or len(sets) == 0:
        LATTICE_SET_ENUM_CACHE.append((_enum_keep_text("__none__"), _enum_keep_text("未作成"), _enum_keep_text("登録セットがありません")))
        return LATTICE_SET_ENUM_CACHE
    for index, lattice_set in enumerate(sets):
        uid = _ensure_set_uid(lattice_set)
        label = _safe_name(getattr(lattice_set, "set_name", ""), f"登録セット {index + 1}")
        LATTICE_SET_ENUM_CACHE.append((_enum_keep_text(uid), _enum_keep_text(label), _enum_keep_text(f"{label} を選択")))
    return LATTICE_SET_ENUM_CACHE


def _on_lattice_set_enum_update(self, context):
    """登録セットプルダウン変更時にアクティブ番号を同期する。"""
    try:
        _clear_all_delete_candidates(self)
    except Exception:
        pass
    value = str(getattr(self, "mpm_lattice_active_set_enum", "__none__") or "__none__")
    sets = _get_lattice_sets(self)
    if value == "__none__" or sets is None or len(sets) == 0:
        try:
            self.mpm_lattice_active_set_index = -1
        except Exception:
            pass
        return
    for index, lattice_set in enumerate(sets):
        if _ensure_set_uid(lattice_set) == value:
            self.mpm_lattice_active_set_index = index
            _cleanup_missing_registered_objects(lattice_set)
            apply_lattice_set_activation_state(self)
            return
    try:
        fallback_index = int(value)
    except Exception:
        fallback_index = 0
    self.mpm_lattice_active_set_index = max(0, min(fallback_index, len(sets) - 1))
    active_index = _ensure_active_lattice_index(self)
    if 0 <= active_index < len(sets):
        _cleanup_missing_registered_objects(sets[active_index])
    apply_lattice_set_activation_state(self)

def _unique_set_name(scene, base_name="登録セット"):
    """既存セット名と重複しない名前を作る。"""
    sets = _get_lattice_sets(scene)
    existing = {str(getattr(item, "set_name", "") or "") for item in sets} if sets is not None else set()
    base = _safe_name(base_name, "登録セット")
    if base not in existing:
        return base
    number = 2
    while True:
        candidate = f"{base} {number}"
        if candidate not in existing:
            return candidate
        number += 1


def _next_lattice_set_name(scene):
    """新規セット用に ラティスセット_01 形式の名前を作る。"""
    sets = _get_lattice_sets(scene)
    existing = {str(getattr(item, "set_name", "") or "") for item in sets} if sets is not None else set()
    number = 1
    while True:
        candidate = f"ラティスセット_{number:02d}"
        if candidate not in existing:
            return candidate
        number += 1


# =========================
# モディファイア操作ヘルパー
# =========================
def _ensure_object_mode(context):
    """モディファイア操作前に可能ならOBJECTモードへ戻す。"""
    try:
        if bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        pass


def _set_managed_modifiers_enabled(lattice_set, enabled):
    """現在セットの管理MODだけを表示/レンダーONOFFする。"""
    if lattice_set is None:
        return 0
    count = 0
    scene = getattr(bpy.context, "scene", None)
    for obj in _iter_registered_existing_objects(lattice_set):
        for mod in _iter_managed_modifiers_for_set_on_object(obj, lattice_set):
            if _is_managed_lattice_modifier_for_set(mod, lattice_set):
                _tag_lattice_modifier_for_set(mod, lattice_set)
                _cleanup_duplicate_managed_lattice_modifiers(obj, lattice_set, mod)
            elif _is_managed_subdivision_modifier_for_set(mod, lattice_set):
                _tag_subdivision_modifier_for_set(mod, lattice_set)
                _cleanup_duplicate_managed_subdivision_modifiers(obj, lattice_set, mod)
            if _set_single_modifier_enabled_from_set(scene, lattice_set, mod, enabled_override=bool(enabled)):
                count += 1
    return count


def _delete_managed_modifiers_for_set(lattice_set):
    """現在セットの管理MODだけをラティス登録OBJから削除する。別セットのMODは残す。"""
    if lattice_set is None:
        return 0
    count = 0
    for obj in _iter_registered_existing_objects(lattice_set):
        if not _is_modifier_area_supported(obj):
            continue
        for candidate in list(getattr(obj, "modifiers", []) or []):
            if _is_managed_lattice_modifier_for_set(candidate, lattice_set) or _is_managed_subdivision_modifier_for_set(candidate, lattice_set):
                try:
                    obj.modifiers.remove(candidate)
                    count += 1
                except Exception:
                    pass
    return count




def _sync_lattice_selected_object_display_items(context):
    """3Dビューの選択OBJをラティス管理用の表示リストへ同期する。"""
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "mpm_lattice_selected_display_items"):
        return 0
    items = wm.mpm_lattice_selected_display_items
    items.clear()
    count = 0
    for obj in getattr(context, "selected_objects", []) or []:
        if obj is None:
            continue
        if getattr(obj, "type", "") == 'LATTICE':
            continue
        if not _is_modifier_area_supported(obj):
            continue
        item = items.add()
        item.object_name = str(getattr(obj, "name", "") or "")
        count += 1
    if count == 0:
        try:
            wm.mpm_lattice_selected_display_index = 0
        except Exception:
            pass
    else:
        try:
            current = int(getattr(wm, "mpm_lattice_selected_display_index", 0) or 0)
            wm.mpm_lattice_selected_display_index = max(0, min(current, count - 1))
        except Exception:
            pass
    return count

# =========================
# ストック保存用ヘルパー（互換入口）
# =========================
def export_lattice_stock_state(scene):
    """ラティス状態を書き出す互換入口。実処理は lattice_stock.py に分離。"""
    from .lattice_stock import export_lattice_stock_state as _export_lattice_stock_state
    return _export_lattice_stock_state(scene)


def apply_lattice_stock_state(scene, state):
    """ラティス状態を復元する互換入口。実処理は lattice_stock.py に分離。"""
    from .lattice_stock import apply_lattice_stock_state as _apply_lattice_stock_state
    return _apply_lattice_stock_state(scene, state)


# =========================
# Nパネル
# =========================
class VIEW3D_PT_manga_camera_lattice_manager(bpy.types.Panel):
    bl_label = "ラティス管理"
    bl_idname = "VIEW3D_PT_manga_camera_lattice_manager"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'カメラ'
    bl_order = 35

    def draw(self, context):
        from .lattice_ui_draw import draw_lattice_manager_panel
        draw_lattice_manager_panel(self.layout, context)


# =========================
# 登録 / 解除
# =========================
from .lattice_types import (
    MPM_LatticeRegisteredObjectItem,
    MPM_LatticeSelectedObjectDisplayItem,
    MPM_LatticeSetItem,
    MPM_UL_lattice_selected_object_list,
    MPM_UL_lattice_registered_object_list,
)
from .lattice_ops import (
    MPM_OT_lattice_select_registered_object,
    MPM_OT_lattice_add_set,
    MPM_OT_lattice_duplicate_set,
    MPM_OT_lattice_delete_set,
    MPM_OT_lattice_register_selected_objects,
    MPM_OT_lattice_remove_selected_objects,
    MPM_OT_lattice_cancel_remove_selected_objects,
    MPM_OT_lattice_fit_to_registered_objects,
    MPM_OT_lattice_apply_or_update_modifiers,
    MPM_OT_lattice_enable_modifiers,
    MPM_OT_lattice_disable_modifiers,
    MPM_OT_lattice_delete_modifiers,
)

LATTICE_MANAGER_CLASSES = (
    MPM_LatticeRegisteredObjectItem,
    MPM_LatticeSelectedObjectDisplayItem,
    MPM_LatticeSetItem,
    MPM_UL_lattice_selected_object_list,
    MPM_UL_lattice_registered_object_list,
    MPM_OT_lattice_select_registered_object,
    MPM_OT_lattice_add_set,
    MPM_OT_lattice_duplicate_set,
    MPM_OT_lattice_delete_set,
    MPM_OT_lattice_register_selected_objects,
    MPM_OT_lattice_remove_selected_objects,
    MPM_OT_lattice_cancel_remove_selected_objects,
    MPM_OT_lattice_fit_to_registered_objects,
    MPM_OT_lattice_apply_or_update_modifiers,
    MPM_OT_lattice_enable_modifiers,
    MPM_OT_lattice_disable_modifiers,
    MPM_OT_lattice_delete_modifiers,
    VIEW3D_PT_manga_camera_lattice_manager,
)


def register_lattice_manager():
    """ラティス管理セクションを登録する。"""
    for attr in (
        "mpm_lattice_sets",
        "mpm_lattice_active_set_index",
        "mpm_lattice_active_set_enum",
        "mpm_lattice_management_enabled",
        "mpm_lattice_multi_set_enabled",
    ):
        if hasattr(bpy.types.Scene, attr):
            try:
                delattr(bpy.types.Scene, attr)
            except Exception:
                pass
    for attr in (
        "mpm_lattice_selected_display_items",
        "mpm_lattice_selected_display_index",
    ):
        if hasattr(bpy.types.WindowManager, attr):
            try:
                delattr(bpy.types.WindowManager, attr)
            except Exception:
                pass
    for cls in LATTICE_MANAGER_CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.mpm_lattice_sets = bpy.props.CollectionProperty(type=MPM_LatticeSetItem)
    bpy.types.Scene.mpm_lattice_active_set_index = bpy.props.IntProperty(default=-1, options={'SKIP_SAVE'})
    bpy.types.Scene.mpm_lattice_active_set_enum = bpy.props.EnumProperty(items=_lattice_set_enum_items, update=_on_lattice_set_enum_update)
    bpy.types.Scene.mpm_lattice_management_enabled = bpy.props.BoolProperty(
        name="ラティス管理有効",
        description="OFF のとき、このアドオンが管理しているラティスモディファイアをすべて無効にします",
        default=True,
        update=_on_lattice_management_enabled_update,
    )
    bpy.types.Scene.mpm_lattice_multi_set_enabled = bpy.props.BoolProperty(
        name="複数登録セット使用",
        description="ON のとき、登録セットごとのモディファイア有効チェックで複数セットを同時使用します",
        default=False,
        update=_on_lattice_multi_set_update,
    )
    bpy.types.WindowManager.mpm_lattice_selected_display_items = bpy.props.CollectionProperty(type=MPM_LatticeSelectedObjectDisplayItem)
    bpy.types.WindowManager.mpm_lattice_selected_display_index = bpy.props.IntProperty(default=0, options={'SKIP_SAVE'})

def unregister_lattice_manager():
    """ラティス管理セクションを解除する。"""
    try:
        apply_lattice_management_enabled(None, True)
    except Exception:
        pass
    for attr in (
        "mpm_lattice_sets",
        "mpm_lattice_active_set_index",
        "mpm_lattice_active_set_enum",
        "mpm_lattice_management_enabled",
        "mpm_lattice_multi_set_enabled",
    ):
        if hasattr(bpy.types.Scene, attr):
            try:
                delattr(bpy.types.Scene, attr)
            except Exception:
                pass
    for attr in (
        "mpm_lattice_selected_display_items",
        "mpm_lattice_selected_display_index",
    ):
        if hasattr(bpy.types.WindowManager, attr):
            try:
                delattr(bpy.types.WindowManager, attr)
            except Exception:
                pass
    for cls in reversed(LATTICE_MANAGER_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


# -------------------------------
# ファイル名：lattice_manager.py
# Version Footer: 1.175
# -------------------------------
