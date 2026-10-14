"""Real UI and pose-mode recording in a disposable factory-startup process."""
import json
from pathlib import Path
import sys
import traceback

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_pose_regression as t

v, bpy = t.base, t.bpy
bpy.context.preferences.view.show_splash = False
drawn, errors, checks = set(), [], []
panels = [cls for cls in v.u.UI_CLASSES if issubclass(cls, bpy.types.Panel)]


def traced_draw(original, name):
    def draw(self, context):
        drawn.add(name)
        try:
            original(self, context)
        except Exception:
            errors.append(traceback.format_exc())
    return draw


for cls in panels:
    cls.bl_category = 'Item'
    cls.bl_options = set(getattr(cls, 'bl_options', ())) - {'DEFAULT_CLOSED'}
    cls.draw = traced_draw(cls.draw, cls.__name__)

t.PoseRegression.setUpClass()
case = t.PoseRegression()
case.setUp()
case.rig.pose.bones['Euler'].location.x = 1
for area in bpy.context.screen.areas:
    if area.type == 'VIEW_3D':
        area.spaces.active.show_region_ui = True
        area.tag_redraw()

step_index = 0
protected_keys = None
stem = 'pose_ui_' + v.x.ADDON_VERSION_STR.replace('.', '_')


def finish():
    result = {'checks': checks, 'errors': errors, 'drawn': sorted(drawn),
              'missing': sorted({cls.__name__ for cls in panels} - drawn)}
    (v.ROOT / 'dist' / (stem + '.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
    bpy.ops.screen.screenshot(filepath=str(v.ROOT / 'dist' / (stem + '.png')))
    case.tearDown()
    t.PoseRegression.tearDownClass()
    bpy.ops.wm.quit_blender()


def timeline_bounds():
    area = next(area for area in bpy.context.screen.areas if area.type == 'DOPESHEET_EDITOR')
    region = next(region for region in area.regions if region.type == 'WINDOW')
    return (region.view2d.region_to_view(0, 0)[0],
            region.view2d.region_to_view(region.width, 0)[0])


def step():
    global step_index, protected_keys
    try:
        bone = case.rig.pose.bones['Euler']
        if protected_keys is not None:
            assert case.points() == protected_keys
        else:
            assert case.rig.animation_data is None
        if step_index == 0:
            assert not case.manager.saved_camera_data
            bpy.ops.screen.screenshot(filepath=str(v.ROOT / 'dist' / (stem + '_empty.png')))
            assert bpy.ops.camera.save_selected_stock_memo() == {'FINISHED'}
            assert case.scene.frame_current == 100
            bpy.ops.object.mode_set(mode='POSE')
            bone.location.x = 7
        elif step_index == 1:
            assert bpy.ops.camera.save_position() == {'FINISHED'}
            assert case.scene.frame_current == 100 and bone.location.x == 7
            assert bpy.context.mode == 'POSE'
        elif step_index == 2:
            bpy.ops.camera.prev_saved_stock()
            assert case.scene.frame_current == 100 and bone.location.x == 1
        elif step_index == 3:
            bpy.ops.camera.next_saved_stock()
            assert case.scene.frame_current == 100 and bone.location.x == 7
        elif step_index == 4:
            bone.location.x = 9
            assert bpy.ops.camera.save_selected_stock_memo() == {'FINISHED'}
            assert case.scene.frame_current == 100 and bone.location.x == 9
            assert len(case.manager.saved_camera_data) == 2
            assert bpy.context.mode == 'POSE'
        elif step_index == 5:
            bpy.ops.camera.prev_saved_stock()
            assert case.scene.frame_current == 100 and bone.location.x == 1
        elif step_index == 6:
            bpy.ops.camera.next_saved_stock()
            assert case.scene.frame_current == 100 and bone.location.x == 9
        elif step_index == 7:
            assert bpy.ops.camera.save_selected_stock_memo() == {'CANCELLED'}
            assert len(case.manager.saved_camera_data) == 2
            assert bpy.context.mode == 'POSE'
        elif step_index == 8:
            bpy.ops.screen.screenshot(filepath=str(v.ROOT / 'dist' / (stem + '_no_keys.png')))
            case.scene.frame_set(200)
            bone.location.x = 13
            bone.keyframe_insert('location', frame=200)
            protected_keys = case.points()
            bone.location.x = 15
            assert bpy.ops.camera.save_position() == {'FINISHED'}
            assert case.scene.frame_current == 200 and bone.location.x == 15
        elif step_index == 9:
            case.scene.frame_set(777, subframe=0.25)
            bpy.ops.camera.prev_saved_stock()
            assert case.scene.frame_current == 100 and bone.location.x == 9
        elif step_index == 10:
            bpy.ops.camera.next_saved_stock()
            assert case.scene.frame_current == 200 and bone.location.x == 15
        elif step_index == 11:
            bone.location.x = 20
            bpy.ops.camera.reload_current_saved_stock()
            assert case.scene.frame_current == 200 and bone.location.x == 15
        elif step_index == 12:
            bpy.ops.camera.recall_position()
            assert case.scene.frame_current == 200 and bone.location.x == 15
            assert len(case.manager.saved_camera_data) == 3
            layout = v.Layout()
            v.u._draw_record_read_controls(layout, bpy.context)
            assert '読込時はストックのポーズを優先' not in layout.labels
            assert 'キーフレームに記録があるため' not in layout.labels
            assert not hasattr(case.scene, 'mpm_record_keyframes')
            assert not hasattr(case.scene, 'mpm_move_to_recorded_frame')
        elif step_index == 13:
            assert bone.location.x == 15
            bpy.ops.screen.screenshot(filepath=str(v.ROOT / 'dist' / (stem + '_stock_priority.png')))
            case.scene.mpm_pose_keyframe_priority = True
            assert bone.location.x == 15
        elif step_index == 14:
            bpy.ops.camera.reload_current_saved_stock()
            assert case.scene.frame_current == 200 and bone.location.x == 13
        elif step_index == 15:
            assert bone.location.x == 13
            layout = v.Layout()
            v.u._draw_record_read_controls(layout, bpy.context)
            assert 'キーフレームに記録があるため' not in layout.labels
            assert '読込時はストックのポーズを優先' not in layout.labels
            bpy.ops.screen.screenshot(filepath=str(v.ROOT / 'dist' / (stem + '_key_priority.png')))
        elif step_index == 16:
            case.scene.mpm_pose_keyframe_priority = False
            assert bone.location.x == 13
            bpy.ops.camera.reload_current_saved_stock()
            assert case.scene.frame_current == 200 and bone.location.x == 15
        elif step_index == 17:
            assert bone.location.x == 15
            evaluated = case.rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
            assert abs(evaluated.pose.bones['Euler'].matrix.translation.x - 15) < 0.001
        else:
            finish()
            return None
        checks.append({'step': step_index, 'frame': case.scene.frame_current, 'mode': bpy.context.mode,
                       'key_priority': case.scene.mpm_pose_keyframe_priority, 'pose_x': bone.location.x})
        step_index += 1
        return 0.5
    except Exception:
        errors.append(traceback.format_exc())
        finish()
        return None


bpy.app.timers.register(step, first_interval=1.5)
