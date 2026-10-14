"""Run with Blender --background --factory-startup --python this_file.

All scenes and files belong to this disposable test process.
"""
import copy
import faulthandler
import importlib
import os
from pathlib import Path
import struct
import sys
import shutil
import uuid
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

import bpy

if os.environ.get('MPM_TEST_STACK_DUMP'):
    faulthandler.enable()
    faulthandler.dump_traceback_later(30)
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
addon = importlib.import_module(ROOT.name)
c, a, u, lm, d, x = (addon.core, addon.all_object_data, addon.ui,
                     addon.lattice_manager, addon.dolly, addon.xmp_rendering)
s = importlib.import_module(ROOT.name + '.storage')
b = importlib.import_module(ROOT.name + '.background')
lo = importlib.import_module(ROOT.name + '.lattice_ops')
lu = importlib.import_module(ROOT.name + '.lattice_ui_draw')
t = importlib.import_module(ROOT.name + '.transforms')


class Layout:
    """Check RNA properties/operators/list IDs without needing a visible window."""
    def __init__(self):
        self.labels = []
        self.operators = []

    def __getattr__(self, key):
        return lambda *args, **kwargs: self

    def panel(self, *args, **kwargs):
        return self, self

    def label(self, **kwargs):
        self.labels.append(kwargs.get('text', ''))

    def prop(self, data, name, **kwargs):
        assert hasattr(data, name), name

    def operator(self, name, **kwargs):
        owner, op = name.split('.')
        getattr(getattr(bpy.ops, owner), op).get_rna_type()
        self.operators.append((name, kwargs))
        return SimpleNamespace()

    def template_list(self, name, list_id, data, prop, active, index, **kwargs):
        assert bpy.types.UIList.bl_rna_get_subclass_py(name) is not None, name
        assert hasattr(data, prop) and hasattr(active, index)
        assert kwargs.get('rows', 0) <= kwargs.get('maxrows', 100)


