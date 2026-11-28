"""M23-M24 connected original synthetic world/audio lifecycle (no retail inputs)."""
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import verify_world_audio as W  # noqa: E402


class WorldAudioLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build = Path(os.environ.get('HG_VERIFY_BUILD_DIR', ROOT / 'local/build'))
        build.mkdir(parents=True, exist_ok=True)
        cls.proc = W.run(os.environ.get('CC', 'cc'), False, build)
        cls.report = json.loads(cls.proc.stdout)
        cls.m23 = json.loads(W.M23.read_text())

    def test_report_matches_committed_receipt(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)
        self.assertEqual(self.proc.stdout, W.EVIDENCE.read_bytes())

    def test_independent_checks_pass(self):
        summary = W.check_report(self.report, self.m23)
        self.assertEqual((summary['cycles'], summary['controls']), (2, 10))
        self.assertGreater(summary['pcm_nonzero_frames_per_cycle'], 0)
        self.assertGreater(summary['steal_per_cycle'], 0)

    def test_checks_reject_world_divergence_and_undetected_control(self):
        diverged = json.loads(json.dumps(self.report))
        diverged['connected']['cycles'][0]['checkpoints']['after_wall']['player_pos'][0] += 0.5
        with self.assertRaises(ValueError):
            W.check_report(diverged, self.m23)
        missed = json.loads(json.dumps(self.report))
        missed['controls'][2]['detected'] = False
        with self.assertRaises(ValueError):
            W.check_report(missed, self.m23)
        injected = json.loads(json.dumps(self.report))
        injected['connected']['cycles'][1]['decision_fnv1a64'] = '0' * 16
        with self.assertRaises(ValueError):
            W.check_report(injected, self.m23)


if __name__ == '__main__':
    unittest.main()
