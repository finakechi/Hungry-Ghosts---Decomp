#!/usr/bin/env python3
"""M17: original-instruction versus C differential of the lifecycle layer.

For each reconstructed lifecycle function, seeded random inputs over its
declared control cells (state bytes, flags, pad bits, FP inputs) and callee
return domains run on both the original instructions and the C. Base memory
images are synthetic (zero RAM) and, when --capture is given, complete
owner-local EE RAM images from original-game captures. Reports per-function
cases, equality, and the fraction of original instruction words executed.

    python3 -B tools/verify_lifecycle_differential.py --elf "$HG_ELF" \
        [--capture local/m8/captures/native-player-init-entry-001.json ...] \
        [--cases 400] [--output local/m17/out/lifecycle-diff.json]
"""
import argparse
import hashlib
import json
import random
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lifecycle_differential import (FUNCTIONS, MAIN_FRAME, MODE_BLOCK, Script, build, case,
                                    elf_image, main_frame_case)

M = MODE_BLOCK
GP = 0x3e7f70
F_VOL = (0, 0x3f800000, 0x3f000000, 0x3e4ccccd)
STATE = [(M + 8, 1, range(0, 7)), (M + 9, 1, range(0, 8)), (M + 0xa, 1, range(0, 8)),
         (M + 0xb, 1, range(0, 6)), (M + 1, 1, (0, 1, 2, 5))]
COMMON = [(0x700000cc, 4, (0, 1, 2, 3)), (GP + 0x2d7c, 1, (0, 0, 1)), (GP + 0x2f40, 1, (0, 1, 2, 2)),
          (GP + 0x2b08, 4, F_VOL)]
