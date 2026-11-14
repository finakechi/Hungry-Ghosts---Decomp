"""Bounded original-instruction comparison for 001385b0; no runtime claim."""
import argparse
import ctypes as C
import json
import random
import sys
from pathlib import Path
from hg_evidence_safety import digest, select_export, receipt
from verify_resource_loader import ROOT, EXPECTED, shared_module, compare
sys.path.insert(0,str(ROOT/'tests'))
from resource_harness import Memory, ResourceEnv, library, machine
from test_resource_rebase import RebaseDriver

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('elf','exports','ps2dinfo','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();assert digest(a.elf)==EXPECTED
    shared=shared_module(a.ps2dinfo);data=a.elf.read_bytes();export=select_export(a.exports,0x1385b0)
    row=json.loads(export.read_text());packet=json.loads((ROOT/'analysis/resource_loader/functions.json').read_text())
    packet['functions']=[row];byte_check=shared.verify(data,packet,require_all_bytes=True)
    segments,_=shared.segments_from_elf(data);lib=library(output=ROOT/'local/m3/rebase.so')
    fn=lib.hg_resource_rebase;fn.argtypes=[C.POINTER(ResourceEnv),C.c_uint32,C.c_uint32];fn.restype=None
    rng=random.Random(0x1385b0)
    for case in range(256):
        cd,md=RebaseDriver(Memory(data,segments)),RebaseDriver(Memory(data,segments))
        count=1+case%5;block=0xb00000;base=rng.getrandbits(32)
        for driver in (cd,md):
            # identical deterministic fixture, with negative/wrapping products
            driver.mem.store(block+8,case*31);driver.mem.store(block+12,case*127)
            for i in range(count):
                ptr=block+i*128;driver.mem.store(ptr,0x60000000 if i==count-1 else 7)
                driver.mem.store(ptr+0x34,(case*0xabcdef)&0xffffffff)
                driver.mem.store(ptr+0x50,[0xffffffff,0x80000000,0x7fffffff,64][case%4])
                driver.mem.store(ptr+0x54,case*1031)
        fn(C.byref(cd.env),block,base)
        m=machine(md);m.hooks[0x1389d0]=lambda cpu:md.external(0x1389d0,cpu.reg[4]&0xffffffff)
        m.reg[4]=block;m.reg[5]=base;m.run(0x1385b0)
        compare(cd,md,'rebase '+str(case))
    result={'schema':'hg-rebase-check/v1','result':'passed','cases':256,'entry':'0x001385b0','byte_check':byte_check,
            'binding':receipt(ROOT,{'elf':a.elf,'export':export,'shared_verifier':a.ps2dinfo/'tools/verify_elf_evidence.py'},'differential',['verify_resource_rebase.py'],['001389d0 is mocked; no fresh runtime or full fixup equivalence.'])}
    with a.output.open('x') as fp:json.dump(result,fp,indent=2);fp.write('\n')
    print('PASS 256 rebase comparisons, 53 original instruction rows')
if __name__=='__main__':main()
