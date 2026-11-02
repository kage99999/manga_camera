"""Stock/exclusion regressions in an isolated factory-startup Blender process."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_fisheye_regression import FisheyeRegression, base, bpy, engine, fish

stock = base.c.fisheye_stock


class FisheyeStock(FisheyeRegression):
    def setUp(self):
        super().setUp()
        self.scene.mpm_lattice_sets.clear()
        self.settings.keep_bounds_helpers = True
        self.settings.zoom = 1
        self.settings.ellipse_x = self.settings.ellipse_y = 1
        self.scene.gyogan_dolly.target = None
        self.scene.gyogan_dolly.ratio = 1
        self.scene.gyogan_dolly.compensate_fisheye = True

    def read(self, item):
        self.assertTrue(base.c._apply_saved_camera_data(self.scene, self.camera, self.manager, item))
        engine.targets.reconcile(self.scene)
        bpy.context.view_layer.update()

    def vertices(self, obj):
        graph = bpy.context.evaluated_depsgraph_get()
        return [tuple(vertex.co) for vertex in obj.evaluated_get(graph).data.vertices]

    def test_values_targets_geometry_json_roundtrip(self):
        obj = self.new_mesh('StockFishGeometry')
        engine.setup_mesh(self.scene, obj)
        self.settings.strength = 2.5
        self.settings.fov = 2.2
        self.settings.zoom = .8
        self.settings.ellipse_x = 1.7
        self.settings.ellipse_y = .7
        self.settings.near_distance = .2
        self.settings.thickness = False
        self.settings.center_mode = 'CURSOR'
        self.scene.cursor.location = (.5, .2, 0)
        engine.targets.reconcile(self.scene)
        bpy.context.view_layer.update()
        expected = self.vertices(obj)
        item = self.item()
        self.manager.save_data(camera_data=[item])
        path = Path(self.manager.save_file_path)
        decoded = json.loads(path.read_text(encoding='utf-8'))['camera_data'][0]
        self.assertEqual(stock.signature(item['fisheye_state']), stock.signature(decoded['fisheye_state']))
        self.settings.enabled = False
        self.settings.strength = -1
        self.settings.ellipse_x = .4
        self.settings.manual_targets.clear()
        self.scene.cursor.location = (20, 20, 20)
        presets = len(self.settings.presets)
        self.read(base.s._normalize_saved_item(decoded))
        self.assertEqual(len(self.settings.presets), presets)
        self.assertTrue(self.settings.enabled)
        self.assertFalse(self.scene.mpm_lattice_management_enabled)
        self.assertEqual(stock.signature(stock.capture(self.scene)), stock.signature(item['fisheye_state']))
        for actual, wanted in zip(self.vertices(obj), expected):
            self.near(actual, wanted)

    def test_duplicate_and_additional_overwrite_include_fisheye(self):
        self.assertEqual(bpy.ops.camera.save_position(), {'FINISHED'})
        first = copy.deepcopy(self.manager.saved_camera_data[0])
        self.assertEqual(bpy.ops.camera.save_position(), {'CANCELLED'})
        self.settings.strength = 2
        self.assertEqual(bpy.ops.camera.save_position(), {'FINISHED'})
        self.assertEqual(len(self.manager.saved_camera_data), 2)
        self.settings.strength = 3
        self.assertEqual(bpy.ops.camera.save_selected_stock_memo(), {'FINISHED'})
        self.assertEqual(len(self.manager.saved_camera_data), 2)
        self.assertEqual(self.manager.saved_camera_data[0], first)
        self.assertEqual(self.manager.saved_camera_data[1]['fisheye_state']['values']['strength'], 3)

    def test_off_lattice_and_legacy_switch_without_deleting_setup(self):
        obj = self.new_mesh('StockExclusive')
        engine.setup_mesh(self.scene, obj)
        fish_item = self.item()
        self.settings.enabled = False
        self.scene.mpm_lattice_management_enabled = True
        group = self.lattice_set([obj])
        lattice_item = self.item()
        lattice_mod = base.lm._find_managed_lattice_modifier(obj, group)
        self.read(fish_item)
        self.assertTrue(self.settings.enabled)
        self.assertFalse(lattice_mod.show_viewport)
        self.read(lattice_item)
        self.assertFalse(self.settings.enabled)
        self.assertTrue(lattice_mod.show_viewport)
        self.scene.mpm_lattice_management_enabled = False
        off = self.item()
        self.read(fish_item)
        self.read(off)
        self.assertFalse(self.settings.enabled)
        self.assertFalse(lattice_mod.show_viewport)
        self.assertTrue(self.settings.targets)
        self.assertTrue(group.lattice_obj)
        legacy = copy.deepcopy(lattice_item)
        legacy.pop('fisheye_state')
        self.read(fish_item)
        self.read(legacy)
        self.assertFalse(self.settings.enabled)
        self.assertTrue(lattice_mod.show_viewport)

    def test_both_manual_enables_are_rejected(self):
        self.scene.mpm_lattice_management_enabled = True
        self.assertFalse(self.scene.mpm_lattice_management_enabled)
        self.settings.enabled = False
        self.scene.mpm_lattice_management_enabled = True
        self.assertEqual(bpy.ops.gyogan.toggle_preview(), {'CANCELLED'})
        self.settings.enabled = True
        self.assertFalse(self.settings.enabled)
        self.assertTrue(self.scene.mpm_lattice_management_enabled)

    def test_bad_json_is_rejected_and_legacy_stays_legacy(self):
        item = self.item()
        for value in (float('nan'), 99, 'bad', None):
            bad = copy.deepcopy(item)
            bad['fisheye_state']['values']['strength'] = value
            with self.assertRaises(ValueError):
                base.s._normalize_saved_item(bad)
            with self.assertRaises(ValueError):
                base.s._normalize_saved_list([bad])
        bad = copy.deepcopy(item)
        bad['lattice_enabled'] = True
        with self.assertRaises(ValueError):
            base.s._normalize_saved_item(bad)
        item.pop('fisheye_state')
        self.assertNotIn('fisheye_state', base.s._normalize_saved_item(item))

    def test_missing_target_stops_before_camera_frame_or_lattice_change(self):
        obj = self.new_mesh('DeletedFishTarget')
        engine.setup_mesh(self.scene, obj)
        item = self.item()
        bpy.data.objects.remove(obj, do_unlink=True)
        self.settings.enabled = False
        self.scene.mpm_lattice_management_enabled = True
        self.camera.location.x = 77
        self.scene.frame_set(91)
        with patch.object(fish, 'warn') as warning:
            self.assertFalse(base.c._apply_saved_camera_data(self.scene, self.camera, self.manager, item))
        self.assertTrue(warning.called)
        self.assertEqual(self.camera.location.x, 77)
        self.assertEqual(self.scene.frame_current, 91)
        self.assertTrue(self.scene.mpm_lattice_management_enabled)

    def test_dolly_roundtrip_preserves_visible_camera_and_compensation(self):
        obj = self.new_mesh('DollyStockFish')
        engine.setup_mesh(self.scene, obj)
        self.start_dolly()
        item = self.item()
        location, lens = fish.camera_values(self.camera, self.scene)
        geometry = self.vertices(obj)
        self.scene.gyogan_dolly.ratio = .7
        bpy.context.view_layer.update()
        self.read(item)
        self.assertTrue(self.scene.gyogan_dolly.active)
        self.assertAlmostEqual(self.scene.gyogan_dolly.ratio, 2)
        self.near(self.camera.matrix_world.translation, location)
        self.assertAlmostEqual(self.camera.data.lens, lens)
        self.assertEqual(len(self.camera.animation_data.drivers), 3)
        for actual, wanted in zip(self.vertices(obj), geometry):
            self.near(actual, wanted)
        self.read(item)
        self.assertEqual(len(self.camera.animation_data.drivers), 3)
        self.near(self.camera.matrix_world.translation, location)

    def test_badge_uses_stock_not_live_preview(self):
        fish_item = self.item()
        self.settings.enabled = False
        off = self.item()
        lattice = {**off, 'lattice_enabled': True}
        for data, fish_active, lattice_active in ((fish_item, True, False), (off, False, False),
                                                 (lattice, False, True), (None, False, False)):
            layout = base.Layout()
            base.u._draw_saved_data_status_labels(layout, data)
            badges = [args for name, args in layout.operators if name == 'camera.additional_data_status_badge']
            self.assertEqual([b['text'] for b in badges[1:3]], ['魚眼', 'ラティス'])
            self.assertEqual([b['depress'] for b in badges[1:3]], [fish_active, lattice_active])

    def test_automatic_scope_and_object_center_are_restored(self):
        obj = self.new_mesh('AutoStockMesh')
        center = bpy.data.objects.new('StockCenter', None)
        self.scene.collection.objects.link(center)
        center.location = (.2, 0, 0)
        self.settings.center_object = center
        self.settings.effect_scope = 'CAMERA'
        engine.targets.reconcile(self.scene)
        item = self.item()
        self.settings.center_object = None
        self.settings.effect_scope = 'MANUAL'
        self.read(item)
        self.assertEqual(self.settings.center_object, center)
        self.assertEqual(self.settings.effect_scope, 'CAMERA')
        self.assertTrue(any(target.source == obj and target.active for target in self.settings.targets))

    def test_animated_dolly_and_foreign_driver_leave_scene_untouched(self):
        self.start_dolly()
        item = self.item()
        self.scene.gyogan_dolly.keyframe_insert('ratio', frame=1)
        self.camera.location.x = 6
        before = tuple(self.camera.location)
        with patch.object(fish, 'warn'):
            self.assertFalse(base.c._apply_saved_camera_data(self.scene, self.camera, self.manager, item))
        self.assertEqual(tuple(self.camera.location), before)
        self.assertTrue(self.scene.animation_data.action)
        self.scene.animation_data_clear()
        curve = self.camera.animation_data.drivers[0]
        curve.driver.expression = '7'
        with patch.object(fish, 'warn'):
            self.assertFalse(base.c._apply_saved_camera_data(self.scene, self.camera, self.manager, item))
        self.assertEqual(curve.driver.expression, '7')
        self.camera.animation_data.drivers.remove(curve)
        engine.dolly.restore(self.scene)
        self.camera.delta_location = (0, 0, 0)

    def test_startup_old_both_on_preserves_lattice_set(self):
        self.settings.enabled = False
        self.scene.mpm_lattice_management_enabled = True
        self.lattice_set([self.new_mesh('StartupLattice')])
        self.settings['enabled'] = True
        fish.reconcile_exclusion(self.scene)
        self.assertFalse(self.settings.enabled)
        self.assertTrue(self.scene.mpm_lattice_management_enabled)

    def test_armature_target_and_source_pose_survive_stock_recall(self):
        obj = self.new_mesh('StockCharacter')
        rig = bpy.data.objects.new('StockRig', bpy.data.armatures.new('StockArmature'))
        self.scene.collection.objects.link(rig)
        bpy.context.view_layer.objects.active = rig
        rig.select_set(True)
        bpy.ops.object.mode_set(mode='EDIT')
        bone = rig.data.edit_bones.new('Bone')
        bone.head, bone.tail = (0, 0, 0), (0, 1, 0)
        bpy.ops.object.mode_set(mode='OBJECT')
        obj.vertex_groups.new(name='Bone').add([0, 1, 2], 1, 'REPLACE')
        obj.modifiers.new('Armature', 'ARMATURE').object = rig
        self.settings.mode = 'ARMATURE'
        engine.setup_armature(self.scene, rig)
        engine.targets.reconcile(self.scene)
        item = self.item()
        self.settings.enabled = False
        self.settings.manual_targets.clear()
        self.settings.strength = -.5
        rig.pose.bones['Bone'].location.x = .1
        self.read(item)
        self.assertAlmostEqual(rig.pose.bones['Bone'].location.x, .1)
        self.assertEqual(self.settings.manual_targets[0].source, rig)
        target = next(target for target in self.settings.targets if target.source == rig)
        self.assertTrue(target.active)
        self.assertTrue(target.output_rig)


if __name__ == '__main__':
    suite = unittest.TestSuite(FisheyeStock(name) for name in FisheyeStock.__dict__ if name.startswith('test_'))
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)
