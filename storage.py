# -*- coding: utf-8 -*-
# ファイル名：storage.py
# 00漫画用Camera Position Manager
# 機能：保存データとパス解決の共通処理
# 変更点（1.186）:
# - 付随データUI調整に合わせてバージョン更新

import json
import os
import unicodedata
from datetime import datetime

import bpy

_ENUM_CACHE: list[tuple[str, str, str]] = []


def rebuild_enum_cache(manager) -> None:
    _ENUM_CACHE.clear()
    for i, data in enumerate(manager.saved_camera_data):
        name = unicodedata.normalize("NFC", str(data.get('bg_image', 'No File')))
        label = f"{i + 1}: {name}"
        _ENUM_CACHE.append((str(i), label, ""))


def safe_basename(filepath: str) -> str:
    if not filepath:
        return "No File"
    try:
        full = bpy.path.abspath(filepath)
    except Exception:
        full = filepath
    full = bpy.path.native_pathsep(full)
    return unicodedata.normalize("NFC", str(os.path.basename(full)))


def _normalize_dirpath(dirpath: str) -> str:
    try:
        return bpy.path.native_pathsep(bpy.path.abspath(dirpath))
    except Exception:
        return bpy.path.native_pathsep(dirpath or "")


def _safe_existing_dirpath(dirpath: str, fallback: str = None) -> str:
    base = fallback if fallback is not None else os.path.expanduser("~")
    candidate = _normalize_dirpath(dirpath or "")
    if candidate and os.path.isdir(candidate):
        return candidate
    base = _normalize_dirpath(base or os.path.expanduser("~"))
    if base and os.path.isdir(base):
        return base
    return os.path.expanduser("~")


def _safe_json_path(filepath: str, fallback_filename: str = "camera_positions.json") -> str:
    try:
        path = bpy.path.native_pathsep(bpy.path.abspath(filepath or ""))
    except Exception:
        path = bpy.path.native_pathsep(filepath or "")
    if not path:
        return os.path.join(bpy.utils.user_resource('CONFIG'), fallback_filename)
    _root, ext = os.path.splitext(path)
    if not ext:
        path = path + '.json'
    return path


def _default_json_dialog_filepath(manager, fallback_filename: str = "camera_positions.json") -> str:
    base_path = _safe_json_path(getattr(manager, 'save_file_path', ''))
    default_dir = _safe_existing_dirpath(getattr(manager, 'output_folder_path', ''), fallback=os.path.expanduser("~"))
    folder_path = _safe_existing_dirpath(
        getattr(manager, 'background_image_folder_path', ''),
        fallback=default_dir,
    )
    folder_name = os.path.basename(folder_path.rstrip("\\/")) if folder_path else ""
    folder_name = unicodedata.normalize("NFC", str(folder_name or "")).strip()
    if not folder_name:
        folder_name = "camera_positions"
    today_label = datetime.now().strftime("%Y_%m%d")
    filename = f"{folder_name}_3D座標データ_[{today_label}].json"
    return os.path.join(default_dir, filename)


def _write_json_atomic(filepath: str, data: dict) -> None:
    path = _safe_json_path(filepath)
    folder = os.path.dirname(path) or bpy.utils.user_resource('CONFIG')
    os.makedirs(folder, exist_ok=True)
    tmp_path = path + '.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp_path, path)


def _resolve_bg_image_path(manager, filename: str) -> str:
    fname = unicodedata.normalize("NFC", str(filename or "")).strip()
    if not fname or fname == "No File":
        return ""
    dirpath = _normalize_dirpath(getattr(manager, "background_image_folder_path", ""))
    if not dirpath:
        return ""
    return os.path.join(dirpath, fname)


def _lattice_state_signature(value) -> tuple:
    """ラティス状態辞書を重複判定に使える安定したタプルへ変換する。"""
    if not isinstance(value, dict):
        return tuple()
    set_states = []
    for item in value.get("set_states", []) or []:
        if not isinstance(item, dict):
            continue
        set_states.append((
            str(item.get("set_uid", "") or ""),
            str(item.get("set_name", "") or ""),
            bool(item.get("modifiers_enabled", True)),
        ))
    return (
        bool(value.get("lattice_enabled", False)),
        bool(value.get("multi_set_enabled", False)),
        str(value.get("active_set_uid", "") or ""),
        str(value.get("active_set_name", "") or ""),
        tuple(set_states),
    )


def _normalize_lattice_state(value, fallback_enabled=False) -> dict:
    """保存用ラティス状態を安全な辞書へ正規化する。"""
    if not isinstance(value, dict):
        value = {}
    out = {
        "lattice_enabled": bool(value.get("lattice_enabled", fallback_enabled)),
        "multi_set_enabled": bool(value.get("multi_set_enabled", False)),
        "active_set_uid": str(value.get("active_set_uid", "") or ""),
        "active_set_name": str(value.get("active_set_name", "") or ""),
        "set_states": [],
    }
    for item in value.get("set_states", []) or []:
        if not isinstance(item, dict):
            continue
        out["set_states"].append({
            "set_uid": str(item.get("set_uid", "") or ""),
            "set_name": str(item.get("set_name", "") or ""),
            "modifiers_enabled": bool(item.get("modifiers_enabled", True)),
        })
    return out


