# -*- coding: utf-8 -*-
# ファイル名：xmp_rendering.py
# 00漫画用Camera Position Manager
# 変更点（1.203）:
# - Quaternion/Axis Angleカメラの有効な回転値をXMPへ反映

import bpy
from bpy.app.handlers import persistent
import os
import time
import zlib
import re
from html import escape
from pathlib import Path
from mathutils import Quaternion, Vector

from .core import (
    _get_valid_scene_camera, _register_timer_once, _unregister_timer,
    get_camera_data_manager,
)
from .storage import _atomic_writer


# =========================
# 定数
# =========================
ADDON_VERSION_STR = "1.203"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_XMP_KEYWORD = "XML:com.adobe.xmp"
_RENDER_OPERATOR_ID = "camera.render_with_xmp"
_MISSING_TARGET_MESSAGE = "対象PNGが見つかりません。設定の出力フォルダとコンポジットの出力先が同じか、ファイル名に現在フレーム番号（######）が付いているか確認して下さい。"

addon_xmp_keymaps = []
_XMP_KEYMAP_REGISTRATION_PENDING = False


# =========================
# メタデータ生成
# =========================
def _format_xyz_values(values, *, degrees=False):
    """XYZ値をWindowsプロパティで読みやすい文字列へ整形する"""
    result = []
    axes = ("X", "Y", "Z")
    safe_values = list(values)[:3] if values is not None else [0.0, 0.0, 0.0]
    for axis, value in zip(axes, safe_values):
        try:
            numeric = float(value)
        except Exception:
            numeric = 0.0
        if degrees:
            result.append(f"{axis}={numeric:.3f}°")
        else:
            result.append(f"{axis}={numeric:.6f}")
    return ", ".join(result)


def _camera_metadata_values(camera, scene=None):
    """カメラとシーンからXMPへ保存する値を取り出す"""
    location_text = _format_xyz_values(getattr(camera, "location", (0.0, 0.0, 0.0)), degrees=False)

    rotation_values = []
    try:
        mode = camera.rotation_mode
        if mode == 'QUATERNION':
            rotation = camera.rotation_quaternion.to_euler('XYZ')
        elif mode == 'AXIS_ANGLE':
            angle, *axis = camera.rotation_axis_angle
            rotation = Quaternion(Vector(axis), angle).to_euler('XYZ')
        else:
            rotation = camera.rotation_euler
        for value in rotation:
            rotation_values.append(float(value) * 180.0 / 3.141592653589793)
    except Exception:
        rotation_values = [0.0, 0.0, 0.0]
    rotation_text = _format_xyz_values(rotation_values, degrees=True)

    try:
        focal_length = float(getattr(getattr(camera, "data", None), "lens", 0.0) or 0.0)
    except Exception:
        focal_length = 0.0
    focal_35mm = int(round(focal_length))
    try:
        frame_number = int(getattr(scene, "frame_current", 0) or 0) if scene is not None else 0
    except Exception:
        frame_number = 0
    return location_text, rotation_text, focal_35mm, frame_number


def _build_xmp_packet(location_text, rotation_text, focal_35mm, frame_number):
    """Windowsプロパティ表示で使った割り当てに合わせてXMPパケットを作る"""
    frame_text = escape(str(int(frame_number)), quote=False)
    make_text = escape(f"位置XYZ: {location_text}", quote=False)
    model_text = escape(f"回転XYZ: {rotation_text}", quote=False)
    focal_text = escape(str(int(focal_35mm)), quote=False)
    raw_location_text = escape(str(location_text), quote=False)
    raw_rotation_text = escape(str(rotation_text), quote=False)
    xmp = (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        '  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '    <rdf:Description rdf:about="" '
        'xmlns:tiff="http://ns.adobe.com/tiff/1.0/" '
        'xmlns:exif="http://ns.adobe.com/exif/1.0/" '
        'xmlns:exifEX="http://cipa.jp/exif/1.0/" '
        'xmlns:manga="https://example.invalid/ns/manga-camera/1.0/">\n'
        f'      <tiff:Make>{make_text}</tiff:Make>\n'
        f'      <tiff:Model>{model_text}</tiff:Model>\n'
        f'      <exif:FocalLengthIn35mmFilm>{focal_text}</exif:FocalLengthIn35mmFilm>\n'
        f'      <exifEX:LensMake>{frame_text}</exifEX:LensMake>\n'
        f'      <manga:FrameNumber>{frame_text}</manga:FrameNumber>\n'
        f'      <manga:LocationXYZ>{raw_location_text}</manga:LocationXYZ>\n'
        f'      <manga:RotationXYZ>{raw_rotation_text}</manga:RotationXYZ>\n'
        '    </rdf:Description>\n'
        '  </rdf:RDF>\n'
        '</x:xmpmeta>\n'
        '<?xpacket end="w"?>'
    )
    return xmp.encode("utf-8")


