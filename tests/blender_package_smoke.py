"""Validate and register the packaged add-on in a factory-startup Blender."""
import ast
import importlib
from pathlib import Path
import sys
from unittest.mock import patch
import zipfile

import bpy

sys.dont_write_bytecode = True
root = Path(__file__).resolve().parents[1]
tree = ast.parse((root / '__init__.py').read_text(encoding='utf-8-sig'))
info = next(ast.literal_eval(node.value) for node in tree.body
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == 'bl_info' for target in node.targets))
version = info['version']
label = f'{version[0]}.{version[2]:03d}'
path = root / 'dist' / f'00_manga_camera_ver_{label.replace(".", "_")}.zip'
sources = list(root.glob('*.py')) + list((root / 'gyogan_camera').glob('*.py'))
with zipfile.ZipFile(path) as archive:
    assert archive.testzip() is None
    assert set(archive.namelist()) == {'00_manga_camera/' + source.relative_to(root).as_posix() for source in sources}
    for source in sources:
        content = archive.read('00_manga_camera/' + source.relative_to(root).as_posix())
        assert content == source.read_bytes(), source.name
        text = content.decode('utf-8-sig')
        ast.parse(text)
        if source.parent == root:
            assert label in '\n'.join(text.splitlines()[:12]), source.name
        if '# Version Footer:' in text:
            assert f'# Version Footer: {label}' in text, source.name

sys.path.insert(0, str(path))
addon = importlib.import_module('00_manga_camera')
assert addon.__file__.startswith(str(path))
assert addon.bl_info['version'] == version
with patch.object(addon.core.CameraDataManager, 'load_data'), patch.object(
        addon.all_object_data.AllObjectDataManager, 'load_data'), patch.object(addon.core.slot_store, 'initialize'):
    addon.register()
    assert addon.core.ADDON_VERSION_STR == label
    assert addon.xmp_rendering.ADDON_VERSION_STR == label
    assert label in addon.ui.VIEW3D_PT_custom_panel.bl_label
    assert not hasattr(bpy.types.Scene, 'mpm_record_keyframes')
    assert not hasattr(bpy.types.Scene, 'mpm_move_to_recorded_frame')
    assert not bpy.context.scene.mpm_pose_keyframe_priority
    assert addon.fisheye.available() == (bpy.app.version >= (5, 0, 0))
    if addon.fisheye.available():
        assert addon.gyogan_camera.__file__.startswith(str(path))
        assert not bpy.types.Panel.bl_rna_get_subclass_py('GYOGAN_PT_main')
    addon.unregister()
    assert not hasattr(bpy.types.Scene, 'record_selected_objects')
    assert not hasattr(bpy.types.Scene, 'mpm_pose_keyframe_priority')
    assert not hasattr(bpy.types.Scene, 'gyogan')
    addon.register()
    addon.unregister()
print(f'PACKAGE OK: {label}, {len(sources)} modules, byte match, register/unregister')
