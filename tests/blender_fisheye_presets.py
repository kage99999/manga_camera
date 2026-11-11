"""Snapshot preset behavior in a disposable Blender process."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_regression as base
import importlib
engine = importlib.import_module(base.ROOT.name + '.gyogan_camera')
p = engine.presets
bpy = base.bpy


class PresetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base.Regression.setUpClass()

    @classmethod
    def tearDownClass(cls):
        base.Regression.tearDownClass()

    def setUp(self):
        self.helper = base.Regression()
        self.helper.setUp()
        self.scene = self.helper.scene
        self.s = self.scene.gyogan
        with p._batch(self.scene):
            self.s.presets.clear()
            self.s.active_preset_id = ''
            self.s.preset_index = -1
            self.s.manual_targets.clear()
            self.s.effect_scope = 'MANUAL'
            self.s.mode = 'MESH'
            self.s.enabled = False
        self.scene.mpm_lattice_management_enabled = False

    def test_empty_ensure_and_save(self):
        self.assertIsNone(p.ensure(self.scene))
        p._save_pre()
        self.assertEqual(len(self.s.presets), 0)
        self.assertEqual(self.s.preset_choice, 'NONE')
        self.assertEqual(self.s.preset_index, -1)

    def test_register_keeps_targets_and_name(self):
        obj = bpy.data.objects['Cube']
        entry = self.s.manual_targets.add()
        entry.source, entry.kind = obj, 'MESH'
        self.s.strength = .25
        saved = p.create(self.scene, name='対象A')
        self.assertEqual(saved.name, '対象A')
        self.assertEqual(saved.entries[0].source, obj)
        self.assertEqual(self.s.preset_choice, saved.uuid)

    def test_snapshot_not_overwritten_and_both_selection_routes(self):
        self.s.strength = .25
        first = p.create(self.scene, name='A')
        first_id, first_json = first.uuid, first.settings_json
        self.s.strength = .75
        second = p.create(self.scene, name='B')
        second_id = second.uuid
        self.s.strength = 1.5
        p._save_pre()
        self.assertEqual(p._find(self.s, first_id).settings_json, first_json)
        self.s.preset_choice = first_id
        self.assertAlmostEqual(self.s.strength, .25)
        self.s.preset_index = 1
        self.assertAlmostEqual(self.s.strength, .75)
        self.assertEqual(self.s.preset_choice, second_id)
        p.menu_set(self.s, 0)
        self.assertEqual(self.s.preset_choice, 'NONE')
        self.assertAlmostEqual(self.s.strength, .75)

    def test_delete_last_without_replacement(self):
        p.create(self.scene, name='A')
        self.s.strength = 1.2
        p.delete(self.scene)
        self.assertEqual(len(self.s.presets), 0)
        self.assertEqual(self.s.preset_choice, 'NONE')
        self.assertAlmostEqual(self.s.strength, 1.2, places=5)

    def test_duplicate_uses_stored_not_live(self):
        self.s.strength = .25
        p.create(self.scene, name='A')
        self.s.strength = .9
        result = p.create(self.scene, duplicate=True)
        self.assertAlmostEqual(p._read(result)['values']['strength'], .25)

    def test_failure_rolls_back_live_state_and_selection(self):
        self.s.strength = .25
        a = p.create(self.scene, name='A').uuid
        self.s.strength = .75
        b = p.create(self.scene, name='B').uuid
        with patch.object(p, '_refresh', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                p.activate(self.scene, a)
        self.assertEqual(self.s.active_preset_id, b)
        self.assertAlmostEqual(self.s.strength, .75)

    def test_reselect_and_rename(self):
        self.s.strength = .25
        saved = p.create(self.scene, name='A')
        saved.name = '名前変更'
        self.assertEqual(p.menu_items(self.s, bpy.context)[1][1], '名前変更')
        self.s.strength = .75
        self.s.preset_index = 0
        self.assertAlmostEqual(self.s.strength, .25)
        self.s.strength = .75
        self.s.preset_choice = saved.uuid
        self.assertAlmostEqual(self.s.strength, .25)

    def test_z_save_reload_keeps_snapshot_and_working_copy(self):
        self.s.strength = .25
        saved_id = p.create(self.scene, name='保存テスト').uuid
        self.s.strength = .75
        path = str(base.Regression.folder / 'preset-persistence.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        settings = bpy.context.scene.gyogan
        self.assertAlmostEqual(settings.strength, .75)
        self.assertAlmostEqual(p._read(p._find(settings, saved_id))['values']['strength'], .25)
        settings.preset_choice = saved_id
        self.assertAlmostEqual(settings.strength, .25)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PresetTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
