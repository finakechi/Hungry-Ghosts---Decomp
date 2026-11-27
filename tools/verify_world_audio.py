#!/usr/bin/env python3
"""Verify the M23-M24 connected original synthetic world/audio lifecycle.

Builds tests/world_audio_lifecycle.c against the M23 world harness and the
M22/M24 audio sources (strict C11, optionally ASan+UBSan+LSan) in the
attempt build directory, runs it, and independently checks its report:

  * the connected world trajectory equals the standalone M23 run in the same
    process and the committed M23 synthetic lifecycle checkpoints;
  * both cycles produce non-zero output while the player moves, use only
    model-made note/voice decisions, and end with no requests, voices,
    commands, channels, envelopes, staging allocations, bank binding, actors
    or dynamic handles left;
  * the second cycle is identical to the first and a repeated run is identical;
  * every control completes and fails in its intended check;
  * the whole report equals evidence/m23-m24/world-audio-lifecycle.json.

No original input is read. The 60 Hz / 48 kHz schedule is supplied by the
harness and says nothing about retail EE thread timing or service order.
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
EVIDENCE = ROOT / 'evidence/m23-m24/world-audio-lifecycle.json'
M23 = ROOT / 'evidence/m23/synthetic-lifecycle.json'
SOURCES = ('tests/world_audio_lifecycle.c', 'src/scene/world_init.c', 'src/front_end.c',
           'src/game_lifecycle.c', 'src/resource_loader.c', 'src/disc_read.c',
           'src/audio/audio_engine.c', 'src/audio/audio_iop.c', 'src/audio/audio_sequencer.c')
FLAGS = ['-std=c11', '-Wall', '-Wextra', '-Werror', '-pedantic', '-ffp-contract=off']
SANITIZE = ['-g', '-O1', '-fno-omit-frame-pointer', '-fsanitize=address,undefined', '-fno-sanitize-recover=all']
CONTROLS = {
    'nc-missing-world-init': 'world-init',
    'nc-missing-bank-registration': 'audio-init',
    'nc-missing-sequence-ticks': 'timeline',
    'nc-flush-before-sequence-update': 'key-on',
    'nc-early-bank-release': 'teardown-order',
    'nc-stale-audio-teardown': 'audio-end-state',
    'nc-stale-world-teardown': 'world-reentry',
    'nc-foreign-voice-release': 'key-off',
    'nc-audio-writes-world-memory': 'memory-ownership',
    'nc-world-writes-audio-memory': 'audio-memory-integrity',
}
CLEAN_END = {'active_requests': 0, 'owned_voices': 0, 'busy_commands': 0, 'open_channels': 0,
             'sounding_envelopes': 0, 'bank_slot_state': 0, 'actors': 0, 'free_nodes': 2048,
             'dynamic_handles': 0, 'unknown_calls': 0}
WORLD_FIELDS = ('frame', 'player_pos', 'camera_eye', 'camera_target', 'yaw', 'speed', 'main_actors',
                'type2_actors', 'free_nodes', 'wall_contacts', 'masked_ee_fnv1a64')
CYCLE_INVARIANT = ('frames', 'updates', 'events', 'accepted', 'events_by_cue', 'decisions', 'key_on',
                   'key_off', 'steal', 'drop', 'complete', 'cancel', 'ring_records', 'spu_writes',
                   'pcm_nonzero_frames', 'audible_moving_updates', 'pcm_fnv1a64', 'spu_fnv1a64',
                   'decision_fnv1a64', 'world_fnv1a64', 'iop_allocs', 'iop_frees', 'end_state')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_report(report, m23):
    """Independent measurement checks; raises ValueError on the first failure."""
    require(report['schema'] == 'hg-world-audio-lifecycle/v1' and report['synthetic_only'] is True
            and report['retail_equivalence_claimed'] is False, 'Wrong report identity or claim')
    require(report['schedule']['source'] == 'external-harness'
            and report['schedule']['sequencer_ticks_per_world_frame'] == 1, 'Schedule is not declared external')
    connected, standalone = report['connected'], report['standalone']
    require(connected['failures'] == [] and connected['stray_audio_writes'] == 0, 'Connected run failed a check')
    require(report['positive_passed'] is True and report['repeat_run_identical'] is True and report['ok'] is True,
            'Positive, repeat or control verdict missing')
    cycles = connected['cycles']
    require(len(cycles) == 2 and len(standalone['cycles']) == 2, 'Two lifecycle cycles required')
    for index, (cycle, alone) in enumerate(zip(cycles, standalone['cycles'])):
        require(cycle['end_state'] == CLEAN_END, f'Cycle {index} left stale world or audio state')
        require(cycle['iop_allocs'] == cycle['iop_frees'] == 1, f'Cycle {index} staging allocation not released')
        require(cycle['lost'] == 0 and cycle['stage_fail'] == 0 and cycle['rejected'] == 0,
                f'Cycle {index} lost, refused or rejected requests')
        require(cycle['pcm_nonzero_frames'] > 0 and cycle['audible_moving_updates'] > 0,
                f'Cycle {index} produced no output alongside movement')
        require(cycle['key_on'] == cycle['key_off'] > 0 and cycle['cancel'] == 1
                and cycle['complete'] == cycle['accepted'] - 1, f'Cycle {index} voices or requests did not retire')
        require(cycle['events_by_cue']['ambient'] == 1 and cycle['events_by_cue']['chime'] == 1
                and cycle['events_by_cue']['step'] > 0 and cycle['events_by_cue']['bump'] > 0,
                f'Cycle {index} lacks overlapping synthetic events')
        for name, point in cycle['checkpoints'].items():
            other = alone['checkpoints'][name]
            require(all(point[key] == other[key] for key in WORLD_FIELDS),
                    f'Cycle {index} {name}: connected world differs from standalone M23')
        require(alone['events'] == 0 and alone['pcm_nonzero_frames'] == 0, 'Standalone run used audio')
    first, second = cycles
    require(all(first[key] == second[key] for key in CYCLE_INVARIANT), 'Second cycle differs from the first')
    reference = m23['cycle1']
    points = first['checkpoints']
    for field, checkpoint in (('walk_pos', 'after_walk'), ('turn_pos', 'after_turn'), ('wall_pos', 'after_wall'),
                              ('release_pos', 'after_release')):
        require(points[checkpoint]['player_pos'] == reference[field], f'{checkpoint} differs from M23 {field}')
    require(points['after_release']['camera_eye'] == reference['release_camera_eye']
            and points['after_release']['camera_target'] == reference['release_camera_target']
            and points['after_turn']['yaw'] == reference['turn_yaw']
            and points['after_wall']['wall_contacts'] == reference['wall_contacts'],
            'Carried camera, yaw or wall contacts differ from M23')
    controls = report['controls']
    require([c['name'] for c in controls] == list(CONTROLS), 'Wrong control inventory')
    for control in controls:
        require(control['harness_completed'] is True and control['detected'] is True
                and control['expected_failure'] == CONTROLS[control['name']]
                and CONTROLS[control['name']] in control['failures'],
                f'Control {control["name"]} was not detected by its intended check')
    return {'cycles': 2, 'frames_per_cycle': first['frames'], 'events_per_cycle': first['events'],
            'key_on_per_cycle': first['key_on'], 'steal_per_cycle': first['steal'],
            'pcm_nonzero_frames_per_cycle': first['pcm_nonzero_frames'], 'controls': len(controls)}


def run(compiler, sanitize, build):
    with tempfile.TemporaryDirectory(prefix='m23-m24-world-audio-', dir=build) as scratch:
        binary = Path(scratch) / 'world_audio_lifecycle'
        flags = FLAGS + (SANITIZE if sanitize else ['-O2'])
        subprocess.run([compiler, *flags, '-I', str(ROOT / 'src'), *[str(ROOT / s) for s in SOURCES],
                        '-lm', '-o', str(binary)], cwd=ROOT, check=True, timeout=180)
        env = {**os.environ, 'ASAN_OPTIONS': 'detect_leaks=1:halt_on_error=1', 'UBSAN_OPTIONS': 'halt_on_error=1'}
        return subprocess.run([str(binary), '--json'], capture_output=True, env=env, timeout=600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler', default=os.environ.get('CC', 'cc'))
    parser.add_argument('--sanitize', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build = Path(os.environ.get('HG_VERIFY_BUILD_DIR', ROOT / 'local/build'))
    build.mkdir(parents=True, exist_ok=True)
    proc = run(args.compiler, args.sanitize, build)
    report = json.loads(proc.stdout)
    expected = EVIDENCE.read_bytes() if EVIDENCE.is_file() else b''
    try:
        summary, failure = check_report(report, json.loads(M23.read_text())), None
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        summary, failure = None, str(exc)
    row = dict(schema='hg-world-audio-verification/v1', compiler=Path(args.compiler).name,
               sanitized=args.sanitize, exit_code=proc.returncode, stderr=proc.stderr.decode(errors='replace'),
               report_sha256=hashlib.sha256(proc.stdout).hexdigest(),
               evidence_sha256=hashlib.sha256(expected).hexdigest(),
               matches_evidence=proc.stdout == expected, summary=summary, failure=failure, report=report)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(row, stream, indent=2)
        stream.write('\n')
    ok = proc.returncode == 0 and not row['stderr'] and failure is None and row['matches_evidence']
    print(f'Connected world/audio lifecycle: exit {proc.returncode}; checks '
          f'{"passed" if failure is None else "FAILED: " + failure}; receipt '
          f'{"matches" if row["matches_evidence"] else "DIFFERS FROM"} {EVIDENCE.relative_to(ROOT)}')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
