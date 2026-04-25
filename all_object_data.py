# -*- coding: utf-8 -*-
# ファイル名：all_object_data.py
# 00漫画用Camera Position Manager
# 機能：全オブジェクト位置・回転・スケールの専用ストック管理
# 変更点（1.122）:
# - 全OBJデータの個別削除UIを追加
# - 全OBJデータ読込ボタン名を変更

import json
import os
import time
from datetime import datetime

import bpy

from .storage import (
    _safe_existing_dirpath,
    _safe_json_path,
    _write_json_atomic,
)


_ALL_OBJECT_DATA_MANAGER = None
_ALL_OBJECT_ENUM_CACHE: list[tuple[str, str, str]] = []


def _current_blend_file_name() -> str:
    filepath = getattr(bpy.data, "filepath", "") or ""
    if filepath:
        return os.path.basename(bpy.path.abspath(filepath))
    return "未保存.blend"


def _current_blend_base_name() -> str:
    name = _current_blend_file_name()
    root, _ext = os.path.splitext(name)
    return root or name or "未保存"


def _current_blend_dir() -> str:
    filepath = getattr(bpy.data, "filepath", "") or ""
    if filepath:
        return _safe_existing_dirpath(os.path.dirname(bpy.path.abspath(filepath)), fallback=os.path.expanduser("~"))
    return os.path.expanduser("~")


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


def _normalize_object_item(item) -> dict:
    if not isinstance(item, dict):
        item = {}
    return {
        "name": str(item.get("name", "") or ""),
        "location": _vec3(item.get("location", (0.0, 0.0, 0.0)), (0.0, 0.0, 0.0)),
        "rotation": _vec3(item.get("rotation", (0.0, 0.0, 0.0)), (0.0, 0.0, 0.0)),
        "scale": _vec3(item.get("scale", (1.0, 1.0, 1.0)), (1.0, 1.0, 1.0)),
    }


def _normalize_stock_item(item) -> dict:
    if not isinstance(item, dict):
        item = {}
    objects = item.get("objects", [])
    if not isinstance(objects, list):
        objects = []
    created_at = item.get("created_at", time.time())
    try:
        created_at = float(created_at)
    except Exception:
        created_at = time.time()
    return {
        "blend_file_name": str(item.get("blend_file_name", "") or "未保存.blend"),
        "created_at": created_at,
        "date_label": str(item.get("date_label", "") or _date_label_from_timestamp(created_at)),
        "memo": str(item.get("memo", "") or ""),
        "objects": [_normalize_object_item(obj) for obj in objects],
    }


def _normalize_stock_list(items) -> list:
    if not isinstance(items, list):
        return []
    result = []
    for item in items:
        try:
            result.append(_normalize_stock_item(item))
        except Exception:
            result.append(_normalize_stock_item({}))
    return result


def _date_label_from_timestamp(value: float) -> str:
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y_%m%d")
    except Exception:
        return datetime.now().strftime("%Y_%m%d")


def _object_signature(stock_item: dict) -> tuple:
    stock = _normalize_stock_item(stock_item)
    objects = []
    for obj in stock.get("objects", []):
        objects.append((
            obj.get("name", ""),
            tuple(obj.get("location", ())),
            tuple(obj.get("rotation", ())),
            tuple(obj.get("scale", ())),
        ))
    return (stock.get("blend_file_name", ""), tuple(objects))


def _build_current_stock_item(scene) -> dict:
    created_at = time.time()
    objects = []
    for obj in sorted(bpy.data.objects, key=lambda item: item.name.casefold()):
        objects.append({
            "name": str(obj.name),
            "location": list(obj.location),
            "rotation": list(obj.rotation_euler),
            "scale": list(obj.scale),
        })
    return {
        "blend_file_name": _current_blend_file_name(),
        "created_at": created_at,
        "date_label": _date_label_from_timestamp(created_at),
        "memo": str(getattr(scene, "all_object_data_memo_text", "") or ""),
        "objects": objects,
    }


def _default_json_dialog_filepath(manager) -> str:
    base_dir = _current_blend_dir()
    date_label = datetime.now().strftime("%Y_%m%d")
    filename = f"{_current_blend_base_name()}_全OBJデータ_[{date_label}].json"
    return os.path.join(base_dir, filename)


class AllObjectDataManager:
    def __init__(self):
        self.stocks = []
        self.save_file_path = _safe_json_path(
            os.path.join(bpy.utils.user_resource('CONFIG'), "all_object_transform_data.json")
        )

    def save_data(self, filepath=None):
        target_path = _safe_json_path(filepath or self.save_file_path, fallback_filename="all_object_transform_data.json")
        self.save_file_path = target_path
        self.stocks = _normalize_stock_list(self.stocks)
        data = {
            "all_object_data": self.stocks,
        }
        _write_json_atomic(target_path, data)

    def load_data(self, filepath=None):
        target_path = _safe_json_path(filepath or self.save_file_path, fallback_filename="all_object_transform_data.json")
        self.save_file_path = target_path
        if not os.path.exists(target_path):
            self.stocks = []
            self.save_data(target_path)
            return
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self.stocks = _normalize_stock_list(data.get("all_object_data", []))
            else:
                self.stocks = []
        except Exception:
            self.stocks = []


