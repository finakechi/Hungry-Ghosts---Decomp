#!/usr/bin/env python3
"""Compare setup orchestration against authenticated original EE instructions."""
import argparse
import ctypes as C
import json
import random
from pathlib import Path
from verify_resource_loader import ROOT, EXPECTED, shared_module, clone, compare
from resource_harness import Memory, machine, library
from test_actor_setup import FUNCTIONS, ARITIES, SetupDriver, configure
from ee_subset import sx, MASK64
from hg_evidence_safety import digest, receipt

RANGES={0x18a330:(0x18a330,0x18a37c),0x18a4c0:(0x18a4c0,0x18a5a8),
        0x18a5b0:(0x18a5b0,0x18a628),0x139fc0:(0x139fc0,0x139fc8),
        0x1235c0:(0x1235c0,0x1235f0)}
RANGES={pc:[span] for pc,span in RANGES.items()}
RANGES[0x18c280]=[(0x18c280,0x18c2e4),(0x18c2e8,0x18c35c),
    (0x18c360,0x18c394),(0x18c398,0x18c3ac),(0x18c3b0,0x18c3ec),
    (0x18c3f0,0x18c4a4),(0x18c4a8,0x18c4bc),(0x18c4c0,0x18c514)]
RANGES[0x15de80]=[(0x15de80,0x15deac),(0x15deb0,0x15e0c0)]
RANGES[0x141e40]=[(0x141e40,0x141e84),(0x141e88,0x141ee0)]
RANGES[0x138370]=[(0x138370,0x1383b4),(0x1383b8,0x138454),
    (0x138458,0x1384c4),(0x1384c8,0x1384e4),(0x1384e8,0x138504),
    (0x138508,0x138524),(0x138528,0x13855c)]
RANGES.update({0x141de0:[(0x141de0,0x141e10)],
    0x139d20:[(0x139d20,0x139e44),(0x139e48,0x139e54),(0x139e58,0x139fb4)],
    0x13a0a0:[(0x13a0a0,0x13a1dc)],0x267300:[(0x267300,0x26744c)]})


def validate_packet(packet):
    fs=packet['functions']
    if len(fs)!=len(RANGES) or {int(f['entry'],16) for f in fs}!=set(RANGES):
        raise ValueError('Missing or unexpected setup function')
    for f in fs:
        spans=RANGES[int(f['entry'],16)]
        if not f['complete'] or [(int(r['start'],16),int(r['end_exclusive'],16)) for r in f['body_ranges']]!=spans:
            raise ValueError('Incomplete setup range')
        if [int(i['address'],16) for i in f['instructions']]!=[pc for start,end in spans for pc in range(start,end,4)]:
            raise ValueError('Missing setup instruction/delay slot')


