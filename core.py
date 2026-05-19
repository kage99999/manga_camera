# -*- coding: utf-8 -*-
# ファイル名：core.py
# 00漫画用Camera Position Manager
# 変更点（1.158）:
# - ラティス管理セクションの見間違い防止用に「選択中OBJ」「登録OBJ」表記を変更

import bpy
import os
import sys
import json
import time
import subprocess
import unicodedata
from bpy.app.handlers import persistent

from .background import (
    _apply_background_display_settings,
    _apply_saved_background_to_camera,
    _find_next_folder_image_path,
    _frame_from_filename_path,
    _get_or_create_background_slot,
    _load_image_safe,
    _set_background_image_from_path,
    _set_background_visibility,
)
from .storage import (
    _ENUM_CACHE,
    _append_unique_saved_items,
    _default_json_dialog_filepath,
    _ensure_manager_saved_data_normalized,
    _find_matching_saved_item_index,
    _get_saved_item_safe,
    _normalize_saved_item,
    _normalize_saved_list,
    _safe_existing_dirpath,
    _safe_json_path,
    _safe_saved_index,
    _saved_value,
    _set_saved_camera_index_safe as _storage_set_saved_camera_index_safe,
    _sync_scene_saved_memo,
    _write_json_atomic,
    ensure_valid_saved_enum,
    rebuild_enum_cache,
    safe_basename,
)

# =========================
# バージョン文字列（UI表示用）
# =========================
def _addon_version_str() -> str:
    """アドオンのversionから '1.053' のような表記を作る"""
    v = (1, 0, 158)  # 1.158
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


def _is_valid_camera_object(obj) -> bool:
    if obj is None:
        return False
    try:
        if bpy.data.objects.get(obj.name) != obj:
            return False
    except Exception:
        return False
    return getattr(obj, "type", "") == "CAMERA" and getattr(obj, "data", None) is not None


def _find_camera_candidate(scene):
    try:
        active = bpy.context.view_layer.objects.active
    except Exception:
        active = None
    if _is_valid_camera_object(active):
        try:
            if scene.objects.get(active.name) == active:
                return active
        except Exception:
            pass

    for obj in getattr(scene, "objects", []) or []:
        if _is_valid_camera_object(obj):
            return obj
    return None


def _get_valid_scene_camera(scene, repair: bool = True):
    if scene is None:
        return None
    camera = getattr(scene, "camera", None)
    if _is_valid_camera_object(camera):
        return camera
    if not repair:
        return None

    replacement = _find_camera_candidate(scene)
    try:
        scene.camera = replacement
    except Exception:
        pass
    return replacement if _is_valid_camera_object(replacement) else None


def _ensure_scene_camera(scene, create: bool = True):
    camera = _get_valid_scene_camera(scene, repair=True)
    if camera or not create:
        return camera
    cam_data = bpy.data.cameras.new(name="Camera")
    cam_obj = bpy.data.objects.new(name="Camera", object_data=cam_data)
    scene.collection.objects.link(cam_obj)
    scene.camera = cam_obj
    return cam_obj


def _sanitize_view3d_local_camera(space, fallback_camera=None) -> bool:
    if space is None or not hasattr(space, "camera"):
        return False
    try:
        local_camera = getattr(space, "camera", None)
    except Exception:
        return False
    if local_camera is None or _is_valid_camera_object(local_camera):
        return False

    safe_camera = fallback_camera if _is_valid_camera_object(fallback_camera) else None
    try:
        setattr(space, "camera", safe_camera)
        return True
    except Exception:
        return False


def _sanitize_view3d_local_cameras(context, camera=None) -> None:
    safe_camera = camera if _is_valid_camera_object(camera) else _get_valid_scene_camera(getattr(context, "scene", None), repair=True)
    try:
        wm = context.window_manager
    except Exception:
        wm = None
    if wm is None:
        return
    for window in getattr(wm, "windows", []) or []:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in getattr(screen, "areas", []) or []:
            if getattr(area, "type", "") != 'VIEW_3D':
                continue
            for space in getattr(area, "spaces", []) or []:
                if getattr(space, "type", "") == 'VIEW_3D':
                    _sanitize_view3d_local_camera(space, safe_camera)


