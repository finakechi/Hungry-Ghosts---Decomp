#!/usr/bin/env python3
"""M14 correction check: 001379f0 guard and threshold against original words."""
import argparse
import ctypes as C
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'tests')]
from ee_subset import Machine
from hg_evidence_safety import digest
from resource_harness import ResourceEnv,library
from verify_player_connected_runtime import CapturedMemory,ConnectedRuntimeDriver
from verify_resource_loader import EXPECTED,shared_module


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True)
    p.add_argument('--ps2dinfo',type=Path,required=True)
    p.add_argument('--capture',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.output.exists() or args.output.with_suffix('.so').exists():raise FileExistsError(args.output)
    if digest(args.elf)!=EXPECTED:raise ValueError('Wrong Japanese original executable')
    elf=args.elf.read_bytes();segments,_=shared_module(args.ps2dinfo).segments_from_elf(elf)
    lib=library(output=args.output.with_suffix('.so'))
    fn=lib.hg_counter_advance;fn.argtypes=[C.POINTER(ResourceEnv)];fn.restype=None
    cases=[]
    for guard,before in ((0,107998),(0,107999),(1,107999),(1,108000)):
        left=CapturedMemory.load_capture(args.capture)
        right=CapturedMemory(bytes(left.raw),bytes(left.scratch))
        for mem in (left,right):
            mem.store(0x3eaee8,guard,1)
            mem.store(0x3fcee8,1-guard,1)
            mem.store(0x46d930,before)
            mem.store(0x46e110,12)
            mem.store(0x46d908,1)
        original=Machine();original.mem=right;original.reg[28]=0x3e7f70
        original.reg[29]=0x1ff7ff0;right.store(0x1ff7ff0,0x00fff000,8)
        original.run(0x1379f0,stop=0x00fff000)
        driver=ConnectedRuntimeDriver(left,elf,segments)
        fn(C.byref(driver.env))
        driver.verify_executed(original.executed)
        diff=[a for a in range(0x1fe0000) if left.raw[a]!=right.raw[a]]
        row=dict(guard=guard,counter_before=before,
                 expected={hex(a):hex(right.load(a)) for a in (0x46d930,0x46e110,0x46d908)},
                 actual={hex(a):hex(left.load(a)) for a in (0x46d930,0x46e110,0x46d908)},
                 first_differences=[hex(a) for a in diff[:16]],difference_count=len(diff),
                 instruction_count=len(original.executed),result='passed' if not diff and not driver.errors else 'failed')
        cases.append(row);print(guard,before,row['result'],flush=True)
    ok=all(row['result']=='passed' for row in cases)
    sources=('src/resource_loader.c','src/resource_loader.h','tools/verify_counter_guard.py','tools/ee_subset.py')
    args.output.write_text(json.dumps(dict(schema='hg-m14-counter-guard/v2',
                                           result='passed' if ok else 'failed',
                                           elf_sha256=EXPECTED,capture_sha256=digest(args.capture),
                                           verification_sources={n:digest(ROOT/n) for n in sources},
                                           library_sha256=digest(args.output.with_suffix('.so')),
                                           cases=cases),indent=2)+'\n')
    return 0 if ok else 1


if __name__=='__main__':sys.exit(main())