def get_all_object_data_manager():
    global _ALL_OBJECT_DATA_MANAGER
    if _ALL_OBJECT_DATA_MANAGER is None:
        _ALL_OBJECT_DATA_MANAGER = AllObjectDataManager()
    return _ALL_OBJECT_DATA_MANAGER


def rebuild_all_object_enum_cache(manager=None) -> None:
    if manager is None:
        manager = get_all_object_data_manager()
    _ALL_OBJECT_ENUM_CACHE.clear()
    for index, item in enumerate(_normalize_stock_list(getattr(manager, "stocks", []))):
        _ALL_OBJECT_ENUM_CACHE.append((str(index), _slot_display_label(index, item), ""))


def _slot_display_label(index: int, item: dict) -> str:
    stock = _normalize_stock_item(item)
    blend_name = stock.get("blend_file_name", "未保存.blend")
    date_label = stock.get("date_label", "")
    return f"{index + 1}: {blend_name} [{date_label}]"


def _safe_all_object_index(scene, manager, default=0) -> int:
    total = len(getattr(manager, "stocks", []) or [])
    if total <= 0:
        return 0
    try:
        index = int(getattr(scene, "all_object_data_index", str(default)) or default)
    except Exception:
        index = int(default)
    return max(0, min(index, total - 1))


def _set_all_object_index_safe(scene, manager, index=0) -> None:
    total = len(getattr(manager, "stocks", []) or [])
    if total <= 0:
        try:
            scene.all_object_data_index = "0"
        except Exception:
            pass
        return
    index = max(0, min(int(index), total - 1))
    try:
        scene.all_object_data_index = str(index)
    except Exception:
        pass


def _sync_scene_all_object_memo(scene, manager) -> None:
    items = _normalize_stock_list(getattr(manager, "stocks", []))
    memo_text = ""
    if items:
        memo_text = str(items[_safe_all_object_index(scene, manager)].get("memo", "") or "")
    try:
        scene.all_object_data_memo_text = memo_text
    except Exception:
        pass


def _get_all_object_items(self, context):
    return _ALL_OBJECT_ENUM_CACHE if _ALL_OBJECT_ENUM_CACHE else [("0", "(No Items)", "")]


def _update_all_object_index(self, context):
    manager = get_all_object_data_manager()
    _sync_scene_all_object_memo(context.scene, manager)


def _apply_stock_item(stock_item: dict) -> int:
    applied_count = 0
    stock = _normalize_stock_item(stock_item)
    for obj_data in stock.get("objects", []):
        obj = bpy.data.objects.get(obj_data.get("name", ""))
        if obj is None:
            continue
        try:
            obj.location = obj_data.get("location", list(obj.location))
            obj.rotation_euler = obj_data.get("rotation", list(obj.rotation_euler))
            obj.scale = obj_data.get("scale", list(obj.scale))
            applied_count += 1
        except Exception:
            continue
    return applied_count


class OBJECT_OT_record_all_object_data(bpy.types.Operator):
    bl_idname = "camera.record_all_object_data"
    bl_label = "全OBJデータを記録"

    def execute(self, context):
        scene = context.scene
        manager = get_all_object_data_manager()
        new_item = _normalize_stock_item(_build_current_stock_item(scene))
        new_sig = _object_signature(new_item)
        for item in _normalize_stock_list(manager.stocks):
            if _object_signature(item) == new_sig:
                self.report({'INFO'}, "同じblendファイル名・同じ全OBJデータのため記録しませんでした")
                return {'CANCELLED'}
        manager.stocks.append(new_item)
        manager.save_data()
        rebuild_all_object_enum_cache(manager)
        _set_all_object_index_safe(scene, manager, len(manager.stocks) - 1)
        _sync_scene_all_object_memo(scene, manager)
        self.report({'INFO'}, "全OBJデータを記録しました")
        return {'FINISHED'}


