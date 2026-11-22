"""Interpret the M12 inventory verifiers' output contracts."""
from collections import Counter
import json
import re

from verify_player_ordinary import CONTROL_SCHEMA, m8_a1_zero_difference
from verify_resource_loader import EXPECTED

PASSED = 'passed'
FAILURE = 'verified-divergence'
REJECTED = 'expected-rejection'
ERROR = 'harness-error'
MISSING = 'missing-input'
INVALID = 'invalid-input'
REFUSED = 'expected-refusal'
WRONG_REFUSAL = 'unexpected-refusal'
SKIPPED = 'skipped'
INTERRUPTED = 'interrupted'
UNRUN = 'unrun'


def read_json(path):
    with path.open(encoding='utf-8') as stream:
        return json.load(stream)


def unittest_result(log, code):
    match = re.search(r'(?m)^Ran ([1-9][0-9]*) tests? in [0-9.]+s$', log)
    if match is None:
        raise ValueError('No completed synthetic test run')
    count = int(match[1])
    if code == 0 and log.rstrip().endswith('\nOK'):
        return PASSED, {'tests': count}
    if code == 1 and re.fullmatch(r'FAILED \(failures=[1-9][0-9]*\)', log.splitlines()[-1]):
        return FAILURE, {'tests': count, 'basis': 'synthetic assertion failure'}
    raise ValueError('Synthetic suite had errors, skipped cases or inconsistent exit status')


def structure_result(log, code):
    lines = log.splitlines()
    if code == 0 and len(lines) == 1 and re.fullmatch(r'OK .+: structurally valid \(NOT binary-verified\)', lines[0]):
        return PASSED, {'basis': 'PS2DInfo structural validation'}
    # The pinned validator prefixes each validation diagnostic with the input path.
    # Its I/O error handler uses the same prefix, so do not promote OS errors.
    if code == 1 and lines and all(line.startswith('FAIL ') and not re.search(r'\[Errno |\[WinError ', line)
                                for line in lines):
        return FAILURE, {'basis': 'PS2DInfo structural validation rejected the packet'}
    raise ValueError('No completed PS2DInfo structural verdict')


def byte_result(kind, log, code):
    if kind == 'ps2dinfo-bytes':
        if code == 0:
            row = json.loads(log)
            if (row['schema'], row['elf_sha256'], row['instructions_unknown']) == (
                    'ps2-elf-byte-check/v1', EXPECTED, 0) and positive(row['instructions_verified']):
                return PASSED, {'words': row['instructions_verified']}
        if code == 1 and re.fullmatch(
            r'FAIL: (byte mismatch at .+|instruction at .+ has unknown bytes|unmapped or ambiguous PT_LOAD at .+'
            r'|recorded endianness conflicts with ELF|recorded load address is not a file-backed PT_LOAD start'
            r'|ELF SHA-256 does not match the evidence record)', log.strip()
        ):
            return FAILURE, {'basis': log.strip()}
    else:
        match = re.fullmatch(r'PASS: ([1-9][0-9]*) function listings match original ELF bytes and JAL targets', log.strip())
        if code == 0 and match:
            return PASSED, {'functions': int(match[1])}
        if code == 1 and re.fullmatch(
            r'ValueError: (ELF mismatch [0-9a-f]{8}|JAL target disagreement|address not in exactly one file-backed PT_LOAD)',
            log.strip().split('\n')[-1],
        ):
            return FAILURE, {'basis': log.strip().split('\n')[-1]}
    raise ValueError('No completed original-byte verdict')


def ordinary_control(output, code):
    row = read_json(output)
    if row['schema'] != CONTROL_SCHEMA or row['elf_sha256'] != EXPECTED:
        raise ValueError('Wrong ordinary-player control result identity')
    if not output.with_suffix('.so').is_file() or not output.with_suffix('.so').stat().st_size:
        raise ValueError('Missing ordinary-player control build')
    verdict = row['result']
    if code == 0 and verdict == 'measured-divergence' and m8_a1_zero_difference(row['difference']):
        return REJECTED, {'basis': 'measured-divergence', 'trace': row['difference']}
    if code == 1 and verdict in ('equal', 'other-trace-divergence'):
        return FAILURE, {'basis': verdict, 'trace': row['difference']}
    raise ValueError('Ordinary-player control did not provide the required trace verdict')


