"""Install the release ZIP in an explicitly isolated Blender user profile."""
import ast
from pathlib import Path
import sys

import addon_utils
import bpy

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
for resource in ('SCRIPTS', 'CONFIG'):
    target = Path(bpy.utils.user_resource(resource)).resolve()
    assert target.is_relative_to((ROOT / 'dist').resolve()), target
tree = ast.parse((ROOT / '__init__.py').read_text(encoding='utf-8-sig'))
info = next(ast.literal_eval(node.value) for node in tree.body
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == 'bl_info' for target in node.targets))
label = f"{info['version'][0]}_{info['version'][2]:03d}"
archive = ROOT / 'dist' / f'00_manga_camera_ver_{label}.zip'
assert bpy.ops.preferences.addon_install(filepath=str(archive)) == {'FINISHED'}
name = '00_manga_camera'
for cycle in range(2):
    addon = addon_utils.enable(name, default_set=False, persistent=False)
    assert addon is not None, 'Restricted-context registration failed'
    assert Path(addon.__file__).resolve().is_relative_to((ROOT / 'dist').resolve())
    assert addon.bl_info['version'] == info['version']
    library = addon.core.get_camera_data_manager().slot_library
    assert library is not None and not library.error, getattr(library, 'error', None)
    assert library.active_id
    assert hasattr(bpy.types.WindowManager, 'mpm_stock_slot')
    assert hasattr(bpy.types.WindowManager, 'mpm_stock_slot_name')
    assert bpy.types.Panel.bl_rna_get_subclass_py('VIEW3D_PT_custom_panel_fisheye')
    if bpy.app.version >= (5, 0, 0):
        assert addon.fisheye.available()
        engine = addon.gyogan_camera
        engine._cancel_registration_timer()
        assert engine._finish_registration() is None
        engine.targets._tick()
        scene = bpy.context.scene
        assert scene.gyogan.presets
        scene.gyogan.enabled = True
        scene.gyogan.subdivision = 'NONE'
        obj = bpy.data.objects['Cube']
        engine.setup_mesh(scene, obj)
        engine.targets.reconcile(scene)
        modifier = next(mod for mod in obj.modifiers if mod.type == 'NODES')
        assert modifier.show_viewport, (cycle, scene.gyogan.enabled, scene.gyogan.camera)
    addon_utils.disable(name, default_set=False)
    assert not hasattr(bpy.types.Scene, 'gyogan')
    assert not hasattr(bpy.types.Scene, 'gyogan_dolly')
    assert not hasattr(bpy.types.Scene, 'record_selected_objects')
    assert not hasattr(bpy.types.WindowManager, 'mpm_stock_slot')
    assert not hasattr(bpy.types.WindowManager, 'mpm_stock_slot_name')
    if bpy.app.version >= (5, 0, 0):
        assert not modifier.show_viewport
        assert not bpy.app.timers.is_registered(engine.targets._tick)
        assert not bpy.app.timers.is_registered(engine._finish_registration)
print(f'INSTALL OK: {label}, Blender {bpy.app.version_string}, enable/disable twice')
