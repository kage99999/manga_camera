# -*- coding: utf-8 -*-
# ファイル名：lattice_stock.py
# 00漫画用Camera Position Manager
# ラティス管理セクション ストック保存/復元
# 変更点（1.175）:
# - lattice_manager.py からストック保存/復元処理を分離

from . import lattice_manager as _lm

_get_lattice_sets = _lm._get_lattice_sets
_ensure_set_uid = _lm._ensure_set_uid
_get_active_lattice_set = _lm._get_active_lattice_set
_safe_name = _lm._safe_name
_ensure_active_lattice_index = _lm._ensure_active_lattice_index
apply_lattice_set_activation_state = _lm.apply_lattice_set_activation_state
apply_lattice_management_enabled = _lm.apply_lattice_management_enabled
_active_lattice_set_uid = _lm._active_lattice_set_uid
_is_lattice_management_enabled = _lm._is_lattice_management_enabled
_is_lattice_multi_set_enabled = _lm._is_lattice_multi_set_enabled

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




__all__ = ["export_lattice_stock_state", "apply_lattice_stock_state"]

# -------------------------------
# ファイル名：lattice_stock.py
# Version Footer: 1.175
# -------------------------------
