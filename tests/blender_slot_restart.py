"""Two-process persistence check, optionally with a real N-panel draw on resume.

Set MPM_SLOT_TEST_ROOT to a directory under this repository's dist folder.
Set MPM_SLOT_TEST_PHASE to seed or resume. GUI resume also emits a screenshot.
"""
import importlib
import json
import os
from pathlib import Path
import sys
import traceback
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
work = Path(os.environ['MPM_SLOT_TEST_ROOT']).resolve()
assert work.is_relative_to((ROOT / 'dist').resolve())
work.mkdir(parents=True, exist_ok=True)
profile = work / 'config'
profile.mkdir(exist_ok=True)
original_resource = bpy.utils.user_resource
resource = patch.object(bpy.utils, 'user_resource', side_effect=lambda kind, *a, **kw:
                        str(profile) if kind == 'CONFIG' else original_resource(kind, *a, **kw))
resource.start()
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT.parent))
addon = importlib.import_module(ROOT.name)

errors, draws = [], []


def wrap_draw(original):
    def draw(self, context):
        try:
            original(self, context)
            draws.append(self.bl_idname)
        except Exception:
            errors.append(traceback.format_exc())
    return draw


if not bpy.app.background:
    for cls in (addon.ui.VIEW3D_PT_custom_panel_record_read, addon.ui.VIEW3D_PT_custom_panel_misc):
        cls.draw = wrap_draw(cls.draw)
        cls.bl_category = 'Item'
        cls.bl_options = set()
        cls.bl_order = -100

addon.register()
manager = addon.core.get_camera_data_manager()
lib = manager.slot_library
assert not lib.error, lib.error
scene = bpy.context.scene
scene.frame_set(11)
scene.camera.location = (8, 9, 10)
scene.saved_memo_text = 'must be refreshed'

if os.environ['MPM_SLOT_TEST_PHASE'] == 'seed':
    chosen = work / 'chosen-slots'
    chosen.mkdir(exist_ok=True)
    lib.set_folder(str(chosen))
    lib.create('50話')
    first = lib.active_id
    manager.save_data(camera_data=[{'position': [1, 2, 3], 'frame_current': 87, 'memo': 'slot-50',
                                    'bg_image': '590811.png'}],
                      background_image_folder_path=str(work / 'background-50'),
                      output_folder_path=str(work / 'output-50'))
    lib.create('51話')
    manager.save_data(camera_data=[{'memo': 'slot-51'}],
                      background_image_folder_path=str(work / 'background-51'),
                      output_folder_path=str(work / 'output-51'))
    lib.select(first)
    print('SLOT RESTART SEED OK')
    addon.unregister()
    resource.stop()
else:
    assert lib.active['name'] == '50話'
    assert Path(lib.folder) == work / 'chosen-slots'
    assert manager.saved_camera_data[0]['memo'] == 'slot-50'
    assert Path(manager.output_folder_path) == work / 'output-50'
    assert Path(manager.background_image_folder_path) == work / 'background-50'
    assert scene.frame_current == 11
    assert tuple(scene.camera.location) == (8, 9, 10)
    if bpy.app.background:
        addon.slots.refresh_ui()
        assert scene.saved_memo_text == 'slot-50'
        addon.unregister()
        addon.register()
        assert addon.core.get_camera_data_manager().slot_library.active['name'] == '50話'
        addon.unregister()
        resource.stop()
        print('SLOT RESTART RESUME / RE-REGISTER OK')
    else:
        bpy.context.preferences.view.show_splash = False
        bpy.context.preferences.filepaths.temporary_directory = str(work)
        scene.show_settings = True

        def open_panels():
            for area in bpy.context.screen.areas:
                if area.type == 'VIEW_3D':
                    area.spaces.active.show_region_ui = True
                    area.tag_redraw()

        def finish():
            try:
                assert scene.frame_current == 11
                assert tuple(scene.camera.location) == (8, 9, 10)
                assert scene.saved_memo_text == 'slot-50'
                assert draws and not errors, errors
                bpy.ops.screen.screenshot(filepath=str(work / 'slots-ui.png'))
            except Exception:
                errors.append(traceback.format_exc())
            (work / 'ui-result.json').write_text(json.dumps({'draws': sorted(set(draws)), 'errors': errors}, indent=2), encoding='utf-8')
            addon.unregister()
            resource.stop()
            bpy.ops.wm.quit_blender()

        bpy.app.timers.register(open_panels, first_interval=1)
        bpy.app.timers.register(finish, first_interval=6)