class OBJECT_OT_apply_all_object_data(bpy.types.Operator):
    bl_idname = "camera.apply_all_object_data"
    bl_label = "全OBJデータを読込"

    def _selected_stock(self, context):
        manager = get_all_object_data_manager()
        items = _normalize_stock_list(manager.stocks)
        if not items:
            return manager, None
        return manager, items[_safe_all_object_index(context.scene, manager)]

    def invoke(self, context, event):
        _manager, stock = self._selected_stock(context)
        if stock is None:
            self.report({'WARNING'}, "全OBJデータのストックがありません")
            return {'CANCELLED'}
        if stock.get("blend_file_name", "") != _current_blend_file_name():
            self.report({'WARNING'}, "操作中のblendファイルとスロットのblendファイルが同名でないと読み込みません")
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        self.layout.label(text="オブジェクト位置・回転・スケール全データを読み込みますか")

    def execute(self, context):
        _manager, stock = self._selected_stock(context)
        if stock is None:
            self.report({'WARNING'}, "全OBJデータのストックがありません")
            return {'CANCELLED'}
        if stock.get("blend_file_name", "") != _current_blend_file_name():
            self.report({'WARNING'}, "操作中のblendファイルとスロットのblendファイルが同名でないと読み込みません")
            return {'CANCELLED'}
        applied_count = _apply_stock_item(stock)
        self.report({'INFO'}, f"全OBJデータを読み込みました: {applied_count}個")
        return {'FINISHED'}


class OBJECT_OT_select_all_object_data(bpy.types.Operator):
    bl_idname = "camera.select_all_object_data"
    bl_label = "全OBJデータスロットを選択"

    index: bpy.props.IntProperty(default=0)

    def execute(self, context):
        manager = get_all_object_data_manager()
        total = len(manager.stocks)
        if total <= 0:
            self.report({'INFO'}, "全OBJデータのストックがありません")
            return {'CANCELLED'}
        _set_all_object_index_safe(context.scene, manager, self.index)
        _sync_scene_all_object_memo(context.scene, manager)
        return {'FINISHED'}


class OBJECT_OT_delete_all_object_data(bpy.types.Operator):
    bl_idname = "camera.delete_all_object_data"
    bl_label = "全OBJデータを削除"

    index: bpy.props.IntProperty(default=0)

    def _target_label(self):
        manager = get_all_object_data_manager()
        items = _normalize_stock_list(manager.stocks)
        if 0 <= self.index < len(items):
            return _slot_display_label(self.index, items[self.index])
        return ""

    def invoke(self, context, event):
        if not self._target_label():
            self.report({'WARNING'}, "削除対象の全OBJデータがありません")
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        self.layout.label(text="この全OBJデータスロットを削除しますか")
        label = self._target_label()
        if label:
            self.layout.label(text=label)

    def execute(self, context):
        scene = context.scene
        manager = get_all_object_data_manager()
        items = _normalize_stock_list(manager.stocks)
        if not (0 <= self.index < len(items)):
            self.report({'WARNING'}, "削除対象の全OBJデータがありません")
            return {'CANCELLED'}
        deleted_label = _slot_display_label(self.index, items[self.index])
        del items[self.index]
        manager.stocks = items
        manager.save_data()
        rebuild_all_object_enum_cache(manager)
        _set_all_object_index_safe(scene, manager, min(self.index, len(manager.stocks) - 1))
        _sync_scene_all_object_memo(scene, manager)
        self.report({'INFO'}, f"全OBJデータを削除しました: {deleted_label}")
        return {'FINISHED'}


class OBJECT_OT_prev_all_object_data(bpy.types.Operator):
    bl_idname = "camera.prev_all_object_data"
    bl_label = "前の全OBJデータへ"

    def execute(self, context):
        manager = get_all_object_data_manager()
        total = len(manager.stocks)
        if total <= 0:
            self.report({'INFO'}, "全OBJデータのストックがありません")
            return {'CANCELLED'}
        current = _safe_all_object_index(context.scene, manager)
        _set_all_object_index_safe(context.scene, manager, (current - 1) % total)
        _sync_scene_all_object_memo(context.scene, manager)
        return {'FINISHED'}


class OBJECT_OT_next_all_object_data(bpy.types.Operator):
    bl_idname = "camera.next_all_object_data"
    bl_label = "次の全OBJデータへ"

    def execute(self, context):
        manager = get_all_object_data_manager()
        total = len(manager.stocks)
        if total <= 0:
            self.report({'INFO'}, "全OBJデータのストックがありません")
            return {'CANCELLED'}
        current = _safe_all_object_index(context.scene, manager)
        _set_all_object_index_safe(context.scene, manager, (current + 1) % total)
        _sync_scene_all_object_memo(context.scene, manager)
        return {'FINISHED'}


