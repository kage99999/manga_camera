# -*- coding: utf-8 -*-
# ファイル名：storage.py
# 00漫画用Camera Position Manager
# 機能：保存データとパス解決の共通処理
# 変更点（1.203）:
# - JSON形式検証、比較の事前絞込、ストック単位の高速JSON書込

import json
import os
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime

import bpy
from .transforms import normalize_rotation, rotation_signature
from .pose_stock import normalize_pose_fields, pose_signature

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


@contextmanager
def _atomic_writer(path, mode='wb', encoding=None):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    tmp_path = None
    try:
        candidate = os.path.join(folder, '.mpm-' + uuid.uuid4().hex + '.tmp')
        # Exclusive creation fails immediately on permission errors on Windows.
        with open(candidate, mode='x' + mode[1:], encoding=encoding) as handle:
            tmp_path = candidate
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path is not None and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _write_json_atomic(filepath: str, data: dict) -> None:
    path = _safe_json_path(filepath)
    with _atomic_writer(path, mode='w', encoding='utf-8') as f:
        # Encode one stock at a time: fast encoder, without a second full-file string.
        f.write('{')
        for index, (key, value) in enumerate(data.items()):
            if index:
                f.write(', ')
            f.write(json.dumps(key, ensure_ascii=False) + ': ')
            if isinstance(value, list):
                f.write('[')
                for item_index, item in enumerate(value):
                    if item_index:
                        f.write(', ')
                    f.write(json.dumps(item, ensure_ascii=False))
                f.write(']')
            else:
                f.write(json.dumps(value, ensure_ascii=False))
        f.write('}')


def _read_stock_json(filepath, stock_key):
    with open(filepath, 'r', encoding='utf-8-sig') as handle:
        data = json.load(handle)
    other_key = 'all_object_data' if stock_key == 'camera_data' else 'camera_data'
    if (not isinstance(data, dict) or stock_key not in data or other_key in data
            or not isinstance(data[stock_key], list)
            or any(not isinstance(item, dict) for item in data[stock_key])):
        label = '通常ストック' if stock_key == 'camera_data' else '全OBJデータ'
        raise ValueError(f'{label}用のJSONではありません。現在のデータは変更していません')
    return data


def _load_initial_stock_data(manager):
    try:
        manager.load_data(create_if_missing=True)
    except Exception as exc:
        # Keep registration usable without allowing the next record to destroy a bad file.
        manager._blocked_save_path = manager.save_file_path
        print(f'記録JSONを読み込めませんでした。別名で保存するか正常なJSONを読み込んでください: {exc}')


def _check_stock_save_target(manager, target_path, explicit_path):
    blocked = getattr(manager, '_blocked_save_path', None)
    if blocked and not explicit_path and os.path.normcase(target_path) == os.path.normcase(blocked):
        raise ValueError('読込に失敗したJSONを保護しています。別名で保存するか正常なJSONを読み込んでください')


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
                rotation_signature(obj),
                tuple(obj.get("scale", ())),
                pose_signature(obj),
            )
        )
    return (
        tuple(item.get("position", ())),
        rotation_signature(item),
        item.get("resolution_x"),
        item.get("resolution_y"),
        item.get("focal_length"),
        item.get("bg_image"),
        item.get("bg_opacity"),
        item.get("bg_depth"),
        bool(item.get("bg_visible", item.get("bg_image") not in ("", "No File"))),
        item.get("frame_current"),
        str(item.get("memo", '') or ''),
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

    bg_image_value = unicodedata.normalize('NFC', str(item.get('bg_image', 'No File') or 'No File'))
    bg_visible_value = item.get('bg_visible', bg_image_value not in ('', 'No File'))
    if bg_visible_value is None:
        bg_visible_value = bg_image_value not in ('', 'No File')

    out = {
        'position': _vec3(item.get('position', (0.0, 0.0, 0.0)), (0.0, 0.0, 0.0)),
        **normalize_rotation(item),
        'resolution_x': int(item.get('resolution_x', 1920) or 1920),
        'resolution_y': int(item.get('resolution_y', 1080) or 1080),
        'focal_length': float(item.get('focal_length', 50.0) or 50.0),
        'bg_image': bg_image_value,
        'bg_opacity': float(_saved_value(item, 'bg_opacity', 1.0)),
        'bg_depth': str(item.get('bg_depth', 'BACK') or 'BACK'),
        'bg_visible': bool(bg_visible_value),
        'frame_current': int(_saved_value(item, 'frame_current', 1)),
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
                **normalize_rotation(obj),
                'scale': _vec3(obj.get('scale', (1.0, 1.0, 1.0)), (1.0, 1.0, 1.0)),
                **normalize_pose_fields(obj),
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
    # Manager load/save commits own normalized lists. Reads reuse that snapshot.
    if isinstance(current, list) and current is getattr(manager, '_normalized_saved_data', None):
        return current
    normalized = _normalize_saved_list(current)
    try:
        manager.saved_camera_data = normalized
        manager._normalized_saved_data = normalized
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


def _stock_comparison_prefix(item):
    # These fields are necessary for a match, and do not traverse any pose bones.
    return (
        tuple(item.get('position', ())), rotation_signature(item),
        item.get('resolution_x'), item.get('resolution_y'), item.get('focal_length'),
        item.get('bg_image'), item.get('bg_opacity'), item.get('bg_depth'),
        bool(item.get('bg_visible', item.get('bg_image') not in ('', 'No File'))),
        item.get('frame_current'), str(item.get('memo', '') or ''),
        bool(item.get('record_selected_objects', False)),
        bool(item.get('lattice_enabled', False)),
        bool(item.get('record_view_layer_exclude_state', False)),
    )


def _find_matching_saved_item_index(manager, new_item) -> int:
    try:
        normalized = _normalize_saved_item(new_item)
        prefix = _stock_comparison_prefix(normalized)
    except Exception:
        return -1
    target_sig = None
    items = _ensure_manager_saved_data_normalized(manager)
    for i, item in enumerate(items):
        try:
            if _stock_comparison_prefix(item) != prefix:
                continue
            if target_sig is None:
                target_sig = _stock_signature(normalized)
            sig = _stock_signature(item)
        except Exception:
            sig = None
        if sig is not None and sig == target_sig:
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
