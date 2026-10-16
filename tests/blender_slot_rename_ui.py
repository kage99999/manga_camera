"""Isolated GUI test; requires --enable-event-simulate. Never sends OS input."""
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
events, errors = [], []
original_set_name = addon.slots._set_slot_name


def set_name(self, value):
    original_set_name(self, value)
    events.append({'name': value})


addon.slots._set_slot_name = set_name
addon.register()
lib = addon.core.get_camera_data_manager().slot_library
assert not lib.error, lib.error
if not any(e['name'] == '50話' for e in lib.entries):
    lib.create('50話')
else:
    lib.select(next(e['id'] for e in lib.entries if e['name'] == '50話'))
scene = bpy.context.scene
scene_state = (scene.frame_current, tuple(scene.camera.location), tuple(scene.camera.rotation_euler))


class MPM_PT_slot_click_test(bpy.types.Panel):
    bl_label = 'Slot Double Click Test'
    bl_idname = 'MPM_PT_slot_click_test'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Item'
    bl_order = -1000

    def draw(self, context):
        try:
            addon.slots.draw_selector(self.layout, context)
        except Exception:
            errors.append(traceback.format_exc())


bpy.utils.register_class(MPM_PT_slot_click_test)
bpy.context.preferences.view.show_splash = False
window = bpy.context.window
area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
area.spaces.active.show_region_ui = True
area.tag_redraw()


def finish():
    try:
        name = os.environ.get('MPM_SLOT_UI_NAME', 'Episode52')
        assert (scene.frame_current, tuple(scene.camera.location), tuple(scene.camera.rotation_euler)) == scene_state
        if os.environ.get('MPM_SLOT_UI_MODE') in {'rename', 'blur'}:
            assert events == [{'name': name}], events
            assert lib.active['name'] == name, lib.active
            assert Path(addon.core.get_camera_data_manager().save_file_path).name == name + '.json'
        elif os.environ.get('MPM_SLOT_UI_MODE') == 'cancel':
            assert not events, events
            assert lib.active['name'] == '50話', lib.active
        elif os.environ.get('MPM_SLOT_UI_MODE') in {'dropdown', 'editing'}:
            assert events == [], events
            assert lib.active['name'] == '50話', lib.active
        elif os.environ.get('MPM_SLOT_UI_MODE') == 'switch':
            assert not events, events
            assert lib.active_id == lib.entries[0]['id'], lib.active
        bpy.ops.screen.screenshot(filepath=str(work / 'rename-ui.png'))
    except Exception:
        errors.append(traceback.format_exc())
    finally:
        result = {'events': events, 'errors': errors, 'active': lib.active['name'], 'regions': [
            {'type': r.type, 'x': r.x, 'y': r.y, 'width': r.width, 'height': r.height}
            for r in area.regions]}
        (work / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        bpy.ops.wm.quit_blender()


if os.environ.get('MPM_SLOT_UI_CLICK'):
    x, y = map(int, os.environ['MPM_SLOT_UI_CLICK'].split(','))

    def click():
        window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
        window.event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)
        window.event_simulate(type='LEFTMOUSE', value='RELEASE', x=x, y=y)

    bpy.app.timers.register(click, first_interval=3.0)
    if os.environ.get('MPM_SLOT_UI_MODE') not in {'dropdown', 'switch'}:
        bpy.app.timers.register(lambda: (click(), None)[1], first_interval=3.15)

    def key(kind, **kwargs):
        window.event_simulate(type=kind, value='PRESS', **kwargs)
        window.event_simulate(type=kind, value='RELEASE')

    def enter_name():
        key('A', ctrl=True)
        for char in os.environ.get('MPM_SLOT_UI_NAME', 'Episode52'):
            key('E', unicode=char)

    mode = os.environ.get('MPM_SLOT_UI_MODE')
    if mode in {'rename', 'cancel', 'editing', 'blur'}:
        bpy.app.timers.register(enter_name, first_interval=4.0)
    if mode == 'rename':
        bpy.app.timers.register(lambda: (key('RET'), None)[1], first_interval=4.5)
    elif mode == 'cancel':
        bpy.app.timers.register(lambda: (key('ESC'), None)[1], first_interval=4.5)
    elif mode == 'blur':
        def outside():
            window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x - 200, y=y)
            window.event_simulate(type='LEFTMOUSE', value='PRESS', x=x - 200, y=y)
            window.event_simulate(type='LEFTMOUSE', value='RELEASE', x=x - 200, y=y)
        bpy.app.timers.register(outside, first_interval=4.5)
    elif mode == 'switch':
        pick_x, pick_y = map(int, os.environ['MPM_SLOT_UI_PICK'].split(','))

        def choose():
            window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=pick_x, y=pick_y)
            window.event_simulate(type='LEFTMOUSE', value='PRESS', x=pick_x, y=pick_y)
            window.event_simulate(type='LEFTMOUSE', value='RELEASE', x=pick_x, y=pick_y)
        bpy.app.timers.register(choose, first_interval=4.0)
bpy.app.timers.register(finish, first_interval=6.0)
