#!/usr/bin/env python3
"""Differential check of ordinary-player C against original EE instructions."""
import argparse
import ctypes as C
import json
import random
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'tests')]
from resource_harness import Memory, ResourceEnv, machine, library
from test_player_ordinary import FUNCTIONS, ARITIES, OrdinaryDriver, configure
from verify_resource_loader import shared_module, EXPECTED, clone, compare
from hg_evidence_safety import digest
from ee_subset import sx, MASK64

CONTROL_SCHEMA = 'hg-ordinary-a1-zero-control/v1'


class CallTraceMismatch(AssertionError):
    def __init__(self, function, case, actual, expected):
        self.difference = dict(function=function, case=case, actual=actual, expected=expected)


def m8_a1_zero_difference(difference):
    expected = dict(
        function='hg_player_ordinary_update', case=1,
        actual=[['call', 0x13a050, 0x3f800000, 0x500090], ['call', 0x15fe00, 0x500000, 0]],
        expected=[['call', 0x13a050, 0x3f800000, 0x500090], ['call', 0x15fe00, 0x500000, 1]],
    )
    return difference == expected and all(type(value) is int for value in (
        difference['case'], difference['actual'][1][-1], difference['expected'][1][-1]))


def run(lib,data,segments,packet, *, record_trace=False):
    rng=random.Random(0x159ad0);coverage=set();cases=0
    for entry,name in FUNCTIONS.items():
        for case in range(512):
            mem=Memory(data,segments);a=0x500000
            mem.put(a,bytes(rng.randrange(256) for _ in range(0x540)))
            mem.store(a+0x19,case%5,1);mem.store(a+0x1a,(case//5)%4,1)
            mem.store(a+0x449,(case//20)%7 if case%3 else 255,1)
            mem.store(a+0x4ad,case%11==0,1)
            mem.store(0x3eaee8,case%13==0,1);mem.store(0x3eaeb0,case%17==0,1)
            mem.store(0x3eaf04,rng.randrange(256),2)
            mem.store(0x3eaec8,32,2);mem.store(0x3eaed0,128,2)
            mem.store(0x3f9e3f,case%19==0,1);mem.store(0x3f9f38,rng.getrandbits(32))
            for off in range(3):mem.store(0x600100+off*4,rng.choice([0,0,1,0x700000,0xffffffff]))
            if case==6:
                mem.store(0x3eaee8,1,1);mem.store(a+0x4ad,0,1);mem.store(0x600104,0)
            cd=OrdinaryDriver(clone(mem));md=OrdinaryDriver(clone(mem))
            getattr(lib,name)(C.byref(cd.env),a)
            m=machine(md)
            for pc in ARITIES:
                if pc==entry:continue
                def hook(cpu,pc=pc):
                    args=[cpu.reg[i]&0xffffffff for i in (4,5,6,7)]
                    # Raw FP bits precede integer arguments on the host bus.
                    if pc in (0x13a050,0x15e190):args=[cpu.fpr[12],*args[:3]]
                    return sx(md.external(pc,*args),32)&MASK64
                m.hooks[pc]=hook
            m.reg[4]=a
            saved={i:((i*0x12345678)<<64)|i for i in list(range(16,24))+[30]}
            saved.update({28:m.reg[28],29:m.reg[29]})
            for i,v in saved.items():m.reg[i]=v
            m.run(entry,limit=20000)
            if any(m.reg[i]!=v for i,v in saved.items()):raise AssertionError('callee-saved registers')
            if record_trace:
                if cd.errors or md.errors:
                    raise RuntimeError((cd.errors, md.errors))
                if cd.calls != md.calls:
                    raise CallTraceMismatch(name, case, [list(call) for call in cd.calls],
                                            [list(call) for call in md.calls])
            compare(cd,md,f'{name}/{case}');coverage.update(m.executed);cases+=1
    expected={int(i['address'],16) for f in packet['functions'] for i in f['instructions']}
    if expected-coverage:raise AssertionError(f'Unexecuted instructions: {sorted(expected-coverage)}')
    return cases,len(expected)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True);p.add_argument('--ps2dinfo',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--source',type=Path)
    p.add_argument('--m8-a1-zero-control', action='store_true',
                   help='Require the historical case-1 a1 trace divergence and write a typed result')
    a=p.parse_args()
    if a.m8_a1_zero_control:
        if not a.source:
            p.error('--m8-a1-zero-control requires --source')
        if any(path.exists() or path.is_symlink() for path in (a.output, a.output.with_suffix('.so'))):
            raise FileExistsError('Fresh control output required')
    if digest(a.elf)!=EXPECTED:raise ValueError('Wrong ELF')
    packet=json.loads((ROOT/'analysis/player_ordinary/functions.json').read_text())
    shared=shared_module(a.ps2dinfo);data=a.elf.read_bytes()
    bytecheck=shared.verify(data,packet,require_all_bytes=True);segments,_=shared.segments_from_elf(data)
    lib=configure(library(source=a.source,output=a.output.with_suffix('.so')))
    if a.m8_a1_zero_control:
        try:
            run(lib,data,segments,packet,record_trace=True)
        except CallTraceMismatch as exc:
            difference = exc.difference
            result = 'measured-divergence' if m8_a1_zero_difference(difference) else 'other-trace-divergence'
        else:
            difference, result = None, 'equal'
        recorded = dict(schema=CONTROL_SCHEMA, result=result, difference=difference, elf_sha256=EXPECTED)
        with a.output.open('x') as f:
            json.dump(recorded, f, indent=2)
            f.write('\n')
        print(json.dumps(recorded))
        raise SystemExit(0 if result == 'measured-divergence' else 1)
    cases,addresses=run(lib,data,segments,packet)
    result=dict(result='passed',cases=cases,instruction_addresses=addresses,original_bytes=bytecheck,
        limitations=['Synthetic external dependencies; no complete gameplay equivalence.',
        'Raw f12 bits precede integer arguments on host bus for 13a050 and 15e190.',
        'Stack save slots omitted; full 128-bit callee-saved registers checked.'])
    a.output.open('x').write(json.dumps(result,indent=2)+'\n');print(json.dumps(result))

if __name__=='__main__':main()
