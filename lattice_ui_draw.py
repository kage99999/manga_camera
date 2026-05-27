# -*- coding: utf-8 -*-
# ファイル名：lattice_ui_draw.py
# 00漫画用Camera Position Manager
# ラティス管理セクション UI描画
# 変更点（1.189）:
# - 登録セット操作ボタンを2段配置に変更
# - ラティス適用ボタンを追加

from . import lattice_manager as _lm

_ensure_active_lattice_index = _lm._ensure_active_lattice_index
_get_active_lattice_set = _lm._get_active_lattice_set
apply_lattice_set_activation_state = _lm.apply_lattice_set_activation_state
apply_lattice_management_enabled = _lm.apply_lattice_management_enabled
_safe_name = _lm._safe_name
_is_lattice_management_enabled = _lm._is_lattice_management_enabled
_valid_registered_object_count = _lm._valid_registered_object_count
_count_managed_lattice_modifiers = _lm._count_managed_lattice_modifiers
_count_managed_subdivision_modifiers = _lm._count_managed_subdivision_modifiers
_count_managed_modifiers = _lm._count_managed_modifiers
_count_any_managed_modifiers_for_set = _lm._count_any_managed_modifiers_for_set
_effective_lattice_set_enabled = _lm._effective_lattice_set_enabled
_iter_pending_delete_candidate_names = _lm._iter_pending_delete_candidate_names
_get_lattice_sets = _lm._get_lattice_sets
_is_lattice_multi_set_enabled = _lm._is_lattice_multi_set_enabled
_modifier_name_for_set = _lm._modifier_name_for_set
_sync_lattice_selected_object_display_items = _lm._sync_lattice_selected_object_display_items

# =========================
# UI描画ヘルパー
# =========================
def _draw_subpanel(layout, panel_id, title):
    """Blender標準寄りの小見出しを描く。"""
    try:
        header, body = layout.panel(panel_id, default_closed=False)
        if header is not None:
            header.label(text=title)
        return body
    except Exception:
        box = layout.box()
        box.label(text=title)
        return box


def _draw_lattice_set_header(layout, context, lattice_set):
    """登録セット、登録名、ラティスOBJを描く。"""
    scene = context.scene
    row = layout.row(align=True)
    row.label(text="登録セット：")
    if _get_lattice_sets(scene) is not None and len(scene.mpm_lattice_sets) > 0:
        row.prop(scene, "mpm_lattice_active_set_enum", text="")
    else:
        disabled = row.row(align=True)
        disabled.enabled = False
        disabled.label(text="未作成")

    row1 = layout.row(align=True)
    row1.operator("camera.lattice_add_set", text="＋新規")
    delete = row1.row(align=True)
    delete.enabled = lattice_set is not None
    delete.operator("camera.lattice_delete_set", text="削除")

    row2 = layout.row(align=True)
    duplicate = row2.row(align=True)
    duplicate.enabled = lattice_set is not None
    duplicate.operator("camera.lattice_duplicate_set", text="複製")
    apply = row2.row(align=True)
    apply.enabled = lattice_set is not None
    apply.operator("camera.lattice_apply_current_set_and_remove", text="適用")

    if lattice_set is None:
        info = layout.box()
        info.enabled = False
        info.label(text="登録セットがありません")
        return

    layout.prop(lattice_set, "set_name", text="登録名")
    layout.prop(lattice_set, "lattice_obj", text="ラティス")


def _draw_selected_objects_panel(layout, context, lattice_set):
    """3Dビューで選択中のOBJ一覧と登録ボタンを描く。"""
    body = _draw_subpanel(layout, "mpm_lattice_selected_objects_panel", "ラティス対象OBJ")
    if body is None:
        return
    count = _sync_lattice_selected_object_display_items(context)
    wm = context.window_manager
    body.label(text=f"選択数：{count}")
    if count > 0:
        body.template_list(
            "MPM_UL_lattice_selected_object_list", "",
            wm, "mpm_lattice_selected_display_items",
            wm, "mpm_lattice_selected_display_index",
            rows=4, maxrows=8,
        )
    else:
        empty = body.box()
        empty.enabled = False
        empty.label(text="ラティス対象OBJはありません")

    add_row = body.row(align=True)
    add_row.enabled = lattice_set is not None and count > 0
    add_row.operator("camera.lattice_register_selected_objects", text="ラティス登録OBJに追加")

def _draw_registered_objects_panel(layout, context, lattice_set):
    """ラティス登録OBJの一覧と削除ボタンを描く。"""
    body = _draw_subpanel(layout, "mpm_lattice_registered_objects_panel", "ラティス登録OBJ")
    if body is None:
        return
    count = len(lattice_set.objects) if lattice_set is not None else 0
    pending_count = len(_iter_pending_delete_candidate_names(lattice_set)) if lattice_set is not None else 0
    selected_names = {str(obj.name) for obj in getattr(context, "selected_objects", []) or [] if obj is not None}
    selected_registered_count = sum(
        1
        for item in (getattr(lattice_set, "objects", []) or [])
        if str(getattr(item, "object_name", "") or "") in selected_names
    ) if lattice_set is not None else 0
    status_text = f"登録数：{count}"
    if pending_count > 0:
        status_text += f" / 削除候補：{pending_count}"
    if selected_registered_count > 0:
        status_text += f" / 選択中：{selected_registered_count}"
    body.label(text=status_text)
    if lattice_set is not None and count > 0:
        body.template_list(
            "MPM_UL_lattice_registered_object_list", "",
            lattice_set, "objects",
            lattice_set, "object_index",
            rows=5, maxrows=10,
        )
    else:
        empty = body.box()
        empty.enabled = False
        empty.label(text="ラティス登録OBJはありません")

    remove_row = body.row(align=True)
    remove_row.enabled = lattice_set is not None and count > 0
    remove_row.operator("camera.lattice_remove_selected_objects", text="登録削除")
    remove_row.operator("camera.lattice_cancel_remove_selected_objects", text="削除取消")

