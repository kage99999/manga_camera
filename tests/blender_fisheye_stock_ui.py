"""Actual panel drawing and stock recall/Undo in a disposable GUI process."""
import json
import os
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_fisheye_stock import FisheyeStock, base, bpy, engine

assert not bpy.app.background and '--factory-startup' in sys.argv
WORK = Path(os.environ['MPM_FISHEYE_UI_ROOT']).resolve()
assert WORK.is_relative_to((base.ROOT / 'dist').resolve())
WORK.mkdir(parents=True, exist_ok=True)
panel = base.u.VIEW3D_PT_custom_panel_record_read
original_draw = panel.draw
drawn, errors, checks = [], [], []


def draw(self, context):
    try:
        original_draw(self, context)
        data = base.s._get_saved_item_safe(base.c.get_camera_data_manager(), int(context.scene.saved_camera_index))
        drawn.append((bool(data.get('fisheye_state', {}).get('enabled')),
                      bool(data.get('lattice_enabled'))))
    except Exception:
        errors.append(traceback.format_exc())


panel.draw = draw
panel.bl_options = set()
panel.bl_order = -100
FisheyeStock.setUpClass()
case = FisheyeStock()
case.setUp()
scene = bpy.context.scene
obj = bpy.data.objects['Cube']
engine.setup_mesh(scene, obj)
scene.gyogan.strength = 2
scene.gyogan.keep_bounds_helpers = False
fish_item = case.item()
scene.gyogan.enabled = False
scene.mpm_lattice_management_enabled = True
case.lattice_set([obj])
lattice_item = case.item()
scene.mpm_lattice_management_enabled = False
off_item = case.item()
case.manager.save_data(camera_data=[fish_item, lattice_item, off_item])
base.c.rebuild_enum_cache(case.manager)
scene.saved_camera_index = '0'
engine.targets.reconcile(scene)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.filepaths.temporary_directory = str(WORK)
bpy.context.preferences.edit.undo_steps = 64
for area in bpy.context.screen.areas:
    if area.type == 'VIEW_3D':
        area.spaces.active.show_region_ui = True
del scene, obj, case


def verify(index):
    scene = bpy.context.scene
    assert scene.gyogan.enabled == (index == 0)
    assert scene.mpm_lattice_management_enabled == (index == 1)
    for target in scene.gyogan.targets:
        for record in target.modifiers:
            modifier = engine.find_modifier(target.source, record)
            assert modifier.show_viewport == (index == 0 and target.active and record.enabled)
    for modifier in base.lm._iter_all_lattice_manager_modifiers(scene):
        assert modifier.show_viewport == (index == 1)
    checks.append(index)


def guarded(callback):
    def run():
        try:
            callback()
        except Exception:
            errors.append(traceback.format_exc())
    return run


def recall(index):
    bpy.context.scene.saved_camera_index = str(index)
    assert bpy.ops.camera.reload_current_saved_stock() == {'FINISHED'}
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            area.spaces.active.show_region_ui = True
            for region in area.regions:
                if region.type == 'UI':
                    region.active_panel_category = 'カメラ'
            area.spaces.active.region_3d.view_perspective = 'CAMERA'
            area.tag_redraw()


def screenshot(index):
    verify(index)
    bpy.ops.screen.screenshot(filepath=str(WORK / f'stock-{index}.png'))
    bpy.ops.ed.undo_push(message='Stock ' + str(index))


def history():
    for operation, index in ((bpy.ops.ed.undo, 1), (bpy.ops.ed.undo, 0),
                             (bpy.ops.ed.redo, 1), (bpy.ops.ed.redo, 2)):
        operation()
        verify(index)


def finish():
    if set(drawn) != {(True, False), (False, True), (False, False)}:
        errors.append('Not all badge states were drawn: ' + str(set(drawn)))
    if len(checks) != 7:
        errors.append('Missing state checks: ' + str(checks))
    (WORK / 'result.json').write_text(json.dumps(dict(drawn=sorted(set(drawn)), checks=checks, errors=errors),
                                               ensure_ascii=False, indent=2), encoding='utf-8')
    FisheyeStock.tearDownClass()
    bpy.ops.wm.quit_blender()


for index in range(3):
    bpy.app.timers.register(guarded(lambda i=index: recall(i)), first_interval=1 + index * 2)
    bpy.app.timers.register(guarded(lambda i=index: screenshot(i)), first_interval=2 + index * 2)
bpy.app.timers.register(guarded(history), first_interval=7)
bpy.app.timers.register(finish, first_interval=9)
