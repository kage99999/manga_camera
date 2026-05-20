# -*- coding: utf-8 -*-
# ファイル名：lattice_manager.py
# 00漫画用Camera Position Manager
# ラティス管理セクション
# 変更点（1.173）:
# - 複数登録セット使用スイッチを追加し、単独セット運用時はカレントセットだけを有効化

import bpy
import uuid


# =========================
# ラティス管理用の定数
# =========================
LATTICE_MANAGER_TAG = "manga_camera_lattice_manager"
LATTICE_MODIFIER_NAME = "ラティ_ラティ"
SUBDIVISION_MODIFIER_NAME = "ラティ_サブディ"
SUBDIVISION_LEGACY_MODIFIER_PREFIXES = ("ラティ_サブディ", "サブディビジョン_アドオンセット", "サブディビジョン", "Subdivision", "Subsurf", "SubD")
LATTICE_LEGACY_MODIFIER_PREFIX = "MC_Lattice_"
SUBDIVISION_MODIFIER_ROLE = "subdivision"
LATTICE_MODIFIER_ROLE = "lattice"
LATTICE_SET_ENUM_CACHE = []
LATTICE_SET_ENUM_STRING_POOL = {}
LATTICE_GLOBAL_FORCED_KEY = "mpm_lattice_global_forced_disabled"
LATTICE_GLOBAL_PREV_VIEWPORT_KEY = "mpm_lattice_prev_show_viewport"
LATTICE_GLOBAL_PREV_RENDER_KEY = "mpm_lattice_prev_show_render"
LATTICE_OBJECT_FORCED_HIDE_KEY = "mpm_lattice_object_forced_hidden"
LATTICE_OBJECT_PREV_HIDE_VIEWPORT_KEY = "mpm_lattice_object_prev_hide_viewport"
LATTICE_OBJECT_PREV_HIDE_RENDER_KEY = "mpm_lattice_object_prev_hide_render"


# =========================
# 汎用ヘルパー
# =========================

def _enum_keep_text(value):
    """動的Enumの文字化け防止用に文字列参照を保持する。"""
    text = str(value or "")
    LATTICE_SET_ENUM_STRING_POOL[text] = text
    return LATTICE_SET_ENUM_STRING_POOL[text]


def _new_uid():
    """セット識別用のIDを作る。"""
    return uuid.uuid4().hex


def _safe_name(value, fallback="登録セット"):
    """空文字にならない表示名を返す。"""
    text = str(value or "").strip()
    return text if text else fallback


def _lattice_set_order_number(lattice_set):
    """登録セットの現在順から、表示用の1始まり番号を返す。"""
    if lattice_set is None:
        return 1
    try:
        scene = getattr(lattice_set, "id_data", None)
        sets = _get_lattice_sets(scene) if scene is not None else None
        target_uid = _ensure_set_uid(lattice_set)
        if sets is not None:
            for index, item in enumerate(sets):
                if _ensure_set_uid(item) == target_uid:
                    return index + 1
    except Exception:
        pass
    return 1


def _lattice_set_order_prefix(lattice_set):
    """登録セット番号を 01 のような2桁表記にする。"""
    return f"{_lattice_set_order_number(lattice_set):02d}"


def _modifier_name_for_set(lattice_set):
    """登録セット番号を先頭に付けたラティス管理用モディファイア名を作る。"""
    set_name = _safe_name(getattr(lattice_set, "set_name", "") if lattice_set is not None else "", "登録セット")
    return f"{_lattice_set_order_prefix(lattice_set)}_{LATTICE_MODIFIER_NAME}_{set_name}"


def _subdivision_modifier_name_for_set(lattice_set):
    """登録セット番号を先頭に付けたサブディビジョン管理用モディファイア名を作る。"""
    set_name = _safe_name(getattr(lattice_set, "set_name", "") if lattice_set is not None else "", "登録セット")
    return f"{_lattice_set_order_prefix(lattice_set)}_{SUBDIVISION_MODIFIER_NAME}_{set_name}"


