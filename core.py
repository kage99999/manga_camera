# -*- coding: utf-8 -*-
# ファイル名：core.py
# 00漫画用Camera Position Manager
# 変更点（1.106）:
# - ドリーズーム有効中の焦点距離手動入力を無効化
# - ストック適用処理の例外整理とUI/機能の現状維持

import bpy
import os
import sys
import json
import time
import subprocess
import unicodedata
import re
from bpy.app.handlers import persistent

# =========================
# バージョン文字列（UI表示用）
# =========================
def _addon_version_str() -> str:
    """アドオンのversionから '1.053' のような表記を作る"""
    v = (1, 0, 103)  # 1.106
    try:
        a, b, c = int(v[0]), int(v[1]), int(v[2])
    except Exception:
        return '0.000'
    # 既存運用は (1,0,53) → 1.053 の表記
    if b == 0:
        return f"{a}.{c:03d}"
    return f"{a}.{b}.{c:03d}"

ADDON_VERSION_STR = _addon_version_str()
PANEL_LABEL = f"カメラコントロール Ver.{ADDON_VERSION_STR}"
ADDON_PACKAGE_NAME = (__package__ or __name__.split(".")[0])
_CAMERA_DATA_MANAGER = None
_POSTLOAD_FIX_PENDING = False


# =========================
# 内部ユーティリティ
# =========================
_ENUM_CACHE: list[tuple[str, str, str]] = []  # プルダウン用キャッシュ

def rebuild_enum_cache(manager) -> None:
    """ストック名（日本語対応済み）でEnumを再構築"""
    global _ENUM_CACHE
    items = []
    for i, data in enumerate(manager.saved_camera_data):
        name = unicodedata.normalize("NFC", str(data.get('bg_image', 'No File')))
        label = f"{i + 1}: {name}"
        items.append((str(i), label, ""))
    _ENUM_CACHE = items

def safe_basename(filepath: str) -> str:
    """パスからファイル名を取得（NFC正規化・日本語対応）"""
    if not filepath:
        return "No File"
    try:
        full = bpy.path.abspath(filepath)
    except Exception:
        full = filepath
    full = bpy.path.native_pathsep(full)
    name = os.path.basename(full)
    return unicodedata.normalize("NFC", str(name))


def _normalize_dirpath(dirpath: str) -> str:
    """ディレクトリ文字列をOS向けに正規化"""
    try:
        return bpy.path.native_pathsep(bpy.path.abspath(dirpath))
    except Exception:
        return bpy.path.native_pathsep(dirpath or "")


def _safe_existing_dirpath(dirpath: str, fallback: str = None) -> str:
    """存在するディレクトリだけを返し、無効ならフォールバックへ寄せる"""
    base = fallback if fallback is not None else os.path.expanduser("~")
    candidate = _normalize_dirpath(dirpath or "")
    if candidate and os.path.isdir(candidate):
        return candidate
    base = _normalize_dirpath(base or os.path.expanduser("~"))
    if base and os.path.isdir(base):
        return base
    return os.path.expanduser("~")


def _safe_json_path(filepath: str, fallback_filename: str = "camera_positions.json") -> str:
    """保存先JSONパスを安全な場所へ補正する"""
    try:
        path = bpy.path.native_pathsep(bpy.path.abspath(filepath or ""))
    except Exception:
        path = bpy.path.native_pathsep(filepath or "")
    if not path:
        return os.path.join(bpy.utils.user_resource('CONFIG'), fallback_filename)
    root, ext = os.path.splitext(path)
    if not ext:
        path = path + '.json'
    return path

def _is_timer_registered(func) -> bool:
    """Blenderタイマー登録済み判定を安全に行う"""
    try:
        checker = getattr(bpy.app.timers, "is_registered", None)
        return bool(checker(func)) if checker else False
    except Exception:
        return False


def _register_timer_once(func, first_interval: float = 0.0) -> bool:
    """同じ関数タイマーの二重登録を避けて登録する"""
    if _is_timer_registered(func):
        return False
    try:
        bpy.app.timers.register(func, first_interval=first_interval)
        return True
    except Exception:
        return False


def _default_json_dialog_filepath(manager, fallback_filename: str = "camera_positions.json") -> str:
    """ファイルブラウザ用の初期JSONパスを安全に組み立てる"""
    base_path = _safe_json_path(getattr(manager, 'save_file_path', ''))
    if base_path and os.path.splitext(base_path)[1].lower() == '.json':
        return base_path

    default_dir = _safe_existing_dirpath(
        getattr(manager, 'output_folder_path', ''),
        fallback=os.path.expanduser("~"),
    )
    return os.path.join(default_dir, fallback_filename)


def _write_json_atomic(filepath: str, data: dict) -> None:
    """JSONをテンポラリ経由で安全に保存する"""
    path = _safe_json_path(filepath)
    folder = os.path.dirname(path) or bpy.utils.user_resource('CONFIG')
    os.makedirs(folder, exist_ok=True)
    tmp_path = path + '.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp_path, path)


def _resolve_bg_image_path(manager, filename: str) -> str:
    """保存済みファイル名と現在の読込フォルダから実ファイルパスを組み立てる"""
    fname = unicodedata.normalize("NFC", str(filename or "")).strip()
    if not fname or fname == "No File":
        return ""
    dirpath = _normalize_dirpath(getattr(manager, "background_image_folder_path", ""))
    if not dirpath:
        return ""
    return os.path.join(dirpath, fname)


def _load_image_safe(filepath: str):
    """同一画像の重複読込を避けつつ画像を取得する"""
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
        # 古い環境向けフォールバック
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
    """ファイル名の自然順ソート用キー"""
    s = unicodedata.normalize("NFC", str(value or ""))
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r'(\d+)', s)]


def _iter_folder_image_paths(dirpath: str):
    """指定フォルダ内の画像ファイルを自然順で返す"""
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
    """保存ストックに登録済みの下絵ファイル名セット"""
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
    """ファイル名末尾6桁の数字を現在フレーム用に返す"""
    try:
        base = os.path.basename(path)
        name, _ext = os.path.splitext(base)
        tail = name[-6:]
        return int(tail) if tail.isdigit() else None
    except Exception:
        return None


def _set_background_image_from_path(scene, camera, manager, filepath: str, update_resolution: bool = False, update_frame: bool = False):
    """画像パスから現在のカメラ下絵だけを切り替える"""
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
    _tag_redraw_all_areas()
    return image


def _find_next_folder_image_path(manager, current_name: str = '', step: int = 1, skip_stocked: bool = False):
    """読込場所設定フォルダから次/前に読む画像を探す"""
    paths = _iter_folder_image_paths(getattr(manager, 'background_image_folder_path', ''))
    if not paths:
        return '', 0

    if skip_stocked:
        stocked = _stocked_bg_name_set(manager)
        filtered = [p for p in paths if unicodedata.normalize('NFC', os.path.basename(p)).casefold() not in stocked]
        paths = filtered
        if not paths:
            return '', 0

    names = [unicodedata.normalize('NFC', os.path.basename(p)) for p in paths]
    cur = unicodedata.normalize('NFC', str(current_name or ''))
    if cur in names:
        idx = names.index(cur)
        next_idx = (idx + (1 if step >= 0 else -1)) % len(paths)
        return paths[next_idx], len(paths)

    return paths[0], len(paths)


