"""Verify stock navigation and timeline visibility in a disposable Blender GUI."""
import json
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_regression as v

v.bpy.context.preferences.view.show_splash = False
v.Regression.setUpClass()
case = v.Regression()
case.setUp()
scene = case.scene
bg = v.b._get_or_create_background_slot(case.camera)
first = dict(case.item(), frame_current=511571, bg_visible=True, bg_opacity=1, bg_depth='FRONT')
second = dict(first, frame_current=123456)
case.manager.save_data(camera_data=[first, second])
v.c.rebuild_enum_cache(case.manager)
case.camera.data.mpm_bg_visible = False
bg.alpha = 0.23
bg.display_depth = 'BACK'
case.camera.data.passepartout_alpha = 0.31
expected_display = v.b._capture_background_display_state(case.camera)
scene.frame_set(1)
ranges = (scene.frame_start, scene.frame_end, scene.frame_preview_start, scene.frame_preview_end)
checks = []
errors = []
index = -1
initial_width = None
version = v.x.ADDON_VERSION_STR.replace('.', '_')
stem = f'stock_ui_{version}_blender_{v.bpy.app.version[0]}_{v.bpy.app.version[1]}'


def timeline_region():
    for area in v.bpy.context.screen.areas:
        if area.type == 'DOPESHEET_EDITOR' and area.spaces.active.mode == 'TIMELINE':
            return next(region for region in area.regions if region.type == 'WINDOW')
    raise AssertionError('No timeline in factory startup')


def bounds():
    region = timeline_region()
    return (region.view2d.region_to_view(0, 0)[0],
            region.view2d.region_to_view(region.width, 0)[0])


def reload_stock():
    scene.frame_set(17)
    return v.bpy.ops.camera.reload_current_saved_stock()


steps = [
    ('dropdown', lambda: setattr(scene, 'saved_camera_index', '0'), 511571),
    ('next', lambda: v.bpy.ops.camera.next_saved_stock(), 123456),
    ('reload', reload_stock, 123456),
    ('previous', lambda: v.bpy.ops.camera.prev_saved_stock(), 511571),
    ('recall', lambda: v.bpy.ops.camera.recall_position(), 123456),
]


def finish():
    result = {'checks': checks, 'errors': errors}
    (v.ROOT / 'dist' / (stem + '.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
    v.bpy.ops.screen.screenshot(filepath=str(v.ROOT / 'dist' / (stem + '.png')))
    v.Regression.tearDownClass()
    v.bpy.ops.wm.quit_blender()


def step():
    global index, initial_width
    try:
        low, high = bounds()
        if index < 0:
            initial_width = high - low
            assert not low <= 511571 <= high, (low, high)
        else:
            name, _, frame = steps[index]
            assert scene.frame_current == frame, (name, scene.frame_current)
            assert low < frame < high, (name, frame, low, high)
            assert abs((high - low) - initial_width) < 0.2, (name, initial_width, high - low)
            assert v.b._capture_background_display_state(case.camera) == expected_display, name
            assert ranges == (scene.frame_start, scene.frame_end,
                              scene.frame_preview_start, scene.frame_preview_end), name
            checks.append({'operation': name, 'frame': frame, 'visible_range': [low, high]})
        index += 1
        if index >= len(steps):
            finish()
            return None
        steps[index][1]()
        return 0.4
    except Exception:
        errors.append(traceback.format_exc())
        finish()
        return None


v.bpy.app.timers.register(step, first_interval=1.5)