SPECS = {
    0x143300: dict(cells=[], args=[(0x12d1c0, 0x121be0, 0x121810)]),
    0x1432c0: dict(cells=[], args=[(0x12d1c0, 0x121810)]),
    0x143240: dict(cells=[(M, 1, range(0, 6)), (M + 4, 4, (0x12d1c0, 0x121be0, 0x121810))]),
    0x12d1c0: dict(cells=STATE + COMMON + [(0x3fa1c9, 1, (0, 1)), (0x95e568, 2, (0, 0x8000, 0x2000, 0x20, 0xa020)),
                                            (GP + 0x2f7c, 4, (0, 0x3f800000, 0x47c34f80, 0x47c35000, 0x47c35080)),
                                            (0x3fa1c4, 2, (0, 1, 0x1ff, 0x8001)), (GP + 0x2f34, 2, (0, 5, 0xffff)),
                                            (GP + 0x3098, 4, (0x500000,))],
                   returns={0x12d7d0: (0, 4), 0x12d680: (0, 1, 2, 3), 0x12e020: (0, 4)}),
    0x12d680: dict(cells=STATE + COMMON + [(0x3f450c, 4, (0x600000,)), (0x3f4508, 4, (0x610000,))],
                   returns={0x2cf330: (0, 1, 2, 3)}),
    0x12d7d0: dict(cells=STATE + COMMON),
    0x12d850: dict(cells=[(GP + 0x2f78, 1, (0, 1)), (GP + 0x2f74, 1, (0, 1)), (0x46e10c, 1, (0, 1))]),
    0x12d9a0: dict(cells=STATE + COMMON + [(0x3fa1b0, 1, (0, 1, 2, 3)), (0x3fa1b1, 1, (0, 5, 8)),
                                            (0x3fa1b2, 1, (0, 1))],
                   returns={0x12dc90: (0, 4), 0x12dd60: (0, 4), 0x12e020: (0, 4)}),
    0x12dbd0: dict(cells=[(0x3fa1b0, 1, range(0, 8)), (0x3fa1b1, 1, (0, 1, 5, 0xff)), (0x3fa1b2, 1, (0, 1, 0xff))],
                   args=[(0, 1, 2)]),
    0x12dc90: dict(cells=STATE),
    0x12dd60: dict(cells=STATE + COMMON + [(GP + 0x3098, 4, (0x500000, 0x510010))],
                   returns={0x12dbd0: (0x93, 0x1d4, 0xffffffff)}),
    0x12e020: dict(cells=STATE + COMMON + [(GP + 0x3098, 4, (0x500000,))], args=[(M + 0xa, M + 0xb), (0, 1, 7)],
                   returns={0x2cfa90: (0, 1)}),
    0x12e6d0: dict(cells=[]),
    0x12e700: dict(cells=STATE + COMMON + [(0x3f9f3e, 1, (0, 1)), (0x3f9e39, 1, (0, 1)), (0x3fa1bb, 1, (0, 1)),
                                            (0x46e106, 1, (0, 1)), (0x3f99db, 1, (0, 1)), (0x3f9f38, 1, (0, 1)),
                                            (0x3fa190, 4, (0, 5, 0x7fffffff)), (M + 0x1e, 2, (0, 1, 0x168)),
                                            (0x46d92f, 1, (0, 0x85, 0x7f)), (0x46d92c, 1, (1, 3)),
                                            (0x3f9e50, 4, (0x41200000,)), (0x3fa1c4, 2, (0, 0x8002))],
                   returns={0x12fa00: (0, 1, 2, 3, 6, 10, 0x12, 0xa, 0x1a), 0x162750: (0, 1, 2, 3, 4),
                            0x178ec0: (0, 1)}),
    0x12fa00: dict(cells=[(GP + 0x2d7c, 1, (0, 0, 0, 1)), (GP + 0x2f40, 1, (0, 0, 0, 2)),
                          (0x3f9e3a, 1, (0, 0, 0, 1)), (0x46d90c, 4, (1, 1, 1, 0)), (0x3f9f3f, 1, (0, 0, 2, 1, 4, 5)),
                          (GP + 0x2f78, 1, (0, 0, 0, 1)), (GP + 0x2f74, 1, (0, 0, 0, 1)),
                          (GP + 0x2f94, 2, (0, 0x800, 0x100, 0x900, 0x40, 0x10)),
                          (0x95e44a, 1, (1, 1, 1, 0, 2)), (0x3f9f6e, 1, (0, 0, 0, 1)), (0x3f9f3d, 1, (0, 0, 0, 1)),
                          (GP + 0x2f54, 2, (0, 0x40, 0xffff))]),
    0x12f7d0: dict(cells=STATE),
    0x12f800: dict(cells=[]),
    0x11d830: dict(cells=[], returns={0x11dfd0: ((1, 1, 0, 0, 1), (0, 1), (2, 0, 3))}),
}
# Focused templates: half the random cases overlay one of these to reach
# deep paths (they only fix control cells; all other cells stay random).
FOCUS = {
    0x12d1c0: [[(M + 8, 1, 0), (M + 9, 1, 0), (M + 0xa, 1, 2), (GP + 0x2f40, 1, 0)],
               [(M + 8, 1, 0), (M + 9, 1, 0), (M + 0xa, 1, 0)],
               [(M + 8, 1, 0), (M + 9, 1, 1)], [(M + 8, 1, 0), (M + 9, 1, 0), (M + 0xa, 1, 3)],
               [(M + 8, 1, 0), (M + 9, 1, 0), (M + 0xa, 1, 5)]],
    0x12d680: [[(M + 9, 1, 0), (M + 0xa, 1, 1), (GP + 0x2d7c, 1, 0)], [(M + 9, 1, 1)]],
    0x12d9a0: [[(M + 9, 1, 5), (0x700000cc, 4, 3)]],
    0x12dd60: [[(M + 0xa, 1, 0), (M + 0xb, 1, 1), (GP + 0x2d7c, 1, 0)],
               [(M + 0xa, 1, 4)], [(M + 0xa, 1, 1), (GP + 0x2d7c, 1, 0), (GP + 0x2f40, 1, 2)]],
    0x12e700: [[(M + 0xa, 1, 2)], [(M + 0xa, 1, 2), (M + 0xb, 1, 1)],
               [(M + 0xa, 1, 4), (M + 0xb, 1, 3), (GP + 0x2f40, 1, 0), (M + 0x1e, 2, 0x168), (0x3f9f38, 1, 0)], [(M + 0xa, 1, 3)], [(M + 0xa, 1, 4)], [(M + 0xa, 1, 5), (M + 0xb, 1, 0)],
               [(M + 0xa, 1, 6), (M + 0xb, 1, 0)], [(M + 0xa, 1, 1), (0x3f9e39, 1, 0)],
               [(M + 0xa, 1, 0), (0x3f9e39, 1, 0)],
               [(M + 0xa, 1, 3), (M + 0xb, 1, 1), (0x3fa190, 4, 0x7fffffff)],
               [(M + 0xa, 1, 4), (M + 0xb, 1, 3), (GP + 0x2f40, 1, 0), (M + 0x1e, 2, 1), (0x3f9f38, 1, 1)]],
    0x12fa00: [[(GP + 0x2d7c, 1, 0), (0x3f9e3a, 1, 0), (0x46d90c, 4, 1), (0x3f9f3f, 1, 0), (GP + 0x2f40, 1, 0),
                (GP + 0x2f78, 1, 0), (GP + 0x2f74, 1, 0)]],
}
# Cells forced in every case so the mode-block pointer is meaningful.
FIXED = [(0x700000c0, 4, M)]


