"""Check native file-browser folders in a disposable factory-startup GUI process."""
import json
import os
from pathlib import Path
import sys
import traceback
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
WORK = Path(os.environ['MPM_SLOT_DIALOG_ROOT']).resolve()
assert WORK.is_relative_to((ROOT / 'dist').resolve())
WORK.mkdir(parents=True, exist_ok=True)
resource = patch.object(bpy.utils, 'user_resource', return_value=str(WORK / 'config'))
resource.start()
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_regression as base

addon = base.addon
addon.register()
manager = addon.core.get_camera_data_manager()
lib = manager.slot_library
assert not lib.error, lib.error
scene = bpy.context.scene
window = bpy.context.window
area = next(area for area in window.screen.areas if area.type == 'VIEW_3D')
region = next(region for region in area.regions if region.type == 'WINDOW')
images, ids = [], []
for name in ('50話', '51話'):
    folder = WORK / name
    folder.mkdir(exist_ok=True)
    path = folder / 'frame000123.png'
    base.Regression.make_png(None, path)
    lib.create(name)
    ids.append(lib.active_id)
    images.append(path)
    assert bpy.ops.camera.load_background_image(filepath=str(path), directory=str(folder)) == {'FINISHED'}

bpy.context.preferences.view.show_splash = False
bpy.context.preferences.filepaths.temporary_directory = str(WORK)
scene.frame_set(17)
state = (tuple(scene.camera.location), tuple(scene.camera.rotation_euler),
         scene.frame_current, scene.camera.data.background_images[0].image)
results, errors = [], []


def guarded(callback):
    def run():
        try:
            callback()
        except Exception:
            errors.append(traceback.format_exc())
    return run


def open_dialog(index):
    bpy.context.window_manager.mpm_stock_slot = ids[index]
    with bpy.context.temp_override(window=window, area=area, region=region):
        assert bpy.ops.camera.load_background_image(
            'INVOKE_DEFAULT', filepath=str(images[1 - index])) == {'RUNNING_MODAL'}


def inspect_dialog(index):
    targets = [(win, item) for win in bpy.context.window_manager.windows
               for item in win.screen.areas if item.type == 'FILE_BROWSER']
    assert len(targets) == 1, targets
    win, browser = targets[0]
    params = browser.spaces.active.params
    directory = os.fsdecode(params.directory)
    results.append({'slot': lib.active['name'], 'directory': directory, 'filename': params.filename})
    try:
        assert Path(directory) == images[index].parent, results[-1]
        assert params.filename == '', results[-1]
        assert (tuple(scene.camera.location), tuple(scene.camera.rotation_euler),
                scene.frame_current, scene.camera.data.background_images[0].image) == state
    finally:
        with bpy.context.temp_override(window=win, area=browser):
            bpy.ops.screen.screenshot(filepath=str(WORK / f'dialog-{index}.png'))
            bpy.ops.file.cancel()


def finish():
    if len(results) != 2:
        errors.append(f'Expected two dialogs, got {len(results)}')
    (WORK / 'result.json').write_text(json.dumps({'results': results, 'errors': errors}, indent=2), encoding='utf-8')
    print('SLOT FOLDER UI:', 'FAIL' if errors else 'OK')
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(guarded(lambda: open_dialog(0)), first_interval=1)
bpy.app.timers.register(guarded(lambda: inspect_dialog(0)), first_interval=3)
bpy.app.timers.register(guarded(lambda: open_dialog(1)), first_interval=5)
bpy.app.timers.register(guarded(lambda: inspect_dialog(1)), first_interval=7)
bpy.app.timers.register(finish, first_interval=9)