def _set_saved_camera_index_safe(scene, manager, index: int | None = 0) -> None:
    """保存ストックのEnum値を安全に更新し、必要な再描画を行う"""
    _storage_set_saved_camera_index_safe(scene, manager, _tag_redraw_all_areas, index)


def _apply_saved_camera_data(scene, camera, manager, data) -> None:
    """保存1件を安全にカメラへ適用する"""
    if not scene or not _is_valid_camera_object(camera):
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
    try:
        _apply_saved_selected_object_data(data if isinstance(data, dict) else {})
    except Exception as e:
        print(f"選択OBJデータの適用中にエラーが発生しました: {e}")


def _apply_saved_selected_object_data(data) -> None:
    if not isinstance(data, dict):
        return
    if not bool(data.get('record_selected_objects', False)):
        return
    selected_objects = data.get('selected_objects', [])
    if not isinstance(selected_objects, list):
        return
    for obj_data in selected_objects:
        if not isinstance(obj_data, dict):
            continue
        name = str(obj_data.get('name', '') or '')
        if not name:
            continue
        obj = bpy.data.objects.get(name)
        if obj is None:
            continue
        try:
            obj.location = obj_data.get('location', list(obj.location))
        except Exception:
            pass
        try:
            obj.rotation_euler = obj_data.get('rotation', list(obj.rotation_euler))
        except Exception:
            pass
        try:
            obj.scale = obj_data.get('scale', list(obj.scale))
        except Exception:
            pass

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
def _on_disable_shift_arrow_conflicts_update(self, context):
    """設定変更時にShift+矢印の既存割り当て無効化状態を反映する"""
    try:
        _sync_shift_arrow_conflict_keymaps()
    except Exception:
        pass


def get_addon_preferences():
    """このアドオンのPreferencesを安全に取得する"""
    try:
        addon = bpy.context.preferences.addons.get(ADDON_PACKAGE_NAME)
        return addon.preferences if addon else None
    except Exception:
        return None


class CameraPositionManagerPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_PACKAGE_NAME

    disable_shift_arrow_conflicts: bpy.props.BoolProperty(
        name="Shift+矢印の既存割り当てを一時無効化",
        description="ONの間だけ、Shift+←/→と競合する既存キーマップを一時無効化し、このアドオンのストック移動ショートカットを優先します",
        default=True,
        update=_on_disable_shift_arrow_conflicts_update,
    )

    def draw(self, context):
        layout = self.layout
        layout.label(text="ショートカット（任意）")
        box = layout.box()
        box.prop(self, "disable_shift_arrow_conflicts", text="Shift+矢印の既存割り当てを一時無効化")
        box.separator()
        box.label(text="Insert : 最新のカメラ位置を呼び出し")
        box.label(text="Ctrl + Insert : カメラ位置を保存")
        box.label(text="Ctrl + Shift + Insert : 下絵を読み込む")
        box.label(text="Shift + ← : 前のストックデータへ")
        box.label(text="Shift + → : 次のストックデータへ")

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
        cam_obj = _ensure_scene_camera(scene, create=True)
        if not _is_valid_camera_object(cam_obj):
            self.report({'ERROR'}, "カメラの準備に失敗しました")
            return {'CANCELLED'}

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
        _sanitize_view3d_local_cameras(context, cam_obj)

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
        camera = _get_valid_scene_camera(scene, repair=True)
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
        _sanitize_view3d_local_cameras(context, camera)
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


