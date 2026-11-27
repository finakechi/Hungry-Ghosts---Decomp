"""Coverage anchors for historical report producers.

Anchors select stable workload identifiers and measurements from tracked sanitized
records. They do not provide execution inputs or replace a fresh verifier result.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# A tuple selects the identifying fields of every row of a measurement list.
ANCHORS = {
    '02': ('m4/m2-differential.json', {'cases_per_function': (), 'randomized_invocations': (),
                                     'end_to_end_sequences': (), 'alias_cases': ()}),
    '03': ('m4/m3-differential.json', {'cases': (), 'entry': ()}),
    '04': ('m6/differential.json', {'cases': (), 'total_cases': (), 'm6_cases': (), 'm6_total_cases': ()}),
    '05': ('m5/differential.json', {'cases': (), 'total_cases': (), 'loader_differential_status': ()}),
    '07': ('m7/instruction-comparison.json', {'cases_per_function': (), 'connected_workers': (),
                                            'instruction_addresses_executed': ()}),
    '08': ('m8/instruction-comparison.json', {'cases_per_function': (), 'instruction_addresses_executed': ()}),
    '09': ('m4/runtime-comparison.json', {'debugger_sequence': ('pc', 'registers'),
                                        'full_member_comparison': (), 'member_size': ()}),
    '10': ('m5/runtime-comparison.json', {'debugger_sequence': ('event', 'pc', 'registers')}),
    '12': ('m7/runtime-comparison.json', {'checkpoints': (), 'operation': (), 'image_sha256': ()}),
    '13': ('m8/setup-instruction-comparison.json', {'cases_per_function': (), 'instruction_addresses_executed': ()}),
    '14': ('m8/setup-runtime-comparison.json', {'captures': (), 'compared_ranges': ('address', 'size_bytes', 'sha256')}),
    '15': ('m8/ordinary-instruction-comparison.json', {'cases': (), 'instruction_addresses': ()}),
    '16': ('m8/connected-instruction-comparison.json', {'complete_bodies': ('entry', 'name', 'cases', 'result'),
                                                       'complete_body_cases': (), 'instruction_addresses': ()}),
    '17': ('m8/connected-transition-instruction-comparison.json', {'cases': (), 'instruction_addresses': (),
                                     'functions': ('entry', 'name', 'cases', 'instruction_addresses', 'result')}),
    '18': ('m8/ordinary-input-runtime-comparison.json', {'cases': ('case', 'edge_mask', 'compared_bytes',
                                                  'before_sha256', 'after_sha256', 'transition_calls')}),
    '19': ('m8/connected-gameplay-runtime-comparison.json', {}),
    '55': ('m9/player-turn-instruction-differential.json', {'functions': ('entry', 'name', 'compared', 'failed_closed',
                            'complete_function'), 'cases': (), 'failed_closed': (),
                            'partial_exclusions': (), 'static_exclusions': (), 'executed_addresses': ()}),
    '56': ('m9/player-turn-runtime-comparison.json', {'handoffs': ('frame',)}),
    '62': ('m10/dispatcher-instruction-differential.json', {'cases': (), 'executed_addresses': ()}),
    '63': ('m10/player-walk-runtime-comparison.json', {'stages': ('frame', 'stage', 'expected'), 'handoffs': ('frame',)}),
    '64': ('m10/dependency-survey.json', {'representative_frames': ('frame',)}),
    '69': ('m11/constraint-runtime-comparison.json', {'stages': ('frame', 'stage'),
                                    'handoffs': ('frame',), 'status_invariance': ('frame',)}),
}


def exact(actual, expected):
    """JSON equality with scalar types preserved."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(exact(actual[key], value) for key, value in expected.items())
    if isinstance(expected, (list, tuple)):
        return len(actual) == len(expected) and all(exact(a, b) for a, b in zip(actual, expected))
    return actual == expected


