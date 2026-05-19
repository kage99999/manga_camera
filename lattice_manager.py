# -*- coding: utf-8 -*-
# ファイル名：lattice_manager.py
# 00漫画用Camera Position Manager
# ラティス管理セクション
# 変更点（1.154）:
# - 登録OBJ一覧のチェックボックス表示を廃止
# - 登録OBJ名の通常クリックで単独選択、Shift+クリックで複数選択に対応
# - 3Dビュー上で選択中の登録OBJを「登録削除」「削除取消」の操作対象に変更
# - 選択中の削除候補だけを取り消す「削除取消」処理を追加

import bpy
import uuid


# =========================
# ラティス管理用の定数
# =========================
LATTICE_MANAGER_TAG = "manga_camera_lattice_manager"
LATTICE_MODIFIER_NAME = "ラティス_アドオンセット"
LATTICE_LEGACY_MODIFIER_PREFIX = "MC_Lattice_"
LATTICE_SET_ENUM_CACHE = []
LATTICE_SET_ENUM_STRING_POOL = {}


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


def _modifier_name_for_set(lattice_set):
    """登録セットごとに区別できるラティス管理用モディファイア名を作る。"""
    set_name = _safe_name(getattr(lattice_set, "set_name", "") if lattice_set is not None else "", "登録セット")
    return f"{LATTICE_MODIFIER_NAME}_{set_name}"


def _modifier_name_matches_set(modifier, lattice_set):
    """カスタムプロパティが読めない環境向けに、名前でも現在セット用MODか判定する。"""
    name = str(getattr(modifier, "name", "") or "")
    target = _modifier_name_for_set(lattice_set)
    return name == target or name.startswith(target + ".")


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
    """登録OBJ項目が削除候補になっているか返す。"""
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
    """登録OBJ一覧の一時チェックを解除する。"""
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


def _iter_registered_object_items(lattice_set, include_delete_candidates=True):
    """登録OBJ項目を削除候補の扱い付きで返す。"""
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


def _modifier_name_is_lattice_manager_style(modifier):
    """このアドオンが作った可能性が高いラティスMOD名か判定する。"""
    name = str(getattr(modifier, "name", "") or "")
    return name == LATTICE_MODIFIER_NAME or name.startswith(LATTICE_MODIFIER_NAME + ".") or name.startswith(LATTICE_LEGACY_MODIFIER_PREFIX)


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
    if created_by == LATTICE_MANAGER_TAG and stored_uid == set_uid:
        return True
    if stored_uid and stored_uid != set_uid:
        return False
    if created_by == LATTICE_MANAGER_TAG and not stored_uid and _modifier_name_matches_set(modifier, lattice_set):
        return True
    if not stored_uid and _modifier_name_matches_set(modifier, lattice_set):
        return True
    return False


def _is_lattice_manager_candidate_modifier(modifier):
    """旧版を含め、このアドオン由来とみなせるラティスMODか判定する。"""
    if getattr(modifier, "type", "") != 'LATTICE':
        return False
    created_by = _modifier_custom_get(modifier, "created_by")
    if created_by == LATTICE_MANAGER_TAG:
        return True
    return _modifier_name_is_lattice_manager_style(modifier)


def _tag_lattice_modifier_for_set(modifier, lattice_set):
    """管理対象MODに現在セットの識別情報を付ける。"""
    set_uid = _ensure_set_uid(lattice_set)
    _modifier_custom_set(modifier, "created_by", LATTICE_MANAGER_TAG)
    _modifier_custom_set(modifier, "set_uid", set_uid)
    try:
        modifier.name = _modifier_name_for_set(lattice_set)
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


def _iter_registered_existing_objects(lattice_set, include_delete_candidates=False):
    """登録OBJのうち現在存在するOBJだけを返す。未確定の削除候補は標準では除外する。"""
    for name in _iter_registered_object_names(lattice_set, include_delete_candidates=include_delete_candidates):
        obj = bpy.data.objects.get(name)
        if obj is not None:
            yield obj


def _count_managed_modifiers(lattice_set):
    """登録OBJのうち管理MODが付いている数を数える。"""
    count = 0
    for obj in _iter_registered_existing_objects(lattice_set):
        if _find_managed_lattice_modifier(obj, lattice_set) is not None:
            count += 1
    return count


def _valid_registered_object_count(lattice_set):
    """登録OBJのうち現在存在する数を数える。"""
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
    """登録OBJ群全体のワールド座標バウンディングBOXを返す。"""
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
    """指定ラティスOBJを登録OBJ群全体の大きさへ合わせる。"""
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
        return False, "合わせる登録OBJがありません"
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
    return True, "ラティスを登録OBJに合わせました"


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