def _get_or_create_background_slot(camera):
    """先頭の下絵スロットを安全に取得"""
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
    """カメラ側とスロット側の下絵表示をまとめて同期"""
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
    """不透明度と深度だけを安全に反映"""
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
    """保存済み下絵情報をカメラへ復元する"""
    bg = _get_or_create_background_slot(camera)
    if bg is None:
        return

    image_path = _resolve_bg_image_path(manager, data.get('bg_image', ""))
    image = _load_image_safe(image_path) if image_path else None
    bg.image = image
    _apply_background_display_settings(bg, data if isinstance(data, dict) else {})
    # 画像が見つからなくても、UI上の表示設定は保持しておく
    _set_background_visibility(camera, bg, bool(image is not None or data.get('bg_image', '') not in ('', 'No File')))

def _stock_signature(item: dict) -> tuple:
    """ストック1件の「同一判定用」シグネチャを作る（created_at は無視する）
    完全一致判定なので、値はそのままタプル化して比較する。
    """
    if not isinstance(item, dict):
        return tuple()

    # list/tuple の混在を避けて、必ず tuple にそろえる
    pos = tuple(item.get("position", ()))
    rot = tuple(item.get("rotation", ()))

    # それ以外は素直に取得（None も含めて完全一致扱い）
    return (
        pos,
        rot,
        item.get("resolution_x"),
        item.get("resolution_y"),
        item.get("focal_length"),
        item.get("bg_image"),
        item.get("bg_opacity"),
        item.get("bg_depth"),
        item.get("frame_current"),
    )

def _append_unique_saved_items(existing_items, incoming_items):
    """既存配列へ完全同一データを除外しながら追加する。
    返り値: (追加後リスト, added_count, skipped_count)
    """
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
    """memo を除いた保存設定が一致する既存ストックの添字を返す。
    一致がなければ -1。
    """
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
    """無効なインデックスを0へ補正"""
    try:
        n = len(manager.saved_camera_data)
    except Exception:
        n = 0
    if n <= 0:
        return
    valid_ids = {str(i) for i in range(n)}
    cur = getattr(scene, "saved_camera_index", None)
    if cur not in valid_ids:
        scene.saved_camera_index = "0"


def _saved_value(data: dict, key: str, default=None):
    """壊れたJSONや旧データでも安全に値を取り出す"""
    if not isinstance(data, dict):
        return default
    value = data.get(key, default)
    return default if value is None else value


def _normalize_saved_item(item) -> dict:
    """保存1件を現行仕様の最低限の辞書へ正規化する"""
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
    }
    if 'created_at' in item:
        out['created_at'] = item.get('created_at')
    return out


def _normalize_saved_list(items) -> list:
    """保存配列全体を安全な形にそろえる"""
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
    """manager内の保存配列をその場で正規化して返す"""
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


def _get_saved_item_safe(manager, index: int, default=None):
    """保存配列から1件を安全取得する"""
    items = _ensure_manager_saved_data_normalized(manager)
    try:
        idx = int(index)
    except Exception:
        return default
    if 0 <= idx < len(items):
        return items[idx]
    return default


def _safe_saved_index(scene, manager, default=0) -> int:
    """Enum文字列や壊れた値から安全に保存インデックスを得る"""
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
    if idx < 0:
        idx = 0
    if idx >= n:
        idx = n - 1
    return idx


def _sync_scene_saved_memo(scene, manager) -> None:
    """現在選択中の保存ストックに紐づく摘要メモを Scene 側へ同期する"""
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

def _tag_redraw_all_areas() -> None:
    """保存ストック周辺のUI表示ズレを減らすため、開いているエリアを再描画する"""
    try:
        wm = bpy.context.window_manager
    except Exception:
        wm = None
    if wm is None:
        return
    for window in getattr(wm, 'windows', []):
        screen = getattr(window, 'screen', None)
        if screen is None:
            continue
        for area in getattr(screen, 'areas', []):
            try:
                area.tag_redraw()
            except Exception:
                pass


def _set_saved_camera_index_safe(scene, manager, index: int | None = 0) -> None:
    """保存ストックのEnum値を安全に更新し、必要な再描画を行う"""
    try:
        total = len(getattr(manager, 'saved_camera_data', []) or [])
    except Exception:
        total = 0

    if total <= 0:
        try:
            scene.saved_camera_index = "0"
        except Exception:
            pass
        _tag_redraw_all_areas()
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
    _tag_redraw_all_areas()


def _apply_saved_camera_data(scene, camera, manager, data) -> None:
    """保存1件を安全にカメラへ適用する"""
    if not scene or not camera or not getattr(camera, "data", None):
        return

    fr = _saved_value(data, 'frame_current', None)
    if fr is not None:
        try:
            scene.frame_set(int(fr))
        except Exception:
            pass

    try:
        camera.location = _saved_value(data, 'position', list(camera.location))
    except Exception:
        pass
    try:
        camera.rotation_euler = _saved_value(data, 'rotation', list(camera.rotation_euler))
    except Exception:
        pass
    try:
        scene.render.resolution_x = int(_saved_value(data, 'resolution_x', scene.render.resolution_x))
    except Exception:
        pass
    try:
        scene.render.resolution_y = int(_saved_value(data, 'resolution_y', scene.render.resolution_y))
    except Exception:
        pass
    try:
        camera.data.lens = float(_saved_value(data, 'focal_length', camera.data.lens))
    except Exception:
        pass

    try:
        _apply_saved_background_to_camera(camera, manager, data if isinstance(data, dict) else {})
    except Exception as e:
        print(f"下絵の読み込み中にエラーが発生しました: {e}")

# ---------------- OS操作 ----------------
def _open_system_folder(path: str):
    if not path or not os.path.exists(path):
        return False
    try:
        if os.name == 'nt':
            try:
                os.startfile(path)  # type: ignore[attr-defined]
            except Exception:
                subprocess.Popen(['explorer', path])
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', path])
        else:
            subprocess.Popen(['xdg-open', path])
        return True
    except Exception:
        return False

def _minimize_all_blender_windows():
    try:
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            GetWindowThreadProcessId = user32.GetWindowThreadProcessId
            EnumWindows = user32.EnumWindows
            IsWindowVisible = user32.IsWindowVisible
            ShowWindow = user32.ShowWindow
            SW_MINIMIZE = 6
            pid = os.getpid()
            EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            def _enum_proc(hWnd, lParam):
                if not IsWindowVisible(hWnd):
                    return True
                _pid = wintypes.DWORD()
                GetWindowThreadProcessId(hWnd, ctypes.byref(_pid))
                if _pid.value == pid:
                    try:
                        ShowWindow(hWnd, SW_MINIMIZE)
                    except Exception:
                        pass
                return True
            EnumWindows(EnumWindowsProc(_enum_proc), 0)
            return True
        elif sys.platform == 'darwin':
            try:
                script = (
                    'tell application "System Events"\n'
                    'try\n'
                    'set miniaturized of windows of (every process whose name is "Blender") to true\n'
                    'end try\n'
                    'end tell'
                )
                subprocess.run(['osascript', '-e', script], check=False)
                return True
            except Exception:
                return False
        else:
            try:
                subprocess.Popen(['wmctrl', '-k', 'on'])
                return True
            except Exception:
                return False
    except Exception:
        return False

# --------------- 3Dビュー探し ---------------
def _get_view3d_window_area_region(context):
    wm = context.window_manager
    for win in wm.windows:
        screen = win.screen
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                for region in area.regions:
                    if region.type == 'WINDOW':
                        return win, area, region
    return None, None, None

# =========================
# アドオン設定（任意）
# =========================
class CameraPositionManagerPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_PACKAGE_NAME
    def draw(self, context):
        layout = self.layout
        layout.label(text="ショートカット（任意）")
        box = layout.box()
        box.label(text="Insert : 最新のカメラ位置を呼び出し")
        box.label(text="Ctrl + Insert : カメラ位置を保存")
        box.label(text="Ctrl + Shift + Insert : 下絵を読み込む")

