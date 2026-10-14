"""Disposable factory-startup GUI check of the real Blender panel draw callbacks."""
from pathlib import Path
import sys
import json
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_regression as v
v.bpy.context.preferences.view.show_splash = False

drawn = set()
errors = []
panels = [cls for cls in v.u.UI_CLASSES if issubclass(cls, v.bpy.types.Panel)]
panels.append(v.lm.VIEW3D_PT_manga_camera_lattice_manager)
def traced_draw(original, name):
    def draw(self, context):
        drawn.add(name)
        try:
            original(self, context)
        except Exception:
            errors.append(traceback.format_exc())
    return draw


for cls in panels:
    # Exercise the original draw code in the factory startup's active sidebar tab.
    cls.bl_category = 'Item'
    cls.bl_options = set(getattr(cls, 'bl_options', ())) - {'DEFAULT_CLOSED'}
    if cls.__name__ == 'VIEW3D_PT_custom_panel_xmp_rendering':
        # Keep the changed section visible in the screenshot, test process only.
        cls.bl_order = -10
    cls.draw = traced_draw(cls.draw, cls.__name__)

v.Regression.setUpClass()
case = v.Regression()
case.setUp()
case.scene.record_selected_objects = True
case.scene.show_settings = True
case.scene.show_all_object_data_section = True
obj = v.bpy.data.objects['Cube']
record = case.item('UI smoke')
record['selected_objects'] = [dict(name=obj.name, location=[0, 0, 0], rotation=[0, 0, 0], scale=[1, 1, 1])]
record['record_selected_objects'] = True
case.manager.save_data(camera_data=[record])
v.c.rebuild_enum_cache(case.manager)
v.a.get_all_object_data_manager().save_data(stocks=[v.a._build_current_stock_item(case.scene)])
case.lattice_set([obj])
case.scene.mpm_lattice_management_enabled = False


def open_panel():
    for area in v.bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            area.spaces.active.show_region_ui = True
            area.tag_redraw()
    return None


def finish():
    result = {'drawn': sorted(drawn), 'errors': errors,
              'missing': sorted({cls.__name__ for cls in panels} - drawn)}
    version = v.x.ADDON_VERSION_STR.replace('.', '_')
    output = v.ROOT / 'dist' / f'ui_smoke_{version}.json'
    output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    v.bpy.ops.screen.screenshot(filepath=str(v.ROOT / 'dist' / f'ui_smoke_{version}.png'))
    v.Regression.tearDownClass()
    v.faulthandler.cancel_dump_traceback_later()
    v.bpy.ops.wm.quit_blender()
    return None


v.bpy.app.timers.register(open_panel, first_interval=1)
v.bpy.app.timers.register(finish, first_interval=5)
