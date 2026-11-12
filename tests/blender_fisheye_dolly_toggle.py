"""Dolly toggle behavior in a disposable Blender process."""
import sys
from pathlib import Path
import unittest
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_fisheye_regression import FisheyeRegression, bpy, engine


class DollyToggle(FisheyeRegression):
    def test_toggle_restores_camera_and_allows_restart(self):
        original_location = self.camera.location.copy()
        original_rotation = self.camera.rotation_euler.copy()
        original_lens = self.camera.data.lens
        self.assertEqual(bpy.ops.gyogan.dolly_toggle(), {'FINISHED'})
        settings = self.scene.gyogan_dolly
        self.assertTrue(settings.active)
        settings.ratio = 2
        bpy.context.view_layer.update()
        self.assertTrue(engine.dolly._find_curve(self.camera.data, 'lens', None))
        self.assertEqual(bpy.ops.gyogan.dolly_toggle(), {'FINISHED'})
        self.assertFalse(settings.active)
        self.assertIsNone(engine.dolly._find_curve(self.camera.data, 'lens', None))
        self.near(self.camera.location, original_location)
        self.near(self.camera.rotation_euler, original_rotation)
        self.assertAlmostEqual(self.camera.data.lens, original_lens)
        self.assertEqual(bpy.ops.gyogan.dolly_toggle(), {'FINISHED'})
        self.assertTrue(settings.active)

    def test_foreign_driver_and_ratio_keys_are_preserved(self):
        self.start_dolly()
        self.assertTrue(self.scene.gyogan_dolly.active)
        self.scene.gyogan_dolly.keyframe_insert(data_path='ratio', frame=1)
        self.assertTrue(self.scene.gyogan_dolly.active)
        action = self.scene.animation_data.action
        curve = engine.dolly._find_curve(self.camera.data, 'lens', None)
        curve.driver.expression = '42'
        self.assertTrue(self.scene.gyogan_dolly.active)
        self.assertEqual(engine.dolly.GYOGAN_OT_dolly_toggle.execute(
            SimpleNamespace(report=lambda *args: None), bpy.context), {'FINISHED'})
        self.assertFalse(self.scene.gyogan_dolly.active)
        self.assertEqual(engine.dolly._find_curve(self.camera.data, 'lens', None), curve)
        self.assertEqual(curve.driver.expression, '42')
        self.assertEqual(self.scene.animation_data.action, action)
        self.camera.data.animation_data.drivers.remove(curve)

    def test_legacy_paused_setup_resumes(self):
        self.start_dolly()
        self.scene.gyogan_dolly.enabled = False
        self.assertEqual(bpy.ops.gyogan.dolly_toggle(), {'FINISHED'})
        self.assertTrue(self.scene.gyogan_dolly.active)
        self.assertTrue(self.scene.gyogan_dolly.enabled)


if __name__ == '__main__':
    suite = unittest.TestSuite(DollyToggle(name) for name in DollyToggle.__dict__ if name.startswith('test_'))
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)
