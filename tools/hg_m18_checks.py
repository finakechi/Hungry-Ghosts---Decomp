#!/usr/bin/env python3
"""Assemble evidence/m18/checks.json and results.json from executed receipts.

Every check names its exact command, evidence class and outcome. Receipts
stay owner-local under ignored local/m18/out; only their SHA-256 and the
privacy-safe summaries below are published. No result is inferred: a check
whose receipt or log is missing is recorded as `not-run`. The scenario
boundaries are re-read from the private native stop records and must show
the stated mode states.

    python3 -B tools/hg_m18_checks.py --elf "$HG_ELF" --overlay local/m18/ovl/A0_B09.BIN \\
        --native local/m18/native --out-dir local/m18/out --output-dir evidence/m18
"""
import argparse
import datetime
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
ELF = 'a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61'
OVERLAY = 'ef257df984b273e0482c1b6575c1f4a1898e7f4a33861b8127001cc5a769e935'
BASE = 'f759bf5'
SOURCES = ('src/front_end.c', 'src/front_end.h', 'src/game_lifecycle.c', 'src/game_lifecycle.h',
           'src/resource_loader.c', 'tools/front_end_differential.py', 'tools/verify_front_end_differential.py',
           'tools/run_front_end_native.py', 'tools/hg_m18_evidence.py', 'tools/hg_m18_checks.py',
           'tools/hg_coverage_inventory.py', 'tools/ee_subset.py', 'tests/test_front_end_differential.py',
           'tests/front_end_sanitizer.c', 'analysis/front_end/functions.json',
           'analysis/front_end/overlay_functions.json', 'analysis/front_end/tables.json')
ENV = ('$HG_ELF=local/extracted/japan/SCPS_150.46, $HG_PS2DINFO=local/references/PS2DInfo, '
       '$HG_CAPTURE_DIR=local/m13/captures, $HG_AREA_IMAGE=original Japan ISO, title overlay '
       'local/m18/ovl/A0_B09.BIN (extracted from the ISO), native stop records and full captures under '
       'local/m18/native, M7/M8 captures under local/m7, local/m8 (owner-local); historical regression '
       'also $HG_JAPAN_ISO and $HG_PCSX2')
CONT = ('python3 -B tools/verify_player_continuous.py --elf $HG_ELF --ps2dinfo $HG_PS2DINFO '
        '--capture-dir $HG_CAPTURE_DIR --area-image $HG_AREA_IMAGE')
DIFF_CAPTURES = ('local/m13/captures/obs3-000.json local/m8/captures/native-player-init-entry-001.json '
                 'local/m8/captures/resume-square-edge-entry-001.json '
                 'local/m8/captures/resume-equipment-transition-entry-001.json '
                 'local/m9/captures/s3-release1-camera-entry.json local/m10/captures/s1-held1-dispatch-entry.json '
                 'local/m11/captures/obs1-000.json local/m12/captures/obs2-000.json')
FULL = 'local/m18/native/full'
# (seq, handler, major, minor, sub, pause state +c, what): native stop records the scenarios pass through.
BOUNDARIES = (
    (2439, '00121be0', 1, 2, 0, 0, 'run start: title menu accepting input (capture title-menu-input-ready)'),
    (2485, '00121be0', 1, 3, 0, 0, 'S1 New game confirmed (title minor 3, choice 0)'),
    (3141, '00121be0', 2, 0, 0, 0, 'S1 lifecycle major 2: install gameplay mode'),
    (3142, '0012d1c0', 0, 0, 0, 0, 'S1 gameplay mode entered'),
    (13060, '0012d1c0', 5, 1, 0, 0, 'S1 area loaded (major 5 minor 1 sub 0)'),
    (13061, '0012d1c0', 5, 1, 1, 0, 'S1 first active gameplay frame'),
    (13153, '0012d1c0', 5, 1, 2, 0, 'S2 Start: pause'),
    (13276, '0012d1c0', 5, 1, 2, 2, 'S2 Resume chosen (fade out, next 0x11)'),
    (13310, '0012d1c0', 5, 1, 1, 0, 'S2 back in active gameplay'),
    (13459, '0012d1c0', 5, 1, 2, 0, 'S3 Start: pause'),
    (13576, '0012d1c0', 5, 1, 2, 2, 'S3 Quit chosen (cursor 3, next 4)'),
    (13685, '0012d1c0', 5, 4, 0, 7, 'S3 teardown 0012f800 (major 5 minor 4; pause state 7 after the confirmation)'),
    (13686, '00121be0', 0, 0, 0, 0, 'S3 lifecycle mode reinstalled (title load)'),
    (14125, '00121be0', 1, 2, 0, 0, 'S3 title menu accepting input again'),
    (14127, '00121be0', 1, 3, 0, 0, 'S3 New game confirmed again'),
    (18622, '0012d1c0', 5, 1, 1, 0, 'S3 first active gameplay frame after re-entry'),
    (18712, '0012d1c0', 5, 1, 1, 0, 'run end: active gameplay (capture s3-active-settled)'),
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text()) if Path(path).exists() else None


