"""Report contracts for the M19-M24 and M23-M24 integration gates.

Each contract validates the verifier's measured report against tracked
evidence (exact equality where the milestone established exact receipts),
re-derives summary measurements from raw reports, and requires every
declared control to be detected. A zero exit status or a success field is
never sufficient on its own.
"""
from pathlib import Path

from hg_verify_legacy_contracts import exact

ROOT = Path(__file__).resolve().parents[1]
ELF = 'a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61'
ISO = '8ca1513c8417c0a40b838ff5edf22a2360f43bce1b0458693a95a6efdafd994e'

KINDS = {
    'm19-resource-job', 'm19-resource-retail', 'm19-connected', 'm20-hgscript-differential',
    'm20-title-hgscript', 'm21-title-scene', 'm21-area-entry', 'm21-ferry-reload', 'm22-retail-check',
    'm23-authentic-boundary', 'm23-world-init-synthetic', 'm24-reference-map', 'm24-sequencer',
    'world-audio', 'c-drivers',
}
# Synthetic C gates whose build and output paths stay inside the attempt
# directory and HG_VERIFY_BUILD_DIR (checked by tests/test_hg_verify.py).
PARALLEL = {'m23-world-init-synthetic', 'm24-sequencer', 'world-audio', 'c-drivers'}


def need(condition, basis):
    if not condition:
        raise ValueError(basis)


def same(row, reference, basis, ignore=()):
    keys = (row.keys() | reference.keys()) - set(ignore)
    differing = sorted(key for key in keys if not exact(row.get(key), reference.get(key)))
    need(not differing, f'{basis}: {", ".join(differing[:6])} differ from tracked evidence')


def reference(check, read):
    return read(ROOT / check['reference'])


def m19(kind, row, directory, read):
    evidence = read(ROOT / 'evidence/m19/results.json')
    if kind == 'm19-resource-job':
        same(row, evidence['differential'], 'M19 normal-worker differential')
        need(row['schema'] == 'hg-m19-job-differential/v1' and row['elf_sha256'] == ELF and row['controls'] and all(
            c['rejected'] is True and c['failure'] and len(c['source_sha256']) == 64 for c in row['controls']),
            'M19 job controls were not all rejected with measured failures')
        return {'jobs': len(row['jobs']), 'controls': len(row['controls'])}
    if kind == 'm19-resource-retail':
        need(row['schema'] == 'hg-m19-retail-checkpoints/v1' and row['iso_sha256'] == ISO and row['elf_sha256'] == ELF and row['result'] == 'passed',
             'M19 retail identity or verdict')
        same({'cold_title': row['cold_title'], 'M4': row['bootstrap']['checks'], 'M7': row['area']['checks']},
             {'cold_title': evidence['cold_title'], 'M4': evidence['checkpoint_domains']['M4'],
              'M7': evidence['checkpoint_domains']['M7']}, 'M19 cold title and checkpoint domains')
        return {'cold_title_frames': row['cold_title']['frames'],
                'domains': len(row['bootstrap']['checks']) + len(row['area']['checks'])}
    summary = evidence['connected']
    derived = dict(
        first=row['first'], last=row['last'], units=row['units'],
        request_and_exit_matches=row['original_operations']['compared_completions'],
        logical_disc_setups=sum(len(v) for v in row['setups'].values()), physical_transfers=len(row['disc_reads']),
        ordered_requests=[[q['kind'], q['directory']] for q in row['requests']],
        frame_indexed_lifecycle_matches=row['lifecycle_matches'], frame_indexed_loader_matches=row['task_matches'],
        worker_faults=len(row['faults']))
    same(derived, {key: summary[key] for key in derived}, 'M19 connected cold-title summary')
    need(row['iso_sha256'] == ISO and row['elf_sha256'] == ELF and row['result'] == 'passed'
         and row['faults'] == [] and row['disc_fault'] is None and row['original_operations']['passed'] is True
         and row['original_operations']['sampled_requests'] == row['original_operations']['model_requests'] == 11,
         'M19 connected run identity, faults or original operations')
    same({'M18': row['checkpoints']}, {'M18': evidence['checkpoint_domains']['M18']}, 'M19 M18 checkpoint domains')
    need(all(d['differing_bytes'] == 0 for c in row['checkpoints'] for d in c['domains']),
         'M19 checkpoint domain differs')
    library = directory / 'lifecycle.so'
    need(library.is_file() and library.stat().st_size > 0, 'Missing compiled connected library artifact')
    return {'units': row['units'], 'requests': len(row['requests']), 'checkpoints': len(row['checkpoints'])}


def m20(kind, check, row, read):
    if kind == 'm20-hgscript-differential':
        evidence = read(ROOT / 'evidence/m20/differential.json')
        same(row, evidence, 'M20 HGScript differential', ignore=('negative_controls',))
        need(row['schema'] == 'hg-m20-differential/v1' and row['elf_sha256'] == ELF and row['iso_sha256'] == ISO,
             'M20 differential identity')
        measured, recorded = row['negative_controls'], evidence['negative_controls']
        need(list(measured) == list(recorded) and all(
            measured[name].startswith(recorded[name] + ': ') and 'differ' in measured[name] for name in recorded),
            'M20 negative control divergence not measured at the recorded tick')
        return {'sequences': len(row['sequences']), 'controls': len(measured)}
    evidence = reference(check, read)
    same(row, evidence, 'M19-M20 connected title script', ignore=('source_revision',))
    need(row['schema'] == 'hg-title-script-integration/v1' and len(row['source_revision']) == 40,
         'Missing title identity or source revision')
    return {'frames': row['runtime']['frames'], 'glyph_requests': row['runtime']['requests']['0x182850']}


