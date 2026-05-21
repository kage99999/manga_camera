# -*- coding: utf-8 -*-
# ファイル名：lattice_types.py
# 00漫画用Camera Position Manager
# ラティス管理セクション PropertyGroup / UIList
# 変更点（1.175）:
# - lattice_manager.py からPropertyGroup/UIListを分離

import bpy
from . import lattice_manager as _lm

_on_set_name_update = _lm._on_set_name_update
_poll_lattice_object = _lm._poll_lattice_object
_on_lattice_object_update = _lm._on_lattice_object_update
_on_lattice_set_modifier_enabled_update = _lm._on_lattice_set_modifier_enabled_update
_object_exists = _lm._object_exists
_is_delete_candidate_item = _lm._is_delete_candidate_item

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
    use_subdivision: bpy.props.BoolProperty(name="サブディビジョン付与", default=False)
    subdivision_levels: bpy.props.IntProperty(name="サブディビジョン数", default=2, min=0, max=6)
    modifiers_enabled: bpy.props.BoolProperty(name="モディファイア有効", default=True, update=_on_lattice_set_modifier_enabled_update)
    objects: bpy.props.CollectionProperty(type=MPM_LatticeRegisteredObjectItem)
    object_index: bpy.props.IntProperty(name="ラティス登録OBJ選択", default=0, options={'SKIP_SAVE'})


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




__all__ = [
    "MPM_LatticeRegisteredObjectItem",
    "MPM_LatticeSelectedObjectDisplayItem",
    "MPM_LatticeSetItem",
    "MPM_UL_lattice_selected_object_list",
    "MPM_UL_lattice_registered_object_list",
]

# -------------------------------
# ファイル名：lattice_types.py
# Version Footer: 1.175
# -------------------------------