def _rename_managed_modifiers_for_set(context, lattice_set):
    """セット名変更時に管理MOD名も合わせる。"""
    if lattice_set is None:
        return 0
    new_name = _modifier_name_for_set(lattice_set)
    renamed = 0
    for obj in _iter_registered_existing_objects(lattice_set):
        mod = _find_managed_lattice_modifier(obj, lattice_set)
        if mod is None:
            continue
        try:
            mod.name = new_name
            renamed += 1
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
            return
    try:
        fallback_index = int(value)
    except Exception:
        fallback_index = 0
    self.mpm_lattice_active_set_index = max(0, min(fallback_index, len(sets) - 1))
    _ensure_active_lattice_index(self)

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
    objects: bpy.props.CollectionProperty(type=MPM_LatticeRegisteredObjectItem)
    object_index: bpy.props.IntProperty(name="登録OBJ選択", default=0, options={'SKIP_SAVE'})


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
    bl_label = "登録OBJを選択"
    bl_description = "登録OBJ一覧でクリックしたOBJを3Dビュー上で選択します。Shift+クリックで複数選択します"

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
        if len(sets) == 0:
            scene.mpm_lattice_active_set_index = -1
            scene.mpm_lattice_active_set_enum = "__none__"
        else:
            new_index = max(0, min(index, len(sets) - 1))
            scene.mpm_lattice_active_set_index = new_index
            scene.mpm_lattice_active_set_enum = _ensure_set_uid(sets[new_index])
        self.report({'INFO'}, f"セットを削除しました / モディファイア削除: {removed_mods}")
        return {'FINISHED'}


class MPM_OT_lattice_register_selected_objects(bpy.types.Operator):
    bl_idname = "camera.lattice_register_selected_objects"
    bl_label = "登録OBJに追加"
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
        self.report({'INFO'}, f"登録OBJに追加しました: {added}")
        return {'FINISHED'}


class MPM_OT_lattice_remove_selected_objects(bpy.types.Operator):
    bl_idname = "camera.lattice_remove_selected_objects"
    bl_label = "登録削除"
    bl_description = "3Dビュー上で選択中の登録OBJを削除候補にします"

    def execute(self, context):
        lattice_set = _get_active_lattice_set(context.scene)
        if lattice_set is None or len(lattice_set.objects) == 0:
            self.report({'WARNING'}, "削除候補にする登録OBJがありません")
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
            self.report({'WARNING'}, "削除候補にできる選択中OBJがありません")
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
            self.report({'WARNING'}, "削除取消できる登録OBJがありません")
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
    bl_label = "ラティスを登録OBJに合わせる"
    bl_description = "現在セットのラティスOBJを登録OBJ群のバウンディングBOXに合わせます"

    def execute(self, context):
        lattice_set = _get_active_lattice_set(context.scene)
        success, message = _fit_lattice_object_to_registered_objects(lattice_set, margin=1.05)
        if not success:
            self.report({'WARNING'}, message)
            return {'CANCELLED'}
        self.report({'INFO'}, message)
        return {'FINISHED'}


def _remove_current_set_modifier_from_object_name(lattice_set, object_name):
    """指定OBJ名から現在セット用の管理ラティスMODだけを削除する。"""
    obj = bpy.data.objects.get(str(object_name or ""))
    if obj is None or not _is_modifier_area_supported(obj):
        return 0
    removed = 0
    for mod in list(getattr(obj, "modifiers", []) or []):
        if _is_managed_lattice_modifier_for_set(mod, lattice_set):
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
    bl_description = "登録OBJへ指定ラティスのラティス管理用モディファイアを追加または更新します"

    def execute(self, context):
        lattice_set = _get_active_lattice_set(context.scene)
        if lattice_set is None:
            self.report({'WARNING'}, "登録セットがありません")
            return {'CANCELLED'}
        removed_items, removed_mods = _finalize_lattice_delete_candidates(lattice_set)
        lattice_obj = getattr(lattice_set, "lattice_obj", None)
        if lattice_obj is None:
            if removed_items > 0:
                self.report({'INFO'}, f"削除候補を確定しました / 登録削除:{removed_items} MOD削除:{removed_mods}")
                return {'FINISHED'}
            self.report({'WARNING'}, "ラティスOBJが未指定です")
            return {'CANCELLED'}
        if len(lattice_set.objects) == 0:
            if removed_items > 0:
                self.report({'INFO'}, f"削除候補を確定しました / 登録削除:{removed_items} MOD削除:{removed_mods}")
                return {'FINISHED'}
            self.report({'WARNING'}, "登録OBJがありません")
            return {'CANCELLED'}
        _ensure_set_uid(lattice_set)
        _ensure_object_mode(context)
        added = 0
        updated = 0
        skipped = 0
        mod_name = _modifier_name_for_set(lattice_set)
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
            duplicates_removed = _cleanup_duplicate_managed_lattice_modifiers(obj, lattice_set, mod)
            if not _set_modifier_lattice_object(mod, lattice_obj):
                skipped += 1
        self.report({'INFO'}, f"ラティスモディファイア処理 完了 / 登録削除:{removed_items} MOD削除:{removed_mods} 追加:{added} 更新:{updated} 除外:{skipped}")
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
    for obj in _iter_registered_existing_objects(lattice_set):
        mod = _find_managed_lattice_modifier(obj, lattice_set)
        if mod is None:
            continue
        _tag_lattice_modifier_for_set(mod, lattice_set)
        _cleanup_duplicate_managed_lattice_modifiers(obj, lattice_set, mod)
        try:
            mod.show_viewport = bool(enabled)
            mod.show_render = bool(enabled)
            count += 1
        except Exception:
            pass
    return count