def _build_current_camera_saved_item(scene, camera) -> dict:
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

    selected_objects = []
    if bool(getattr(scene, 'record_selected_objects', False)):
        for obj in getattr(bpy.context, 'selected_objects', []) or []:
            if obj == camera:
                continue
            selected_objects.append({
                'name': str(obj.name),
                'location': list(obj.location),
                'rotation': list(obj.rotation_euler),
                'scale': list(obj.scale),
            })

    return {
        'position': list(camera.location),
        'rotation': list(camera.rotation_euler),
        'resolution_x': scene.render.resolution_x,
        'resolution_y': scene.render.resolution_y,
        'focal_length': camera.data.lens,
        'bg_image': bg_image,
        'bg_opacity': bg_opacity,
        'bg_depth': bg_depth,
        'frame_current': int(scene.frame_current),
        'memo': str(getattr(scene, 'saved_memo_text', '') or ''),
        'record_selected_objects': bool(getattr(scene, 'record_selected_objects', False)),
        'selected_objects': selected_objects,
        'created_at': float(time.time()),
    }


def _save_current_item_as_stock(scene, manager, new_item, overwrite_index: int | None = None):
    memo_text = str(new_item.get('memo', '') or '')
    saved_items = _ensure_manager_saved_data_normalized(manager)

    if overwrite_index is not None and 0 <= overwrite_index < len(saved_items):
        existing = dict(saved_items[overwrite_index])
        if 'created_at' in existing:
            new_item['created_at'] = existing.get('created_at')
        manager.saved_camera_data[overwrite_index] = _normalize_saved_item(new_item)
        manager.save_data()
        rebuild_enum_cache(manager)
        _set_saved_camera_index_safe(scene, manager, overwrite_index)
        return 'overwrite'

    match_index = _find_matching_saved_item_index(manager, new_item)
    if match_index >= 0:
        existing = saved_items[match_index]
        same_memo = str(_saved_value(existing, 'memo', '') or '') == memo_text
        if same_memo:
            return 'skip'

    manager.saved_camera_data.append(_normalize_saved_item(new_item))
    manager.save_data()
    rebuild_enum_cache(manager)
    _set_saved_camera_index_safe(scene, manager, len(manager.saved_camera_data) - 1)
    return 'append'


def _normalized_without_created_at(item: dict) -> dict:
    normalized = _normalize_saved_item(item)
    normalized.pop('created_at', None)
    return normalized


def _same_base_record_data(existing_item: dict, current_item: dict) -> bool:
    existing = _normalized_without_created_at(existing_item)
    current = _normalized_without_created_at(current_item)
    return (
        tuple(existing.get('position', ())) == tuple(current.get('position', ()))
        and tuple(existing.get('rotation', ())) == tuple(current.get('rotation', ()))
        and existing.get('resolution_x') == current.get('resolution_x')
        and existing.get('resolution_y') == current.get('resolution_y')
        and existing.get('focal_length') == current.get('focal_length')
        and existing.get('bg_image') == current.get('bg_image')
        and existing.get('bg_opacity') == current.get('bg_opacity')
        and existing.get('bg_depth') == current.get('bg_depth')
        and existing.get('frame_current') == current.get('frame_current')
    )

class OBJECT_OT_save_camera_position(bpy.types.Operator):
    bl_idname = "camera.save_position"
    bl_label = "カメラ位置を保存"

    def execute(self, context):
        scene = context.scene
        camera = _get_valid_scene_camera(scene, repair=True)
        manager = get_camera_data_manager()
        if camera:
            new_item = _build_current_camera_saved_item(scene, camera)
            result = _save_current_item_as_stock(scene, manager, new_item)
            if result == 'skip':
                self.report({'INFO'}, "同じ設定・同じ摘要メモのため記録しませんでした")
                return {'CANCELLED'}
            self.report({'INFO'}, "カメラ位置を保存しました")
        else:
            self.report({'WARNING'}, "カメラが選択されていません")
        return {'FINISHED'}


