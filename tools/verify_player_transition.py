#!/usr/bin/env python3
"""Compare equipment-transition C with authenticated original EE instructions.

External calls are synthetic. Audio comparisons use the bounded chop model,
finite volume [0,1], and this caller's fixed fifth integer argument of 1.
"""
import argparse
import ctypes as C
import hashlib
import json
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'tests')]
from ee_subset import MASK64, sx
from hg_evidence_safety import digest
from resource_harness import Memory, machine, library
from test_player_transition import FUNCTIONS, ARITIES, TransitionDriver, configure
from verify_resource_loader import EXPECTED, clone, compare, shared_module


def selected_packet(packet):
    """Require all four complete bodies; the shared verifier checks their bytes."""
    selected = [f for f in packet['functions'] if int(f['entry'], 16) in FUNCTIONS]
    if len(selected) != len(FUNCTIONS):
        raise ValueError('Missing or duplicated transition function evidence')
    for f in selected:
        pc = int(f['entry'], 16)
        if f['name'] != FUNCTIONS[pc][0] or f.get('complete') is not True:
            raise ValueError('Wrong transition function identity/completeness')
    return dict(packet, functions=selected)


def run(lib, data, segments, packet):
    rng = random.Random(0x158e20)
    results = []
    total = 0
    for pc, (name, arity) in FUNCTIONS.items():
        coverage = set()
        for case in range(768):
            actor = 0x500000
            mem = Memory(data, segments)
            mem.put(actor, bytes(rng.getrandbits(8) for _ in range(0x540)))
            for offset in (0x448, 0x449):
                mem.store(actor + offset, case % 4, 1)
            for offset, value in [(0x50c, case % 2), (0x50b, case % 8),
                                  (0x509, (case // 4) % 8), (0x50a, (case // 32) % 4),
                                  (0x45f, case % 5 == 0), (0x462, case % 8),
                                  (0x4a3, (case // 8) % 4)]:
                mem.store(actor + offset, value, 1)
            for address, value in [(0x3fa200, (case // 2) % 8),
                                   (0x3fa201, (case // 16) % 4),
                                   (0x3fa202, 255 if case % 3 else 7),
                                   (0x46e12f, case % 4)]:
                mem.store(address, value, 1)
            mem.store(0x46da32 + (case % 8) * 2, case % 3, 2)
            mem.store(0x600100, case % 3)
            mem.store(0x600104, 0x700000 if case % 3 else 0)
            mem.store(0x600108, (case // 4) % 8)
            mem.store(0x60010c, (case // 32) % 4)
            volumes = [0, 0x3f000000, 0x3f800000, 0x3f7d70a4, 0x3f400000, 0x3f010204]
            mem.store(0x3eaa78, volumes[case % 6] if case % 2 else rng.randrange(0x3f800001))
            # Explicitly eligible old-mode-2 cases avoid correlated modulo gates.
            if pc == 0x158e20 and case in (6, 7):
                mem.store(actor + 0x448, 2, 1)
                mem.store(actor + 0x449, 2, 1)
                mem.store(actor + 0x50c, 1, 1)
                mem.store(0x46da32 + mem.load(actor + 0x50b, 1) * 2, case % 2, 2)
            mem.store(0x3eaee8, case % 3 == 0, 1)
            mem.store(0x46d90c, [0, 1, 2, 255, 0xffffffff, 0x80000000][case % 6])
            mem.store(0x3f9fcc, case % 5 == 0, 1)
            mem.store(0x3f9f3c, case % 7 == 0, 1)
            mem.store(0x46d894, rng.getrandbits(32))
            mem.store(0x3f9f08, rng.getrandbits(32))
            arg = (0 if case == 0 else rng.getrandbits(32)) if pc == 0x15b260 else actor
            cd, md = TransitionDriver(clone(mem)), TransitionDriver(clone(mem))
            args = (arg,) if arity else ()
            c_result = getattr(lib, name)(C.byref(cd.env), *args)
            model = machine(md)
            for target in ARITIES:
                if target == pc:
                    continue

                def hook(cpu, target=target):
                    if target == 0x156ad0 and cpu.reg[8] & 0xffffffff != 1:
                        raise AssertionError('Original fifth sound argument is not 1')
                    values = [cpu.reg[i] & 0xffffffff for i in (4, 5, 6, 7)]
                    return sx(md.external(target, *values), 32) & MASK64

                model.hooks[target] = hook
            if arity:
                model.reg[4] = arg
            # Scalar writes must preserve the upper halves of saved registers.
            saved = {i: (i << 96) | (i << 64) | i for i in [*range(16, 24), 30]}
            saved.update({28: model.reg[28], 29: model.reg[29]})
            for i, value in saved.items():
                model.reg[i] = value
            m_result = model.run(pc, limit=20000)
            if any(model.reg[i] != value for i, value in saved.items()):
                raise AssertionError(f'{name}/{case}: callee-saved register mismatch')
            compare(cd, md, f'{name}/{case}')
            if pc in (0x15b260, 0x15c160) and c_result != m_result & 0xffffffff:
                raise AssertionError(f'{name}/{case}: return mismatch')
            coverage.update(model.executed)
            total += 1
        f = next(f for f in packet['functions'] if int(f['entry'], 16) == pc)
        expected = {int(i['address'], 16) for i in f['instructions']}
        if expected - coverage:
            raise AssertionError(f'{name}: uncovered instructions {sorted(expected - coverage)}')
        results.append(dict(entry=f['entry'], name=name, cases=768,
                            instruction_addresses=len(expected), result='passed'))
    return total, results


def negative_controls(source_bytes, data, segments, output_stem):
    """Compile three specific defects and require original instructions to disagree."""
    work = ROOT / 'local/build' / ('player-transition-controls-' + output_stem)
    work.mkdir(parents=True, exist_ok=False)
    text = source_bytes.decode('utf-8')
    controls = []
    for label, original, mutation, entry in [
        ('wrong-mode', 'w8(e,a+0x448,1);', 'w8(e,a+0x448,2);', 0x158e20),
        ('wrong-equipment-callback', 'w32(e,object+0x1c,0x173f50);',
         'w32(e,object+0x1c,0x16b0f0);', 0x15c160),
        ('missing-timer-decrement', 'w32(e,0x46d90c,count-1u);',
         'w32(e,0x46d90c,count);', 0x1594f0),
    ]:
        if text.count(original) != 1:
            raise ValueError('Defect anchor is missing or ambiguous: ' + label)
        changed = text.replace(original, mutation)
        source = work / (label + '.c')
        with source.open('x') as output:
            output.write(changed)
        lib = configure(library(source=source, output=source.with_suffix('.so')))
        mem = Memory(data, segments)
        actor = 0x500000
        mem.store(actor + 0x449, 1, 1)
        mem.store(actor + 0x462, 6 if entry == 0x15c160 else 0, 1)
        for address, value, width in [(0x3fa202, 255, 1), (0x3fa200, 0, 1),
                                      (0x3fa201, 0, 1), (0x46d90c, 1, 4),
                                      (0x3eaee8, 0, 1), (0x3f9fcc, 0, 1),
                                      (0x3f9f3c, 0, 1), (0x600104, 0x700000, 4)]:
            mem.store(address, value, width)
        cd, md = TransitionDriver(clone(mem)), TransitionDriver(clone(mem))
        name, arity = FUNCTIONS[entry]
        getattr(lib, name)(C.byref(cd.env), *((actor,) if arity else ()))
        model = machine(md)
        for target in ARITIES:
            if target == entry:
                continue

            def hook(cpu, target=target):
                return sx(md.external(target, *(cpu.reg[i] & 0xffffffff
                                                for i in (4, 5, 6, 7))), 32) & MASK64

            model.hooks[target] = hook
        if arity:
            model.reg[4] = actor
        model.run(entry, limit=10000)
        if cd.errors or md.errors:
            raise AssertionError('A control fixture has an unresolved external dependency')
        try:
            compare(cd, md, label)
        except AssertionError as mismatch:
            controls.append(dict(control=label, result='rejected-as-required',
                                 reason=str(mismatch), mutated_source_sha256=digest(source)))
        else:
            raise AssertionError('Compiled defect survived: ' + label)
    return controls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--elf', type=Path, required=True)
    parser.add_argument('--ps2dinfo', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source', type=Path, help='Disposable C source for compiled negative controls')
    parser.add_argument('--negative-controls', action='store_true',
                        help='Compile and reject three defects in the supplied source snapshot')
    args = parser.parse_args()
    if args.output.exists() or args.output.with_suffix('.so').exists():
        raise FileExistsError('Use fresh output paths')
    if digest(args.elf) != EXPECTED:
        raise ValueError('Wrong original Japanese ELF')
    packet_path = ROOT / 'analysis/player_connected/functions.json'
    packet = selected_packet(json.loads(packet_path.read_text()))
    shared = shared_module(args.ps2dinfo)
    data = args.elf.read_bytes()
    bytecheck = shared.verify(data, packet, require_all_bytes=True)
    segments, _ = shared.segments_from_elf(data)
    source_digests = {name: digest(ROOT / name) for name in [
        'src/resource_loader.h', 'tools/ee_subset.py',
        'tools/verify_player_transition.py', 'tests/test_player_transition.py',
        'analysis/player_connected/functions.json']}
    source = args.source or ROOT / 'src/resource_loader.c'
    # Compile one immutable snapshot so another integrated function can be
    # edited concurrently without misidentifying the code actually compared.
    source_bytes = source.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    build = ROOT / 'local/build'
    build.mkdir(parents=True, exist_ok=True)
    if args.negative_controls:
        controls = negative_controls(source_bytes, data, segments, args.output.stem)
        total, functions = 0, []
    else:
        with tempfile.TemporaryDirectory(prefix='player-transition-', dir=build) as temporary:
            snapshot = Path(temporary) / 'resource_loader.c'
            snapshot.write_bytes(source_bytes)
            lib = configure(library(source=snapshot, output=args.output.with_suffix('.so')))
        total, functions = run(lib, data, segments, packet)
    result = dict(
        schema='hg-player-transition-instructions/v1', result='passed',
        elf_sha256=EXPECTED, cases=total,
        instruction_addresses=sum(f['instruction_addresses'] for f in functions),
        functions=functions, original_bytes=bytecheck, compiled_source_sha256=source_hash,
        sources=source_digests,
        limitations=[
            'Synthetic external dependencies; these checks do not establish retail equivalence.',
            'Sound volume finite [0,1], bounded chop FP model; original fifth sound argument fixed at 1.',
            'Guest stack scratch excluded from memory comparison; full 128-bit saved registers checked.',
        ])
    if args.negative_controls:
        result['schema'] = 'hg-player-transition-negatives/v1'
        result['controls'] = controls
        result.pop('cases')
        result.pop('instruction_addresses')
        result.pop('functions')
        result['limitations'][-1] = 'Guest stack scratch excluded from memory comparison.'
    with args.output.open('x') as output:
        json.dump(result, output, indent=2)
        output.write('\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
