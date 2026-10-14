"""Review fixes in a disposable Blender --background --factory-startup process."""
import copy
import importlib
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import bpy
from mathutils import Quaternion

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_regression as base
import blender_pose_regression as poses

c, a, s, b, lm, x = base.c, base.a, base.s, base.b, base.lm, base.x


class ReviewRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base.Regression.setUpClass()

    @classmethod
    def tearDownClass(cls):
        base.Regression.tearDownClass()

    def setUp(self):
        self.scenes = {scene.as_pointer() for scene in bpy.data.scenes}
        self.pose = poses.PoseRegression()
        self.pose.setUp()
        self.scene, self.manager = self.pose.scene, self.pose.manager
        self.scene.mpm_lattice_sets.clear()
        self.scene.mpm_lattice_management_enabled = True
        self.scene.mpm_lattice_multi_set_enabled = False
        self.folder = base.Regression.folder
        self.manager.save_data(camera_data=[self.pose.item()])
        c.rebuild_enum_cache(self.manager)
        c._set_saved_camera_index_safe(self.scene, self.manager, 0, apply_saved_data=False)
        self.all_manager = a.get_all_object_data_manager()
        self.all_manager.save_data(str(self.folder / 'all.json'), stocks=[a._build_current_stock_item(self.scene)])

    def tearDown(self):
        bpy.context.window.scene = self.scene
        self.scene.mpm_lattice_sets.clear()
        for scene in list(bpy.data.scenes):
            if scene.as_pointer() not in self.scenes:
                bpy.data.scenes.remove(scene)
        self.pose.tearDown()

    def run_operator(self, cls, **props):
        reports = []
        op = SimpleNamespace(report=lambda level, message: reports.append((level, message)), **props)
        return cls.execute(op, bpy.context), reports

    def test_wrong_json_both_directions_keep_memory_paths_and_files(self):
        for manager, other, attr, cls in (
                (self.manager, self.all_manager, 'saved_camera_data', c.OBJECT_OT_load_stock_data),
                (self.all_manager, self.manager, 'stocks', a.OBJECT_OT_load_all_object_data_json)):
            current = getattr(manager, attr)
            current_path = manager.save_file_path
            foreign = Path(other.save_file_path)
            original = foreign.read_bytes()
            result, reports = self.run_operator(cls, filepath=str(foreign))
            self.assertEqual(result, {'CANCELLED'})
            self.assertEqual(reports[0][0], {'ERROR'})
            self.assertIs(getattr(manager, attr), current)
            self.assertEqual(manager.save_file_path, current_path)
            manager.save_data()
            self.assertEqual(foreign.read_bytes(), original)

    def test_invalid_json_and_missing_files_keep_both_managers(self):
        path = self.folder / 'invalid.json'
        for manager, key, attr in ((self.manager, 'camera_data', 'saved_camera_data'),
                                   (self.all_manager, 'all_object_data', 'stocks')):
            current, target = getattr(manager, attr), manager.save_file_path
            for value in ('{broken', '[]', '{}', json.dumps({key: {}}),
                          json.dumps({key: [None]}), json.dumps({'camera_data': [], 'all_object_data': []})):
                path.write_text(value, encoding='utf-8')
                with self.assertRaises((ValueError, OSError)):
                    manager.load_data(str(path))
                self.assertIs(getattr(manager, attr), current)
                self.assertEqual(manager.save_file_path, target)
                self.assertEqual(path.read_text(encoding='utf-8'), value)
            with self.assertRaises(FileNotFoundError):
                manager.load_data(str(self.folder / 'missing.json'))
            self.assertIs(getattr(manager, attr), current)
            self.assertEqual(manager.save_file_path, target)

    def test_read_permission_failure_is_not_success(self):
        for manager, attr in ((self.manager, 'saved_camera_data'), (self.all_manager, 'stocks')):
            current, target = getattr(manager, attr), manager.save_file_path
            with patch('builtins.open', side_effect=PermissionError('test read failure')):
                with self.assertRaises(PermissionError):
                    manager.load_data(str(self.folder / 'denied.json'))
            self.assertIs(getattr(manager, attr), current)
            self.assertEqual(manager.save_file_path, target)

    def test_append_rejects_foreign_json_without_writes_or_scene_changes(self):
        original = Path(self.manager.save_file_path).read_bytes()
        self.scene.frame_set(123)
        self.scene.saved_memo_text = 'unsaved memo'
        result, _ = self.run_operator(c.OBJECT_OT_append_stock_data,
                                     filepath=self.all_manager.save_file_path, skip_identical=True)
        self.assertEqual(result, {'CANCELLED'})
        self.assertEqual(Path(self.manager.save_file_path).read_bytes(), original)
        self.assertEqual(self.scene.frame_current, 123)
        self.assertEqual(self.scene.saved_memo_text, 'unsaved memo')

    def test_legacy_and_empty_json_remain_readable(self):
        path = self.folder / 'legacy.json'
        for manager, key, attr in ((self.manager, 'camera_data', 'saved_camera_data'),
                                   (self.all_manager, 'all_object_data', 'stocks')):
            for values in ([{}], []):
                path.write_text(json.dumps({key: values}), encoding='utf-8-sig')
                manager.load_data(str(path))
                self.assertEqual(len(getattr(manager, attr)), len(values))
                self.assertEqual(manager.save_file_path, str(path))

    def test_startup_bad_file_blocks_automatic_save_but_allows_recovery(self):
        for cls in (c.CameraDataManager, a.AllObjectDataManager):
            path = self.folder / ('bad-' + cls.__name__ + '.json')
            path.write_text('{broken', encoding='utf-8')
            manager = cls()
            manager.save_file_path = str(path)
            s._load_initial_stock_data(manager)
            with self.assertRaises(ValueError):
                manager.save_data()
            self.assertEqual(path.read_text(encoding='utf-8'), '{broken')
            recovery = str(self.folder / ('recovered-' + cls.__name__ + '.json'))
            manager.save_data(recovery)
            manager.load_data(recovery)
            manager.save_data()
            self.assertEqual(path.read_text(encoding='utf-8'), '{broken')

    def test_latest_and_central_recall_apply_once(self):
        for operator in (bpy.ops.camera.recall_position, bpy.ops.camera.reload_current_saved_stock):
            with patch.object(c, '_apply_saved_camera_data', wraps=c._apply_saved_camera_data) as apply:
                self.assertEqual(operator(), {'FINISHED'})
                self.assertEqual(apply.call_count, 1)

    def test_image_skip_preserves_direction_and_wraps(self):
        paths = [str(self.folder / f'shot{n:06d}.png') for n in range(1, 5)]
        with patch.object(b, '_iter_folder_image_paths', return_value=paths):
            with patch.object(b, '_stocked_bg_name_set', return_value={'shot000003.png'}):
                for step, expected in ((-1, paths[1]), (1, paths[3])):
                    self.assertEqual(b._find_next_folder_image_path(
                        self.manager, 'shot000003.png', step, True), (expected, 3))
                self.assertEqual(b._find_next_folder_image_path(self.manager, 'shot000004.png', 1, True)[0], paths[0])
                self.assertEqual(b._find_next_folder_image_path(self.manager, 'shot000001.png', -1, True)[0], paths[3])
                self.assertEqual(b._find_next_folder_image_path(self.manager, 'missing.png', -1, True)[0], paths[0])
            with patch.object(b, '_stocked_bg_name_set', return_value={Path(p).name for p in paths}):
                self.assertEqual(b._find_next_folder_image_path(self.manager, 'shot000003.png', 1, True), ('', 0))

    def test_partial_location_scale_and_euler_keys_restore_unkeyed_axes(self):
        bone = self.pose.rig.pose.bones['Euler']
        self.scene.mpm_pose_keyframe_priority = True
        for prop, original, key_value in (('location', (1, 2, 3), 8),
                                           ('scale', (1, 2, 3), 4),
                                           ('rotation_euler', (0.1, 0.2, 0.3), 0.8)):
            setattr(bone, prop, original)
            saved = self.pose.item()
            getattr(bone, prop)[0] = key_value
            bone.keyframe_insert(prop, index=0, frame=100)
            getattr(bone, prop)[1] = 9
            getattr(bone, prop)[2] = 10
            points = self.pose.points()
            c._apply_saved_camera_data(self.scene, self.pose.camera, self.manager, saved)
            actual = getattr(bone, prop)
            for actual_value, expected in zip(actual, (key_value, original[1], original[2])):
                self.assertAlmostEqual(actual_value, expected, places=5)
            self.assertEqual(points, self.pose.points())

    def test_xmp_rotation_uses_active_rotation_mode(self):
        camera = self.pose.camera
        camera.rotation_euler = (0, 0, 0)
        camera.rotation_mode = 'QUATERNION'
        camera.rotation_quaternion = Quaternion((0, 0, 1), math.pi / 2)
        self.assertIn('Z=90.000', x._camera_metadata_values(camera, self.scene)[1])
        camera.rotation_mode = 'AXIS_ANGLE'
        camera.rotation_axis_angle = (math.pi / 2, 0, 1, 0)
        self.assertIn('Y=90.000', x._camera_metadata_values(camera, self.scene)[1])

    def test_lattice_does_not_touch_other_scene(self):
        other = bpy.data.scenes.new('ReviewOtherScene')
        bpy.context.window.scene = other
        helper = base.Regression()
        helper.scene = other
        obj = helper.new_mesh('ReviewOtherMesh')
        helper.lattice_set([obj])
        bpy.context.window.scene = self.scene
        self.scene.mpm_lattice_management_enabled = False
        self.assertTrue(obj.modifiers[0].show_viewport)
        self.assertTrue(other.mpm_lattice_management_enabled)
        # Scene-owned callbacks must not accidentally use the active scene.
        other.mpm_lattice_management_enabled = False
        self.assertFalse(obj.modifiers[0].show_viewport)

    def test_lattice_stock_batches_set_updates(self):
        for i in range(4):
            self.pose.helper.lattice_set([self.pose.helper.new_mesh('ReviewMesh' + str(i))])
        state = lm.export_lattice_stock_state(self.scene)
        with patch.object(lm, '_set_single_lattice_set_effective_enabled',
                          wraps=lm._set_single_lattice_set_effective_enabled) as update:
            lm.apply_lattice_stock_state(self.scene, state)
            self.assertEqual(update.call_count, 4)
        self.assertEqual(state, lm.export_lattice_stock_state(self.scene))

    def test_different_camera_does_not_compare_all_saved_pose_bones(self):
        items = []
        for i in range(100):
            item = copy.deepcopy(self.pose.item())
            item['position'][0] = i
            items.append(item)
        self.manager.save_data(camera_data=items)
        new_item = self.pose.item()
        new_item['position'][0] = 1000
        with patch.object(s, 'pose_signature', side_effect=AssertionError('unnecessary pose comparison')):
            self.assertEqual(s._find_matching_saved_item_index(self.manager, new_item), -1)
        identical = copy.deepcopy(self.manager.saved_camera_data[40])
        self.assertEqual(s._find_matching_saved_item_index(self.manager, identical), 40)
        identical['selected_objects'][0]['pose_bones'][0]['location'][0] += 1
        self.assertEqual(s._find_matching_saved_item_index(self.manager, identical), -1)

    def test_streamed_json_roundtrip_and_serialization_failure_keep_disk(self):
        path = self.folder / 'streamed.json'
        values = {'camera_data': [self.pose.item(), self.pose.item()], 'memo': 'Japanese: \u6458\u8981', 'empty': []}
        s._write_json_atomic(str(path), values)
        self.assertEqual(json.loads(path.read_text(encoding='utf-8')), values)
        original = path.read_bytes()
        values['camera_data'].append(object())
        with self.assertRaises(TypeError):
            s._write_json_atomic(str(path), values)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(list(self.folder.glob('.mpm-*.tmp')))

    def test_partial_custom_array_key_and_rotation_mode_guard(self):
        bone = self.pose.rig.pose.bones['Euler']
        bone['vector'] = [1.0, 2.0, 3.0]
        saved = self.pose.item()
        bone['vector'] = [8.0, 9.0, 10.0]
        bone.keyframe_insert('["vector"]', index=0, frame=100)
        self.scene.mpm_pose_keyframe_priority = True
        c._apply_saved_camera_data(self.scene, self.pose.camera, self.manager, saved)
        self.assertEqual(list(bone['vector']), [8.0, 2.0, 3.0])
        bone.rotation_mode = 'QUATERNION'
        bone.rotation_quaternion = Quaternion((0, 1, 0), 0.7)
        bone.keyframe_insert('rotation_quaternion', frame=100)
        c._apply_saved_camera_data(self.scene, self.pose.camera, self.manager, saved)
        self.assertEqual(bone.rotation_mode, 'QUATERNION')
        self.assertAlmostEqual(bone.rotation_quaternion.to_euler().y, 0.7, places=5)

    def test_lattice_batch_exception_releases_guard(self):
        stock = importlib.import_module(base.ROOT.name + '.lattice_stock')
        with patch.object(stock, '_restore_lattice_stock_properties', side_effect=RuntimeError('test failure')):
            with self.assertRaises(RuntimeError):
                lm.apply_lattice_stock_state(self.scene, {})
        self.assertFalse(lm._lattice_updates_suspended(self.scene))

    def test_partial_quaternion_and_axis_angle_keys(self):
        self.scene.mpm_pose_keyframe_priority = True
        for name, prop, values in (
                ('Quaternion', 'rotation_quaternion', (1.0, 0.1, 0.2, 0.3)),
                ('AxisAngle', 'rotation_axis_angle', (0.4, 0.0, 1.0, 0.0))):
            bone = self.pose.rig.pose.bones[name]
            setattr(bone, prop, values)
            saved = self.pose.item()
            getattr(bone, prop)[0] = 0.8
            bone.keyframe_insert(prop, index=0, frame=100)
            getattr(bone, prop)[1] = 0.9
            getattr(bone, prop)[2] = 0.8
            points = self.pose.points()
            c._apply_saved_camera_data(self.scene, self.pose.camera, self.manager, saved)
            for actual, expected in zip(getattr(bone, prop), (0.8, *values[1:])):
                self.assertAlmostEqual(actual, expected, places=5)
            self.assertEqual(points, self.pose.points())

    def test_lattice_batch_preserves_single_multi_and_disabled_states(self):
        groups = [self.pose.helper.lattice_set([self.pose.helper.new_mesh('ReviewToggle' + str(i))])
                  for i in range(2)]
        objects = [bpy.data.objects[group.objects[0].object_name] for group in groups]
        state = lm.export_lattice_stock_state(self.scene)
        state['multi_set_enabled'] = True
        state['set_states'][0]['modifiers_enabled'] = True
        state['set_states'][1]['modifiers_enabled'] = False
        lm.apply_lattice_stock_state(self.scene, state)
        self.assertEqual([obj.modifiers[0].show_viewport for obj in objects], [True, False])
        state['multi_set_enabled'] = False
        state['active_set_uid'] = groups[1].set_uid
        lm.apply_lattice_stock_state(self.scene, state)
        self.assertEqual([obj.modifiers[0].show_viewport for obj in objects], [False, True])
        state['lattice_enabled'] = False
        lm.apply_lattice_stock_state(self.scene, state)
        self.assertEqual([obj.modifiers[0].show_viewport for obj in objects], [False, False])
        self.assertTrue(all(group.lattice_obj.hide_viewport for group in groups))


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], verbosity=2)
