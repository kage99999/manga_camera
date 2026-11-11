"""Attached-stock deletion tests using disposable Blender data."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_regression as base

c, u = base.c, base.u


class DeleteAttached(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base.Regression.setUpClass()

    @classmethod
    def tearDownClass(cls):
        base.Regression.tearDownClass()

    def setUp(self):
        self.helper = base.Regression()
        self.helper.setUp()
        self.scene, self.manager = self.helper.scene, self.helper.manager
        item = self.helper.item('memo')
        item.update(record_selected_objects=True, selected_objects=[{'name': 'Cube'}],
                    fisheye_state={'version': 1, 'enabled': False},
                    record_view_layer_exclude_state=True,
                    view_layer_exclude_state={'view_layer_name': 'ViewLayer', 'collections': []},
                    record_local_view_state=True, local_view_state={'enabled': False, 'objects': []})
        self.manager.save_data(camera_data=[item])
        c.rebuild_enum_cache(self.manager)
        c._set_saved_camera_index_safe(self.scene, self.manager, 0, apply_saved_data=False)

    def operation(self, kind):
        item = self.manager.saved_camera_data[0]
        return SimpleNamespace(kind=kind, stock_index=0, slot_path=self.manager.save_file_path,
                               stock_snapshot=json.dumps(item, sort_keys=True, ensure_ascii=False),
                               report=lambda *args: None)

    def test_each_field_only(self):
        original = copy.deepcopy(self.manager.saved_camera_data[0])
        expected = {'OBJデータ': ('record_selected_objects', False),
                    '魚眼': ('fisheye_state', None), 'ラティス': ('lattice_enabled', False),
                    'ビューレイヤー': ('record_view_layer_exclude_state', False),
                    'ローカルビュー': ('record_local_view_state', False), '適用': ('memo', '')}
        for kind, (key, value) in expected.items():
            self.manager.save_data(camera_data=[original])
            op = self.operation(kind)
            self.assertEqual(u.MPM_OT_additional_data_status_badge.execute(op, bpy.context), {'FINISHED'})
            actual = self.manager.saved_camera_data[0]
            self.assertEqual(actual.get(key), value)
            self.assertEqual(actual['position'], original['position'])
            if kind != '適用':
                self.assertEqual(actual['memo'], 'memo')

    def test_stale_or_failed_write_does_not_change_stock(self):
        before = copy.deepcopy(self.manager.saved_camera_data)
        op = self.operation('適用')
        op.stock_snapshot = '{}'
        self.assertEqual(u.MPM_OT_additional_data_status_badge.execute(op, bpy.context), {'CANCELLED'})
        op = self.operation('適用')
        with patch.object(self.manager, 'save_data', side_effect=OSError('disk full')):
            self.assertEqual(u.MPM_OT_additional_data_status_badge.execute(op, bpy.context), {'CANCELLED'})
        self.assertEqual(self.manager.saved_camera_data, before)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(DeleteAttached))
    if not result.wasSuccessful():
        raise SystemExit(1)
