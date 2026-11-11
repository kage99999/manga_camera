"""Read-only probe of a supplied blend and stock JSON in an isolated Blender process."""
import importlib
import json
import os
from pathlib import Path
import sys

import bpy

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root.parent))
addon = importlib.import_module(root.name)
stock = json.loads(Path(os.environ['MPM_POSE_PROBE_JSON']).read_text(encoding='utf-8'))['camera_data'][1]
record = next(x for x in stock['selected_objects'] if x['name'] == 'rig')
obj = bpy.data.objects[record['name']]
saved = next(x for x in record['pose_bones'] if x['name'] == 'c_hand_ik.r')
bone = obj.pose.bones[saved['name']]
assert not addon.pose_stock._has_active_nla(obj.animation_data)
bpy.context.scene.frame_set(stock['frame_current'])
bpy.context.view_layer.update()
before = tuple(bone.location)
addon.pose_stock.apply_pose_record(obj, record, prefer_keyframes=False)
after = tuple(bone.location)
assert all(abs(a - b) < 1e-5 for a, b in zip(after, saved['location'])), (after, saved['location'])
bpy.context.view_layer.update()
evaluated = bpy.context.evaluated_depsgraph_get()
for name in ('Body_Low', 'Eyes'):
    mesh = bpy.data.objects[name]
    assert any(m.type == 'ARMATURE' and m.object == obj for m in mesh.modifiers)
    assert mesh.evaluated_get(evaluated).data is not None
after_update = tuple(bone.location)
assert all(abs(a - b) < 1e-5 for a, b in zip(after_update, saved['location'])), after_update
print('EXTERNAL POSE PROBE OK', before, after, after_update)