class OBJECT_OT_save_all_object_data_json(bpy.types.Operator):
    bl_idname = "camera.save_all_object_data_json"
    bl_label = "全OBJデータを保存"

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")

    def execute(self, context):
        manager = get_all_object_data_manager()
        filepath = _safe_json_path(self.filepath, fallback_filename="all_object_transform_data.json")
        try:
            manager.save_data(filepath)
        except Exception as e:
            self.report({'ERROR'}, f"全OBJデータ保存に失敗しました: {e}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"全OBJデータを保存しました: {filepath}")
        return {'FINISHED'}

    def invoke(self, context, event):
        self.filepath = _default_json_dialog_filepath(get_all_object_data_manager())
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class OBJECT_OT_load_all_object_data_json(bpy.types.Operator):
    bl_idname = "camera.load_all_object_data_json"
    bl_label = "全OBJデータを読込む"

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")

    def execute(self, context):
        manager = get_all_object_data_manager()
        filepath = _safe_json_path(self.filepath, fallback_filename="all_object_transform_data.json")
        if not os.path.exists(filepath):
            self.report({'ERROR'}, f"ファイルが存在しません: {filepath}")
            return {'CANCELLED'}
        manager.load_data(filepath)
        rebuild_all_object_enum_cache(manager)
        _set_all_object_index_safe(context.scene, manager, 0)
        _sync_scene_all_object_memo(context.scene, manager)
        self.report({'INFO'}, f"全OBJデータを読込しました: {filepath}")
        return {'FINISHED'}

    def invoke(self, context, event):
        self.filepath = _default_json_dialog_filepath(get_all_object_data_manager())
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


def draw_all_object_data_controls(layout, context):
    scene = context.scene
    manager = get_all_object_data_manager()
    rebuild_all_object_enum_cache(manager)

    box = layout.box()
    box.label(text="全OBJデータ記録")
    box.prop(scene, "all_object_data_memo_text", text="摘要メモ")

    row = box.row(align=True)
    row.scale_y = 1.2
    row.operator("camera.record_all_object_data", text="記録")
    row.operator("camera.apply_all_object_data", text="読込")

    if manager.stocks:
        try:
            box.prop(scene, "all_object_data_index", text="")
        except Exception:
            rebuild_all_object_enum_cache(manager)
            box.prop(scene, "all_object_data_index", text="")

        row = box.row(align=True)
        row.operator("camera.prev_all_object_data", text="<")
        current = _safe_all_object_index(scene, manager)
        sub = row.row()
        sub.alignment = 'CENTER'
        sub.label(text=f"{current + 1} / {len(manager.stocks)}")
        row.operator("camera.next_all_object_data", text=">")
        box.label(text=f"スロット数: {len(manager.stocks)}")

        list_box = box.box()
        list_box.label(text="スロット一覧")
        current = _safe_all_object_index(scene, manager)
        for index, item in enumerate(_normalize_stock_list(manager.stocks)):
            row = list_box.row(align=True)
            text = _slot_display_label(index, item)
            if index == current:
                text = f"選択中: {text}"
            select_op = row.operator("camera.select_all_object_data", text=text)
            select_op.index = index
            delete_op = row.operator("camera.delete_all_object_data", text="削除", icon='TRASH')
            delete_op.index = index
    else:
        box.label(text="全OBJデータのストックがありません")

    row = box.row(align=True)
    row.operator("camera.save_all_object_data_json", text="JSON保存")
    row.operator("camera.load_all_object_data_json", text="JSON読込")


_CLASSES = (
    OBJECT_OT_record_all_object_data,
    OBJECT_OT_apply_all_object_data,
    OBJECT_OT_select_all_object_data,
    OBJECT_OT_delete_all_object_data,
    OBJECT_OT_prev_all_object_data,
    OBJECT_OT_next_all_object_data,
    OBJECT_OT_save_all_object_data_json,
    OBJECT_OT_load_all_object_data_json,
)


def register_all_object_data():
    bpy.types.Scene.show_all_object_data_section = bpy.props.BoolProperty(
        name="全OBJデータ記録",
        description="全オブジェクト位置・回転・スケール記録UIを表示します",
        default=False,
    )
    bpy.types.Scene.all_object_data_memo_text = bpy.props.StringProperty(
        name="摘要メモ",
        description="全OBJデータの選択中スロットに紐づく摘要メモ",
        default="",
        options={'TEXTEDIT_UPDATE'},
    )
    bpy.types.Scene.all_object_data_index = bpy.props.EnumProperty(
        name="全OBJデータスロット",
        description="全OBJデータの保存スロット",
        items=_get_all_object_items,
        update=_update_all_object_index,
    )

    for cls in _CLASSES:
        bpy.utils.register_class(cls)

    manager = get_all_object_data_manager()
    manager.load_data()
    rebuild_all_object_enum_cache(manager)


def unregister_all_object_data():
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass

    for attr in ("show_all_object_data_section", "all_object_data_memo_text", "all_object_data_index"):
        if hasattr(bpy.types.Scene, attr):
            try:
                delattr(bpy.types.Scene, attr)
            except Exception:
                pass

    global _ALL_OBJECT_DATA_MANAGER
    _ALL_OBJECT_DATA_MANAGER = None


# -------------------------------
# ファイル名：all_object_data.py
# Version Footer: 1.122
# -------------------------------
