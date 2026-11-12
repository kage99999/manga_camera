"""Curve fisheye regression tests in a disposable Blender process."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_fisheye_stock import FisheyeStock, bpy, engine, base, stock
import unittest


class CurveTests(FisheyeStock):
    def curve(self, name='FishCurve'):
        data = bpy.data.curves.new(name, 'CURVE')
        data.dimensions = '3D'
        data.bevel_depth = .1
        spline = data.splines.new('BEZIER')
        spline.bezier_points.add(2)
        for point, co in zip(spline.bezier_points, ((-4, -1, 0), (0, 2, 0), (4, -1, 0))):
            point.co = co
            point.handle_left_type = point.handle_right_type = 'AUTO'
        obj = bpy.data.objects.new(name, data)
        self.scene.collection.objects.link(obj)
        bpy.context.view_layer.update()
        return obj

    def vertices(self, obj):
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        try:
            return [tuple(vertex.co) for vertex in mesh.vertices]
        finally:
            evaluated.to_mesh_clear()

    def test_manual_toggle_and_remove(self):
        obj = self.curve()
        for selected in bpy.context.selected_objects:
            selected.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        before = self.vertices(obj)
        points = [tuple(p.co) for p in obj.data.splines[0].bezier_points]
        self.settings.strength = 2.5
        self.assertEqual(bpy.ops.gyogan.add_targets(), {'FINISHED'})
        bpy.context.view_layer.update()
        changed = self.vertices(obj)
        self.assertNotEqual(before, changed)
        self.assertEqual(obj.type, 'CURVE')
        self.assertEqual(points, [tuple(p.co) for p in obj.data.splines[0].bezier_points])
        obj.data.splines[0].bezier_points[1].co.y = 3
        obj.data.update_tag()
        bpy.context.view_layer.update()
        self.assertNotEqual(changed, self.vertices(obj))
        obj.data.splines[0].bezier_points[1].co.y = 2
        obj.data.update_tag()
        bpy.context.view_layer.update()
        self.settings.enabled = False
        bpy.context.view_layer.update()
        for a, b in zip(self.vertices(obj), before):
            self.near(a, b)
        self.assertEqual(bpy.ops.gyogan.remove_all(), {'FINISHED'})
        self.assertFalse(obj.modifiers)

    def test_preset_and_stock_roundtrip(self):
        obj = self.curve()
        engine.setup_curve(self.scene, obj)
        self.settings.strength = 2
        engine.targets.reconcile(self.scene)
        bpy.context.view_layer.update()
        expected = self.vertices(obj)
        preset = engine.presets.create(self.scene, name='Curve')
        item = self.item()
        normalized = base.s._normalize_saved_item(item)
        self.assertEqual(normalized['fisheye_state']['entries'][0]['kind'], 'CURVE')
        self.settings.manual_targets.clear()
        self.settings.strength = .5
        self.read(normalized)
        self.assertEqual(self.settings.manual_targets[0].source, obj)
        for a, b in zip(self.vertices(obj), expected):
            self.near(a, b)
        self.settings.strength = .5
        engine.presets.activate(self.scene, preset.uuid)
        self.assertAlmostEqual(self.settings.strength, 2)
        self.assertEqual(self.settings.manual_targets[0].kind, 'CURVE')

    def test_auto_camera_scope(self):
        obj = self.curve()
        self.settings.effect_scope = 'CAMERA'
        engine.targets.reconcile(self.scene)
        target = next(t for t in self.settings.targets if t.source == obj)
        self.assertEqual(target.kind, 'CURVE')
        self.assertTrue(target.active)
        self.assertIsNotNone(target.bounds_object)

    def test_missing_and_wrong_type_stop_stock_restore(self):
        obj = self.curve()
        engine.setup_curve(self.scene, obj)
        item = self.item()
        self.settings.strength = .5
        obj.name = 'renamed_curve'
        with self.assertRaises(ValueError):
            stock.prepare(self.scene, self.camera, item)
        self.assertAlmostEqual(self.settings.strength, .5)
        wrong = self.new_mesh('FishCurve')
        with self.assertRaises(ValueError):
            stock.prepare(self.scene, self.camera, item)
        self.assertEqual(wrong.type, 'MESH')
        self.assertAlmostEqual(self.settings.strength, .5)


if __name__ == '__main__':
    suite = unittest.TestSuite(CurveTests(name) for name in CurveTests.__dict__ if name.startswith('test_'))
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)