# =========================
# PNGへXMPを書き込む処理
# =========================
def _make_png_chunk(chunk_type, payload):
    """PNGチャンクを作る"""
    chunk_type_bytes = chunk_type if isinstance(chunk_type, bytes) else str(chunk_type).encode("ascii")
    payload = payload if isinstance(payload, bytes) else bytes(payload)
    length = len(payload).to_bytes(4, "big")
    crc = zlib.crc32(chunk_type_bytes)
    crc = zlib.crc32(payload, crc) & 0xFFFFFFFF
    return length + chunk_type_bytes + payload + crc.to_bytes(4, "big")


def _make_png_itxt_chunk(keyword, text_bytes):
    """XMP格納用のiTXtチャンクを作る"""
    keyword_bytes = str(keyword).encode("latin-1", errors="replace")
    payload = (
        keyword_bytes
        + b"\x00"            # keyword terminator
        + b"\x00"            # compression flag = uncompressed
        + b"\x00"            # compression method
        + b"\x00"            # language tag terminator
        + b"\x00"            # translated keyword terminator
        + text_bytes
    )
    return _make_png_chunk(b"iTXt", payload)


def _split_png_chunks(data):
    """PNGをシグネチャとチャンク列へ分解する"""
    if not isinstance(data, (bytes, bytearray)) or len(data) < len(_PNG_SIGNATURE):
        raise ValueError("PNGデータが不正です")
    if bytes(data[:8]) != _PNG_SIGNATURE:
        raise ValueError("PNGシグネチャが不正です")

    chunks = []
    offset = 8
    total = len(data)
    while offset + 12 <= total:
        length = int.from_bytes(data[offset:offset + 4], "big")
        chunk_type = bytes(data[offset + 4:offset + 8])
        chunk_end = offset + 12 + length
        if chunk_end > total:
            raise ValueError("PNGチャンク長が不正です")
        payload = bytes(data[offset + 8:offset + 8 + length])
        crc = bytes(data[offset + 8 + length:chunk_end])
        chunks.append((chunk_type, payload, crc))
        offset = chunk_end
        if chunk_type == b"IEND":
            break
    return bytes(data[:8]), chunks


def _extract_itxt_keyword(payload):
    """iTXtチャンクのkeywordだけを取り出す"""
    if not payload:
        return ""
    try:
        keyword_bytes = payload.split(b"\x00", 1)[0]
        return keyword_bytes.decode("latin-1", errors="ignore")
    except Exception:
        return ""


def _write_xmp_to_png(png_path, xmp_packet):
    """PNGにXMPを後付けし、既存XMPがあれば置き換える"""
    with open(png_path, "rb") as handle:
        original_data = handle.read()

    signature, chunks = _split_png_chunks(original_data)
    rebuilt_chunks = []
    inserted = False

    for chunk_type, payload, crc in chunks:
        if chunk_type == b"iTXt" and _extract_itxt_keyword(payload) == _XMP_KEYWORD:
            continue
        if chunk_type == b"IEND" and not inserted:
            rebuilt_chunks.append(_make_png_itxt_chunk(_XMP_KEYWORD, xmp_packet))
            inserted = True
        rebuilt_chunks.append(len(payload).to_bytes(4, "big") + chunk_type + payload + crc)

    if not inserted:
        rebuilt_chunks.append(_make_png_itxt_chunk(_XMP_KEYWORD, xmp_packet))
        rebuilt_chunks.append(_make_png_chunk(b"IEND", b""))

    with _atomic_writer(png_path) as handle:
        handle.write(signature)
        for chunk_bytes in rebuilt_chunks:
            handle.write(chunk_bytes)


# =========================
# 対象ファイル検索
# =========================
def _resolve_target_folder(folder_text):
    """Blender相対パスも含めて対象フォルダを解決する"""
    raw = str(folder_text or "").strip()
    if not raw:
        return ""
    try:
        return bpy.path.abspath(raw)
    except Exception:
        return raw


def _get_xmp_output_folder():
    """設定の出力フォルダを参照し、旧XMP専用設定は使用しない。"""
    return _resolve_target_folder(get_camera_data_manager().output_folder_path)


def _filename_contains_current_frame(filename, frame_number):
    """ファイル名内の数字列が現在フレームと一致するか判定する"""
    try:
        target = int(frame_number)
    except Exception:
        return False
    stem = os.path.splitext(os.path.basename(str(filename)))[0]
    for match in re.finditer(r"\d+", stem):
        try:
            if int(match.group(0)) == target:
                return True
        except Exception:
            continue
    return False


