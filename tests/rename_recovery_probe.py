"""Probe a rollback strategy on disposable files; not production rename code."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


def update(image, target, json_path, fail):
    original = json_path.read_bytes()
    os.rename(image, target)
    try:
        if fail == 'before_json':
            raise OSError('simulated JSON failure')
        temp = json_path.with_suffix('.tmp')
        temp.write_bytes(b'new')
        os.replace(temp, json_path)
        if fail == 'after_json':
            raise OSError('simulated post-commit failure')
    except OSError:
        temp = json_path.with_suffix('.tmp')
        if temp.exists():
            temp.unlink()
        json_path.write_bytes(original)
        os.rename(target, image)
        raise


class RecoveryProbe(unittest.TestCase):
    def test_failure_rollback(self):
        with TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'dist') as folder:
            path = Path(folder)
            for phase in ('before_json', 'after_json'):
                image, new, data = path / 'old.png', path / 'new.png', path / 'stock.json'
                image.write_bytes(b'image')
                data.write_bytes(b'old')
                with self.assertRaises(OSError):
                    update(image, new, data, phase)
                self.assertEqual(image.read_bytes(), b'image')
                self.assertFalse(new.exists())
                self.assertEqual(data.read_bytes(), b'old')


if __name__ == '__main__':
    unittest.main()