def _view_layer_exclude_state_signature(value) -> tuple:
    """ビューレイヤー除外状態を重複判定用の安定したタプルへ変換する。"""
    if not isinstance(value, dict):
        return tuple()
    items = []
    for item in value.get("collections", []) or []:
        if not isinstance(item, dict):
            continue
        path_value = item.get("path", [])
        if isinstance(path_value, str):
            path_tuple = tuple(part for part in path_value.split("/") if part)
        else:
            try:
                path_tuple = tuple(str(part) for part in path_value)
            except Exception:
                path_tuple = tuple()
        items.append((
            path_tuple,
            str(item.get("collection_name", "") or ""),
            bool(item.get("exclude", False)),
        ))
    return (
        str(value.get("view_layer_name", "") or ""),
        tuple(items),
    )


def _normalize_view_layer_exclude_state(value) -> dict:
    """保存用ビューレイヤー除外状態を安全な辞書へ正規化する。"""
    if not isinstance(value, dict):
        value = {}
    out = {
        "view_layer_name": str(value.get("view_layer_name", "") or ""),
        "collections": [],
    }
    for item in value.get("collections", []) or []:
        if not isinstance(item, dict):
            continue
        path_value = item.get("path", [])
        if isinstance(path_value, str):
            path_list = [part for part in path_value.split("/") if part]
        else:
            try:
                path_list = [str(part) for part in path_value]
            except Exception:
                path_list = []
        collection_name = str(item.get("collection_name", "") or "")
        if not path_list and collection_name:
            path_list = [collection_name]
        if not path_list:
            continue
        out["collections"].append({
            "path": path_list,
            "collection_name": collection_name or path_list[-1],
            "exclude": bool(item.get("exclude", False)),
        })
    return out


def _stock_signature(item: dict) -> tuple:
    if not isinstance(item, dict):
        return tuple()
    selected_objects = []
    for obj in item.get("selected_objects", ()) or ():
        if not isinstance(obj, dict):
            continue
        selected_objects.append(
            (
                obj.get("name"),
                tuple(obj.get("location", ())),
                tuple(obj.get("rotation", ())),
                tuple(obj.get("scale", ())),
            )
        )
    return (
        tuple(item.get("position", ())),
        tuple(item.get("rotation", ())),
        item.get("resolution_x"),
        item.get("resolution_y"),
        item.get("focal_length"),
        item.get("bg_image"),
        item.get("bg_opacity"),
        item.get("bg_depth"),
        item.get("frame_current"),
        bool(item.get("record_selected_objects", False)),
        tuple(selected_objects),
        bool(item.get("lattice_enabled", False)),
        _lattice_state_signature(item.get("lattice_state")),
        bool(item.get("record_view_layer_exclude_state", False)),
        _view_layer_exclude_state_signature(item.get("view_layer_exclude_state")),
    )


def _saved_value(data: dict, key: str, default=None):
    if not isinstance(data, dict):
        return default
    value = data.get(key, default)
    return default if value is None else value


def _normalize_saved_item(item) -> dict:
    if not isinstance(item, dict):
        item = {}

    def _vec3(value, fallback):
        try:
            seq = list(value)
        except Exception:
            return list(fallback)
        if len(seq) < 3:
            seq = seq + list(fallback)[len(seq):3]
        try:
            return [float(seq[0]), float(seq[1]), float(seq[2])]
        except Exception:
            return list(fallback)

    out = {
        'position': _vec3(item.get('position', (0.0, 0.0, 0.0)), (0.0, 0.0, 0.0)),
        'rotation': _vec3(item.get('rotation', (0.0, 0.0, 0.0)), (0.0, 0.0, 0.0)),
        'resolution_x': int(item.get('resolution_x', 1920) or 1920),
        'resolution_y': int(item.get('resolution_y', 1080) or 1080),
        'focal_length': float(item.get('focal_length', 50.0) or 50.0),
        'bg_image': unicodedata.normalize('NFC', str(item.get('bg_image', 'No File') or 'No File')),
        'bg_opacity': float(item.get('bg_opacity', 1.0) or 1.0),
        'bg_depth': str(item.get('bg_depth', 'BACK') or 'BACK'),
        'frame_current': int(item.get('frame_current', 1) or 1),
        'memo': str(item.get('memo', '') or ''),
        'record_selected_objects': bool(item.get('record_selected_objects', False)),
        'selected_objects': [],
        'lattice_enabled': bool(item.get('lattice_enabled', False)),
        'lattice_state': _normalize_lattice_state(item.get('lattice_state'), bool(item.get('lattice_enabled', False))),
        'record_view_layer_exclude_state': bool(item.get('record_view_layer_exclude_state', False)),
        'view_layer_exclude_state': _normalize_view_layer_exclude_state(item.get('view_layer_exclude_state')),
    }
    selected_objects = item.get('selected_objects', [])
    if isinstance(selected_objects, list):
        for obj in selected_objects:
            if not isinstance(obj, dict):
                continue
            out['selected_objects'].append({
                'name': str(obj.get('name', '') or ''),
                'location': _vec3(obj.get('location', (0.0, 0.0, 0.0)), (0.0, 0.0, 0.0)),
                'rotation': _vec3(obj.get('rotation', (0.0, 0.0, 0.0)), (0.0, 0.0, 0.0)),
                'scale': _vec3(obj.get('scale', (1.0, 1.0, 1.0)), (1.0, 1.0, 1.0)),
            })
    if 'created_at' in item:
        out['created_at'] = item.get('created_at')
    return out