def _iter_recent_frame_pngs(target_folder, frame_number, start_time):
    """対象フォルダ内から今回のフレーム番号を含み、今回更新されたPNGを探す"""
    root = Path(target_folder)
    if not root.exists() or not root.is_dir():
        return []

    safe_start_time = float(start_time) - 2.0
    results = []
    try:
        candidates = list(root.rglob("*.png"))
    except Exception:
        candidates = []

    for path in candidates:
        try:
            if not path.is_file():
                continue
            if not _filename_contains_current_frame(path.name, frame_number):
                continue
            if float(path.stat().st_mtime) < safe_start_time:
                continue
            results.append(str(path))
        except Exception:
            continue
    return sorted(set(results))


# =========================
# UI
# =========================
def draw_xmp_rendering_controls(layout, context):
    """XMP付与レンダリングセクションを描画する"""
    target_folder = _get_xmp_output_folder()

    folder_box = layout.box()
    folder_box.label(text="出力フォルダ（設定と共通）")
    current_row = folder_box.row()
    current_row.enabled = False
    current_row.label(text=target_folder if target_folder else "未設定：設定から出力フォルダを指定して下さい")

    row = layout.row()
    row.scale_y = 2.0
    row.operator(_RENDER_OPERATOR_ID, text="XMP付与レンダリング", icon='RENDER_STILL')


# =========================
# 表示ヘルパー
# =========================
def _report_status(operator, context, level, message, *, keep_status=False):
    """レポート欄とステータスバーへ状態を表示する"""
    try:
        operator.report(level, message)
    except Exception:
        pass
    try:
        context.workspace.status_text_set(message if keep_status else None)
    except Exception:
        pass
    try:
        wm = getattr(bpy.context, "window_manager", None)
        for window in getattr(wm, "windows", []):
            screen = getattr(window, "screen", None)
            for area in getattr(screen, "areas", []):
                try:
                    area.tag_redraw()
                except Exception:
                    pass
    except Exception:
        pass
    try:
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
    except Exception:
        pass


def _clear_status_text(context):
    """ステータスバー表示を戻す"""
    try:
        context.workspace.status_text_set(None)
    except Exception:
        pass


# =========================
# オペレーター
# =========================
class OBJECT_OT_render_with_xmp(bpy.types.Operator):
    bl_idname = _RENDER_OPERATOR_ID
    bl_label = "XMP付与レンダリング"
    bl_description = "通常通りレンダリングし、設定の出力フォルダ内の現在フレームPNGへXMPメタデータを付与します"

    _timer = None
    _is_running = False

    def _remove_timer(self, context):
        """このオペレーターで追加したタイマーを解除する"""
        if self._timer is None:
            return
        try:
            context.window_manager.event_timer_remove(self._timer)
        except Exception:
            pass
        self._timer = None

    def _run_render_and_xmp(self, context):
        """レンダリング本体とXMP付与を実行する"""
        scene = context.scene
        camera = _get_valid_scene_camera(scene, repair=True)
        if camera is None:
            self.report({'WARNING'}, "アクティブカメラが設定されていません")
            return {'CANCELLED'}

        render_start_time = time.time()
        frame_number = int(getattr(scene, "frame_current", 0) or 0)
        target_folder = _get_xmp_output_folder()
        location_text, rotation_text, focal_35mm, xmp_frame_number = _camera_metadata_values(camera, scene)

        try:
            render_result = bpy.ops.render.render('EXEC_DEFAULT')
        except Exception as e:
            _clear_status_text(context)
            self.report({'ERROR'}, f"レンダリングに失敗しました: {e}")
            return {'CANCELLED'}

        if 'FINISHED' not in set(render_result):
            _clear_status_text(context)
            self.report({'WARNING'}, "レンダリングが完了しませんでした")
            return {'CANCELLED'}

        if not target_folder:
            _clear_status_text(context)
            self.report({'WARNING'}, "レンダリング完了：出力フォルダ未設定のためXMP付与を行いませんでした。設定から出力フォルダを指定して下さい。")
            return {'FINISHED'}

        targets = _iter_recent_frame_pngs(target_folder, frame_number, render_start_time)
        if not targets:
            _clear_status_text(context)
            self.report({'WARNING'}, _MISSING_TARGET_MESSAGE)
            return {'FINISHED'}

        xmp_packet = _build_xmp_packet(location_text, rotation_text, focal_35mm, xmp_frame_number)
        success_count = 0
        failed_count = 0
        for png_path in targets:
            try:
                _write_xmp_to_png(png_path, xmp_packet)
                success_count += 1
            except Exception:
                failed_count += 1

        if success_count > 0 and failed_count <= 0:
            _clear_status_text(context)
            self.report({'INFO'}, f"XMP付与完了：{success_count}件")
            return {'FINISHED'}
        if success_count > 0:
            _clear_status_text(context)
            self.report({'WARNING'}, f"XMP付与完了：{success_count}件 / 失敗：{failed_count}件")
            return {'FINISHED'}

        _clear_status_text(context)
        self.report({'ERROR'}, f"XMP付与に失敗しました：{failed_count}件")
        return {'CANCELLED'}

    def execute(self, context):
        scene = context.scene
        camera = _get_valid_scene_camera(scene, repair=True)
        if camera is None:
            self.report({'WARNING'}, "アクティブカメラが設定されていません")
            return {'CANCELLED'}

        _report_status(self, context, {'INFO'}, "レンダリング開始", keep_status=True)

        try:
            self._timer = context.window_manager.event_timer_add(0.2, window=context.window)
            context.window_manager.modal_handler_add(self)
            self._is_running = True
            return {'RUNNING_MODAL'}
        except Exception:
            return self._run_render_and_xmp(context)

    def modal(self, context, event):
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}

        self._remove_timer(context)
        self._is_running = False
        return self._run_render_and_xmp(context)