class Regression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = ROOT / 'dist' / ('manga-camera-tests-' + uuid.uuid4().hex)
        cls.folder.mkdir()
        c._CAMERA_DATA_MANAGER = c.CameraDataManager()
        c._CAMERA_DATA_MANAGER.save_file_path = str(cls.folder / 'camera.json')
        a._ALL_OBJECT_DATA_MANAGER = a.AllObjectDataManager()
        a._ALL_OBJECT_DATA_MANAGER.save_file_path = str(cls.folder / 'all.json')
        addon.register()

    @classmethod
    def tearDownClass(cls):
        addon.unregister()
        assert cls.folder.resolve().parent == (ROOT / 'dist').resolve()
        assert cls.folder.name.startswith('manga-camera-tests-')
        shutil.rmtree(cls.folder)

    def setUp(self):
        self.scene = bpy.context.scene
        self.camera = c._ensure_scene_camera(self.scene)
        self.scene.record_selected_objects = False
        self.scene.mpm_pose_keyframe_priority = False
        self.scene.record_view_layer_exclude_state = False
        self.scene.saved_memo_text = ''
        self.scene.mpm_dolly_props.enabled = False
        self.manager = c.get_camera_data_manager()
        self.manager.save_file_path = str(self.folder / 'camera.json')
        self.manager.saved_camera_data = []
        self.manager.background_image_folder_path = str(self.folder)
        self.manager.output_folder_path = str(self.folder)
        c.rebuild_enum_cache(self.manager)

    def item(self, memo=''):
        item = c._build_current_camera_saved_item(self.scene, self.camera)
        item['memo'] = memo
        return item

    def new_mesh(self, name):
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        obj = bpy.data.objects.new(name, mesh)
        self.scene.collection.objects.link(obj)
        return obj

    def lattice_set(self, objects):
        bpy.ops.camera.lattice_add_set()
        lattice_set = lm._get_active_lattice_set(self.scene)
        for obj in objects:
            lattice_set.objects.add().object_name = obj.name
            modifier = obj.modifiers.new(lm._modifier_name_for_set(lattice_set), 'LATTICE')
            modifier.object = lattice_set.lattice_obj
            lm._tag_lattice_modifier_for_set(modifier, lattice_set)
        bpy.context.view_layer.update()
        return lattice_set

    def test_lattice_preflight_keeps_all_data(self):
        obj = self.new_mesh('ShapeKeyMesh')
        obj.shape_key_add(name='Basis')
        lattice_set = self.lattice_set([obj])
        name, uid = lattice_set.lattice_obj.name, lattice_set.set_uid
        self.assertEqual(bpy.ops.camera.lattice_apply_current_set_and_remove(), {'CANCELLED'})
        self.assertIsNotNone(bpy.data.objects.get(name))
        self.assertTrue(any(item.set_uid == uid for item in self.scene.mpm_lattice_sets))
        self.assertIsNotNone(obj.modifiers[0].object)

    def test_lattice_runtime_partial_failure_keeps_set(self):
        first, second = self.new_mesh('ApplyGood'), self.new_mesh('ApplyFail')
        lattice_set = self.lattice_set([first, second])
        name, uid = lattice_set.lattice_obj.name, lattice_set.set_uid
        original = lo._apply_single_lattice_modifier_preserving_subdivision

        def apply(context, obj, group, modifier):
            if obj == second:
                return False, 'simulated failure'
            return original(context, obj, group, modifier)

        with patch.object(lo, '_apply_single_lattice_modifier_preserving_subdivision', apply):
            self.assertEqual(bpy.ops.camera.lattice_apply_current_set_and_remove(), {'FINISHED'})
        self.assertIsNotNone(bpy.data.objects.get(name))
        self.assertTrue(any(item.set_uid == uid for item in self.scene.mpm_lattice_sets))
        self.assertEqual(len(first.modifiers), 0)
        self.assertIsNotNone(second.modifiers[0].object)
        self.assertEqual(bpy.ops.camera.lattice_apply_current_set_and_remove(), {'FINISHED'})
        self.assertIsNone(bpy.data.objects.get(name))

    def test_atomic_json_and_failed_record_keep_committed_data(self):
        item = self.item('original')
        self.manager.save_data(camera_data=[item])
        c.rebuild_enum_cache(self.manager)
        before = copy.deepcopy(self.manager.saved_camera_data)
        path = Path(self.manager.save_file_path)
        disk_before = path.read_bytes()
        with patch.object(s.os, 'replace', side_effect=OSError('simulated replace failure')):
            with self.assertRaises(OSError):
                c._save_current_item_as_stock(self.scene, self.manager, self.item('different'))
            with self.assertRaises(OSError):
                self.manager.save_data(str(self.folder / 'other.json'))
        self.assertEqual(before, self.manager.saved_camera_data)
        self.assertEqual(self.manager.save_file_path, str(path))
        self.assertEqual(path.read_bytes(), disk_before)
        self.assertEqual(list(self.folder.glob('*.tmp')), [])

    def test_save_operator_reports_failure_without_success(self):
        reports = []
        operator = SimpleNamespace(filepath=str(self.folder / 'failed.json'),
                                   report=lambda level, text: reports.append((level, text)))
        with patch.object(c, '_write_json_atomic', side_effect=OSError('simulated disk error')):
            result = c.OBJECT_OT_save_stock_data.execute(operator, bpy.context)
        self.assertEqual(result, {'CANCELLED'})
        self.assertEqual([level for level, text in reports], [{'ERROR'}])

    def test_all_object_save_failure_keeps_slots_and_path(self):
        manager = a.get_all_object_data_manager()
        original = copy.deepcopy(manager.stocks)
        old_path = manager.save_file_path
        with patch.object(a, '_write_json_atomic', side_effect=OSError('simulated failure')):
            with self.assertRaises(OSError):
                manager.save_data(str(self.folder / 'failed-all.json'), stocks=[a._build_current_stock_item(self.scene)])
        self.assertEqual(manager.stocks, original)
        self.assertEqual(manager.save_file_path, old_path)

    def make_png(self, path):
        data = (x._PNG_SIGNATURE
                + x._make_png_chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
                + x._make_png_chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00'))
                + x._make_png_chunk(b'IEND', b''))
        path.write_bytes(data)
        return data

    def test_xmp_atomic_failure_and_repeat_success(self):
        path = self.folder / 'xmp.png'
        original = self.make_png(path)
        with patch.object(s.os, 'replace', side_effect=OSError('simulated replace failure')):
            with self.assertRaises(OSError):
                x._write_xmp_to_png(str(path), b'<first/>')
        self.assertEqual(path.read_bytes(), original)
        with patch.object(s.os, 'fsync', side_effect=OSError('simulated flush failure')):
            with self.assertRaises(OSError):
                x._write_xmp_to_png(str(path), b'<first/>')
        self.assertEqual(path.read_bytes(), original)
        x._write_xmp_to_png(str(path), b'<first/>')
        x._write_xmp_to_png(str(path), b'<second/>')
        chunks = x._split_png_chunks(path.read_bytes())[1]
        self.assertEqual(sum(kind == b'iTXt' for kind, _, _ in chunks), 1)
        self.assertIn(b'<second/>', path.read_bytes())
        self.assertEqual(list(self.folder.glob('*.tmp')), [])

    def test_xmp_uses_shared_setting_and_removes_independent_ui(self):
        target = self.folder / 'shared-output'
        target.mkdir(exist_ok=True)
        self.scene['mpm_xmp_target_folder'] = str(self.folder / 'legacy-output')
        self.addCleanup(self.scene.pop, 'mpm_xmp_target_folder')
        self.assertFalse(hasattr(bpy.types.Scene, 'mpm_xmp_target_folder'))
        operator = SimpleNamespace(directory=str(target), report=lambda *_: None)
        self.assertEqual(c.OBJECT_OT_set_output_folder.execute(operator, bpy.context), {'FINISHED'})
        self.manager.load_data()
        self.assertEqual(Path(x._get_xmp_output_folder()), target)
        layout = Layout()
        x.draw_xmp_rendering_controls(layout, bpy.context)
        self.assertIn(str(target), layout.labels)
        self.assertEqual([name for name, _ in layout.operators], ['camera.render_with_xmp'])
        for name in ('OBJECT_OT_set_xmp_target_folder', 'OBJECT_OT_clear_xmp_target_folder'):
            self.assertIsNone(bpy.types.Operator.bl_rna_get_subclass_py(name))
        self.manager.output_folder_path = str(self.folder)
        self.assertEqual(Path(x._get_xmp_output_folder()), self.folder)

    def test_xmp_shared_output_pipeline_filters_and_embeds_pngs(self):
        target = self.folder / 'xmp-pipeline'
        nested = target / 'nested'
        nested.mkdir(parents=True)
        self.manager.output_folder_path = str(target)
        self.scene.frame_current = 123456
        legacy = self.folder / 'legacy-123456.png'
        self.scene['mpm_xmp_target_folder'] = str(self.folder)
        self.addCleanup(self.scene.pop, 'mpm_xmp_target_folder')
        old = target / 'old-123456.png'
        other = target / 'frame-654321.png'
        jpg = target / 'frame-123456.jpg'
        unchanged = {path: self.make_png(path) for path in (legacy, old, other)}
        unchanged[jpg] = b'not a PNG'
        jpg.write_bytes(unchanged[jpg])
        os.utime(old, (time.time() - 60, time.time() - 60))
        written = [target / 'frame-123456.png', nested / 'frame-123456.png']
        original_chunks = {}

        def render(*args, **kwargs):
            self.assertEqual(args, ('EXEC_DEFAULT',))
            self.assertEqual(kwargs, {})
            for path in written:
                original_chunks[path] = x._split_png_chunks(self.make_png(path))[1]
            return {'FINISHED'}

        reports = []
        operator = SimpleNamespace(report=lambda level, text: reports.append((level, text)))
        with patch.object(bpy.ops, 'render') as render_ops:
            renderer = render_ops.render
            renderer.side_effect = render
            result = x.OBJECT_OT_render_with_xmp._run_render_and_xmp(operator, bpy.context)
        self.assertEqual(result, {'FINISHED'})
        renderer.assert_called_once()
        self.assertEqual(reports[0][0], {'INFO'})
        for path in written:
            data = path.read_bytes()
            self.assertIn(b'<manga:FrameNumber>123456</manga:FrameNumber>', data)
            chunks = x._split_png_chunks(data)[1]
            self.assertEqual([chunk for chunk in chunks if chunk[0] != b'iTXt'], original_chunks[path])
        for path, data in unchanged.items():
            self.assertEqual(path.read_bytes(), data)

    def test_xmp_empty_shared_folder_does_not_search_legacy_or_cwd(self):
        self.manager.output_folder_path = ''
        self.scene['mpm_xmp_target_folder'] = str(self.folder)
        self.addCleanup(self.scene.pop, 'mpm_xmp_target_folder')
        reports = []
        operator = SimpleNamespace(report=lambda level, text: reports.append((level, text)))
        with patch.object(bpy.ops, 'render') as render_ops:
            renderer = render_ops.render
            renderer.return_value = {'FINISHED'}
            with patch.object(x, '_iter_recent_frame_pngs') as search:
                result = x.OBJECT_OT_render_with_xmp._run_render_and_xmp(operator, bpy.context)
        self.assertEqual(result, {'FINISHED'})
        renderer.assert_called_once()
        search.assert_not_called()
        self.assertEqual(reports[0][0], {'WARNING'})

    def test_xmp_no_target_warns_without_writing(self):
        for target in (self.folder / 'empty-output', self.folder / 'missing-output'):
            if target.name == 'empty-output':
                target.mkdir()
            self.manager.output_folder_path = str(target)
            reports = []
            operator = SimpleNamespace(report=lambda level, text: reports.append((level, text)))
            with patch.object(bpy.ops, 'render') as render_ops:
                render_ops.render.return_value = {'FINISHED'}
                with patch.object(x, '_write_xmp_to_png') as write:
                    result = x.OBJECT_OT_render_with_xmp._run_render_and_xmp(operator, bpy.context)
            self.assertEqual(result, {'FINISHED'})
            write.assert_not_called()
            self.assertEqual(reports, [({'WARNING'}, x._MISSING_TARGET_MESSAGE)])

    def test_zero_values_and_legacy_defaults(self):
        item = s._normalize_saved_item(dict(self.item(), bg_opacity=0, frame_current=0))
        self.assertEqual(item['bg_opacity'], 0)
        self.assertEqual(item['frame_current'], 0)
        self.manager.save_data(camera_data=[item])
        self.manager.load_data()
        self.camera.data.background_images.clear()
        c._apply_saved_camera_data(self.scene, self.camera, self.manager, self.manager.saved_camera_data[0])
        self.assertEqual(self.scene.frame_current, 0)
        self.assertEqual(self.camera.data.background_images[0].alpha, 0)
        self.assertTrue(s._normalize_saved_item({'bg_image': 'old.png'})['bg_visible'])
        self.assertEqual(s._normalize_saved_item({'rotation': [1, 2]})['rotation'], [1, 2, 0])
        self.assertEqual(s._ensure_manager_saved_data_normalized(SimpleNamespace(saved_camera_data=None)), [])

    def test_stock_background_keeps_current_display_and_changes_image(self):
        path = self.folder / 'stock-display-000099.png'
        self.make_png(path)
        bg = b._get_or_create_background_slot(self.camera)
        extra = self.camera.data.background_images.new()
        extra.show_background_image = False
        self.addCleanup(self.camera.data.background_images.remove, extra)
        for master, slot, opacity, depth in (
            (True, False, 0.23, 'BACK'),
            (False, True, 0.0, 'FRONT'),
            (True, True, 0.37, 'FRONT'),
        ):
            self.camera.data.show_background_images = master
            bg.show_background_image = slot
            bg.alpha = opacity
            bg.display_depth = depth
            self.camera.data.passepartout_alpha = 0.31
            self.camera.data.show_passepartout = False
            before = b._capture_background_display_state(self.camera)
            item = dict(self.item(), bg_image=path.name, bg_visible=not (master and slot),
                        bg_opacity=1, bg_depth='FRONT' if depth == 'BACK' else 'BACK')
            c._apply_saved_camera_data(self.scene, self.camera, self.manager, item)
            self.assertEqual(b._capture_background_display_state(self.camera), before)
            self.assertEqual(Path(bpy.path.abspath(bg.image.filepath)), path)
            self.assertFalse(extra.show_background_image)

    def test_stock_missing_image_and_legacy_fields_keep_display(self):
        bg = b._get_or_create_background_slot(self.camera)
        self.camera.data.mpm_bg_visible = False
        bg.alpha = 0.19
        bg.display_depth = 'FRONT'
        before = b._capture_background_display_state(self.camera)
        for image_name in ('missing-image.png', 'No File'):
            legacy = s._normalize_saved_item({'bg_image': image_name, 'frame_current': 25})
            c._apply_saved_camera_data(self.scene, self.camera, self.manager, legacy)
            self.assertEqual(b._capture_background_display_state(self.camera), before)
            self.assertIsNone(bg.image)

    def test_stock_display_is_captured_before_frame_evaluation(self):
        bg = b._get_or_create_background_slot(self.camera)
        self.addCleanup(self.camera.data.animation_data_clear)
        bg.alpha = 0.2
        bg.keyframe_insert(data_path='alpha', frame=1)
        bg.alpha = 0.8
        bg.keyframe_insert(data_path='alpha', frame=100)
        self.scene.frame_set(1)
        before = b._capture_background_display_state(self.camera)
        c._apply_saved_camera_data(self.scene, self.camera, self.manager,
                                  dict(self.item(), frame_current=100, bg_opacity=1))
        self.assertEqual(self.scene.frame_current, 100)
        self.assertEqual(b._capture_background_display_state(self.camera), before)

    def test_stock_frame_roundtrip_selection_navigation_reload_and_recall(self):
        self.scene.frame_set(0)
        first = self.item()
        self.scene.frame_set(511571)
        second = self.item()
        second['bg_image'] = 'frame-000099.png'
        self.manager.save_data(camera_data=[first, second])
        self.manager.load_data()
        c.rebuild_enum_cache(self.manager)
        ranges = (self.scene.frame_start, self.scene.frame_end,
                  self.scene.frame_preview_start, self.scene.frame_preview_end)
        self.scene.saved_camera_index = '0'
        self.assertEqual(self.scene.frame_current, 0)
        self.assertEqual(bpy.ops.camera.next_saved_stock(), {'FINISHED'})
        self.assertEqual(self.scene.frame_current, 511571)
        self.scene.frame_set(10)
        self.assertEqual(bpy.ops.camera.reload_current_saved_stock(), {'FINISHED'})
        self.assertEqual(self.scene.frame_current, 511571)
        self.assertEqual(bpy.ops.camera.prev_saved_stock(), {'FINISHED'})
        self.assertEqual(self.scene.frame_current, 0)
        self.assertEqual(bpy.ops.camera.recall_position(), {'FINISHED'})
        self.assertEqual(self.scene.frame_current, 511571)
        self.assertEqual(ranges, (self.scene.frame_start, self.scene.frame_end,
                                 self.scene.frame_preview_start, self.scene.frame_preview_end))

    def test_rotations_camera_selected_and_all_object_roundtrip(self):
        obj = self.new_mesh('RotationTest')
        for selected in list(bpy.context.selected_objects):
            selected.select_set(False)
        obj.select_set(True)
        self.scene.record_selected_objects = True
        for mode in ('XYZ', 'ZYX', 'QUATERNION', 'AXIS_ANGLE'):
            with self.subTest(mode=mode):
                for target in (obj, self.camera):
                    target.rotation_mode = mode
                    target.rotation_euler = (0.2, 0.3, 0.4)
                    if mode == 'QUATERNION':
                        target.rotation_quaternion = (0.70710678, 0, 0, 0.70710678)
                    elif mode == 'AXIS_ANGLE':
                        target.rotation_axis_angle = (1.2, 0, 1, 0)
                camera_item = s._normalize_saved_item(self.item())
                all_item = a._normalize_stock_item(a._build_current_stock_item(self.scene))
                expected = t.rotation_signature(t.capture_rotation(obj))
                for target in (obj, self.camera):
                    target.rotation_mode = 'XYZ'
                    target.rotation_euler = (0, 0, 0)
                c._apply_saved_camera_data(self.scene, self.camera, self.manager, camera_item)
                self.assertEqual(t.rotation_signature(t.capture_rotation(obj)), expected)
                self.assertEqual(t.rotation_signature(t.capture_rotation(self.camera)), expected)
                obj.rotation_mode = 'XYZ'
                obj.rotation_euler = (0, 0, 0)
                a._apply_stock_item(all_item)
                self.assertEqual(t.rotation_signature(t.capture_rotation(obj)), expected)
        t.apply_rotation(obj, {'rotation': [0.1, 0.2, 0.3]})
        self.assertAlmostEqual(obj.rotation_euler.x, 0.1)

    def test_memo_duplicates_and_append(self):
        self.manager.save_data(camera_data=[self.item('A'), self.item('B')])
        c.rebuild_enum_cache(self.manager)
        self.assertEqual(c._save_current_item_as_stock(self.scene, self.manager, self.item('B')), 'skip')
        self.assertEqual(len(self.manager.saved_camera_data), 2)
        items, added, skipped = s._append_unique_saved_items([self.item('A')], [self.item('B'), self.item('B')])
        self.assertEqual((added, skipped), (1, 1))
        self.assertEqual([item['memo'] for item in items], ['A', 'B'])

    def test_additional_memo_overwrite_does_not_increase_stocks(self):
        self.manager.save_data(camera_data=[self.item('before')])
        c.rebuild_enum_cache(self.manager)
        c._set_saved_camera_index_safe(self.scene, self.manager, 0)
        self.scene.saved_memo_text = 'after'
        self.assertEqual(bpy.ops.camera.save_selected_stock_memo(), {'FINISHED'})
        self.assertEqual(len(self.manager.saved_camera_data), 1)
        self.assertEqual(self.manager.saved_camera_data[0]['memo'], 'after')
        self.assertEqual(bpy.ops.camera.save_selected_stock_memo(), {'CANCELLED'})

    def test_additional_first_record_and_empty_ui(self):
        self.scene.saved_memo_text = 'first memo'
        self.scene.record_selected_objects = True
        layout = Layout()
        with patch.object(layout, 'prop', wraps=layout.prop) as prop:
            u._draw_saved_memo_controls(layout, bpy.context)
        self.assertNotEqual(layout.__dict__.get('enabled', True), False)
        self.assertIn('camera.save_selected_stock_memo', [op for op, _ in layout.operators])
        self.assertTrue({'saved_memo_text', 'record_selected_objects', 'record_view_layer_exclude_state'}
                        <= {call.args[1] for call in prop.call_args_list})
        self.assertEqual(self.scene.saved_memo_text, 'first memo')
        self.scene.record_selected_objects = False
        frame = self.scene.frame_current
        self.assertEqual(bpy.ops.camera.save_selected_stock_memo(), {'FINISHED'})
        self.assertEqual(len(self.manager.saved_camera_data), 1)
        self.assertEqual(self.manager.saved_camera_data[0]['memo'], 'first memo')
        self.assertEqual(self.scene.frame_current, frame)
        self.assertEqual(bpy.ops.camera.save_selected_stock_memo(), {'CANCELLED'})

    def test_panel_reads_do_not_normalize_all_stocks_or_clear_memo(self):
        self.manager.save_data(camera_data=[self.item('saved')])
        c.rebuild_enum_cache(self.manager)
        self.scene.saved_memo_text = 'being edited'
        with patch.object(s, '_normalize_saved_list', side_effect=AssertionError('normalization during draw')):
            layout = Layout()
            u._draw_record_read_controls(layout, bpy.context)
            u._draw_saved_memo_controls(layout, bpy.context)
        self.assertEqual(self.scene.saved_memo_text, 'being edited')
        manager = a.get_all_object_data_manager()
        manager.save_data(stocks=[a._build_current_stock_item(self.scene)])
        with patch.object(a, '_normalize_stock_list', side_effect=AssertionError('normalization during draw')):
            a.draw_all_object_data_controls(Layout(), bpy.context)

    def test_removed_keyframe_options_are_absent_from_ui_rna_and_json(self):
        self.assertNotIn('mpm_record_keyframes', bpy.types.Scene.bl_rna.properties)
        self.assertNotIn('mpm_move_to_recorded_frame', bpy.types.Scene.bl_rna.properties)
        layout = Layout()
        with patch.object(layout, 'prop', wraps=layout.prop) as prop:
            u._draw_record_read_controls(layout, bpy.context)
            u._draw_saved_memo_controls(layout, bpy.context)
        names = {call.args[1] for call in prop.call_args_list}
        self.assertFalse({'mpm_record_keyframes', 'mpm_move_to_recorded_frame'} & names)
        self.assertNotIn('mpm_record_keyframes', self.item())
        self.assertNotIn('mpm_move_to_recorded_frame', self.item())

    def test_legacy_keyframe_options_do_not_disable_frame_recall(self):
        self.scene['mpm_record_keyframes'] = True
        self.scene['mpm_move_to_recorded_frame'] = False
        try:
            self.scene.frame_set(42)
            saved = self.item()
            self.scene.frame_set(77)
            c._apply_saved_camera_data(self.scene, self.camera, self.manager, saved)
            self.assertEqual(self.scene.frame_current, 42)
        finally:
            del self.scene['mpm_record_keyframes']
            del self.scene['mpm_move_to_recorded_frame']

    def test_pose_priority_defaults_ui_and_no_json_fields(self):
        prop = bpy.types.Scene.bl_rna.properties['mpm_pose_keyframe_priority']
        self.assertFalse(prop.default)
        self.assertEqual(prop.name, 'ポーズをキーフレーム優先')
        self.assertFalse(self.scene.mpm_pose_keyframe_priority)
        self.assertNotIn('mpm_pose_keyframe_priority', self.item())
        layout = Layout()
        with patch.object(layout, 'prop', wraps=layout.prop) as calls:
            u._draw_record_read_controls(layout, bpy.context)
        self.assertIn('mpm_pose_keyframe_priority', [call.args[1] for call in calls.call_args_list])
        self.assertIn('0 / 0', layout.labels)

    def test_pose_priority_persists_in_blend_per_scene(self):
        self.scene.mpm_pose_keyframe_priority = True
        other = bpy.data.scenes.new('IndependentPosePriority')
        self.assertFalse(other.mpm_pose_keyframe_priority)
        path = self.folder / 'pose-priority.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
        with bpy.data.libraries.load(str(path)) as (_, target):
            target.scenes = [self.scene.name]
        loaded = target.scenes[0]
        self.assertTrue(loaded.mpm_pose_keyframe_priority)
        bpy.data.scenes.remove(loaded)
        bpy.data.scenes.remove(other)

    def test_first_stock_list_is_reused(self):
        wm = bpy.context.window_manager
        u._sync_recorded_object_display_items(wm, ['Camera', 'Cube'], 0)
        item = wm.mpm_recorded_object_items_v130[0]
        item.delete_candidate = True
        pointer = item.as_pointer()
        u._sync_recorded_object_display_items(wm, ['Camera', 'Cube'], 0)
        self.assertEqual(wm.mpm_recorded_object_items_v130[0].as_pointer(), pointer)
        self.assertTrue(wm.mpm_recorded_object_items_v130[0].delete_candidate)

    def test_lattice_draw_is_read_only_and_toggles_work(self):
        obj = self.new_mesh('ToggleTest')
        lattice_set = self.lattice_set([obj])
        with patch.object(lu, 'apply_lattice_set_activation_state', side_effect=AssertionError('write during draw')) as sync:
            with patch.object(lu, 'apply_lattice_management_enabled', side_effect=AssertionError('write during draw')) as apply:
                lu.draw_lattice_manager_panel(Layout(), bpy.context)
                sync.assert_not_called()
                apply.assert_not_called()
        self.scene.mpm_lattice_management_enabled = False
        self.assertFalse(obj.modifiers[0].show_viewport)
        self.scene.mpm_lattice_management_enabled = True
        self.assertTrue(obj.modifiers[0].show_viewport)

    def test_image_list_cache_detects_changes_and_skips_save(self):
        folder = self.folder / 'frames'
        folder.mkdir(exist_ok=True)
        self.make_png(folder / 'shot000002.png')
        self.make_png(folder / 'shot000010.png')
        paths = b._iter_folder_image_paths(str(folder))
        with patch.object(b, '_natural_sort_key', side_effect=AssertionError('unnecessary sort')):
            self.assertEqual(b._iter_folder_image_paths(str(folder)), paths)
        self.make_png(folder / 'shot000003.png')
        self.assertEqual([Path(path).name for path in b._iter_folder_image_paths(str(folder))],
                         ['shot000002.png', 'shot000003.png', 'shot000010.png'])
        self.manager.background_image_folder_path = str(folder)
        with patch.object(self.manager, 'save_data', side_effect=AssertionError('unnecessary save')):
            b._set_background_image_from_path(self.scene, self.camera, self.manager,
                str(folder / 'shot000002.png'), lambda: None, update_resolution=True, update_frame=True)
            self.scene.saved_memo_text = 'clear this'
            self.assertEqual(bpy.ops.camera.next_folder_image(), {'FINISHED'})
        self.assertEqual(self.scene.frame_current, 3)
        self.assertEqual(self.scene.saved_memo_text, '')
        self.assertEqual(self.scene.render.resolution_x, 4)  # Blender's minimum resolution.

    def test_primary_panels_have_registered_properties_and_operators(self):
        for cls in u.UI_CLASSES:
            if issubclass(cls, bpy.types.Panel):
                cls.draw(SimpleNamespace(layout=Layout()), bpy.context)
        layout = Layout()
        u._draw_record_read_controls(layout, bpy.context)
        self.assertIn('0 / 0', layout.labels)

    def test_z_file_load_keeps_dolly_and_unregister_clears_timers(self):
        self.scene.mpm_dolly_props.target_obj = self.camera
        self.scene.mpm_dolly_props.enabled = True
        other = bpy.data.scenes.new('OtherDollyScene')
        other.mpm_dolly_props.enabled = False
        self.assertIn(d._depsgraph_handler, bpy.app.handlers.depsgraph_update_post)
        path = str(self.folder / 'reload.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        self.assertTrue(bpy.context.scene.mpm_dolly_props.enabled)
        self.assertIn(d._depsgraph_handler, bpy.app.handlers.depsgraph_update_post)
        self.assertTrue(bpy.app.timers.is_registered(c._register_addon_keymaps_impl))
        self.assertTrue(bpy.app.timers.is_registered(x._register_xmp_keymaps_impl))
        lm._restore_lattice_state_after_load()
        c._schedule_addon_keymap_registration()
        c._register_timer_once(lambda: None, first_interval=30)
        timers = tuple(c._REGISTERED_TIMERS)
        addon.unregister()
        self.assertFalse(any(bpy.app.timers.is_registered(timer) for timer in timers))
        # Re-register in the same session to catch incomplete RNA cleanup.
        c._CAMERA_DATA_MANAGER = c.CameraDataManager()
        c._CAMERA_DATA_MANAGER.save_file_path = str(self.folder / 'camera.json')
        a._ALL_OBJECT_DATA_MANAGER = a.AllObjectDataManager()
        a._ALL_OBJECT_DATA_MANAGER.save_file_path = str(self.folder / 'all.json')
        addon.register()


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Regression))
    if not result.wasSuccessful():
        raise RuntimeError('Blender regression tests failed')
    faulthandler.cancel_dump_traceback_later()
