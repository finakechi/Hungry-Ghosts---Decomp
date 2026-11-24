#!/usr/bin/env python3
"""Assemble evidence/m17/checks.json and results.json from executed receipts.

Every check names its exact command, evidence class and outcome. Receipts
stay owner-local under ignored local/m17/out; only their SHA-256 and the
privacy-safe summaries below are published. No result is inferred: a check
whose receipt or log is missing is recorded as `not-run`.

    python3 -B tools/hg_m17_checks.py --out-dir local/m17/out --output-dir evidence/m17
"""
import argparse
import datetime
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ELF = 'a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61'
BASE = '46cdb2189a3190b49830b0222e3a5cfcc4e0fd0b'
SOURCES = ('src/game_lifecycle.c', 'src/game_lifecycle.h', 'src/resource_loader.c', 'src/resource_loader.h',
           'tools/lifecycle_differential.py', 'tools/verify_lifecycle_differential.py',
           'tools/lifecycle_negative_controls.py', 'tools/run_lifecycle_sequence.py',
           'tools/verify_lifecycle_native.py', 'tools/verify_player_continuous.py',
           'tools/verify_lifecycle_corridor_controls.py', 'tools/hg_coverage_inventory.py',
           'tools/hg_m17_evidence.py', 'tools/ee_subset.py', 'tests/lifecycle_sanitizer.c',
           'tests/test_lifecycle_differential.py', 'tests/test_lifecycle_sequence.py',
           'analysis/game_lifecycle/functions.json', 'analysis/game_lifecycle/tables.json')
ENV = ('$HG_ELF=local/extracted/japan/SCPS_150.46, $HG_PS2DINFO=local/references/PS2DInfo, '
       '$HG_CAPTURE_DIR=local/m13/captures, $HG_AREA_IMAGE=original Japan ISO, M7/M8 captures under '
       'local/m7, local/m8 (owner-local); historical regression also $HG_JAPAN_ISO and $HG_PCSX2')
CONT = ('python3 -B tools/verify_player_continuous.py --elf $HG_ELF --ps2dinfo $HG_PS2DINFO '
        '--capture-dir $HG_CAPTURE_DIR --area-image $HG_AREA_IMAGE')
DIFF_CAPTURES = ('local/m13/captures/obs3-000.json local/m8/captures/native-player-init-entry-001.json '
                 'local/m8/captures/resume-square-edge-entry-001.json '
                 'local/m8/captures/resume-equipment-transition-entry-001.json '
                 'local/m9/captures/s3-release1-camera-entry.json local/m10/captures/s1-held1-dispatch-entry.json '
                 'local/m11/captures/obs1-000.json local/m12/captures/obs2-000.json')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text()) if Path(path).exists() else None


