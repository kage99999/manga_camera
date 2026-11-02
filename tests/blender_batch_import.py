"""Batch import regression, only disposable factory-startup data."""
import copy
import math
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

import bpy

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_slot_regression as slots_test

base = slots_test.base
c, s, u = base.c, base.s, base.u
batch = c.batch_import


class BatchRegression(unittest.TestCase):
    setUpClass = classmethod(slots_test.SlotRegression.setUpClass.__func__)
    tearDownClass = classmethod(slots_test.SlotRegression.tearDownClass.__func__)

    def setUp(self):
        slots_test.SlotRegression.setUp(self)
        self.scene.camera = bpy.data.objects.get('Camera')
        self.camera = self.scene.camera
        self.scene.mpm_batch_camera_position = (0, -1, 0.5)
        self.scene.mpm_batch_camera_rotation = (math.pi / 2, 0, 0)
        self.images = self.folder / 'images'
        self.images.mkdir()
        self.manager.save_data(background_image_folder_path=str(self.images))

    def png(self, name, width=8, height=9):
        path = self.images / name
        path.parent.mkdir(parents=True, exist_ok=True)
        chunk = base.x._make_png_chunk
        path.write_bytes(base.x._PNG_SIGNATURE
                         + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
                         + chunk(b'IDAT', zlib.compress((b'\x00' + b'\xff\x00\x00' * width) * height))
                         + chunk(b'IEND', b''))
        return path

    def run_import(self):
        return batch.append_folder(self.scene, self.manager, str(self.images))

    def test_filename_filter_order_dimensions_and_frame(self):
        for name in ('000010.PNG', '000002.png', '000000.png', '999999.png', '12345.png',
                     '1234567.png', 'prefix123456.png', '１２３４５６.png', '123456.jpg', 'nested/000005.png'):
            self.png(name)
        (self.images / '000003.png').mkdir()
        self.assertEqual(self.run_import(), (4, 0))
        items = self.manager.saved_camera_data
        self.assertEqual([i['bg_image'] for i in items], ['000000.png', '000002.png', '000010.PNG', '999999.png'])
        self.assertEqual([i['frame_current'] for i in items], [0, 2, 10, 999999])
        for item in items:
            self.assertEqual((item['resolution_x'], item['resolution_y']), (8, 9))
            self.assertEqual(item['position'], [0, -1, 0.5])
            self.assertAlmostEqual(item['rotation'][0], math.pi / 2)
            self.assertEqual(item['bg_depth'], 'FRONT')
            self.assertTrue(item['bg_visible'])

    def test_custom_defaults_current_lens_and_no_attachments(self):
        self.png('123456.png', 11, 12)
        self.scene.mpm_batch_camera_position = (3, 4, 5)
        self.scene.mpm_batch_camera_rotation = (0.1, 0.2, 0.3)
        self.camera.data.lens = 72
        self.scene.saved_memo_text = 'do not capture'
        self.scene.record_selected_objects = True
        self.scene.record_view_layer_exclude_state = True
        self.scene.record_local_view_state = True
        self.run_import()
        item = self.manager.saved_camera_data[0]
        self.assertEqual(item['position'], [3, 4, 5])
        self.assertEqual(item['rotation'], list(self.scene.mpm_batch_camera_rotation))
        self.assertEqual(item['focal_length'], 72)
        self.assertEqual((item['resolution_x'], item['resolution_y']), (11, 12))
        self.assertEqual(item['memo'], '')
        self.assertEqual(item['selected_objects'], [])
        for flag in ('record_selected_objects', 'record_view_layer_exclude_state', 'record_local_view_state', 'lattice_enabled'):
            self.assertFalse(item[flag])
        self.assertFalse(item['fisheye_state']['enabled'])
        self.assertEqual(item['view_layer_exclude_state']['collections'], [])
        self.assertNotIn('local_view_state', item)

    def test_duplicate_name_skips_changed_camera_and_memo(self):
        self.png('123456.png')
        self.run_import()
        self.manager.saved_camera_data[0]['memo'] = 'keep me'
        self.manager.saved_camera_data[0]['bg_image'] = '123456.PNG'
        self.manager.save_data()
        original = copy.deepcopy(self.manager.saved_camera_data)
        self.scene.mpm_batch_camera_position = (9, 9, 9)
        self.png('123457.png')
        self.assertEqual(self.run_import(), (1, 1))
        self.assertEqual(self.manager.saved_camera_data[0], original[0])
        with patch.object(self.manager, 'save_data', wraps=self.manager.save_data) as save:
            self.assertEqual(self.run_import(), (0, 2))
            save.assert_not_called()

    def test_no_matches_or_bad_folder_does_not_save(self):
        before = Path(self.manager.save_file_path).read_bytes()
        self.assertEqual(self.run_import(), (0, 0))
        for path in ('', str(self.images / 'missing')):
            with self.assertRaises(ValueError):
                batch.append_folder(self.scene, self.manager, path)
        self.assertEqual(Path(self.manager.save_file_path).read_bytes(), before)

    def test_corrupt_image_aborts_without_partial_save_or_image_leak(self):
        self.png('000001.png')
        (self.images / '000002.png').write_bytes(b'broken')
        images = set(i.as_pointer() for i in bpy.data.images)
        before = Path(self.manager.save_file_path).read_bytes()
        with self.assertRaisesRegex(ValueError, '000002.png'):
            self.run_import()
        self.assertEqual(self.manager.saved_camera_data, [])
        self.assertEqual(Path(self.manager.save_file_path).read_bytes(), before)
        self.assertEqual(set(i.as_pointer() for i in bpy.data.images), images)

    def test_save_failure_keeps_memory_disk_and_folder(self):
        self.png('000001.png')
        self.manager.save_data(background_image_folder_path=str(self.folder))
        before = Path(self.manager.save_file_path).read_bytes()
        with patch.object(c, '_write_json_atomic', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.run_import()
        self.assertEqual(self.manager.saved_camera_data, [])
        self.assertEqual(self.manager.background_image_folder_path, str(self.folder))
        self.assertEqual(Path(self.manager.save_file_path).read_bytes(), before)

    def test_one_save_many_images_and_existing_datablock_preserved(self):
        first = self.png('000001.png')
        image = bpy.data.images.load(str(first))
        image.use_fake_user = True
        before = set(i.as_pointer() for i in bpy.data.images)
        for index in range(2, 51):
            self.png(f'{index:06d}.png')
        with patch.object(self.manager, 'save_data', wraps=self.manager.save_data) as save:
            self.assertEqual(self.run_import(), (50, 0))
            self.assertEqual(save.call_count, 1)
        self.assertEqual(set(i.as_pointer() for i in bpy.data.images), before)
        self.assertTrue(image.use_fake_user)

    def test_operator_preserves_scene_and_selects_first_added_without_apply(self):
        self.png('000001.png')
        self.png('000002.png')
        self.scene.saved_memo_text = 'unsaved memo'
        self.scene.frame_set(25, subframe=0.25)
        self.camera.location = (12, 13, 14)
        bpy.context.view_layer.objects.active = bpy.data.objects['Cube']
        bpy.ops.object.mode_set(mode='EDIT')
        before = (tuple(self.camera.location), self.scene.frame_current, self.scene.frame_subframe,
                  self.scene.render.resolution_x, self.scene.render.resolution_y,
                  bpy.context.mode, bpy.context.view_layer.objects.active, self.scene.saved_memo_text)
        try:
            with patch.object(c, '_apply_saved_camera_data', side_effect=AssertionError('must not apply')):
                self.assertEqual(bpy.ops.camera.batch_load_background_images(directory=str(self.images)), {'FINISHED'})
            after = (tuple(self.camera.location), self.scene.frame_current, self.scene.frame_subframe,
                     self.scene.render.resolution_x, self.scene.render.resolution_y,
                     bpy.context.mode, bpy.context.view_layer.objects.active, self.scene.saved_memo_text)
            self.assertEqual(before, after)
            self.assertEqual(self.scene.saved_camera_index, '0')
        finally:
            bpy.ops.object.mode_set(mode='OBJECT')

    def test_import_without_camera(self):
        self.scene.camera = None
        self.png('000001.png')
        self.assertEqual(self.run_import(), (1, 0))
        self.assertIsNone(self.scene.camera)
        self.assertEqual(self.manager.saved_camera_data[0]['focal_length'], 50)

    def test_changed_folder_must_contain_existing_stock_images(self):
        self.png('000001.png')
        self.run_import()
        new = self.images / 'new'
        new.mkdir()
        self.png('new/000002.png')
        before = Path(self.manager.save_file_path).read_bytes()
        with self.assertRaisesRegex(ValueError, '新しいスロット'):
            batch.append_folder(self.scene, self.manager, str(new))
        self.assertEqual(Path(self.manager.save_file_path).read_bytes(), before)
        self.png('new/000001.png')
        self.assertEqual(batch.append_folder(self.scene, self.manager, str(new)), (1, 1))
        self.assertEqual(self.manager.background_image_folder_path, str(new))

    def test_registered_ui_labels_and_settings_properties(self):
        layout = base.Layout()
        u._draw_record_read_controls(layout, bpy.context)
        labels = [kw.get('text') for name, kw in layout.operators]
        index = labels.index('下絵読込')
        self.assertEqual(labels[index:index + 3], ['下絵読込', '一括読込', '再読込'])
        self.scene.show_settings = True
        u._draw_misc_controls(layout, bpy.context)
        self.assertIn('一括読込の初期値', layout.labels)

    def test_no_active_slot_or_stale_target_rejected(self):
        self.png('000001.png')
        op = SimpleNamespace(_slot_id=('other', 'slot'), directory=str(self.images), report=lambda *args: None)
        self.assertEqual(batch.OBJECT_OT_batch_load_background_images.execute(op, bpy.context), {'CANCELLED'})
        self.lib.delete(self.lib.active_id)
        with self.assertRaises(Exception):
            self.run_import()

    def test_recall_and_reload_json(self):
        self.png('123456.png', 100, 120)
        self.run_import()
        self.manager.load_data()
        item = self.manager.saved_camera_data[0]
        c._apply_saved_camera_data(self.scene, self.camera, self.manager, item)
        self.assertEqual(tuple(self.camera.location), (0, -1, 0.5))
        self.assertEqual(self.scene.frame_current, 123456)
        self.assertEqual((self.scene.render.resolution_x, self.scene.render.resolution_y), (100, 120))
        self.assertEqual(Path(self.camera.data.background_images[0].image.filepath).name, '123456.png')

    def test_settings_persist_in_blend(self):
        self.scene.mpm_batch_camera_position = (8, -4, 3)
        self.scene.mpm_batch_camera_rotation = (0.5, 0.25, 0.75)
        path = str(self.folder / 'settings.blend')
        bpy.data.libraries.write(path, {self.scene})
        with bpy.data.libraries.load(path) as (source, target):
            target.scenes = [self.scene.name]
        loaded = target.scenes[0]
        try:
            self.assertEqual(tuple(loaded.mpm_batch_camera_position), (8, -4, 3))
            self.assertEqual(tuple(loaded.mpm_batch_camera_rotation), (0.5, 0.25, 0.75))
        finally:
            bpy.data.scenes.remove(loaded)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(BatchRegression)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
