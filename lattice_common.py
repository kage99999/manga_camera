# -*- coding: utf-8 -*-
# ファイル名：lattice_common.py
# 00漫画用Camera Position Manager
# ラティス管理セクション共通定数・共通ヘルパー
# 変更点（1.175）:
# - lattice_manager.py から共通処理を分離

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



__all__ = [
    "LATTICE_MANAGER_TAG",
    "LATTICE_MODIFIER_NAME",
    "SUBDIVISION_MODIFIER_NAME",
    "SUBDIVISION_LEGACY_MODIFIER_PREFIXES",
    "LATTICE_LEGACY_MODIFIER_PREFIX",
    "SUBDIVISION_MODIFIER_ROLE",
    "LATTICE_MODIFIER_ROLE",
    "LATTICE_SET_ENUM_CACHE",
    "LATTICE_SET_ENUM_STRING_POOL",
    "LATTICE_GLOBAL_FORCED_KEY",
    "LATTICE_GLOBAL_PREV_VIEWPORT_KEY",
    "LATTICE_GLOBAL_PREV_RENDER_KEY",
    "LATTICE_OBJECT_FORCED_HIDE_KEY",
    "LATTICE_OBJECT_PREV_HIDE_VIEWPORT_KEY",
    "LATTICE_OBJECT_PREV_HIDE_RENDER_KEY",
    "_enum_keep_text",
    "_new_uid",
    "_safe_name",
    "_lattice_set_order_number",
    "_lattice_set_order_prefix",
    "_modifier_name_for_set",
    "_subdivision_modifier_name_for_set",
    "_modifier_name_matches_set",
    "_modifier_base_name_without_numeric_suffix",
    "_subdivision_modifier_name_matches_set",
    "_poll_lattice_object",
    "_get_lattice_sets",
    "_ensure_active_lattice_index",
    "_get_active_lattice_set",
    "_ensure_set_uid",
    "_object_exists",
    "_iter_registered_object_names",
    "_registered_name_set",
    "_is_delete_candidate_item",
    "_clear_delete_candidates",
    "_clear_registered_object_checks",
    "_clear_all_delete_candidates",
    "_cleanup_missing_registered_objects",
    "_iter_registered_object_items",
    "_iter_pending_delete_candidate_names",
    "_is_modifier_area_supported",
    "_modifier_custom_get",
    "_modifier_custom_set",
    "_modifier_custom_delete",
    "_modifier_unique_key",
    "_tag_modifier_owner_for_update",
]

# -------------------------------
# ファイル名：lattice_common.py
# Version Footer: 1.175
# -------------------------------