def _get_recorded_object_delete_candidate_names_from_ui(context) -> set[str]:
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "mpm_recorded_object_items_v130"):
        return set()
    names = set()
    try:
        for item in wm.mpm_recorded_object_items_v130:
            if bool(getattr(item, "delete_candidate", False)):
                name = str(getattr(item, "object_name", "") or "")
                if name:
                    names.add(name)
    except Exception:
        return set()
    return names


def _clear_recorded_object_delete_candidates_from_ui(context):
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "mpm_recorded_object_items_v130"):
        return
    try:
        for item in wm.mpm_recorded_object_items_v130:
            item.delete_candidate = False
    except Exception:
        pass


def _recorded_object_name_from_saved_data(obj_data) -> str:
    if isinstance(obj_data, dict):
        return str(obj_data.get('name', '') or obj_data.get('object_name', '') or '')
    if isinstance(obj_data, str):
        return obj_data
    return str(getattr(obj_data, "name", "") or '')


def _selected_object_data_from_context(context, camera, excluded_names: set[str] | None = None) -> list[dict]:
    excluded_names = set(excluded_names or set())
    selected_objects = []
    seen = set()
    for obj in (getattr(context, 'selected_objects', []) or []):
        if obj is None or obj == camera:
            continue
        name = str(getattr(obj, 'name', '') or '')
        if not name or name in excluded_names or name in seen:
            continue
        seen.add(name)
        selected_objects.append({
            'name': name,
            'location': list(obj.location),
            'rotation': list(obj.rotation_euler),
            'scale': list(obj.scale),
        })
    return selected_objects


def _merge_recorded_object_data(existing_item: dict, selected_object_data: list[dict], delete_candidate_names: set[str]) -> list[dict]:
    delete_candidate_names = set(delete_candidate_names or set())
    selected_by_name = {
        _recorded_object_name_from_saved_data(obj_data): obj_data
        for obj_data in (selected_object_data or [])
        if _recorded_object_name_from_saved_data(obj_data)
    }
    merged = []
    used_names = set()
    existing_objects = existing_item.get('selected_objects', [])
    if isinstance(existing_objects, list):
        for obj_data in existing_objects:
            name = _recorded_object_name_from_saved_data(obj_data)
            if not name or name in delete_candidate_names:
                continue
            if name in selected_by_name:
                merged.append(selected_by_name[name])
            else:
                merged.append(obj_data)
            used_names.add(name)
    for name, obj_data in selected_by_name.items():
        if name and name not in used_names and name not in delete_candidate_names:
            merged.append(obj_data)
            used_names.add(name)
    return merged


def _build_item_with_recorded_object_delta(context, scene, camera, existing_item: dict, base_item: dict, delete_candidate_names: set[str]) -> dict:
    new_item = dict(base_item)
    selected_object_data = []
    if bool(getattr(scene, 'record_selected_objects', False)):
        selected_object_data = _selected_object_data_from_context(context, camera, excluded_names=delete_candidate_names)
    merged_objects = _merge_recorded_object_data(existing_item, selected_object_data, delete_candidate_names)
    new_item['memo'] = str(getattr(scene, 'saved_memo_text', '') or '')
    new_item['selected_objects'] = merged_objects
    new_item['record_selected_objects'] = bool(merged_objects)
    return new_item


def _build_item_with_recorded_object_deletions(scene, existing_item: dict, delete_candidate_names: set[str]) -> dict:
    new_item = dict(existing_item)
    existing_objects = existing_item.get('selected_objects', [])
    filtered_objects = []
    if isinstance(existing_objects, list):
        for obj_data in existing_objects:
            name = _recorded_object_name_from_saved_data(obj_data)
            if name and name in delete_candidate_names:
                continue
            filtered_objects.append(obj_data)
    new_item['memo'] = str(getattr(scene, 'saved_memo_text', '') or '')
    new_item['selected_objects'] = filtered_objects
    new_item['record_selected_objects'] = bool(filtered_objects)
    return new_item


