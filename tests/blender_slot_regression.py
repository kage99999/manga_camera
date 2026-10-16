"""Slot integration tests. Only disposable factory-startup processes and files."""
import copy
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid
import zipfile

import bpy

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_regression as base

addon, c, s, u = base.addon, base.c, base.s, base.u
store_module, slots = c.slot_store, addon.slots


class SlotRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ROOT / 'dist' / ('slot-tests-' + uuid.uuid4().hex)
        cls.root.mkdir(parents=True)
        cls.original_resource = bpy.utils.user_resource
        cls.resource = patch.object(bpy.utils, 'user_resource', side_effect=lambda kind, *a, **kw:
                                    str(cls.root) if kind == 'CONFIG' else cls.original_resource(kind, *a, **kw))
        cls.resource.start()
        addon.register()
        assert c.get_camera_data_manager().slot_library.active

    @classmethod
    def tearDownClass(cls):
        addon.unregister()
        cls.resource.stop()
        assert cls.root.resolve().parent == (ROOT / 'dist').resolve()
        shutil.rmtree(cls.root)

    def setUp(self):
        self.folder = self.root / uuid.uuid4().hex
        self.folder.mkdir()
        self.manager = c.CameraDataManager()
        self.manager.save_file_path = str(self.folder / 'camera_positions.json')
        self.lib = store_module.SlotLibrary(self.manager, str(self.folder))
        self.manager.slot_library = self.lib
        self.lib.start()
        c._CAMERA_DATA_MANAGER = self.manager
        self.scene = bpy.context.scene
        self.camera = self.scene.camera
        self.scene.mpm_dolly_props.enabled = False
        self.scene.saved_memo_text = ''
        self.scene.record_selected_objects = False
        self.scene.record_view_layer_exclude_state = False
        slots.refresh_ui()

    def item(self, memo='saved'):
        result = c._build_current_camera_saved_item(self.scene, self.camera)
        result.update(memo=memo, frame_current=87, position=[4.0, 5.0, 6.0], focal_length=80.0)
        return result

    def write(self, path, data):
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        return str(path)

    def snapshot(self):
        return (self.lib.folder, self.lib.active_id, copy.deepcopy(self.manager.saved_camera_data),
                self.manager.save_file_path, self.manager.output_folder_path,
                self.manager.background_image_folder_path, copy.deepcopy(self.lib.catalog))

    def reopen(self):
        manager = c.CameraDataManager()
        lib = store_module.SlotLibrary(manager, str(self.folder))
        manager.slot_library = lib
        lib.start()
        return manager, lib

    def test_migration_leaves_legacy_bytes_untouched(self):
        folder = self.folder / 'migration'
        folder.mkdir()
        path = folder / 'camera_positions.json'
        self.write(path, {'camera_data': [self.item('legacy')]})
        old = path.read_bytes()
        manager = c.CameraDataManager()
        manager.save_file_path = str(path)
        lib = store_module.SlotLibrary(manager, str(folder))
        manager.slot_library = lib
        lib.start()
        self.assertEqual(path.read_bytes(), old)
        self.assertEqual(lib.active['name'], '既存データ')
        self.assertEqual(manager.saved_camera_data[0]['memo'], 'legacy')
        manager.save_data(camera_data=[])
        self.assertEqual(path.read_bytes(), old)

    def test_create_empty_slots_and_reopen_last_choice(self):
        self.lib.create('50話')
        first = self.lib.active_id
        self.manager.save_data(camera_data=[self.item()])
        self.lib.create('51話')
        self.assertEqual(self.manager.saved_camera_data, [])
        self.lib.select(first)
        manager, lib = self.reopen()
        self.assertEqual(lib.active_id, first)
        self.assertEqual(lib.active['name'], '50話')
        self.assertEqual(manager.saved_camera_data[0]['memo'], 'saved')

    def test_folders_are_per_slot_and_offline_paths_survive_record(self):
        self.lib.create('50話')
        first = self.lib.active_id
        bg, output = str(self.folder / 'offline-bg'), str(self.folder / 'offline-output')
        self.manager.save_data(background_image_folder_path=bg, output_folder_path=output)
        self.lib.create('51話')
        self.manager.save_data(background_image_folder_path=str(self.folder), output_folder_path=str(self.root))
        self.lib.select(first)
        self.manager.save_data(camera_data=[self.item()])
        manager, _ = self.reopen()
        self.assertEqual(manager.background_image_folder_path, bg)
        self.assertEqual(manager.output_folder_path, output)

    def test_enum_switch_never_applies_camera_frame_pose_or_view(self):
        self.lib.create('50話')
        first = self.lib.active_id
        self.manager.save_data(camera_data=[self.item('fifty')])
        self.lib.create('51話')
        self.manager.save_data(camera_data=[self.item('fifty-one')])
        self.scene.frame_set(12)
        self.camera.location = (8, 9, 10)
        self.camera.data.lens = 33
        slots.refresh_ui()
        with (patch.object(c, '_apply_saved_camera_data', side_effect=AssertionError('must not apply')),
              patch.object(c, '_apply_camera_view', side_effect=AssertionError('must not change view'))):
            bpy.context.window_manager.mpm_stock_slot = first
        self.assertEqual(self.lib.active_id, first)
        self.assertEqual(self.scene.frame_current, 12)
        self.assertEqual(tuple(self.camera.location), (8, 9, 10))
        self.assertEqual(self.camera.data.lens, 33)
        self.assertEqual(self.scene.saved_memo_text, 'fifty')
        self.assertEqual(bpy.ops.camera.reload_current_saved_stock(), {'FINISHED'})
        self.assertEqual(self.scene.frame_current, 87)
        self.assertEqual(tuple(self.camera.location), (4, 5, 6))

    def test_refresh_other_scene_indices_without_applying(self):
        other = bpy.data.scenes.new('SlotOtherScene')
        try:
            other.frame_set(212)
            self.manager.save_data(camera_data=[self.item()])
            with patch.object(c, '_apply_saved_camera_data', side_effect=AssertionError('must not apply')):
                slots.refresh_ui()
            self.assertEqual(other.frame_current, 212)
            self.assertEqual(other.saved_memo_text, 'saved')
        finally:
            bpy.data.scenes.remove(other)

    def test_record_and_additional_data_only_touch_active_slot(self):
        self.lib.create('50話')
        first_path = Path(self.manager.save_file_path)
        original = first_path.read_bytes()
        self.lib.create('51話')
        self.scene.saved_memo_text = 'new'
        self.assertEqual(bpy.ops.camera.save_selected_stock_memo(), {'FINISHED'})
        self.assertEqual(len(self.manager.saved_camera_data), 1)
        self.assertEqual(first_path.read_bytes(), original)

    def test_exports_do_not_change_autosave_or_selection(self):
        self.manager.save_data(camera_data=[self.item()])
        before = self.snapshot()
        target = self.folder / 'backup.json'
        self.manager.save_data(str(target))
        self.lib.export_all(str(self.folder / 'all.zip'))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['camera_data'][0]['memo'], 'saved')
        for path in (self.lib.settings_path, self.manager.save_file_path,
                     str(Path(self.lib.folder) / 'another.json')):
            with self.assertRaises(ValueError):
                self.manager.save_data(path)

    def test_old_json_import_creates_new_slot_and_keeps_source(self):
        path = self.folder / '50話.json'
        self.write(path, {'camera_data': [self.item('imported')]})
        source = path.read_bytes()
        old = Path(self.manager.save_file_path).read_bytes()
        self.assertEqual(bpy.ops.camera.load_stock_data(filepath=str(path)), {'FINISHED'})
        self.assertEqual(self.lib.active['name'], '50話')
        self.manager.load_data(str(path))
        self.assertEqual(self.lib.active['name'], '50話 (2)')
        self.manager.save_data(camera_data=[])
        self.assertEqual(path.read_bytes(), source)
        self.assertEqual((Path(self.lib.folder) / '既存データ.json').read_bytes(), old)

    def test_zip_roundtrip_appends_all_and_preserves_active(self):
        self.lib.create('50話')
        first = self.lib.active_id
        self.manager.save_data(camera_data=[self.item('50')])
        self.lib.create('51話')
        self.manager.save_data(camera_data=[self.item('51')])
        self.lib.select(first)
        archive = self.folder / 'all.zip'
        self.lib.export_all(str(archive))
        self.lib.import_all(str(archive))
        self.assertEqual(len(self.lib.entries), 6)
        self.assertEqual(self.lib.active['name'], '50話 (2)')
        self.assertEqual(self.manager.saved_camera_data[0]['memo'], '50')

    def test_folder_copy_keeps_old_library_and_restart_uses_new(self):
        self.lib.create('50話')
        old = self.lib.folder
        self.manager.save_data(camera_data=[self.item()])
        original = Path(self.manager.save_file_path).read_bytes()
        folder = self.folder / 'chosen'
        folder.mkdir()
        self.lib.set_folder(str(folder))
        self.assertEqual((Path(old) / '50話.json').read_bytes(), original)
        self.assertEqual((folder / '50話.json').read_bytes(), original)
        manager, lib = self.reopen()
        self.assertEqual(lib.folder, str(folder))
        self.assertEqual(manager.saved_camera_data[0]['memo'], 'saved')

    def test_existing_folder_switch_does_not_merge_or_overwrite(self):
        old = self.lib.folder
        destination = self.folder / 'other'
        destination.mkdir()
        self.lib.set_folder(str(destination))
        self.lib.create('other')
        self.lib.set_folder(old)
        self.assertEqual(len(self.lib.entries), 1)
        self.lib.set_folder(str(destination))
        self.assertEqual(len(self.lib.entries), 2)

    def test_copy_collision_rolls_back_and_preserves_settings(self):
        self.lib.create('50話')
        destination = self.folder / 'collision'
        destination.mkdir()
        occupied = destination / '50話.json'
        occupied.write_text('unrelated', encoding='utf-8')
        before = self.snapshot()
        settings = Path(self.lib.settings_path).read_bytes()
        with self.assertRaises(FileExistsError):
            self.lib.set_folder(str(destination))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(occupied.read_text(encoding='utf-8'), 'unrelated')
        self.assertEqual(set(p.name for p in destination.iterdir()), {'50話.json'})
        self.assertEqual(Path(self.lib.settings_path).read_bytes(), settings)

    def test_delete_last_slot_stays_empty_on_restart_and_create_recovers(self):
        self.manager.save_data(camera_data=[self.item()])
        ident = self.lib.active_id
        self.lib.delete(ident)
        self.assertEqual(self.manager.saved_camera_data, [])
        self.assertEqual(len(list((Path(self.lib.folder) / '.trash').glob('*.json'))), 1)
        _, lib = self.reopen()
        self.assertEqual(lib.entries, [])
        with self.assertRaises(ValueError):
            self.manager.save_data()
        self.lib.create('new')
        self.manager.save_data(camera_data=[self.item()])

    def test_rename_preserves_identity_bytes_and_selected_record_on_restart(self):
        self.lib.create('50話')
        ident = self.lib.active_id
        self.manager.save_data(camera_data=[self.item('first'), self.item('second')])
        path = Path(self.manager.save_file_path)
        data = json.loads(path.read_text(encoding='utf-8'))
        data['future_field'] = {'keep': 'unknown'}
        self.write(path, data)
        self.lib.reload()
        slots.refresh_ui()
        original, settings = path.read_bytes(), Path(self.lib.settings_path).read_bytes()
        c._set_saved_camera_index_safe(self.scene, self.manager, 1, apply_saved_data=False)
        self.scene.saved_memo_text = 'unsaved text'
        stock_list = self.manager.saved_camera_data
        frame, position = self.scene.frame_current, tuple(self.camera.location)
        with patch.object(c, '_apply_saved_camera_data', side_effect=AssertionError('rename apply')):
            self.assertEqual(bpy.ops.camera.rename_stock_slot(
                slot_id=ident, slot_folder=self.lib.folder, slot_name='50話 修正'), {'FINISHED'})
        self.assertFalse(path.exists())
        self.assertEqual(Path(self.manager.save_file_path).name, '50話 修正.json')
        self.assertEqual(Path(self.manager.save_file_path).read_bytes(), original)
        self.assertEqual(Path(self.lib.settings_path).read_bytes(), settings)
        self.assertIs(self.manager.saved_camera_data, stock_list)
        self.assertEqual(int(self.scene.saved_camera_index), 1)
        self.assertEqual(self.scene.saved_memo_text, 'unsaved text')
        self.assertEqual((self.scene.frame_current, tuple(self.camera.location)), (frame, position))
        _, reopened = self.reopen()
        self.assertEqual((reopened.active_id, reopened.active['name']), (ident, '50話 修正'))

    def test_rename_same_name_is_noop_and_case_only_works(self):
        self.lib.create('Episode')
        before = self.snapshot()
        with patch.object(store_module, '_write_json_atomic', side_effect=AssertionError('no-op write')):
            self.assertFalse(self.lib.rename(self.lib.active_id, 'Episode'))
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(self.lib.rename(self.lib.active_id, 'episode'))
        self.assertEqual(self.lib.active['name'], 'episode')
        self.assertIn('episode.json', os.listdir(self.lib.folder))
        self.assertNotIn('Episode.json', os.listdir(self.lib.folder))
        self.lib.check_write()

    def test_rename_rejects_collisions_invalid_names_and_changed_selection(self):
        first = self.lib.active_id
        self.lib.create('50話')
        self.lib.create('51話')
        path = Path(self.lib.folder) / 'foreign.json'
        path.write_text('unrelated', encoding='utf-8')
        before = self.snapshot()
        for name in ('', '50話', '../outside', 'foreign', 'NUL'):
            with self.assertRaises(ValueError, msg=name):
                self.lib.rename(self.lib.active_id, name)
            self.assertEqual(self.snapshot(), before)
        with self.assertRaises(ValueError):
            self.lib.rename(first, 'wrong selection')
        self.assertEqual(path.read_text(encoding='utf-8'), 'unrelated')

    def test_rename_catalog_failure_keeps_original_and_removes_unpublished_copy(self):
        self.lib.create('Episode')
        before, path = self.snapshot(), Path(self.manager.save_file_path)
        original = path.read_bytes()
        for name in ('Changed', 'episode'):
            with patch.object(store_module, '_write_json_atomic', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    self.lib.rename(self.lib.active_id, name)
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(path.read_bytes(), original)
            self.assertNotIn(name + '.json', os.listdir(self.lib.folder))
        _, lib = self.reopen()
        self.assertEqual(lib.active['name'], 'Episode')

    def test_rename_copy_failure_keeps_catalog_and_source(self):
        before = self.snapshot()
        path, original = Path(self.manager.save_file_path), Path(self.manager.save_file_path).read_bytes()
        with patch.object(store_module.shutil, 'copyfileobj', side_effect=OSError('copy denied')):
            with self.assertRaises(OSError):
                self.lib.rename(self.lib.active_id, 'failed')
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse((Path(self.lib.folder) / 'failed.json').exists())

    def test_inline_name_commit_preserves_selection_and_unsaved_memo(self):
        self.lib.create('50話')
        self.manager.save_data(camera_data=[self.item('first'), self.item('second')])
        slots.refresh_ui()
        c._set_saved_camera_index_safe(self.scene, self.manager, 1, apply_saved_data=False)
        self.scene.saved_memo_text = 'not committed'
        wm = bpy.context.window_manager
        self.assertEqual(wm.mpm_stock_slot_name, '50話')
        with patch.object(c, '_apply_saved_camera_data', side_effect=AssertionError('rename apply')):
            wm.mpm_stock_slot_name = '第50話 修正'
        self.assertEqual(wm.mpm_stock_slot_name, '第50話 修正')
        self.assertEqual(Path(self.manager.save_file_path).name, '第50話 修正.json')
        self.assertEqual(int(self.scene.saved_camera_index), 1)
        self.assertEqual(self.scene.saved_memo_text, 'not committed')

    def test_inline_name_failed_commit_keeps_original(self):
        self.lib.create('50話')
        before = self.snapshot()
        original = Path(self.manager.save_file_path).read_bytes()
        with patch.object(self.manager, 'report_error') as report:
            for value in ('', '既存データ', '../bad'):
                bpy.context.window_manager.mpm_stock_slot_name = value
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(bpy.context.window_manager.mpm_stock_slot_name, '50話')
            with patch.object(self.lib, 'rename', side_effect=OSError('disk full')):
                bpy.context.window_manager.mpm_stock_slot_name = 'unwritten'
            self.assertEqual(report.call_count, 4)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(Path(self.manager.save_file_path).read_bytes(), original)

    def test_inline_name_follows_selected_slot_and_handles_empty_library(self):
        first = self.lib.active_id
        self.lib.create('50話')
        bpy.context.window_manager.mpm_stock_slot = first
        self.assertEqual(bpy.context.window_manager.mpm_stock_slot_name, '既存データ')
        self.lib.delete(first)
        self.lib.delete(self.lib.active_id)
        self.assertEqual(bpy.context.window_manager.mpm_stock_slot_name, '')
        bpy.context.window_manager.mpm_stock_slot_name = 'cannot create'
        self.assertEqual(self.lib.entries, [])

    def test_invalid_names_and_duplicate_creation_leave_files_intact(self):
        before = self.snapshot()
        for name in ('', '../bad', 'x/y', 'x\\y', 'CON', 'name.', 'x:y', 'x' * 81, '既存データ'):
            with self.assertRaises(ValueError, msg=name):
                self.lib.create(name)
            self.assertEqual(self.snapshot(), before)

    def test_settings_failure_rolls_back_add_catalog_and_files(self):
        before = self.snapshot()
        catalog_bytes = (Path(self.lib.folder) / store_module.CATALOG).read_bytes()
        with patch.object(self.lib, '_remember', side_effect=OSError('settings denied')):
            with self.assertRaises(OSError):
                self.lib.create('50話')
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((Path(self.lib.folder) / store_module.CATALOG).read_bytes(), catalog_bytes)
        self.assertFalse((Path(self.lib.folder) / '50話.json').exists())

    def test_failed_switch_keeps_current_data_and_persisted_choice(self):
        first = self.lib.active_id
        self.lib.create('bad')
        bad_id, bad_path = self.lib.active_id, Path(self.manager.save_file_path)
        self.lib.select(first)
        before = self.snapshot()
        settings = Path(self.lib.settings_path).read_bytes()
        bad_path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.lib.select(bad_id)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(Path(self.lib.settings_path).read_bytes(), settings)

    def test_failed_delete_keeps_original_and_visible_slot(self):
        self.manager.save_data(camera_data=[self.item()])
        before, path = self.snapshot(), Path(self.manager.save_file_path)
        original = path.read_bytes()
        with patch.object(self.lib, '_remember', side_effect=OSError('settings denied')):
            with self.assertRaises(OSError):
                self.lib.delete(self.lib.active_id)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(len(list((Path(self.lib.folder) / '.trash').glob('*.json'))), 1)

    def test_rollback_failure_keeps_catalog_referenced_new_files(self):
        writer = store_module._write_json_atomic
        catalog_path = os.path.join(self.lib.folder, store_module.CATALOG)
        calls = 0

        def write(path, data):
            nonlocal calls
            if path == catalog_path:
                calls += 1
                if calls == 2:
                    raise OSError('rollback denied')
            return writer(path, data)

        with (patch.object(store_module, '_write_json_atomic', side_effect=write),
              patch.object(self.lib, '_remember', side_effect=OSError('settings denied'))):
            with self.assertRaises(OSError):
                self.lib.create('50話')
        catalog = store_module._read_catalog(self.lib.folder)
        self.assertTrue(all((Path(self.lib.folder) / e['file']).is_file() for e in catalog['slots']))

    def test_external_file_change_blocks_stale_save_until_reload(self):
        path = Path(self.manager.save_file_path)
        self.write(path, {'camera_data': [self.item('external')]})
        original = path.read_bytes()
        with self.assertRaises(ValueError):
            self.manager.save_data(camera_data=[])
        self.assertEqual(path.read_bytes(), original)
        self.manager.load_data()
        self.assertEqual(self.manager.saved_camera_data[0]['memo'], 'external')
        self.manager.save_data()

    def test_external_catalog_change_blocks_stale_create_and_record(self):
        path = Path(self.lib.folder) / store_module.CATALOG
        catalog = json.loads(path.read_text(encoding='utf-8'))
        catalog['revision'] = 'external'
        self.write(path, catalog)
        with self.assertRaises(ValueError):
            self.lib.create('bad')
        with self.assertRaises(ValueError):
            self.manager.save_data()
        self.lib.select(self.lib.active_id)
        self.manager.save_data()

    def test_corrupt_startup_preserves_json_and_blocks_save(self):
        path = Path(self.manager.save_file_path)
        path.write_text('{broken', encoding='utf-8')
        manager = c.CameraDataManager()
        with patch.object(store_module.bpy.utils, 'user_resource', return_value=str(self.folder)):
            lib = store_module.initialize(manager)
        self.assertTrue(lib.error)
        with self.assertRaises(ValueError):
            manager.save_data()
        self.assertEqual(path.read_text(encoding='utf-8'), '{broken')

    def test_unavailable_folder_is_not_recreated_or_fallback_overwritten(self):
        self.write(Path(self.lib.settings_path), {
            'version': 1, 'folder': str(self.folder / 'disconnected'), 'active_slot': self.lib.active_id})
        manager = c.CameraDataManager()
        with patch.object(store_module.bpy.utils, 'user_resource', return_value=str(self.folder)):
            lib = store_module.initialize(manager)
        self.assertTrue(lib.error)
        self.assertFalse((self.folder / 'disconnected').exists())
        with self.assertRaises(ValueError):
            manager.save_data()

    def test_invalid_bulk_import_is_all_or_nothing(self):
        archive = self.folder / 'invalid.zip'
        catalog = copy.deepcopy(self.lib.catalog)
        catalog['slots'].append({'id': uuid.uuid4().hex, 'name': 'bad', 'file': 'bad.json'})
        before = self.snapshot()
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr(store_module.CATALOG, json.dumps(catalog))
            z.writestr(self.lib.active['file'], json.dumps({'camera_data': []}))
            z.writestr('bad.json', json.dumps({'all_object_data': []}))
        with self.assertRaises(ValueError):
            self.lib.import_all(str(archive))
        self.assertEqual(self.snapshot(), before)
        with zipfile.ZipFile(archive, 'a') as z:
            z.writestr('../outside.json', '{}')
        with self.assertRaises(ValueError):
            self.lib.import_all(str(archive))
        self.assertFalse((self.folder.parent / 'outside.json').exists())

    def test_ui_has_selector_and_settings_only_folder_picker(self):
        record, settings = base.Layout(), base.Layout()
        self.scene.show_settings = True
        u._draw_record_read_controls(record, bpy.context)
        u._draw_misc_controls(settings, bpy.context)
        record_ops = [name for name, _ in record.operators]
        setting_ops = [name for name, _ in settings.operators]
        self.assertIn('camera.create_stock_slot', record_ops)
        self.assertIn('camera.delete_stock_slot', record_ops)
        self.assertNotIn('camera.set_stock_slot_folder', record_ops)
        for name in ('camera.set_stock_slot_folder', 'camera.export_all_stock_slots', 'camera.import_all_stock_slots'):
            self.assertIn(name, setting_ops)

    def test_slot_name_and_filename_share_compact_label_rows(self):
        class Layout(base.Layout):
            def __init__(self):
                super().__init__()
                self.children, self.props, self.menus = [], [], []

            def split(self, **kwargs):
                child = self.row()
                child.split_options = kwargs
                return child

            def row(self, **kwargs):
                child = Layout()
                self.children.append(child)
                return child

            def prop(self, data, name, **kwargs):
                super().prop(data, name, **kwargs)
                self.props.append(name)

            def prop_menu_enum(self, data, name, **kwargs):
                super().prop(data, name, **kwargs)
                self.menus.append(name)

            def walk(self):
                yield self
                for child in self.children:
                    yield from child.walk()

        self.manager.save_data(camera_data=[self.item()])
        slots.refresh_ui()
        layout = Layout()
        u._draw_record_read_controls(layout, bpy.context)
        for label, prop in (
                ('スロット', 'mpm_stock_slot_name'),
                ('下絵ﾌｧｲﾙ名', 'saved_camera_index')):
            rows = [child for child in layout.children
                    if any(label in nested.labels for nested in child.children)]
            self.assertEqual(len(rows), 1, label)
            self.assertNotIn('split_options', vars(rows[0]))
            self.assertTrue(any(prop in child.props for child in rows[0].walk()))
            label_cell = next(child for child in rows[0].children if label in child.labels)
            self.assertEqual(label_cell.alignment, 'LEFT')
        field = next(child for child in layout.walk() if 'mpm_stock_slot' in child.menus)
        self.assertEqual(field.emboss, 'NONE_OR_STATUS')
        name = next(child for child in field.walk() if 'mpm_stock_slot_name' in child.props)
        self.assertEqual(name.emboss, 'PULLDOWN_MENU')
        self.assertNotIn('下絵のファイル名', layout.labels)

    def test_slot_ui_draw_does_not_read_or_write_library_files(self):
        with (patch.object(store_module, '_read_catalog', side_effect=AssertionError('draw disk read')),
              patch.object(store_module, '_write_json_atomic', side_effect=AssertionError('draw disk write'))):
            slots.draw_selector(base.Layout(), bpy.context)
            slots.draw_folder(base.Layout())

    def test_managed_save_failure_keeps_memory_disk_and_stamp(self):
        self.manager.save_data(camera_data=[self.item('original')])
        before = self.snapshot()
        path, stamp = Path(self.manager.save_file_path), self.lib.file_stamp
        original = path.read_bytes()
        with patch.object(c, '_write_json_atomic', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.manager.save_data(camera_data=[self.item('new')])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.lib.file_stamp, stamp)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SlotRegression)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
