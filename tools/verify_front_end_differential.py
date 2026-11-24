#!/usr/bin/env python3
"""M18: original-instruction versus C differential of the front-end services.

For each reconstructed function, seeded random inputs over its declared
control cells and callee return domains run on both the original
instructions and the C. Base memory images are synthetic (zero RAM) and, with
--capture, complete owner-local EE RAM + scratchpad images from the M18
native captures (zlib files written by the private driver). Reports per
function: cases, equality, executed fraction of the original words.

    python3 -B tools/verify_front_end_differential.py --elf "$HG_ELF" --overlay "$HG_A0_B09" \
        [--capture local/m18/native/full/title-menu-input-ready ...] [--cases 400] [--output out.json]
"""
import argparse
import hashlib
import json
import random
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import front_end_differential as F
from lifecycle_differential import Script

GP = 0x3e7f70
M = 0x3ead30
FADE = GP + 0x2f40
PAD = 0x95e340
BUF = F.SP - 0x80 + 0x60          # 0013e090 guest frame buffer (sp+60)
VOL = (0, 0x3f800000, 0x3f000000, 0x3e4ccccd)
FLT = (0, 0x3f000000, 0x3f800000, 0x3ecccccd, 0x3f666666, 0x3dcccccd)
EDGE = (0, 0x1000, 0x4000, 0x20, 0x40, 0x800, 0x820, 0x840, 0x5020, 0xa000)
LIBPAD = {0x10dcf8: (0, 1, 2, 6, 7), 0x10e0d8: (0, 1, 4, 7, 0xffffffff), 0x10e210: (0, 1), 0x10de10: (0, 1, 2),
          0x10de98: (0, 1), 0x10e388: (0, 1), 0x10e5c8: (0, 1), 0x10e628: (0, 1)}


def pad_effect(rng):
    data = bytes([rng.choice((0, 0, 0, 0xff)), rng.choice((0x41, 0x73, 0x79, 0x70))])
    data += bytes(rng.choice((0xff, 0xfe, 0xf7, 0xef, 0xbf, 0x7f, 0, rng.randrange(256))) for _ in range(2))
    data += bytes(rng.choice((0, 0x08, 0x10, 0x7f, 0x80, 0xe0, 0xe1, 0xff)) for _ in range(4))
    data += bytes(rng.randrange(256) for _ in range(12)) + bytes(12)

    def effect(bus, n):
        bus.put(BUF, data)
    return effect


