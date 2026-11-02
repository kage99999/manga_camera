"""Render batch controls and imported stocks in a disposable GUI process."""
import json
import os
from pathlib import Path
import sys
import traceback

import bpy

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_batch_import as test

base = test.base
errors, drawn = [], set()
targets = {'VIEW3D_PT_custom_panel_record_read', 'VIEW3D_PT_custom_panel_misc'}
settings_only = bool(os.environ.get('MPM_BATCH_UI_SETTINGS'))
if settings_only:
    targets = {'VIEW3D_PT_custom_panel_misc'}


def trace(original, name):
    def draw(self, context):
        drawn.add(name)
        try:
            original(self, context)
        except Exception:
            errors.append(traceback.format_exc())
    return draw


for cls in base.u.UI_CLASSES:
    if issubclass(cls, bpy.types.Panel):
        cls.bl_category = 'Item'
        cls.bl_options = set() if cls.__name__ in targets else {'DEFAULT_CLOSED'}
        cls.draw = trace(cls.draw, cls.__name__)

test.BatchRegression.setUpClass()
case = test.BatchRegression()
case.setUp()
case.scene.show_settings = True
bpy.context.preferences.view.show_splash = False
for index in (101001, 101002, 101003):
    case.png(f'{index:06d}.png', 500, 600)
area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
area.spaces.active.show_region_ui = True
region = next(r for r in area.regions if r.type == 'WINDOW')
stem = 'batch_ui_' + base.c.ADDON_VERSION_STR.replace('.', '_')
if settings_only:
    stem += '_settings'
step_index = 0


def step():
    global step_index
    try:
        if step_index == 0:
            with bpy.context.temp_override(area=area, region=region):
                assert bpy.ops.camera.batch_load_background_images(directory=str(case.images)) == {'FINISHED'}
        elif step_index == 1:
            assert len(case.manager.saved_camera_data) == 3
            bpy.ops.screen.screenshot(filepath=str(base.ROOT / 'dist' / (stem + '.png')))
            with bpy.context.temp_override(area=area, region=region):
                assert bpy.ops.camera.reload_current_saved_stock() == {'FINISHED'}
        else:
            assert case.scene.frame_current == 101001
            assert targets.issubset(drawn), drawn
            (base.ROOT / 'dist' / (stem + '.json')).write_text(json.dumps({
                'errors': errors, 'drawn': sorted(drawn), 'frame': case.scene.frame_current,
                'stocks': len(case.manager.saved_camera_data),
            }, indent=2), encoding='utf-8')
            test.BatchRegression.tearDownClass()
            bpy.ops.wm.quit_blender()
            return None
    except Exception:
        errors.append(traceback.format_exc())
        (base.ROOT / 'dist' / (stem + '.json')).write_text(json.dumps({'errors': errors}), encoding='utf-8')
        bpy.ops.wm.quit_blender()
        return None
    step_index += 1
    area.tag_redraw()
    return 1.5


bpy.app.timers.register(step, first_interval=2.0)
