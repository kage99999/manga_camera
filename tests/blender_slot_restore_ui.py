"""Disposable factory-startup GUI test; input is only sent to this Blender."""
import json
from pathlib import Path
import sys
import traceback

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_slot_regression as t

bpy, slots, addon = t.bpy, t.slots, t.addon
errors, drawn, operators = [], set(), []


def traced(original, name):
    def draw(self, context):
        try:
            original(self, context)
            drawn.add(name)
            if name == 'restore':
                operators[:] = [self]
        except Exception:
            errors.append(traceback.format_exc())
    return draw


for cls in t.u.UI_CLASSES:
    if issubclass(cls, bpy.types.Panel):
        cls.bl_category = 'Item'
        cls.bl_options = set(getattr(cls, 'bl_options', ())) - {'DEFAULT_CLOSED'}
        cls.draw = traced(cls.draw, cls.__name__)
slots.MPM_OT_restore_stock_slot.draw = traced(slots.MPM_OT_restore_stock_slot.draw, 'restore')
slots.MPM_OT_delete_stock_slot.draw = traced(slots.MPM_OT_delete_stock_slot.draw, 'delete')
t.SlotRegression.setUpClass()
case = t.SlotRegression()
case.setUp()
case.scene.show_settings = True
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.filepaths.temporary_directory = str(case.root)
window = bpy.context.window
area = next(area for area in window.screen.areas if area.type == 'VIEW_3D')
region = next(region for region in area.regions if region.type == 'WINDOW')
area.spaces.active.show_region_ui = True
for index in range(12):
    case.lib.create('話数' + str(index))
    case.manager.save_data(camera_data=[case.item('restore-ui')])
    case.lib.delete(case.lib.active_id)
case.lib.delete(case.lib.active_id)
slots.refresh_ui()
case.scene.frame_set(77)
state = tuple(case.camera.location), case.scene.frame_current
stem = t.ROOT / 'dist' / ('slot_restore_ui_' + t.base.x.ADDON_VERSION_STR.replace('.', '_'))


def guarded(callback):
    def run():
        try:
            callback()
        except Exception:
            errors.append(traceback.format_exc())
    return run


def open_dialog():
    assert not case.lib.entries
    with bpy.context.temp_override(window=window, area=area, region=region):
        assert bpy.ops.camera.restore_stock_slot('INVOKE_DEFAULT') == {'RUNNING_MODAL'}


def inspect_and_confirm():
    assert operators and 'restore' in drawn
    operator = operators[0]
    assert len(operator.backups) == 13
    operator.selected_index = next(i for i, item in enumerate(operator.backups) if item.name == '話数5')
    bpy.ops.screen.screenshot(filepath=str(stem) + '_dialog.png')
    window.event_simulate(type='RET', value='PRESS')
    window.event_simulate(type='RET', value='RELEASE')


def inspect_restored():
    assert case.lib.active['name'] == '話数5', case.lib.active
    assert case.manager.saved_camera_data[0]['memo'] == 'restore-ui'
    assert len(case.lib.deleted_slots()) == 13
    assert (tuple(case.camera.location), case.scene.frame_current) == state
    bpy.ops.screen.screenshot(filepath=str(stem) + '_restored.png')


def open_delete():
    with bpy.context.temp_override(window=window, area=area, region=region):
        assert bpy.ops.camera.delete_stock_slot('INVOKE_DEFAULT') == {'RUNNING_MODAL'}


def cancel_delete():
    assert 'delete' in drawn
    bpy.ops.screen.screenshot(filepath=str(stem) + '_delete.png')
    window.event_simulate(type='ESC', value='PRESS')
    window.event_simulate(type='ESC', value='RELEASE')


def confirm_delete():
    assert case.lib.active['name'] == '話数5'
    assert len(case.lib.deleted_slots()) == 13
    open_delete()


def accept_delete():
    window.event_simulate(type='RET', value='PRESS')
    window.event_simulate(type='RET', value='RELEASE')


def inspect_deleted():
    assert not case.lib.entries
    assert len(case.lib.deleted_slots()) == 14
    assert (tuple(case.camera.location), case.scene.frame_current) == state


def finish():
    stem.with_suffix('.json').write_text(json.dumps({'errors': errors, 'drawn': sorted(drawn),
        'active': case.lib.active, 'version': t.base.x.ADDON_VERSION_STR}, ensure_ascii=False, indent=2), encoding='utf-8')
    print('SLOT RESTORE UI:', 'FAIL' if errors else 'OK')
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(guarded(open_dialog), first_interval=1.5)
bpy.app.timers.register(guarded(inspect_and_confirm), first_interval=3.5)
bpy.app.timers.register(guarded(inspect_restored), first_interval=6)
bpy.app.timers.register(guarded(open_delete), first_interval=7)
bpy.app.timers.register(guarded(cancel_delete), first_interval=9)
bpy.app.timers.register(guarded(confirm_delete), first_interval=11)
bpy.app.timers.register(guarded(accept_delete), first_interval=13)
bpy.app.timers.register(guarded(inspect_deleted), first_interval=15)
bpy.app.timers.register(finish, first_interval=16)
