# -*- coding: utf-8 -*-
# ファイル名：__init__.py
# 00漫画用Camera Position Manager
# 変更点（1.120）:
# - 選択中/記録済みOBJデータ一覧の幅指定を調整
# - 選択中OBJ一覧もボタン行表示へ変更
# - UIと機能は現状維持

bl_info = {
    "name": "00漫画用Camera Position Manager",
    "version": (1, 0, 120),
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
# Version Footer: 1.120
# -------------------------------