def control_evidence(row):
    result = row['result']
    if result == 'error' or any(r['result'] == 'missing' for r in row.get('registers', [])):
        return ERROR, 'control execution or measurement error'
    kind = row['kind']
    if kind == 'fail-closed':
        if result == 'refused' and row['refusal_code'] == '0x50':
            return REFUSED, 'exact no-floor refusal (0x50)'
        if (result == 'unexpected-refusal' and row['refusal_code'] != '0x50'
                and re.fullmatch(r'0x[0-9a-f]+', row['refusal_code'])):
            return WRONG_REFUSAL, f'expected 0x50, got {row["refusal_code"]}'
        if result == 'ESCAPED':
            return FAILURE, 'expected refusal did not occur'
        raise ValueError('Missing exact airborne refusal verdict')

    final = row['comparison']
    stage = row['first_divergent_stage']
    counts = [final['different_bytes'], final['scratchpad']['different_bytes']]
    if stage is not None:
        counts += [stage['different_bytes'], stage['scratchpad']]
    if any(type(count) is not int or count < 0 for count in counts):
        raise ValueError('Invalid runtime memory difference count')
    memory = any(counts)
    measured = row['registers']
    if len(measured) != 3 or {r['register'] for r in measured} != {
        'floor-query-v0', 'floor-return-v0', 'constraint-entry-f12'
    } or any(
        r['result'] not in ('passed', 'failed')
        or not all(re.fullmatch('[0-9a-f]{8}', r[key]) for key in ('native', 'calculated'))
        or (r['result'] == 'passed') != (r['native'] == r['calculated']) for r in measured
    ):
        raise ValueError('Incomplete runtime control registers')
    registers = [r['register'] for r in measured if r['result'] == 'failed']
    if final['external_dma']['modeled_accesses']:
        return ERROR, 'control accessed the external DMA buffer'
    if kind == 'null-control':
        if result == 'clean' and not memory and not registers and final['result'] == 'passed':
            return PASSED, 'equal'
        if result == 'DIVERGED' and (memory or registers):
            return FAILURE, 'null control diverged'
        raise ValueError('Null control lacks a completed equality or divergence verdict')
    if result == 'ESCAPED':
        return FAILURE, 'mutation escaped'
    basis = (['memory'] if memory else []) + (['register'] if registers else [])
    if result != 'rejected' or not basis or row['rejected_by'] != basis:
        raise ValueError('Control rejection lacks its measured evidence')
    if row['control'] == 'floor-returns-two' and (basis, registers) != (['register'], ['floor-return-v0']):
        raise ValueError('Floor return control lacks register-only divergence')
    return REJECTED, '+'.join(basis)


def positive(value):
    return type(value) is int and value > 0


def output_path(check, directory):
    return directory / check.get('outputs', ['output.log', 'result.json'])[1]


def reported_verdict(row, code, verdict, details, success='passed'):
    if code != int(verdict not in (PASSED, REJECTED)) or row['result'] != (
            success if verdict in (PASSED, REJECTED) else 'failed'):
        raise ValueError('Verifier status disagrees with its measurements or exit status')
    return verdict, details


