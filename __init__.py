# -*- coding: utf-8 -*-
# ファイル名：__init__.py
# 00漫画用Camera Position Manager
# 変更点（1.154）:
# - 登録OBJ一覧のチェック削除候補化とクリック選択を追加

bl_info = {
    "name": "00漫画用Camera Position Manager",
    "version": (1, 0, 154),
    "blender": (2, 80, 0),
    "category": "Object",
}

from . import core
from . import dolly
from . import all_object_data
from . import lattice_manager
from . import ui

def register():
    core.register_core()
    dolly.register_dolly()
    all_object_data.register_all_object_data()
    lattice_manager.register_lattice_manager()
    ui.register_ui()

def unregister():
    ui.unregister_ui()
    lattice_manager.unregister_lattice_manager()
    all_object_data.unregister_all_object_data()
    dolly.unregister_dolly()
    core.unregister_core()

# -------------------------------
# ファイル名：__init__.py
# Version Footer: 1.154
# -------------------------------