def choose(rng, spec):
    cells = [(a, w, rng.choice(list(d))) for a, w, d in spec.get('cells', [])]
    returns = {pc: rng.choice(list(d)) for pc, d in spec.get('returns', {}).items()}
    args = tuple(rng.choice(list(d)) for d in spec.get('args', []))
    return cells, returns, args


def script_factory(returns):
    def make():
        seq = {pc: (list(v) if isinstance(v, tuple) else v) for pc, v in returns.items()}
        return Script(seq)
    return make


def body_words(code, entry, end):
    """Instruction words of the original body, excluding trailing alignment nops."""
    words = [pc for pc in range(entry, end, 4)]
    while words and struct.unpack_from('<I', code, words[-1] - 0x100000)[0] == 0:
        words.pop()
    return words


TABLES = {0x12d240: (0x3daec0, 6), 0x12d9d0: (0x3daee0, 7), 0x12e748: (0x3daf00, 7)}


def reachable(code, entry, end, stop=None):
    """Statically reachable instruction words (jump tables from ELF data)."""
    word = lambda pc: struct.unpack_from('<I', code, pc - 0x100000)[0]
    seen, work = set(), [entry]
    while work:
        pc = work.pop()
        if pc in seen or not entry <= pc < end or pc == stop:
            continue
        seen.add(pc)
        w = word(pc)
        op, rs, rt, fn = w >> 26, (w >> 21) & 31, (w >> 16) & 31, w & 63
        off = ((w & 0xffff) ^ 0x8000) - 0x8000
        target = pc + 4 + off * 4
        if op in (4, 5, 6, 7) or (op == 1 and rt in (0, 1)) or (op == 0x11 and rs == 8):
            seen.add(pc + 4)
            work.append(target)
            if not (op == 4 and rs == 0 and rt == 0):
                work.append(pc + 8)
        elif op == 0 and fn == 8:                 # jr
            seen.add(pc + 4)
            if pc in TABLES:
                table, n = TABLES[pc]
                work.extend(word(table + 4 * i) for i in range(n))
        elif op == 2:
            seen.add(pc + 4)
            work.append(((pc + 4) & 0xf0000000) | ((w & 0x3ffffff) << 2))
        else:
            work.append(pc + 4)
    return seen


def load_capture(path, cache):
    path = Path(path)
    key = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16]
    raw_path, scratch_path = cache / f'{key}.ram', cache / f'{key}.scratch'
    if raw_path.exists():
        return raw_path.read_bytes(), scratch_path.read_bytes(), json.loads((cache / f'{key}.json').read_text())
    data = json.loads(path.read_text())
    ram = scratch = None
    for s in data['samples']:
        address, raw = int(s['address'], 16), bytes.fromhex(s['bytes'])
        if hashlib.sha256(raw).hexdigest() != s['sha256']:
            raise ValueError(f'capture sample digest mismatch in {path}')
        if address == 0 and len(raw) == 0x2000000:
            ram = raw
        elif address == 0x70000000 and len(raw) == 0x4000:
            scratch = raw
    if ram is None:
        raise ValueError(f'{path} has no complete EE RAM sample')
    meta = dict(capture=str(path), capture_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                scratch='captured' if scratch else 'zero (not captured)')
    scratch = scratch or bytes(0x4000)
    cache.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(ram)
    scratch_path.write_bytes(scratch)
    (cache / f'{key}.json').write_text(json.dumps(meta))
    return ram, scratch, meta


