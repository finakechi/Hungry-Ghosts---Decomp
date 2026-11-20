"""Synthetic regressions for the focused verification runner."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import hg_verify as runner
import hg_verify_results as results
from verify_player_grounded_runtime import airborne_refusal


CONTROL = {
    'schema': results.CONTROL_SCHEMA,
    'elf_sha256': results.EXPECTED,
    'result': 'measured-divergence',
    'difference': {
        'function': 'hg_player_ordinary_update',
        'case': 1,
        'actual': [['call', 0x13a050, 0x3f800000, 0x500090], ['call', 0x15fe00, 0x500000, 0]],
        'expected': [['call', 0x13a050, 0x3f800000, 0x500090], ['call', 0x15fe00, 0x500000, 1]],
    },
}


class ResultTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.result = self.directory / 'result.json'

    def control(self, code):
        return results.classify({'result': 'ordinary-control'}, self.directory, code)[0]

    def test_nonzero_exit_never_proves_a_mutation(self):
        (self.directory / 'output.log').write_text('cc: fatal error: file not found\n')
        self.assertEqual(self.control(1), results.ERROR)
        self.assertEqual(self.control(-signal.SIGSEGV), results.ERROR)
        self.assertEqual(self.control(0), results.ERROR)
        self.result.write_text('{')
        self.assertEqual(self.control(1), results.ERROR)

    def test_control_requires_build_and_exact_typed_trace(self):
        self.result.write_text(json.dumps(CONTROL))
        self.assertEqual(self.control(0), results.ERROR)
        self.result.with_suffix('.so').write_bytes(b'compiled fixture')
        self.assertEqual(self.control(1), results.ERROR)
        self.assertEqual(self.control(0), results.REJECTED)
        wrong = json.loads(self.result.read_text())
        wrong['difference']['actual'][-1][-1] = False
        self.result.write_text(json.dumps(wrong))
        self.assertEqual(self.control(0), results.ERROR)
        self.result.write_text(json.dumps({**CONTROL, 'result': 'equal', 'difference': None}))
        self.assertEqual(self.control(1), results.FAILURE)

    def test_verified_byte_mismatch_and_harness_error_are_distinct(self):
        check = {'result': 'ps2dinfo-bytes'}
        log = self.directory / 'output.log'
        log.write_text('FAIL: byte mismatch at 0x0015FC10 (file +0x60410)\n')
        self.assertEqual(results.classify(check, self.directory, 1)[0], results.FAILURE)
        log.write_text('FAIL: [Errno 2] file not found\n')
        self.assertEqual(results.classify(check, self.directory, 1)[0], results.ERROR)
        self.assertEqual(results.classify(check, self.directory, 0)[0], results.ERROR)

    def test_missing_runtime_measurement_cannot_supply_rejection(self):
        row = {'kind': 'compiled-defect', 'result': 'rejected',
               'registers': [{'register': 'floor-return-v0', 'result': 'missing'}]}
        self.assertEqual(results.control_evidence(row)[0], results.ERROR)

    def test_exact_refusal_and_mixed_harness_errors(self):
        item = "ValueError('C path outside reconstructed domain (code 0x50)')"
        self.assertEqual(airborne_refusal(AssertionError([item])), '0x50')
        self.assertIsNone(airborne_refusal(AssertionError([item, 'Unbalanced guest frame'])))
        self.assertEqual(airborne_refusal(AssertionError([item.replace('0x50', '0x54')])), '0x54')
        row = {'kind': 'fail-closed', 'result': 'refused', 'refusal_code': '0x50'}
        self.assertEqual(results.control_evidence(row)[0], results.REFUSED)
        row.update(result='unexpected-refusal', refusal_code='0x54')
        self.assertEqual(results.control_evidence(row)[0], results.WRONG_REFUSAL)


    def test_runtime_control_requires_floor_register_and_untouched_dma(self):
        row = {
            'control': 'floor-returns-two', 'kind': 'compiled-defect', 'result': 'rejected',
            'first_divergent_stage': None, 'rejected_by': ['register'],
            'comparison': {'result': 'passed', 'different_bytes': 0, 'scratchpad': {'different_bytes': 0},
                           'external_dma': {'modeled_accesses': []}},
            'registers': [
                {'register': name, 'result': 'failed' if name == 'floor-return-v0' else 'passed',
                 'native': '00000001', 'calculated': '00000002' if name == 'floor-return-v0' else '00000001'}
                for name in ('floor-query-v0', 'floor-return-v0', 'constraint-entry-f12')],
        }
        self.assertEqual(results.control_evidence(row)[0], results.REJECTED)
        row['comparison']['external_dma']['modeled_accesses'].append('0x95e400')
        self.assertEqual(results.control_evidence(row)[0], results.ERROR)
        row['comparison']['external_dma']['modeled_accesses'].clear()
        row['comparison']['different_bytes'] = 1
        with self.assertRaises(ValueError):
            results.control_evidence(row)

    def test_inherited_refusals_require_exact_code_and_branch(self):
        from resource_harness import Memory
        from verify_player_constraint_differential import classify_refusal
        from verify_player_turn_camera import UnexpectedRefusal, check_refusal

        memory = Memory()
        self.assertFalse(check_refusal(0x12c790, [], memory, [], []))
        memory.store(0x3eaed4, 1, 1)
        self.assertTrue(check_refusal(0x12c790, [], memory, ["ValueError('domain fault 0x21')"], [('fault', 0x21)]))
        for errors, calls in (
                ([], []),
                (["ValueError('domain fault 0x20')"], [('fault', 0x20)]),
                (["ValueError('domain fault 0x21')", 'other fault'], [('fault', 0x21)])):
            with self.assertRaises(UnexpectedRefusal):
                check_refusal(0x12c790, [], memory, errors, calls)
        expected = ["ValueError('C path outside reconstructed domain (code 0x40)')"]
        wrong = [expected[0].replace('0x40', '0x41')]
        self.assertEqual(classify_refusal(expected, True, {0x15eaf0})['result'], 'unsupported')
        for errors, pcs in ((expected, set()), (wrong, {0x15eaf0}), ([], {0x15eaf0})):
            self.assertEqual(classify_refusal(errors, True, pcs)['failure'], 'refusal-mismatch')
        self.assertEqual(classify_refusal(expected + ['additional error'], True, {0x15eaf0})['result'], 'error')

    def test_escaped_control_with_numeric_case_count_is_divergence(self):
        check = {'result': 'report', 'expected': results.PASSED,
                 'controls': {'negative_controls': {'lost-clamp': 'rejected'}}}
        self.result.write_text(json.dumps({'result': 'failed', 'cases': 768,
                                           'negative_controls': [{'control': 'lost-clamp', 'result': 'ESCAPED'}]}))
        (self.directory / 'output.log').write_text('')
        self.assertEqual(results.classify(check, self.directory, 1)[0], results.FAILURE)

    def test_compiler_failure_never_passes_sanitizer_build(self):
        (self.directory / 'output.log').write_text('cc: fatal error: input.c: No such file or directory')
        self.assertEqual(results.classify({'result': 'sanitizer-build'}, self.directory, 1)[0], results.ERROR)
        self.assertEqual(results.classify({'result': 'sanitizer-build'}, self.directory, 0)[0], results.ERROR)


class PlanningTests(unittest.TestCase):
    def test_profiles_cover_history_and_expose_dependency(self):
        rows = runner.inventory(runner.INVENTORY, runner.HISTORICAL, 'full')
        self.assertEqual(sorted(c['historical'] for c in rows), [f'{i:02d}' for i in range(79)])
        self.assertEqual(len({row['id'] for row in rows}), 79)
        by_id = {row['id']: row for row in rows}
        self.assertEqual(by_id['sanitizer-run']['requires'], ['sanitizer-build'])
        self.assertLess(rows.index(by_id['sanitizer-build']), rows.index(by_id['sanitizer-run']))
        self.assertIn('--m8-a1-zero-control', by_id['ordinary-a1-zero-control']['argv'])
        self.assertEqual(len(runner.inventory(runner.INVENTORY, runner.HISTORICAL, 'focused')), 8)

    def test_missing_and_wrong_retail_inputs_block_before_execution(self):
        check = next(c for c in runner.inventory(runner.INVENTORY, runner.HISTORICAL, 'focused')
                     if c['id'] == 'grounded-bytes-jal')
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            elf = folder / 'game.elf'
            options = runner.configured_paths(runner.ROOT, None) | {'elf': str(elf)}
            stop = runner.Stop()
            inputs = runner.Inputs(options, stop)
            with runner.run_lock(folder) as lock:
                missing = runner.attempt(folder, check, inputs, {}, lock, stop)
                elf.write_bytes(b'wrong')
                incorrect = runner.attempt(folder, check, inputs, {}, lock, stop)
            self.assertEqual((missing['verdict'], incorrect['verdict']), (results.MISSING, results.INVALID))
            self.assertFalse(missing['executed'] or incorrect['executed'])
            self.assertNotEqual(missing['attempt'], incorrect['attempt'])


class ReceiptTests(unittest.TestCase):
    def test_resume_rejects_orphaned_changed_or_corrupt_attempts(self):
        with tempfile.TemporaryDirectory() as location:
            root = Path(location)
            old = root / 'attempt-old'
            old.mkdir()
            orphan = root / 'attempt-orphan'
            (old / 'output.log').write_text('result')
            basis = {'source': 'one'}
            entry = {'schema': runner.STATE_SCHEMA, 'verdict': results.PASSED,
                     'fingerprint': basis, 'artifacts': runner.artifacts(old)}
            entry['receipt_sha256'] = runner.canonical_hash(entry)
            runner.atomic_json(old / 'receipt.json', entry)

            self.assertTrue(runner.reusable(root, basis)['reused'])
            self.assertIsNone(runner.reusable(root, {'source': 'two'}))
            (old / 'output.log').write_text('changed')
            self.assertIsNone(runner.reusable(root, basis))
            (old / 'output.log').write_text('result')
            corrupt = dict(entry, verdict=results.REJECTED)
            runner.atomic_json(old / 'receipt.json', corrupt)
            self.assertIsNone(runner.reusable(root, basis))
            runner.atomic_json(old / 'receipt.json', entry)
            orphan.mkdir()
            (orphan / 'result.json').write_text('partial')
            self.assertIsNone(runner.reusable(root, basis))
            self.assertEqual((orphan / 'result.json').read_text(), 'partial')

    def test_timeout_and_lock_cannot_produce_a_completion_receipt(self):
        with tempfile.TemporaryDirectory() as location:
            directory = Path(location)
            with runner.run_lock(directory) as lock:
                with self.assertRaisesRegex(ValueError, 'already has a writer'):
                    with runner.run_lock(directory):
                        self.fail('Concurrent writer was accepted')
                with self.assertRaises(TimeoutError):
                    runner.run_child([sys.executable, '-c', 'import time; time.sleep(10)'], directory,
                                     {'PATH': os.defpath}, lock, runner.Stop(), timeout=0.05)
            self.assertTrue((directory / 'output.log').exists())
            self.assertFalse((directory / 'receipt.json').exists())


if __name__ == '__main__':
    unittest.main()
