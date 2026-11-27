#!/usr/bin/env python3
"""M17 compiled negative controls for the lifecycle differential.

Each control applies one deliberate defect to src/game_lifecycle.c, compiles
it, and runs the original-instruction differential for the targeted
function. A control is effective only if the defect is rejected; the null
control (no change) must pass.

    python3 -B tools/lifecycle_negative_controls.py --elf "$HG_ELF" \
        [--output local/m17/out/negative-controls.json]
"""
import argparse
import hashlib
import json
import random
import sys
from pathlib import Path
from hg_verify_build import build_path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lifecycle_differential import build, elf_image
import verify_lifecycle_differential as V

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'src/game_lifecycle.c'
CONTROLS = [
    ('null-control', None, None, 0x12dd60, False),
    ('area-load-wrong-pack-kind', '        if(r8(e,blk(e)+1)) {\n            call(e,0x130150,2,call(e,0x12dbd0,1,0,0,0),0,0);',
     '        if(r8(e,blk(e)+1)) {\n            call(e,0x130150,3,call(e,0x12dbd0,1,0,0,0),0,0);', 0x12dd60, True),
    ('area-load-missing-player-clear', 'call(e,0x119ac8,PLAYER,0,0x520,0);', '', 0x12dd60, True),
    ('area-pack-id-off-by-one', 'v+0x93u', 'v+0x94u', 0x12dbd0, True),
    ('gameplay-missing-frame-tick', '    if(!le)w32(e,G(0x2f7c),0);\n    w32(e,G(0x2f80),r32(e,G(0x2f80))+1u);',
     '    if(!le)w32(e,G(0x2f7c),0);', 0x12d1c0, True),
    ('gameplay-accumulator-threshold', '0x47c35000u', '0x47c34f80u', 0x12d1c0, True),
    ('pause-quit-wrong-minor', 'w8(e,b+9,4);', 'w8(e,b+9,5);', 0x12e700, True),
    ('pause-store-after-call', '''        w32(e,0x95e57c,0);
        set8(e,0xa,2);set8(e,0xb,0);set8(e,0xc,0);w16(e,blk(e)+0x1c,0);
        call(e,0x1404f0,0,0,0,0);''', '''        set8(e,0xa,2);set8(e,0xb,0);set8(e,0xc,0);w16(e,blk(e)+0x1c,0);
        call(e,0x1404f0,0,0,0,0);
        w32(e,0x95e57c,0);''', 0x12e700, True),
    ('event-flags-start-mask', 'pad&0x800u', 'pad&0x400u', 0x12fa00, True),
    ('sound-missing-t0', '''    call(e,HG_CALL_T_REGS,1,0,0,0);              /* t0 = 1 at every call site */''',
     '''    call(e,HG_CALL_T_REGS,0,0,0,0);''', 0x12dd60, True),
    ('teardown-wrong-handler', 'w32(e,0x700000f8,1);\n    call(e,0x143300,0x121be0,0,0,0);',
     'w32(e,0x700000f8,1);\n    call(e,0x143300,0x121810,0,0,0);', 0x12f800, True),
    ('mode-set-short-clear', '''    uint32_t b=blk(e);
    for(uint32_t off=8;off<0x28;off+=4)w32(e,b+off,0);''', '''    uint32_t b=blk(e);
    for(uint32_t off=8;off<0x24;off+=4)w32(e,b+off,0);''', 0x143300, True),
    ('major5-active-rule', 'r8(e,0x3fa1b1)==8', 'r8(e,0x3fa1b1)==5', 0x12d9a0, True),
    ('restart-save-order', '''    w16(e,0x3fa1c4,s1);
    call(e,0x11b378,0x3fa194,sp+0x80,0,0);''', '''    call(e,0x11b378,0x3fa194,sp+0x80,0,0);
    w16(e,0x3fa1c4,s1);''', 0x12e700, True),
    ('warp-mask', 'w8(e,0x3f9e37,d&0x7fu);', 'w8(e,0x3f9e37,d&0xffu);', 0x12e700, True),
    ('volume-round-up', 'return (bits&0x80000000u)?0u-v:v;', 'return (bits&0x80000000u)?0u-v:v+1u;', 0x12dd60, True),
    ('main-frame-wrong-counter', 'w32(e,G(0x2f84),r32(e,G(0x2f84))+1u);', 'w32(e,G(0x2f80),r32(e,G(0x2f80))+1u);',
     0x11d938, True),
    ('logo-timer-299', 'w16(e,blk(e)+0x1a,300);', 'w16(e,blk(e)+0x1a,299);', 0x1219e0, True),
    ('new-game-area-triple', 'w8(e,0x3fa1b1,9);', 'w8(e,0x3fa1b1,8);', 0x121be0, True),
    ('lifecycle-missing-frame-tick', '    w32(e,0x700000c4,1);\n    w32(e,G(0x2f80),r32(e,G(0x2f80))+1u);',
     '    w32(e,0x700000c4,1);', 0x121be0, True),
    ('boot-wrong-next-mode', 'w32(e,0x700000f8,0);\n        call(e,0x143300,0x121be0,0,0,0);',
     'w32(e,0x700000f8,0);\n        call(e,0x143300,0x12d1c0,0,0,0);', 0x121810, True),
    ('vblank-counter-skipped', 'w32(e,0x95e300,r32(e,0x95e300)+1u);', '', 0x11df10, True),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--elf', required=True)
    parser.add_argument('--cases', type=int, default=400)
    parser.add_argument('--output')
    a = parser.parse_args()
    code = elf_image(a.elf)
    ram = bytearray(0x2000000)
    ram[0x100000:0x100000 + len(code)] = code
    bases = {'elf-image': (bytes(ram), bytes(0x4000))}
    source = SOURCE.read_text()
    out_dir = build_path(ROOT / 'local/m17/controls')
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for name, old, new, target, defect in CONTROLS:
        text = source
        if old is not None:
            if source.count(old) != 1:
                raise SystemExit(f'{name}: mutation anchor not unique ({source.count(old)})')
            text = source.replace(old, new)
        path = out_dir / f'{name}.c'
        path.write_text(text)
        digest = hashlib.sha256(text.encode()).hexdigest()
        lib = build(output=ROOT / f'local/build/m17-control-{digest[:12]}.so', source=path)
        report = V.run(code, lib, bases, a.cases, 17, {target})
        row = report[f'{target:08x}']
        rejected = row['failures'] > 0
        effective = rejected == defect
        results.append(dict(control=name, target=f'{target:08x}', defect=defect, source_sha256=digest,
                            cases=row['cases'], failures=row['failures'], rejected=rejected,
                            effective=effective, first_failure=(row['first_failures'] or [None])[0]))
        print(f"{name:<34} target={target:08x} failures={row['failures']:4d}/{row['cases']} "
              f"{'EFFECTIVE' if effective else 'INEFFECTIVE'}")
    summary = dict(schema='hg-m17-lifecycle-controls/v1',
                   baseline_sha256=hashlib.sha256(source.encode()).hexdigest(),
                   controls=results, passed=all(r['effective'] for r in results))
    if a.output:
        Path(a.output).parent.mkdir(parents=True, exist_ok=True)
        Path(a.output).open('x').write(json.dumps(summary, indent=2) + '\n')
    print('PASSED' if summary['passed'] else 'FAILED')
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