def _modifier_name_matches_set(modifier, lattice_set):
    """カスタムプロパティが読めない環境向けに、名前でも現在セット用MODか判定する。"""
    name = str(getattr(modifier, "name", "") or "")
    target = _modifier_name_for_set(lattice_set)
    return name == target or name.startswith(target + ".")


def _modifier_base_name_without_numeric_suffix(name):
    """Blenderが付ける .001 のような連番を除いた名前を返す。"""
    text = str(name or "")
    if len(text) >= 4 and text[-4] == "." and text[-3:].isdigit():
        return text[:-4]
    return text


def _subdivision_modifier_name_matches_set(modifier, lattice_set):
    """カスタムプロパティが読めない環境向けに、名前でも現在セット用サブディビジョンMODか判定する。"""
    name = str(getattr(modifier, "name", "") or "")
    base_name = _modifier_base_name_without_numeric_suffix(name)
    target = _subdivision_modifier_name_for_set(lattice_set)
    if name == target or name.startswith(target + ".") or base_name == target:
        return True
    set_name = _safe_name(getattr(lattice_set, "set_name", "") if lattice_set is not None else "", "")
    if set_name and name.startswith(SUBDIVISION_MODIFIER_NAME + "_") and set_name in name:
        return True
    return False


def _poll_lattice_object(self, obj):
    """ラティスOBJ欄でラティスだけを候補にする。"""
    return bool(obj is not None and getattr(obj, "type", "") == 'LATTICE')


def _get_lattice_sets(scene):
    """Sceneからラティスセット一覧を安全に取得する。"""
    return getattr(scene, "mpm_lattice_sets", None)


def _ensure_active_lattice_index(scene):
    """アクティブなセット番号を有効範囲に丸め、Enum側も現在セットIDへ同期する。"""
    sets = _get_lattice_sets(scene)
    if sets is None or len(sets) == 0:
        try:
            scene.mpm_lattice_active_set_index = -1
            scene.mpm_lattice_active_set_enum = "__none__"
        except Exception:
            pass
        return -1
    try:
        index = int(getattr(scene, "mpm_lattice_active_set_index", 0) or 0)
    except Exception:
        index = 0
    index = max(0, min(index, len(sets) - 1))
    try:
        scene.mpm_lattice_active_set_index = index
        active_uid = _ensure_set_uid(sets[index])
        if str(getattr(scene, "mpm_lattice_active_set_enum", "") or "") != active_uid:
            scene.mpm_lattice_active_set_enum = active_uid
    except Exception:
        pass
    return index

def _get_active_lattice_set(scene):
    """現在選択中のラティスセットを返す。"""
    sets = _get_lattice_sets(scene)
    if sets is None or len(sets) == 0:
        return None
    index = _ensure_active_lattice_index(scene)
    if 0 <= index < len(sets):
        return sets[index]
    return None


def _ensure_set_uid(lattice_set):
    """既存ファイル由来でIDが空の場合にIDを補う。"""
    if lattice_set is None:
        return ""
    uid = str(getattr(lattice_set, "set_uid", "") or "")
    if not uid:
        uid = _new_uid()
        try:
            lattice_set.set_uid = uid
        except Exception:
            pass
    return uid


def _object_exists(name):
    """指定名のOBJが現在のBlend内に存在するか確認する。"""
    return bool(name and bpy.data.objects.get(str(name)) is not None)


def _iter_registered_object_names(lattice_set, include_delete_candidates=True):
    """セットに登録されているOBJ名を順番に返す。"""
    if lattice_set is None:
        return []
    names = []
    for item in getattr(lattice_set, "objects", []) or []:
        if not include_delete_candidates and _is_delete_candidate_item(item):
            continue
        name = str(getattr(item, "object_name", "") or "")
        if name:
            names.append(name)
    return names


def _registered_name_set(lattice_set):
    """セット内OBJ名の重複チェック用setを返す。"""
    return set(_iter_registered_object_names(lattice_set))


def _is_delete_candidate_item(item):
    """ラティス登録OBJ項目が削除候補になっているか返す。"""
    try:
        return bool(getattr(item, "delete_candidate", False))
    except Exception:
        return False