SPECS = {
    0x138fa0: dict(cells=[(FADE + i, 1, (0, 1, 2, 3, 0xff)) for i in range(4)] + [(FADE + 8, 4, (0, 1, 8))]),
    0x138fd0: dict(cells=[(0xa6bf95, 1, (0, 1))], args=[(0, 1, 0x80), (0, 1, 2)]),
    0x139010: dict(cells=[], args=[(0, 1, 0xff), (0, 1, 3)]),
    0x139040: dict(cells=[], args=[(4, 8, 0x10, 0xff), (0, 1, 0x80), (0, 1, 2, 3, 4, 0xff), (0, 1, 2)]),
    0x1390b0: dict(cells=[(0xa6bf95, 1, (0, 1))], args=[(4, 8, 6), (0, 1), (0, 1, 2), (0, 1)]),
    0x1390f0: dict(cells=[(FADE + 8, 4, (0, 1, 2, 5, 6, 8, 0xc))], args=[(0, 1, 0x40, 0x7f, 0x80, 0xff, 100)]),
    0x139140: dict(cells=[(FADE + 8, 4, (0, 1, 2, 5, 6, 8, 0xc))], args=[(0, 1, 0x40, 0x7f, 0x80, 0xff, 100)]),
    0x139190: dict(cells=[(FADE + 8, 4, (0, 1, 2, 5, 6, 8, 0xc))], args=[(0, 1, 0x40, 0x7f, 0x80, 0xff, 100)]),
    0x1391f0: dict(cells=[(FADE + 8, 4, (0, 1, 2, 5, 6, 8, 0xc))], args=[(0, 1, 0x40, 0x7f, 0x80, 0xff, 100)]),
    0x139240: dict(cells=[(FADE, 1, (0, 1, 2, 3, 4)), (FADE + 1, 1, (0, 1)), (FADE + 2, 1, (0, 1, 0xff)),
                          (FADE + 3, 1, (0, 1, 2, 3, 4, 5, 6, 7, 8, 0xff)),
                          (FADE + 4, 2, (0, 4, 0xf7, 0xfb, 0xfc, 0xff, 0x100, 0xfffc, 0x7fff)),
                          (FADE + 6, 2, (0, 4, 6, 8, 0xff, 0xfffc)), (0x700000d4, 4, (0, 1, 2))]),
    0x13d970: dict(cells=[(0x3fa1b8, 1, (0, 1))] + [(PAD + o, 2, (0, 0x20, 0x1000, 0xffff)) for o in
                                                   (0x224, 0x226, 0x228, 0x22a, 0x234, 0x236)]
                   + [(PAD + o, 4, FLT + (0xbf800000,)) for o in range(0x1f8, 0x224, 4)]),
    0x13dc30: dict(cells=[(PAD + 0x10a, 1, tuple(range(0, 12))), (PAD + 0x10b, 1, (0, 1)),
                          (PAD + 0x108, 2, (4, 7, 0x41, 0x73)), (PAD + 0x1a3, 1, (4, 7, 0)),
                          (PAD + 0x100, 4, (0, 1)), (PAD + 0x104, 4, (0, 1))],
                   args=[(PAD,)], returns={**LIBPAD, 0x13e090: (0, 1)}),
    0x13e090: dict(cells=[(PAD + 0x10b, 1, (0, 1)), (PAD + 0x100, 4, (0, 0, 1)), (PAD + 0x23c, 4, (0, 1)),
                          (PAD + 0x208, 4, FLT), (PAD + 0x224, 2, (0, 0x10, 0x1010, 0xffff)),
                          (PAD + 0x230, 2, (0, 0x10, 0x1010)),
                          (PAD + 0x22c, 2, (0, 0x1f, 0x20, 0x21, 0xffff)), (PAD + 0x22e, 2, (0, 1, 6, 7, 8)),
                          (PAD + 0x238, 2, (0, 0x20, 0x21)), (PAD + 0x23a, 2, (1, 7, 8))]
                   + [(PAD + 0x10c + 8 * i, 4, (0, 0x3dcccccd)) for i in range(16)]
                   + [(PAD + 0x110 + 8 * i, 4, (0x3f800000, 0x3f666666)) for i in range(16)],
                   args=[(PAD,), (0, 1, 2)], returns={0x10db90: (0, 0x20, 0x20), 0x13e8f0: FLT,
                                                      0x13e500: (0, 0x8000, 0x1000, 0x4000, 0x2000)},
                   pad=True),
    0x13e500: dict(cells=[], args=[tuple(range(0, 0x100, 7)) + (0x0f, 0x10, 0xe0, 0xe1, 0xff, 0x1ff), (0, 1)]),
    0x13e550: dict(cells=[(PAD + 0x224, 2, (0, 0xffff, 0x1234, 0x8001, 0x0f0f))], args=[(PAD,)]),
    0x13e8f0: dict(cells=[], args=[(0, 1, 0x19, 0x80, 0xcc, 0xe6, 0xff)],
                   fpr={12: (0, 0x3dcccccd, 0x3e800000), 13: (0x3f000000, 0x3f666666, 0x3f800000)}),
    0x13d910: dict(cells=[], args=[(PAD, 0x500000)]),
    0x13dad0: dict(cells=[], args=[(PAD, 0x500000)]),
    0x147f10: dict(cells=[(GP + 0x3098, 4, (0x500000, 0x510010))]),
    0x141980: dict(cells=[(GP + 0x2f20 + i, 1, (0, 1, 0x7f, 0xff)) for i in range(0x18)]),
    0x141810: dict(cells=[(GP + 0x2f20 + i, 1, (0, 1, 0x7f, 0xff)) for i in range(0x18)]),
    0x1418b0: dict(cells=[(0x3fa1bc + i, 1, (0, 1, 0x20, 0xff)) for i in range(8)], args=[(0x3fa1bc, 0x500000)]),
    0x2cf020: dict(cells=[(0x966500, 2, (0, 1, 2, 3, 0xffff)), (0x966502, 2, (0, 1, 2)),
                          (0x966504, 2, (0, 1, 2)), (0x966506, 2, (0, 1, 2, 3, 0xfffe)),
                          (0x966518, 4, (0, 1, 2, 3))],
                   returns={0x1783c0: (0xffffd8f0, 0xffffd8ee, 0xfffffffc, 0xffffffff, 0, 1, 2),
                            0x1782a0: (0, 1, 3), 0x178270: (0, 1, 3)}),
    0xa87880: dict(cells=[(M + 9, 1, (0, 1, 2, 2, 2, 3, 4, 5)), (M + 0x11, 1, (0, 1, 2, 3)), (M + 0x12, 1, (0, 1)),
                          (M + 0x1a, 2, (0, 1, 2)), (M + 0x1c, 2, (0, 1, 2, 0x4b0)),
                          (0x70000410, 4, (0, 1)), (GP + 0x2ef4, 4, (0, 1, 2)),
                          (0x3f9f60, 4, (0, 0x43000000, 0x42fe0000)), (0x95e568, 2, EDGE),
                          (0x95e564, 2, (0, 0, 0x20, 0x1000)), (FADE, 1, (0, 2, 3)), (GP + 0x2b08, 4, VOL)],
                   returns={0xa87de0: (0, 1)}),
    0xa87de0: dict(cells=[(M + 0xb, 1, (0, 1, 2, 2, 3)), (M + 0x1e, 2, (0, 1, 2, 0xf, 0xffff)),
                          (0x70000410, 4, (0, 1, 2))], args=[(0, 1)], returns={0x2cf020: (0, 1, 2, 3)}),
    0x162750: dict(cells=[(M + 0xc, 1, (0, 1, 1, 1, 2, 4, 4, 6, 7, 0x11, 0x12, 0x20)),
                          (M + 0x20, 2, (0, 1, 2, 3, 4)), (M + 0x22, 2, (0, 1, 2)),
                          (M + 0x14, 1, (0, 1, 4, 5, 0x11)), (M + 0x28, 4, (0x1635b0,)),
                          (GP + 0x2f94, 2, EDGE), (GP + 0x2f96, 2, (0, 0x1000, 0x4000, 0x5000)),
                          (FADE, 1, (0, 2, 2)), (GP + 0x2d7c, 1, (0, 0, 1)), (GP + 0x2b08, 4, VOL)],
                   returns={0x1635b0: (0,)}),
    0x161cf0: dict(cells=[(M + 0x22, 2, (0, 1, 2, 3, 0xffff)), (GP + 0x2f94, 2, EDGE), (GP + 0x2b08, 4, VOL)],
                   args=[(M + 0x22,), (0, 1, 2, 3, 0x10001)]),
}
SCRATCH_BASE = bytes(0x4000)


