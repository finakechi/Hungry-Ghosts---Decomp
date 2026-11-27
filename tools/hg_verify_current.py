"""Report contracts for the extended verification inventory."""
from pathlib import Path
import re

from hg_evidence_safety import digest
from hg_verify_legacy_contracts import exact

ROOT = Path(__file__).resolve().parents[1]
KINDS = {
    'continuous', 'predispatch', 'predispatch-contracts', 'counter-guard', 'lifecycle-differential',
    'lifecycle-native', 'lifecycle-controls', 'lifecycle-corridor-controls', 'lifecycle-sequence',
    'front-end-differential', 'front-end-native', 'front-end-controls', 'content-corpus',
    'audio-instructions', 'audio-connected', 'overlay-bytes',
}
SCHEMAS = {
    'continuous': 'hg-player-continuous/v6',
    'predispatch': 'hg-m14-predispatch-window/v2',
    'predispatch-contracts': 'hg-m14-predispatch-contracts/v1',
    'counter-guard': 'hg-m14-counter-guard/v2',
    'lifecycle-differential': 'hg-m17-lifecycle-differential/v1',
    'lifecycle-native': 'hg-m17-lifecycle-native/v1',
    'lifecycle-controls': 'hg-m17-lifecycle-controls/v1',
    'lifecycle-corridor-controls': 'hg-m17-corridor-controls/v1',
    'front-end-differential': 'hg-m18-front-end-differential/v1',
    'front-end-native': 'hg-m18-native-lifecycle/v1',
    'front-end-controls': 'hg-m18-native-lifecycle-controls/v1',
    'content-corpus': 'hg-content-corpus/v1',
    'audio-instructions': 'hg-audio-instructions/v1',
    'audio-connected': 'hg-audio-connected/v1',
}
ELF_REPORTS = {
    'continuous', 'predispatch', 'predispatch-contracts', 'counter-guard', 'lifecycle-differential',
    'lifecycle-native', 'lifecycle-corridor-controls', 'front-end-differential', 'audio-instructions',
}
STATUS_REPORTS = {'continuous', 'predispatch', 'predispatch-contracts', 'counter-guard'}
BOOL_REPORTS = {'lifecycle-differential', 'lifecycle-native', 'lifecycle-controls',
                'lifecycle-corridor-controls', 'front-end-controls'}


def need(condition, basis):
    if not condition:
        raise ValueError(basis)


def positive(value):
    return type(value) is int and value > 0


def sha(value):
    return isinstance(value, str) and bool(re.fullmatch('[0-9a-f]{64}', value))


def against(rows, reference, keys):
    need(exact([[row[key] for key in keys] for row in rows],
               [[row[key] for key in keys] for row in reference]), f'Wrong measurement inventory for {keys}')


def compare_counts(row, wanted):
    for name, value in wanted.items():
        need(exact(row[name], value), f'Invalid measurement {name}')


def c_artifact(directory, row):
    library = directory / 'result.so'
    need(library.stat().st_size > 0 and digest(library) == row['library_sha256'],
         'Missing or inconsistent compiled comparison artifact')


