#!/usr/bin/env python3
"""M7 strict original-instruction comparisons; synthetic dependencies explicit."""
import argparse
import ctypes as C
import json
import random
from pathlib import Path
from verify_resource_loader import ROOT, EXPECTED, shared_module, clone, compare
from hg_evidence_safety import digest, receipt
from resource_harness import Memory, machine, library
from test_engine_preparation import configure, EngineDriver, EngineOperationDriver, FUNCTIONS, TASK, engine_pack
from test_resource_fixup import FRAME
from ee_subset import sx, MASK64

BODY_RANGES = {
    0x12fcf0: [(0x12fcf0,0x12fdd4)],
    0x12fde0: [(0x12fde0,0x12fe84)],
    0x12fe90: [(0x12fe90,0x12fefc),(0x12ff00,0x12ff14)],
    0x130020: [(0x130020,0x130084),(0x130088,0x1300fc),(0x130100,0x130150)],
}


def validate_packet(packet):
    """Require the whole claimed operation, including disjoint delay slots."""
    functions=packet['functions']
    if len(functions)!=len(BODY_RANGES) or {int(f['entry'],16) for f in functions}!=set(BODY_RANGES):
        raise ValueError('Missing or unexpected M7 function body')
    for fn in functions:
        entry=int(fn['entry'],16)
        ranges=[(int(r['start'],16),int(r['end_exclusive'],16)) for r in fn['body_ranges']]
        addresses=[int(i['address'],16) for i in fn['instructions']]
        expected=[pc for start,end in BODY_RANGES[entry] for pc in range(start,end,4)]
        if not fn['complete'] or ranges!=BODY_RANGES[entry] or addresses!=expected:
            raise ValueError('Incomplete M7 original body/ranges')


class WordBus(Memory):
    """Sparse synthetic guest bus for modulo-2^32 pointer cases, not a PS2 map."""
    def valid(self, address, size):
        return 0 <= address and size >= 0 and address+size <= 1 << 32


def copy_bus(mem):
    result = WordBus(mem.elf,mem.segments)
    result.pages = {k:bytearray(v) for k,v in mem.pages.items()}
    return result


def execute(lib, mem, name, coverage, task=TASK, change=None):
    cd,md = EngineDriver(copy_bus(mem)),EngineDriver(copy_bus(mem))
    cd.change_selector=md.change_selector=change
    getattr(lib,FUNCTIONS[name][1])(C.byref(cd.env),task)
    m=machine(md)
    m.hooks.pop(FUNCTIONS[name][0],None)
    m.reg[4]=sx(task,32)&MASK64
    saved={n:((n*0x12345678)<<64)|n for n in list(range(16,24))+[30]}
    saved.update({28:m.reg[28],29:m.reg[29]})
    for n,v in saved.items():m.reg[n]=v
    m.run(FUNCTIONS[name][0],limit=10000)
    if any(m.reg[n]!=v for n,v in saved.items()):
        raise AssertionError(name+': original callee-saved register mismatch')
    compare(cd,md,name);coverage.update(m.executed)


