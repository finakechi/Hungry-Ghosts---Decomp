#!/usr/bin/env python3
"""Synthetic differential of M10's active dispatcher against original instructions."""
import argparse
import ctypes as C
import json
import random
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tests'),str(ROOT/'tools')]
from resource_harness import Memory, library, machine
from test_player_walk_camera import DispatchDriver, configure, P
from verify_resource_loader import EXPECTED, shared_module, clone, compare
from hg_evidence_safety import digest


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True);p.add_argument('--ps2dinfo',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--cases',type=int,default=1200)
    a=p.parse_args()
    if a.cases<1:raise ValueError('Require positive case count')
    if digest(a.elf)!=EXPECTED:raise ValueError('Wrong ELF')
    data=a.elf.read_bytes();segments,_=shared_module(a.ps2dinfo).segments_from_elf(data)
    lib=configure(library(output=a.output.with_suffix('.so')));rng=random.Random(0x15856010);executed=set()
    for case in range(a.cases):
        mem=Memory(data,segments)
        mem.put(P,bytes(rng.randrange(256) for _ in range(0x540)))
        mem.store(P+0x18,1,1);mem.store(0x3eaee8,case%2,1);mem.store(0x3f9f38,0,1)
        for address in (0x3f9e3f,0x3fa067):mem.store(address,rng.randrange(256),1)
        mem.store(P+0x3c0,rng.choice([0,0x3d567750,0x3f800000,0xbf800000]))
        cd,md=DispatchDriver(clone(mem)),DispatchDriver(clone(mem))
        effects=[(0x30,4,rng.getrandbits(32)),(0x428,4,rng.getrandbits(32)),
                 (0x4ac,1,rng.choice([0,1,127,128,255])),(0x45a,2,rng.choice([0,1,32767,32768,65535]))]
        cd.active_effects=md.active_effects=effects
        lib.hg_player_dispatch(C.byref(cd.env))
        m=machine(md)
        m.hooks[0x159570]=lambda cpu:md.external(0x159570,cpu.reg[4]&0xffffffff)
        def wrap(cpu):cpu.fpr[0]=md.external(0x1204e0,cpu.fpr[12]);return 0
        m.hooks[0x1204e0]=wrap
        saved={i:(i<<64)|i for i in range(16,24)}
        for i,v in saved.items():m.reg[i]=v
        m.run(0x158560)
        if any(m.reg[i]!=v for i,v in saved.items()):raise AssertionError('Saved register mismatch')
        compare(cd,md,f'active dispatcher case {case}');executed.update(m.executed)
    packet=json.loads((ROOT/'analysis/player_walk_camera/functions.json').read_text())
    shared_module(a.ps2dinfo).verify(data,packet,require_all_bytes=True)
    result=dict(result='passed',cases=a.cases,elf_sha256=EXPECTED,
        executed_addresses=[hex(x) for x in sorted(executed)],
        limitations=['Only state-1 active branch is reconstructed; callees are synthetic effects and return values.','Coordinator is surveyed, not reconstructed.'])
    with a.output.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print('PASSED',a.cases,'cases;',len(executed),'dispatcher addresses')

if __name__=='__main__':main()
