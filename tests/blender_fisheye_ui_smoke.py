"""Real UI draw check; run only in a separate factory-startup GUI process."""
import json
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_regression as base

bpy = base.bpy
panel = base.u.VIEW3D_PT_custom_panel_fisheye
original_draw = panel.draw
drawn, errors = [], []


def draw(self, context):
    try:
        original_draw(self, context)
        drawn.append((context.scene.gyogan.enabled, context.scene.gyogan.center_mode))
    except Exception:
        errors.append(traceback.format_exc())


panel.draw = draw
panel.bl_category = 'Item'
panel.bl_options = set()
panel.bl_order = -100
base.Regression.setUpClass()
case = base.Regression()
case.setUp()
bpy.context.preferences.view.show_splash = False
engine = base.addon.gyogan_camera
obj = bpy.data.objects['Cube']
scene = bpy.context.scene
scene.gyogan.subdivision = 'SIMPLE'
engine.setup_mesh(scene, obj)


def open_panel():
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            area.spaces.active.show_region_ui = True
            area.spaces.active.region_3d.view_perspective = 'CAMERA'
            area.tag_redraw()


def switch():
    bpy.ops.gyogan.toggle_preview()
    assert not scene.gyogan.enabled
    scene.gyogan.center_mode = 'CURSOR'


def finish():
    version = base.x.ADDON_VERSION_STR.replace('.', '_')
    result = dict(drawn=drawn, errors=errors,
                  on_drawn=any(on for on, _ in drawn),
                  off_drawn=any(not on for on, _ in drawn),
                  cursor_drawn=any(mode == 'CURSOR' for _, mode in drawn))
    (base.ROOT / 'dist' / f'fisheye_ui_{version}.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    bpy.ops.screen.screenshot(filepath=str(base.ROOT / 'dist' / f'fisheye_ui_{version}.png'))
    base.Regression.tearDownClass()
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(open_panel, first_interval=1)
bpy.app.timers.register(switch, first_interval=3)
bpy.app.timers.register(finish, first_interval=5)
