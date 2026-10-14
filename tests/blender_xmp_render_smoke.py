"""Render a disposable compositor PNG and verify XMP through the shared setting."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_regression as v

v.Regression.setUpClass()
try:
    case = v.Regression()
    case.setUp()
    scene = case.scene
    scene.frame_set(123456)
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = 1
    scene.render.resolution_x = 16
    scene.render.resolution_y = 16
    scene.render.resolution_percentage = 100
    scene.render.use_compositing = True
    scene.open_output_after_render = False
    target = case.folder / 'render-output'
    target.mkdir()
    settings = v.SimpleNamespace(directory=str(target), report=lambda *_: None)
    assert v.c.OBJECT_OT_set_output_folder.execute(settings, v.bpy.context) == {'FINISHED'}
    scene['mpm_xmp_target_folder'] = str(case.folder / 'unused-legacy-folder')

    if hasattr(scene, 'compositing_node_group'):
        tree = v.bpy.data.node_groups.new('XMPTest', 'CompositorNodeTree')
        scene.compositing_node_group = tree
    else:
        scene.use_nodes = True
        tree = scene.node_tree
        tree.nodes.clear()
    source = tree.nodes.new('CompositorNodeRLayers')
    output = tree.nodes.new('CompositorNodeOutputFile')
    if hasattr(output.format, 'media_type'):
        output.format.media_type = 'IMAGE'
    output.format.file_format = 'PNG'
    if hasattr(output, 'directory'):
        output.directory = str(target)
        output.file_name = 'frame-######'
        output.file_output_items.new('RGBA', 'Image')
    else:
        output.base_path = str(target)
        output.file_slots[0].path = 'frame-######'
    tree.links.new(source.outputs['Image'], output.inputs[0])

    reports = []
    operator = v.SimpleNamespace(report=lambda level, text: reports.append((level, text)))
    result = v.x.OBJECT_OT_render_with_xmp._run_render_and_xmp(operator, v.bpy.context)
    assert result == {'FINISHED'}, (result, reports)
    pngs = list(target.rglob('*.png'))
    assert pngs, reports
    for png in pngs:
        data = png.read_bytes()
        assert b'<manga:FrameNumber>123456</manga:FrameNumber>' in data, reports
        image = v.bpy.data.images.load(str(png), check_existing=False)
        assert tuple(image.size) == (16, 16)
        v.bpy.data.images.remove(image)
    assert reports[-1][0] == {'INFO'}, reports
    print('XMP_REAL_RENDER_OK', v.bpy.app.version_string, len(pngs), reports)
finally:
    v.Regression.tearDownClass()