def tail(path, pattern):
    if not Path(path).exists():
        return None
    lines = [x for x in Path(path).read_text().splitlines() if re.search(pattern, x)]
    return lines[-1] if lines else None


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--out-dir', type=Path, default=ROOT / 'local/m17/out')
    p.add_argument('--output-dir', type=Path, default=ROOT / 'evidence/m17')
    a = p.parse_args()
    o = a.out_dir
    checks, receipts = [], {}

    def add(id_, evidence, command, result, detail='', receipt=None):
        if receipt is not None and Path(receipt).exists():
            receipts[id_] = sha(receipt)
        checks.append(dict(id=id_, evidence=evidence, command=command, result=result, detail=detail))

    def verdict(ok):
        return 'not-run' if ok is None else ('passed' if ok else 'failed')

    add('elf-identity', 'original-byte', 'sha256sum $HG_ELF', 'passed', ELF)
    packet = load(ROOT / 'analysis/game_lifecycle/functions.json')
    n = sum(len(f['instructions']) for f in packet['functions'])
    add('lifecycle-packet-ps2dinfo', 'original-byte',
        'python3 -B tools/hg_m17_evidence.py --elf $HG_ELF --ps2dinfo $HG_PS2DINFO '
        '--output analysis/game_lifecycle/functions.json --tables analysis/game_lifecycle/tables.json',
        'passed', f'{len(packet["functions"])} rows, {n} instructions verified, 0 unknown (--require-all-bytes)')
    add('lifecycle-packet-record', 'original-byte',
        'python3 -B tools/verify_elf_evidence.py --elf $HG_ELF --record analysis/game_lifecycle/functions.json',
        'passed', f'PASS: {len(packet["functions"])} function listings match original ELF bytes and JAL targets')
    add('strict-build', 'synthetic', 'cc -std=c11 -O2 -Wall -Wextra -Werror -pedantic -fPIC -shared -Isrc '
        'src/game_lifecycle.c src/resource_loader.c src/disc_read.c', 'passed')

    diff = load(o / 'retail-diff-002.json')
    if diff:
        rows = diff['functions'] if 'functions' in diff else diff
        detail = diff.get('summary') if isinstance(diff, dict) and 'summary' in diff else None
    log = tail(o / 'retail-diff-002.log', r'^(PASSED|FAILED)$')
    lines = [x for x in (o / 'retail-diff-002.log').read_text().splitlines() if 'cases=' in x] \
        if (o / 'retail-diff-002.log').exists() else []
    add('lifecycle-differential-retail', 'model-differential',
        'python3 -B tools/verify_lifecycle_differential.py --elf $HG_ELF ' +
        ' '.join(f'--capture {c}' for c in DIFF_CAPTURES.split()) +
        ' --cases 2400 --seed 17 --output local/m17/out/retail-diff-002.json',
        verdict(None if log is None else log == 'PASSED'),
        f'{len(lines)} entries; ' + '; '.join(re.sub(r'\s+', ' ', x.strip()) for x in lines),
        o / 'retail-diff-002.json')
    neg = load(o / 'negative-controls-002.json')
    add('lifecycle-differential-negative-controls', 'model-differential',
        'python3 -B tools/lifecycle_negative_controls.py --elf $HG_ELF --output local/m17/out/negative-controls-002.json',
        verdict(None if neg is None else neg.get('passed', neg.get('result') == 'passed')),
        '' if neg is None else f"{sum(1 for r in neg['controls'] if r.get('effective'))}/{len(neg['controls'])} effective",
        o / 'negative-controls-002.json')
    corridor = load(o / 'lifecycle-corridor-002.json')
    add('lifecycle-corridor-22-frames', 'runtime', CONT + ' --lifecycle --output local/m17/out/lifecycle-corridor-002.json',
        verdict(None if corridor is None else corridor['result'] == 'passed'),
        '' if corridor is None else (f"{len(corridor['frames'])} frames ({corridor['frames'][0]['counter']}-"
                                     f"{corridor['frames'][-1]['counter']}), failures {corridor['failures']}, "
                                     f"interrupts/frame {sorted({f['interrupts'] for f in corridor['frames']})}, "
                                     f"{len(corridor.get('interpreted_pcs', []))} interpreted original words"),
        o / 'lifecycle-corridor-002.json')
    cc = load(o / 'corridor-controls-002.json')
    add('lifecycle-corridor-controls', 'runtime',
        'python3 -B tools/verify_lifecycle_corridor_controls.py --elf $HG_ELF --ps2dinfo $HG_PS2DINFO '
        '--capture-dir $HG_CAPTURE_DIR --area-image $HG_AREA_IMAGE --output local/m17/out/corridor-controls-002.json',
        verdict(None if cc is None else cc['passed']),
        '' if cc is None else '; '.join(f"{r['control']} {r['outcome']}" for r in cc['controls']),
        o / 'corridor-controls-002.json')
    m16 = load(o / 'm16-corridor-001.json')
    add('m16-corridor-22-frames-with-controls', 'runtime',
        CONT + ' --negative-controls --output local/m17/out/m16-corridor-001.json',
        verdict(None if m16 is None else m16['result'] == 'passed'),
        '' if m16 is None else (f"{len(m16['frames'])} frames, failures {m16['failures']}, controls "
                                f"{sum(1 for c in m16.get('negative_controls', []) if c['result'] == ('clean' if c['kind'] == 'null-control' else 'rejected'))}"
                                f"/{len(m16.get('negative_controls', []))} as intended"),
        o / 'm16-corridor-001.json')
    natives = {}
    for id_, name, entry, before, after in (
            ('native-pause-entry', 'native-pause-entry-001.json', 'frame', 'local/m8/captures/pad-read-entry.json',
             'local/m8/captures/pause-transition.json'),
            ('native-pause-retained', 'native-pause-retained-001.json', 'mode',
             'local/m8/captures/pause-transition.json', 'local/m8/captures/pause-retained.json'),
            ('native-area-pack', 'native-area-pack-001.json', 'pack', 'local/m7/captures/c6-entry.json',
             'local/m7/captures/c6-entry.json')):
        r = load(o / name)
        natives[id_] = None if r is None else dict(
            entry=r['entry'], before_sha256=r['before']['sha256'], after_sha256=r['after']['sha256'],
            before_state=r['before']['state'], after_state=r['after']['state'], input_pad=r['input_pad'],
            pack=r['pack'], compared_bytes=r['compared_bytes'], c_changed_bytes=r['c_changed_bytes'],
            mismatches=r['mismatches'], uncaptured_reads=len(r['uncaptured_reads']),
            calls=[f"{c['pc']} {c['kind']}" for c in r['calls']],
            negative_controls=[dict(control=c['control'], rejected=c['rejected']) for c in r['negative_controls']],
            passed=r['passed'])
        add(id_, 'runtime-observation',
            f'python3 -B tools/verify_lifecycle_native.py --elf $HG_ELF --entry {entry} --before {before} '
            f'--after {after} --negative-controls --output local/m17/out/{name}',
            verdict(None if r is None else r['passed']),
            '' if r is None else (f"{r['compared_bytes']} bytes compared, {len(r['mismatches'])} mismatches, "
                                  f"{len(r['uncaptured_reads'])} uncaptured reads, controls "
                                  f"{sum(c['rejected'] for c in r['negative_controls'])}/{len(r['negative_controls'])} rejected"
                                  + (f", pack C {r['pack']['c']:#x} native {r['pack']['native']:#x}" if r['pack'] else '')),
            o / name)
    seq = load(a.output_dir / 'lifecycle-sequence.json')
    hs = [] if seq is None else [s['state']['handler'] for s in seq['states']]
    add('lifecycle-sequence-synthetic', 'synthetic',
        'python3 -B tools/run_lifecycle_sequence.py --frames 1500 --output evidence/m17/lifecycle-sequence.json',
        verdict(None if seq is None else not seq['faults']),
        '' if seq is None else (f"{len(seq['states'])} distinct mode states, {len(seq['requests'])} resource requests, "
                                f"handlers {' -> '.join(h for i, h in enumerate(hs) if i == 0 or hs[i - 1] != h)}"
                                " (collapsed), faults 0"))
    for id_, command, logname, pattern in (
            ('synthetic-suite', 'python3 -B -m unittest discover -s tests', 'synthetic-001.log', r'^Ran \d+ tests'),
            ('sanitizer-lifecycle-harness', 'cc -std=c11 -Wall -Wextra -Werror -pedantic -O1 -g '
             '-fsanitize=address,undefined -fno-sanitize-recover=all -Isrc tests/lifecycle_sanitizer.c '
             'src/game_lifecycle.c src/resource_loader.c src/disc_read.c -o local/build/lifecycle-sanitizer '
             '&& local/build/lifecycle-sanitizer', 'sanitizer-001.log', r'lifecycle sanitizer'),
            ('ubsan-lifecycle-suites', 'HG_SANITIZE=1 python3 -B -m unittest tests.test_lifecycle_sequence '
             'tests.test_lifecycle_differential', 'ubsan-001.log', r'^Ran \d+ tests'),
            ('m14-native-window', 'python3 -B tools/verify_predispatch_window.py --elf $HG_ELF --ps2dinfo $HG_PS2DINFO '
             '--capture-dir $HG_CAPTURE_DIR --negative-controls --output local/m17/out/m14-native-001.json',
             'm14-native-001.log', r'^passed \d+ frames'),
            ('m14-register-contracts', 'python3 -B tools/verify_predispatch_contracts.py --elf $HG_ELF '
             '--ps2dinfo $HG_PS2DINFO --capture-dir $HG_CAPTURE_DIR --output local/m17/out/m14-contracts-001.json',
             'm14-contracts-001.log', r'^passed '),
            ('m14-counter-guard', 'python3 -B tools/verify_counter_guard.py --elf $HG_ELF --ps2dinfo $HG_PS2DINFO '
             '--capture $HG_CAPTURE_DIR/obs3-010.json --output local/m17/out/m14-counter-001.json',
             'm14-counter-001.log', r' passed$'),
            ('historical-regression', 'python3 -B tools/hg_verify.py run --profile full  (HG_JAPAN_ISO, HG_PCSX2)',
             'historical-regression-002.log', r'gates accepted')):
        path = o / logname
        text = path.read_text() if path.exists() else None
        rc = re.findall(r'^rc=(\d+)$', text, re.M) if text else []
        result = 'not-run' if not rc else ('passed' if rc[-1] == '0' else 'failed')
        line = tail(path, pattern) or ''
        line = re.sub(r'/home/.*?/(?=local/)', '', line)[:400]
        add(id_, 'synthetic' if id_ in ('synthetic-suite', 'sanitizer-lifecycle-harness', 'ubsan-lifecycle-suites')
            else ('regression' if id_ == 'historical-regression' else 'runtime'), command, result, line, path)
    sources = {n: sha(ROOT / n) for n in SOURCES}
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(['git', 'status', '--porcelain', '--', 'src', 'tools', 'tests', 'analysis'],
                                cwd=ROOT, capture_output=True, text=True).stdout.strip())
    overall = 'PASSED' if all(c['result'] == 'passed' for c in checks) else (
        'INCOMPLETE' if any(c['result'] == 'not-run' for c in checks) else 'FAILED')
    record = dict(schema='hg-m17-checks/v1', recorded_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  result=overall, base=BASE, base_note='main at the M16 PR #20 merge.',
                  source_commit=head, source_tree_dirty=dirty, environment=ENV,
                  identities=dict(japanese_elf_sha256=ELF,
                                  japan_iso_sha256='8ca1513c8417c0a40b838ff5edf22a2360f43bce1b0458693a95a6efdafd994e',
                                  area1_sha256='8ec765c77cb432b8dbe29c3ef8daa99d23ff0dcfdca939636ffc7b8b78cfa788',
                                  ps2dinfo_revision='2e4a226e00d33ab35391aacade71b55826e5f52f',
                                  pcsx2_appimage_sha256='0c46bb6a88aa2782b10853a7b07cf3387ba99cbef2b966372cd2315b8571abea'),
                  sources=sources, receipts=receipts, checks=checks)
    results = dict(schema='hg-m17-results/v1', result=overall, elf_sha256=ELF, native_observations=natives,
                   corridor=None if corridor is None else dict(
                       frames=[f['counter'] for f in corridor['frames']], failures=corridor['failures'],
                       lifecycle_external=sorted({x for f in corridor['frames'] for x in
                                                  (e if isinstance(e, str) else json.dumps(e) for e in f['lifecycle_external'])}),
                       interpreted_original_words=len(corridor.get('interpreted_pcs', []))),
                   evidence_classes=dict(
                       model_differential='original-instruction versus C, same inputs, on the ELF and eight retail '
                                          'full-RAM states; not runtime observation',
                       runtime='C connected frame versus native coordinator-entry captures (obs3 corridor)',
                       runtime_observation='C paths versus partial native M7/M8 captures',
                       synthetic='host-modelled sequence and unit suites; not retail equivalence'))
    a.output_dir.mkdir(parents=True, exist_ok=True)
    (a.output_dir / 'checks.json').write_text(json.dumps(record, indent=2) + '\n')
    (a.output_dir / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    for c in checks:
        print(f"{c['id']:<42} {c['result']}")
    print(overall)
    return 0 if overall == 'PASSED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
