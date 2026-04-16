# -*- coding: utf-8 -*-
# ファイル名：__init__.py
# 00漫画用Camera Position Manager
# 変更点（1.113）:
# - メモ記録を条件付き上書きへ調整
# - 上書き条件外では通常記録へフォールバック
# - UIと機能は現状維持

bl_info = {
    "name": "00漫画用Camera Position Manager",
    "version": (1, 0, 113),
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
# Version Footer: 1.113
# -------------------------------