# =========================
# データ管理
# =========================
class CameraDataManager:
    def __init__(self):
        home_dir = os.path.expanduser("~")
        self.saved_camera_data = []
        self.output_folder_path = _safe_existing_dirpath(home_dir, fallback=home_dir)
        self.background_image_folder_path = _safe_existing_dirpath(home_dir, fallback=home_dir)
        self.save_file_path = _safe_json_path(os.path.join(bpy.utils.user_resource('CONFIG'), "camera_positions.json"))

    def save_data(self, filepath=None):
        target_path = _safe_json_path(filepath or self.save_file_path)
        self.save_file_path = target_path
        home_dir = os.path.expanduser("~")
        self.saved_camera_data = _normalize_saved_list(self.saved_camera_data)
        self.output_folder_path = _safe_existing_dirpath(self.output_folder_path, fallback=home_dir)
        self.background_image_folder_path = _safe_existing_dirpath(self.background_image_folder_path, fallback=home_dir)
        data = {
            'camera_data': self.saved_camera_data,
            'output_folder_path': self.output_folder_path,
            'background_image_folder_path': self.background_image_folder_path
        }
        try:
            _write_json_atomic(target_path, data)
        except Exception as e:
            print(f"データの保存中にエラー: {e}")
            self.report_error(f"データの保存中にエラーが発生しました: {e}")

    def load_data(self, filepath=None):
        target_path = _safe_json_path(filepath or self.save_file_path)
        self.save_file_path = target_path
        home_dir = os.path.expanduser("~")
        if os.path.exists(target_path):
            try:
                with open(target_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self.saved_camera_data = _normalize_saved_list(data.get('camera_data', []))
                        self.output_folder_path = _safe_existing_dirpath(data.get('output_folder_path', home_dir), fallback=home_dir)
                        self.background_image_folder_path = _safe_existing_dirpath(data.get('background_image_folder_path', home_dir), fallback=home_dir)
                    else:
                        self.saved_camera_data = []
                        self.output_folder_path = _safe_existing_dirpath(home_dir, fallback=home_dir)
                        self.background_image_folder_path = _safe_existing_dirpath(home_dir, fallback=home_dir)
            except json.JSONDecodeError:
                self.saved_camera_data = []
                self.output_folder_path = _safe_existing_dirpath(home_dir, fallback=home_dir)
                self.background_image_folder_path = _safe_existing_dirpath(home_dir, fallback=home_dir)
                print("設定ファイルが壊れています。初期化しました。")
            except Exception as e:
                print(f"読み込み中に予期しないエラー: {e}")
                self.report_error(f"データの読み込み中にエラーが発生しました: {e}")
        else:
            print("設定ファイルが見つかりません。新規作成します。")
            self.save_data(target_path)

    def report_error(self, message):
        def draw(self, context):
            self.layout.label(text=str(message))

        try:
            ctx = bpy.context
            wm = getattr(ctx, "window_manager", None)
        except Exception:
            wm = None

        if wm is not None:
            try:
                wm.popup_menu(draw, title="エラー", icon='ERROR')
                return
            except Exception:
                pass

        print(f"[CameraPosMgr][ERROR] {message}")

def get_camera_data_manager():
    global _CAMERA_DATA_MANAGER
    if _CAMERA_DATA_MANAGER is None:
        _CAMERA_DATA_MANAGER = CameraDataManager()
    return _CAMERA_DATA_MANAGER

# =========================
# オペレーター / パネル
# =========================
class OBJECT_OT_select_camera_data(bpy.types.Operator):
    """どのモードでも安全にカメラ選択＆プロパティのデータタブへ"""
    bl_idname = "camera.select_data"
    bl_label = "カメラデータタブを選択"

    def execute(self, context):
        scene = context.scene
        cam_obj = scene.camera
        if not cam_obj or cam_obj.name not in bpy.data.objects:
            cam_data = bpy.data.cameras.new(name="Camera")
            cam_obj = bpy.data.objects.new(name="Camera", object_data=cam_data)
            scene.collection.objects.link(cam_obj)
            scene.camera = cam_obj

        win, area, region = _get_view3d_window_area_region(context)
        if win and area and region:
            with context.temp_override(window=win, area=area, region=region):
                try:
                    if bpy.ops.object.mode_set.poll():
                        bpy.ops.object.mode_set(mode='OBJECT')
                except Exception:
                    pass
                try:
                    bpy.ops.object.select_all(action='DESELECT')
                except Exception:
                    for ob in list(context.selected_objects):
                        try:
                            ob.select_set(False)
                        except Exception:
                            pass
        try:
            cam_obj.select_set(True)
        except Exception:
            pass
        context.view_layer.objects.active = cam_obj

        try:
            for win2 in context.window_manager.windows:
                for area2 in win2.screen.areas:
                    if area2.type == 'PROPERTIES':
                        sp = area2.spaces.active
                        if 'DATA' in sp.bl_rna.properties["context"].enum_items.keys():
                            sp.context = 'DATA'
                        break
        except Exception:
            pass

        self.report({'INFO'}, "カメラデータタブへ移動しました")
        return {'FINISHED'}

class OBJECT_OT_recall_camera_position(bpy.types.Operator):
    bl_idname = "camera.recall_position"
    bl_label = "カメラ位置を呼び出す"
    def execute(self, context):
        scene = context.scene
        camera = scene.camera
        if not camera:
            self.report({'WARNING'}, "カメラがありません")
            return {'CANCELLED'}
        manager = get_camera_data_manager()
        # Insert 呼び出し時は、最新保存内容とのズレを避けるため一度ディスク内容を同期する
        try:
            manager.load_data()
            rebuild_enum_cache(manager)
        except Exception:
            pass
        saved_items = _ensure_manager_saved_data_normalized(manager)
        if not saved_items:
            self.report({'WARNING'}, "ストックがありません")
            return {'CANCELLED'}
        latest_index = len(saved_items) - 1
        data = saved_items[latest_index]
        _apply_saved_camera_data(scene, camera, manager, data)
        # UIのプルダウンも最新ストック位置へ合わせる
        try:
            _set_saved_camera_index_safe(scene, manager, latest_index)
        except Exception:
            pass
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.spaces.active.region_3d.view_perspective = 'CAMERA'
                break
        try:
            if context.area:
                context.area.tag_redraw()
        except Exception:
            pass
        self.report({'INFO'}, "最新のカメラ位置を呼び出しました")
        return {'FINISHED'}

class OBJECT_OT_save_camera_position(bpy.types.Operator):
    bl_idname = "camera.save_position"
    bl_label = "カメラ位置を保存"

    def execute(self, context):
        scene = context.scene
        camera = scene.camera
        manager = get_camera_data_manager()
        if camera:
            position = list(camera.location)
            rotation = list(camera.rotation_euler)
            resolution_x = scene.render.resolution_x
            resolution_y = scene.render.resolution_y
            focal_length = camera.data.lens
            bg_image = "No File"
            bg_opacity = 1.0
            bg_depth = 'BACK'
            if camera.data.background_images:
                for bg in camera.data.background_images:
                    if bg.image:
                        bg_image = safe_basename(bg.image.filepath)
                        bg_opacity = getattr(bg, "opacity", getattr(bg, "alpha", 1.0))
                        bg_depth = bg.display_depth
                        break
            frame_current = int(scene.frame_current)

            memo_text = str(getattr(scene, 'saved_memo_text', '') or '')

            new_item = {
                'position': position,
                'rotation': rotation,
                'resolution_x': resolution_x,
                'resolution_y': resolution_y,
                'focal_length': focal_length,
                'bg_image': bg_image,
                'bg_opacity': bg_opacity,
                'bg_depth': bg_depth,
                'frame_current': frame_current,
                'memo': memo_text,
                'created_at': float(time.time()),
            }

            saved_items = _ensure_manager_saved_data_normalized(manager)
            match_index = _find_matching_saved_item_index(manager, new_item)
            if match_index >= 0:
                existing = saved_items[match_index]
                same_memo = str(_saved_value(existing, 'memo', '') or '') == memo_text
                if same_memo:
                    self.report({'INFO'}, "同じ設定・同じ摘要メモのため記録しませんでした")
                    return {'CANCELLED'}
                replaced = dict(new_item)
                if 'created_at' in existing:
                    replaced['created_at'] = existing.get('created_at')
                manager.saved_camera_data[match_index] = _normalize_saved_item(replaced)
                manager.save_data()
                rebuild_enum_cache(manager)
                _set_saved_camera_index_safe(scene, manager, match_index)
                self.report({'INFO'}, "摘要メモを含めて保存データを上書きしました")
                return {'FINISHED'}

            manager.saved_camera_data.append(_normalize_saved_item(new_item))
            manager.save_data()
            rebuild_enum_cache(manager)
            _set_saved_camera_index_safe(scene, manager, len(manager.saved_camera_data) - 1)
            self.report({'INFO'}, "カメラ位置を保存しました")
        else:
            self.report({'WARNING'}, "カメラが選択されていません")
        return {'FINISHED'}

class OBJECT_OT_delete_camera_position(bpy.types.Operator):
    bl_idname = "camera.delete_position"
    bl_label = "カメラ位置を削除"
    def execute(self, context):
        scene = context.scene
        manager = get_camera_data_manager()
        index = _safe_saved_index(scene, manager)
        saved_items = _ensure_manager_saved_data_normalized(manager)
        if 0 <= index < len(saved_items):
            deleted_index = index
            manager.saved_camera_data.pop(index)
            manager.save_data()
            rebuild_enum_cache(manager)
            total = len(_ensure_manager_saved_data_normalized(manager))
            if total == 0:
                _set_saved_camera_index_safe(scene, manager, 0)
            else:
                new_index = max(0, min(deleted_index - 1, total - 1))  # 指定位置の一つ前へ
                _set_saved_camera_index_safe(scene, manager, new_index)
            self.report({'INFO'}, "カメラ位置を削除しました")
        else:
            self.report({'WARNING'}, "無効なインデックス")
        return {'FINISHED'}

class OBJECT_OT_set_camera_location_zero(bpy.types.Operator):
    bl_idname = "camera.set_location_zero"
    bl_label = "カメラ位置をゼロに設定"
    bl_description = "指定した軸の位置を 0 に設定します"

    axis: bpy.props.EnumProperty(
        name="軸",
        items=(
            ('X', 'X', 'X軸'),
            ('Y', 'Y', 'Y軸'),
            ('Z', 'Z', 'Z軸'),
        ),
        default='X',
    )

    def execute(self, context):
        scene = context.scene
        camera = scene.camera
        if not camera:
            self.report({'WARNING'}, "アクティブカメラがありません")
            return {'CANCELLED'}

        axis_map = {'X': 0, 'Y': 1, 'Z': 2}
        idx = axis_map.get(self.axis, 0)
        try:
            camera.location[idx] = 0.0
            if context.area:
                context.area.tag_redraw()
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"位置の設定に失敗しました: {e}")
            return {'CANCELLED'}