def _clear_delete_candidates(lattice_set):
    """指定セット内の削除候補表示を取り消す。"""
    if lattice_set is None:
        return 0
    cleared = 0
    for item in getattr(lattice_set, "objects", []) or []:
        if _is_delete_candidate_item(item):
            try:
                item.delete_candidate = False
                cleared += 1
            except Exception:
                pass
    return cleared


def _clear_registered_object_checks(lattice_set):
    """ラティス登録OBJ一覧の一時チェックを解除する。"""
    if lattice_set is None:
        return 0
    cleared = 0
    for item in getattr(lattice_set, "objects", []) or []:
        try:
            if bool(getattr(item, "ui_checked", False)):
                item.ui_checked = False
                cleared += 1
        except Exception:
            pass
    return cleared


def _clear_all_delete_candidates(scene):
    """全セットの未確定削除候補を取り消す。"""
    sets = _get_lattice_sets(scene)
    if sets is None:
        return 0
    return sum(_clear_delete_candidates(lattice_set) for lattice_set in sets)


def _cleanup_missing_registered_objects(lattice_set):
    """現在のBlend内に存在しないOBJ名をラティス登録OBJから自動整理する。"""
    if lattice_set is None:
        return 0
    removed = 0
    try:
        object_count = len(lattice_set.objects)
    except Exception:
        return 0
    for index in range(object_count - 1, -1, -1):
        item = lattice_set.objects[index]
        name = str(getattr(item, "object_name", "") or "")
        if not name or bpy.data.objects.get(name) is None:
            try:
                lattice_set.objects.remove(index)
                removed += 1
            except Exception:
                pass
    try:
        if len(lattice_set.objects) == 0:
            lattice_set.object_index = 0
        else:
            current = int(getattr(lattice_set, "object_index", 0) or 0)
            lattice_set.object_index = max(0, min(current, len(lattice_set.objects) - 1))
    except Exception:
        pass
    return removed


def _iter_registered_object_items(lattice_set, include_delete_candidates=True):
    """ラティス登録OBJ項目を削除候補の扱い付きで返す。"""
    if lattice_set is None:
        return []
    result = []
    for item in getattr(lattice_set, "objects", []) or []:
        if not include_delete_candidates and _is_delete_candidate_item(item):
            continue
        result.append(item)
    return result


def _iter_pending_delete_candidate_names(lattice_set):
    """削除候補になっているOBJ名を返す。"""
    names = []
    for item in _iter_registered_object_items(lattice_set, include_delete_candidates=True):
        name = str(getattr(item, "object_name", "") or "")
        if name and _is_delete_candidate_item(item):
            names.append(name)
    return names


def _is_modifier_area_supported(obj):
    """対象OBJがモディファイアを持てるか緩く判定する。"""
    return obj is not None and hasattr(obj, "modifiers")


def _modifier_custom_get(modifier, key, default=None):
    """Modifierのカスタムプロパティを安全に読む。"""
    try:
        return modifier.get(key, default)
    except Exception:
        try:
            return modifier[key]
        except Exception:
            return default


def _modifier_custom_set(modifier, key, value):
    """Modifierのカスタムプロパティを安全に書く。"""
    try:
        modifier[key] = value
        return True
    except Exception:
        return False


def _modifier_custom_delete(modifier, key):
    """Modifierのカスタムプロパティを安全に削除する。"""
    try:
        if key in modifier.keys():
            del modifier[key]
        return True
    except Exception:
        return False


def _modifier_unique_key(modifier):
    """同一Modifier判定用に、Python wrapper id ではなく所有OBJ名+MOD名を使う。"""
    try:
        owner = getattr(modifier, "id_data", None)
        owner_name = str(getattr(owner, "name_full", "") or getattr(owner, "name", "") or "")
        mod_name = str(getattr(modifier, "name", "") or "")
        if owner_name or mod_name:
            return (owner_name, mod_name)
    except Exception:
        pass
    return ("__id__", id(modifier))


