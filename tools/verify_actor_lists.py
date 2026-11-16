#!/usr/bin/env python3
"""M8 actor list instruction comparisons; no retail runtime equivalence claim."""
import argparse
import ctypes as C
import json
import random
from pathlib import Path
from verify_resource_loader import ROOT, EXPECTED, shared_module, clone, compare
from hg_evidence_safety import digest, receipt
from resource_harness import Memory, machine, library
from test_actor_lists import FUNCTIONS, configure, ActorDriver
from ee_subset import sx, MASK64

BODY_RANGES = {
    0x142810: [(0x142810,0x1428f0)],
    0x1428f0: [(0x1428f0,0x14296c)],
    0x142970: [(0x142970,0x1429b0)],
    0x1429b0: [(0x1429b0,0x1429f4),(0x1429f8,0x142a34),
               (0x142a38,0x142a5c),(0x142a60,0x142a9c),
               (0x142aa0,0x142abc),(0x142ac0,0x142b08)],
    0x1430a0: [(0x1430a0,0x1430f8)],
    0x143100: [(0x143100,0x143150)],
    0x143150: [(0x143150,0x1431a0)],
    0x1431a0: [(0x1431a0,0x1431f0)],
}


def validate_packet(packet):
    functions=packet['functions']
    if len(functions)!=len(BODY_RANGES) or {int(f['entry'],16) for f in functions}!=set(BODY_RANGES):
        raise ValueError('Missing or unexpected actor function')
    for f in functions:
        ranges=BODY_RANGES[int(f['entry'],16)]
        if not f['complete'] or [(int(r['start'],16),int(r['end_exclusive'],16)) for r in f['body_ranges']]!=ranges:
            raise ValueError('Incomplete actor body')
        if [int(i['address'],16) for i in f['instructions']]!=[pc for start,end in ranges for pc in range(start,end,4)]:
            raise ValueError('Missing actor instruction/delay slot')


def run(lib, data, segments):
    rng = random.Random(0x1504608); coverage = set(); counts = {}
    for entry, (name, arity) in FUNCTIONS.items():
        for case in range(160):
            mem = Memory(data, segments); actor = 0x500000
            for a in (actor, actor+0x400):
                mem.put(a, bytes(rng.randrange(256) for _ in range(0x300)))
                mem.store(a+0x29, case%4, 1)
            mem.put(0x539430, bytes(56))
            mem.store(0x3f9772, 99, 1)
            args = ()
            if entry == 0x142810:
                base = 0x4d8150
                # Alias selected tail with actor+20 or actor+24: implementations
                # that cache the first tail load incorrectly fail these cases.
                if case%8 in (2, 3):
                    base = actor+(0x20 if case%8 == 2 else 0x24)-(0x6130c if case%4 == 2 else 0x612fc)
                args = (actor, base, case%2)
                off = (0x61308 if case%4 == 2 else 0x612f8)+(8 if case%2 else 0)
                mem.store(base+off, 0)
                mem.store(base+off+4, actor+0x400 if case%3 else 0)
            elif entry in (0x1428f0, 0x142970):
                args = (actor, rng.getrandbits(32), rng.getrandbits(32), case%3)
                for head in (0x539448, 0x539450, 0x539458, 0x539460):
                    mem.store(head+4, actor+0x400 if case%3 else 0)
            elif entry == 0x1429b0:
                pool = [0, 1, 2, 3, 0x80, 0x81, 0xff, 0x100][case%8]
                args = (pool, rng.getrandbits(32), rng.getrandbits(32))
                for head in (0x539430, 0x539434, 0x539438):
                    mem.store(head, 0 if case%5 == 0 else actor)
                    mem.store(head+12, rng.getrandbits(32))
                mem.store(actor+0x24, actor+0x400)
            else:
                head = {0x1430a0:0x539448, 0x143100:0x539450,
                        0x143150:0x539458, 0x1431a0:0x539460}[entry]
                mem.store(head, actor if case%3 else 0)
                for a, pc, nxt in ((actor, 0x100100, actor+0x400), (actor+0x400, 0x100200, 0)):
                    mem.store(a+0x1c, pc); mem.store(a+0x24, nxt)
            cd, md = ActorDriver(clone(mem)), ActorDriver(clone(mem))
            result = getattr(lib, name)(C.byref(cd.env), *args)
            m = machine(md)
            for pc in (0x100100, 0x100200):
                m.hooks[pc] = lambda cpu, pc=pc: md.external(pc, cpu.reg[4]&0xffffffff)
            for i, v in enumerate(args, 4):m.reg[i] = sx(v, 32)&MASK64
            saved = {i: ((i*0x12345678)<<64)|i for i in list(range(16,24))+[30]}
            saved.update({28:m.reg[28],29:m.reg[29]})
            for i,v in saved.items():m.reg[i]=v
            ee = m.run(entry, limit=20000)&0xffffffff
            if entry == 0x1429b0 and result != ee:raise AssertionError('Allocation return mismatch')
            if any(m.reg[i] != v for i,v in saved.items()):raise AssertionError('Callee-saved register mismatch')
            compare(cd, md, f'{name}/{case}'); coverage.update(m.executed)
            counts[name] = counts.get(name, 0)+1
    return counts, coverage


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf', type=Path, required=True)
    p.add_argument('--ps2dinfo', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--source', type=Path)
    a=p.parse_args()
    if digest(a.elf) != EXPECTED:raise ValueError('Wrong original ELF')
    packet_path=ROOT/'analysis/actor_lists/functions.json'
    packet=json.loads(packet_path.read_text()); data=a.elf.read_bytes()
    validate_packet(packet)
    shared=shared_module(a.ps2dinfo)
    checked=shared.verify(data,packet,require_all_bytes=True)
    segments,endian=shared.segments_from_elf(data)
    if endian!='little':raise ValueError('Wrong byte order')
    lib=configure(library(source=a.source,output=a.output.with_suffix('.so')))
    counts,coverage=run(lib,data,segments)
    expected={int(i['address'],16) for f in packet['functions'] for i in f['instructions']}
    if expected-coverage:raise AssertionError('Unexecuted instructions: '+str(sorted(hex(x) for x in expected-coverage)))
    limits=['Synthetic external memset and actor callbacks; not original-game execution.',
            'Stack save slots excluded from state comparison; saved 128-bit registers checked.',
            'Actor callback arguments beyond a0 are unspecified and not compared.']
    result=dict(schema='hg-actor-lists-differential/v1',result='passed',elf_sha256=EXPECTED,
                seed='0x1504608',cases_per_function=counts,original_bytes=checked,
                instruction_addresses_executed=len(expected),limitations=limits)
    result['binding']=receipt(ROOT,{'elf':a.elf,'packet':packet_path,
        'shared_verifier':a.ps2dinfo/'tools/verify_elf_evidence.py'},'differential',
        ['verify_actor_lists.py'],limits)
    with a.output.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps({k:v for k,v in result.items() if k!='binding'}))


if __name__=='__main__':main()