def m21(kind, check, row, directory, read):
    evidence = reference(check, read)
    if kind == 'm21-ferry-reload':
        same(row, evidence, 'M21 ferry reload', ignore=('schema', 'iso_sha256', 'elf_sha256'))
        need(row['schema'] == 'hg-m21-ferry-reload/v1' and row['iso_sha256'] == ISO and row['elf_sha256'] == ELF
             and row['confirmations'] == 1 and all(d['match'] is True and d['diff'] == [] for d in row['domains'].values())
             and len(row['domains']) == 7, 'M21 ferry reload identity, prompt or domain match')
        return {'units': row['units'], 'native_vblanks': row['native_vblanks'], 'domains': len(row['domains'])}
    same(row, evidence, 'M21 scene coordination receipt')
    need(row['iso_sha256'] == ISO and row['elf_sha256'] == ELF, 'M21 receipt identity')
    if kind == 'm21-area-entry':
        need(row['schema'] == 'hg-m21-area-entry/v1', 'M21 area entry schema')
        return {'units': row['driver_units'], 'domains': len(row['domains'])}
    if '--negative-controls' in check['argv']:
        need(row['controls'] and all(row['controls'].values()), 'M21 title controls missing a measured divergence')
    need(row['schema'] == 'hg-m21-title-scene/v1', 'M21 title scene schema')
    return {'frames': row['frames'], 'requests': len(row['requests'])}


def m24(kind, check, row, directory, read):
    if kind == 'm24-reference-map':
        same(row, read(ROOT / 'evidence/m24/reference-map.json'), 'M24 reference map')
        need(row['elf_sha256'] == ELF, 'M24 reference map ELF identity')
        return {'functions': len(row['functions']), 'scheduler_registrations': len(row['scheduler_registrations'])}
    evidence = read(ROOT / 'evidence/m24/sequencer.json')
    need(row['schema'] == 'hg-m24-sequencer-verification/v1' and row['exit_code'] == 0 and row['stderr'] == ''
         and row['sanitized'] is ('--sanitize' in check['argv']) and row['matches_evidence'] is True,
         'M24 sequencer build, exit or receipt identity')
    same(row['report'], evidence, 'M24 sequencer receipt')
    scenarios = row['report']['scenarios']
    positive = [s for s in scenarios if not s['expected_failure']]
    controls = [s for s in scenarios if s['expected_failure']]
    need(row['report']['ok'] is True and len(positive) == 9 and len(controls) == 5
         and all(s['harness_completed'] and s['failures'] == [] and s['pcm_nonzero_frames'] > 0 for s in positive)
         and all(s['harness_completed'] and s['expected_failure'] in s['failures'] for s in controls),
         'M24 scenarios or controls not proved')
    return {'scenarios': len(positive), 'controls': len(controls)}


def synthetic(kind, check, row, directory, read):
    if kind == 'm23-world-init-synthetic':
        evidence = read(ROOT / 'evidence/m23/synthetic-lifecycle.json')
        same(row, evidence, 'M23 synthetic lifecycle', ignore=('compiler', 'sanitized'))
        need(row['sanitized'] is True and row['synthetic_only'] is True and row['retail_equivalence_claimed'] is False
             and len(row['negative_controls']) == 10, 'M23 synthetic run not sanitized or controls missing')
        return {'controls': len(row['negative_controls']), 'cycle2_parity': True}
    if kind == 'world-audio':
        from verify_world_audio import check_report
        evidence = read(ROOT / 'evidence/m23-m24/world-audio-lifecycle.json')
        need(row['schema'] == 'hg-world-audio-verification/v1' and row['exit_code'] == 0 and row['stderr'] == ''
             and row['sanitized'] is ('--sanitize' in check['argv']) and row['failure'] is None
             and row['matches_evidence'] is True, 'Connected lifecycle build, exit or receipt identity')
        same(row['report'], evidence, 'Connected world/audio receipt')
        return check_report(row['report'], read(ROOT / 'evidence/m23/synthetic-lifecycle.json'))
    from verify_c_drivers import DRIVERS
    need(row['schema'] == 'hg-c-driver-sanitizers/v1' and list(row['drivers']) == list(DRIVERS)
         and all(d['exit_code'] == 0 and not d['sanitizer_report'] and d['stderr'] == '' and d['stdout_bytes'] == 0
                 for d in row['drivers'].values()), 'C driver failed or reported a sanitizer defect')
    need(row['canary']['detected'] is True and row['canary']['exit_code'] != 0,
         'Sanitizer canary was not detected; instrumentation unproved')
    return {'drivers': len(row['drivers']), 'canary': 'detected'}


def measurements(check, directory, row, read):
    kind = check['result']
    if kind.startswith('m19-'):
        return m19(kind, row, directory, read)
    if kind.startswith('m20-'):
        return m20(kind, check, row, read)
    if kind.startswith('m21-'):
        return m21(kind, check, row, directory, read)
    if kind == 'm22-retail-check':
        same(row, read(ROOT / 'evidence/m22/retail-check.json'), 'M22 retail service comparison')
        need(row['result'] == 'pass' and row['instruction_negative_controls']
             and all(row['instruction_negative_controls'].values()), 'M22 verdict or instruction controls')
        return {'instruction_sequences': len(row['instruction_sequences']),
                'controls': len(row['instruction_negative_controls'])}
    if kind == 'm23-authentic-boundary':
        same(row, read(ROOT / 'evidence/m23/authentic-boundary.json'), 'M23 authentic boundary')
        need(row['elf_sha256'] == ELF and row['iso_sha256'] == ISO, 'M23 boundary identity')
        return {'checkpoints': len(row['checkpoints'])}
    if kind.startswith('m24-'):
        return m24(kind, check, row, directory, read)
    return synthetic(kind, check, row, directory, read)
