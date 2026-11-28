#!/usr/bin/env python3
"""Build and run the M24 synthetic sequencer driver and compare its receipt.

Compiles tests/audio_sequencer.c with the M22/M24 audio sources (strict C11,
optionally ASan+UBSan+LSan) into the attempt build directory, runs it, and
requires the JSON report to equal evidence/m24/sequencer.json byte for byte
(the committed receipt was identical across GCC, Clang and sanitized builds).
No original input is read.
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
EVIDENCE = ROOT / 'evidence/m24/sequencer.json'
SOURCES = ('tests/audio_sequencer.c', 'src/audio/audio_engine.c', 'src/audio/audio_iop.c',
           'src/audio/audio_sequencer.c')
FLAGS = ['-std=c11', '-Wall', '-Wextra', '-Werror', '-pedantic', '-ffp-contract=off']
SANITIZE = ['-g', '-O1', '-fno-omit-frame-pointer', '-fsanitize=address,undefined', '-fno-sanitize-recover=all']


def run(compiler, sanitize, build):
    with tempfile.TemporaryDirectory(prefix='m24-seq-', dir=build) as scratch:
        binary = Path(scratch) / 'audio_sequencer'
        flags = FLAGS + (SANITIZE if sanitize else ['-O2'])
        subprocess.run([compiler, *flags, '-I', str(ROOT / 'src'), *[str(ROOT / s) for s in SOURCES],
                        '-lm', '-o', str(binary)], cwd=ROOT, check=True, timeout=120)
        env = {**os.environ, 'ASAN_OPTIONS': 'detect_leaks=1:halt_on_error=1', 'UBSAN_OPTIONS': 'halt_on_error=1'}
        proc = subprocess.run([str(binary)], capture_output=True, env=env, timeout=120)
    return proc


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
    expected = EVIDENCE.read_bytes()
    row = dict(schema='hg-m24-sequencer-verification/v1', compiler=Path(args.compiler).name,
               sanitized=args.sanitize, exit_code=proc.returncode, stderr=proc.stderr.decode(errors='replace'),
               report_sha256=hashlib.sha256(stdout).hexdigest(),
               evidence_sha256=hashlib.sha256(expected).hexdigest(),
               matches_evidence=stdout == expected, report=report)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(row, stream, indent=2)
        stream.write('\n')
    ok = proc.returncode == 0 and row['matches_evidence'] and not row['stderr']
    print(f'M24 sequencer: exit {proc.returncode}, {len(report["scenarios"])} records, '
          f'receipt {"matches" if row["matches_evidence"] else "DIFFERS FROM"} evidence/m24/sequencer.json')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
