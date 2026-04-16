# -*- coding: utf-8 -*-
# ファイル名：__init__.py
# 00漫画用Camera Position Manager
# 変更点（1.106）:
# - 読込ダイアログとパス管理の整合性を改善
# - JSONダイアログ初期パスを安定化
# - UIと機能は現状維持

bl_info = {
    "name": "00漫画用Camera Position Manager",
    "version": (1, 0, 104),
    "blender": (2, 80, 0),
    "category": "Object",
}

from . import core
from . import dolly
from . import ui

def register():
    core.register_core()
    dolly.register_dolly()
    ui.register_ui()

def unregister():
    ui.unregister_ui()
    dolly.unregister_dolly()
    core.unregister_core()

# -------------------------------
# ファイル名：__init__.py
# Version Footer: 1.106
# -------------------------------
