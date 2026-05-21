# -*- coding: utf-8 -*-
# ファイル名：xmp_rendering.py
# 00漫画用Camera Position Manager
# 変更点（1.180）:
# - CompositorのFile Output等で書き出されたPNGへXMPメタデータを後付けするレンダリング機能を追加
# - XMP対象フォルダをSceneプロパティとして追加
# - Shift + F12 のショートカットを追加
# - tiff:Makeを既存のカメラ製造元情報へ戻し、現在フレーム数はexifEX:LensMake（レンズメーカー欄）へ追加
# - レンダリング開始を完了通知と同じレポート通知位置へ出すため、タイマー経由の実行へ変更

import bpy
import os
import time
import zlib
import re
from html import escape
from pathlib import Path

from .core import _get_valid_scene_camera, _register_timer_once


# =========================
# 定数
# =========================
ADDON_VERSION_STR = "1.180"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_XMP_KEYWORD = "XML:com.adobe.xmp"
_RENDER_OPERATOR_ID = "camera.render_with_xmp"
_MISSING_TARGET_MESSAGE = "対象ファイルが見つかりません。フォルダパスがあってるか、コンポジターの指定で######がついているか確認して下さい。"

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
        for value in list(camera.rotation_euler)[:3]:
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

    with open(png_path, "wb") as handle:
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
    scene = context.scene
    target_folder = str(getattr(scene, "mpm_xmp_target_folder", "") or "")

    folder_box = layout.box()
    folder_box.label(text="XMP対象フォルダ")
    folder_row = folder_box.row(align=True)
    folder_row.operator("camera.set_xmp_target_folder", text="フォルダを指定", icon='FILE_FOLDER')
    folder_row.operator("camera.clear_xmp_target_folder", text="", icon='X')
    current_row = folder_box.row()
    current_row.enabled = False
    current_row.label(text=target_folder if target_folder else "未設定：XMP付与せず通常レンダリング")

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
class OBJECT_OT_set_xmp_target_folder(bpy.types.Operator):
    bl_idname = "camera.set_xmp_target_folder"
    bl_label = "XMP対象フォルダを指定"
    bl_description = "XMPメタデータを付与するPNGを探す対象フォルダを指定します"

    directory: bpy.props.StringProperty(
        name="XMP対象フォルダ",
        description="XMPメタデータを付与するPNGを探す対象フォルダ",
        default="",
        subtype='DIR_PATH',
    )

    def invoke(self, context, event):
        current = str(getattr(context.scene, "mpm_xmp_target_folder", "") or "")
        if current:
            try:
                self.directory = bpy.path.abspath(current)
            except Exception:
                self.directory = current
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        context.scene.mpm_xmp_target_folder = str(self.directory or "")
        self.report({'INFO'}, "XMP対象フォルダを設定しました")
        return {'FINISHED'}


class OBJECT_OT_clear_xmp_target_folder(bpy.types.Operator):
    bl_idname = "camera.clear_xmp_target_folder"
    bl_label = "XMP対象フォルダをクリア"
    bl_description = "XMP対象フォルダを空白に戻します。空白の場合はレンダリングのみ行い、XMP付与は行いません"

    def execute(self, context):
        context.scene.mpm_xmp_target_folder = ""
        self.report({'INFO'}, "XMP対象フォルダをクリアしました")
        return {'FINISHED'}


class OBJECT_OT_render_with_xmp(bpy.types.Operator):
    bl_idname = _RENDER_OPERATOR_ID
    bl_label = "XMP付与レンダリング"
    bl_description = "通常通りレンダリングし、指定フォルダ内の現在フレームPNGへXMPメタデータを付与します"

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
        target_folder_text = str(getattr(scene, "mpm_xmp_target_folder", "") or "").strip()
        target_folder = _resolve_target_folder(target_folder_text)
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

        if not target_folder_text:
            _clear_status_text(context)
            self.report({'INFO'}, "レンダリング完了：XMP対象フォルダ未設定のため、XMP付与は行いませんでした")
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
        kmi = km.keymap_items.new(_RENDER_OPERATOR_ID, type='F12', value='PRESS', shift=True)
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
        _register_timer_once(_register_xmp_keymaps_impl, first_interval=0.0)
    except Exception:
        _XMP_KEYMAP_REGISTRATION_PENDING = False


# =========================
# 登録 / 解除
# =========================
CLASSES = (
    OBJECT_OT_set_xmp_target_folder,
    OBJECT_OT_clear_xmp_target_folder,
    OBJECT_OT_render_with_xmp,
)


def register_xmp_rendering():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.mpm_xmp_target_folder = bpy.props.StringProperty(
        name="XMP対象フォルダ",
        description="XMPメタデータを付与するPNGを探すフォルダ。空白の場合はレンダリングのみ行い、XMP付与は行いません",
        default="",
        subtype='DIR_PATH',
    )
    _schedule_xmp_keymap_registration()


def unregister_xmp_rendering():
    global _XMP_KEYMAP_REGISTRATION_PENDING
    _XMP_KEYMAP_REGISTRATION_PENDING = False
    _clear_tracked_xmp_keymaps()
    try:
        wm = bpy.context.window_manager
        kc = wm.keyconfigs.addon if wm else None
    except Exception:
        kc = None
    _remove_duplicate_xmp_keymaps(kc)
    if hasattr(bpy.types.Scene, "mpm_xmp_target_folder"):
        try:
            del bpy.types.Scene.mpm_xmp_target_folder
        except Exception:
            pass
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


# -------------------------------
# ファイル名：xmp_rendering.py
# Version Footer: 1.180
# -------------------------------