def corridor(kind, row, directory, read):
    from verify_player_continuous import observation
    from verify_predispatch_window import REQUIRED_RUNTIME, REQUIRED_COMPILED, DIAGNOSTIC_RUNTIME, DIAGNOSTIC_COMPILED

    obs = sorted(observation()['frames'], key=lambda item: item['counter'])
    frames = row['frames']
    against(frames, obs, ('counter',))
    need(row['smoke_max_frames'] is None, 'Smoke limits cannot provide full corridor coverage')
    c_artifact(directory, row)
    if kind == 'continuous':
        need(not row['failures'] and positive(len(row['interpreted_pcs'])) and row['frame_count'] == len(obs),
             'Incomplete connected execution')
        for index, (frame, expected) in enumerate(zip(frames, obs)):
            stages = frame['stages']
            wanted = {'camera-chain', 'model-draw', 'render-node-tail', 'late-frame', 'rng-continuity',
                      'pre-dispatch', 'pre-dispatch-window', 'render-handoff', 'camera-entry', 'camera-return',
                      'history-copy', 'coordinator-counter'}
            wanted.update(f'push-{i}-{push["tier"]}' for i, push in enumerate(expected['pushes']))
            need(wanted <= stages.keys(), 'Missing continuous stage measurements')
            allowed = ('rng-continuity',) if index == len(obs) - 1 else ()
            for name, stage in stages.items():
                if name == 'pre-dispatch-window':
                    compare_counts(stage, {'absolute_missing_count': 0, 'absolute_unexpected_count': 0,
                                           'absolute_value_differences': 0, 'scratch_missing_count': 0,
                                           'scratch_unexpected_count': 0, 'scratch_value_differences': 0,
                                           'oracle_capture_differences': {'ram': 0, 'scratch': 0}, 'ctx_matches': True})
                    need(positive(stage['oracle_steps']) and bool(stage['streams']) and all(
                        s['result'] == 'passed' and s['delta_matches'] is True and exact(s['missing_offsets'], 0)
                        and exact(s['unexpected_offsets'], 0) and exact(s['value_differences'], 0)
                        for s in stage['streams']), 'Missing pre-dispatch window oracle steps or stream equality')
                elif 'capture' in stage:
                    compare_counts(stage, {'guarded_ram_count': 0, 'guarded_scratch_count': 0})
                    need(positive(stage['scratch_compared']), 'Missing guarded scratchpad comparison')
                need(stage['result'] == ('skipped' if name in allowed else 'passed'),
                     f'Unexpected continuous comparison result for {name}')
            rng = stages['rng-continuity']
            need(exact(rng['interpreted_draws'], frame['rng_draws'])
                 and exact(rng['native_draws'], None if allowed else frame['rng_draws']),
                 'Incomplete RNG continuity comparison')
            if row['lifecycle']:
                need(exact(frame['interrupts'], 1), 'Lifecycle frame did not deliver exactly one interrupt')
        if not row['lifecycle']:
            reference = read(ROOT / 'evidence/m16/results.json')['negative_controls']
            measured = row['negative_controls']
            against(measured, reference, ('control', 'kind'))
            for control in measured:
                if control['kind'] == 'null-control':
                    need(control['result'] == 'clean', 'Continuous null control diverged')
                else:
                    need(control['result'] == 'rejected' and control['first_divergent_stage'] != 'execution-fault',
                         'Unmeasured continuous control rejection')
    else:
        for frame in frames:
            oracle = frame['oracle']
            compare_counts(oracle, {'identity_mismatches': [] if kind == 'predispatch' else 0,
                                    'full_ram_differences': 0, 'scratch_differences': 0})
            if kind == 'predispatch':
                compare_counts(frame, {'result': 'passed', 'c_service_events': [[0x198f60, 1], [0x198f60, 0]],
                                       'interpreted_entries': ['0x17f6d0', '0x17f2d0']})
            else:
                need(len(frame['calls']) == 31 and frame['contract_failures'] == []
                     and frame['poisoned_differential']['result'] == 'passed', 'Incomplete callee contract comparisons')
                need(all(c['original_fault'] is None and positive(c['original_steps']) and
                         all(c[mode]['equal'] is True for mode in ('poison-1', 'poison-2', 'bridge'))
                         for c in frame['calls']), 'Callee contract has unproved equality')
        if kind == 'predispatch':
            need(exact([(h['from_counter'], h['to_counter']) for h in row['phase_handoffs']],
                       [(a['counter'], b['counter']) for a, b in zip(obs, obs[1:])])
                 and all(h['phase_matches'] is True for h in row['phase_handoffs']), 'Incomplete phase handoff coverage')
            for field, ids, expect in (
                ('negative_controls', REQUIRED_RUNTIME + REQUIRED_COMPILED, True),
                ('diagnostic_controls', DIAGNOSTIC_RUNTIME + DIAGNOSTIC_COMPILED, False),
            ):
                measured = row[field]
                need(exact([r['name'] for r in measured], list(ids)), 'Wrong pre-dispatch control inventory')
                need(all(r['detected'] is expect and not r['detail'].get('error') for r in measured),
                     'Incomplete pre-dispatch control measurements')
        else:
            need(row['contract_frames'] is None and frames[0]['poisoned_store_width_pre_fix']['result'] == 'failed',
                 'Contract run omitted the store-width control or used a smoke limit')
    return {'frames': len(frames)}


