# -*- coding: utf-8 -*-
# ファイル名：__init__.py
# 00漫画用Camera Position Manager
# 変更点（1.184）:
# - ラティス管理の新規作成時にラティスも同時作成
# - 登録名とラティスOBJ名の同期に対応

bl_info = {
    "name": "00漫画用Camera Position Manager",
    "version": (1, 0, 184),
    "blender": (2, 80, 0),
    "category": "Object",
}

from . import core
from . import dolly
from . import all_object_data
from . import lattice_manager
from . import xmp_rendering
from . import ui

def register():
    core.register_core()
    dolly.register_dolly()
    all_object_data.register_all_object_data()
    lattice_manager.register_lattice_manager()
    xmp_rendering.register_xmp_rendering()
    ui.register_ui()

def unregister():
    ui.unregister_ui()
    xmp_rendering.unregister_xmp_rendering()
    lattice_manager.unregister_lattice_manager()
    all_object_data.unregister_all_object_data()
    dolly.unregister_dolly()
    core.unregister_core()

# -------------------------------
# ファイル名：__init__.py
# Version Footer: 1.184
# -------------------------------