def _tag_modifier_owner_for_update(modifier):
    """MODのON/OFF変更後に所有OBJを更新対象としてマークする。"""
    try:
        owner = getattr(modifier, "id_data", None)
        if owner is not None:
            try:
                owner.update_tag(refresh={'OBJECT', 'DATA'})
            except TypeError:
                owner.update_tag()
            except Exception:
                pass
            data = getattr(owner, "data", None)
            if data is not None:
                try:
                    data.update_tag()
                except Exception:
                    pass
    except Exception:
        pass


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
# PropertyGroup / UIList
# =========================
class MPM_LatticeRegisteredObjectItem(bpy.types.PropertyGroup):
    object_name: bpy.props.StringProperty(name="OBJ名", default="")
    delete_candidate: bpy.props.BoolProperty(name="削除候補", default=False, options={'SKIP_SAVE'})
    ui_checked: bpy.props.BoolProperty(name="対象", default=False, options={'SKIP_SAVE'})


class MPM_LatticeSelectedObjectDisplayItem(bpy.types.PropertyGroup):
    object_name: bpy.props.StringProperty(name="OBJ名", default="")


class MPM_LatticeSetItem(bpy.types.PropertyGroup):
    set_uid: bpy.props.StringProperty(name="内部ID", default="")
    set_name: bpy.props.StringProperty(name="登録名", default="登録セット", update=_on_set_name_update)
    lattice_obj: bpy.props.PointerProperty(name="ラティス", type=bpy.types.Object, poll=_poll_lattice_object, update=_on_lattice_object_update)
    use_subdivision: bpy.props.BoolProperty(name="サブディビジョン付与", default=False)
    subdivision_levels: bpy.props.IntProperty(name="サブディビジョン数", default=2, min=0, max=6)
    modifiers_enabled: bpy.props.BoolProperty(name="モディファイア有効", default=True, update=_on_lattice_set_modifier_enabled_update)
    objects: bpy.props.CollectionProperty(type=MPM_LatticeRegisteredObjectItem)
    object_index: bpy.props.IntProperty(name="ラティス登録OBJ選択", default=0, options={'SKIP_SAVE'})


class MPM_UL_lattice_selected_object_list(bpy.types.UIList):
    bl_idname = "MPM_UL_lattice_selected_object_list"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        name = str(getattr(item, "object_name", "") or "")
        obj = bpy.data.objects.get(name) if name else None
        icon_name = 'OBJECT_DATA' if obj is not None else 'ERROR'
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            row.label(text=name if name else "名称なし", icon=icon_name)
        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text="", icon=icon_name)


class MPM_UL_lattice_registered_object_list(bpy.types.UIList):
    bl_idname = "MPM_UL_lattice_registered_object_list"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        name = str(getattr(item, "object_name", "") or "")
        exists = _object_exists(name)
        delete_candidate = _is_delete_candidate_item(item)
        selected_names = {str(obj.name) for obj in getattr(context, "selected_objects", []) or [] if obj is not None}
        is_view_selected = bool(name and name in selected_names)
        icon_name = 'OBJECT_DATA' if exists else 'ERROR'
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            row.alert = delete_candidate
            op = row.operator(
                "camera.lattice_select_registered_object",
                text=name if name else "名称なし",
                icon=icon_name,
                emboss=False,
            )
            op.object_name = name
            op.object_index = int(index)
            if is_view_selected:
                mark = row.row(align=True)
                mark.enabled = False
                mark.label(text="選択中", icon='RESTRICT_SELECT_OFF')
            if delete_candidate:
                warn = row.row(align=True)
                warn.alert = True
                warn.label(text="削除候補")
        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text="", icon=icon_name)


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
# UI描画ヘルパー
# =========================
def _draw_subpanel(layout, panel_id, title):
    """Blender標準寄りの小見出しを描く。"""
    try:
        header, body = layout.panel(panel_id, default_closed=False)
        if header is not None:
            header.label(text=title)
        return body
    except Exception:
        box = layout.box()
        box.label(text=title)
        return box