def runtime_result(check, directory, code):
    from verify_player_grounded_runtime import NATIVE, operation_checks, observation

    output = output_path(check, directory)
    row = read_json(output)
    if row['schema'] != 'hg-player-grounded-runtime/v1' or row['elf_sha256'] != EXPECTED:
        raise ValueError('Wrong grounded runtime result identity')
    for suffix in ('.so', '.baseline.json'):
        if not output.with_suffix(suffix).is_file():
            raise ValueError(f'Missing runtime artifact {output.with_suffix(suffix).name}')

    obs = observation()
    frames = obs['frames']
    expected_stages = {
        (f['counter'], calculated, f['captures'][native]['sha256'])
        for f in frames for native, names in NATIVE.items() for calculated in names
    } | {(f['counter'], f'push-{i}-{push["tier"]}', push['sha256'])
         for f in frames for i, push in enumerate(f['pushes'])}
    actual_stages = [(r['frame'], r['stage'], r['expected_sha256']) for r in row['stages']]
    actual_handoffs = [r['frame'] for r in row['handoffs']]
    expected_registers = {(f['counter'], name) for f in frames for name in
                          ('floor-query-v0', 'floor-return-v0', 'constraint-entry-f12')}
    actual_registers = [(r['frame'], r['register']) for r in row['registers']]
    if (len(actual_stages) != len(expected_stages) or set(actual_stages) != expected_stages
            or actual_handoffs != [f['counter'] for f in frames[1:]]
            or len(actual_registers) != len(expected_registers) or set(actual_registers) != expected_registers
            or operation_checks(obs, row['operation_values']) != row['operation_checks']):
        raise ValueError('Runtime result does not cover the observation or its operation checks')
    outcomes = [r['result'] for key in ('stages', 'handoffs', 'registers', 'status_invariance') for r in row[key]]
    error = any(result not in ('passed', 'failed') for result in outcomes)
    failed = 'failed' in outcomes or not all(row['operation_checks'].values())
    for stage in row['stages']:
        if stage['external_dma']['modeled_accesses']:
            failed = True

    controls_folder = output.parent / (output.stem + '-controls')
    control_results = {}
    expected_controls = check['controls']['negative_controls']
    for control in row['negative_controls']:
        name = control['control']
        if name in control_results or name not in expected_controls:
            raise ValueError('Invalid or repeated runtime control name')
        if read_json(controls_folder / (name + '.result.json')) != control:
            raise ValueError(f'Control artifact disagrees for {name}')
        expected_kind = {'clean': 'null-control', 'refused': 'fail-closed'}.get(expected_controls[name])
        if (expected_kind and control['kind'] != expected_kind or
                not expected_kind and control['kind'] not in ('compiled-defect', 'input-counterfactual')):
            raise ValueError(f'Wrong control kind for {name}')
        verdict, basis = control_evidence(control)
        control_results[name] = {'verdict': verdict, 'basis': basis, 'kind': control['kind']}
    if set(control_results) != set(expected_controls):
        raise ValueError('Runtime control coverage is incomplete')
    verdicts = {r['verdict'] for r in control_results.values()}
    details = {'stages': len(actual_stages), 'registers': len(actual_registers), 'handoffs': len(actual_handoffs),
               'native_targets': len({r['expected'] for r in row['stages']}), 'controls': control_results}
    if error or ERROR in verdicts:
        verdict = ERROR
    elif WRONG_REFUSAL in verdicts:
        verdict = WRONG_REFUSAL
    else:
        verdict = FAILURE if failed or FAILURE in verdicts else PASSED
    if verdict != PASSED:
        details['basis'] = 'Grounded comparison or control failed; see measurement and control receipts'
    return reported_verdict(row, code, verdict, details)


def grounded_differential(check, directory, code):
    from verify_player_grounded_differential import CONTROLS, control_verdict, tally

    output = output_path(check, directory)
    row = read_json(output)
    if (row['schema'] != check['report_schema'] or row['elf_sha256'] != EXPECTED
            or row['mode'] != 'equivalence' or not output.with_suffix('.so').is_file()):
        raise ValueError('Wrong grounded differential identity')
    cases = row['cases']
    requested = int(check['argv'][check['argv'].index('--cases') + 1])
    if [r['index'] for r in cases] != list(range(requested)) or tally(cases) != row['counts']:
        raise ValueError('Inconsistent grounded differential coverage')
    counts = row['counts']
    verdict = ERROR if counts['error'] else PASSED
    if verdict == PASSED and (counts['failures']['refusal-mismatch'] or counts['failures']['unsupported-branch-not-refused']):
        verdict = WRONG_REFUSAL
    elif verdict == PASSED and (counts['failed'] or not counts['passed']):
        verdict = FAILURE
    controls = row['negative_controls']
    required = {name: expect for name, expect, *_ in CONTROLS}
    if len(controls) != len(required) or {r['control']: r['expect'] for r in controls} != required:
        raise ValueError('Grounded differential control coverage does not match the configured mutations')
    results = {}
    requested_controls = int(check['argv'][check['argv'].index('--negative-controls') + 1])
    for control in controls:
        name = control['control']
        if control['result'] == 'error':
            verdict = ERROR
        else:
            nums = control['counts']
            if sum(nums[key] for key in ('passed', 'failed', 'unsupported', 'error')) != requested_controls:
                raise ValueError(f'Incomplete case count for {name}')
            measured = control_verdict(nums, control['expect'])
            if measured != control['result']:
                raise ValueError(f'Inconsistent differential control {name}')
            if measured != 'rejected' and verdict == PASSED:
                verdict = FAILURE
        results[name] = control['result']
    details = {'counts': counts, 'controls': results,
               'expected_refusals': counts['unsupported'], 'equal_cases': counts['passed']}
    if verdict != PASSED:
        details['basis'] = 'Grounded instruction differential or its mutation controls failed'
    return reported_verdict(row, code, verdict, details)


