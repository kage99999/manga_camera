"""Build the installable archive, including the embedded fisheye package."""
import ast
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
tree = ast.parse((ROOT / '__init__.py').read_text(encoding='utf-8-sig'))
info = next(ast.literal_eval(node.value) for node in tree.body
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == 'bl_info' for target in node.targets))
version = info['version']
label = f'{version[0]}_{version[2]:03d}'
output = ROOT / 'dist' / f'00_manga_camera_ver_{label}.zip'
output.parent.mkdir(exist_ok=True)
sources = sorted(ROOT.glob('*.py')) + sorted((ROOT / 'gyogan_camera').glob('*.py'))
with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
    for source in sources:
        ast.parse(source.read_text(encoding='utf-8-sig'))
        archive.write(source, '00_manga_camera/' + source.relative_to(ROOT).as_posix())
print(f'{output}: {len(sources)} modules, {output.stat().st_size} bytes')
