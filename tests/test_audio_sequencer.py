"""M24 synthetic sequencer and voice-manager lifecycle tests (tests/audio_sequencer.c)."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCES = ['tests/audio_sequencer.c', 'src/audio/audio_engine.c',
           'src/audio/audio_iop.c', 'src/audio/audio_sequencer.c']
POSITIVE = ['short-notification', 'timed-events', 'overlapping-channels',
            'exhaustion-steal-reuse', 'active-cancel', 'bank-release',
            'service-reset', 'repeated-requests', 'malformed-input']
CONTROLS = {
    'nc-missing-event-advancement': 'timeline',
    'nc-dropped-service-requests': 'key-on',
    'nc-missing-key-off': 'key-off',
    'nc-foreign-key-off': 'key-off',
    'nc-ownership-log-mutation': 'ownership',
}


def run_harness(directory):
    binary = Path(directory) / 'audio-sequencer'
    subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-O2', '-Wall', '-Wextra',
                    '-Werror', '-pedantic', '-ffp-contract=off', '-I', 'src', *SOURCES,
                    '-lm', '-o', str(binary)], cwd=ROOT, check=True, timeout=60)
    result = subprocess.run([binary], capture_output=True, text=True, timeout=60)
    return result.returncode, json.loads(result.stdout)


class TestAudioSequencer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build = Path(os.environ.get('HG_VERIFY_BUILD_DIR', ROOT / 'local/build'))
        build.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='m24-seq-', dir=build) as directory:
            cls.code, cls.report = run_harness(directory)
        cls.by_name = {s['name']: s for s in cls.report['scenarios']}
        receipt = os.environ.get('HG_M24_RECEIPT')
        if receipt:
            Path(receipt).write_text(json.dumps(cls.report, indent=1) + '\n')

    def test_harness_accepts(self):
        self.assertEqual(self.code, 0)
        self.assertTrue(self.report['ok'])
        self.assertEqual(self.report['format'], 'hg-m24-sequencer/1')

    def test_positive_scenarios_pass_every_check(self):
        for name in POSITIVE:
            with self.subTest(name=name):
                scenario = self.by_name[name]
                self.assertTrue(scenario['harness_completed'])
                self.assertEqual(scenario['failures'], [], scenario['detail'])
                self.assertEqual(scenario['key_on'], scenario['key_off'])

    def test_automatic_decisions_cover_the_lifecycle(self):
        s = self.by_name
        self.assertEqual((s['short-notification']['key_on'], s['short-notification']['ring_records']), (1, 6))
        self.assertEqual(s['overlapping-channels']['key_on'], 4)
        self.assertEqual((s['exhaustion-steal-reuse']['steal'], s['exhaustion-steal-reuse']['drop']), (1, 1))
        self.assertEqual((s['repeated-requests']['key_on'], s['repeated-requests']['steal']), (46, 2))
        self.assertEqual(s['malformed-input']['reject'], 2)
        self.assertEqual(s['malformed-input']['stage_fail'], 1)
        for name in POSITIVE:
            self.assertGreater(s[name]['pcm_nonzero_frames'], 0, name)

    def test_negative_controls_measure_real_divergence(self):
        for name, check in CONTROLS.items():
            with self.subTest(name=name):
                control = self.by_name[name]
                # A harness failure aborts the process and never reaches here.
                self.assertTrue(control['harness_completed'])
                self.assertEqual(control['expected_failure'], check)
                self.assertIn(check, control['failures'])
        self.assertEqual(self.by_name['nc-dropped-service-requests']['pcm_nonzero_frames'], 0)

    def test_connected_decision_log(self):
        # [tick, kind, request, play_index, track, note, voice, detail]
        log = self.report['connected_decisions']
        kinds = [entry[1] for entry in log]
        self.assertEqual(kinds[0], 1)    # START
        self.assertEqual(kinds[-1], 11)  # COMPLETE
        key_on = [e for e in log if e[1] == 6]
        self.assertEqual([(e[0], e[5], e[6]) for e in key_on], [(1, 60, 8), (7, 67, 8)])


if __name__ == '__main__':
    unittest.main()