class OBJECT_OT_save_selected_stock_memo(bpy.types.Operator):
    bl_idname = "camera.save_selected_stock_memo"
    bl_label = "追加データ記録"

    def execute(self, context):
        scene = context.scene
        camera = _get_valid_scene_camera(scene, repair=True)
        manager = get_camera_data_manager()
        if not camera:
            self.report({'WARNING'}, "カメラが選択されていません")
            return {'CANCELLED'}

        saved_items = _ensure_manager_saved_data_normalized(manager)
        if not saved_items:
            self.report({'WARNING'}, "記録データがありません")
            return {'CANCELLED'}

        index = _safe_saved_index(scene, manager)
        if not (0 <= index < len(saved_items)):
            self.report({'WARNING'}, "無効なストックです")
            return {'CANCELLED'}

        existing = dict(saved_items[index])
        delete_candidate_names = _get_recorded_object_delete_candidate_names_from_ui(context)

        current_item = _build_current_camera_saved_item(scene, camera)
        if delete_candidate_names or bool(getattr(scene, 'record_selected_objects', False)):
            current_item = _build_item_with_recorded_object_delta(
                context,
                scene,
                camera,
                existing,
                current_item,
                delete_candidate_names,
            )

        existing_normalized = _normalized_without_created_at(existing)
        current_normalized = _normalized_without_created_at(current_item)
        if existing_normalized == current_normalized:
            self.report({'INFO'}, "同じ設定・同じ追加データのため記録しませんでした")
            return {'CANCELLED'}
        same_base_record_data = _same_base_record_data(existing, current_item)

        result = _save_current_item_as_stock(
            scene,
            manager,
            current_item,
            overwrite_index=index if same_base_record_data else None,
        )
        if result == 'skip':
            self.report({'INFO'}, "同じ設定・同じ追加データのため記録しませんでした")
            return {'CANCELLED'}
        if result == 'overwrite':
            _clear_recorded_object_delete_candidates_from_ui(context)
            self.report({'INFO'}, "選択中ストックへ追加データを追加・更新保存しました")
        else:
            _clear_recorded_object_delete_candidates_from_ui(context)
            self.report({'INFO'}, "カメラ位置を保存しました")
        return {'FINISHED'}


