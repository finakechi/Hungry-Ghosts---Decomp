#!/usr/bin/env python3
"""Build, run, and independently verify the M26 audio timing & voice lifecycle.

Compiles tests/audio_timing_lifecycle.c against src/audio/{audio_engine,
audio_iop,audio_sequencer}.c (strict C11, optionally ASan+UBSan+LSan) in
the attempt build directory, runs it, independently re-derives every family's
multi-schedule logical parity, cycle-2 parity, resource accounting, audible
PCM output, and all 10 targeted controls, and compares byte-for-byte with
evidence/m26/timing-lifecycle.json.

No original retail input is read. Synthetic schedules and cues do not claim
retail equivalence.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'evidence/m26/timing-lifecycle.json'
SOURCES = (
    'tests/audio_timing_lifecycle.c',
    'src/audio/audio_engine.c',
    'src/audio/audio_iop.c',
    'src/audio/audio_sequencer.c',
)
FLAGS = ['-std=c11', '-Wall', '-Wextra', '-Werror', '-pedantic', '-ffp-contract=off']
SANITIZE = ['-g', '-O1', '-fno-omit-frame-pointer', '-fsanitize=address,undefined', '-fno-sanitize-recover=all']

EXPECTED_FAMILIES = (
    's1-short-timed-cue',
    's2-overlapping-owners',
    's3-exhaustion-contention',
    's4-pending-active-cancel',
    's5-release-retire-reuse',
    's6-bank-replace-reset-rebind',
    's7-irregular-bounds-malformed',
)
MULTI_SCHEDULE_FAMILIES = EXPECTED_FAMILIES[:5]
EXPECTED_SCHEDULES = ('unit-tick', 'coarse-2', 'irregular-zero')
EXPECTED_CONTROLS = {
    'nc-dropped-timing': 'timeline',
    'nc-reordered-events': 'event-order',
    'nc-missing-channel-consumption': 'channel-consumption',
    'nc-ownership-violation': 'ownership',
    'nc-missing-key-off': 'key-off',
    'nc-premature-reuse': 'premature-reuse',
    'nc-bad-cancel-retirement': 'retirement',
    'nc-bad-bank-retirement': 'bank-retirement',
    'nc-stale-reset': 'reset-cleanup',
    'nc-service-order-loss': 'service-order',
}
CYCLE_PARITY_KEYS = (
    'updates', 'flushes', 'decisions', 'key_on', 'key_off', 'voice_retire',
    'steal', 'drop', 'reject', 'stage_fail', 'cancel_active', 'cancel_pending',
    'lost', 'ring_records', 'spu_writes', 'channel_bytes', 'pcm_nonzero_frames',
    'render_frames', 'elapsed_ticks', 'logical_hash', 'pcm_fnv1a64',
    'iop_allocs', 'iop_frees', 'clean_end',
)
LOGICAL_SCHEDULE_KEYS = (
    'decisions', 'key_on', 'key_off', 'voice_retire', 'steal', 'drop',
    'reject', 'stage_fail', 'cancel_active', 'cancel_pending', 'lost',
    'ring_records', 'spu_writes', 'channel_bytes', 'elapsed_ticks',
    'logical_hash', 'clean_end',
)


def require(cond, msg):
    if not cond:
        raise ValueError(msg)


def check_report(report):
    """Independently re-derive M26 timing, schedule, ownership, and control invariants."""
    require(report['schema'] == 'hg-m26-audio-timing/v1', 'wrong schema')
    require(report['synthetic_only'] is True and report['retail_equivalence_claimed'] is False,
            'synthetic/retail claim flags invalid')
    require(report['ok'] is True, 'top-level ok flag is not true')

    families = report['families']
    require(tuple(f['name'] for f in families) == EXPECTED_FAMILIES, 'unexpected scenario family list')

    total_schedules = 0
    total_key_ons = 0
    for fam in families:
        name = fam['name']
        scheds = fam['schedules']
        if name in MULTI_SCHEDULE_FAMILIES:
            require(fam['multi_schedule'] is True and fam['schedule_logical_parity'] is True,
                    f'{name}: multi-schedule parity flag false')
            require(tuple(s['schedule'] for s in scheds) == EXPECTED_SCHEDULES,
                    f'{name}: expected schedules {EXPECTED_SCHEDULES}')
            base_c1 = scheds[0]['cycle1']
            for s in scheds[1:]:
                for k in LOGICAL_SCHEDULE_KEYS:
                    require(s['cycle1'][k] == base_c1[k],
                            f'{name}/{s["schedule"]}: logical field {k} disagrees with unit-tick')
            # Different schedules must actually exercise distinct service counts.
            require(len({s['cycle1']['updates'] for s in scheds}) >= 2,
                    f'{name}: schedules did not vary update counts')
        else:
            require(len(scheds) == 1, f'{name}: expected single lifecycle schedule')

        for s in scheds:
            total_schedules += 1
            require(s['harness_completed'] is True and s['failures'] == [],
                    f'{name}/{s["schedule"]}: failures {s["failures"]} ({s["detail"]})')
            c1, c2 = s['cycle1'], s['cycle2']
            for k in CYCLE_PARITY_KEYS:
                require(c1[k] == c2[k], f'{name}/{s["schedule"]}: cycle2 {k} != cycle1')
            require(c1['clean_end'] is True, f'{name}/{s["schedule"]}: end state not clean')
            require(c1['iop_allocs'] == c1['iop_frees'], f'{name}/{s["schedule"]}: staging leak')
            require(c1['key_on'] == c1['key_off'] > 0, f'{name}/{s["schedule"]}: key-on/off mismatch')
            require(c1['pcm_nonzero_frames'] > 0, f'{name}/{s["schedule"]}: zero rendered PCM')
            total_key_ons += c1['key_on']

    by_fam = {f['name']: f['schedules'][0]['cycle1'] for f in families}
    require(by_fam['s3-exhaustion-contention']['steal'] == 1
            and by_fam['s3-exhaustion-contention']['drop'] == 3,
            's3 steal/drop counts unexpected')
    require(by_fam['s4-pending-active-cancel']['cancel_pending'] == 1
            and by_fam['s4-pending-active-cancel']['cancel_active'] == 1
            and by_fam['s4-pending-active-cancel']['channel_bytes'] == 6,
            's4 pending/active cancellation or channel consumption unexpected')
    require(by_fam['s5-release-retire-reuse']['key_on'] == 36
            and by_fam['s5-release-retire-reuse']['voice_retire'] == 36,
            's5 wrap/reuse counts unexpected')
    require(by_fam['s6-bank-replace-reset-rebind']['lost'] == 2
            and by_fam['s6-bank-replace-reset-rebind']['iop_allocs'] == 2,
            's6 bank replacement/reset accounting unexpected')
    require(by_fam['s7-irregular-bounds-malformed']['reject'] == 3
            and by_fam['s7-irregular-bounds-malformed']['stage_fail'] == 1,
            's7 malformed rejection counts unexpected')

    controls = report['controls']
    require([c['name'] for c in controls] == list(EXPECTED_CONTROLS), 'unexpected control inventory')
    for ctrl in controls:
        exp = EXPECTED_CONTROLS[ctrl['name']]
        require(ctrl['harness_completed'] is True and ctrl['detected'] is True
                and ctrl['expected_failure'] == exp and exp in ctrl['failures'],
                f'control {ctrl["name"]} did not fail expected check {exp}: {ctrl["failures"]}')

    return {
        'families': len(families),
        'schedule_runs': total_schedules,
        'multi_schedule_families': len(MULTI_SCHEDULE_FAMILIES),
        'controls': len(controls),
        'cycle1_total_key_on': total_key_ons,
    }


def run(compiler, sanitize, build):
    with tempfile.TemporaryDirectory(prefix='m26-timing-', dir=build) as scratch:
        binary = Path(scratch) / 'audio_timing_lifecycle'
        flags = FLAGS + (SANITIZE if sanitize else ['-O2'])
        subprocess.run([compiler, *flags, '-I', str(ROOT / 'src'),
                        *[str(ROOT / s) for s in SOURCES], '-lm', '-o', str(binary)],
                       cwd=ROOT, check=True, timeout=120)
        env = {**os.environ, 'ASAN_OPTIONS': 'detect_leaks=1:halt_on_error=1',
               'UBSAN_OPTIONS': 'halt_on_error=1'}
        return subprocess.run([str(binary)], capture_output=True, env=env, timeout=120)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler', default=os.environ.get('CC', 'cc'))
    parser.add_argument('--sanitize', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build = Path(os.environ.get('HG_VERIFY_BUILD_DIR', ROOT / 'local/build'))
    build.mkdir(parents=True, exist_ok=True)
    proc = run(args.compiler, args.sanitize, build)
    stdout = proc.stdout
    report = json.loads(stdout)
    expected = EVIDENCE.read_bytes() if EVIDENCE.is_file() else b''
    try:
        summary, failure = check_report(report), None
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        summary, failure = None, str(exc)
    row = dict(
        schema='hg-m26-audio-timing-verification/v1',
        compiler=Path(args.compiler).name,
        sanitized=args.sanitize,
        exit_code=proc.returncode,
        stderr=proc.stderr.decode(errors='replace'),
        report_sha256=hashlib.sha256(stdout).hexdigest(),
        evidence_sha256=hashlib.sha256(expected).hexdigest(),
        matches_evidence=stdout == expected,
        summary=summary,
        failure=failure,
        report=report,
    )
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(row, stream, indent=2)
        stream.write('\n')
    ok = proc.returncode == 0 and not row['stderr'] and failure is None and row['matches_evidence']
    print(f'M26 audio timing: exit {proc.returncode}; checks '
          f'{"passed" if failure is None else "FAILED: " + failure}; receipt '
          f'{"matches" if row["matches_evidence"] else "DIFFERS FROM"} {EVIDENCE.relative_to(ROOT)}')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