def _draw_lattice_set_header(layout, context, lattice_set):
    """登録セット、登録名、ラティスOBJを描く。"""
    scene = context.scene
    row = layout.row(align=True)
    row.label(text="登録セット：")
    if _get_lattice_sets(scene) is not None and len(scene.mpm_lattice_sets) > 0:
        row.prop(scene, "mpm_lattice_active_set_enum", text="")
    else:
        disabled = row.row(align=True)
        disabled.enabled = False
        disabled.label(text="未作成")

    button_row = layout.row(align=True)
    button_row.operator("camera.lattice_add_set", text="＋新規")
    duplicate = button_row.row(align=True)
    duplicate.enabled = lattice_set is not None
    duplicate.operator("camera.lattice_duplicate_set", text="複製")
    delete = button_row.row(align=True)
    delete.enabled = lattice_set is not None
    delete.operator("camera.lattice_delete_set", text="削除")

    if lattice_set is None:
        info = layout.box()
        info.enabled = False
        info.label(text="登録セットがありません")
        return

    layout.prop(lattice_set, "set_name", text="登録名")
    layout.prop(lattice_set, "lattice_obj", text="ラティス")


def _draw_selected_objects_panel(layout, context, lattice_set):
    """3Dビューで選択中のOBJ一覧と登録ボタンを描く。"""
    body = _draw_subpanel(layout, "mpm_lattice_selected_objects_panel", "ラティス対象OBJ")
    if body is None:
        return
    count = _sync_lattice_selected_object_display_items(context)
    wm = context.window_manager
    body.label(text=f"選択数：{count}")
    if count > 0:
        body.template_list(
            "MPM_UL_lattice_selected_object_list", "",
            wm, "mpm_lattice_selected_display_items",
            wm, "mpm_lattice_selected_display_index",
            rows=4, maxrows=8,
        )
    else:
        empty = body.box()
        empty.enabled = False
        empty.label(text="ラティス対象OBJはありません")

    add_row = body.row(align=True)
    add_row.enabled = lattice_set is not None and count > 0
    add_row.operator("camera.lattice_register_selected_objects", text="ラティス登録OBJに追加")

def _draw_registered_objects_panel(layout, context, lattice_set):
    """ラティス登録OBJの一覧と削除ボタンを描く。"""
    body = _draw_subpanel(layout, "mpm_lattice_registered_objects_panel", "ラティス登録OBJ")
    if body is None:
        return
    count = len(lattice_set.objects) if lattice_set is not None else 0
    pending_count = len(_iter_pending_delete_candidate_names(lattice_set)) if lattice_set is not None else 0
    selected_names = {str(obj.name) for obj in getattr(context, "selected_objects", []) or [] if obj is not None}
    selected_registered_count = sum(
        1
        for item in (getattr(lattice_set, "objects", []) or [])
        if str(getattr(item, "object_name", "") or "") in selected_names
    ) if lattice_set is not None else 0
    status_text = f"登録数：{count}"
    if pending_count > 0:
        status_text += f" / 削除候補：{pending_count}"
    if selected_registered_count > 0:
        status_text += f" / 選択中：{selected_registered_count}"
    body.label(text=status_text)
    if lattice_set is not None and count > 0:
        body.template_list(
            "MPM_UL_lattice_registered_object_list", "",
            lattice_set, "objects",
            lattice_set, "object_index",
            rows=5, maxrows=10,
        )
    else:
        empty = body.box()
        empty.enabled = False
        empty.label(text="ラティス登録OBJはありません")

    remove_row = body.row(align=True)
    remove_row.enabled = lattice_set is not None and count > 0
    remove_row.operator("camera.lattice_remove_selected_objects", text="登録削除")
    remove_row.operator("camera.lattice_cancel_remove_selected_objects", text="削除取消")