def _delete_managed_modifiers_for_set(lattice_set):
    """現在セットの管理MODだけを登録OBJから削除する。別セットのMODは残す。"""
    if lattice_set is None:
        return 0
    count = 0
    for obj in _iter_registered_existing_objects(lattice_set):
        if not _is_modifier_area_supported(obj):
            continue
        for candidate in list(getattr(obj, "modifiers", []) or []):
            if _is_managed_lattice_modifier_for_set(candidate, lattice_set):
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
    body = _draw_subpanel(layout, "mpm_lattice_selected_objects_panel", "選択中OBJ")
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
        empty.label(text="選択中OBJはありません")

    add_row = body.row(align=True)
    add_row.enabled = lattice_set is not None and count > 0
    add_row.operator("camera.lattice_register_selected_objects", text="登録OBJに追加")

def _draw_registered_objects_panel(layout, context, lattice_set):
    """登録OBJの一覧と削除ボタンを描く。"""
    body = _draw_subpanel(layout, "mpm_lattice_registered_objects_panel", "登録OBJ")
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
        empty.label(text="登録OBJはありません")

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
    managed_count = _count_managed_modifiers(lattice_set) if lattice_set is not None else 0
    mod_name = _modifier_name_for_set(lattice_set) if lattice_set is not None else "未作成"

    if lattice_obj is not None:
        body.label(text=f"対象ラティス：{lattice_obj.name}")
    else:
        disabled = body.row(align=True)
        disabled.enabled = False
        disabled.label(text="対象ラティス：未指定")
    body.label(text=f"モディファイア名：{mod_name}")

    ready = lattice_set is not None and lattice_obj is not None and registered_count > 0 and valid_count > 0

    fit_row = body.row(align=True)
    fit_row.enabled = ready
    fit_row.operator("camera.lattice_fit_to_registered_objects", text="ラティスを登録OBJに合わせる")

    apply_row = body.row(align=True)
    apply_row.scale_y = 1.2
    apply_row.enabled = ready
    apply_row.operator("camera.lattice_apply_or_update_modifiers", text="追加 / 更新")

    mod_ready = lattice_set is not None and managed_count > 0
    row = body.row(align=True)
    row.enabled = mod_ready
    row.operator("camera.lattice_enable_modifiers", text="有効")
    row.operator("camera.lattice_disable_modifiers", text="無効")

    delete_row = body.row(align=True)
    delete_row.enabled = mod_ready
    delete_row.operator("camera.lattice_delete_modifiers", text="削除", icon='TRASH')

def _draw_status_panel(layout, context, lattice_set):
    """現在セットの状態を描く。"""
    body = _draw_subpanel(layout, "mpm_lattice_status_panel", "状態")
    if body is None:
        return
    if lattice_set is None:
        disabled = body.column(align=True)
        disabled.enabled = False
        disabled.label(text="登録OBJ：0")
        disabled.label(text="モディファイアあり：0 / 0")
        disabled.label(text="ラティス：未指定")
        return

    registered_total = len(lattice_set.objects)
    valid_total = _valid_registered_object_count(lattice_set)
    managed_total = _count_managed_modifiers(lattice_set)
    lattice_obj = getattr(lattice_set, "lattice_obj", None)

    body.label(text=f"登録OBJ：{registered_total}")
    if registered_total != valid_total:
        warn = body.row(align=True)
        warn.alert = True
        warn.label(text=f"存在するOBJ：{valid_total} / {registered_total}")
    body.label(text=f"モディファイアあり：{managed_total} / {valid_total}")

    lattice_row = body.row(align=True)
    lattice_row.enabled = lattice_obj is not None
    lattice_row.label(text="ラティス：指定済み" if lattice_obj is not None else "ラティス：未指定")


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
        _ensure_active_lattice_index(scene)
        lattice_set = _get_active_lattice_set(scene)
        _draw_lattice_set_header(layout, context, lattice_set)
        if lattice_set is None:
            return
        _draw_selected_objects_panel(layout, context, lattice_set)
        _draw_registered_objects_panel(layout, context, lattice_set)
        _draw_modifier_management_panel(layout, context, lattice_set)
        _draw_status_panel(layout, context, lattice_set)


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
    bpy.types.WindowManager.mpm_lattice_selected_display_items = bpy.props.CollectionProperty(type=MPM_LatticeSelectedObjectDisplayItem)
    bpy.types.WindowManager.mpm_lattice_selected_display_index = bpy.props.IntProperty(default=0, options={'SKIP_SAVE'})

def unregister_lattice_manager():
    """ラティス管理セクションを解除する。"""
    for attr in (
        "mpm_lattice_sets",
        "mpm_lattice_active_set_index",
        "mpm_lattice_active_set_enum",
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
# Version Footer: 1.154
# -------------------------------
