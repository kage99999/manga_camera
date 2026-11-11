"""Disposable GUI probe: can a modal handler observe clicks on an open enum?"""
import json
import os
from pathlib import Path

import bpy

out = Path(os.environ['MPM_ENUM_PROBE_DIR']).resolve()
out.mkdir(parents=True, exist_ok=True)
events = []


class PROBE_OT_monitor(bpy.types.Operator):
    bl_idname = 'wm.mpm_enum_probe_monitor'
    bl_label = 'Monitor'

    def invoke(self, context, event):
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'LEFTMOUSE':
            events.append((event.type, event.value, event.mouse_x, event.mouse_y))
        return {'PASS_THROUGH'}


class PROBE_PT_enum(bpy.types.Panel):
    bl_label = 'Enum Probe'
    bl_idname = 'VIEW3D_PT_mpm_enum_probe'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Item'
    bl_order = -1000

    def draw(self, context):
        self.layout.prop(context.scene, 'mpm_enum_probe', text='')


for cls in (PROBE_OT_monitor, PROBE_PT_enum):
    bpy.utils.register_class(cls)
bpy.types.Scene.mpm_enum_probe = bpy.props.EnumProperty(items=[(str(i), f'{i}: No File', '') for i in range(1, 8)])
bpy.context.preferences.view.show_splash = False
window = bpy.context.window
area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
area.spaces.active.show_region_ui = True
region = next(r for r in area.regions if r.type == 'WINDOW')


def start():
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.wm.mpm_enum_probe_monitor('INVOKE_DEFAULT')
    bpy.ops.screen.screenshot(filepath=str(out / 'initial.png'))


def click():
    x, y = map(int, os.environ['MPM_ENUM_PROBE_CLICK'].split(','))
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    window.event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)
    window.event_simulate(type='LEFTMOUSE', value='RELEASE', x=x, y=y)


def finish():
    bpy.ops.screen.screenshot(filepath=str(out / 'after.png'))
    (out / 'result.json').write_text(json.dumps({'events': events, 'value': bpy.context.scene.mpm_enum_probe}), encoding='utf-8')
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(start, first_interval=2.0)
if os.environ.get('MPM_ENUM_PROBE_CLICK'):
    for delay in (3.0, 4.0, 4.15):
        bpy.app.timers.register(lambda: (click(), None)[1], first_interval=delay)
bpy.app.timers.register(finish, first_interval=6.0)
