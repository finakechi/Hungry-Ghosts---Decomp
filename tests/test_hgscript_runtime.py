"""Synthetic public tests for the HGScript guest runtime."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TestHGScriptRuntime(unittest.TestCase):
    def test_compiled_guest_playback(self):
        build = Path(os.environ.get('HG_VERIFY_BUILD_DIR', ROOT / 'local/build'))
        build.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='hgscript-', dir=build) as directory:
            binary = Path(directory) / 'hgscript'
            subprocess.run([
                'cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-pedantic',
                '-I', 'src/hgscript', 'tests/hgscript_runtime.c',
                'src/hgscript/hgscript.c', 'src/hgscript/timed.c', '-o', str(binary),
            ], cwd=ROOT, check=True, timeout=30)
            subprocess.run([binary], check=True, timeout=10)