def _draw_modifier_management_panel(layout, context, lattice_set):
    """ラティスモディファイア管理ボタンを描く。"""
    body = _draw_subpanel(layout, "mpm_lattice_modifier_management_panel", "モディファイア管理")
    if body is None:
        return
    lattice_obj = getattr(lattice_set, "lattice_obj", None) if lattice_set is not None else None
    registered_count = len(lattice_set.objects) if lattice_set is not None else 0
    valid_count = _valid_registered_object_count(lattice_set) if lattice_set is not None else 0
    managed_count = _count_any_managed_modifiers_for_set(lattice_set) if lattice_set is not None else 0
    mod_name = _modifier_name_for_set(lattice_set) if lattice_set is not None else "未作成"

    if lattice_obj is not None:
        body.label(text=f"対象ラティス：{lattice_obj.name}")
    else:
        disabled = body.row(align=True)
        disabled.enabled = False
        disabled.label(text="対象ラティス：未指定")
    body.label(text=f"モディファイア名：{mod_name}")

    if lattice_set is not None:
        body.prop(lattice_set, "use_subdivision", text="サブディビジョン付与")
        subd_col = body.column(align=True)
        subd_col.enabled = bool(getattr(lattice_set, "use_subdivision", False))
        subd_col.prop(lattice_set, "subdivision_levels", text="サブディビジョン数")
        modifier_enabled_row = body.row(align=True)
        if _is_lattice_multi_set_enabled(context.scene):
            modifier_enabled_row.prop(lattice_set, "modifiers_enabled", text="モディファイア有効")
        else:
            modifier_enabled_row.enabled = False
            modifier_enabled_row.label(text="モディファイア有効（カレント固定）", icon='CHECKBOX_HLT')

    ready = lattice_set is not None and lattice_obj is not None and registered_count > 0 and valid_count > 0

    fit_row = body.row(align=True)
    fit_row.enabled = ready
    fit_row.operator("camera.lattice_fit_to_registered_objects", text="ラティスをラティス登録OBJに合わせる")

    apply_row = body.row(align=True)
    apply_row.scale_y = 1.2
    apply_row.enabled = ready
    apply_row.operator("camera.lattice_apply_or_update_modifiers", text="追加 / 更新")

    # モディファイア単体削除ボタンはUIから撤去する。
    # 登録対象から外したい場合は「登録削除」→「追加 / 更新」で現在セット用MODも整理する。

def _draw_status_panel(layout, context, lattice_set):
    """現在セットの状態を描く。"""
    body = _draw_subpanel(layout, "mpm_lattice_status_panel", "状態")
    if body is None:
        return
    if lattice_set is None:
        disabled = body.column(align=True)
        disabled.enabled = False
        disabled.label(text="ラティス登録OBJ：0")
        disabled.label(text="モディファイアあり：0 / 0")
        disabled.label(text="ラティス：未指定")
        return

    registered_total = len(lattice_set.objects)
    valid_total = _valid_registered_object_count(lattice_set)
    managed_total = _count_managed_modifiers(lattice_set)
    lattice_total = _count_managed_lattice_modifiers(lattice_set)
    subdivision_total = _count_managed_subdivision_modifiers(lattice_set)
    use_subdivision = bool(getattr(lattice_set, "use_subdivision", False))
    lattice_obj = getattr(lattice_set, "lattice_obj", None)

    body.label(text=f"ラティス登録OBJ：{registered_total}")
    if registered_total != valid_total:
        warn = body.row(align=True)
        warn.alert = True
        warn.label(text=f"存在するOBJ：{valid_total} / {registered_total}")
    body.label(text=f"モディファイアあり：{managed_total} / {valid_total}")
    body.label(text=f"ラティスMOD：{lattice_total} / {valid_total}")
    if use_subdivision:
        body.label(text=f"サブディビジョン：{subdivision_total} / {valid_total}")

    lattice_row = body.row(align=True)
    lattice_row.enabled = lattice_obj is not None
    lattice_row.label(text="ラティス：指定済み" if lattice_obj is not None else "ラティス：未指定")


