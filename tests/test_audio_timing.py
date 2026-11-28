"""M26 synthetic audio timing and voice management lifecycle tests."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import verify_audio_timing as vat  # noqa: E402


class TestM26AudioTimingLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build = Path(os.environ.get('HG_VERIFY_BUILD_DIR', ROOT / 'local/build'))
        build.mkdir(parents=True, exist_ok=True)
        cls.proc = vat.run(os.environ.get('CC', 'cc'), sanitize=False, build=build)
        cls.report = json.loads(cls.proc.stdout)

    def test_driver_passes_and_matches_committed_evidence(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr.decode(errors='replace'))
        self.assertEqual(self.proc.stderr, b'')
        summary = vat.check_report(self.report)
        self.assertEqual(summary['families'], 7)
        self.assertEqual(summary['multi_schedule_families'], 5)
        self.assertEqual(summary['controls'], 10)
        self.assertEqual(self.proc.stdout, vat.EVIDENCE.read_bytes())

    def test_checker_rejects_tampered_schedule_or_control(self):
        broken = json.loads(json.dumps(self.report))
        broken['families'][0]['schedules'][1]['cycle1']['logical_hash'] = '0000000000000000'
        with self.assertRaisesRegex(ValueError, 'logical field logical_hash'):
            vat.check_report(broken)

        broken2 = json.loads(json.dumps(self.report))
        broken2['controls'][5]['failures'] = ['timeline']
        with self.assertRaisesRegex(ValueError, 'premature-reuse'):
            vat.check_report(broken2)


if __name__ == '__main__':
    unittest.main()
