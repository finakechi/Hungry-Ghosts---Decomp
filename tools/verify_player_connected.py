#!/usr/bin/env python3
"""Compare connected player C bodies with authenticated original instructions.

The 175190 comparison covers only the selected state-0 entry path, with synthetic
callees preserving that entry state. It is not a complete reconstructed function.
"""
import argparse
import ctypes as C
import json
import random
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'tests')]
from resource_harness import Memory, ResourceEnv, machine, library
from test_player_connected import FUNCTIONS, ARITIES, RETURNS, ConnectedDriver, ConnectedTests, configure
from verify_resource_loader import shared_module, EXPECTED, clone, compare
from hg_evidence_safety import digest
from ee_subset import sx, MASK64


def setup(rng,data,segments,entry,case):
    mem=Memory(data,segments);a=0x500000
    mem.put(a,bytes(rng.randrange(256) for _ in range(0x540)))
    for address in (0x3fa1d0,0x3fa200,0x3fa204,0x46e12c):mem.store(address,rng.getrandbits(32))
    mem.store(0x3fa202,255 if case%2 else rng.randrange(255),1)
    mem.store(0x46e12d,case%4,1);mem.store(0x3f43fc,0x76543210)
    args=[a]
    if entry==0x139fe0:args=[a,rng.getrandbits(32)]
    elif entry==0x158ce0:
        mem.store(a+0x450,0x510000 if case%2 else 0);mem.store(a+0x454,0x520000 if case%3 else 0)
        mem.put(0x510000,bytes([0xaa])*0x30);mem.put(0x520000,bytes([0xbb])*0x30);args=[a,case%3]
    elif entry==0x15e190:
        sequence=rng.getrandbits(32);blend=rng.getrandbits(32)
        mem.store(a+0xb6,sequence&0xffff if case%2 else rng.randrange(65536),2);args=[blend,a,sequence]
    elif entry==0x13a2a0:
        mem.store(a+0x58,0x530000);index=case%4;mem.store(0x530000+index*4,0x540040)
        mem.put(0x540000,bytes(rng.randrange(256) for _ in range(192)))
        args=[a,0x540000+(case%9)*16,index]
    elif entry==0x159570:
        mem.store(a,rng.choice([0,1,2,3,7,255]),1)
        mem.store(a+0x448,rng.choice([0,1,2,3,4,5,255]),1)
        mem.store(a+0x2c3,rng.randrange(2),1)
        mem.store(a+0x49f,rng.choice([0,1,2,3,4,6,7,8,15]),1)
        mem.store(a+0x4fe,rng.randrange(2),1);mem.store(a+0x514,rng.randrange(3),1)
        mem.store(a+0x464,rng.randrange(2),1)
        mem.store(a+0x420,rng.choice([0,1,0x80000000,0xffffffff]))
        for off in (0x45a,0x414,0x416,0x4d8,0x4f0):mem.store(a+off,rng.choice([0,1,2,0xffff]),2)
        mem.store(0x3f9e3a,rng.choice([0,1,11,0x80,255]),1)
        mem.store(0x3eaee8,rng.randrange(2),1);mem.store(0x3eaed4,rng.choice([0,1,6,255]),1)
        mem.store(0x3eaef0,case%2);mem.store(0x3fa200,rng.choice([0,2,3,6,255]),1)
        mem.put(0x3eaf00,bytes(rng.randrange(256) for _ in range(60)))
        mem.store(0x46d908,case%8);mem.store(0x3e0738+(case%8)*2,rng.randrange(4),2)
        for pc,slot in RETURNS.items():mem.store(slot,rng.choice([0,1,7,0x80000000,0xffffffff]))
        mem.store(0x600110,rng.choice([0,7]));mem.store(0x600114,rng.choice([0,7]))
        args=[a,0x1ff0790]
        if case==1:
            # Eligible retained target: mask restoration would clear +464, so
            # explicitly keep every mask condition off to reach its publication.
            mem.store(0x3f9e3a,0,1);mem.store(0x3eaee8,0,1)
            mem.store(a+0x45a,0,2);mem.store(a+0x49f,0,1)
            mem.store(a+0x464,1,1);mem.store(a+0x420,0x123456)
    elif entry==0x175190:
        mem.store(a+0x19,0,1);mem.store(a+0x462,case%3,1);mem.store(a+0x4a3,case%4,1)
        mem.store(0x3fa200,(case//3)%3,1);mem.store(0x3fa201,(case//4)%4,1)
        mem.store(0x3eacec,case%2,1);mem.store(0x3eaef0,case%2*4)
        mem.store(0x60010c,case%7==0)
    return mem,args


def execute(lib,data,segments,entry,name,args,mem,mutation=None):
    cd=ConnectedDriver(clone(mem),mutation);md=ConnectedDriver(clone(mem),mutation)
    result=getattr(lib,name)(C.byref(cd.env),*args);m=machine(md)
    for pc in ARITIES:
        if pc==entry:continue
        def hook(cpu,pc=pc):
            values=[cpu.reg[i]&0xffffffff for i in (4,5,6,7)]
            if pc in (0x15e190,0x188550,0x15c540,0x12c030,0x17d930):values=[cpu.fpr[12],*values[:3]]
            elif pc in (0x13a060,0x15c410):values=[cpu.fpr[12],cpu.fpr[13],*values[:2]]
            value=md.external(pc,*values)
            if pc==0x120830:cpu.fpr[0]=value;return 0
            return sx(value,32)&MASK64
        m.hooks[pc]=hook
    original_args=args
    if entry==0x15e190:m.fpr[12]=args[0];original_args=args[1:]
    if entry==0x159570:original_args=args[:1]
    for i,value in enumerate(original_args,4):m.reg[i]=value
    saved={i:((i*0x12345678)<<64)|i for i in list(range(16,24))+[30]}
    saved.update({28:m.reg[28],29:m.reg[29]})
    for i,value in saved.items():m.reg[i]=value
    model_result=m.run(entry,limit=20000)
    if any(m.reg[i]!=value for i,value in saved.items()):raise AssertionError('callee-saved registers')
    if entry==0x15e190 and result!=(model_result&0xffffffff):raise AssertionError('return mismatch')
    compare(cd,md,f'{name}/{args}')
    if entry==0x159570 and cd.mem.get(args[1]+0x30,60)!=md.mem.get(args[1]+0x30,60):
        raise AssertionError('guest input-save frame differs')
    return m.executed


def run(lib,data,segments,packet):
    rng=random.Random(0x158a10159570);coverage=set();results=[]
    for entry,(name,nargs) in FUNCTIONS.items():
        count=1024 if entry==0x159570 else 256
        for case in range(count):
            mem,args=setup(rng,data,segments,entry,case)
            mutation=None
            # Some dispatches introduce or remove a mask condition, exercising
            # the original post-call reload and guest scratch preservation.
            if entry==0x159570 and case%3==0:mutation=(0x45a,case%2,2)
            coverage.update(execute(lib,data,segments,entry,name,args,mem,mutation))
        results.append(dict(entry=hex(entry),name=name,cases=count,result='passed'))
    expected={int(i['address'],16) for f in packet['functions'] if int(f['entry'],16) in FUNCTIONS for i in f['instructions']}
    if expected-coverage:raise AssertionError(f'Unexecuted complete-body addresses: {[hex(x) for x in sorted(expected-coverage)]}')
    selected=set()
    for case in range(128):
        mem,args=setup(rng,data,segments,0x175190,case)
        selected.update(execute(lib,data,segments,0x175190,'hg_player_equipment_enter',args,mem))
    return dict(result='passed',complete_bodies=results,complete_body_cases=sum(x['cases'] for x in results),
        instruction_addresses=len(expected),selected_state0=dict(entry='0x00175190',cases=128,
        complete_function=False,executed_addresses=len(selected),addresses=[hex(x) for x in sorted(selected)]))


def negative_controls(output):
    """Compile defects in the fixed C composition and require test rejection."""
    source=(ROOT/'src/resource_loader.c').read_text()
    folder=output.parent/(output.stem+'-controls');folder.mkdir()
    baseline=configure(library(output=folder/'baseline.so'))
    test=ConnectedTests('test_integrated_setup_square_and_following_update_with_control')
    test.lib=baseline;test.test_integrated_setup_square_and_following_update_with_control()
    changes=[
        ('wrong-square-edge-source','uint32_t edge=r16(e,0x3eaf04);',
            'uint32_t edge=r16(e,0x3eaf08);'),
        ('lost-transition-actor','case 0x159380:hg_player_apply_transition(&p->env,a);return 0;',
            'case 0x159380:hg_player_apply_transition(&p->env,0);return 0;'),
        ('wrong-subsequent-update-state','w8(e,a+0x19,1);\n    }\n    return 1;',
            'w8(e,a+0x19,2);\n    }\n    return 1;')]
    results=[]
    for name,old,new in changes:
        if source.count(old)!=1:raise AssertionError(f'{name}: mutation target must be unique')
        path=folder/(name+'.c');path.open('x').write(source.replace(old,new))
        lib=configure(library(source=path,output=folder/(name+'.so')))
        test=ConnectedTests('test_integrated_setup_square_and_following_update_with_control');test.lib=lib
        try:test.test_integrated_setup_square_and_following_update_with_control()
        except AssertionError as exc:
            results.append(dict(control=name,result='rejected',compiled=True,source_sha256=digest(path),
                test='test_integrated_setup_square_and_following_update_with_control',reason=str(exc)))
        else:raise AssertionError(name+' defect escaped')
    return dict(result='passed',baseline='passed',controls=results,
        sources={name:digest(ROOT/name) for name in ['src/resource_loader.c','src/resource_loader.h',
            'tests/test_player_connected.py','tools/verify_player_connected.py']},
        limitations=['Compiled synthetic integrated controls; no retail equivalence.'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path);p.add_argument('--ps2dinfo',type=Path)
    p.add_argument('--negative-controls',action='store_true',help='Compile synthetic integration defects; no retail inputs required')
    p.add_argument('--output',type=Path,required=True);p.add_argument('--source',type=Path)
    args=p.parse_args()
    if args.negative_controls:
        if args.source:p.error('--source cannot accompany --negative-controls')
        result=negative_controls(args.output)
        args.output.open('x').write(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2));return
    if args.elf is None or args.ps2dinfo is None:p.error('--elf and --ps2dinfo are required for instruction comparisons')
    if digest(args.elf)!=EXPECTED:raise ValueError('Wrong original ELF')
    packet=json.loads((ROOT/'analysis/player_connected/functions.json').read_text())
    shared=shared_module(args.ps2dinfo);data=args.elf.read_bytes()
    bytecheck=shared.verify(data,packet,require_all_bytes=True);segments,_=shared.segments_from_elf(data)
    result=run(configure(library(source=args.source,output=args.output.with_suffix('.so'))),data,segments,packet)
    result.update(original_bytes=bytecheck,sources={name:digest(ROOT/name) for name in
        ['src/resource_loader.c','src/resource_loader.h','tests/test_player_connected.py','tools/verify_player_connected.py']},
        limitations=['Synthetic engine/math dependencies; no retail equivalence.',
            '175190 is a state-0 split, with common callees required to preserve state 0.',
            'Callee-saved 128-bit registers checked. Stack saves excluded; active input-save frame compared.',
            'FP call operands and return values use explicit raw-bit host seams.'])
    args.output.open('x').write(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))

if __name__=='__main__':main()
