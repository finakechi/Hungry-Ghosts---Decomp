"""Public title scheduling tests with authored data and service callbacks."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TestTitleScene(unittest.TestCase):
    def test_title_task_and_service_order(self):
        build = Path(os.environ.get('HG_VERIFY_BUILD_DIR', ROOT / 'local/build'))
        build.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='title-scene-', dir=build) as directory:
            binary = Path(directory) / 'title-scene'
            subprocess.run([
                'cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-pedantic',
                '-ffp-contract=off', '-I', 'src', 'tests/title_scene.c',
                'src/scene/title_scene.c', 'src/hgscript/title.c', 'src/hgscript/hgscript.c',
                'src/hgscript/timed.c', 'src/resource_loader.c', 'src/disc_read.c', '-o', str(binary),
            ], cwd=ROOT, check=True, timeout=30)
            subprocess.run([binary], check=True, timeout=10)