class OBJECT_OT_set_camera_rotation_snap(bpy.types.Operator):
    bl_idname = "camera.set_rotation_snap"
    bl_label = "カメラ回転をスナップ"
    bl_description = "指定した軸の回転角度を 0 / 90 / 180 / 270 度に設定します"

    axis: bpy.props.EnumProperty(
        name="軸",
        items=(
            ('X', 'X', 'X軸'),
            ('Y', 'Y', 'Y軸'),
            ('Z', 'Z', 'Z軸'),
        ),
        default='X',
    )
    angle: bpy.props.IntProperty(
        name="角度",
        default=0,
    )

    def execute(self, context):
        scene = context.scene
        camera = scene.camera
        if not camera:
            self.report({'WARNING'}, "アクティブカメラがありません")
            return {'CANCELLED'}

        axis_map = {'X': 0, 'Y': 1, 'Z': 2}
        idx = axis_map.get(self.axis, 0)
        try:
            from math import radians
            camera.rotation_euler[idx] = radians(int(self.angle))
            if context.area:
                context.area.tag_redraw()
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"回転角度の設定に失敗しました: {e}")
            return {'CANCELLED'}

class OBJECT_OT_load_background_image(bpy.types.Operator):
    bl_idname = "camera.load_background_image"
    bl_label = "下絵を読み込む"
    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    directory: bpy.props.StringProperty(subtype="DIR_PATH")

    @staticmethod
    def _frame_from_filename(path: str):
        return _frame_from_filename_path(path)

    def _ensure_camera_and_align_if_needed(self, context):
        """カメラが無ければ作成。下絵読み込み時は常に Ctrl+Alt+Num0 相当でビューに合わせる。"""
        scene = context.scene
        cam_obj = scene.camera
        if not cam_obj or cam_obj.name not in bpy.data.objects:
            # カメラ新規作成
            cam_data = bpy.data.cameras.new(name="Camera")
            cam_obj = bpy.data.objects.new(name="Camera", object_data=cam_data)
            scene.collection.objects.link(cam_obj)
            scene.camera = cam_obj

        # 3Dビューの window/area/region を取得
        win, area, region = _get_view3d_window_area_region(context)
        if not (win and area and region):
            # 3Dビューが無いケースは整備のみ実施
            try:
                bpy.ops.object.select_all(action='DESELECT')
            except Exception:
                pass
            try:
                cam_obj.select_set(True)
            except Exception:
                pass
            context.view_layer.objects.active = cam_obj
            return  # ビュー合わせはスキップ

        with context.temp_override(window=win, area=area, region=region, scene=scene):
            # オブジェクトモードに
            try:
                if bpy.ops.object.mode_set.poll():
                    bpy.ops.object.mode_set(mode='OBJECT')
            except Exception:
                pass
            # カメラをアクティブ選択
            try:
                bpy.ops.object.select_all(action='DESELECT')
            except Exception:
                pass
            try:
                cam_obj.select_set(True)
            except Exception:
                pass
            context.view_layer.objects.active = cam_obj

            # 常に「ビューにカメラを合わせる」（Ctrl+Alt+Num0 相当）
            # ※カメラビュー中でも、結果的に同じ見た目であれば実質的に変化しない
            try:
                bpy.ops.view3d.camera_to_view()
            except Exception:
                # 失敗しても致命ではないので続行
                pass

            # 最後にカメラビューへ
            try:
                bpy.ops.view3d.view_camera()
            except Exception:
                pass

    def execute(self, context):
        manager = get_camera_data_manager()
        scene = context.scene
        camera = scene.camera

        # 1) カメラ準備＆アライン（必要なら）
        self._ensure_camera_and_align_if_needed(context)
        camera = scene.camera  # 念のため再取得

        # 2) 画像ロード＆スロット設定（既存仕様踏襲）
        if not camera:
            self.report({'ERROR'}, "カメラの準備に失敗しました")
            return {'CANCELLED'}
        try:
            image = _load_image_safe(self.filepath)
            bg = _get_or_create_background_slot(camera)
            if image is None:
                raise RuntimeError("画像ファイルが見つからないか、読み込めませんでした")
            bg.image = image
            bg.display_depth = 'FRONT'
            _set_background_visibility(camera, bg, True)
            # 解像度は画像に合わせる（従来どおり）
            if image.size[0] > 0 and image.size[1] > 0:
                scene.render.resolution_x = image.size[0]
                scene.render.resolution_y = image.size[1]

            # ファイル名末尾6桁→現在フレーム
            fnum = self._frame_from_filename(self.filepath)
            if fnum is not None:
                scene.frame_set(int(fnum))

            # 既定の読込フォルダ更新
            manager.background_image_folder_path = _safe_existing_dirpath(self.directory, fallback=manager.background_image_folder_path)
            manager.save_data()
            self.report({'INFO'}, f"下絵を読み込みました: {self.filepath}")
        except Exception as e:
            self.report({'ERROR'}, f"下絵の読み込みに失敗: {e}")
            return {'CANCELLED'}

        # 3) カメラビューに切替（_ensure_camera_and_align_if_needed で実施済みだが保険で実行）
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                try:
                    area.spaces.active.region_3d.view_perspective = 'CAMERA'
                except Exception:
                    pass
                break
        return {'FINISHED'}

    def invoke(self, context, event):
        manager = get_camera_data_manager()
        self.directory = _safe_existing_dirpath(manager.background_image_folder_path, fallback=manager.background_image_folder_path)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