def legacy_coverage(check, row, read_json, elf_sha256):
    number = check.get('historical')
    if number in ANCHORS:
        name, fields = ANCHORS[number]
        expected = read_json(ROOT / 'evidence' / name)
        for field, keys in fields.items():
            actual, anchor = row[field], expected[field]
            if keys:
                actual = [{key: item[key] for key in keys} for item in actual]
                anchor = [{key: item[key] for key in keys} for item in anchor]
            if not exact(actual, anchor):
                raise ValueError(f'Historical {check["id"]} coverage differs for {field}')
    required_bytes = {
        '03': 'byte_check', '04': 'byte_check', '05': 'byte_check',
        '07': 'original_bytes', '08': 'original_bytes', '12': 'original_bytes',
        '13': 'original_bytes', '15': 'original_bytes', '16': 'original_bytes',
        '17': 'original_bytes', '21': 'original_bytes', '55': 'original_bytes',
    }
    if number in required_bytes and required_bytes[number] not in row:
        raise ValueError('Missing nested original-byte report')
    if number == '16' and (row['selected_state0']['cases'] != 128
                           or not row['selected_state0']['addresses']):
        raise ValueError('Missing selected equipment-state coverage')
    if number == '10' and row['m6_processing']['result'] != 'passed':
        raise ValueError('Missing M6 PACK observation acceptance')
    for field in ('original_bytes', 'byte_check'):
        if field in row:
            byte = row[field]
            if (byte['schema'] != 'ps2-elf-byte-check/v1' or byte['elf_sha256'] != elf_sha256
                    or not exact(byte['instructions_unknown'], 0)
                    or type(byte['instructions_verified']) is not int or byte['instructions_verified'] <= 0):
                raise ValueError('Invalid nested original-byte verification')
    if number == '19':
        expected = read_json(ROOT / 'evidence/m8/connected-gameplay-runtime-comparison.json')
        actual_cases, expected_cases = row['cases'], expected['cases']
        if len(actual_cases) != len(expected_cases) or [c['case'] for c in actual_cases] != [c['case'] for c in expected_cases]:
            raise ValueError('Historical connected-player-saved-runtime case inventory differs')
        for actual_case, expected_case in zip(actual_cases, expected_cases):
            if actual_case['case'] == 'connected-gameplay-sequence':
                if not exact(actual_case['gameplay_checks'], expected_case['gameplay_checks']):
                    raise ValueError('Historical connected-player-saved-runtime gameplay_checks differ')
                actual_stages, expected_stages = actual_case['stages'], expected_case['stages']
                if len(actual_stages) != len(expected_stages):
                    raise ValueError('Historical connected-player-saved-runtime sequence stages differ')
                for st_a, st_e in zip(actual_stages, expected_stages):
                    if st_a['stage'] != st_e['stage'] or st_a['result'] != 'passed':
                        raise ValueError(f'Historical connected-player-saved-runtime sequence stage invalid: {st_a.get("stage")}')
                    for field in ('compared_bytes', 'before_sha256', 'after_sha256', 'capture_sha256'):
                        if field in st_e and not exact(st_a[field], st_e[field]):
                            raise ValueError(f'Historical connected-player-saved-runtime sequence {field} differs')
                    if 'different_bytes' in st_e and not exact(st_a['different_bytes'], 0):
                        raise ValueError('Historical connected-player-saved-runtime sequence stage has nonzero differences')
                    if 'external_dma' in st_e and st_a['external_dma']['modeled_accesses']:
                        raise ValueError('Historical connected-player-saved-runtime sequence stage accessed external DMA')
            else:
                for field in ('compared_bytes', 'before_sha256', 'after_sha256'):
                    if not exact(actual_case[field], expected_case[field]):
                        raise ValueError(f'Historical connected-player-saved-runtime {field} differs for {actual_case["case"]}')
    if number == '56':
        expected = read_json(ROOT / 'evidence/m9/player-turn-runtime-comparison.json')
        actual_stages, expected_stages = row['stages'], expected['stages']
        if len(actual_stages) != len(expected_stages) or [s['stage'] for s in actual_stages] != [s['stage'] for s in expected_stages]:
            raise ValueError('Historical turn-camera-saved-runtime stage inventory differs')
        for st_a, st_e in zip(actual_stages, expected_stages):
            if st_a['stage'] == 'release5-update':
                if (st_a['expected'] != st_e['expected'] or not exact(st_a['turn_entry_hits'], 1)
                        or not exact(st_a['turn_entry_probe'], {'result': 'passed', 'different_bytes': 0, 'scratch_different_bytes': 0})
                        or st_a['stick'] != '0x0' or st_a['player_reconstructed']['rate_440'] != '0x0'):
                    raise ValueError('Historical turn-camera-saved-runtime release5-update probe invalid')
            else:
                for field in ('entry_sha256', 'expected_sha256'):
                    if not exact(st_a[field], st_e[field]):
                        raise ValueError(f'Historical turn-camera-saved-runtime {field} differs for {st_a["stage"]}')
    for stage in row.get('stages', []):
        if stage['stage'] == 'release5-update' and number == '56':
            if stage['result'] == 'passed' and (
                    not exact(stage['turn_entry_hits'], 1)
                    or stage['turn_entry_probe']['result'] != 'passed'
                    or not exact(stage['turn_entry_probe']['different_bytes'], 0)
                    or not exact(stage['turn_entry_probe']['scratch_different_bytes'], 0)):
                raise ValueError('Turn-camera release5-update pass contradicts probe measurements')
        elif stage['stage'].startswith('foot-query-register-') and number == '69':
            if stage['result'] == 'passed' and stage['calculated'] != stage['original_v0_low']:
                # The original field is fixed-width while hex() omits padding.
                if int(stage['calculated'], 16) != int(stage['original_v0_low'], 16):
                    raise ValueError('Foot-query register mismatch')
        elif stage['result'] == 'passed':
            if (type(stage['compared_bytes']) is not int or stage['compared_bytes'] <= 0
                    or not exact(stage['different_bytes'], 0)
                    or not exact(stage['scratchpad']['different_bytes'], 0)
                    or stage['external_dma']['modeled_accesses']):
                raise ValueError('Stage pass contradicts memory or external-access measurements')
    if number == '70':
        cases = row['cases']
        wanted = int(check['argv'][check['argv'].index('--cases') + 1])
        if not exact([case['index'] for case in cases], list(range(wanted))):
            raise ValueError('Constraint case inventory mismatch')
        expected = {name: sum(case['result'] == name for case in cases)
                    for name in ('passed', 'failed', 'unsupported', 'error')}
        if (sum(expected.values()) != wanted or not exact(row['counts'], expected) or not expected['passed']
                or any((case.get('refusal_code') != '0x40' or case.get('original_special') is not True) for case in cases if case['result'] == 'unsupported')):
            raise ValueError('Invalid constraint outcome tally or refusal identity')
    if number == '77':
        obs = read_json(ROOT / 'evidence/m11/constraint-observation.json')
        labels = ('constraint-entry', 'constraint-return', 'history-copy', 'camera-entry', 'camera-return',
                  'bump-sound-call')
        wanted = [(frame['counter'], label, frame['captures'][label]['sha256'])
                  for frame in obs['frames'] for label in labels if label in frame['captures']]
        actual = [(item['frame'], item['stage'], item['expected_sha256']) for item in row['stages']]
        if actual != wanted or [item['frame'] for item in row['handoffs']] != [
                frame['counter'] for frame in obs['frames'][1:]]:
            raise ValueError('M11 frame replay coverage is incomplete')
