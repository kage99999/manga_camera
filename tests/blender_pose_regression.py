"""Pose recording checks in a disposable --factory-startup Blender process."""
import copy
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

import bpy

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_regression as base

c, s = base.c, base.s
p = importlib.import_module(base.ROOT.name + '.pose_stock')


class PoseRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base.Regression.setUpClass()

    @classmethod
    def tearDownClass(cls):
        base.Regression.tearDownClass()

    def setUp(self):
        self.helper = base.Regression()
        self.helper.setUp()
        self.scene, self.camera, self.manager = self.helper.scene, self.helper.camera, self.helper.manager
        self.old_objects = {obj.as_pointer() for obj in bpy.data.objects}
        self.old_actions = {action.as_pointer() for action in bpy.data.actions}
        self.rig = self.make_rig('PoseTestRig')
        self.scene.frame_set(100)
        self.scene.record_selected_objects = True
        self.scene.tool_settings.use_keyframe_insert_auto = False

    def tearDown(self):
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj in list(bpy.data.objects):
            if obj.as_pointer() not in self.old_objects:
                bpy.data.objects.remove(obj, do_unlink=True)
        for action in list(bpy.data.actions):
            if action.as_pointer() not in self.old_actions:
                bpy.data.actions.remove(action)

    def select(self, obj):
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

    def make_rig(self, name):
        data = bpy.data.armatures.new(name)
        obj = bpy.data.objects.new(name, data)
        self.scene.collection.objects.link(obj)
        self.select(obj)
        bpy.ops.object.mode_set(mode='EDIT')
        for index, name in enumerate(('Euler', 'Quaternion', 'AxisAngle')):
            bone = data.edit_bones.new(name)
            bone.head = (index, 0, 0)
            bone.tail = (index, 0, 1)
        bpy.ops.object.mode_set(mode='OBJECT')
        for name, mode in zip(('Euler', 'Quaternion', 'AxisAngle'), ('XYZ', 'QUATERNION', 'AXIS_ANGLE')):
            obj.pose.bones[name].rotation_mode = mode
        return obj

    def item(self):
        return self.helper.item(self.scene.saved_memo_text)

    def save(self, **kwargs):
        return c._save_current_item_as_stock(self.scene, self.manager, self.item(), **kwargs)

    def curves(self, obj=None):
        obj = obj or self.rig
        anim = obj.animation_data
        return list(p.iter_action_curves(anim.action, getattr(anim, 'action_slot', None))) if anim else []

    def points(self, obj=None):
        return {(curve.data_path, curve.array_index): tuple(tuple(point.co) for point in curve.keyframe_points)
                for curve in self.curves(obj)}

    def test_changed_camera_does_not_lose_unkeyed_pose(self):
        self.save()
        self.rig.pose.bones['Euler'].location.x = 3
        self.camera.location.x += 1
        self.assertEqual(self.save(), 'append')
        self.assertAlmostEqual(self.rig.pose.bones['Euler'].location.x, 3)

    def test_pose_mode_hidden_unselected_bones_and_selections_preserved(self):
        self.rig.data.bones['AxisAngle'].hide = True
        bpy.ops.object.mode_set(mode='POSE')
        bpy.ops.pose.select_all(action='DESELECT')
        self.rig.data.bones.active = self.rig.data.bones['Euler']
        self.rig.pose.bones['Euler'].location.y = 2
        before = {bone.name for bone in bpy.context.selected_pose_bones or ()}
        self.save()
        self.assertEqual(bpy.context.mode, 'POSE')
        self.assertEqual(before, {bone.name for bone in bpy.context.selected_pose_bones or ()})
        self.assertEqual(bpy.context.view_layer.objects.active, self.rig)
        self.assertTrue(self.rig.select_get())
        self.assertEqual(len(self.manager.saved_camera_data[0]['selected_objects'][0]['pose_bones']), 3)

    def test_pose_json_roundtrip_and_legacy_compatibility(self):
        self.rig.pose.bones['Quaternion']['ik_switch'] = 0.3
        self.save()
        item = self.manager.saved_camera_data[0]
        restored = s._normalize_saved_item(json.loads(json.dumps(item)))
        self.assertEqual(s._stock_signature(item), s._stock_signature(restored))
        changed = copy.deepcopy(restored)
        changed['selected_objects'][0]['pose_bones'][0]['location'][0] += 1
        self.assertNotEqual(s._stock_signature(item), s._stock_signature(changed))
        legacy = copy.deepcopy(item)
        del legacy['selected_objects'][0]['pose_bones']
        del legacy['selected_objects'][0]['pose_custom_properties']
        self.assertNotIn('pose_bones', s._normalize_saved_item(legacy)['selected_objects'][0])

    def test_existing_animation_wins_on_recall_and_no_keys_inserted(self):
        self.scene.mpm_pose_keyframe_priority = True
        self.rig.pose.bones['Euler'].location.x = 1
        self.save()
        self.rig.pose.bones['Euler'].location.x = 8
        self.rig.pose.bones['Euler'].keyframe_insert('location', frame=100)
        self.scene.frame_set(101)
        before = self.points()
        c._apply_saved_camera_data(self.scene, self.camera, self.manager, self.manager.saved_camera_data[0])
        self.assertEqual(self.rig.pose.bones['Euler'].location.x, 8)
        self.assertEqual(self.points(), before)
        self.rig.animation_data_clear()
        c._apply_saved_camera_data(self.scene, self.camera, self.manager, self.manager.saved_camera_data[0])
        self.assertEqual(self.rig.pose.bones['Euler'].location.x, 1)
        self.assertIsNone(self.rig.animation_data)

    def test_additional_memo_and_pose_only_overwrite(self):
        self.save()
        before = self.points()
        self.scene.saved_memo_text = 'memo only'
        op = c.OBJECT_OT_save_selected_stock_memo.bl_idname.split('.')[1]
        self.assertEqual(getattr(bpy.ops.camera, op)(), {'FINISHED'})
        self.assertEqual(len(self.manager.saved_camera_data), 1)
        self.assertEqual(self.points(), before)
        self.rig.pose.bones['Euler'].location.x = 2
        self.assertEqual(getattr(bpy.ops.camera, op)(), {'FINISHED'})
        self.assertEqual(len(self.manager.saved_camera_data), 1)
        self.assertEqual(self.scene.frame_current, 100)
        self.assertEqual(self.rig.pose.bones['Euler'].location.x, 2)

    def test_additional_pose_mode_overwrite_keeps_slot_and_other_frames(self):
        self.save()
        self.rig.pose.bones['Euler'].location.x = 1
        self.save()
        other_stock = copy.deepcopy(self.manager.saved_camera_data[1])
        before_points = self.points()
        c._set_saved_camera_index_safe(self.scene, self.manager, 0)
        created_at = self.manager.saved_camera_data[0]['created_at']
        bpy.ops.object.mode_set(mode='POSE')
        selected = {bone.name for bone in bpy.context.selected_pose_bones or ()}
        bone = self.rig.pose.bones['Euler']
        bone.location.x = 4
        self.rig.pose.bones['Quaternion'].rotation_quaternion = (0.8, 0.6, 0, 0)
        self.rig.pose.bones['AxisAngle'].rotation_axis_angle = (0.5, 0, 0, 1)
        pose = p.pose_signature(p.capture_object_record(self.rig, bpy.context))
        self.assertEqual(bpy.ops.camera.save_selected_stock_memo(), {'FINISHED'})
        self.assertEqual(self.scene.frame_current, 100)
        self.assertEqual(self.scene.saved_camera_index, '0')
        self.assertEqual(len(self.manager.saved_camera_data), 2)
        self.assertEqual(self.manager.saved_camera_data[0]['created_at'], created_at)
        self.assertEqual(self.manager.saved_camera_data[1], other_stock)
        self.assertEqual(bpy.context.mode, 'POSE')
        self.assertEqual(selected, {bone.name for bone in bpy.context.selected_pose_bones or ()})
        self.assertEqual(bpy.context.view_layer.objects.active, self.rig)
        self.assertEqual(p.pose_signature(self.manager.saved_camera_data[0]['selected_objects'][0]), pose)
        for key, points in before_points.items():
            self.assertEqual(self.points()[key][1:], points[1:])
            self.assertEqual(len(self.points()[key]), len(points))
        disk = json.loads(Path(self.manager.save_file_path).read_text(encoding='utf-8'))
        self.assertEqual(disk['camera_data'], self.manager.saved_camera_data)
        self.assertEqual(bpy.ops.camera.save_selected_stock_memo(), {'CANCELLED'})
        c._set_saved_camera_index_safe(self.scene, self.manager, 1)
        self.assertEqual(bone.location.x, 1)
        c._set_saved_camera_index_safe(self.scene, self.manager, 0)
        self.assertEqual(bone.location.x, 4)

    def test_additional_pose_and_memo_still_append(self):
        self.save()
        original = copy.deepcopy(self.manager.saved_camera_data[0])
        self.rig.pose.bones['Euler'].location.x = 3
        self.scene.saved_memo_text = 'changed along with pose'
        self.assertEqual(bpy.ops.camera.save_selected_stock_memo(), {'FINISHED'})
        self.assertEqual(len(self.manager.saved_camera_data), 2)
        self.assertEqual(self.manager.saved_camera_data[0], original)
        self.assertEqual(self.scene.frame_current, 100)

    def test_additional_pose_and_camera_still_append(self):
        self.save()
        original = copy.deepcopy(self.manager.saved_camera_data[0])
        self.rig.pose.bones['Euler'].location.x = 3
        self.camera.location.x += 1
        self.assertEqual(bpy.ops.camera.save_selected_stock_memo(), {'FINISHED'})
        self.assertEqual(len(self.manager.saved_camera_data), 2)
        self.assertEqual(self.manager.saved_camera_data[0], original)
        self.assertEqual(self.scene.frame_current, 100)

    def test_pose_only_predicate_requires_all_other_record_fields_unchanged(self):
        existing = self.item()
        self.rig.pose.bones['Euler'].location.x = 3
        current = self.item()
        self.assertTrue(c._only_pose_record_data_changed(existing, current))
        self.assertFalse(c._only_pose_record_data_changed(existing, existing))
        variants = [
            ('memo', 'changed'), ('bg_image', 'another.png'), ('frame_current', 101),
            ('resolution_x', 300), ('focal_length', 100), ('bg_visible', not current['bg_visible']),
            ('bg_opacity', 0.25), ('bg_depth', 'FRONT'), ('lattice_enabled', not current['lattice_enabled']),
            ('record_view_layer_exclude_state', True), ('record_selected_objects', False),
        ]
        for name, value in variants:
            with self.subTest(name=name):
                changed = copy.deepcopy(current)
                changed[name] = value
                self.assertFalse(c._only_pose_record_data_changed(existing, changed))
        for name in ('name', 'location', 'rotation', 'scale'):
            with self.subTest(object_field=name):
                changed = copy.deepcopy(current)
                if name == 'name':
                    changed['selected_objects'][0][name] += 'renamed'
                else:
                    changed['selected_objects'][0][name][0] += 1
                self.assertFalse(c._only_pose_record_data_changed(existing, changed))
        changed = copy.deepcopy(current)
        changed['selected_objects'][0]['pose_bones'].pop()
        self.assertFalse(c._only_pose_record_data_changed(existing, changed))

    def test_additional_first_record_has_pose_without_leaving_occupied_frame(self):
        bone = self.rig.pose.bones['Euler']
        bone.keyframe_insert('location', frame=100)
        bone.location.x = 4
        self.scene.saved_memo_text = 'first record'
        bpy.ops.object.mode_set(mode='POSE')
        self.assertEqual(bpy.ops.camera.save_selected_stock_memo(), {'FINISHED'})
        self.assertEqual(len(self.manager.saved_camera_data), 1)
        self.assertEqual(self.scene.frame_current, 100)
        self.assertEqual(self.manager.saved_camera_data[0]['memo'], 'first record')
        self.assertEqual(bone.location.x, 4)
        self.assertEqual(bpy.context.mode, 'POSE')
        self.assertEqual(bpy.ops.camera.save_selected_stock_memo(), {'CANCELLED'})

    def test_fractional_custom_values_do_not_create_false_duplicates(self):
        bone = self.rig.pose.bones['Euler']
        bone['ik_switch'] = 0.1234567
        bone['vector'] = [0.1234567, 0.7654321]
        self.rig['switch'] = 0.2345678
        self.save()
        points = self.points()
        self.assertEqual(self.save(), 'skip')
        op = c.OBJECT_OT_save_selected_stock_memo.bl_idname.split('.')[1]
        self.assertEqual(getattr(bpy.ops.camera, op)(), {'CANCELLED'})
        self.assertEqual(len(self.manager.saved_camera_data), 1)
        self.assertEqual(self.points(), points)

    def test_deleted_unselected_rig_does_not_force_append(self):
        other = self.make_rig('PoseTestToRemove')
        self.rig.select_set(True)
        self.save()
        self.select(self.rig)
        existing = self.manager.saved_camera_data[0]
        item = c._build_item_with_recorded_object_delta(
            bpy.context, self.scene, self.camera, existing, self.item(), {other.name})
        self.assertEqual(c._save_current_item_as_stock(self.scene, self.manager, item, overwrite_index=0),
                         'overwrite')
        self.assertEqual(len(self.manager.saved_camera_data), 1)
        self.assertEqual(self.scene.frame_current, 100)


    def test_pose_stocks_on_same_frame_restore_all_bones_without_actions(self):
        bone = self.rig.pose.bones['Euler']
        bone.rotation_euler.x = 0.25
        self.assertEqual(bpy.ops.camera.save_position(), {'FINISHED'})
        bone.rotation_euler.x = 0.75
        self.rig.pose.bones['Quaternion'].rotation_quaternion = (0.8, 0.6, 0, 0)
        self.rig.pose.bones['AxisAngle'].rotation_axis_angle = (0.5, 0, 0, 1)
        self.assertEqual(self.save(), 'append')
        self.assertEqual([item['frame_current'] for item in self.manager.saved_camera_data], [100, 100])
        second = p.pose_signature(self.item()['selected_objects'][0])
        c._set_saved_camera_index_safe(self.scene, self.manager, 0)
        self.assertAlmostEqual(bone.rotation_euler.x, 0.25)
        c._set_saved_camera_index_safe(self.scene, self.manager, 1)
        self.assertEqual(p.pose_signature(self.item()['selected_objects'][0]), second)
        self.assertIsNone(self.rig.animation_data)

    def test_identical_record_does_not_add_stock_or_actions(self):
        self.save()
        before = set(bpy.data.actions.keys())
        self.assertEqual(self.save(), 'skip')
        self.assertEqual(len(self.manager.saved_camera_data), 1)
        self.assertEqual(self.scene.frame_current, 100)
        self.assertEqual(set(bpy.data.actions.keys()), before)
        self.assertIsNone(self.rig.animation_data)

    def test_existing_keys_shared_action_and_manual_pose_untouched_on_save(self):
        bone = self.rig.pose.bones['Euler']
        bone.keyframe_insert('location', frame=100)
        action = self.rig.animation_data.action
        other = self.make_rig('SharedActionRig')
        other.animation_data_create().action = action
        if hasattr(action, 'slots'):
            other.animation_data.action_slot = self.rig.animation_data.action_slot
        self.select(self.rig)
        before, actions = self.points(), set(bpy.data.actions.keys())
        self.scene.frame_set(100, subframe=0.25)
        bone.location.x = 9
        self.save()
        self.assertEqual(bone.location.x, 9)
        self.assertEqual(self.scene.frame_subframe, 0.25)
        self.assertEqual(self.rig.animation_data.action, action)
        self.assertEqual(other.animation_data.action, action)
        self.assertEqual(self.points(), before)
        self.assertEqual(set(bpy.data.actions.keys()), actions)

    def test_additional_pose_overwrite_leaves_existing_keys_untouched(self):
        bone = self.rig.pose.bones['Euler']
        bone.keyframe_insert('location', frame=100)
        self.save()
        original, points = copy.deepcopy(self.manager.saved_camera_data[0]), self.points()
        action = self.rig.animation_data.action
        bone.location.x = 12
        self.assertEqual(bpy.ops.camera.save_selected_stock_memo(), {'FINISHED'})
        saved = self.manager.saved_camera_data[0]
        self.assertEqual(len(self.manager.saved_camera_data), 1)
        self.assertEqual(saved['created_at'], original['created_at'])
        self.assertEqual(self.points(), points)
        self.assertEqual(self.rig.animation_data.action, action)
        self.assertEqual(bone.location.x, 12)
        self.assertEqual(self.scene.frame_current, 100)

    def test_json_failure_leaves_pose_action_stock_and_disk_unchanged(self):
        bone = self.rig.pose.bones['Euler']
        bone.keyframe_insert('location', frame=100)
        self.save()
        path = Path(self.manager.save_file_path)
        disk, stocks, points = path.read_bytes(), copy.deepcopy(self.manager.saved_camera_data), self.points()
        bone.location.x = 10
        with patch.object(self.manager, 'save_data', side_effect=OSError('test failure')):
            with self.assertRaises(OSError):
                self.save()
            with self.assertRaises(OSError):
                self.save(overwrite_index=0, overwrite_pose_only=True)
        self.assertEqual(path.read_bytes(), disk)
        self.assertEqual(self.manager.saved_camera_data, stocks)
        self.assertEqual(self.points(), points)
        self.assertEqual(bone.location.x, 10)
        self.assertEqual(self.scene.frame_current, 100)

    def test_pose_selection_targets_object_mode_all_and_pose_mode_active(self):
        other = self.make_rig('SecondRig')
        self.rig.select_set(True)
        self.save()
        self.assertEqual(len([obj for obj in self.manager.saved_camera_data[0]['selected_objects']
                              if obj.get('pose_bones')]), 2)
        bpy.ops.object.mode_set(mode='POSE')
        captured = self.item()['selected_objects']
        self.assertEqual([obj['name'] for obj in captured if obj.get('pose_bones')], [other.name])
        self.assertIsNone(other.animation_data)
        self.assertIsNone(self.rig.animation_data)

    def test_auto_key_setting_never_creates_keys_during_record_or_recall(self):
        self.scene.tool_settings.use_keyframe_insert_auto = True
        ranges = (self.scene.frame_start, self.scene.frame_end)
        try:
            self.rig.pose.bones['Euler'].location.x = 3
            self.save()
            self.scene.frame_set(777)
            bpy.ops.camera.reload_current_saved_stock()
            self.assertEqual(self.scene.frame_current, 100)
            self.assertIsNone(self.rig.animation_data)
            self.assertTrue(self.scene.tool_settings.use_keyframe_insert_auto)
            self.assertEqual((self.scene.frame_start, self.scene.frame_end), ranges)
        finally:
            self.scene.tool_settings.use_keyframe_insert_auto = False

    def test_every_recall_path_moves_to_saved_frame_and_pose(self):
        bone = self.rig.pose.bones['Euler']
        bone.location.x = 2
        self.save()
        self.scene.frame_set(200)
        bone.location.x = 4
        self.save()
        callbacks = [
            (lambda: c._set_saved_camera_index_safe(self.scene, self.manager, 0), 100, 2),
            (lambda: bpy.ops.camera.next_saved_stock(), 200, 4),
            (lambda: bpy.ops.camera.prev_saved_stock(), 100, 2),
            (lambda: bpy.ops.camera.reload_current_saved_stock(), 100, 2),
            (lambda: bpy.ops.camera.recall_position(), 200, 4),
        ]
        for callback, frame, value in callbacks:
            self.scene.frame_set(777, subframe=0.25)
            bone.location.x = 20
            callback()
            self.assertEqual(self.scene.frame_current, frame)
            self.assertEqual(self.scene.frame_subframe, 0)
            self.assertEqual(bone.location.x, value)

    def test_json_import_restores_frame_and_pose(self):
        self.rig.pose.bones['Euler'].location.x = 6
        self.save()
        path = Path(self.manager.save_file_path)
        self.scene.frame_set(800)
        self.rig.pose.bones['Euler'].location.x = 10
        self.assertEqual(bpy.ops.camera.load_stock_data(filepath=str(path)), {'FINISHED'})
        self.assertEqual(self.scene.frame_current, 100)
        self.assertEqual(self.rig.pose.bones['Euler'].location.x, 6)

    def test_key_priority_and_partial_pose_restore_without_notice(self):
        self.scene.mpm_pose_keyframe_priority = True
        bone = self.rig.pose.bones['Euler']
        bone.location.x = 7
        bone.scale.x = 2
        self.save()
        saved = self.manager.saved_camera_data[0]
        bone.location.x = 1
        bone.keyframe_insert('location', frame=90)
        bone.location.x = 3
        bone.keyframe_insert('location', frame=110)
        self.scene.frame_set(100)
        expected = bone.location.x
        bone.scale.x = 5
        c._apply_saved_camera_data(self.scene, self.camera, self.manager, saved)
        self.assertAlmostEqual(bone.location.x, expected)
        self.assertEqual(bone.scale.x, 2)
        self.assertEqual(p.stock_pose_priority_reasons(saved), {'KEYFRAME'})
        layout = base.Layout()
        base.u._draw_record_read_controls(layout, bpy.context)
        self.assertNotIn('キーフレームに記録があるため', layout.labels)
        self.assertNotIn('該当するポーズはキーが優先されます', layout.labels)
        self.rig.animation_data_clear()
        layout = base.Layout()
        base.u._draw_record_read_controls(layout, bpy.context)
        self.assertNotIn('キーフレームに記録があるため', layout.labels)

    def test_unrelated_camera_object_and_missing_bone_keys_do_not_warn(self):
        self.save()
        saved = self.manager.saved_camera_data[0]
        self.rig.keyframe_insert('location', frame=100)
        self.camera.keyframe_insert('location', frame=100)
        try:
            self.assertEqual(p.stock_pose_priority_reasons(saved), set())
            bone = self.rig.pose.bones['Euler']
            bone.keyframe_insert('location', frame=100)
            saved['selected_objects'][0]['pose_bones'] = [
                rec for rec in saved['selected_objects'][0]['pose_bones'] if rec['name'] != 'Euler']
            self.assertEqual(p.stock_pose_priority_reasons(saved), set())
        finally:
            self.camera.animation_data_clear()

    def test_muted_and_empty_curves_do_not_block_pose_or_warn(self):
        bone = self.rig.pose.bones['Euler']
        bone.location.x = 8
        self.save()
        bone.keyframe_insert('location', frame=100)
        for curve in self.curves():
            curve.mute = True
        saved = self.manager.saved_camera_data[0]
        self.assertEqual(p.stock_pose_priority_reasons(saved), set())
        bone.location.x = 3
        c._apply_saved_camera_data(self.scene, self.camera, self.manager, saved)
        self.assertEqual(bone.location.x, 8)
        for curve in self.curves():
            curve.mute = False
            curve.keyframe_points.clear()
        self.assertEqual(p.stock_pose_priority_reasons(saved), set())

    def test_driver_protection_without_notice(self):
        bone = self.rig.pose.bones['Euler']
        self.save()
        curve = bone.driver_add('location', 0)
        curve.driver.expression = '3.0'
        self.scene.frame_set(101)
        saved = self.manager.saved_camera_data[0]
        c._apply_saved_camera_data(self.scene, self.camera, self.manager, saved)
        self.assertEqual(p.stock_pose_priority_reasons(saved), {'DRIVER'})
        self.assertAlmostEqual(bone.location.x, 3)
        layout = base.Layout()
        base.u._draw_record_read_controls(layout, bpy.context)
        self.assertNotIn('ドライバーが優先されます', layout.labels)
        self.assertNotIn('キーフレームに記録があるため', layout.labels)

    def test_numeric_custom_properties_restore_or_respect_animation(self):
        self.scene.mpm_pose_keyframe_priority = True
        bone = self.rig.pose.bones['Euler']
        bone['ik_switch'] = 0.5
        self.rig['pose_switch'] = 0.75
        self.save()
        saved = self.manager.saved_camera_data[0]
        bone['ik_switch'], self.rig['pose_switch'] = 0.0, 0.0
        c._apply_saved_camera_data(self.scene, self.camera, self.manager, saved)
        self.assertEqual(bone['ik_switch'], 0.5)
        self.assertEqual(self.rig['pose_switch'], 0.75)
        bone['ik_switch'] = 1.0
        bone.keyframe_insert('["ik_switch"]', frame=100)
        c._apply_saved_camera_data(self.scene, self.camera, self.manager, saved)
        self.assertEqual(bone['ik_switch'], 1.0)
        self.assertEqual(p.stock_pose_priority_reasons(saved), {'KEYFRAME'})

    def test_nla_is_preserved_without_notice(self):
        bone = self.rig.pose.bones['Euler']
        bone.keyframe_insert('location', frame=100)
        anim = self.rig.animation_data
        action = anim.action
        track = anim.nla_tracks.new()
        strip = track.strips.new('PoseStrip', 100, action)
        anim.action = None
        bone.location.x = 12
        self.assertEqual(self.save(), 'append')
        self.assertEqual(bone.location.x, 12)
        self.assertEqual(len(anim.nla_tracks), 1)
        self.assertEqual(strip.action, action)
        saved = self.manager.saved_camera_data[0]
        self.assertEqual(p.stock_pose_priority_reasons(saved), {'NLA'})
        layout = base.Layout()
        base.u._draw_record_read_controls(layout, bpy.context)
        self.assertNotIn('該当OBJの保存ポーズは適用しません', layout.labels)
        self.assertNotIn('キーフレームに記録があるため', layout.labels)

    def test_legacy_scene_switch_values_cannot_key_or_disable_recall(self):
        self.scene['mpm_record_keyframes'] = True
        self.scene['mpm_move_to_recorded_frame'] = False
        try:
            self.save()
            self.rig.pose.bones['Euler'].location.x = 4
            self.save()
            self.assertEqual(self.scene.frame_current, 100)
            self.assertIsNone(self.rig.animation_data)
            self.scene.frame_set(500)
            c._set_saved_camera_index_safe(self.scene, self.manager, 0)
            self.assertEqual(self.scene.frame_current, 100)
            self.assertEqual(self.rig.pose.bones['Euler'].location.x, 0)
        finally:
            del self.scene['mpm_record_keyframes']
            del self.scene['mpm_move_to_recorded_frame']

    def test_disabled_object_recording_and_legacy_stocks_have_no_notice(self):
        self.rig.pose.bones['Euler'].keyframe_insert('location', frame=100)
        self.scene.record_selected_objects = False
        self.save()
        self.assertFalse(self.manager.saved_camera_data[0]['selected_objects'])
        self.assertEqual(p.stock_pose_priority_reasons(self.manager.saved_camera_data[0]), set())
        self.scene.record_selected_objects = True
        legacy = self.item()
        for record in legacy['selected_objects']:
            record.pop('pose_bones', None)
        self.assertEqual(p.stock_pose_priority_reasons(legacy), set())
        c._apply_saved_camera_data(self.scene, self.camera, self.manager, legacy)


    def test_stock_priority_restores_keyed_pose_without_changing_action(self):
        bone = self.rig.pose.bones['Euler']
        bone['pose_switch'] = 0.0
        self.rig['rig_switch'] = 0.0
        for prop in ('location', 'rotation_euler', 'scale', '["pose_switch"]'):
            bone.keyframe_insert(prop, frame=100)
        self.rig.keyframe_insert('["rig_switch"]', frame=100)
        action, before = self.rig.animation_data.action, self.points()
        bone.location = (2, 3, 4)
        bone.rotation_euler = (0.25, 0.5, 0.75)
        bone.scale = (1.5, 2.0, 2.5)
        bone['pose_switch'], self.rig['rig_switch'] = 0.75, 0.5
        self.save()
        saved_signature = p.pose_signature(self.item()['selected_objects'][0])
        self.scene.frame_set(777)
        bpy.ops.camera.reload_current_saved_stock()
        bpy.context.view_layer.update()
        self.assertEqual(self.scene.frame_current, 100)
        self.assertEqual(p.pose_signature(self.item()['selected_objects'][0]), saved_signature)
        self.assertEqual(self.rig.animation_data.action, action)
        self.assertEqual(self.points(), before)
        self.scene.mpm_pose_keyframe_priority = True
        self.assertEqual(p.pose_signature(self.item()['selected_objects'][0]), saved_signature)
        bpy.ops.camera.reload_current_saved_stock()
        self.assertEqual(tuple(bone.location), (0, 0, 0))
        self.assertEqual(tuple(bone.rotation_euler), (0, 0, 0))
        self.assertEqual(tuple(bone.scale), (1, 1, 1))
        self.assertEqual(bone['pose_switch'], 0)
        self.assertEqual(self.rig['rig_switch'], 0)
        self.assertEqual(self.points(), before)

    def test_both_priorities_on_all_recall_routes_and_import(self):
        bone = self.rig.pose.bones['Euler']
        bone.location.x = 1
        bone.keyframe_insert('location', frame=100)
        bone.location.x = 8
        self.save()
        self.scene.frame_set(200)
        bone.location.x = 2
        bone.keyframe_insert('location', frame=200)
        bone.location.x = 9
        self.save()
        before = self.points()
        path = Path(self.manager.save_file_path)
        for priority in (False, True):
            self.scene.mpm_pose_keyframe_priority = priority
            callbacks = [
                (lambda: c._set_saved_camera_index_safe(self.scene, self.manager, 0), 100, 8, 1),
                (lambda: bpy.ops.camera.next_saved_stock(), 200, 9, 2),
                (lambda: bpy.ops.camera.prev_saved_stock(), 100, 8, 1),
                (lambda: bpy.ops.camera.reload_current_saved_stock(), 100, 8, 1),
                (lambda: bpy.ops.camera.recall_position(), 200, 9, 2),
                (lambda: bpy.ops.camera.load_stock_data(filepath=str(path)), 100, 8, 1),
            ]
            for callback, frame, stock_value, keyed_value in callbacks:
                with self.subTest(priority=priority, frame=frame, callback=callback):
                    self.scene.frame_set(777, subframe=0.25)
                    callback()
                    bpy.context.view_layer.update()
                    self.assertEqual(self.scene.frame_current, frame)
                    self.assertEqual(bone.location.x, keyed_value if priority else stock_value)
                    self.assertEqual(self.scene.mpm_pose_keyframe_priority, priority)
                    self.assertEqual(self.points(), before)

    def test_both_priorities_draw_only_checkbox_without_pose_inspection(self):
        bone = self.rig.pose.bones['Euler']
        bone.keyframe_insert('location', frame=100)
        bone.location.x = 8
        self.save()
        for priority in (False, True):
            self.scene.mpm_pose_keyframe_priority = priority
            self.assertEqual(bone.location.x, 8)
            layout = base.Layout()
            with patch.object(p, 'stock_pose_priority_reasons', side_effect=AssertionError('UI pose scan')):
                with patch.object(layout, 'prop', wraps=layout.prop) as calls:
                    base.u._draw_record_read_controls(layout, bpy.context)
            self.assertIn('mpm_pose_keyframe_priority', [call.args[1] for call in calls.call_args_list])
            self.assertEqual(bone.location.x, 8)
            self.assertNotIn('キーフレームに記録があるため', layout.labels)
            self.assertNotIn('読込時はストックのポーズを優先', layout.labels)
            self.assertNotIn('キーのポーズに戻る場合があります', layout.labels)
            self.assertEqual(self.scene.frame_current, 100)

    def test_priority_choice_does_not_change_recording_or_duplicate_detection(self):
        self.save()
        for priority in (True, False):
            self.scene.mpm_pose_keyframe_priority = priority
            self.assertEqual(self.save(), 'skip')
            self.assertEqual(len(self.manager.saved_camera_data), 1)
            self.assertIsNone(self.rig.animation_data)
            self.assertNotIn('mpm_pose_keyframe_priority', self.item())

    def test_stock_priority_preserves_drivers_even_when_other_channels_have_keys(self):
        bone = self.rig.pose.bones['Euler']
        bone.rotation_euler.x = 0
        bone.keyframe_insert('rotation_euler', frame=100)
        bone.location.x = 5
        bone.rotation_euler.x = 0.75
        self.save()
        curve = bone.driver_add('location', 0)
        curve.driver.expression = '3.0'
        bpy.ops.camera.reload_current_saved_stock()
        bpy.context.view_layer.update()
        self.assertAlmostEqual(bone.location.x, 3)
        self.assertAlmostEqual(bone.rotation_euler.x, 0.75)
        self.assertFalse(curve.mute)
        layout = base.Layout()
        base.u._draw_record_read_controls(layout, bpy.context)
        self.assertNotIn('ドライバーが優先されます', layout.labels)
        self.assertNotIn('読込時はストックのポーズを優先', layout.labels)

    def test_stock_pose_is_not_reapplied_on_later_frame_changes(self):
        bone = self.rig.pose.bones['Euler']
        bone.location.x = 1
        bone.keyframe_insert('location', frame=100)
        bone.location.x = 8
        self.save()
        bpy.ops.camera.reload_current_saved_stock()
        self.assertEqual(bone.location.x, 8)
        self.scene.frame_set(101)
        self.assertEqual(bone.location.x, 1)
        self.assertFalse(self.scene.mpm_pose_keyframe_priority)

    def test_non_euler_rotations_restore_over_keys_without_changing_modes_or_keys(self):
        for name, channel in (('Quaternion', 'rotation_quaternion'), ('AxisAngle', 'rotation_axis_angle')):
            bone = self.rig.pose.bones[name]
            bone.keyframe_insert(channel, frame=100)
        before = self.points()
        self.rig.pose.bones['Quaternion'].rotation_quaternion = (0.8, 0.6, 0, 0)
        self.rig.pose.bones['AxisAngle'].rotation_axis_angle = (0.5, 0, 0, 1)
        self.save()
        expected = p.pose_signature(self.item()['selected_objects'][0])
        self.scene.frame_set(777)
        bpy.ops.camera.reload_current_saved_stock()
        bpy.context.view_layer.update()
        self.assertEqual(p.pose_signature(self.item()['selected_objects'][0]), expected)
        self.assertEqual(self.points(), before)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PoseRegression))
    if not result.wasSuccessful():
        raise RuntimeError('Blender pose regression tests failed')