class OBJECT_OT_reload_background_image(bpy.types.Operator):
    bl_idname = "camera.reload_background_image"
    bl_label = "下絵を再読込"
    def execute(self, context):
        scene = context.scene
        camera = scene.camera
        manager = get_camera_data_manager()

        if not camera or not camera.data:
            self.report({'WARNING'}, "カメラがありません")
            return {'CANCELLED'}

        # まず既存の下絵スロットを確保
        bg = _get_or_create_background_slot(camera)

        # 1) 既に画像があるなら、そのファイルパスを優先して単純リロード
        if bg.image:
            try:
                if getattr(bg.image, "filepath", "") and os.path.exists(bpy.path.native_pathsep(bpy.path.abspath(bg.image.filepath))):
                    bg.image.reload()
                    img = bg.image
                else:
                    raise RuntimeError("画像パスが無効です")
            except Exception:
                img = None

            if img is not None:
                try:
                    if img.size[0] > 0 and img.size[1] > 0:
                        scene.render.resolution_x = img.size[0]
                        scene.render.resolution_y = img.size[1]
                    _set_background_visibility(camera, bg, True)
                    self.report({'INFO'}, f"再読み込み: {safe_basename(img.filepath)}")
                    return {'FINISHED'}
                except Exception as e:
                    self.report({'ERROR'}, f"再読み込みに失敗: {e}")
                    return {'CANCELLED'}

        # 2) 画像がNone、または既存画像のパスが無効な場合：選択中ストックの記録から探す
        idx = _safe_saved_index(scene, manager)
        data = _get_saved_item_safe(manager, idx)
        if data is not None:
            image_path = _resolve_bg_image_path(manager, data.get('bg_image', ""))
            if image_path and os.path.exists(image_path):
                try:
                    img = _load_image_safe(image_path)
                    if img is None:
                        raise RuntimeError("画像ファイルが見つからないか、読み込めませんでした")
                    bg.image = img
                    _apply_background_display_settings(bg, data)
                    _set_background_visibility(camera, bg, True)
                    if img.size[0] > 0 and img.size[1] > 0:
                        scene.render.resolution_x = img.size[0]
                        scene.render.resolution_y = img.size[1]
                    self.report({'INFO'}, f"再読み込み: {safe_basename(img.filepath)}")
                except Exception as e:
                    self.report({'ERROR'}, f"再読み込みに失敗: {e}")
                    return {'CANCELLED'}
            else:
                self.report({'INFO'}, "記録済みファイル名の画像が見つかりません（後で同名ファイルを配置すれば有効化されます）")
        else:
            self.report({'INFO'}, "ストックがありません")

        # カメラビューへ
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.spaces.active.region_3d.view_perspective = 'CAMERA'
                break
        return {'FINISHED'}

class OBJECT_OT_open_output_folder(bpy.types.Operator):
    bl_idname = "camera.open_output_folder"
    bl_label = "出力フォルダを開く"
    def execute(self, context):
        manager = get_camera_data_manager()
        path = manager.output_folder_path
        if os.path.exists(path):
            ok = _open_system_folder(path)
            if not ok:
                self.report({'ERROR'}, "フォルダを開けませんでした")
        else:
            self.report({'ERROR'}, "指定された出力フォルダが存在しません")
        return {'FINISHED'}

class OBJECT_OT_set_output_folder(bpy.types.Operator):
    bl_idname = "camera.set_output_folder"
    bl_label = "出力フォルダを指定"
    directory: bpy.props.StringProperty(subtype="DIR_PATH")
    def execute(self, context):
        manager = get_camera_data_manager()
        normalized_dir = _safe_existing_dirpath(self.directory, fallback=manager.output_folder_path)
        manager.output_folder_path = normalized_dir
        manager.save_data()
        self.report({'INFO'}, f"出力フォルダを指定しました: {normalized_dir}")
        return {'FINISHED'}
    def invoke(self, context, event):
        manager = get_camera_data_manager()
        self.directory = _safe_existing_dirpath(manager.output_folder_path, fallback=manager.output_folder_path)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

class OBJECT_OT_set_background_image_folder(bpy.types.Operator):
    bl_idname = "camera.set_background_image_folder"
    bl_label = "読込場所を設定"
    directory: bpy.props.StringProperty(subtype="DIR_PATH")
    def execute(self, context):
        manager = get_camera_data_manager()
        normalized_dir = _safe_existing_dirpath(self.directory, fallback=manager.background_image_folder_path)
        manager.background_image_folder_path = normalized_dir
        manager.save_data()
        self.report({'INFO'}, f"読込場所を設定しました: {normalized_dir}")
        return {'FINISHED'}
    def invoke(self, context, event):
        manager = get_camera_data_manager()
        self.directory = _safe_existing_dirpath(manager.background_image_folder_path, fallback=manager.background_image_folder_path)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

class OBJECT_OT_reset_saved_data(bpy.types.Operator):
    bl_idname = "camera.reset_saved_data"
    bl_label = "記録データの初期化"
    def execute(self, context):
        manager = get_camera_data_manager()
        manager.saved_camera_data = []
        manager.save_data()
        rebuild_enum_cache(manager)
        try:
            _set_saved_camera_index_safe(context.scene, manager, 0)
        except Exception:
            pass
        self.report({'INFO'}, "記録データを初期化しました")
        return {'FINISHED'}
    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

class OBJECT_OT_save_stock_data(bpy.types.Operator):
    bl_idname = "camera.save_stock_data"
    bl_label = "ストックデータを保存"
    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    def execute(self, context):
        manager = get_camera_data_manager()
        filepath = _safe_json_path(self.filepath)
        manager.save_data(filepath)
        self.report({'INFO'}, f"ストックデータを保存しました: {filepath}")
        return {'FINISHED'}
    def invoke(self, context, event):
        manager = get_camera_data_manager()
        self.filepath = _default_json_dialog_filepath(manager)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