def log_failure(log):
    last = log.rstrip().rsplit('\n', maxsplit=1)[-1]
    if last.startswith('FileNotFoundError:'):
        return MISSING, {'basis': last}
    if re.match(r'(ValueError|AssertionError): (Wrong original |Wrong ELF|Wrong capture frame|'
                r'(?:Observation/capture|Screenshot|Base capture) digest mismatch|Debugger artifact changed|'
                r'Capture (?:identity|digest|build) mismatch)', last, re.I):
        return INVALID, {'basis': last}
    if last.startswith('UnexpectedRefusal:') or 'C path outside reconstructed domain (code ' in last:
        return WRONG_REFUSAL, {'basis': last}
    if (re.search(r'AssertionError: .+: (?:external call trace differs|memory differs at|memory mismatch at)', log)
            or re.match(r'AssertionError: (?:lifecycle mismatch |[Cc]allee-saved register mismatch|'
                        r'Allocation return mismatch|Compiled defect survived: |.+ defect escaped)', last)):
        return FAILURE, {'basis': last}
    return ERROR, {'basis': last or 'Verifier exited without a completed result'}


def measured_control(control):
    if control.get('error') or control.get('execution_error'):
        return False
    if control.get('reason'):
        return True  # Typed mismatch or the specific assertion named by the standalone verifier.
    if 'comparison' in control:
        comparison = control['comparison']
        return (not comparison.get('external_dma', {}).get('modeled_accesses') and
                any(positive(value) for value in (comparison['different_bytes'],
                    comparison.get('scratchpad', {}).get('different_bytes'))))
    return bool(control.get('failed_stages') or control.get('failed_checks') or positive(control.get('different_words')))


def legacy_report(check, directory, log, code):
    output = output_path(check, directory)
    if not output.is_file():
        if code:
            return log_failure(log)
        raise ValueError('Verifier exited without writing its required report')
    row = read_json(output)
    if row.get('schema') != check.get('report_schema') or check.get('report_elf') and row.get('elf_sha256') != EXPECTED:
        raise ValueError('Wrong verifier report schema or ELF identity')
    details = {}
    failure = ERROR if row['result'] not in ('passed', 'failed') else None
    for key, required in check.get('controls', {}).items():
        controls = row[key]
        if any(r.get('error') or r.get('execution_error') or r.get('result') == 'error' for r in controls):
            return ERROR, {'basis': 'A negative control had an execution error'}
        if len(controls) != len(required) or {r['control'] for r in controls} != required.keys():
            raise ValueError('Negative control inventory mismatch')
        for control in controls:
            if control['result'] == 'ESCAPED':
                failure = FAILURE
            elif control['result'] != required[control['control']] or not measured_control(control):
                raise ValueError(f'Unproved negative-control rejection for {control["control"]}')
        details['controls'] = {r['control']: r['result'] for r in controls}

    for key in ('stages', 'handoffs', 'cases', 'status_invariance', 'input_counterfactuals', 'original_camera_model'):
        data = row.get(key)
        if not isinstance(data, list) or not data or not all(isinstance(r, dict) and 'result' in r for r in data):
            continue
        if any(r.get('execution_error') or r['result'] == 'error' for r in data):
            return ERROR, {'basis': f'{key} contain an execution error'}
        outcomes = Counter(r['result'] for r in data)
        if outcomes['failed'] and failure not in (ERROR, WRONG_REFUSAL):
            failure = (WRONG_REFUSAL if key == 'cases' and any(r.get('failure') == 'refusal-mismatch' for r in data)
                       else FAILURE)
        details[key] = len(data)
        if key == 'stages':
            details['equal_stages'] = outcomes['passed']
        if outcomes['unsupported']:
            details['expected_refusals'] = outcomes['unsupported']
    if 'counts' in row:
        details['counts'] = row['counts']
        if row['counts'].get('error'):
            return ERROR, {'basis': 'Differential cases contain an execution error', **details}
    operation_checks = row.get('operation_checks')
    if operation_checks and not all(operation_checks.values()):
        failure = FAILURE
    if 'failed_closed' in row and type(row['failed_closed']) is int:
        details['expected_refusals'] = row['failed_closed']
    if failure:
        details['basis'] = 'One or more measured comparisons failed or a mutation escaped'
    if row['result'] == 'failed' and not failure:
        return ERROR, {'basis': 'Report did not substantiate its failure'}
    verdict = failure or (REJECTED if check['expected'] == REJECTED else PASSED)
    return reported_verdict(row, code, verdict, details)


