"""Synthetic regressions for the focused verification runner."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from threading import Barrier, Event
import time
import unittest
from unittest.mock import patch

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

    def test_disc_read_false_acceptance_and_malformed_measurements(self):
        check = next(c for c in runner.inventory(runner.INVENTORY, runner.HISTORICAL, 'historical')
                     if c['historical'] == '01')
        (self.directory / 'output.log').touch()
        self.result = results.output_path(check, self.directory)
        identity = {'schema': check['report_schema'], 'elf_sha256': results.EXPECTED, 'result': 'passed'}
        for measurements in ({}, {'cases': []}, {'cases': [{'result': 'failed'}]}):
            with self.subTest(measurements=measurements):
                self.result.write_text(json.dumps(identity | measurements))
                self.assertEqual(results.classify(check, self.directory, 0)[0], results.ERROR)
        self.result.unlink()
        self.assertEqual(results.classify(check, self.directory, 0)[0], results.ERROR)
        self.result.write_text('{')
        self.assertEqual(results.classify(check, self.directory, 0)[0], results.ERROR)

    def test_disc_read_complete_report_and_faults(self):
        from verify_disc_read import CASES_PER_FUNCTION, CHUNKS, FUNCTIONS, LENGTHS, SEED
        check = {'result': 'disc-read', 'report_schema': 'hg-disc-read-differential/v2'}
        (self.directory / 'output.log').touch()
        # Invented lifecycle lengths exercised only at the report boundary.
        # No original instruction execution or native observation is implied.
        sequences = [dict(chunk=chunk, length=length, steps=1 + i)
                     for chunk in CHUNKS for i, length in enumerate(LENGTHS)]
        steps = sum(item['steps'] for item in sequences)
        randomized = len(FUNCTIONS) * CASES_PER_FUNCTION
        row = dict(schema=check['report_schema'], elf_sha256=results.EXPECTED, result='passed',
                   seed=hex(SEED), function_entries=FUNCTIONS,
                   randomized_by_function=dict.fromkeys(FUNCTIONS, CASES_PER_FUNCTION),
                   randomized_cases_per_function=CASES_PER_FUNCTION, randomized_cases=randomized,
                   cases=randomized + steps, lifecycle=sequences,
                   lifecycle_sequences=len(sequences), lifecycle_steps=steps)
        self.result.write_text(json.dumps(row))
        self.assertEqual(results.classify(check, self.directory, 0)[0], results.PASSED)
        self.assertEqual(results.classify(check, self.directory, 1)[0], results.ERROR)
        for bad in (dict(cases=True), dict(seed='0x0'), dict(elf_sha256='0'*64), dict(schema='unknown'),
                    dict(function_entries={}), dict(randomized_by_function={'poll': 350}),
                    dict(lifecycle=[sequences[0], *sequences[:-1]]), dict(lifecycle=sequences[:-1]),
                    dict(lifecycle=[{**sequences[0], 'steps': 0}, *sequences[1:]])):
            with self.subTest(bad=bad.keys()):
                self.result.write_text(json.dumps(row | bad))
                self.assertEqual(results.classify(check, self.directory, 0)[0], results.ERROR)
        self.result.write_text(json.dumps(row)[:-1] + ', "cases": 1}')
        self.assertEqual(results.classify(check, self.directory, 0)[0], results.ERROR)

    def test_discovery_discloses_only_the_three_public_retail_skips(self):
        check = dict(result='discovery', minimum_tests=4)
        (self.directory / 'output.log').touch()
        names = sorted(results.RETAIL_AUDIO_TESTS)
        report = dict(schema='hg-unittest-discovery/v1', pattern='test*.py', run=4,
                      discovered=['test.clean', *names], passed=['test.clean'], skipped=names,
                      failures=[], errors=[], expected_failures=[], unexpected_successes=[])
        self.result.write_text(json.dumps(report))
        verdict, details = results.classify(check, self.directory, 0)
        self.assertEqual((verdict, details['retail_tests_unrun']), (results.PASSED, names))
        self.assertEqual(results.classify(check, self.directory, 0, authenticated_image=True)[0], results.ERROR)
        for bad in (dict(run=3), dict(skipped=names + ['test.clean']),
                    dict(expected_failures=['test.clean']), dict(discovered=['test.clean']*4)):
            self.result.write_text(json.dumps(report | bad))
            self.assertEqual(results.classify(check, self.directory, 0)[0], results.ERROR)

    def test_legacy_reports_require_the_recorded_workload(self):
        gates = runner.inventory(runner.INVENTORY, runner.HISTORICAL, 'historical')
        check = next(row for row in gates if row['historical'] == '15') | {'outputs': ['output.log', 'result.json']}
        (self.directory / 'output.log').touch()
        row = {'result': 'passed', 'cases': 1536, 'instruction_addresses': 190}
        for bad in (dict(cases=0), dict(cases=True), dict(instruction_addresses=0)):
            self.result.write_text(json.dumps(row | bad))
            self.assertEqual(results.classify(check, self.directory, 0)[0], results.ERROR)

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

    def test_gate_19_and_56_hardened_contracts_accept_tracked_and_reject_faults(self):
        gates = {c['historical']: c for c in runner.inventory(runner.INVENTORY, runner.HISTORICAL, 'historical')}
        (self.directory / 'output.log').write_text('')
        # Gate 19: includes nested connected-gameplay-sequence case without top-level compared_bytes.
        ev19 = json.loads((runner.ROOT / 'evidence/m8/connected-gameplay-runtime-comparison.json').read_text())
        check19 = gates['19'] | {'outputs': ['output.log', 'result.json']}
        self.result.write_text(json.dumps(ev19))
        self.assertEqual(results.classify(check19, self.directory, 0)[0], results.PASSED)
        bad19 = json.loads(json.dumps(ev19))
        bad19['cases'][-1]['gameplay_checks']['mode_entered'] = False
        self.result.write_text(json.dumps(bad19))
        self.assertEqual(results.classify(check19, self.directory, 0)[0], results.ERROR)
        bad19_stage = json.loads(json.dumps(ev19))
        bad19_stage['cases'][-1]['stages'][1]['different_bytes'] = 4
        self.result.write_text(json.dumps(bad19_stage))
        self.assertEqual(results.classify(check19, self.directory, 0)[0], results.ERROR)

        # Gate 56: includes probe-only release5-update stage without entry_sha256/compared_bytes.
        ev56 = json.loads((runner.ROOT / 'evidence/m9/player-turn-runtime-comparison.json').read_text())
        check56 = gates['56'] | {'outputs': ['output.log', 'result.json']}
        self.result.write_text(json.dumps(ev56))
        self.assertEqual(results.classify(check56, self.directory, 0)[0], results.PASSED)
        bad56 = json.loads(json.dumps(ev56))
        bad56['stages'][-1]['turn_entry_probe']['different_bytes'] = 1
        self.result.write_text(json.dumps(bad56))
        self.assertEqual(results.classify(check56, self.directory, 0)[0], results.ERROR)

    def test_corridor_pre_dispatch_window_stage_contract(self):
        import hg_verify_current as cur
        from verify_player_continuous import observation
        obs = sorted(observation()['frames'], key=lambda item: item['counter'])
        (self.directory / 'result.so').write_bytes(b'elf-fixture')
        so_sha = runner.digest(self.directory / 'result.so')
        def make_frame(index, expected):
            stages = {
                name: {'result': 'passed'}
                for name in ('camera-chain', 'model-draw', 'render-node-tail', 'late-frame',
                             'render-handoff', 'camera-entry', 'camera-return', 'history-copy',
                             'coordinator-counter', 'pre-dispatch')
            }
            stages['pre-dispatch']['capture'] = 'c'
            stages['pre-dispatch'].update(guarded_ram_count=0, guarded_scratch_count=0, scratch_compared=0x4000)
            stages['pre-dispatch-window'] = {
                'capture': 'w', 'result': 'passed', 'absolute_missing_count': 0,
                'absolute_unexpected_count': 0, 'absolute_value_differences': 0,
                'scratch_missing_count': 0, 'scratch_unexpected_count': 0,
                'scratch_value_differences': 0, 'oracle_capture_differences': {'ram': 0, 'scratch': 0},
                'ctx_matches': True, 'oracle_steps': 42,
                'streams': [{'result': 'passed', 'delta_matches': True, 'missing_offsets': 0,
                             'unexpected_offsets': 0, 'value_differences': 0}],
            }
            stages['rng-continuity'] = {
                'result': 'skipped' if index == len(obs) - 1 else 'passed',
                'interpreted_draws': 2, 'native_draws': None if index == len(obs) - 1 else 2,
            }
            for i, push in enumerate(expected['pushes']):
                stages[f'push-{i}-{push["tier"]}'] = {
                    'capture': push['name'], 'result': 'passed',
                    'guarded_ram_count': 0, 'guarded_scratch_count': 0, 'scratch_compared': 0x4000,
                }
            return {'counter': expected['counter'], 'stages': stages, 'rng_draws': 2, 'interrupts': 1}
        report = {
            'schema': 'hg-player-continuous/v6', 'elf_sha256': results.EXPECTED, 'result': 'passed',
            'lifecycle': True, 'smoke_max_frames': None, 'library_sha256': so_sha,
            'failures': [], 'interpreted_pcs': ['0x100000'], 'frame_count': len(obs),
            'frames': [make_frame(i, item) for i, item in enumerate(obs)],
        }
        check = {'id': 'lifecycle-corridor', 'result': 'continuous', 'argv': ['--lifecycle'],
                 'outputs': ['output.log', 'result.json']}
        (self.directory / 'output.log').write_text('')
        self.result.write_text(json.dumps(report))
        self.assertEqual(results.classify(check, self.directory, 0)[0], results.PASSED)
        report['frames'][0]['stages']['pre-dispatch-window']['oracle_capture_differences']['ram'] = 1
        self.result.write_text(json.dumps(report))
        self.assertEqual(results.classify(check, self.directory, 0)[0], results.ERROR)


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
            # Force equal timestamps so this case is deterministic.
            stamp = max(old.stat().st_mtime_ns, orphan.stat().st_mtime_ns)
            os.utime(old, ns=(stamp, stamp))
            os.utime(orphan, ns=(stamp, stamp))
            self.assertIsNone(runner.reusable(root, basis))

    def test_changed_source_and_input_invalidate_receipt_basis(self):
        with tempfile.TemporaryDirectory() as location:
            fixture = Path(location) / 'fixture'
            fixture.write_bytes(b'one')
            inputs = runner.Inputs(runner.configured_paths(runner.ROOT, None), runner.Stop())
            check = {'id': 'fixture', 'inputs': [str(fixture)], 'argv': []}
            basis = inputs.snapshot(check, {})
            inputs.require_current(check, {}, basis)
            fixture.write_bytes(b'two')
            with self.assertRaisesRegex(ValueError, 'tools or inputs changed'):
                inputs.require_current(check, {}, basis)
            with patch.object(inputs, 'sources', return_value=((), {}, 'different')):
                with self.assertRaisesRegex(ValueError, 'sources changed'):
                    inputs.require_current(check, {}, basis)

    def test_artifact_sealing_rejects_symlink_and_keeps_nested_json(self):
        with tempfile.TemporaryDirectory() as location:
            directory = Path(location)
            nested = directory / 'nested'
            nested.mkdir()
            (nested / 'receipt.json').write_text('nested measurement')
            (directory / 'receipt.json').write_text('top-level seal')
            self.assertEqual(set(runner.artifacts(directory)), {'nested/receipt.json'})
            (directory / 'escaped.log').symlink_to(nested / 'receipt.json')
            with self.assertRaisesRegex(ValueError, 'Invalid attempt artifact'):
                runner.artifacts(directory)

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


class SchedulerTests(unittest.TestCase):
    @staticmethod
    def check(name, *, requires=(), after=(), kind='structure', exclusive=False):
        return dict(id=name, name=name, result=kind, evidence='synthetic', expected=results.PASSED,
                    requires=list(requires), after=list(after), argv=[], exclusive=exclusive)

    def test_overlap_requires_after_and_independent_failure(self):
        barrier = Barrier(2, timeout=5)
        root_finished = Event()
        independent_finished = Event()
        checks = [self.check('root'), self.check('independent'),
                  self.check('hard', requires=('root',)), self.check('ordered', after=('root',)),
                  self.check('exclusive', kind='report', exclusive=True)]
        launched = []

        def run(check, parents):
            name = check['id']
            launched.append(name)
            if name == 'root':
                barrier.wait()
                root_finished.set()
                raise RuntimeError('worker crash')
            if name == 'independent':
                barrier.wait()
                independent_finished.set()
                return runner.brief(check, results.FAILURE)
            if name == 'ordered':
                self.assertTrue(root_finished.is_set())
                self.assertFalse(parents)
            if name == 'exclusive':
                self.assertTrue(independent_finished.is_set())
            return runner.brief(check, results.PASSED)

        rows = runner.schedule(checks, 2, runner.Stop(), run, lambda *_: None)
        self.assertNotIn('hard', launched)
        self.assertEqual({key: row['verdict'] for key, row in rows.items()},
                         dict(root=results.ERROR, independent=results.FAILURE, hard=results.SKIPPED,
                              ordered=results.PASSED, exclusive=results.PASSED))

    def test_invalid_graph_is_rejected(self):
        for checks in ([self.check('a', after=('missing',))],
                       [self.check('a', after=('b',)), self.check('b', requires=('a',))]):
            with self.assertRaisesRegex(ValueError, 'missing or cyclic'):
                runner.schedule(checks, 2, runner.Stop(), lambda *_: None, lambda *_: None)

    def test_interrupted_active_children_do_not_start_pending_work(self):
        stop = runner.Stop()
        checks = [self.check(name) for name in ('a', 'b', 'c')]
        barrier = Barrier(2, timeout=5)
        with tempfile.TemporaryDirectory() as location:
            folder = Path(location)
            def run(check, _):
                directory = folder / check['id']
                directory.mkdir()
                barrier.wait()
                try:
                    with runner.run_lock(directory) as lock:
                        runner.run_child([sys.executable, '-c', 'import time; print("ready", flush=True); time.sleep(30)'],
                                         directory, {'PATH': os.defpath}, lock, stop, 5)
                except runner.Interrupted:
                    return runner.brief(check, results.INTERRUPTED)
                self.fail('Child did not observe interrupt')

            from threading import Thread
            def interrupt():
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    logs = list(folder.glob('*/output.log'))
                    if len(logs) == 2 and all(log.stat().st_size for log in logs):
                        stop.signum = signal.SIGTERM
                        return
                    time.sleep(0.01)
            worker = Thread(target=interrupt)
            worker.start()
            rows = runner.schedule(checks, 2, stop, run, lambda *_: None)
            worker.join()
            self.assertEqual({key: row['verdict'] for key, row in rows.items()},
                             dict(a=results.INTERRUPTED, b=results.INTERRUPTED))

    def test_current_and_historical_profiles_keep_private_prerequisites(self):
        historical = runner.inventory(runner.INVENTORY, runner.HISTORICAL, 'historical')
        current = runner.inventory(runner.INVENTORY, runner.HISTORICAL, 'current')
        self.assertEqual([row for row in current if row.get('historical')], historical)
        nodes = {row['id']: row for row in current}
        self.assertIn('{elf}', nodes['audio-instructions']['inputs'])
        self.assertIn('{image}', nodes['audio-owner-endpoints']['inputs'])
        self.assertIn('{image}', nodes['content-corpus']['inputs'])
        self.assertIn('.ee.z', ' '.join(nodes['front-end-differential']['inputs']))
        synthetic = runner.inventory(runner.INVENTORY, runner.HISTORICAL, 'synthetic')
        self.assertFalse(any(row['id'] in {'content-corpus', 'retail-input-identity'} for row in synthetic))



if __name__ == '__main__':
    unittest.main()
