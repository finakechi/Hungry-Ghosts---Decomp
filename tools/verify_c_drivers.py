#!/usr/bin/env python3
"""Run the M20-M22 synthetic C drivers under ASan+UBSan+LSan.

The Python suite builds these assert-based drivers without sanitizers. This
check compiles each with strict C11 and -fsanitize=address,undefined, runs it
with leak detection and halt-on-error, and requires exit 0 with empty output.
A deliberately overflowing canary must be reported by a sanitizer, which
shows the instrumentation is active in the same toolchain. (The M19 resource
driver keeps its historical sanitizer gates; M23/M24 run under their own
verifiers.) No original input is read.
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
HGSCRIPT = ('src/hgscript/hgscript.c', 'src/hgscript/timed.c')
DRIVERS = {
    'm20-hgscript-runtime': (('tests/hgscript_runtime.c', *HGSCRIPT), 'src/hgscript'),
    'm20-hgscript-title': (('tests/hgscript_title.c', *HGSCRIPT, 'src/hgscript/title.c',
                            'src/resource_loader.c', 'src/disc_read.c'), 'src'),
    'm21-title-scene': (('tests/title_scene.c', 'src/scene/title_scene.c', 'src/hgscript/title.c', *HGSCRIPT,
                         'src/resource_loader.c', 'src/disc_read.c'), 'src'),
    'm22-m24-scene-audio': (('tests/scene_audio_interface.c', 'src/scene/title_scene.c', 'src/hgscript/title.c',
                             *HGSCRIPT, 'src/front_end.c', 'src/game_lifecycle.c', 'src/resource_loader.c',
                             'src/disc_read.c', 'src/audio/audio_engine.c', 'src/audio/audio_iop.c',
                             'src/audio/audio_sequencer.c'), 'src'),
}
CANARY = ('#include <stdlib.h>\n'
          'int main(void) { volatile char *p = malloc(8); p[8] = 1; free((void *)p); return 0; }\n')
FLAGS = ['-std=c11', '-g', '-O1', '-Wall', '-Wextra', '-Werror', '-pedantic', '-ffp-contract=off',
         '-fno-omit-frame-pointer', '-fsanitize=address,undefined', '-fno-sanitize-recover=all']


def build_and_run(compiler, sources, include, binary):
    subprocess.run([compiler, *FLAGS, '-I', str(ROOT / include), *[str(ROOT / s) for s in sources],
                    '-lm', '-o', str(binary)], cwd=ROOT, check=True, timeout=180)
    env = {**os.environ, 'ASAN_OPTIONS': 'detect_leaks=1:halt_on_error=1', 'UBSAN_OPTIONS': 'halt_on_error=1'}
    proc = subprocess.run([str(binary)], capture_output=True, env=env, timeout=300, cwd=ROOT)
    return dict(exit_code=proc.returncode, stdout_sha256=hashlib.sha256(proc.stdout).hexdigest(),
                stdout_bytes=len(proc.stdout), stderr=proc.stderr.decode(errors='replace')[-2000:],
                sanitizer_report='Sanitizer' in proc.stderr.decode(errors='replace')
                or 'runtime error:' in proc.stderr.decode(errors='replace'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler', default=os.environ.get('CC', 'cc'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build = Path(os.environ.get('HG_VERIFY_BUILD_DIR', ROOT / 'local/build'))
    build.mkdir(parents=True, exist_ok=True)
    drivers = {}
    with tempfile.TemporaryDirectory(prefix='c-drivers-', dir=build) as scratch:
        scratch = Path(scratch)
        for name, (sources, include) in DRIVERS.items():
            drivers[name] = dict(sources=list(sources), **build_and_run(args.compiler, sources, include,
                                                                        scratch / name))
        canary_source = scratch / 'canary.c'
        canary_source.write_text(CANARY)
        canary = build_and_run(args.compiler, (canary_source.relative_to(ROOT) if canary_source.is_relative_to(ROOT)
                                               else canary_source,), 'src', scratch / 'canary')
    # UBSan's object-size check or ASan's heap-buffer-overflow report may fire first.
    canary_detected = canary['exit_code'] != 0 and canary['sanitizer_report']
    row = dict(schema='hg-c-driver-sanitizers/v1', compiler=Path(args.compiler).name, flags=FLAGS,
               drivers=drivers, canary=dict(detected=canary_detected, exit_code=canary['exit_code']))
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(row, stream, indent=2)
        stream.write('\n')
    clean = all(d['exit_code'] == 0 and not d['sanitizer_report'] and not d['stderr'] and not d['stdout_bytes']
                for d in drivers.values())
    print(f'{sum(d["exit_code"] == 0 for d in drivers.values())}/{len(drivers)} drivers clean under '
          f'ASan+UBSan+LSan; canary {"detected" if canary_detected else "NOT detected"}')
    return 0 if clean and canary_detected else 1


if __name__ == '__main__':
    sys.exit(main())
