"""Local-view stock tests in an isolated factory-startup Blender."""
from pathlib import Path
import sys
import unittest

import bpy

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_regression as base

c, s, lv = base.c, base.s, base.addon.local_view


class LocalViewRegression(unittest.TestCase):
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
        self.area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
        self.space = self.area.spaces.active
        self.region = next(r for r in self.area.regions if r.type == 'WINDOW')
        self.override = bpy.context.temp_override(area=self.area, region=self.region)
        self.override.__enter__()
        self.old_objects = {o.as_pointer() for o in bpy.data.objects}
        self.first, self.second = self.helper.new_mesh('LocalA'), self.helper.new_mesh('LocalB')
        self.scene.record_view_layer_exclude_state = True
        self.scene.record_local_view_state = True
        self.space.use_local_camera = False
        self.select(self.first)

    def tearDown(self):
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        if self.space.local_view is not None:
            bpy.ops.view3d.localview(frame_selected=False)
        for obj in list(bpy.data.objects):
            if obj.as_pointer() not in self.old_objects:
                bpy.data.objects.remove(obj, do_unlink=True)
        self.override.__exit__(None, None, None)

    def select(self, obj):
        for other in bpy.context.view_layer.objects:
            other.select_set(other == obj)
        bpy.context.view_layer.objects.active = obj

    def state(self, *objects, enabled=True):
        return {'record_view_layer_exclude_state': True,
                'local_view_state': {'enabled': enabled, 'objects': [o.name for o in objects]}}

    def apply(self, data):
        self.assertTrue(c._apply_saved_camera_data(self.scene, self.camera, self.manager, data))
        bpy.context.view_layer.update()

    def names(self):
        graph = bpy.context.view_layer.depsgraph
        return {o.name for o in bpy.context.view_layer.objects if o.evaluated_get(graph).local_view_get(self.space)}

    def test_capture_and_normalized_json_roundtrip(self):
        self.apply(self.state(self.first, self.second))
        item = self.helper.item()
        self.manager.save_data(camera_data=[item])
        self.manager.load_data()
        self.assertEqual(self.manager.saved_camera_data[0]['local_view_state'], item['local_view_state'])
        self.assertEqual(set(item['local_view_state']['objects']), {self.first.name, self.second.name})
        self.assertTrue(base.u._saved_data_has_view_layer_state(item))

    def test_on_off_membership_and_selection(self):
        before = {o.name for o in bpy.context.selected_objects}
        self.apply(self.state(self.first, self.second))
        self.assertEqual(self.names(), {self.first.name, self.second.name})
        self.assertEqual({o.name for o in bpy.context.selected_objects}, before)
        self.assertEqual(bpy.context.object, self.first)
        self.apply(self.state(self.first, enabled=False))
        self.assertIsNone(self.space.local_view)
        self.assertEqual({o.name for o in bpy.context.selected_objects}, before)

    def test_enter_without_selection(self):
        self.select(None)
        self.apply(self.state(self.second))
        self.assertEqual(self.names(), {self.second.name})
        self.assertFalse(any(o.select_get() for o in bpy.context.view_layer.objects))
        self.assertIsNone(bpy.context.object)

    def test_legacy_and_disabled_do_not_change_local_view(self):
        self.apply(self.state(self.first))
        self.apply({'frame_current': 1})
        self.assertEqual(self.names(), {self.first.name})
        item = self.state(enabled=False)
        item['record_view_layer_exclude_state'] = False
        self.apply(item)
        self.assertEqual(self.names(), {self.first.name})
        self.assertNotIn('local_view_state', s._normalize_saved_item({}))

    def test_no_viewport_and_disabled_capture_do_not_guess(self):
        self.assertEqual(lv.capture_fields(bpy.context, False), {})
        with bpy.context.temp_override(area=None, region=None):
            self.assertEqual(lv.capture_fields(bpy.context, True), {})
            lv.apply(bpy.context, self.state(self.first))
        self.assertIsNone(self.space.local_view)

    def test_missing_members_keep_existing_view(self):
        self.apply(self.state(self.first))
        self.apply({'record_view_layer_exclude_state': True,
                    'local_view_state': {'enabled': True, 'objects': ['missing']}})
        self.assertEqual(self.names(), {self.first.name})
        self.apply({'record_view_layer_exclude_state': True,
                    'local_view_state': {'enabled': True, 'objects': ['missing', self.second.name]}})
        self.assertEqual(self.names(), {self.second.name})

    def test_duplicate_detection_and_additional_data_overwrite(self):
        self.assertEqual(bpy.ops.camera.save_position(), {'FINISHED'})
        self.assertEqual(bpy.ops.camera.save_position(), {'CANCELLED'})
        self.apply(self.state(self.first))
        self.assertEqual(bpy.ops.camera.save_position(), {'FINISHED'})
        self.assertEqual(len(self.manager.saved_camera_data), 2)
        self.apply(self.state(self.second))
        self.assertEqual(bpy.ops.camera.save_selected_stock_memo(), {'FINISHED'})
        self.assertEqual(len(self.manager.saved_camera_data), 2)
        self.assertEqual(self.manager.saved_camera_data[-1]['local_view_state']['objects'], [self.second.name])

    def test_warning_conditions_and_local_camera(self):
        self.apply(self.state(self.first))
        self.space.region_3d.view_perspective = 'PERSP'
        self.assertIsNone(lv.warning_camera(bpy.context))
        self.space.region_3d.view_perspective = 'CAMERA'
        self.assertEqual(lv.warning_camera(bpy.context), self.camera)
        self.space.overlay.show_overlays = False
        self.assertEqual(lv.warning_camera(bpy.context), self.camera)
        self.camera.local_view_set(self.space, True)
        bpy.context.view_layer.update()
        self.assertIsNone(lv.warning_camera(bpy.context))
        second_cam = bpy.data.objects.new('OtherCamera', bpy.data.cameras.new('OtherCamera'))
        self.scene.collection.objects.link(second_cam)
        self.space.use_local_camera = True
        self.space.camera = second_cam
        self.assertEqual(lv.warning_camera(bpy.context), second_cam)
        self.space.use_local_camera = False
        self.space.overlay.show_overlays = True

    def test_other_viewport_is_untouched(self):
        other = next(a for a in bpy.context.screen.areas if a != self.area and a.type == 'PROPERTIES')
        old_type = other.type
        try:
            other.type = 'VIEW_3D'
            with bpy.context.temp_override(area=other, region=next(r for r in other.regions if r.type == 'WINDOW')):
                lv.apply(bpy.context, self.state(self.second))
            self.apply(self.state(self.first))
            self.assertEqual(self.names(), {self.first.name})
            graph = bpy.context.view_layer.depsgraph
            self.assertTrue(self.second.evaluated_get(graph).local_view_get(other.spaces.active))
            self.assertFalse(self.first.evaluated_get(graph).local_view_get(other.spaces.active))
        finally:
            with bpy.context.temp_override(area=other, region=next(r for r in other.regions if r.type == 'WINDOW')):
                if other.spaces.active.local_view:
                    bpy.ops.view3d.localview(frame_selected=False)
            other.type = old_type

    def test_invalid_state_is_not_silently_defaulted(self):
        for state in (None, {}, {'enabled': 'yes'}, {'enabled': True, 'objects': 'Cube'},
                      {'enabled': True, 'objects': [None]}):
            with self.assertRaises(ValueError):
                s._normalize_saved_list([{'local_view_state': state}])
        self.assertEqual(lv.signature({'enabled': True, 'objects': ['b', 'a', 'a']}),
                         lv.signature({'enabled': True, 'objects': ['a', 'b']}))

    def test_view_layer_exclusion_is_restored_before_local_membership(self):
        collection = bpy.data.collections.new('LocalExcludedCollection')
        self.scene.collection.children.link(collection)
        collection.objects.link(self.second)
        self.scene.collection.objects.unlink(self.second)
        bpy.context.view_layer.update()
        self.apply(self.state(self.first, self.second))
        item = self.helper.item()
        child = bpy.context.view_layer.layer_collection.children[collection.name]
        child.exclude = True
        bpy.context.view_layer.update()
        self.apply(item)
        self.assertFalse(child.exclude)
        self.assertEqual(self.names(), {self.first.name, self.second.name})
        bpy.data.collections.remove(collection)

    def test_hidden_selection_does_not_prevent_local_entry(self):
        self.first.hide_set(True)
        self.apply(self.state(self.second))
        self.assertIsNotNone(self.space.local_view)
        self.assertEqual(self.names(), {self.second.name})

    def test_pose_mode_restore_after_local_view_change(self):
        rig = bpy.data.objects.new('LocalPoseRig', bpy.data.armatures.new('LocalPoseRig'))
        self.scene.collection.objects.link(rig)
        self.select(rig)
        bpy.ops.object.mode_set(mode='EDIT')
        bone = rig.data.edit_bones.new('Control')
        bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
        bpy.ops.object.mode_set(mode='POSE')
        bone = rig.pose.bones['Control']
        bone.location.x = 1
        bone.keyframe_insert('location', frame=1)
        self.scene.record_selected_objects = True
        self.apply(self.state(rig, self.first))
        bone.location.x = 3
        item = self.helper.item()
        self.apply(self.state(enabled=False))
        self.apply(item)
        self.assertEqual(bpy.context.mode, 'POSE')
        self.assertEqual(self.names(), {rig.name, self.first.name})
        self.assertAlmostEqual(bone.location.x, 3)
        self.assertAlmostEqual(rig.evaluated_get(bpy.context.evaluated_depsgraph_get()).pose.bones['Control'].location.x, 3)
        self.scene.mpm_pose_keyframe_priority = True
        self.apply(item)
        self.assertAlmostEqual(bone.location.x, 1)

    def test_independent_recording_and_restore_all_four_combinations(self):
        collection = bpy.data.collections.new('IndependentLayer')
        self.scene.collection.children.link(collection)
        collection.objects.link(self.second)
        self.scene.collection.objects.unlink(self.second)
        bpy.context.view_layer.update()
        child = bpy.context.view_layer.layer_collection.children[collection.name]
        try:
            for view_enabled, local_enabled in ((False, False), (True, False), (False, True), (True, True)):
                with self.subTest(view=view_enabled, local=local_enabled):
                    child.exclude = False
                    self.apply(self.state(self.first))
                    self.scene.record_view_layer_exclude_state = view_enabled
                    self.scene.record_local_view_state = local_enabled
                    item = self.helper.item()
                    self.manager.save_data(camera_data=[item])
                    self.manager.load_data()
                    item = self.manager.saved_camera_data[0]
                    self.assertEqual(item['record_local_view_state'], local_enabled)
                    self.assertEqual(bool(item['view_layer_exclude_state']['collections']), view_enabled)
                    self.assertEqual('local_view_state' in item, local_enabled)
                    self.assertEqual(base.u._saved_data_has_view_layer_state(item), view_enabled)
                    self.assertEqual(lv.has_recorded_state(item), local_enabled)
                    child.exclude = True
                    self.apply(self.state(self.camera))
                    # Read uses saved flags, not the next-recording checkboxes.
                    self.scene.record_view_layer_exclude_state = not view_enabled
                    self.scene.record_local_view_state = not local_enabled
                    self.apply(item)
                    self.assertEqual(child.exclude, not view_enabled)
                    expected = {self.first.name if local_enabled else self.camera.name}
                    if view_enabled and not local_enabled:
                        # Blender adds newly unexcluded bases to local views; no local snapshot is applied.
                        expected.add(self.second.name)
                    self.assertEqual(self.names(), expected)
        finally:
            bpy.data.collections.remove(collection)

    def test_legacy_flag_migration_and_explicit_local_opt_out(self):
        old = self.state(self.first)
        normalized = s._normalize_saved_item(old)
        self.assertTrue(normalized['record_local_view_state'])
        legacy_normalized = dict(normalized)
        legacy_normalized.pop('record_local_view_state')
        self.assertEqual(s._stock_signature(legacy_normalized), s._stock_signature(normalized))
        self.assertFalse(s._normalize_saved_item({'record_view_layer_exclude_state': True})['record_local_view_state'])
        self.apply(normalized)
        explicit_off = {**self.state(self.second), 'record_local_view_state': False}
        self.apply(s._normalize_saved_item(explicit_off))
        self.assertEqual(self.names(), {self.first.name})
        local_only = {**self.state(self.second), 'record_view_layer_exclude_state': False, 'record_local_view_state': True}
        self.apply(s._normalize_saved_item(local_only))
        self.assertEqual(self.names(), {self.second.name})

    def test_local_only_duplicate_and_additional_data_overwrite(self):
        self.scene.record_view_layer_exclude_state = False
        self.assertEqual(bpy.ops.camera.save_position(), {'FINISHED'})
        self.assertEqual(bpy.ops.camera.save_position(), {'CANCELLED'})
        self.apply(self.state(self.first))
        self.assertEqual(bpy.ops.camera.save_selected_stock_memo(), {'FINISHED'})
        self.assertEqual(len(self.manager.saved_camera_data), 1)
        self.assertTrue(self.manager.saved_camera_data[0]['record_local_view_state'])
        self.scene.record_local_view_state = False
        self.assertEqual(bpy.ops.camera.save_selected_stock_memo(), {'FINISHED'})
        self.assertEqual(len(self.manager.saved_camera_data), 1)
        self.assertNotIn('local_view_state', self.manager.saved_camera_data[0])

    def test_reset_turns_both_recording_options_off(self):
        self.assertEqual(bpy.ops.camera.reset_attached_data_input_state(), {'FINISHED'})
        self.assertFalse(self.scene.record_local_view_state)
        self.assertFalse(self.scene.record_view_layer_exclude_state)

    def test_six_badges_in_equal_width_rows(self):
        class Layout(base.Layout):
            def __init__(self):
                super().__init__()
                self.factors = []

            def split(self, **kwargs):
                self.factors.append(kwargs['factor'])
                return self

        item = {**self.state(self.first), 'record_view_layer_exclude_state': False,
                'record_local_view_state': True, 'memo': 'example',
                'fisheye_state': {'enabled': True}}
        layout = Layout()
        base.u._draw_saved_data_status_labels(layout, item)
        badges = [args for name, args in layout.operators if name == 'camera.additional_data_status_badge']
        self.assertEqual([b['text'] for b in badges],
                         ['OBJデータ', '魚眼', 'ラティス', 'ビューレイヤー', 'ローカルビュー', '適用'])
        self.assertEqual([b['depress'] for b in badges], [False, True, False, False, True, True])
        self.assertEqual(layout.factors, [1 / 3, 0.5, 1 / 3, 0.5])


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(LocalViewRegression))
    if not result.wasSuccessful():
        raise SystemExit(1)