class OBJECT_OT_select_recorded_object(bpy.types.Operator):
    bl_idname = "camera.select_recorded_object"
    bl_label = "記録済みOBJを選択"

    object_name: bpy.props.StringProperty(name="オブジェクト名")
    extend_selection: bpy.props.BoolProperty(
        name="選択に追加",
        description="既存の選択を維持して追加選択します",
        default=False,
        options={'SKIP_SAVE'},
    )

    def invoke(self, context, event):
        self.extend_selection = bool(getattr(self, "extend_selection", False) or getattr(event, "shift", False))
        return self.execute(context)

    def execute(self, context):
        name = str(getattr(self, "object_name", "") or "")
        if not name:
            self.report({'WARNING'}, "オブジェクト名がありません")
            return {'CANCELLED'}

        obj = bpy.data.objects.get(name)
        if obj is None:
            self.report({'WARNING'}, f"オブジェクトが見つかりません: {name}")
            return {'CANCELLED'}

        try:
            if bpy.ops.object.mode_set.poll():
                bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass

        try:
            if bool(getattr(self, "extend_selection", False)):
                currently_selected = bool(obj.select_get())
                obj.select_set(not currently_selected)
                if not currently_selected:
                    context.view_layer.objects.active = obj
                else:
                    selected_after = [candidate for candidate in (getattr(context, "selected_objects", []) or []) if candidate is not None]
                    if selected_after:
                        context.view_layer.objects.active = selected_after[-1]
            else:
                try:
                    bpy.ops.object.select_all(action='DESELECT')
                except Exception:
                    for selected in list(getattr(context, "selected_objects", []) or []):
                        try:
                            selected.select_set(False)
                        except Exception:
                            pass
                obj.select_set(True)
                context.view_layer.objects.active = obj
        except Exception as e:
            self.report({'ERROR'}, f"オブジェクト選択に失敗しました: {e}")
            return {'CANCELLED'}

        # UIList行をクリックした場合でも、記録済みOBJデータ欄の選択行をクリック対象へ同期する
        try:
            wm = context.window_manager
            if hasattr(wm, "mpm_recorded_object_items_v130") and hasattr(wm, "mpm_recorded_object_index_v130"):
                for idx, item in enumerate(wm.mpm_recorded_object_items_v130):
                    if str(getattr(item, "object_name", "") or "") == name:
                        wm.mpm_recorded_object_index_v130 = idx
                        break
        except Exception:
            pass

        try:
            if context.area:
                context.area.tag_redraw()
        except Exception:
            pass

        if bool(getattr(self, "extend_selection", False)):
            self.report({'INFO'}, f"オブジェクトを追加選択しました: {name}")
        else:
            self.report({'INFO'}, f"オブジェクトを選択しました: {name}")
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
        camera = _get_valid_scene_camera(scene, repair=True)
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
        camera = _get_valid_scene_camera(scene, repair=True)
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
        cam_obj = _ensure_scene_camera(scene, create=True)
        if not _is_valid_camera_object(cam_obj):
            return
        _sanitize_view3d_local_cameras(context, cam_obj)

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
            _sanitize_view3d_local_camera(area.spaces.active, cam_obj)
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

        # 1) カメラ準備＆アライン（必要なら）
        self._ensure_camera_and_align_if_needed(context)
        camera = _get_valid_scene_camera(scene, repair=True)  # 念のため再取得

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
        _sanitize_view3d_local_cameras(context, camera)
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
        camera = _get_valid_scene_camera(scene, repair=True)
        manager = get_camera_data_manager()

        if not camera:
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
        _sanitize_view3d_local_cameras(context, camera)
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
        camera = _get_valid_scene_camera(scene, repair=True)
        manager = get_camera_data_manager()
        if not camera:
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
            _set_background_image_from_path(scene, camera, manager, path, _tag_redraw_all_areas, update_resolution=True, update_frame=True)
            scene.saved_memo_text = ""
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
        camera = _get_valid_scene_camera(scene, repair=True)
        manager = get_camera_data_manager()
        if not camera:
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
            _set_background_image_from_path(scene, camera, manager, path, _tag_redraw_all_areas, update_resolution=True, update_frame=True)
            scene.saved_memo_text = ""
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
    camera = _get_valid_scene_camera(context.scene, repair=True)
    if camera is None:
        return
    _sanitize_view3d_local_cameras(context, camera)
    # カメラビューへ切替（画像が無くても構図確認できるように）
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            area.spaces.active.region_3d.view_perspective = 'CAMERA'
            break

