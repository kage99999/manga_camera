"""Native Undo/Redo across actual event-loop timer updates.

Run in a separate GUI Blender with --factory-startup, never through a live MCP.
MPM_UNDO_KIND=MESH/ARMATURE, MPM_UNDO_KEEP=1/0, MPM_UNDO_INTERVAL=.15.
All objects, history, settings and output files belong to this test process.
"""
import faulthandler
import json
import os
from pathlib import Path
import sys
import traceback

import bpy

if bpy.app.background or '--factory-startup' not in sys.argv:
    raise RuntimeError('This test requires an isolated factory-startup GUI')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_regression as base

faulthandler.enable()
base.Regression.setUpClass()
engine = base.addon.gyogan_camera
kind = os.environ.get('MPM_UNDO_KIND', 'MESH')
keep = os.environ.get('MPM_UNDO_KEEP', '1') == '1'
interval = float(os.environ.get('MPM_UNDO_INTERVAL', '.15'))
case = base.Regression()
case.setUp()
scene = bpy.context.scene
scene.camera.location = (0, 0, 10)
scene.camera.rotation_euler = (0, 0, 0)
scene.camera.data.lens = 50
scene.gyogan.subdivision = 'NONE'
scene.gyogan.center_mode = 'DEFAULT'
scene.gyogan.enabled = False
scene.gyogan.mode = kind
scene.gyogan.effect_scope = 'CAMERA'
scene.gyogan.keep_bounds_helpers = keep
for obj in list(scene.objects):
    if obj != scene.camera:
        bpy.data.objects.remove(obj, do_unlink=True)
for index in range(3):
    obj = case.new_mesh('UndoMesh%03d' % index)
    obj.location.x = (index - 1) * .3
    material = bpy.data.materials.new('UndoMaterial%03d' % index)
    obj.data.materials.append(material)
    obj.material_slots[0].link = 'OBJECT'
    obj.material_slots[0].material = material
    bevel = obj.modifiers.new('SourceBevel', 'BEVEL')
    bevel.width = .01
    if kind == 'ARMATURE':
        rig = bpy.data.objects.new('UndoRig%03d' % index, bpy.data.armatures.new('TestRig'))
        scene.collection.objects.link(rig)
        bpy.context.view_layer.objects.active = rig
        rig.select_set(True)
        bpy.ops.object.mode_set(mode='EDIT')
        bone = rig.data.edit_bones.new('Bone')
        bone.head, bone.tail = (0, 0, 0), (0, 1, 0)
        bpy.ops.object.mode_set(mode='OBJECT')
        rig.select_set(False)
        obj.vertex_groups.new(name='Bone').add([0, 1, 2], 1, 'REPLACE')
        obj.modifiers.new('Armature', 'ARMATURE').object = rig
    else:
        engine.setup_mesh(scene, obj)
bpy.context.view_layer.objects.active = scene.camera
scene.camera.select_set(True)
bpy.context.preferences.edit.undo_steps = 128
bpy.context.preferences.view.show_splash = False
recovery = base.ROOT / 'dist' / ('undo-test-recovery-' + str(os.getpid()))
recovery.mkdir(exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(recovery)
del scene, obj, material, bevel, case
if kind == 'ARMATURE':
    del rig, bone

history = [(False, 50.0)]
for index in range(1, 13):
    on, lens = history[-1]
    history.append((not on, lens) if index % 2 else (on, 50.0 + index))
actions = [('seed', 0)]
actions.extend(('toggle' if i % 2 else 'lens', i) for i in range(1, 13))
actions.extend(('undo', i) for i in reversed(range(12)))
actions.extend(('redo', i) for i in range(1, 13))
actions.extend(('undo', 11) if i % 2 == 0 else ('redo', 12) for i in range(60))
step_index = 0
checks = []
pending_check = None
result_path = base.ROOT / 'dist' / (
    'fisheye_undo_' + bpy.app.version_string + '_' + kind + '_' + str(int(keep)) + '.json')


def helper_ids():
    return sorted((obj.name, obj.session_uid) for obj in bpy.data.objects
                  if obj.get('gyogan_role') in {'camera_bounds', 'camera_bounds_evaluator'})


def verify(index):
    scene = bpy.context.scene
    expected_on, expected_lens = history[index]
    assert scene.gyogan.enabled == expected_on, (index, scene.gyogan.enabled, expected_on)
    assert abs(scene.camera.data.lens - expected_lens) < 1.e-5
    assert bpy.context.mode == 'OBJECT', bpy.context.mode
    assert bpy.context.view_layer.objects.active == scene.camera
    if expected_on:
        assert len(scene.gyogan.targets) == 3
        assert len(helper_ids()) == (6 if kind == 'MESH' else 3)
    elif not keep or index == 0:
        assert not helper_ids()
    for target in scene.gyogan.targets:
        if target.kind == 'MESH':
            for record in target.modifiers:
                mod = engine.find_modifier(target.source, record)
                assert mod.show_viewport == (expected_on and target.active and record.enabled)
        else:
            for output in target.outputs:
                assert output.source.hide_get() == (expected_on and target.active)
                assert output.output.hide_get() == (not expected_on or not target.active)
    return dict(index=index, enabled=expected_on, lens=expected_lens,
                targets=len(scene.gyogan.targets), helpers=helper_ids())


def finish(error=None):
    result = dict(blender=bpy.app.version_string, kind=kind, keep=keep,
                  steps=len(checks), passed=error is None, error=error, checks=checks)
    result_path.write_text(json.dumps(result, indent=2), encoding='utf8')
    print('FISHEYE_UNDO_RESULT=' + json.dumps(result), flush=True)
    base.Regression.tearDownClass()
    # Windows native worker shutdown may raise handled first-chance exceptions.
    # Keep fatal Python diagnostics on throughout the actual test and cleanup.
    faulthandler.disable()
    bpy.ops.wm.quit_blender()


def step():
    global step_index, pending_check
    try:
        if pending_check is not None:
            expected_index, expected_helpers = pending_check
            verify(expected_index)
            assert helper_ids() == expected_helpers, 'Timer rebuilt an Undo-restored helper'
        if step_index == len(actions):
            finish()
            return None
        action, index = actions[step_index]
        print('FISHEYE_UNDO_STEP', step_index, action, index, flush=True)
        if action == 'seed':
            bpy.ops.ed.undo_push(message='Initial OFF')
        elif action == 'toggle':
            bpy.ops.gyogan.toggle_preview('EXEC_DEFAULT', True)
        elif action == 'lens':
            bpy.ops.wm.context_set_float('EXEC_DEFAULT', True,
                                        data_path='scene.camera.data.lens', value=history[index][1])
        elif action == 'undo':
            bpy.ops.ed.undo()
        else:
            bpy.ops.ed.redo()
        checks.append(verify(index))
        pending_check = (index, helper_ids())
        step_index += 1
        return interval
    except Exception:
        error = traceback.format_exc()
        print(error, flush=True)
        finish(error)
        return None


bpy.app.timers.register(step, first_interval=1)
