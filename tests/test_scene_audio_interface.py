"""Repeatable synthetic scene-to-audio interface boundary tests."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TestSceneAudioInterface(unittest.TestCase):
    def test_scene_to_audio_boundary_and_negative_controls(self):
        build = Path(os.environ.get('HG_VERIFY_BUILD_DIR', ROOT / 'local/build'))
        build.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='scene-audio-', dir=build) as directory:
            binary = Path(directory) / 'scene-audio-interface'
            subprocess.run([
                os.environ.get('CC', 'cc'),
                '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-pedantic',
                '-ffp-contract=off', '-I', 'src',
                'tests/scene_audio_interface.c',
                'src/scene/title_scene.c',
                'src/hgscript/title.c',
                'src/hgscript/hgscript.c',
                'src/hgscript/timed.c',
                'src/front_end.c',
                'src/game_lifecycle.c',
                'src/resource_loader.c',
                'src/disc_read.c',
                'src/audio/audio_engine.c',
                'src/audio/audio_iop.c',
                '-lm', '-o', str(binary),
            ], cwd=ROOT, check=True, timeout=30)
            subprocess.run([binary], check=True, timeout=10)


if __name__ == '__main__':
    unittest.main()