# =========================
# ストック保存用ヘルパー
# =========================
def export_lattice_stock_state(scene):
    """追加データ記録へ保存するラティス状態を辞書化する。"""
    state = {
        "lattice_enabled": _is_lattice_management_enabled(scene),
        "multi_set_enabled": _is_lattice_multi_set_enabled(scene),
        "active_set_uid": _active_lattice_set_uid(scene),
        "active_set_name": "",
        "set_states": [],
    }
    active = _get_active_lattice_set(scene) if scene is not None else None
    if active is not None:
        state["active_set_name"] = _safe_name(getattr(active, "set_name", ""), "")
    sets = _get_lattice_sets(scene) if scene is not None else None
    if sets is not None:
        for lattice_set in sets:
            state["set_states"].append({
                "set_uid": _ensure_set_uid(lattice_set),
                "set_name": _safe_name(getattr(lattice_set, "set_name", ""), ""),
                "modifiers_enabled": bool(getattr(lattice_set, "modifiers_enabled", True)),
            })
    return state


def apply_lattice_stock_state(scene, state):
    """ストックから読み込んだラティス状態を復元する。"""
    if scene is None:
        return 0
    if not isinstance(state, dict):
        state = {"lattice_enabled": bool(state)}
    changed = 0
    sets = _get_lattice_sets(scene)
    set_states = state.get("set_states", [])
    if sets is not None and isinstance(set_states, list):
        for saved in set_states:
            if not isinstance(saved, dict):
                continue
            saved_uid = str(saved.get("set_uid", "") or "")
            saved_name = str(saved.get("set_name", "") or "")
            for lattice_set in sets:
                uid = _ensure_set_uid(lattice_set)
                name = str(getattr(lattice_set, "set_name", "") or "")
                if (saved_uid and uid == saved_uid) or (not saved_uid and saved_name and name == saved_name):
                    try:
                        lattice_set.modifiers_enabled = bool(saved.get("modifiers_enabled", True))
                    except Exception:
                        pass
                    break
    active_uid = str(state.get("active_set_uid", "") or "")
    active_name = str(state.get("active_set_name", "") or "")
    if sets is not None and len(sets) > 0:
        target_index = None
        for index, lattice_set in enumerate(sets):
            uid = _ensure_set_uid(lattice_set)
            name = str(getattr(lattice_set, "set_name", "") or "")
            if (active_uid and uid == active_uid) or (not active_uid and active_name and name == active_name):
                target_index = index
                break
        if target_index is not None:
            try:
                scene.mpm_lattice_active_set_index = target_index
                scene.mpm_lattice_active_set_enum = _ensure_set_uid(sets[target_index])
            except Exception:
                pass
    try:
        if hasattr(scene, "mpm_lattice_multi_set_enabled"):
            scene.mpm_lattice_multi_set_enabled = bool(state.get("multi_set_enabled", False))
    except Exception:
        pass
    enabled = bool(state.get("lattice_enabled", state.get("enabled", False)))
    try:
        if hasattr(scene, "mpm_lattice_management_enabled"):
            scene.mpm_lattice_management_enabled = enabled
        else:
            changed += apply_lattice_management_enabled(scene, enabled)
    except Exception:
        changed += apply_lattice_management_enabled(scene, enabled)
    try:
        changed += apply_lattice_management_enabled(scene, enabled)
    except Exception:
        pass
    return changed


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
        layout = self.layout
        scene = context.scene
        layout.prop(scene, "mpm_lattice_management_enabled", text="ラティス管理有効")
        management_enabled = bool(getattr(scene, "mpm_lattice_management_enabled", True))
        multi_row = layout.row(align=True)
        multi_row.enabled = management_enabled
        multi_row.prop(scene, "mpm_lattice_multi_set_enabled", text="複数登録セット使用")
        try:
            if management_enabled:
                apply_lattice_set_activation_state(scene)
            else:
                apply_lattice_management_enabled(scene, False)
        except Exception:
            pass
        _ensure_active_lattice_index(scene)
        lattice_set = _get_active_lattice_set(scene)
        body = layout.column(align=True)
        body.enabled = management_enabled
        _draw_lattice_set_header(body, context, lattice_set)
        if lattice_set is None:
            return
        _draw_selected_objects_panel(body, context, lattice_set)
        _draw_registered_objects_panel(body, context, lattice_set)
        _draw_modifier_management_panel(body, context, lattice_set)
        _draw_status_panel(body, context, lattice_set)


# =========================
# 登録 / 解除
# =========================
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
# Version Footer: 1.173
# -------------------------------