class OBJECT_OT_load_stock_data(bpy.types.Operator):
    bl_idname = "camera.load_stock_data"
    bl_label = "ストックデータを読込む"
    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    def execute(self, context):
        manager = get_camera_data_manager()
        filepath = _safe_json_path(self.filepath)
        if os.path.exists(filepath):
            manager.load_data(filepath)
            rebuild_enum_cache(manager)
            scene = context.scene
            _set_saved_camera_index_safe(scene, manager, 0)
            self.report({'INFO'}, f"ストックデータを読込しました: {filepath}")
        else:
            self.report({'ERROR'}, f"ファイルが存在しません: {filepath}")
        return {'FINISHED'}
    def invoke(self, context, event):
        manager = get_camera_data_manager()
        self.filepath = _default_json_dialog_filepath(manager)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

class OBJECT_OT_append_stock_data(bpy.types.Operator):
    bl_idname = "camera.append_stock_data"
    bl_label = "ストックデータ追加読込"
    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    
    # ファイルブラウザ右側に出すチェック（プロパティなし→プロパティありにする）
    skip_identical: bpy.props.BoolProperty(
        name="完全同一データは登録スキップ",
        description="既存ストックと内容が完全に一致するデータは追加しません",
        default=True,
    )

    def execute(self, context):
        manager = get_camera_data_manager()
        filepath = _safe_json_path(self.filepath)

        if not os.path.exists(filepath):
            self.report({'ERROR'}, f"ファイルが存在しません: {filepath}")
            return {'CANCELLED'}

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 期待する形式は {"camera_data": [...], ...}
            if isinstance(data, dict):
                incoming_list = _normalize_saved_list(data.get('camera_data', []))
            else:
                incoming_list = []

            # 既存データへ「追加」
            # ただし「完全同一データは登録スキップ」がONなら、既存と同一のものは追加しない
            added_count = 0
            skipped_count = 0
            if self.skip_identical:
                merged, added_count, skipped_count = _append_unique_saved_items(
                    manager.saved_camera_data,
                    incoming_list,
                )
                manager.saved_camera_data = merged
            else:
                manager.saved_camera_data.extend(incoming_list)
                added_count = len(incoming_list)

            # 追加した結果を既存の保存先へ反映（= 結合して既存ファイルにする）
            manager.save_data()

            rebuild_enum_cache(manager)
            _set_saved_camera_index_safe(context.scene, manager, 0)

            # 追加結果を分かりやすく表示
            self.report({'INFO'}, f"追加{added_count}個、重複スキップ{skipped_count}個")
            return {'FINISHED'}

        except Exception as e:
            self.report({'ERROR'}, f"追加読込に失敗しました: {e}")
            return {'CANCELLED'}

    def invoke(self, context, event):
        manager = get_camera_data_manager()
        self.filepath = _default_json_dialog_filepath(manager)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

class OBJECT_OT_manage_saved_data(bpy.types.Operator):
    bl_idname = "camera.manage_saved_data"
    bl_label = "保存データを管理"
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=400)
    def draw(self, context):
        layout = self.layout
        manager = get_camera_data_manager()
        for index, data in enumerate(_ensure_manager_saved_data_normalized(manager)):
            row = layout.row(align=True)
            bg_image_name = unicodedata.normalize("NFC", str(data.get('bg_image', "No File")))
            memo_text = unicodedata.normalize("NFC", str(data.get('memo', "")))
            memo_text = memo_text.replace("\n", " ").replace("\r", " ").strip()
            if len(memo_text) > 18:
                memo_text = memo_text[:18] + "…"
            file_col = row.column(align=True)
            file_col.scale_x = 1.25
            file_col.label(text=f"{index + 1}: {bg_image_name}")
            row.separator(factor=0.5)
            delete_col = row.column(align=True)
            delete_col.scale_x = 0.78
            op = delete_col.operator("camera.delete_saved_data", text="削除")
            op.index = index
            row.separator(factor=0.5)
            memo_col = row.column(align=True)
            memo_col.scale_x = 1.1
            memo_col.label(text=memo_text if memo_text else "-")
    def execute(self, context):
        return {'FINISHED'}

class OBJECT_OT_delete_saved_data(bpy.types.Operator):
    bl_idname = "camera.delete_saved_data"
    bl_label = "保存データを削除"
    index: bpy.props.IntProperty()
    def execute(self, context):
        manager = get_camera_data_manager()
        try:
            deleted_index = self.index
            manager.saved_camera_data.pop(deleted_index)
            manager.save_data()
            rebuild_enum_cache(manager)
            scene = context.scene
            total = len(_ensure_manager_saved_data_normalized(manager))
            if total == 0:
                _set_saved_camera_index_safe(scene, manager, 0)
            else:
                new_index = max(0, min(deleted_index - 1, total - 1))
                _set_saved_camera_index_safe(scene, manager, new_index)
            self.report({'INFO'}, f"保存データ {self.index + 1} を削除しました")
        except IndexError:
            self.report({'ERROR'}, "無効なインデックス")
        return {'FINISHED'}

# ▼ ソート: 名前 → created_at
class OBJECT_OT_sort_saved_data(bpy.types.Operator):
    bl_idname = "camera.sort_saved_data"
    bl_label = "ストックをソート"
    reverse: bpy.props.BoolProperty(
        name="降順",
        default=True,
    )
    def execute(self, context):
        manager = get_camera_data_manager()
        saved_items = _ensure_manager_saved_data_normalized(manager)
        if not saved_items:
            self.report({'INFO'}, "ストックが空です")
            return {'CANCELLED'}
        def _key(d):
            name = unicodedata.normalize("NFC", str(d.get('bg_image', "")))
            created = d.get('created_at', 0.0)
            return (name, created)
        saved_items.sort(key=_key, reverse=bool(self.reverse))
        manager.saved_camera_data = saved_items
        manager.save_data()
        rebuild_enum_cache(manager)
        _set_saved_camera_index_safe(context.scene, manager, 0)
        order = "降順" if bool(self.reverse) else "昇順"
        self.report({'INFO'}, f"ストックをソートしました（{order}）")
        return {'FINISHED'}

# ▼ 追加：ストック前後移動（ループ）
class OBJECT_OT_prev_folder_image(bpy.types.Operator):
    bl_idname = "camera.prev_folder_image"
    bl_label = "前の画像"

    def execute(self, context):
        scene = context.scene
        camera = scene.camera
        manager = get_camera_data_manager()
        if not camera or not getattr(camera, 'data', None):
            self.report({'WARNING'}, 'カメラがありません')
            return {'CANCELLED'}

        current_name = ''
        try:
            bg = _get_or_create_background_slot(camera)
            if bg and bg.image:
                current_name = safe_basename(getattr(bg.image, 'filepath', '') or bg.image.name)
        except Exception:
            current_name = ''

        path, total = _find_next_folder_image_path(
            manager,
            current_name=current_name,
            step=-1,
            skip_stocked=bool(getattr(scene, 'bg_cycle_skip_stocked', False)),
        )
        if not path:
            if total == 0:
                self.report({'WARNING'}, '読込場所に読み込める画像がありません')
            else:
                self.report({'WARNING'}, '読み込める画像がありません')
            return {'CANCELLED'}
        try:
            _set_background_image_from_path(scene, camera, manager, path, update_resolution=True, update_frame=True)
        except Exception as e:
            self.report({'ERROR'}, f'画像送りに失敗: {e}')
            return {'CANCELLED'}
        self.report({'INFO'}, f'下絵を切替: {os.path.basename(path)}')
        return {'FINISHED'}