def run(lib,data,segments):
    rng=random.Random(0x1504607);coverage=set();counts={}
    for name in FUNCTIONS:
        for case in range(240):
            mem=WordBus(data,segments)
            task=0xfffffff0 if case%11==0 else TASK
            for off in range(0x50):mem.store((task+off)&0xffffffff,rng.randrange(256),1)
            mem.store(0x700000c4,case%4)
            mem.store(0x3fa1ba,case%3,1)
            kind=(case//4)%3
            mem.store(0x3fa1b0,kind,1)
            mem.store(0x3fa1b1,3 if case%2 else 0,1)
            mem.store(0x3fa1b2,3 if case%3 else 0,1)
            mem.store(0x3f9fef,(0x4d if kind==2 else 0x4c) if case%5==0 else 0,1)
            mem.store(0x46e12d,2 if case%3 else 0,1)
            mem.store(0x3fa202,255 if case%2 else 7,1)
            if name=='load_context':
                k=[0,1,2,3,255][case%5];sub=[0,1,2,255][case%4]
                mem.store(0x3fa200,k,1);mem.store(0x3fa201,sub,1)
                at=0x3e01c0+k*12+sub*4 if k<3 else 0x3e01e4+k*4
                mem.store(at,0xffffffff if case%3==0 else rng.getrandbits(32))
            if name=='load_indexed':
                ident=[0x207,0x307,0xffffff07,0xfffffe07][case%4]
                high=sx(ident,32)>>8
                mem.store((task+0x44)&0xffffffff,ident)
                mem.store((0x3e0200+(high-2)*4)&0xffffffff,0x500000)
                value=[19,0xffffffff,0x10013,0][case%4]
                mem.store(0x50001c,value)
                slot=[0,1,2,3,0xffff][(case//4)%5]
                mem.store(0x46e136,slot,2)
                mem.store(0x46e130+slot*2,value if case%3==0 else 0x55,2)
                # Deliberate alias of task phase bytes and ring publication.
                if case%19==0:task=0x46e0f8;mem.store(task+0x44,ident)
            execute(lib,mem,name,coverage,task,2 if case%7==0 else None)
            counts[name]=counts.get(name,0)+1
    # Actual worker -> wrappers -> general loaders -> real fixup/geometry/rebase.
    for case in range(4):
        cd=EngineOperationDriver(Memory(data,segments));engine_pack(cd)
        cd.mem.store(0x3f9fef,0x4c if case==1 else 0,1)
        cd.mem.store(TASK+0x32,8 if case>1 else 3,2)
        md=EngineOperationDriver(clone(cd.mem));md.files=dict(cd.files)
        lib.hg_resource_worker_fixed(C.byref(cd.env),FRAME)
        m=machine(md)
        for pc in [0x130900,0x131420,0x137ed0]+[v[0] for v in FUNCTIONS.values()]:m.hooks.pop(pc,None)
        m.run(0x130220,limit=1000000)
        compare(cd,md,'connected engine worker');coverage.update(m.executed)
    return counts,coverage


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',required=True,type=Path);p.add_argument('--ps2dinfo',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path);p.add_argument('--source',type=Path)
    a=p.parse_args();data=a.elf.read_bytes()
    if digest(a.elf)!=EXPECTED:raise ValueError('Wrong original ELF')
    packet_path=ROOT/'analysis/engine_preparation/functions.json'
    packet=json.loads(packet_path.read_text());validate_packet(packet);shared=shared_module(a.ps2dinfo)
    checked=shared.verify(data,packet,require_all_bytes=True)
    segments,endian=shared.segments_from_elf(data)
    if endian!='little':raise ValueError('Wrong byte order')
    lib=configure(library(source=a.source,output=a.output.with_suffix('.so')))
    counts,coverage=run(lib,data,segments)
    expected={int(i['address'],16) for f in packet['functions'] for i in f['instructions']}
    missing=expected-coverage
    if missing:raise AssertionError('Unexecuted M7 instructions: '+str(sorted(hex(x) for x in missing)))
    result=dict(schema='hg-engine-differential/v1',result='passed',elf_sha256=EXPECTED,
        seed='0x1504607',cases_per_function=counts,connected_workers=4,
        original_bytes=checked,instruction_addresses_executed=len(expected),
        comparison='All non-stack bus pages, ordered calls/defined arguments, original callee-saved 128-bit registers; wrapper returns are void.',
        limitations=['Synthetic kernel/CDVD/audio completion contracts; not original-game execution.',
            'WordBus wrap cases are synthetic addressing checks, not valid PS2 physical mappings.'])
    result['binding']=receipt(ROOT,{'elf':a.elf,'packet':packet_path,
        'shared_verifier':a.ps2dinfo/'tools/verify_elf_evidence.py'},'differential',
        ['verify_engine_preparation.py'],result['limitations'])
    with a.output.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps({k:v for k,v in result.items() if k!='binding'}))


if __name__=='__main__':main()
