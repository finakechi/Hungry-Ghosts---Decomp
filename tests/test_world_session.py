"""M25 world-session synthetic tests (public CI; no retail inputs, no skips).

The driver's own checker and the Python re-derivation must both accept the
positive run, both must reject tampered traces, every control must be
detected by its intended check, and the M23 receipt must stay unchanged.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'tools') not in sys.path:
    sys.path.insert(0, str(ROOT / 'tools'))

import verify_world_session as W

CLASSES = {'original-bytes', 'runtime-observation', 'existing-c', 'independent-model', 'fixture-external',
           'inference-conflict', 'unknown'}


class WorldSessionSynthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = os.environ.get('CC', 'cc')
        cls.tmp = tempfile.TemporaryDirectory(prefix='m25-test-')
        build = Path(cls.tmp.name) / 'build'
        build.mkdir()
        cls.build = build
        cls.row = W.verify_synthetic(compiler=cls.compiler, sanitize=False, build_dir=build)
        binary = Path(cls.tmp.name) / 'driver'
        W.build_driver(binary, cls.compiler, False)
        out = subprocess.run([str(binary), '--json'], capture_output=True, text=True, check=True, timeout=300)
        cls.raw = json.loads(out.stdout)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def rederive(self, report):
        return W.rederive(report, W.tracked_coordinator_order())['problems']

    def test_clean_run_matches_tracked_receipt(self):
        r = self.row
        self.assertEqual((r['exit_code'], r['stderr'], r['failure'], r['problems']), (0, '', None, []))
        self.assertTrue(r['matches_evidence'])
        self.assertFalse(r['report']['retail_equivalence_claimed'])

    def test_two_scenes_interleaved_with_sustained_updates(self):
        report = self.row['report']
        a, b = report['scenes']
        self.assertNotEqual(a['spawn'], b['spawn'])
        self.assertNotEqual((a['player_bones'], a['props'], a['player_arena']), (b['player_bones'], b['props'], b['player_arena']))
        self.assertEqual([c['scene'] for c in report['cycles']], [a['name'], b['name']] * 2)
        for c in report['cycles']:
            self.assertGreaterEqual(c['post_ready_updates'], 120)
            self.assertEqual(c['failures'], [])
            self.assertEqual(c['after_teardown'], {'free_nodes': 2048, 'owned_nodes': 0, 'pool_free': [286, 40, 256]})
            self.assertGreater(c['motion']['slide_frames'], 0)
            self.assertGreater(c['motion']['floor_changes'], 0)
            self.assertEqual(c['first_tick']['input_block_3f9f6e'], 1)
        self.assertEqual(report['rederived']['parity'], {a['name']: True, b['name']: True})

    def test_coordinator_order_matches_elf_derived_evidence(self):
        self.assertEqual(self.raw['checker_coordinator_order'], W.tracked_coordinator_order())
        self.assertEqual(len(W.tracked_coordinator_order()), 18)

    def test_rederivation_accepts_raw_and_rejects_tampering(self):
        self.assertEqual(self.rederive(self.raw), [])
        fields = self.raw['cycles'][0]['trace_fields']

        def tamper(cycle, step, field, delta):
            t = json.loads(json.dumps(self.raw))
            t['cycles'][cycle]['trace'][step][fields.index(field)] += delta
            return self.rederive(t)
        self.assertTrue(any('discontinuity' in p or 'eye' in p for p in tamper(1, 120, 'x', 3.0)))
        self.assertTrue(any('eye off head' in p for p in tamper(0, 90, 'eye_y', 0.5)))
        self.assertTrue(any('dropped update' in p for p in tamper(2, 100, 'c3fa190', 1)))
        self.assertTrue(any('floor' in p for p in tamper(3, 110, 'y', 0.75)))
        self.assertTrue(any('pitch' in p for p in tamper(1, 5, 'pitch', -0.0523)))
        t = json.loads(json.dumps(self.raw))
        t['controls'][5]['detected'] = False
        self.assertTrue(any('control' in p for p in self.rederive(t)))
        t = json.loads(json.dumps(self.raw))
        t['cycles'][3]['trace'] = t['cycles'][3]['trace'][:150]
        self.assertTrue(self.rederive(t))

    def test_controls_detected_by_intended_checks(self):
        controls = {k['name']: k for k in self.raw['controls']}
        self.assertEqual(len(controls), 14)
        for name, k in controls.items():
            fired = k['fired'].split(',')
            self.assertTrue(k['detected'], name)
            self.assertIn(k['intended'], fired, name)
            if k['forbidden']:
                self.assertNotIn(k['forbidden'], fired, name)
        # Failed setups report the structured error and leave no allocation.
        self.assertTrue(controls['exhaust-nodes-before-player']['api'].startswith('CAPACITY/'))
        self.assertTrue(controls['bind-player-to-area']['api'].startswith('INVALID_PLAYER_BINDING/'))
        self.assertTrue(controls['event-entry-branch']['api'].startswith('UNSUPPORTED_ENTRY/'))

    def test_invalid_inputs_fail_safely(self):
        rows = self.raw['invalid_inputs']
        self.assertGreaterEqual(len(rows), 30)
        self.assertEqual([r['name'] for r in rows if r['expected'] != r['actual']], [])

    def test_build_outputs_stay_in_build_dir(self):
        self.assertEqual(list(self.build.iterdir()), [])

    def test_provenance_ledger_is_complete(self):
        ledger = json.loads((ROOT / 'evidence/m25/provenance-ledger.json').read_text())
        self.assertEqual(set(ledger['classes']), CLASSES)
        for row in ledger['fields']:
            self.assertIn(row['session'], CLASSES, row['field'])
            self.assertIn(row['retail'], CLASSES, row['field'])
            self.assertRegex(row['address'], r'^[0-9a-f]{8}$', row['field'])
        used = {row['session'] for row in ledger['fields']} | {row['retail'] for row in ledger['fields']}
        self.assertEqual(used, CLASSES)
        by_field = {row['field']: row for row in ledger['fields']}
        self.assertEqual(by_field['003f9f6e input block']['retail'], 'inference-conflict')
        self.assertEqual(by_field['gameplay audio resume']['retail'], 'unknown')

    def test_m23_receipt_unchanged(self):
        import verify_world_init as M23
        report = M23.verify_synthetic(compiler=self.compiler, sanitize=False, build_dir=self.build)
        tracked = json.loads((ROOT / 'evidence/m23/synthetic-lifecycle.json').read_text())
        for key in ('compiler', 'sanitized'):
            report.pop(key, None)
            tracked.pop(key, None)
        self.assertEqual(report, tracked)


if __name__ == '__main__':
    unittest.main()
