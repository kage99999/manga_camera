"""Event-loop rendering and persistence of local-view warnings in disposable GUI."""
import json
from pathlib import Path
import sys
import traceback

import bpy

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_regression as base

lv = base.addon.local_view
drawn, errors, steps, overlay_calls = set(), [], [], []

def trace_draw(original, name):
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
        cls.bl_options = set(getattr(cls, 'bl_options', ())) - {'DEFAULT_CLOSED'}
        cls.draw = trace_draw(cls.draw, cls.__name__)

original_draw = lv.draw_warning
def draw_warning():
    try:
        if lv.warning_camera(bpy.context):
            overlay_calls.append(index)
        original_draw()
    except Exception:
        errors.append(traceback.format_exc())
lv.draw_warning = draw_warning
base.Regression.setUpClass()
case = base.Regression()
case.setUp()
case.scene.record_view_layer_exclude_state = True
case.scene.record_local_view_state = True
case.scene.record_selected_objects = True
case.scene.saved_memo_text = '配置確認'
case.scene.mpm_lattice_management_enabled = False
bpy.context.preferences.view.show_splash = False
area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
region = next(r for r in area.regions if r.type == 'WINDOW')
space = area.spaces.active
space.show_region_ui = True
obj = bpy.data.objects['Cube']
index = 0
record = None
stem = 'local_view_ui_' + base.x.ADDON_VERSION_STR.replace('.', '_') + '_' + str(bpy.app.version[0]) + '_' + str(bpy.app.version[1])

def screenshot(suffix):
    bpy.ops.screen.screenshot(filepath=str(base.ROOT / 'dist' / (stem + suffix + '.png')))

def finish():
    (base.ROOT / 'dist' / (stem + '.json')).write_text(json.dumps({
        'steps': steps, 'errors': errors, 'drawn': sorted(drawn),
        'warning_draw_steps': sorted(set(overlay_calls)),
    }, indent=2), encoding='utf-8')
    base.Regression.tearDownClass()
    assert lv._draw_handle is None
    bpy.ops.wm.quit_blender()

def step():
    global index, record
    try:
        with bpy.context.temp_override(area=area, region=region):
            if index == 0:
                lv.apply(bpy.context, {'record_view_layer_exclude_state': True,
                                      'local_view_state': {'enabled': True, 'objects': [obj.name]}})
                space.region_3d.view_perspective = 'CAMERA'
                bpy.context.view_layer.update()
                assert bpy.ops.camera.save_position() == {'FINISHED'}
                record = case.manager.saved_camera_data[-1]
                assert record['local_view_state']['objects'] == [obj.name]
            elif index == 1:
                assert lv.warning_camera(bpy.context) == case.camera
                assert overlay_calls
                screenshot('_warning')
                case.camera.local_view_set(space, True)
            elif index == 2:
                assert lv.warning_camera(bpy.context) is None
                screenshot('_camera_included')
                bpy.ops.camera.reload_current_saved_stock()
            elif index == 3:
                assert lv.warning_camera(bpy.context) == case.camera
                space.overlay.show_overlays = False
                space.region_3d.view_camera_zoom = 100
            elif index == 4:
                assert lv.warning_camera(bpy.context) == case.camera
                screenshot('_footer')
                space.region_3d.view_perspective = 'PERSP'
            elif index == 5:
                assert lv.warning_camera(bpy.context) is None
                space.region_3d.view_perspective = 'CAMERA'
                lv.apply(bpy.context, {'record_view_layer_exclude_state': True,
                                      'local_view_state': {'enabled': False, 'objects': []}})
            elif index == 6:
                assert space.local_view is None
                assert lv.warning_camera(bpy.context) is None
                bpy.ops.camera.reload_current_saved_stock()
            elif index == 7:
                assert lv.warning_camera(bpy.context) == case.camera
                assert not errors
                case.scene.record_view_layer_exclude_state = False
                assert bpy.ops.camera.save_selected_stock_memo() == {'FINISHED'}
            elif index == 8:
                item = case.manager.saved_camera_data[-1]
                assert not base.u._saved_data_has_view_layer_state(item)
                assert lv.has_recorded_state(item)
                screenshot('_local_only')
                case.scene.record_view_layer_exclude_state = True
                case.scene.record_local_view_state = False
                assert bpy.ops.camera.save_selected_stock_memo() == {'FINISHED'}
            elif index == 9:
                item = case.manager.saved_camera_data[-1]
                assert base.u._saved_data_has_view_layer_state(item)
                assert not lv.has_recorded_state(item)
                assert len(case.manager.saved_camera_data) == 1
                screenshot('_view_layer_only')
            else:
                finish()
                return None
            steps.append(index)
            index += 1
            area.tag_redraw()
        return 0.7
    except Exception:
        errors.append(traceback.format_exc())
        finish()
        return None

bpy.app.timers.register(step, first_interval=2)
