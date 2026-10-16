"""Run the supplied upstream suite through the FULL manga_camera registration.

Blender --background --factory-startup --python-exit-code 1 --python this.py -- smoke
The original test source is extracted, unchanged, from the user's handoff ZIP.
"""
import importlib
import os
from pathlib import Path
import runpy
import sys
from types import ModuleType
import zipfile

import bpy

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
suite = sys.argv[sys.argv.index('--') + 1]
SUITES = ('smoke', 'persistence', 'center', 'negative', 'ellipse', 'dolly',
          'dolly_navigation', 'camera_switch', 'bone_compat', 'framing',
          'targets', 'subdivision', 'presets', 'cursor', 'helpers')
assert suite in SUITES, suite
output = ROOT / 'dist' / 'fisheye-upstream' / bpy.app.version_string / suite
output.mkdir(parents=True, exist_ok=True)
os.environ['GYOGAN_TEST_OUTPUT_DIR'] = str(output)
source = output / 'source'
with zipfile.ZipFile(ROOT / '統合したい魚眼カメラ' / '引継ぎ用.zip') as archive:
    prefix = 'gyogan_camera_handoff/source/tests/'
    for name in archive.namelist():
        if not name.startswith(prefix) or not name.endswith('.py'):
            continue
        destination = source / 'tests' / name[len(prefix):]
        assert destination.resolve().is_relative_to(source.resolve())
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(archive.read(name))

sys.path.insert(0, str(ROOT.parent))
parent = importlib.import_module(ROOT.name)
engine = importlib.import_module(ROOT.name + '.gyogan_camera')

# Only the test import is adapted. Production imports and RNA paths are untouched.
proxy = ModuleType('gyogan_camera')
proxy.__getattr__ = lambda name: getattr(engine, name)
registrations = []


def register_integrated():
    # unregister() clears both managers; isolate every subsequent enable too.
    parent.core._CAMERA_DATA_MANAGER = parent.core.CameraDataManager()
    parent.core._CAMERA_DATA_MANAGER.save_file_path = str(output / 'camera.json')
    parent.all_object_data._ALL_OBJECT_DATA_MANAGER = parent.all_object_data.AllObjectDataManager()
    parent.all_object_data._ALL_OBJECT_DATA_MANAGER.save_file_path = str(output / 'all.json')
    from unittest.mock import patch
    with patch.object(parent.core.slot_store, 'initialize', side_effect=parent.core._load_initial_stock_data):
        parent.register()
    assert parent.fisheye.available()
    assert bpy.types.Panel.bl_rna_get_subclass_py('VIEW3D_PT_custom_panel_fisheye')
    assert not bpy.types.Panel.bl_rna_get_subclass_py('GYOGAN_PT_main')
    registrations.append(True)


proxy.register = register_integrated
proxy.unregister = parent.unregister
sys.modules['gyogan_camera'] = proxy
for name in ('nodes', 'armature', 'dolly', 'cameras', 'targets', 'framing', 'subdivision', 'presets'):
    sys.modules['gyogan_camera.' + name] = getattr(engine, name)

try:
    if suite == 'bone_compat':
        # This upstream suite tests node helpers without registering any addon.
        register_integrated()
    runpy.run_path(str(source / 'tests' / ('blender_' + suite + '.py')), run_name='__main__')
    assert registrations, 'Test did not register the integrated engine'
finally:
    if parent.fisheye.available():
        parent.unregister()