def differential(kind, check, row, read):
    argv = check['argv']
    cases = int(argv[argv.index('--cases') + 1])
    captures = [Path(arg).stem for i, arg in enumerate(argv) if i and argv[i - 1] == '--capture']
    if kind == 'front-end-differential':
        ref = read(ROOT / 'evidence/m18/results.json')
        compare_counts(row, {'seed': 18, 'overlay_sha256': ref['title_overlay_sha256'],
                             'cases_per_base': cases, 'bases': ['elf-image', *captures]})
        need(row['functions'].keys() == ref['differential'].keys(), 'Wrong front-end function inventory')
        n = cases * len(row['bases'])
        for name, expected in ref['differential'].items():
            compare_counts(row['functions'][name], {'cases': n, 'mismatches': 0, 'failures': [],
                           'executed_words': expected['executed_words'], 'words': expected['words']})
        compare_counts(row, {'mismatches': 0, 'total_cases': n * len(row['functions'])})
    else:
        from verify_lifecycle_differential import MAIN_FRAME, SPECS
        compare_counts(row, {'seed': 17, 'cases_per_base': cases, 'bases': ['elf-image', *captures]})
        entries = {f'{entry:08x}': cases if any(spec.get(key) for key in ('cells', 'returns', 'args')) else 1
                   for entry, spec in SPECS.items()}
        entries[f'{MAIN_FRAME[0]:08x}'] = max(2, cases // 10)
        need(row['functions'].keys() == entries.keys() and len(row['inputs']) == len(captures)
             and all(sha(item['capture_sha256']) for item in row['inputs']), 'Wrong lifecycle differential input inventory')
        for entry, per_base in entries.items():
            r = row['functions'][entry]
            compare_counts(r, {'cases': per_base * len(row['bases']), 'failures': 0,
                               'first_failures': [], 'unexecuted': []})
            need(positive(r['words']) and exact(r['words'], r['executed_words']), 'Incomplete reachable word coverage')
    return {'functions': len(row['functions']), 'cases': sum(r['cases'] for r in row['functions'].values())}


def controls(kind, row, directory, read):
    measured = row['controls']
    if kind == 'front-end-controls':
        reference = read(ROOT / 'evidence/m18/results.json')['negative_controls']
        against(measured, reference, ('control', 'source', 'start', 'defect'))
        for item in measured:
            need(item['rejected'] is item['defect'] and exact(item['returncode'], int(item['defect']))
                 and bool(item['first_failure']) is item['defect'] and sha(item['source_sha256']),
                 'Front-end mutant lacks a measured divergence or clean null')
    elif kind == 'lifecycle-controls':
        from lifecycle_negative_controls import CONTROLS
        need(exact([r['control'] for r in measured], [c[0] for c in CONTROLS]), 'Wrong lifecycle control IDs')
        for item, (_, _, _, target, defect) in zip(measured, CONTROLS):
            need(item['rejected'] is defect and item['effective'] is True and exact(item['target'], f'{target:08x}')
                 and positive(item['cases']) and (positive(item['failures']) if defect else exact(item['failures'], 0))
                 and bool(item['first_failure']) is defect, 'Unproved lifecycle control')
    else:
        from verify_lifecycle_corridor_controls import CONTROLS
        need(exact([r['control'] for r in measured], [c[0] for c in CONTROLS]), 'Wrong corridor control IDs')
        for item, (_, old, _) in zip(measured, CONTROLS):
            expected = 'clean' if old is None else 'rejected'
            need(item['outcome'] == expected and bool(item['failures']) is (old is not None)
                 and item['effective'] is True, 'Unproved corridor control')
            for failure in item['failures']:
                if failure['stage'] == 'driver':
                    need(item['control'] in ('double-vblank-delivery', 'mode-pointer-not-refreshed')
                         and failure['error'].startswith('Lifecycle frame did not reach the next coordinator entry'),
                         'Unqualified corridor execution failure')
    return {'controls': len(measured)}


def content(row, read):
    iso = read(ROOT / 'evidence/local-inputs.json')['inputs']['HG_JAPAN_ISO']
    source = row['source']
    compare_counts(source, {'sha256': iso['sha256'], 'kind': 'iso-image', 'authenticated': True})
    compare_counts(row['coverage'], {'members_enumerated': 2541, 'hgscript_candidates': 74,
                   'hgscript_decoded': 74, 'hgscript_binding_unresolved': 48, 'failed_members': 0})
    members = row['members']
    scripts = row['scripts']
    need(len(members) == 2541 and len(scripts) == 74 and row['failures'] == []
         and all(r['roundtrip'] == 'decoded' for r in scripts)
         and sum(r['binding_status'] == 'unresolved' for r in scripts) == 48, 'Incomplete PACK/HGScript coverage')
    need(len({(r['dir'], r['index']) for r in members}) == 2541, 'Repeated PACK member')
    return {'members': len(members), 'scripts': len(scripts), 'unresolved_bindings': 48}


def audio(kind, row, directory, read):
    if kind == 'audio-instructions':
        from hg_audio.verify_audio import FUNCTIONS
        records = row['results']
        compare_counts(row, {'cases_per_function': 20, 'seed': 7})
        need(exact([(r['function'], r['case']) for r in records],
                   [(name, i) for name in FUNCTIONS for i in range(20)]), 'Wrong Audio case inventory')
        counts = {key: sum(r['status'] == status for r in records) for key, status in
                  (('passed', 'match'), ('failed', 'mismatch'), ('skipped', 'skipped'), ('excluded', 'excluded'))}
        need(sum(counts.values()) == len(records) and counts['passed'] == 407 and counts['excluded'] == 13 and not counts['failed']
             and not counts['skipped'] and all(r['reason'] == 'original would fault (unaligned access); excluded'
                for r in records if r['status'] == 'excluded'), 'Invalid Audio measurement or exclusion')
        compare_counts(row, counts)
        return counts
    from hg_audio.audio_runner import ISO_SHA256, KNOWN
    compare_counts(row, {'member': '6b51', 'iso_sha256': ISO_SHA256})
    stages = row['stages']
    need(exact([r['stage'] for r in stages], ['registration', 'selection', 'service', 'output', 'stop_release']),
         'Missing connected Audio stages')
    registration, selection, service, output, stop = stages
    spec = KNOWN[row['member']]
    compare_counts(registration, {'endpoint': hex(spec['endpoint'])})
    compare_counts(selection, {'play_index': spec['table1_index']})
    compare_counts(stop, {'stop_command_bytes': [0xff, 0x41, 0xf7], 'slot_state_after': 0})
    import wave
    with wave.open(str(directory / 'audio.wav'), 'rb') as wav:
        need(positive(output['samples_rendered']) and wav.getnframes() == output['samples_rendered']
             and wav.getframerate() == spec['sample_rate'] and wav.getnchannels() == 1
             and wav.getsampwidth() == 2 and len(wav.readframes(wav.getnframes())) == 2 * wav.getnframes()
             and positive(service['iop_payloads']) and sha(registration['member_sha256'])
             and sha(output['payload_sha256']), 'Unproved Audio transfer or WAV artifact')
    return {'stages': len(stages), 'samples': output['samples_rendered']}


def measurements(check, directory, log, code, output, read, elf):
    kind = check['result']
    need(code == 0, 'Extended verifier did not complete successfully; inspect attempt log and report')
    if kind == 'overlay-bytes':
        need(log.strip() == 'PASS: 2740 front-end instruction words and 4 tables match the original ELF/overlay',
             'Missing authenticated ELF/overlay counts')
        return {'words': 2740, 'tables': 4}
    row = read(output)
    if kind in SCHEMAS:
        need(row['schema'] == SCHEMAS[kind], 'Wrong extended verification schema')
    if kind in ELF_REPORTS:
        need(row['elf_sha256'] == elf, 'Wrong extended verifier ELF identity')
    if kind in STATUS_REPORTS:
        need(row['result'] == 'passed', 'Extended report disagrees with verifier status')
    if kind in BOOL_REPORTS:
        need(row['passed'] is True, 'Extended report disagrees with verifier status')
    if kind in ('continuous', 'predispatch', 'predispatch-contracts'):
        if kind == 'continuous':
            need(row['lifecycle'] is ('--lifecycle' in check['argv']), 'Wrong corridor mode')
        return corridor(kind, row, directory, read)
    if kind == 'counter-guard':
        c_artifact(directory, row)
        cases = row['cases']
        need(exact([(r['guard'], r['counter_before']) for r in cases],
                   [(0, 107998), (0, 107999), (1, 107999), (1, 108000)]), 'Missing counter guard cases')
        for item in cases:
            compare_counts(item, {'result': 'passed', 'difference_count': 0, 'first_differences': []})
            need(positive(item['instruction_count']) and item['expected'].keys() == {'0x46d930', '0x46e110', '0x46d908'}
                 and exact(item['expected'], item['actual']), 'Counter guard memory comparison incomplete')
        return {'cases': len(cases)}
    if kind in ('front-end-differential', 'lifecycle-differential'):
        return differential(kind, check, row, read)
    if kind in ('front-end-controls', 'lifecycle-controls', 'lifecycle-corridor-controls'):
        return controls(kind, row, directory, read)
    if kind == 'front-end-native':
        ref = read(ROOT / 'evidence/m18/results.json')['connected_run']
        compare_counts(row, {**{key: ref[key] for key in ('start_seq', 'end_seq', 'units', 'transitions')},
                            'start': 'title-menu-input-ready', 'failing_units': 0, 'failures': [], 'faults': [],
                            'requests': [dict(seq=q['seq'], entry=q['entry'], kind=q['kind'], directory=int(q['id'], 16))
                                         for q in ref['loader_requests']], 'external_fades': ref['external_fade_recoveries']})
        return {'frames': row['units'], 'loader_requests': len(row['requests'])}
    if kind == 'lifecycle-native':
        from verify_lifecycle_native import CONTROLS
        ref = read(ROOT / 'evidence/m17/results.json')['native_observations'][check['id']]
        entry = check['argv'][check['argv'].index('--entry') + 1]
        compare_counts(row, {'entry': entry, 'compared_bytes': ref['compared_bytes'],
                             'mismatches': [], 'errors': [], 'uncaptured_reads': [], 'unobserved_written': []})
        need(row['before']['sha256'] == ref['before_sha256'] and row['after']['sha256'] == ref['after_sha256'],
             'Wrong native observation')
        if entry == 'pack':
            compare_counts(row['pack'], {'c': 0xc6, 'native': 0xc6, 'address': '003f8e44'})
        measured = row['negative_controls']
        need(exact([c['control'] for c in measured], [c[0] for c in CONTROLS[entry]])
             and all(c['rejected'] is True and c['returncode'] == 1 and c['measurement'] for c in measured),
             'Missing native lifecycle control measurement')
        return {'bytes': row['compared_bytes'], 'controls': len(measured)}
    if kind == 'lifecycle-sequence':
        compare_counts(row, {'frames': 1500, 'faults': [],
                             'counters': {'frame_tick': 889, 'main_loop': 1500, 'vblank': 1500}})
        need(bool(row['states']) and bool(row['requests']), 'Missing synthetic lifecycle states or requests')
        return {'frames': row['frames'], 'requests': len(row['requests'])}
    if kind == 'content-corpus':
        return content(row, read)
    return audio(kind, row, directory, read)