class OBJECT_OT_next_folder_image(bpy.types.Operator):
    bl_idname = "camera.next_folder_image"
    bl_label = "次の画像"

    def execute(self, context):
        scene = context.scene
        camera = scene.camera
        manager = get_camera_data_manager()
        if not camera or not getattr(camera, 'data', None):
            self.report({'WARNING'}, 'カメラがありません')
            return {'CANCELLED'}

        current_name = ''
        try:
            bg = _get_or_create_background_slot(camera)
            if bg and bg.image:
                current_name = safe_basename(getattr(bg.image, 'filepath', '') or bg.image.name)
        except Exception:
            current_name = ''

        path, total = _find_next_folder_image_path(
            manager,
            current_name=current_name,
            step=1,
            skip_stocked=bool(getattr(scene, 'bg_cycle_skip_stocked', False)),
        )
        if not path:
            if total == 0:
                self.report({'WARNING'}, '読込場所に読み込める画像がありません')
            else:
                self.report({'WARNING'}, '読み込める画像がありません')
            return {'CANCELLED'}
        try:
            _set_background_image_from_path(scene, camera, manager, path, update_resolution=True, update_frame=True)
        except Exception as e:
            self.report({'ERROR'}, f'画像送りに失敗: {e}')
            return {'CANCELLED'}
        self.report({'INFO'}, f'下絵を切替: {os.path.basename(path)}')
        return {'FINISHED'}


class OBJECT_OT_prev_saved_stock(bpy.types.Operator):
    bl_idname = "camera.prev_saved_stock"
    bl_label = "前のストックへ"
    def execute(self, context):
        scene = context.scene
        manager = get_camera_data_manager()
        total = len(manager.saved_camera_data)
        if total == 0:
            self.report({'INFO'}, "ストックがありません")
            return {'CANCELLED'}
        cur = _safe_saved_index(scene, manager)
        new = (cur - 1) % total
        _set_saved_camera_index_safe(scene, manager, new)
        return {'FINISHED'}

class OBJECT_OT_next_saved_stock(bpy.types.Operator):
    bl_idname = "camera.next_saved_stock"
    bl_label = "次のストックへ"
    def execute(self, context):
        scene = context.scene
        manager = get_camera_data_manager()
        total = len(manager.saved_camera_data)
        if total == 0:
            self.report({'INFO'}, "ストックがありません")
            return {'CANCELLED'}
        cur = _safe_saved_index(scene, manager)
        new = (cur + 1) % total
        _set_saved_camera_index_safe(scene, manager, new)
        return {'FINISHED'}

# =========================
# プロパティ・更新
# =========================
def get_saved_camera_items(self, context):
    return _ENUM_CACHE if _ENUM_CACHE else [("0", "(No Items)", "")]

def _apply_camera_view(context):
    # カメラビューへ切替（画像が無くても構図確認できるように）
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            area.spaces.active.region_3d.view_perspective = 'CAMERA'
            break

def update_saved_camera_index(self, context):
    scene = context.scene
    camera = scene.camera
    manager = get_camera_data_manager()
    saved_items = _ensure_manager_saved_data_normalized(manager)
    if camera and saved_items:
        index = _safe_saved_index(scene, manager)
        index = max(0, min(index, len(saved_items) - 1))
        data = saved_items[index]

        _apply_saved_camera_data(scene, camera, manager, data)
        _sync_scene_saved_memo(scene, manager)

        _apply_camera_view(context)
        _tag_redraw_all_areas()

def register_scene_simple_properties():
    bpy.types.Scene.saved_camera_index = bpy.props.EnumProperty(
        name="保存されたカメラ位置",
        description="保存されたカメラ位置のインデックス",
        items=get_saved_camera_items,
        update=update_saved_camera_index
    )
    bpy.types.Scene.show_settings = bpy.props.BoolProperty(
        name="show_settings", description="設定パネル表示", default=False
    )
    bpy.types.Scene.show_background_section = bpy.props.BoolProperty(
        name="下絵", description="下絵の設定を表示", default=True
    )
    bpy.types.Scene.open_output_after_render = bpy.props.BoolProperty(
        name="レンダリング後出力フォルダ開く",
        description="レンダリング完了時にBlenderを最小化して出力フォルダを開く",
        default=False
    )
    bpy.types.Scene.bg_cycle_skip_stocked = bpy.props.BoolProperty(
        name="ストック済は読み込まない",
        description="保存ストックと同名の下絵ファイルは画像送りでスキップします",
        default=False
    )
    bpy.types.Scene.saved_memo_text = bpy.props.StringProperty(
        name="摘要メモ",
        description="現在選択中の記録データに紐づく摘要メモ。記録ボタンで保存されます",
        default="",
        options={'TEXTEDIT_UPDATE'}
    )

# =========================
# 下絵 表示同期（Nパネル ⇔ カメラデータ）
# =========================
def _get_mpm_bg_visible(self):
    """Nパネル用：下絵の表示状態を返す（カメラ側のチェックと同義になるようにする）"""
    try:
        enabled = bool(getattr(self, "show_background_images", True))
    except Exception:
        enabled = True
    try:
        if getattr(self, "background_images", None) and len(self.background_images) > 0:
            bg0 = self.background_images[0]
            if hasattr(bg0, "show_background_image"):
                return bool(enabled and bg0.show_background_image)
    except Exception:
        pass
    return bool(enabled)

def _set_mpm_bg_visible(self, value):
    """Nパネル用：下絵表示を設定（カメラ側の下絵ON/OFFも同時に切替）"""
    v = bool(value)
    try:
        self.show_background_images = v
    except Exception:
        pass
    try:
        if not getattr(self, "background_images", None) or len(self.background_images) == 0:
            self.background_images.new()
        if getattr(self, "background_images", None) and len(self.background_images) > 0:
            bg0 = self.background_images[0]
            if hasattr(bg0, "show_background_image"):
                bg0.show_background_image = v
    except Exception:
        pass

def register_camera_sync_properties():
    """カメラデータに、下絵表示の同期用プロパティを追加"""
    bpy.types.Camera.mpm_bg_visible = bpy.props.BoolProperty(
        name="下絵表示",
        description="下絵の表示（カメラのBackground Images有効/無効と同期）",
        get=_get_mpm_bg_visible,
        set=_set_mpm_bg_visible,
    )

def unregister_camera_sync_properties():
    if hasattr(bpy.types.Camera, "mpm_bg_visible"):
        delattr(bpy.types.Camera, "mpm_bg_visible")

def unregister_scene_simple_properties():
    for attr in ("saved_camera_index", "show_settings", "show_background_section", "open_output_after_render", "bg_cycle_skip_stocked", "saved_memo_text"):
        if hasattr(bpy.types.Scene, attr):
            delattr(bpy.types.Scene, attr)

# =========================
# レンダー後フォルダ開き
# =========================
_pending_open_after_render = False
_pending_open_folder_path = ""

def _do_open_after_render():
    global _pending_open_after_render, _pending_open_folder_path
    try:
        _minimize_all_blender_windows()
        manager = get_camera_data_manager()
        folder_path = _pending_open_folder_path or manager.output_folder_path
        _open_system_folder(folder_path)
    finally:
        _pending_open_after_render = False
        _pending_open_folder_path = ""
    return None

def _schedule_after_render(scene):
    global _pending_open_after_render, _pending_open_folder_path
    try:
        if not getattr(scene, "open_output_after_render", False):
            return
        if _pending_open_after_render:
            return
        manager = get_camera_data_manager()
        _pending_open_folder_path = manager.output_folder_path
        _pending_open_after_render = True
        _register_timer_once(_do_open_after_render, first_interval=0.2)
    except Exception as e:
        print(f"[CameraPosMgr] schedule_after_render error: {e}")
        _pending_open_after_render = False
        _pending_open_folder_path = ""