def run(lib,data,segments):
    rng=random.Random(0x18a330);coverage=set();counts={}
    for entry,(name,arity) in FUNCTIONS.items():
        for case in range(1024 if entry==0x18c280 else 512 if entry==0x138370 else 128):
            mem=Memory(data,segments);a=0x500000
            mem.put(a,bytes(rng.randrange(256) for _ in range(0x300)))
            mem.store(a+0x2a,3,2)
            mem.store(0x3f430c,0x600000);mem.store(0x3f431c,0x700000)
            ids=[rng.getrandbits(32) if case&(1<<i) else 0 for i in range(3)]
            mode=rng.getrandbits(32);seq=0 if case%2 else rng.getrandbits(32)|1
            args={0x18a330:(a,*ids,mode,seq),0x18a4c0:(a,*ids),
                  0x18a5b0:(a,mode,seq),0x139fc0:(a,mode),0x1235c0:(a,case%16),
                  0x18c280:([0,1,2,3,0xffffffff][case%5],),0x15de80:(a,),
                  0x141e40:(a,[0,1,2,4,0xffffffff,0xfffffffe][case%6]),
                  0x141de0:(),0x139d20:(a,0x600000,0x600800),
                  0x13a0a0:(a,),0x267300:(a,0 if case%9==0 else 0x600000),
                  0x138370:(a,[0x1000000,0x2000000,0x3000000,0x4000000,0x5000000,0x9000001,0xff000000][case%7])}[entry]
            if entry==0x141de0:
                mem.store(0x4d8140,0x700004);mem.store(0x700000,case*0x1234567)
                mem.store(0x4d8144,case)
            if entry==0x139d20:
                mem.store(0x600008,case%4);mem.store(0x600038,0 if case%5==0 else 0x1000)
                mem.store(0x601000,case%2);mem.store(0x601010,1 if case%3 else 0)
                mem.store(a+2,[2,4,5][(case//3)%3],1);mem.store(a+4,(case//2)%4)
                for off in range(0x20,0x3c,4):mem.store(0x601000+off,rng.choice([0,0x3f000000,0x3f800000,0x40000000,0xbf800000,0xc0000000]))
                mem.store(0x601040,[0,0x3dcccccc,0x3dcccccd,0x3f800000,0x42480000,0x42480001,0xbf800000][case%7])
            if entry==0x13a0a0:
                mem.store(a+0x10,0x600000);mem.store(0x60000c,0x1000)
                mem.store(0x600008,[0,1,3,0x8000,0xffff][case%5])
                mem.store(a+0x38,case%2,1);mem.store(a+0x58,0x700000)
                for i in range(4):mem.store(0x700000+i*4,0x710000+i*0x100)
            if entry==0x267300:
                for i in range(3):mem.store(0x600000+i*2,1 if case&(1<<i) else 0,2)
                for off,value in ((8,0x1000),(0xc,0x1010),(0x10,0x1020),(0x14,0x1018),(0x18,0x1100),(0x1c,0x1200)):
                    mem.store(0x600000+off,value)
                mem.store(0x600110,0 if case%3==0 else 0x700000)
            if entry==0x141e40:
                mem.store(a+0x54,0);mem.store(0x600108,a)
                mem.store(0x4d8144,[0,2,8,0xffffffff][(case//6)%4])
            if entry==0x138370:
                count=rng.choice([0,1,3,0xffff]);mem.store(a,count)
                for i in range(16):
                    mem.store(a+0x100+i*8,0 if case%11==0 else 0x700000+i*0x100)
                    mem.store(a+0x104+i*8,[0x1000000,0x9000000,0xff000000,0][(case+i//2)%4])
                    for off in (0xc,0x30,0x34,0x38):mem.store(0x700000+i*0x100+off,0 if case%3 else 0x80)
            if entry==0x15de80:
                mem.store(0x3fa067,case%7==0,1)
                mem.store(0x3f9fee,case%16,1)
                mem.store(0x3eaf04,0x20 if case%3 else 0,2)
                for off in (0xc,0x10,0x1c,0x20):
                    mem.store(0x3eaf00+off,rng.choice([0,0x80000000,0x3f000000,0xbf000000,0x3f000001,0xbf000001,0x3f800000,0xbf800000]))
                for off in (0x4a8,0x4aa):mem.store(a+off,rng.choice([0,1,0xffff,40]),2)
                for off in (0x4a4,0x4a5,0x4a6):mem.store(a+off,rng.choice([0,1,2,255]),1)
            if entry==0x18c280:
                mem.store(0x3eb058,0 if case%29==0 else a)
                mem.store(0x3eb05c,0 if case%11==0 else 0x600000)
                mem.store(0x60002b,[0,0x10,0x20,0x30,0x80][(case//5)%5],1)
                mem.store(0x600000,0x800000 if case&32 else 0x24)
                mem.store(a+0xdc,[0x3f800000,0x3f000000,0xbf800000,0,0x40000000][(case//7)%5])
                mem.store(0x60001c,0x3f800000 if case&64 else 0x3f000000)
                mem.store(a+0xe0,0x1000000 if case&128 else 0)
                mem.store(0x3eb054,[0,1023,1024,1025][(case//3)%4])
                mem.store(0x600100,(case//9)%3);mem.store(0x600104,(case//17)%2)
            cd,md=SetupDriver(clone(mem)),SetupDriver(clone(mem))
            result=getattr(lib,name)(C.byref(cd.env),*args)
            m=machine(md)
            for pc in ARITIES:
                m.hooks[pc]=lambda cpu,pc=pc:sx(md.external(pc,*(cpu.reg[i]&0xffffffff for i in (4,5,6,7))),32)&MASK64
            # Execute original setup callees in the six-argument parent.
            for pc in FUNCTIONS:m.hooks.pop(pc,None)
            if entry in (0x18a330,0x18a4c0):
                m.hooks[0x138370]=lambda cpu:md.external(0x138370,*(cpu.reg[i]&0xffffffff for i in (4,5,6,7)))
            for pc in (0x139d20,0x267300,0x13a0a0,0x141e40,0x141de0):
                if pc!=entry:
                    m.hooks[pc]=lambda cpu,pc=pc:sx(md.external(pc,*(cpu.reg[i]&0xffffffff for i in (4,5,6,7))),32)&MASK64
            # Isolated orchestration keeps its lower-level two-argument seams.
            if entry in (0x18a330,0x18a4c0,0x18a5b0):
                for pc in (0x139fc0,0x1235c0):
                    m.hooks[pc]=lambda cpu,pc=pc:md.external(pc,*(cpu.reg[i]&0xffffffff for i in (4,5,6,7)))
            for i,v in enumerate(args,4):m.reg[i]=sx(v,32)&MASK64
            saved={i:((i*0x12345678)<<64)|i for i in list(range(16,24))+[30]}
            saved.update({28:m.reg[28],29:m.reg[29]})
            for i,v in saved.items():m.reg[i]=v
            ee=m.run(entry,limit=20000)&0xffffffff
            if entry in (0x15de80,0x138370,0x141e40,0x141de0) and result!=ee:
                raise AssertionError(f'{name}/{case}: return C={result:#x}, EE={ee:#x}')
            if any(m.reg[i]!=v for i,v in saved.items()):raise AssertionError('Callee-saved register mismatch')
            compare(cd,md,f'{name}/{case}');coverage.update(m.executed)
            counts[name]=counts.get(name,0)+1
    return counts,coverage


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True);p.add_argument('--ps2dinfo',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--source',type=Path)
    a=p.parse_args()
    if digest(a.elf)!=EXPECTED:raise ValueError('Wrong original ELF')
    packet_path=ROOT/'analysis/actor_setup/functions.json';packet=json.loads(packet_path.read_text())
    validate_packet(packet);shared=shared_module(a.ps2dinfo);data=a.elf.read_bytes()
    checked=shared.verify(data,packet,require_all_bytes=True);segments,endian=shared.segments_from_elf(data)
    if endian!='little':raise ValueError('Wrong byte order')
    lib=configure(library(source=a.source,output=a.output.with_suffix('.so')))
    counts,coverage=run(lib,data,segments)
    expected={int(i['address'],16) for f in packet['functions'] for i in f['instructions']}
    if expected-coverage:raise AssertionError('Unexecuted setup instructions: '+str(sorted(expected-coverage)))
    limits=['Isolated original call seams use synthetic dependencies; fixed composition tested separately.',
            'Stack save slots excluded; full 128-bit callee-saved registers checked.',
            'Public C arguments are 32-bit guest values, sign-extended at EE entry.',
            'FP inputs finite normal or zero; binding scale excludes overflow; no exceptional flags/timing.',
            'No input-to-active-update retail equivalence or complete M8 claim.']
    result=dict(schema='hg-actor-setup-differential/v1',result='passed',elf_sha256=EXPECTED,
                seed='0x18a330',cases_per_function=counts,original_bytes=checked,
                instruction_addresses_executed=len(expected),limitations=limits)
    result['binding']=receipt(ROOT,{'elf':a.elf,'packet':packet_path,
        'shared_verifier':a.ps2dinfo/'tools/verify_elf_evidence.py'},'differential',['verify_actor_setup.py'],limits)
    with a.output.open('x') as out:json.dump(result,out,indent=2);out.write('\n')
    print(json.dumps({k:v for k,v in result.items() if k!='binding'}))


if __name__=='__main__':main()