def run(code, lib, bases, cases, seed, only=None):
    report = {}
    for entry, spec in SPECS.items():
        if only and entry not in only:
            continue
        end = FUNCTIONS[entry][0]
        words = reachable(code, entry, end, stop={0x11d830: 0x11d938}.get(entry))
        executed = set()
        failures = []
        total = 0
        rng = random.Random(seed ^ entry)
        n_cases = 1 if not spec.get('cells') and not spec.get('returns') and not spec.get('args') else cases
        for label, (ram, scratch) in bases.items():
            for i in range(n_cases):
                cells, returns, args = choose(rng, spec) if i else (
                    [(a, w, list(d)[0]) for a, w, d in spec.get('cells', [])],
                    {pc: list(d)[0] for pc, d in spec.get('returns', {}).items()},
                    tuple(list(d)[0] for d in spec.get('args', [])))
                if i and entry in FOCUS and rng.random() < 0.5:
                    cells = cells + rng.choice(FOCUS[entry])
                ok, detail, ran = case(code, lib, entry, ram, scratch, FIXED + cells,
                                       script_factory(returns), args=args)
                executed |= ran & words
                total += 1
                if not ok:
                    failures.append(dict(base=label, case=i, cells=[(hex(a), w, hex(v)) for a, w, v in cells],
                                         returns={hex(k): v for k, v in returns.items()},
                                         args=[hex(a) for a in args], detail=str(detail)))
        report[f'{entry:08x}'] = dict(function=FUNCTIONS[entry][2], cases=total, failures=len(failures),
                                      first_failures=failures[:3], words=len(words),
                                      executed_words=len(executed),
                                      unexecuted=[f'{pc:08x}' for pc in sorted(words - executed)][:40])
    # Main-loop body.
    if not only or MAIN_FRAME[0] in only:
        words = reachable(code, MAIN_FRAME[0], MAIN_FRAME[1])
        executed, failures, total = set(), [], 0
        rng = random.Random(seed ^ MAIN_FRAME[0])

        def vsync(bus, n):
            bus.put(0x95e308, (1).to_bytes(4, 'little'), log=False)
        for label, (ram, scratch) in bases.items():
            for i in range(max(2, cases // 10)):
                debug = rng.choice((0, 1)) if i else 0
                cells = FIXED + [(0x700000f0, 2, rng.choice((0, 0x280, 0xfff0))),
                                 (0x700000f2, 2, rng.choice((0, 0x1c0, 0x8000)))]

                def make(debug=debug):
                    return Script({0x100b80: debug}, {0x13da80: vsync})
                ok, detail, ran = main_frame_case(code, lib, ram, scratch, cells, make)
                executed |= ran & words
                total += 1
                if not ok:
                    failures.append(dict(base=label, case=i, detail=str(detail)))
        report[f'{MAIN_FRAME[0]:08x}'] = dict(function='hg_main_frame', cases=total, failures=len(failures),
                                              first_failures=failures[:3], words=len(words),
                                              executed_words=len(executed),
                                              unexecuted=[f'{pc:08x}' for pc in sorted(words - executed)][:40])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--elf', required=True)
    parser.add_argument('--capture', action='append', default=[])
    parser.add_argument('--cases', type=int, default=300)
    parser.add_argument('--seed', type=int, default=17)
    parser.add_argument('--only', action='append', default=[])
    parser.add_argument('--source', help='alternate C source (negative controls)')
    parser.add_argument('--output')
    a = parser.parse_args()
    code = elf_image(a.elf)
    lib = build(output=Path('local/build') / ('m17-lifecycle-' + hashlib.sha256(
        Path(a.source or 'src/game_lifecycle.c').read_bytes()).hexdigest()[:12] + '.so'), source=a.source)
    scratch = bytearray(0x4000)
    ram = bytearray(0x2000000)
    ram[0x100000:0x100000 + len(code)] = code     # authenticated ELF image, zero elsewhere
    bases = {'elf-image': (bytes(ram), bytes(scratch))}
    inputs = []
    for path in a.capture:
        ram, scr, meta = load_capture(path, Path('local/m17/cache'))
        bases[Path(path).stem] = (ram, scr)
        inputs.append(meta)
    only = {int(x, 16) for x in a.only}
    report = run(code, lib, bases, a.cases, a.seed, only)
    summary = dict(schema='hg-m17-lifecycle-differential/v1', elf_sha256=hashlib.sha256(Path(a.elf).read_bytes()).hexdigest(),
                   c_source_sha256=hashlib.sha256(Path(a.source or 'src/game_lifecycle.c').read_bytes()).hexdigest(),
                   seed=a.seed, cases_per_base=a.cases, bases=list(bases), inputs=inputs, functions=report,
                   passed=all(r['failures'] == 0 for r in report.values()))
    text = json.dumps(summary, indent=2)
    if a.output:
        Path(a.output).parent.mkdir(parents=True, exist_ok=True)
        Path(a.output).open('x').write(text + '\n')
    for k, r in report.items():
        print(f"{k} {r['function']:<30} cases={r['cases']:5d} fail={r['failures']:4d} "
              f"coverage={r['executed_words']}/{r['words']}")
    print('PASSED' if summary['passed'] else 'FAILED')
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