def classify(check, directory, code):
    if code not in (0, 1):
        return ERROR, {'basis': f'Unexpected child exit status {code}'}
    try:
        kind = check['result']
        if kind == 'ordinary-control':
            path = output_path(check, directory)
            if not path.is_file() and code:
                return log_failure((directory / 'output.log').read_text(errors='replace'))
            return ordinary_control(path, code)
        if kind == 'runtime':
            return runtime_result(check, directory, code)
        if kind == 'grounded-differential':
            return grounded_differential(check, directory, code)
        log = (directory / 'output.log').read_text(encoding='utf-8', errors='replace')
        if kind == 'unittest':
            return unittest_result(log, code)
        if kind == 'structure':
            return structure_result(log, code)
        if kind.endswith('bytes'):
            return byte_result(kind, log, code)
        if kind in ('report', 'legacy-controls'):
            return legacy_report(check, directory, log, code)
        if kind == 'audit':
            row = read_json(output_path(check, directory))
            if row['schema'] == check['report_schema'] and isinstance(row['findings'], list):
                if code == 0 and row['result'] == 'no-heuristic-findings' and not row['findings']:
                    return PASSED, {'findings': 0}
                if code == 1 and row['result'] == 'review-required' and row['findings']:
                    return FAILURE, {'basis': 'Publication audit requires review', 'findings': len(row['findings'])}
        if kind == 'local-inputs':
            row = json.loads(log)
            statuses = {r['name']: r['status'] for r in row['inputs']}
            if row['schema'] == 'ps2-local-input-check/v1' and statuses.keys() == {'HG_ELF', 'HG_JAPAN_ISO'}:
                if code == 0 and set(statuses.values()) == {'verified'}:
                    return PASSED, {'inputs': statuses}
                if code:
                    return INVALID, {'basis': 'Original media identity rejected', 'inputs': statuses}
        if kind == 'sanitizer-build' and code == 0 and positive((directory / 'sanitizer').stat().st_size):
            return PASSED, {'basis': 'Compiled strict C11 with ASan/UBSan'}
        if kind == 'sanitizer-run':
            if code == 0 and re.search(r'(?m)^PASS: synthetic M1-M7 .+ M12 .+ under sanitizers$', log):
                return PASSED, {'basis': 'Executed integration assertions under ASan/UBSan with leak detection'}
            if re.search(r'(?m)(ERROR: (?:AddressSanitizer|LeakSanitizer): .+|runtime error: .+)', log):
                return FAILURE, {'basis': 'Sanitizer reported a defect'}
        if kind in ('git-diff', 'compileall') and code == 0 and not log.strip():
            return PASSED, {'basis': 'No whitespace errors' if kind == 'git-diff' else 'Python sources compiled'}
        if kind == 'git-diff' and code == 1 and re.search(r'(?m)^.+:[0-9]+: (?:trailing whitespace|new blank line at EOF|space before tab)', log):
            return FAILURE, {'basis': 'Git reported a diff whitespace error'}
        return log_failure(log)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, RecursionError) as exc:
        return ERROR, {'basis': str(exc)}
