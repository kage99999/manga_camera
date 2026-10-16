"""Integrated fisheye/legacy coexistence in a disposable Blender process."""
import importlib.util
from pathlib import Path
import sys
import unittest
import shutil
import uuid
from unittest.mock import patch
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_regression as base

bpy, addon = base.bpy, base.addon
fish = addon.fisheye
engine = importlib.import_module(base.ROOT.name + '.gyogan_camera')


class FisheyeRegression(base.Regression):
    # Run just these integration tests, not a second copy of the base suite.
    @classmethod
    def setUpClass(cls):
        cls.profile = base.ROOT / 'dist' / ('fisheye-test-config-' + uuid.uuid4().hex)
        cls.profile.mkdir()
        original = bpy.utils.user_resource

        def resource(kind, *args, **kwargs):
            return str(cls.profile) if kind == 'CONFIG' else original(kind, *args, **kwargs)

        cls.resource_patch = patch.object(bpy.utils, 'user_resource', side_effect=resource)
        cls.resource_patch.start()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        try:
            super().tearDownClass()
        finally:
            cls.resource_patch.stop()
            assert cls.profile.resolve().parent == (base.ROOT / 'dist').resolve()
            shutil.rmtree(cls.profile)

    def setUp(self):
        super().setUp()
        if not fish.available():
            self.skipTest('Fisheye requires Blender 5.0+')
        self.camera.location = (0, 0, 10)
        self.camera.rotation_mode = 'XYZ'
        self.camera.rotation_euler = (0, 0, 0)
        self.camera.delta_location = (0, 0, 0)
        self.camera.data.lens = 50
        self.scene.cursor.location = (0, 0, 0)
        self.settings = self.scene.gyogan
        self.settings.enabled = True
        self.settings.effect_scope = 'MANUAL'
        self.settings.subdivision = 'NONE'
        self.settings.center_mode = 'OBJECT'
        self.settings.center_object = None
        self.settings.strength = 1
        self.settings.subdivision_pending = False
        bpy.context.view_layer.update()
        engine.cameras.synchronize(self.scene)

    def tearDown(self):
        if fish.available():
            self.scene = bpy.context.scene
            engine.dolly.restore(self.scene)
            while self.scene.gyogan.targets:
                engine.remove_target(self.scene, len(self.scene.gyogan.targets) - 1)
            self.scene.gyogan.manual_targets.clear()
            self.scene.gyogan.effect_scope = 'MANUAL'
            self.scene.mpm_dolly_props.enabled = False

    def near(self, a, b):
        self.assertLess((Vector(a) - Vector(b)).length, 1e-5)

    def start_dolly(self):
        engine.dolly.setup(self.scene)
        self.scene.gyogan_dolly.ratio = 2
        bpy.context.view_layer.update()

    def test_integration_panel_and_no_standalone_panel(self):
        self.assertTrue(fish.available())
        self.assertIsNone(bpy.types.Panel.bl_rna_get_subclass_py('GYOGAN_PT_main'))
        cls = addon.ui.VIEW3D_PT_custom_panel_fisheye
        self.assertEqual(cls.bl_category, 'カメラ')
        self.assertIn('DEFAULT_CLOSED', cls.bl_options)
        self.assertEqual(cls.bl_order, 45)
        layout = base.Layout()
        fish.draw(layout, bpy.context)
        self.assertIn(('gyogan.remove_all', {'icon': 'LOOP_BACK'}), layout.operators)

    def test_legacy_dolly_blocks_fisheye_without_partial_setup(self):
        self.scene.mpm_dolly_props.enabled = True
        with self.assertRaisesRegex(ValueError, '既存のドリーズーム'):
            engine.dolly.setup(self.scene)
        self.assertFalse(self.scene.gyogan_dolly.active)
        self.assertFalse(self.camera.data.animation_data and self.camera.data.animation_data.drivers)

    def test_fisheye_dolly_blocks_legacy_even_when_preview_off(self):
        self.start_dolly()
        self.scene.mpm_dolly_props.enabled = True
        self.assertFalse(self.scene.mpm_dolly_props.enabled)
        self.scene.gyogan_dolly.enabled = False
        self.scene.mpm_dolly_props.enabled = True
        self.assertFalse(self.scene.mpm_dolly_props.enabled)
        engine.dolly.restore(self.scene)
        self.scene.mpm_dolly_props.enabled = True
        self.assertTrue(self.scene.mpm_dolly_props.enabled)

    def test_saved_conflict_uses_fisheye_only(self):
        self.start_dolly()
        self.scene.mpm_dolly_props['enabled'] = True
        addon.dolly._depsgraph_handler(self.scene, None)
        self.assertFalse(self.scene.mpm_dolly_props.enabled)
        self.assertTrue(self.scene.gyogan_dolly.active)

    def test_unused_section_does_not_scan_scene_or_get_depsgraph(self):
        with patch.object(engine.targets, '_safe_to_prepare', side_effect=AssertionError('scan')):
            with patch.object(engine.targets, '_depsgraph', side_effect=AssertionError('evaluate')):
                engine.targets.reconcile(self.scene)
                # No dependency-graph access is necessary for an unused section.
                engine.targets._update(self.scene, None)
                engine.targets._frame(self.scene)
        self.assertFalse(self.settings.targets)

    def test_tick_checks_mode_once(self):
        obj = self.new_mesh('FisheyeTick')
        engine.setup_mesh(self.scene, obj)
        engine.targets._dirty.clear()
        engine.targets.request(self.scene)
        with patch.object(engine.targets, '_safe_to_prepare', return_value=True) as check:
            engine.targets._tick()
        self.assertEqual(check.call_count, 1)

    def test_non_camera_reference_does_not_break_fisheye_ui_or_updates(self):
        obj = self.new_mesh('InvalidCameraObject')
        engine.setup_mesh(self.scene, obj)
        self.scene.camera = obj
        # Exercise the interval before the normal camera synchronization handler.
        self.assertIn('カメラ以外', engine.cameras.warning(self.scene))
        fish.draw(base.Layout(), bpy.context)
        engine.targets.reconcile(self.scene, prepare=False)
        self.scene.camera = self.camera
        engine.cameras.synchronize(self.scene)

    def test_restored_bounds_reused_but_real_modifier_edit_rebuilds(self):
        obj = self.new_mesh('UndoBoundsSource')
        bevel = obj.modifiers.new('OriginalBevel', 'BEVEL')
        bevel.width = .02
        target = engine.setup_mesh(self.scene, obj)
        proxy = engine.targets.ensure_bounds_proxy(self.scene, target)
        identity = proxy.session_uid
        engine.targets._proxy_states.clear()
        self.assertEqual(engine.targets.ensure_bounds_proxy(self.scene, target).session_uid, identity)
        bevel.show_expanded = not bevel.show_expanded
        engine.targets._proxy_states.clear()
        self.assertEqual(engine.targets.ensure_bounds_proxy(self.scene, target).session_uid, identity)
        bevel.width = .05
        self.assertNotEqual(engine.targets.ensure_bounds_proxy(self.scene, target).session_uid, identity)

    def test_restored_bounds_with_missing_evaluator_is_repaired(self):
        obj = self.new_mesh('UndoMissingEvaluator')
        target = engine.setup_mesh(self.scene, obj)
        proxy = engine.targets.ensure_bounds_proxy(self.scene, target)
        identity = proxy.session_uid
        engine.targets._remove_bounds_evaluator(proxy)
        engine.targets._proxy_states.clear()
        rebuilt = engine.targets.ensure_bounds_proxy(self.scene, target)
        self.assertNotEqual(rebuilt.session_uid, identity)
        self.assertIsNotNone(rebuilt.get('gyogan_bounds_evaluator'))

    def test_stock_captures_driven_position_lens_without_frame_set(self):
        self.start_dolly()
        before = tuple(self.camera.location)
        item = self.item()
        self.near(item['position'], self.camera.matrix_world.translation)
        self.assertAlmostEqual(item['focal_length'], self.camera.data.lens)
        self.assertNotEqual(tuple(item['position']), before)
        self.assertEqual(tuple(self.camera.location), before)
        self.assertTrue(self.scene.gyogan_dolly.active)
        self.assertNotIn('fisheye_state', item)

    def test_stock_read_releases_driver_before_camera_restore(self):
        self.start_dolly()
        item = self.item()
        item['position'] = [2, 3, 14]
        item['focal_length'] = 34
        self.settings.strength = 2
        base.c._apply_saved_camera_data(self.scene, self.camera, self.manager, item)
        bpy.context.view_layer.update()
        self.assertFalse(self.scene.gyogan_dolly.active)
        self.near(self.camera.location, item['position'])
        self.near(self.camera.delta_location, (0, 0, 0))
        self.assertAlmostEqual(self.camera.data.lens, 34)
        self.assertEqual(self.settings.strength, 2)

    def test_all_object_snapshot_and_restore_release_dolly(self):
        self.start_dolly()
        lens = self.camera.data.lens
        stock = base.a._build_current_stock_item(self.scene)
        item = next(item for item in stock['objects'] if item['name'] == self.camera.name)
        self.near(item['location'], self.camera.matrix_world.translation)
        base.a._apply_stock_item(stock)
        bpy.context.view_layer.update()
        self.assertFalse(self.scene.gyogan_dolly.active)
        self.near(self.camera.location, item['location'])
        self.assertAlmostEqual(self.camera.data.lens, lens)

    def test_xmp_uses_visible_dolly_camera(self):
        self.start_dolly()
        location, rotation, lens, frame = base.x._camera_metadata_values(self.camera, self.scene)
        self.assertEqual(location, base.x._format_xyz_values(self.camera.matrix_world.translation))
        self.assertEqual(lens, round(self.camera.data.lens))

    def test_camera_release_preserves_edited_driver_and_warning(self):
        self.start_dolly()
        curve = self.camera.animation_data.drivers[0]
        curve.driver.expression = '7'
        fish.release_for_camera_write(self.camera)
        self.assertIn(curve, list(self.camera.animation_data.drivers))
        self.assertIn('変更を保持', self.scene.gyogan_dolly.status)
        self.camera.animation_data.drivers.remove(curve)
        self.camera.delta_location = (0, 0, 0)

    def test_lattice_toggle_does_not_claim_fisheye_modifiers(self):
        obj = self.new_mesh('LatticeAndFisheye')
        lattice = self.lattice_set([obj])
        self.settings.subdivision = 'SIMPLE'
        target = engine.setup_mesh(self.scene, obj)
        owned = [(record, engine.find_modifier(obj, record)) for record in target.modifiers]
        self.assertTrue(any(mod.type == 'SUBSURF' for record, mod in owned))
        for record, mod in owned:
            self.assertFalse(base.lm._is_lattice_manager_candidate_modifier(mod, self.scene))
        self.scene.mpm_lattice_management_enabled = False
        self.assertTrue(all(mod.show_viewport for record, mod in owned))
        self.settings.enabled = False
        self.assertTrue(all(not mod.show_viewport for record, mod in owned))
        self.scene.mpm_lattice_management_enabled = True
        self.assertTrue(all(not mod.show_viewport for record, mod in owned))
        self.settings.enabled = True
        engine.remove_target(self.scene, 0)
        self.assertTrue(base.lm._find_managed_lattice_modifier(obj, lattice))

    def test_registration_failure_unwinds_only_own_classes(self):
        addon.unregister()
        original = bpy.utils.register_class

        def fail(cls):
            if cls is engine.dolly.GYOGAN_OT_dolly_toggle:
                raise RuntimeError('deliberate registration failure')
            return original(cls)

        try:
            with patch.object(bpy.utils, 'register_class', side_effect=fail):
                with self.assertRaisesRegex(RuntimeError, 'deliberate'):
                    addon.register()
            self.assertFalse(hasattr(bpy.types.Scene, 'gyogan'))
            self.assertFalse(hasattr(bpy.types.Scene, 'gyogan_dolly'))
            self.assertFalse(hasattr(bpy.types.Scene, 'record_selected_objects'))
            self.assertFalse(engine._registered_classes)
            self.assertFalse(engine.dolly._registered_classes)
            self.assertFalse(bpy.app.timers.is_registered(engine.targets._tick))
        finally:
            addon.register()

    def test_foreign_registration_is_preserved_and_retry_works(self):
        addon.unregister()
        path = base.ROOT / 'gyogan_camera' / '__init__.py'
        spec = importlib.util.spec_from_file_location('_foreign_gyogan', path,
                        submodule_search_locations=[str(path.parent)])
        foreign = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = foreign
        spec.loader.exec_module(foreign)
        foreign.register()
        try:
            addon.register()
            self.assertFalse(fish.available())
            self.assertTrue(hasattr(bpy.types.Scene, 'gyogan'))
            self.assertTrue(bpy.app.timers.is_registered(foreign.targets._tick))
            self.assertFalse(bpy.app.timers.is_registered(engine.targets._tick))
            layout = base.Layout()
            fish.draw(layout, bpy.context)
            self.assertTrue(any('単体版' in label for label in layout.labels))
            addon.unregister()
            self.assertTrue(hasattr(bpy.types.Scene, 'gyogan'))
        finally:
            foreign.unregister()
            addon.register()
        self.assertTrue(fish.available())

    def test_unregister_reregister_has_single_handlers_and_timer(self):
        self.start_dolly()
        addon.unregister()
        self.near(self.camera.location, (0, 0, 10))
        self.assertFalse(bpy.app.timers.is_registered(engine.targets._tick))
        addon.register()
        self.assertTrue(bpy.app.timers.is_registered(engine.targets._tick))
        for module in (engine.targets, engine.cameras, engine.dolly, engine.presets):
            for name, fn in module.HANDLERS:
                self.assertEqual(getattr(bpy.app.handlers, name).count(fn), 1)


if __name__ == '__main__':
    suite = unittest.TestSuite(FisheyeRegression(name) for name in
                              FisheyeRegression.__dict__ if name.startswith('test_'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