@persistent
def _on_render_complete(scene): _schedule_after_render(scene)
@persistent
def _on_render_post(scene): _schedule_after_render(scene)


def _ensure_core_handlers_registered() -> None:
    if _on_render_complete not in bpy.app.handlers.render_complete:
        bpy.app.handlers.render_complete.append(_on_render_complete)
    if _on_render_post not in bpy.app.handlers.render_post:
        bpy.app.handlers.render_post.append(_on_render_post)
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def _ensure_core_handlers_unregistered() -> None:
    try:
        if _on_render_complete in bpy.app.handlers.render_complete:
            bpy.app.handlers.render_complete.remove(_on_render_complete)
        if _on_render_post in bpy.app.handlers.render_post:
            bpy.app.handlers.render_post.remove(_on_render_post)
        if _on_load_post in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.remove(_on_load_post)
    except Exception:
        pass

@persistent
def _on_load_post(dummy):
    """ファイル読み込み後の初期整備（ハンドラ登録等）"""
    global _pending_open_after_render, _pending_open_folder_path, _POSTLOAD_FIX_PENDING
    _pending_open_after_render = False
    _pending_open_folder_path = ""
    _ensure_core_handlers_registered()
    manager = get_camera_data_manager()
    rebuild_enum_cache(manager)
    if _POSTLOAD_FIX_PENDING:
        return
    _POSTLOAD_FIX_PENDING = True
    def _deferred_fix():
        global _POSTLOAD_FIX_PENDING
        try:
            saved_items = _ensure_manager_saved_data_normalized(manager)
            if saved_items:
                valid = {str(i) for i in range(len(saved_items))}
                for sc in bpy.data.scenes:
                    if getattr(sc, "saved_camera_index", None) not in valid:
                        _set_saved_camera_index_safe(sc, manager, 0)
        except Exception:
            return 0.2
        finally:
            _POSTLOAD_FIX_PENDING = False
        return None
    if not _register_timer_once(_deferred_fix, first_interval=0.0):
        _POSTLOAD_FIX_PENDING = False

# =========================
# 登録・解除
# =========================
CLASSES = (
    CameraPositionManagerPreferences,
    OBJECT_OT_select_camera_data,
    OBJECT_OT_recall_camera_position,
    OBJECT_OT_save_camera_position,
    OBJECT_OT_delete_camera_position,
    OBJECT_OT_set_camera_location_zero,
    OBJECT_OT_set_camera_rotation_snap,
    OBJECT_OT_load_background_image,
    OBJECT_OT_reload_background_image,
    OBJECT_OT_open_output_folder,
    OBJECT_OT_set_output_folder,
    OBJECT_OT_set_background_image_folder,
    OBJECT_OT_reset_saved_data,
    OBJECT_OT_save_stock_data,
    OBJECT_OT_load_stock_data,
    OBJECT_OT_append_stock_data,
    OBJECT_OT_manage_saved_data,
    OBJECT_OT_delete_saved_data,
    OBJECT_OT_sort_saved_data,
    OBJECT_OT_prev_folder_image,
    OBJECT_OT_next_folder_image,
    OBJECT_OT_prev_saved_stock,
    OBJECT_OT_next_saved_stock,
)

addon_keymaps = []
_KEYMAP_REGISTRATION_PENDING = False
_KEYMAP_OPERATOR_IDS = {
    "camera.recall_position",
    "camera.save_position",
    "camera.load_background_image",
}


def _clear_tracked_keymaps():
    """このモジュールが追跡しているキーマップだけ安全に除去"""
    for km, kmi in list(addon_keymaps):
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    addon_keymaps.clear()


def _remove_duplicate_addon_keymaps(kc):
    """addon keyconfig 内に残った同一オペレーターの重複登録を掃除"""
    if kc is None:
        return
    for km in getattr(kc, "keymaps", []):
        if getattr(km, "name", "") != "3D View":
            continue
        if getattr(km, "space_type", None) != 'VIEW_3D':
            continue
        for kmi in list(getattr(km, "keymap_items", [])):
            try:
                if getattr(kmi, "idname", "") in _KEYMAP_OPERATOR_IDS:
                    km.keymap_items.remove(kmi)
            except Exception:
                pass


def _register_addon_keymaps_impl():
    """Insert 系ショートカットを1回だけ登録する"""
    global _KEYMAP_REGISTRATION_PENDING
    _KEYMAP_REGISTRATION_PENDING = False
    try:
        wm = bpy.context.window_manager
        kc = wm.keyconfigs.addon if wm else None
    except Exception:
        kc = None
    if kc is None:
        return 0.2

    _clear_tracked_keymaps()
    _remove_duplicate_addon_keymaps(kc)

    try:
        km = kc.keymaps.new(name="3D View", space_type='VIEW_3D')
        kmi1 = km.keymap_items.new("camera.recall_position", type='INSERT', value='PRESS')
        kmi2 = km.keymap_items.new("camera.save_position", type='INSERT', value='PRESS', ctrl=True)
        kmi3 = km.keymap_items.new("camera.load_background_image", type='INSERT', value='PRESS', ctrl=True, shift=True)
        addon_keymaps.extend([(km, kmi1), (km, kmi2), (km, kmi3)])
    except Exception:
        _clear_tracked_keymaps()
    return None


def _schedule_addon_keymap_registration():
    """起動直後などで keyconfig 未初期化でも後から再登録できるようにする"""
    global _KEYMAP_REGISTRATION_PENDING
    if _KEYMAP_REGISTRATION_PENDING:
        return
    _KEYMAP_REGISTRATION_PENDING = True
    try:
        _register_timer_once(_register_addon_keymaps_impl, first_interval=0.0)
    except Exception:
        _KEYMAP_REGISTRATION_PENDING = False


def register_core():
    global _CAMERA_DATA_MANAGER

    register_scene_simple_properties()
    register_camera_sync_properties()

    for cls in CLASSES:
        bpy.utils.register_class(cls)

    manager = get_camera_data_manager()
    manager.load_data()
    rebuild_enum_cache(manager)

    _ensure_core_handlers_registered()

    saved_items = _ensure_manager_saved_data_normalized(manager)
    if saved_items:
        def _deferred_fix():
            try:
                valid = {str(i) for i in range(len(saved_items))}
                for sc in bpy.data.scenes:
                    if getattr(sc, "saved_camera_index", None) not in valid:
                        _set_saved_camera_index_safe(sc, manager, 0)
            except Exception:
                return 0.2
            return None
        _register_timer_once(_deferred_fix, first_interval=0.0)

    # 参考のキーバインド
    _schedule_addon_keymap_registration()

def unregister_core():
    global _CAMERA_DATA_MANAGER, _KEYMAP_REGISTRATION_PENDING
    # ヘッダー/ハンドラ撤去
    _ensure_core_handlers_unregistered()

    # キーマップ撤去
    _KEYMAP_REGISTRATION_PENDING = False
    _clear_tracked_keymaps()
    try:
        wm = bpy.context.window_manager
        kc = wm.keyconfigs.addon if wm else None
    except Exception:
        kc = None
    _remove_duplicate_addon_keymaps(kc)

    # クラス解除
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass

    unregister_scene_simple_properties()
    unregister_camera_sync_properties()
    _CAMERA_DATA_MANAGER = None

if __name__ == "__main__":
    register()

# -------------------------------
# ファイル名：core.py
# Version Footer: 1.106
# -------------------------------