def update_saved_camera_index(self, context):
    scene = context.scene
    camera = _get_valid_scene_camera(scene, repair=True)
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
    bpy.types.Scene.show_shortcut_settings_section = bpy.props.BoolProperty(
        name="ショートカット項目",
        description="設定内のショートカット項目を表示します",
        default=False,
        options={'SKIP_SAVE'},
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
    bpy.types.Scene.record_selected_objects = bpy.props.BoolProperty(
        name="選択OBJデータ",
        description="ON のとき、選択中OBJの位置・回転・サイズを記録データへ含めます",
        default=False,
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
    for attr in ("saved_camera_index", "show_settings", "show_background_section", "show_shortcut_settings_section", "open_output_after_render", "bg_cycle_skip_stocked", "saved_memo_text", "record_selected_objects"):
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
    OBJECT_OT_save_selected_stock_memo,
    OBJECT_OT_select_recorded_object,
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
addon_shift_arrow_keymaps = []
_disabled_shift_arrow_keymaps = []
_KEYMAP_REGISTRATION_PENDING = False
_STOCK_SHIFT_ARROW_OPERATOR_IDS = {
    "camera.prev_saved_stock",
    "camera.next_saved_stock",
}
_KEYMAP_OPERATOR_IDS = {
    "camera.recall_position",
    "camera.save_position",
    "camera.load_background_image",
    "camera.prev_saved_stock",
    "camera.next_saved_stock",
}


def _clear_tracked_keymaps():
    """このモジュールが追跡しているキーマップだけ安全に除去"""
    for km, kmi in list(addon_keymaps):
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    addon_keymaps.clear()
    addon_shift_arrow_keymaps.clear()


def _remove_duplicate_addon_keymaps(kc):
    """addon keyconfig 内に残った同一オペレーターの重複登録を掃除"""
    if kc is None:
        return
    for km in getattr(kc, "keymaps", []):
        for kmi in list(getattr(km, "keymap_items", [])):
            try:
                if getattr(kmi, "idname", "") in _KEYMAP_OPERATOR_IDS:
                    km.keymap_items.remove(kmi)
            except Exception:
                pass


def _restore_disabled_shift_arrow_keymaps():
    """このアドオンが一時無効化したShift+矢印キーマップだけ元の状態へ戻す"""
    for _kc_name, _km_name, _idname, kmi, was_active in list(_disabled_shift_arrow_keymaps):
        try:
            kmi.active = bool(was_active)
        except Exception:
            pass
    _disabled_shift_arrow_keymaps.clear()
    _refresh_keyconfigs()


def _set_addon_shift_arrow_keymaps_active(active):
    """このアドオン自身のShift+矢印キーマップだけ有効／無効を切り替える"""
    for _km, kmi in list(addon_shift_arrow_keymaps):
        try:
            kmi.active = bool(active)
        except Exception:
            pass
    _refresh_keyconfigs()


def _is_exact_shift_arrow_keymap_item(kmi):
    """Shift+←/→の既存キーマップか判定する（フレーム移動系の取りこぼしを減らす）"""
    try:
        # このアドオン自身の前後ストック移動は競合扱いしない
        if getattr(kmi, "idname", "") in _STOCK_SHIFT_ARROW_OPERATOR_IDS:
            return False
        # 対象キーは左右矢印だけ
        if getattr(kmi, "type", "") not in {'LEFT_ARROW', 'RIGHT_ARROW'}:
            return False
        # 基本は押下イベントを対象にする。Blender側の表現差を考慮してANYも許可する。
        if getattr(kmi, "value", "PRESS") not in {'PRESS', 'ANY'}:
            return False
        # Shiftが関係する割り当てだけを対象にする。
        shift_flag = bool(getattr(kmi, "shift", False))
        key_modifier = str(getattr(kmi, "key_modifier", "NONE"))
        if not shift_flag and key_modifier not in {'LEFT_SHIFT', 'RIGHT_SHIFT'}:
            return False
        # Ctrl/Alt/OSキー併用のショートカットは、今回のShift+矢印とは別物として触らない。
        if bool(getattr(kmi, "ctrl", False)):
            return False
        if bool(getattr(kmi, "alt", False)):
            return False
        if bool(getattr(kmi, "oskey", False)):
            return False
        return True
    except Exception:
        return False


def _refresh_keyconfigs():
    """キーマップ変更をBlender側へ反映させる"""
    try:
        bpy.context.window_manager.keyconfigs.update()
    except Exception:
        pass


def _iter_existing_shift_arrow_conflict_keymaps():
    """既存側のShift+矢印キーマップ候補を列挙する"""
    try:
        wm = bpy.context.window_manager
        keyconfigs = []
        if getattr(wm.keyconfigs, "user", None) is not None:
            keyconfigs.append(wm.keyconfigs.user)
        if getattr(wm.keyconfigs, "default", None) is not None:
            keyconfigs.append(wm.keyconfigs.default)
    except Exception:
        return

    seen = set()
    for kc in keyconfigs:
        for km in getattr(kc, "keymaps", []):
            for kmi in getattr(km, "keymap_items", []):
                try:
                    ident = id(kmi)
                    if ident in seen:
                        continue
                    seen.add(ident)
                    if _is_exact_shift_arrow_keymap_item(kmi):
                        yield kc, km, kmi
                except Exception:
                    continue


def _sync_shift_arrow_conflict_keymaps():
    """設定に応じてShift+矢印の既存割り当てを一時無効化／復元する"""
    _restore_disabled_shift_arrow_keymaps()
    prefs = get_addon_preferences()

    # チェックOFF時は、既存割り当てを復元するだけでなく、
    # アドオン側のShift+矢印キーマップも一時停止する。
    # これをしないと、既存キーマップを戻してもアドオン側が先に反応してしまう。
    if prefs is None or not bool(getattr(prefs, "disable_shift_arrow_conflicts", False)):
        _set_addon_shift_arrow_keymaps_active(False)
        _refresh_keyconfigs()
        return

    _set_addon_shift_arrow_keymaps_active(True)
    for kc, km, kmi in _iter_existing_shift_arrow_conflict_keymaps() or []:
        try:
            was_active = bool(getattr(kmi, "active", True))
            if not was_active:
                continue
            kmi.active = False
            _disabled_shift_arrow_keymaps.append((
                str(getattr(kc, "name", "")),
                str(getattr(km, "name", "")),
                str(getattr(kmi, "idname", "")),
                kmi,
                was_active,
            ))
        except Exception:
            pass
    _refresh_keyconfigs()


def _new_keymap_item_prefer_head(km, operator_id, event_type, *, shift=False, ctrl=False, alt=False):
    """可能ならkeymap先頭へ登録し、既存フレーム移動より優先されやすくする"""
    try:
        return km.keymap_items.new(operator_id, type=event_type, value='PRESS', shift=shift, ctrl=ctrl, alt=alt, head=True)
    except TypeError:
        return km.keymap_items.new(operator_id, type=event_type, value='PRESS', shift=shift, ctrl=ctrl, alt=alt)


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
        km_view = kc.keymaps.new(name="3D View", space_type='VIEW_3D')
        kmi1 = _new_keymap_item_prefer_head(km_view, "camera.recall_position", 'INSERT')
        kmi2 = _new_keymap_item_prefer_head(km_view, "camera.save_position", 'INSERT', ctrl=True)
        kmi3 = _new_keymap_item_prefer_head(km_view, "camera.load_background_image", 'INSERT', ctrl=True, shift=True)

        addon_keymaps.extend([
            (km_view, kmi1),
            (km_view, kmi2),
            (km_view, kmi3),
        ])

        # Shift+矢印はフレーム移動系の標準キーマップと競合しやすいので、
        # WindowだけでなくFrames/Screen/3D Viewにも登録して取りこぼしを減らす。
        stock_keymap_specs = [
            ("Window", 'EMPTY'),
            ("Frames", 'EMPTY'),
            ("Screen", 'EMPTY'),
            ("3D View", 'VIEW_3D'),
        ]
        for km_name, space_type in stock_keymap_specs:
            km_stock = kc.keymaps.new(name=km_name, space_type=space_type)
            kmi_prev = _new_keymap_item_prefer_head(km_stock, "camera.prev_saved_stock", 'LEFT_ARROW', shift=True)
            kmi_next = _new_keymap_item_prefer_head(km_stock, "camera.next_saved_stock", 'RIGHT_ARROW', shift=True)
            addon_keymaps.extend([
                (km_stock, kmi_prev),
                (km_stock, kmi_next),
            ])
            addon_shift_arrow_keymaps.extend([
                (km_stock, kmi_prev),
                (km_stock, kmi_next),
            ])

        _sync_shift_arrow_conflict_keymaps()
        _refresh_keyconfigs()
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
    _restore_disabled_shift_arrow_keymaps()
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
# Version Footer: 1.158
# -------------------------------
