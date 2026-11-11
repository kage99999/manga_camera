# -*- coding: utf-8 -*-
# ファイル名：__init__.py
# 00漫画用Camera Position Manager
# 変更点（1.220）:
# - 下絵ファイル名から名前変更できる操作を追加

bl_info = {
    "name": "00漫画用Camera Position Manager",
    "version": (1, 0, 220),
    "blender": (2, 80, 0),
    "category": "Object",
}

from . import core
from . import dolly
from . import all_object_data
from . import lattice_manager
from . import xmp_rendering
from . import ui
from . import fisheye
from . import slots
from . import local_view

def register():
    steps = (
        (core.register_core, core.unregister_core),
        (slots.register_slots, slots.unregister_slots),
        (dolly.register_dolly, dolly.unregister_dolly),
        (all_object_data.register_all_object_data, all_object_data.unregister_all_object_data),
        (lattice_manager.register_lattice_manager, lattice_manager.unregister_lattice_manager),
        (xmp_rendering.register_xmp_rendering, xmp_rendering.unregister_xmp_rendering),
        (fisheye.register_fisheye, fisheye.unregister_fisheye),
        (ui.register_ui, ui.unregister_ui),
        (local_view.register, local_view.unregister),
    )
    started = []
    try:
        for setup, cleanup in steps:
            started.append(cleanup)
            setup()
    except Exception:
        for cleanup in reversed(started):
            try:
                cleanup()
            except Exception as exc:
                print(f"アドオン登録の巻き戻し: {cleanup.__name__}: {exc}")
        raise

def unregister():
    local_view.unregister()
    ui.unregister_ui()
    fisheye.unregister_fisheye()
    xmp_rendering.unregister_xmp_rendering()
    lattice_manager.unregister_lattice_manager()
    all_object_data.unregister_all_object_data()
    dolly.unregister_dolly()
    slots.unregister_slots()
    core.unregister_core()

# -------------------------------
# ファイル名：__init__.py
# Version Footer: 1.220
# -------------------------------