def _normalize_saved_list(items) -> list:
    if not isinstance(items, list):
        return []
    normalized = []
    for item in items:
        try:
            normalized.append(_normalize_saved_item(item))
        except Exception:
            normalized.append(_normalize_saved_item({}))
    return normalized


def _ensure_manager_saved_data_normalized(manager) -> list:
    if manager is None:
        return []
    try:
        current = getattr(manager, 'saved_camera_data', [])
    except Exception:
        current = []
    normalized = _normalize_saved_list(current)
    try:
        manager.saved_camera_data = normalized
    except Exception:
        pass
    return normalized


def _append_unique_saved_items(existing_items, incoming_items):
    base = list(_normalize_saved_list(existing_items))
    incoming = _normalize_saved_list(incoming_items)
    seen = set()
    result = []
    for item in base:
        sig = _stock_signature(item)
        result.append(item)
        if sig:
            seen.add(sig)
    added_count = 0
    skipped_count = 0
    for item in incoming:
        sig = _stock_signature(item)
        if sig and sig in seen:
            skipped_count += 1
            continue
        result.append(item)
        added_count += 1
        if sig:
            seen.add(sig)
    return result, added_count, skipped_count


def _find_matching_saved_item_index(manager, new_item) -> int:
    try:
        target_sig = _stock_signature(_normalize_saved_item(new_item))
    except Exception:
        target_sig = None
    if not target_sig:
        return -1
    items = _ensure_manager_saved_data_normalized(manager)
    for i, item in enumerate(items):
        try:
            sig = _stock_signature(item)
        except Exception:
            sig = None
        if sig == target_sig:
            return i
    return -1


def ensure_valid_saved_enum(scene, manager):
    try:
        n = len(manager.saved_camera_data)
    except Exception:
        n = 0
    if n <= 0:
        return
    valid_ids = {str(i) for i in range(n)}
    if getattr(scene, "saved_camera_index", None) not in valid_ids:
        scene.saved_camera_index = "0"


def _get_saved_item_safe(manager, index: int, default=None):
    items = _ensure_manager_saved_data_normalized(manager)
    try:
        idx = int(index)
    except Exception:
        return default
    if 0 <= idx < len(items):
        return items[idx]
    return default


def _safe_saved_index(scene, manager, default=0) -> int:
    try:
        n = len(getattr(manager, 'saved_camera_data', []) or [])
    except Exception:
        n = 0
    if n <= 0:
        return 0
    try:
        idx = int(getattr(scene, 'saved_camera_index', str(default)) or default)
    except Exception:
        idx = int(default)
    return max(0, min(idx, n - 1))


def _sync_scene_saved_memo(scene, manager) -> None:
    try:
        items = _ensure_manager_saved_data_normalized(manager)
    except Exception:
        items = []
    memo_text = ""
    if items:
        idx = _safe_saved_index(scene, manager)
        data = _get_saved_item_safe(manager, idx, default={}) or {}
        try:
            memo_text = str(_saved_value(data, 'memo', '') or '')
        except Exception:
            memo_text = ""
    try:
        scene.saved_memo_text = memo_text
    except Exception:
        pass


def _set_saved_camera_index_safe(scene, manager, redraw_callback, index: int | None = 0) -> None:
    try:
        total = len(getattr(manager, 'saved_camera_data', []) or [])
    except Exception:
        total = 0
    if total <= 0:
        try:
            scene.saved_camera_index = "0"
        except Exception:
            pass
        redraw_callback()
        return
    try:
        idx = 0 if index is None else int(index)
    except Exception:
        idx = 0
    idx = max(0, min(idx, total - 1))
    try:
        scene.saved_camera_index = str(idx)
    except Exception:
        pass
    redraw_callback()