def scratch_with_mode(scratch):
    s = bytearray(scratch)
    s[0xc0:0xc4] = M.to_bytes(4, 'little')
    return bytes(s)


def load_capture(stem):
    stem = Path(stem)
    ee = zlib.decompress(Path(str(stem) + '.ee.z').read_bytes())
    sp = zlib.decompress(Path(str(stem) + '.sp.z').read_bytes())
    rec = json.loads(Path(str(stem) + '.json').read_text())
    if hashlib.sha256(ee).hexdigest() != rec['ee_sha256'] or hashlib.sha256(sp).hexdigest() != rec['sp_sha256']:
        raise ValueError(f'capture digest mismatch: {stem}')
    return stem.name, ee, sp


def run(code, lib, bases, cases, seed, entries=None):
    rng = random.Random(seed)
    report = {}
    for entry in (entries or F.FUNCTIONS):
        end = F.FUNCTIONS[entry][0]
        spec = SPECS[entry]
        words = set(range(entry, end, 4))
        executed, ok, failures, n = set(), 0, [], 0
        for name, ram, scratch in bases:
            for _ in range(cases):
                cells = [(a, w, rng.choice(v)) for a, w, v in spec['cells']]
                args = tuple(rng.choice(v) for v in spec.get('args', []))
                fpr = {k: rng.choice(v) for k, v in spec.get('fpr', {}).items()}
                rets = {pc: [rng.choice(v) for _ in range(24)] for pc, v in spec.get('returns', {}).items()}
                effects = {0x10db90: pad_effect(rng)} if spec.get('pad') else {}
                equal, detail, ex = F.case(code, lib, entry, ram, scratch, cells,
                                           lambda: Script(dict(rets), dict(effects)), args=args, fpr=fpr)
                n += 1
                executed |= ex & words
                if equal:
                    ok += 1
                elif len(failures) < 3:
                    failures.append(dict(base=name, cells=[(hex(a), w, hex(v)) for a, w, v in cells],
                                         args=[hex(x) for x in args], detail=str(detail)[:600]))
        report[f'{entry:08x}'] = dict(cases=n, equal=ok, mismatches=n - ok, failures=failures,
                                      words=len(words), executed_words=len(executed),
                                      executed_fraction=round(len(executed) / len(words), 4))
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--elf', required=True)
    p.add_argument('--overlay', required=True)
    p.add_argument('--capture', action='append', default=[])
    p.add_argument('--cases', type=int, default=200)
    p.add_argument('--seed', type=int, default=18)
    p.add_argument('--only', action='append', default=[])
    p.add_argument('--library')
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    code = F.code_image(a.elf, a.overlay)
    lib = F.build(a.library) if a.library else F.build()
    image = bytearray(0x2000000)              # synthetic: authenticated ELF image, rest zero
    image[0x100000:0x100000 + len(code)] = code
    bases = [('elf-image', bytes(image), scratch_with_mode(SCRATCH_BASE))]
    for stem in a.capture:
        name, ee, sp = load_capture(stem)
        bases.append((name, ee, scratch_with_mode(sp)))
    entries = [int(x, 16) for x in a.only] or None
    report = run(code, lib, bases, a.cases, a.seed, entries)
    total = sum(r['cases'] for r in report.values())
    bad = sum(r['mismatches'] for r in report.values())
    result = dict(schema='hg-m18-front-end-differential/v1', elf_sha256=F.L.ELF_SHA256,
                  overlay_sha256=F.OVERLAY_SHA256, bases=[b[0] for b in bases], cases_per_base=a.cases,
                  seed=a.seed, total_cases=total, mismatches=bad, functions=report)
    for k, r in report.items():
        print(f"{k} cases {r['cases']:6d} mismatches {r['mismatches']:5d} executed {r['executed_words']}/{r['words']}")
        for f_ in r['failures'][:1]:
            print('   ', f_['detail'][:400])
    print('total', total, 'mismatches', bad)
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        with a.output.open('x') as fp:
            json.dump(result, fp, indent=1)
            fp.write('\n')
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