def _draw_modifier_management_panel(layout, context, lattice_set):
    """ラティスモディファイア管理ボタンを描く。"""
    body = _draw_subpanel(layout, "mpm_lattice_modifier_management_panel", "モディファイア管理")
    if body is None:
        return
    lattice_obj = getattr(lattice_set, "lattice_obj", None) if lattice_set is not None else None
    registered_count = len(lattice_set.objects) if lattice_set is not None else 0
    valid_count = _valid_registered_object_count(lattice_set) if lattice_set is not None else 0
    managed_count = _count_any_managed_modifiers_for_set(lattice_set) if lattice_set is not None else 0
    mod_name = _modifier_name_for_set(lattice_set) if lattice_set is not None else "未作成"

    if lattice_obj is not None:
        body.label(text=f"対象ラティス：{lattice_obj.name}")
    else:
        warning = body.row(align=True)
        warning.alert = True
        warning.label(text="対象ラティス：ラティスを指定して下さい", icon='ERROR')
    body.label(text=f"モディファイア名：{mod_name}")

    if lattice_set is not None:
        body.prop(lattice_set, "use_subdivision", text="サブディビジョン付与")
        subd_col = body.column(align=True)
        subd_col.enabled = bool(getattr(lattice_set, "use_subdivision", False))
        subd_col.prop(lattice_set, "subdivision_levels", text="サブディビジョン数")
        modifier_enabled_row = body.row(align=True)
        if _is_lattice_multi_set_enabled(context.scene):
            modifier_enabled_row.prop(lattice_set, "modifiers_enabled", text="モディファイア有効")
        else:
            modifier_enabled_row.enabled = False
            modifier_enabled_row.label(text="モディファイア有効（カレント固定）", icon='CHECKBOX_HLT')

    ready = lattice_set is not None and lattice_obj is not None and registered_count > 0 and valid_count > 0

    fit_row = body.row(align=True)
    fit_row.enabled = ready
    fit_row.operator("camera.lattice_fit_to_registered_objects", text="ラティスをラティス登録OBJに合わせる")

    apply_row = body.row(align=True)
    apply_row.scale_y = 1.2
    apply_row.enabled = ready
    apply_row.operator("camera.lattice_apply_or_update_modifiers", text="追加 / 更新")

    # モディファイア単体削除ボタンはUIから撤去する。
    # 登録対象から外したい場合は「登録削除」→「追加 / 更新」で現在セット用MODも整理する。

def _draw_status_panel(layout, context, lattice_set):
    """現在セットの状態を描く。"""
    body = _draw_subpanel(layout, "mpm_lattice_status_panel", "状態")
    if body is None:
        return
    if lattice_set is None:
        disabled = body.column(align=True)
        disabled.enabled = False
        disabled.label(text="ラティス登録OBJ：0")
        disabled.label(text="モディファイアあり：0 / 0")
        disabled.label(text="ラティス：未指定")
        return

    registered_total = len(lattice_set.objects)
    valid_total = _valid_registered_object_count(lattice_set)
    managed_total = _count_managed_modifiers(lattice_set)
    lattice_total = _count_managed_lattice_modifiers(lattice_set)
    subdivision_total = _count_managed_subdivision_modifiers(lattice_set)
    use_subdivision = bool(getattr(lattice_set, "use_subdivision", False))
    lattice_obj = getattr(lattice_set, "lattice_obj", None)

    body.label(text=f"ラティス登録OBJ：{registered_total}")
    if registered_total != valid_total:
        warn = body.row(align=True)
        warn.alert = True
        warn.label(text=f"存在するOBJ：{valid_total} / {registered_total}")
    body.label(text=f"モディファイアあり：{managed_total} / {valid_total}")
    body.label(text=f"ラティスMOD：{lattice_total} / {valid_total}")
    if use_subdivision:
        body.label(text=f"サブディビジョン：{subdivision_total} / {valid_total}")

    lattice_row = body.row(align=True)
    lattice_row.enabled = lattice_obj is not None
    lattice_row.label(text="ラティス：指定済み" if lattice_obj is not None else "ラティス：未指定")




def draw_lattice_manager_panel(layout, context):
    """ラティス管理Panelの描画入口。既存UIを変えずに描画処理だけ分離する。"""
    scene = context.scene
    layout.prop(scene, "mpm_lattice_management_enabled", text="ラティス管理有効")
    management_enabled = bool(getattr(scene, "mpm_lattice_management_enabled", True))
    multi_row = layout.row(align=True)
    multi_row.enabled = management_enabled
    multi_row.prop(scene, "mpm_lattice_multi_set_enabled", text="複数登録セット使用")
    try:
        if management_enabled:
            apply_lattice_set_activation_state(scene)
        else:
            apply_lattice_management_enabled(scene, False)
    except Exception:
        pass
    _ensure_active_lattice_index(scene)
    lattice_set = _get_active_lattice_set(scene)
    body = layout.column(align=True)
    body.enabled = management_enabled
    _draw_lattice_set_header(body, context, lattice_set)
    if lattice_set is None:
        return
    _draw_selected_objects_panel(body, context, lattice_set)
    _draw_registered_objects_panel(body, context, lattice_set)
    _draw_modifier_management_panel(body, context, lattice_set)
    _draw_status_panel(body, context, lattice_set)


__all__ = ["draw_lattice_manager_panel"]

# -------------------------------
# ファイル名：lattice_ui_draw.py
# Version Footer: 1.189
# -------------------------------
