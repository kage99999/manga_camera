# -*- coding: utf-8 -*-
# ファイル名：background.py
# 00漫画用Camera Position Manager
# 機能：下絵画像と背景表示の共通処理

import os
import re
import unicodedata

import bpy

from .storage import (
    _ensure_manager_saved_data_normalized,
    _resolve_bg_image_path,
    _safe_existing_dirpath,
    _saved_value,
    safe_basename,
)


def _load_image_safe(filepath: str):
    if not filepath:
        return None
    try:
        abs_path = bpy.path.abspath(filepath)
    except Exception:
        abs_path = filepath
    abs_path = bpy.path.native_pathsep(abs_path)
    if not abs_path or not os.path.exists(abs_path):
        return None
    try:
        return bpy.data.images.load(abs_path, check_existing=True)
    except TypeError:
        pass
    except Exception:
        return None

    norm_target = os.path.normcase(os.path.normpath(abs_path))
    for img in bpy.data.images:
        try:
            existing = bpy.path.native_pathsep(bpy.path.abspath(img.filepath))
        except Exception:
            existing = getattr(img, "filepath", "")
        if existing and os.path.normcase(os.path.normpath(existing)) == norm_target:
            return img
    try:
        return bpy.data.images.load(abs_path)
    except Exception:
        return None


def _natural_sort_key(value: str):
    s = unicodedata.normalize("NFC", str(value or ""))
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r'(\d+)', s)]


def _iter_folder_image_paths(dirpath: str):
    path = _safe_existing_dirpath(dirpath, fallback="")
    if not path or not os.path.isdir(path):
        return []
    exts = {'.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff', '.bmp'}
    items = []
    try:
        for name in os.listdir(path):
            full = os.path.join(path, name)
            if not os.path.isfile(full):
                continue
            if os.path.splitext(name)[1].lower() not in exts:
                continue
            items.append(full)
    except Exception:
        return []
    items.sort(key=lambda fp: _natural_sort_key(os.path.basename(fp)))
    return items


def _stocked_bg_name_set(manager):
    result = set()
    for item in _ensure_manager_saved_data_normalized(manager):
        try:
            name = safe_basename(_saved_value(item, 'bg_image', ''))
        except Exception:
            name = ''
        if name and name != 'No File':
            result.add(unicodedata.normalize('NFC', name).casefold())
    return result


def _frame_from_filename_path(path: str):
    try:
        base = os.path.basename(path)
        name, _ext = os.path.splitext(base)
        tail = name[-6:]
        return int(tail) if tail.isdigit() else None
    except Exception:
        return None


def _get_or_create_background_slot(camera):
    if not camera or not getattr(camera, "data", None):
        return None
    try:
        slots = camera.data.background_images
    except Exception:
        return None
    try:
        if slots and len(slots) > 0:
            return slots[0]
    except Exception:
        pass
    try:
        return slots.new()
    except Exception:
        return None


def _set_background_visibility(camera, bg, visible: bool = True) -> None:
    v = bool(visible)
    if camera and getattr(camera, "data", None):
        try:
            camera.data.show_background_images = v
        except Exception:
            pass
        try:
            camera.data.mpm_bg_visible = v
        except Exception:
            pass
    if bg is not None:
        try:
            if hasattr(bg, 'show_background_image'):
                bg.show_background_image = v
        except Exception:
            pass


def _apply_background_display_settings(bg, data) -> None:
    if bg is None:
        return
    bg_opacity = data.get('bg_opacity', 1.0) if isinstance(data, dict) else 1.0
    try:
        if hasattr(bg, "opacity"):
            bg.opacity = float(bg_opacity)
        elif hasattr(bg, "alpha"):
            bg.alpha = float(bg_opacity)
    except Exception:
        pass
    try:
        bg.display_depth = str(data.get('bg_depth', 'BACK')) if isinstance(data, dict) else 'BACK'
    except Exception:
        pass


def _apply_saved_background_to_camera(camera, manager, data) -> None:
    bg = _get_or_create_background_slot(camera)
    if bg is None:
        return
    image_path = _resolve_bg_image_path(manager, data.get('bg_image', ""))
    image = _load_image_safe(image_path) if image_path else None
    bg.image = image
    _apply_background_display_settings(bg, data if isinstance(data, dict) else {})
    _set_background_visibility(camera, bg, bool(image is not None or data.get('bg_image', '') not in ('', 'No File')))


def _set_background_image_from_path(scene, camera, manager, filepath: str, redraw_callback, update_resolution: bool = False, update_frame: bool = False):
    if not scene or not camera or not getattr(camera, 'data', None):
        raise RuntimeError('カメラがありません')
    image = _load_image_safe(filepath)
    if image is None:
        raise RuntimeError('画像ファイルが見つからないか、読み込めませんでした')
    bg = _get_or_create_background_slot(camera)
    if bg is None:
        raise RuntimeError('下絵スロットを作成できませんでした')
    bg.image = image
    try:
        bg.display_depth = 'FRONT'
    except Exception:
        pass
    _set_background_visibility(camera, bg, True)
    if update_resolution:
        try:
            if image.size[0] > 0 and image.size[1] > 0:
                scene.render.resolution_x = image.size[0]
                scene.render.resolution_y = image.size[1]
        except Exception:
            pass
    if update_frame:
        try:
            fnum = _frame_from_filename_path(filepath)
            if fnum is not None:
                scene.frame_set(int(fnum))
        except Exception:
            pass
    manager.background_image_folder_path = _safe_existing_dirpath(os.path.dirname(filepath), fallback=manager.background_image_folder_path)
    try:
        manager.save_data()
    except Exception:
        pass
    redraw_callback()
    return image


def _find_next_folder_image_path(manager, current_name: str = '', step: int = 1, skip_stocked: bool = False):
    paths = _iter_folder_image_paths(getattr(manager, 'background_image_folder_path', ''))
    if not paths:
        return '', 0
    if skip_stocked:
        stocked = _stocked_bg_name_set(manager)
        paths = [p for p in paths if unicodedata.normalize('NFC', os.path.basename(p)).casefold() not in stocked]
        if not paths:
            return '', 0
    names = [unicodedata.normalize('NFC', os.path.basename(p)) for p in paths]
    cur = unicodedata.normalize('NFC', str(current_name or ''))
    if cur in names:
        idx = names.index(cur)
        next_idx = (idx + (1 if step >= 0 else -1)) % len(paths)
        return paths[next_idx], len(paths)
    return paths[0], len(paths)
