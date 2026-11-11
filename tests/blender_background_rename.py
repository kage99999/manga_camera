"""Isolated stock/image rename tests."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_slot_regression as base

c = base.c
rename = c.background_rename.rename_stock


class RenameTests(unittest.TestCase):
    setUpClass = classmethod(base.SlotRegression.setUpClass.__func__)
    tearDownClass = classmethod(base.SlotRegression.tearDownClass.__func__)

    def setUp(self):
        base.SlotRegression.setUp(self)
        self.manager.save_data(background_image_folder_path=str(self.folder))
        item = c._build_current_camera_saved_item(self.scene, self.camera)
        item['bg_image'] = 'old.png'
        other = copy.deepcopy(item)
        other['memo'] = 'other'
        self.manager.save_data(camera_data=[item, other])
        self.old = self.folder / 'old.png'
        self.old.write_bytes(b'image data')

    def test_file_and_all_references(self):
        self.assertEqual(rename(self.manager, 0, 'new.png', file_and_all=True), (True, ''))
        self.assertEqual((self.folder / 'new.png').read_bytes(), b'image data')
        self.assertFalse(self.old.exists())
        self.assertEqual([x['bg_image'] for x in self.manager.saved_camera_data], ['new.png', 'new.png'])
        self.manager.load_data()
        self.assertEqual(self.manager.saved_camera_data[1]['bg_image'], 'new.png')

    def test_stock_only_and_missing(self):
        rename(self.manager, 0, 'label.png', file_and_all=False)
        self.assertTrue(self.old.exists())
        self.assertEqual([x['bg_image'] for x in self.manager.saved_camera_data], ['label.png', 'old.png'])
        rename(self.manager, 0, 'another.png', file_and_all=True)
        self.assertTrue(self.old.exists())
        self.assertEqual(self.manager.saved_camera_data[0]['bg_image'], 'another.png')

    def test_existing_destination_and_invalid_names_are_safe(self):
        existing = self.folder / 'taken.png'
        existing.write_bytes(b'keep')
        before = copy.deepcopy(self.manager.saved_camera_data)
        for name in ('taken.png', '../bad.png', 'new.jpg', 'CON.png'):
            with self.assertRaises((OSError, ValueError)):
                rename(self.manager, 0, name, file_and_all=True)
        self.assertEqual(existing.read_bytes(), b'keep')
        self.assertEqual(self.old.read_bytes(), b'image data')
        self.assertEqual(before, self.manager.saved_camera_data)

    def test_failed_save_does_not_lose_original(self):
        before = Path(self.manager.save_file_path).read_bytes()
        with patch.object(self.manager, 'save_data', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                rename(self.manager, 0, 'new.png', file_and_all=True)
        self.assertEqual(self.old.read_bytes(), b'image data')
        self.assertEqual(Path(self.manager.save_file_path).read_bytes(), before)
        self.assertFalse((self.folder / 'new.png').exists())

    def test_failed_delete_retains_both_files_and_updated_json(self):
        with patch.object(c.background_rename.os, 'unlink', side_effect=PermissionError('locked')):
            changed, warning = rename(self.manager, 0, 'new.png', file_and_all=True)
        self.assertTrue(changed)
        self.assertTrue(warning)
        self.assertTrue(self.old.exists())
        self.assertTrue((self.folder / 'new.png').exists())
        self.assertEqual(self.manager.saved_camera_data[0]['bg_image'], 'new.png')


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RenameTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