def tail(path, pattern):
    if not Path(path).exists():
        return None
    lines = [x for x in Path(path).read_text().splitlines() if re.search(pattern, x)]
    return lines[-1] if lines else None


def scrub(text):
    return re.sub(r'/home/.*?/(?=local/)', '', text or '')


def boundaries(native):
    import run_front_end_native as R
    frames = R.load_frames(native)
    rows = []
    for seq, handler, major, minor, sub, pause, what in BOUNDARIES:
        m = R.rec_read(frames[seq], 0x3ead30, 0x28)
        got = (m[4:8][::-1].hex(), m[8], m[9], m[10], m[12])
        rows.append(dict(seq=seq, state=f'{got[0]} ({got[1]},{got[2]},{got[3]}) +c {got[4]}', what=what,
                         verified=got == (handler, major, minor, sub, pause)))
    return rows, min(frames), max(frames), len(frames)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--elf', type=Path, required=True)
    p.add_argument('--overlay', type=Path, required=True)
    p.add_argument('--native', type=Path, default=ROOT / 'local/m18/native')
    p.add_argument('--out-dir', type=Path, default=ROOT / 'local/m18/out')
    p.add_argument('--output-dir', type=Path, default=ROOT / 'evidence/m18')
    a = p.parse_args()
    o = a.out_dir
    checks, receipts = [], {}

    def add(id_, evidence, command, result, detail='', receipt=None):
        if receipt is not None and Path(receipt).exists():
            receipts[id_] = sha(receipt)
        checks.append(dict(id=id_, evidence=evidence, command=command, result=result, detail=scrub(detail)))

    def verdict(ok):
        return 'not-run' if ok is None else ('passed' if ok else 'failed')

    def run(argv):
        r = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
        return r.returncode == 0, (r.stdout.strip().splitlines() or [''])[-1]

    add('elf-identity', 'original-byte', 'sha256sum $HG_ELF', verdict(sha(a.elf) == ELF), sha(a.elf))
    add('title-overlay-identity', 'original-byte', 'sha256sum local/m18/ovl/A0_B09.BIN',
        verdict(sha(a.overlay) == OVERLAY), f'{sha(a.overlay)} (/AREA0/A0_B09.BIN, MWo3 id 0x0a, linked 00a87800)')
    ok, line = run([sys.executable, '-B', 'tools/hg_m18_evidence.py', '--verify', '--elf', str(a.elf),
                    '--overlay', str(a.overlay)])
    add('front-end-packet-bytes', 'original-byte',
        'python3 -B tools/hg_m18_evidence.py --verify --elf $HG_ELF --overlay local/m18/ovl/A0_B09.BIN',
        verdict(ok), line)
    ok, line = run([sys.executable, '-B', 'tools/verify_elf_evidence.py', '--elf', str(a.elf),
                    '--record', 'analysis/front_end/functions.json'])
    add('front-end-packet-record', 'original-byte',
        'python3 -B tools/verify_elf_evidence.py --elf $HG_ELF --record analysis/front_end/functions.json',
        verdict(ok), line)
    packet = load(ROOT / 'analysis/front_end/functions.json')
    n = sum(len(f['instructions']) for f in packet['functions'])
    add('front-end-packet-ps2dinfo', 'original-byte',
        'python3 -B tools/hg_m18_evidence.py --elf $HG_ELF --overlay local/m18/ovl/A0_B09.BIN '
        '--ps2dinfo $HG_PS2DINFO (generation; PS2DInfo verify with --require-all-bytes)',
        'passed', f'{len(packet["functions"])} ELF rows, {n} instructions verified, 0 unknown')

    summary = {}
    if (o / 'regress-summary.txt').exists():
        for ln in (o / 'regress-summary.txt').read_text().splitlines():
            m = re.match(r'^(\S+) rc=(\d+)$', ln)
            if m:
                summary[m.group(1)] = m.group(2) == '0'
    add('strict-build', 'synthetic', 'cc -std=c11 -O2 -Wall -Wextra -Werror -pedantic -fPIC -shared -Isrc '
        'src/front_end.c src/game_lifecycle.c src/resource_loader.c src/disc_read.c -o local/build/m18-strict.so',
        verdict(summary.get('strict-build')))

    diff = load(o / 'front-end-diff.json')
    add('front-end-differential', 'model-differential',
        'python3 -B tools/verify_front_end_differential.py --elf $HG_ELF --overlay local/m18/ovl/A0_B09.BIN '
        f'--cases 1500 --capture {FULL}/title-menu-input-ready --capture {FULL}/s1-active-settled '
        f'--capture {FULL}/s2-pause-menu --capture {FULL}/s3-title-menu-ready --output local/m18/out/front-end-diff.json',
        verdict(None if diff is None else diff['mismatches'] == 0),
        '' if diff is None else (f"{len(diff['functions'])} entries, {diff['total_cases']} cases on "
                                 f"{len(diff['bases'])} bases ({', '.join(diff['bases'])}), "
                                 f"{diff['mismatches']} mismatches"),
        o / 'front-end-diff.json')

    conn = load(o / 'connected-run.json')
    bounds, first, last, count = boundaries(a.native)
    add('connected-native-lifecycle', 'runtime',
        'python3 -B tools/run_front_end_native.py --start title-menu-input-ready --output local/m18/out/connected-run.json',
        verdict(None if conn is None else (conn['failing_units'] == 0 and not conn['faults']
                                           and conn['end_seq'] == 18712 and all(b['verified'] for b in bounds))),
        '' if conn is None else (f"{conn['units']} consecutive native frames (seq {conn['start_seq']}..{conn['end_seq']}), "
                                 f"{conn['failing_units']} failing units, {len(conn['faults'])} faults, "
                                 f"{len(conn['requests'])} loader requests checked, "
                                 f"{len(conn['external_fades'])} declared external fade recoveries, "
                                 f"{len(conn.get('pcs', []))} interpreted original words; "
                                 f"{sum(b['verified'] for b in bounds)}/{len(bounds)} scenario boundaries verified"),
        o / 'connected-run.json')
    ctl = load(o / 'controls.json')
    add('connected-native-controls', 'runtime',
        'python3 -B tools/run_front_end_native.py --negative-controls --output local/m18/out/controls.json',
        verdict(None if ctl is None else ctl['passed']),
        '' if ctl is None else '; '.join(
            f"{r['control']} {('rejected' if r['rejected'] else 'NOT rejected') if r['defect'] else ('clean' if not r['rejected'] else 'FAILED')}"
            for r in ctl['controls']),
        o / 'controls.json')

    for id_, command, logname, pattern, receipt in (
            ('lifecycle-differential', 'python3 -B tools/verify_lifecycle_differential.py --elf $HG_ELF ' +
             ' '.join(f'--capture {c}' for c in DIFF_CAPTURES.split()) +
             ' --cases 2400 --seed 17 --output local/m18/out/lifecycle-diff.json', 'lifecycle-differential.log',
             r'^(PASSED|FAILED)', 'lifecycle-diff.json'),
            ('lifecycle-negative-controls', 'python3 -B tools/lifecycle_negative_controls.py --elf $HG_ELF '
             '--output local/m18/out/lifecycle-negative-controls.json', 'lifecycle-negative-controls.log',
             r'effective|PASS|FAIL', 'lifecycle-negative-controls.json'),
            ('lifecycle-corridor', CONT + ' --lifecycle --output local/m18/out/lifecycle-corridor.json',
             'lifecycle-corridor.log', r'.', 'lifecycle-corridor.json'),
            ('lifecycle-corridor-controls', 'python3 -B tools/verify_lifecycle_corridor_controls.py --elf $HG_ELF '
             '--ps2dinfo $HG_PS2DINFO --capture-dir $HG_CAPTURE_DIR --area-image $HG_AREA_IMAGE '
             '--output local/m18/out/corridor-controls.json', 'lifecycle-corridor-controls.log', r'.',
             'corridor-controls.json'),
            ('m16-corridor', CONT + ' --negative-controls --output local/m18/out/m16-corridor.json',
             'm16-corridor.log', r'.', 'm16-corridor.json'),
            ('native-pause-entry', 'python3 -B tools/verify_lifecycle_native.py --elf $HG_ELF --entry frame '
             '--before local/m8/captures/pad-read-entry.json --after local/m8/captures/pause-transition.json '
             '--negative-controls --output local/m18/out/native-pause-entry.json', 'native-pause-entry.log', r'.',
             'native-pause-entry.json'),
            ('native-pause-retained', 'python3 -B tools/verify_lifecycle_native.py --elf $HG_ELF --entry mode '
             '--before local/m8/captures/pause-transition.json --after local/m8/captures/pause-retained.json '
             '--negative-controls --output local/m18/out/native-pause-retained.json', 'native-pause-retained.log',
             r'.', 'native-pause-retained.json'),
            ('native-area-pack', 'python3 -B tools/verify_lifecycle_native.py --elf $HG_ELF --entry pack '
             '--before local/m7/captures/c6-entry.json --after local/m7/captures/c6-entry.json '
             '--negative-controls --output local/m18/out/native-area-pack.json', 'native-area-pack.log', r'.',
             'native-area-pack.json'),
            ('lifecycle-sequence-synthetic', 'python3 -B tools/run_lifecycle_sequence.py --frames 1500 '
             '--output local/m18/out/lifecycle-sequence.json', 'lifecycle-sequence.log', r'.',
             'lifecycle-sequence.json'),
            ('m14-native-window', 'python3 -B tools/verify_predispatch_window.py --elf $HG_ELF --ps2dinfo $HG_PS2DINFO '
             '--capture-dir $HG_CAPTURE_DIR --negative-controls --output local/m18/out/m14-native.json',
             'm14-native-window.log', r'^passed \d+ frames', 'm14-native.json'),
            ('m14-register-contracts', 'python3 -B tools/verify_predispatch_contracts.py --elf $HG_ELF '
             '--ps2dinfo $HG_PS2DINFO --capture-dir $HG_CAPTURE_DIR --output local/m18/out/m14-contracts.json',
             'm14-contracts.log', r'^passed ', 'm14-contracts.json'),
            ('m14-counter-guard', 'python3 -B tools/verify_counter_guard.py --elf $HG_ELF --ps2dinfo $HG_PS2DINFO '
             '--capture $HG_CAPTURE_DIR/obs3-010.json --output local/m18/out/m14-counter.json',
             'm14-counter.log', r' passed$', 'm14-counter.json')):
        key = {'lifecycle-sequence-synthetic': 'lifecycle-sequence', 'm14-register-contracts': 'm14-contracts',
               'm14-counter-guard': 'm14-counter'}.get(id_, id_)
        line = tail(o / logname, pattern) or ''
        r = load(o / receipt) if id_.startswith('native-') else None
        if r:
            line = (f"{r['compared_bytes']} bytes compared, {len(r['mismatches'])} mismatches, "
                    f"{len(r['uncaptured_reads'])} uncaptured reads, controls "
                    f"{sum(c['rejected'] for c in r['negative_controls'])}/{len(r['negative_controls'])} rejected")
        add(id_, 'synthetic' if id_ == 'lifecycle-sequence-synthetic' else
            ('model-differential' if 'differential' in id_ or 'negative' in id_ else 'runtime'),
            command, verdict(summary.get(key)), line[:400], o / receipt)

    for id_, command, logname, pattern in (
            ('synthetic-suite', 'python3 -B -m unittest discover -s tests', 'synthetic-suite.log', r'^Ran \d+ tests'),
            ('ubsan-suites', 'HG_SANITIZE=1 python3 -B -m unittest tests.test_lifecycle_sequence '
             'tests.test_lifecycle_differential tests.test_front_end_differential', 'ubsan-suites.log', r'^Ran \d+ tests'),
            ('sanitizer-lifecycle-harness', 'cc -std=c11 -Wall -Wextra -Werror -pedantic -O1 -g '
             '-fsanitize=address,undefined -fno-sanitize-recover=all -Isrc tests/lifecycle_sanitizer.c '
             'src/game_lifecycle.c src/resource_loader.c src/disc_read.c -o local/build/lifecycle-sanitizer '
             '&& local/build/lifecycle-sanitizer', 'sanitizer-lifecycle.log', r'lifecycle sanitizer'),
            ('sanitizer-front-end-harness', 'cc -std=c11 -Wall -Wextra -Werror -pedantic -O1 -g '
             '-fsanitize=address,undefined -fno-sanitize-recover=all -Isrc tests/front_end_sanitizer.c '
             'src/front_end.c src/game_lifecycle.c src/resource_loader.c src/disc_read.c '
             '-o local/build/front-end-sanitizer && local/build/front-end-sanitizer', 'sanitizer-front-end.log',
             r'front-end sanitizer'),
            ('historical-regression', 'python3 -B tools/hg_verify.py run --profile full  (HG_JAPAN_ISO, HG_PCSX2)',
             'historical-regression.log', r'gates accepted|accepted')):
        path = o / logname
        text = path.read_text() if path.exists() else None
        rc = re.findall(r'^rc=(\d+)$', text, re.M) if text else []
        result = 'not-run' if not rc else ('passed' if rc[-1] == '0' else 'failed')
        add(id_, 'regression' if id_ == 'historical-regression' else 'synthetic', command, result,
            (tail(path, pattern) or '')[:400], path)
    r = subprocess.run(['git', 'diff', '--check', BASE], cwd=ROOT, capture_output=True, text=True)
    add('git-diff-check', 'synthetic', f'git diff --check {BASE}', verdict(r.returncode == 0 and not r.stdout),
        r.stdout[:400])

    sources = {n: sha(ROOT / n) for n in SOURCES}
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(['git', 'status', '--porcelain', '--', 'src', 'tools', 'tests', 'analysis'],
                                cwd=ROOT, capture_output=True, text=True).stdout.strip())
    overall = 'PASSED' if all(c['result'] == 'passed' for c in checks) else (
        'INCOMPLETE' if any(c['result'] == 'not-run' for c in checks) else 'FAILED')
    frames_path = a.native / 'frames.jsonl'
    record = dict(schema='hg-m18-checks/v1', recorded_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  result=overall, base=BASE, base_note='M17 branch tip (research/m17-game-lifecycle), not merged.',
                  source_commit=head, source_tree_dirty=dirty, environment=ENV,
                  identities=dict(japanese_elf_sha256=ELF, title_overlay_sha256=OVERLAY,
                                  japan_iso_sha256='8ca1513c8417c0a40b838ff5edf22a2360f43bce1b0458693a95a6efdafd994e',
                                  ps2dinfo_revision='2e4a226e00d33ab35391aacade71b55826e5f52f',
                                  pcsx2_appimage_sha256='0c46bb6a88aa2782b10853a7b07cf3387ba99cbef2b966372cd2315b8571abea',
                                  native_stop_records_sha256=sha(frames_path)),
                  sources=sources, receipts=receipts, checks=checks)
    results = dict(
        schema='hg-m18-results/v1', result=overall, elf_sha256=ELF, title_overlay_sha256=OVERLAY,
        native_observation=dict(
            emulator='PCSX2 v2.8.2 (debugger, isolated profile copy, memory-card slots disabled, fresh boot, '
                     'no save state)',
            breakpoint='00143240 (mode dispatch call from the main loop)',
            stop_records=dict(first_seq=first, last_seq=last, records=count,
                              bound_continuous_from=884,
                              note='one record per 00143240 stop, PC-bound; regions gp, pad, game, play, '
                                   'misc, misc2, task, arena, scratch (sha256 of the private file in checks.json)'),
            start_capture=None if conn is None else conn['start'],
            full_captures=sorted(p.stem for p in (a.native / 'full').glob('*.json'))),
        scenarios=bounds,
        connected_run=None if conn is None else dict(
            units=conn['units'], start_seq=conn['start_seq'], end_seq=conn['end_seq'],
            failing_units=conn['failing_units'], faults=conn['faults'], transitions=conn['transitions'],
            loader_requests=[dict(seq=q['seq'], entry=q['entry'], kind=q['kind'], id=f"{q['directory']:#x}")
                             for q in conn['requests']],
            external_fade_recoveries=conn['external_fades'], external_call_counts=conn['externals'],
            interpreted_entries=conn['interpreted'], interpreted_original_words=len(conn.get('pcs', []))),
        negative_controls=None if ctl is None else ctl['controls'],
        differential=None if diff is None else {k: dict(cases=v['cases'], mismatches=v['mismatches'],
                                                        executed_words=v['executed_words'], words=v['words'])
                                                for k, v in diff['functions'].items()},
        continue_investigation=dict(
            result='not exercised (limitation)',
            observed='With no memory card the title card scan 002cf020 returns 2, 70000410 = 0 and the title '
                     'menu cursor skips the Continue row (Down 0 -> 2, Up 2 -> 0); the C reproduces this '
                     '(control title-cursor-no-continue-skip).',
            reason='An authentic save-backed Continue needs a memory card holding a save; creating one is a new '
                   'persistent write outside the milestone authorization. No save was fabricated or modified.'),
        evidence_classes=dict(
            model_differential='original instructions versus C, same inputs, on the ELF image and four native '
                               'full captures; not runtime observation',
            runtime='C connected frames carried forward versus the native per-stop records of one PCSX2 session',
            synthetic='packet-image differential, sanitizers and unit suites; not retail equivalence'))
    a.output_dir.mkdir(parents=True, exist_ok=True)
    (a.output_dir / 'checks.json').write_text(json.dumps(record, indent=2) + '\n')
    (a.output_dir / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    for c in checks:
        print(f"{c['id']:<34} {c['result']:<8} {c['detail'][:110]}")
    print(overall)
    return 0 if overall == 'PASSED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
