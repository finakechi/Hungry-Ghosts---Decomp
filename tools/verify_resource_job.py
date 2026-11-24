#!/usr/bin/env python3
"""Compare M19 sleep-boundary continuation against original EE worker instructions."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from ee_subset import Machine
from hg_identify import elf32, va_to_offset
from resource_execution import BUSY, ORIGINAL_ELF, TASK, TASK_ACTIVE
from verify_resource_loader import ROOT, shared_module
from resource_harness import library

sys.path.insert(0, str(ROOT / 'tests'))
from test_resource_execution import Harness

DOMAINS = (
    (0x3eace0, 0x3ead30), (0x3eaf88, 0x3eaf8c), (0x3f3b00, 0x3f4d60),
    (TASK, TASK + 0x50), (0x3f9fe0, 0x3f9ff0), (0x46e10c, 0x46e10d),
    (0x46e130, 0x46e138), (0xa87800, 0xa88010), (0xb00000, 0xb00800),
    (0xc00000, 0xc00800), (0xc91000, 0xc91800), (0xd00000, 0xd04000),
    (0x1e00000, 0x1e00800),
)
PUBLICATION = 'w32(e,0x3f4300+id*4,r32(e,task+0x48)+(r32(e,cursor)-r32(e,header+0x10)));'
CONTROLS = (
    ('premature-completion', (0, 0, 0),
     'if(!done)return 0;', 'if(!done){job->stage=JOB_FINISH;break;}'),
    ('wrong-followup-directory', (2, 0xc6, 0),
     'r32(e,task+0x44)+r8(e,0x3fa1b2)+1', 'r32(e,task+0x44)+r8(e,0x3fa1b2)+2'),
    ('omitted-fixup', (0, 0, 0),
     'if(pc==0x137ed0)return hg_resource_fixup(&p->env,a,b,c,p->frame);',
     'if(pc==0x137ed0)return a;'),
    ('wrong-handle-publication', (0, 0, 0), PUBLICATION,
     PUBLICATION.replace('0x3f4300+id*4', '0x3f4300+(id+1)*4')),
)


def compare_job(lib, original, kind, ident, scenario):
    def setup():
        d = Harness(lib, original=original)
        d.store(0x700000c4, 2)
        d.store(0x3fa1b0, 1, 1)
        d.store(0x3fa201, 0, 1)
        d.store(0x3e01c0, 0x4c)
        d.store(0x3e0200, 0x500000)
        d.store(0x500004, 0x4c)
        d.store(0x3e0214, 0x50)
        d.store(0x3e0218, 0xffffffff)
        d.store(0x3f2c80, scenario & 1, 1)
        d.store(0x3f4300 + 0xa1 * 4, 0xbadba5e)
        for address, value in ((0x3f4d3c, 0xb00800), (0x3f4d44, 0xc00000),
                               (0x3f4d48, 0xc91000), (0x3f4d4c, 0xd00000),
                               (0x3f4d50, 0xd00000), (0x3f4d5c, 0xe00000)):
            d.store(address, value)
        if scenario == 1:
            d.store(0x3f9fe0, ident)
            d.store(0x3f9fef, 0x49 if kind == 7 else 0x4c, 1)
            d.store(0x46e136, 1, 2)
            d.store(0x46e132, 0x4c, 2)
            d.store(0x3fa201, 255, 1)
        if scenario == 2:
            d.store(0x46e12d, 2, 1)
            d.store(0x3fa202, 1, 1)
        return d

    c, ee = setup(), setup()
    c.request(kind, ident)
    snapshots = []

    def snapshot(d):
        return tuple(d.mem.read(start, end-start) for start, end in DOMAINS)

    while c.sched.thread:
        if len(snapshots) == 256 or c.errors or c.sched.fault:
            raise AssertionError((kind, ident, scenario, c.errors, c.sched.fault))
        snapshots.append(snapshot(c))
        c.tick()
    if c.errors or c.sched.fault:
        raise AssertionError((c.errors, c.sched.fault))
    snapshots.append(snapshot(c))
    machine = Machine()
    machine.mem = ee.mem
    machine.reg[28], machine.reg[29] = 0x3e7f70, 0x3f8e00
    compared = 0

    def compare():
        nonlocal compared
        if compared >= len(snapshots):
            raise AssertionError('original sleeps exceeded C continuation count')
        for (start, _), expected, actual in zip(DOMAINS, snapshots[compared], snapshot(ee)):
            if expected != actual:
                offset = next(i for i, (x, y) in enumerate(zip(expected, actual)) if x != y)
                raise AssertionError(f'kind {kind}, dir {ident:x}, scenario {scenario}, slice {compared}: '
                                     f'{start+offset:08x} C={expected[offset]:02x} EE={actual[offset]:02x}')
        compared += 1

    def sleep(_):
        compare()
        ee.sched.tick()
        return 0

    def ext(cpu, pc):
        a, b, c, d = (cpu.reg[i] & 0xffffffff for i in range(4, 8))
        return ee.external(pc, a, b, c, d)

    machine.hooks = {
        0x103d60: lambda _: 1,
        0x103d80: lambda _: 0,  # run worker immediately after original scheduling returns
        0x103da0: lambda _: 0,
        0x1434e0: sleep,
        0x10f520: lambda _: ee.sched._sync(None, 1),
        0x10fd28: lambda _: 2,
        0x110268: lambda _: 0,
        0x10ff30: lambda cpu: ee.sched._read(None, *(cpu.reg[i] & 0xffffffff for i in range(4, 7)),
                                          cpu.get(cpu.reg[7] & 0xffffffff, 3)),
    }
    for pc in (0x119ac8, 0x1041e0, 0x152ae0):
        machine.hooks[pc] = lambda cpu, pc=pc: ext(cpu, pc)
    machine.reg[4], machine.reg[5] = kind, ident
    machine.run(0x130150)
    machine.run(0x130220, limit=200000)
    compare()
    if compared != len(snapshots) or c.sched.reads != ee.sched.reads or ee.sched.fault:
        raise AssertionError('EE and C yield count or optical call sequence differs')
    c_calls = [call for call in c.calls if call[0] != 0x103da0]
    if c_calls != ee.calls:
        raise AssertionError('EE and C cache, memset or upload call order differs')
    return compared, machine.executed


def negative_controls(folder, original):
    folder.mkdir(parents=True, exist_ok=True)
    nominal = (ROOT / 'src/resource_loader.c').read_text()
    results = []
    for name, test, old, new in CONTROLS:
        if old not in nominal:
            raise ValueError(f'missing control mutation anchor {name}')
        source = folder / f'{name}.c'
        with source.open('x') as out:
            out.write(nominal.replace(old, new, 1))
        lib = library(source=source, output=source.with_suffix('.so'))
        try:
            compare_job(lib, original, *test)
        except AssertionError as exc:
            results.append(dict(control=name, rejected=True, failure=str(exc),
                                source_sha256=hashlib.sha256(source.read_bytes()).hexdigest()))
        else:
            raise AssertionError(f'defect escaped comparison: {name}')
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf', type=Path, required=True)
    p.add_argument('--ps2dinfo', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--source', type=Path)
    p.add_argument('--negative-controls', action='store_true')
    a = p.parse_args()
    elf = a.elf.read_bytes()
    if hashlib.sha256(elf).hexdigest() != ORIGINAL_ELF:
        raise ValueError('Original boot ELF digest differs')
    segments, endian = shared_module(a.ps2dinfo).segments_from_elf(elf)
    if endian != 'little':
        raise ValueError('Wrong boot byte order')
    packet = json.loads((ROOT / 'analysis/resource_loading/functions.json').read_text())
    if packet['elf_sha256'] != ORIGINAL_ELF:
        raise ValueError('Wrong body manifest ELF digest')
    mapping = elf32(elf)
    bodies = set()
    for row in packet['functions']:
        start, end = (int(row[k], 16) for k in ('entry', 'end_exclusive'))
        off = va_to_offset(mapping, start, end-start)
        if not any(low <= start < end <= high and off == base + start - low for low, high, base in segments):
            raise ValueError('PS2DInfo ELF mapping differs')
        if hashlib.sha256(elf[off:off+end-start]).hexdigest() != row['sha256']:
            raise ValueError(f'Original bytes differ for {row["name"]}')
        bodies.update(range(start, end, 4))
    lib = library(output=a.output.with_suffix('.so'), source=a.source)
    cases = [(kind, ident, scenario) for kind, ident in (
        (0, 0), (1, 1), (1, 0x50), (2, 0xc6), (3, 0xc7), (4, 0), (5, 0x201),
        (6, 1), (6, 2), (7, 0x49), (7, 0x4c), (8, 0),
    ) for scenario in range(3)]
    slices, coverage = 0, set()
    for case in cases:
        count, executed = compare_job(lib, (elf, segments), *case)
        slices += count
        coverage.update(executed)
    # The branch at 12ffbc skips an alignment word at 12ffc4.
    padding = {0x12ffc4}
    missing = bodies - coverage - padding
    if missing:
        raise AssertionError(f'new original words were never executed: {sorted(hex(x) for x in missing)}')
    result = dict(schema='hg-m19-job-differential/v1', elf_sha256=ORIGINAL_ELF,
                  original_bytes=len(bodies)*4, new_words_executed=len(bodies & coverage), jobs=len(cases),
                  padding_words=[f'{pc:08x}' for pc in sorted(padding)],
                  sleep_and_return_comparisons=slices, result='passed')
    if a.negative_controls:
        result['controls'] = negative_controls(a.output.with_suffix('.controls'), (elf, segments))
    with a.output.open('x') as out:
        json.dump(result, out, indent=2)
        out.write('\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