# =========================
# キーマップ
# =========================
def _clear_tracked_xmp_keymaps():
    """このモジュールが登録したキーマップだけ除去する"""
    for km, kmi in list(addon_xmp_keymaps):
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    addon_xmp_keymaps.clear()


def _remove_duplicate_xmp_keymaps(kc):
    """同じOperatorの重複キーマップを除去する"""
    if kc is None:
        return
    for km in getattr(kc, "keymaps", []):
        for kmi in list(getattr(km, "keymap_items", [])):
            try:
                if getattr(kmi, "idname", "") == _RENDER_OPERATOR_ID:
                    km.keymap_items.remove(kmi)
            except Exception:
                pass


def _refresh_keyconfigs():
    """キーマップ変更を反映する"""
    try:
        bpy.context.window_manager.keyconfigs.update()
    except Exception:
        pass


def _new_xmp_keymap_item_prefer_head(km):
    """Blender標準のShift+F12より先にXMPレンダリングを評価させる"""
    try:
        return km.keymap_items.new(
            _RENDER_OPERATOR_ID,
            type='F12',
            value='PRESS',
            shift=True,
            head=True,
        )
    except TypeError:
        return km.keymap_items.new(
            _RENDER_OPERATOR_ID,
            type='F12',
            value='PRESS',
            shift=True,
        )


def _register_xmp_keymaps_impl():
    """Shift + F12 を登録する"""
    global _XMP_KEYMAP_REGISTRATION_PENDING
    _XMP_KEYMAP_REGISTRATION_PENDING = False
    try:
        wm = bpy.context.window_manager
        kc = wm.keyconfigs.addon if wm else None
    except Exception:
        kc = None
    if kc is None:
        return 0.2

    _clear_tracked_xmp_keymaps()
    _remove_duplicate_xmp_keymaps(kc)

    try:
        km = kc.keymaps.new(name="Window", space_type='EMPTY')
        kmi = _new_xmp_keymap_item_prefer_head(km)
        addon_xmp_keymaps.append((km, kmi))
        _refresh_keyconfigs()
    except Exception:
        _clear_tracked_xmp_keymaps()
    return None


def _schedule_xmp_keymap_registration():
    """起動直後でも後から安全にキーマップ登録する"""
    global _XMP_KEYMAP_REGISTRATION_PENDING
    if _XMP_KEYMAP_REGISTRATION_PENDING:
        return
    _XMP_KEYMAP_REGISTRATION_PENDING = True
    try:
        registered = _register_timer_once(_register_xmp_keymaps_impl, first_interval=0.0)
        if not registered:
            _XMP_KEYMAP_REGISTRATION_PENDING = False
            _register_xmp_keymaps_impl()
    except Exception:
        _XMP_KEYMAP_REGISTRATION_PENDING = False


# =========================
# 登録 / 解除
# =========================
CLASSES = (
    OBJECT_OT_render_with_xmp,
)


@persistent
def _on_xmp_file_load(_dummy):
    global _XMP_KEYMAP_REGISTRATION_PENDING
    _XMP_KEYMAP_REGISTRATION_PENDING = False
    _schedule_xmp_keymap_registration()


def register_xmp_rendering():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    _schedule_xmp_keymap_registration()
    if _on_xmp_file_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_xmp_file_load)


def unregister_xmp_rendering():
    global _XMP_KEYMAP_REGISTRATION_PENDING
    _XMP_KEYMAP_REGISTRATION_PENDING = False
    _unregister_timer(_register_xmp_keymaps_impl)
    if _on_xmp_file_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_xmp_file_load)
    _clear_tracked_xmp_keymaps()
    try:
        wm = bpy.context.window_manager
        kc = wm.keyconfigs.addon if wm else None
    except Exception:
        kc = None
    _remove_duplicate_xmp_keymaps(kc)
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


# -------------------------------
# ファイル名：xmp_rendering.py
# Version Footer: 1.203
# -------------------------------
