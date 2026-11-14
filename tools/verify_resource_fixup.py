#!/usr/bin/env python3
"""M4 original-byte and instruction differential checks; no runtime claims.

Uses the M1-M3 runner and bus, never translated Ghidra pseudocode as an oracle.
"""
import argparse
import ctypes as C
import json
import random
import sys
from pathlib import Path
from hg_evidence_safety import digest, select_export, receipt
from verify_resource_loader import ROOT, EXPECTED, shared_module, compare, clone
sys.path.insert(0,str(ROOT/'tests'))
from resource_harness import Memory, machine, library
from test_resource_fixup import FRAME, FUNCTIONS, configure, FixupDriver, flk

SYMBOLS={n:('hg_resource_'+n if n in ('fixup','map_address','base_delta') else 'hg_'+n) for n in FUNCTIONS}

def evidence(exports):
    packet=json.loads((ROOT/'analysis/resource_loader/functions.json').read_text())
    packet.pop('binding',None);packet['functions']=[]
    packet['provenance'].update(source='Read-only HGExport from preserved M2 project; ELF checked with pinned verifier.',
        retrieval_method='headless Ghidra -readOnly -noanalysis',
        limitations=['Analyst names; external geometry/material 001386e0 and upload 00152ae0 remain boundaries.',
                     'Explicit guest scratch parameters adapt original stack locals; no byte matching.'])
    for name,(entry,_,_) in FUNCTIONS.items():
        selected=select_export(exports,entry);r=json.loads(selected.read_text())
        packet['functions'].append(dict(entry=r['entry'],name=SYMBOLS[name],complete=True,
            body_ranges=r['body_ranges'],instructions=r['instructions'],
            references=[{**v,'ghidra_type':v['type'],'type':'call' if 'CALL' in v['type'] else 'unknown'} for v in r['references_to']],
            interpretation='M4 reconstruction; scope and external boundaries in docs/M4.md.',
            verification_status='original-byte-verified',source='src/resource_loader.c',
            original_listing_sha256=r['listing_sha256'],snapshot_sha256=digest(selected)))
    return packet

def execute(lib,data,segments,name,args,seedmem,coverage):
    cd=FixupDriver(clone(seedmem),lib);md=FixupDriver(clone(seedmem),lib)
    result=getattr(lib,SYMBOLS[name])(C.byref(cd.env),*args)
    if cd.errors:raise AssertionError(cd.errors)
    m=machine(md)
    m.hooks.pop(0x137ed0,None)
    for pc in (0x1386e0,0x152ae0):
        m.hooks[pc]=lambda cpu,pc=pc:md.external(pc,*(cpu.reg[i]&0xffffffff for i in (4,5)))
    original_args=args[:3] if name=='fixup' else args[:1] if name=='map_address' else args
    from ee_subset import sx, MASK64
    for n,v in enumerate(original_args,4):m.reg[n]=sx(v,32)&MASK64
    if name=='fixup':m.reg[29]=args[3]+0x240
    if name=='map_address':m.reg[29]=args[1]+0x20
    # Original ABI must restore all s0..s8 lanes, gp and sp. Seed 128-bit s regs.
    saved={n:(0x123456789abcdef0<<64)|(n*0x1234567) for n in list(range(16,24))+[30]}
    for n,v in saved.items():m.reg[n]=v
    saved.update({28:m.reg[28],29:m.reg[29]})
    answer=m.run(FUNCTIONS[name][0],limit=300000)
    for n,v in saved.items():
        if m.reg[n]!=v:raise AssertionError(f'{name}: original ABI register {n} not restored')
    if FUNCTIONS[name][2] and result!=(answer&0xffffffff):
        raise AssertionError(f'{name}: return C {result:08x} EE {answer&0xffffffff:08x}; args {args}')
    compare(cd,md,name+str(args));coverage.update(m.executed)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('elf','exports','ps2dinfo','output'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--packet',type=Path)
    p.add_argument('--source',type=Path,help='Disposable C source for negative controls')
    a=p.parse_args()
    if digest(a.elf)!=EXPECTED:raise ValueError('Wrong original executable')
    shared=shared_module(a.ps2dinfo);data=a.elf.read_bytes();segments,_=shared.segments_from_elf(data)
    packet=evidence(a.exports);byte_check=shared.verify(data,packet,require_all_bytes=True)
    if a.packet:
        with a.packet.open('x') as f:json.dump(packet,f,indent=2);f.write('\n')
    lib=configure(library(source=a.source,output=ROOT/'local/m4/fixup.so'));rng=random.Random(0x137ed0)
    coverage=set();counts={n:0 for n in FUNCTIONS}
    def check(name,args,mem):
        execute(lib,data,segments,name,args,mem,coverage);counts[name]+=1
    for case in range(320):
        mem=Memory(data,segments)
        types=rng.choice([(9,),(9,1,9,8,6),(1,1),(8,6,0),(9,9,9),(9,1,255)])
        block=flk(mem,types=types,links=1+case%5,size=rng.choice([0x400,0x401,0x47f,0xffffff80,0xfffffff0]))
        for i,kind in enumerate(types):
            q=block+0x200+i*0x400
            if kind==9:
                old=rng.getrandbits(32);mem.store(q+8,old);mem.store(q+12,(old+rng.randrange(257))&0xffffffff)
                for j in range(1+case%5):
                    mem.store(q+j*0x80+0x34,rng.getrandbits(32))
                    mem.store(q+j*0x80+0x50,rng.choice([0,64,0xffffffff,0x80000000,0x7fffffff]))
                    mem.store(q+j*0x80+0x54,rng.getrandbits(32))
            if kind==1 and case%2:
                mem.store(q,1);mem.store(q+0x30,0x80 if case%3 else 0)
        id=rng.choice([4,0,0x7c,0x96,0x22f,999999,0x1000004])
        if case%17==0:mem.store(block+12,0xff,1)
        ntex=types.count(9);index=rng.randrange(ntex) if ntex else 0
        try:check('fixup',(block,index,id,FRAME),mem)
        except Exception:
            print('case',case,'types',types,'id',id,'count',mem.load(block+4));raise
    # Standalone linked resource IDs and signed/empty counts.
    for id in (0x3d,0x7d,0x92):
        mem=Memory(data,segments);flk(mem,links=3);check('fixup',(0xb00200,0,id,FRAME),mem)
    for count in (0,0x80000000,0xffffffff):
        mem=Memory(data,segments);flk(mem);mem.store(0xb00004,count)
        check('fixup',(0xb00000,0,4,FRAME),mem)
    # Malformed index aliases the zero-filled model table in the original frame.
    mem=Memory(data,segments);flk(mem);mem.store(8,0x180)
    check('fixup',(0xb00000,16,4,FRAME),mem)
    for types in ((9,)*16,(9,)*17,(1,)*64,(1,)*65):
        mem=Memory(data,segments);flk(mem,types=types)
        check('fixup',(0xb00000,0,4,FRAME),mem)
    for name in FUNCTIONS:
        if name=='fixup':continue
        for case in range(128):
            mem=Memory(data,segments);block=flk(mem,types=(9,1,8))
            mem.put(FRAME-64,bytes(rng.randrange(256) for _ in range(64)))
            # Arbitrary signed permutation table exercises lb sign extension.
            mem.put(0x3e0260,bytes(rng.randrange(256) for _ in range(32)))
            if name=='map_address':
                v=[0,1,7,8,31,32,127,128,0x7fffffff,0x80000000,0xffffffff][case%11]
                if case>32:v=rng.getrandbits(32)
                args=(v,FRAME)
            elif name=='base_delta':args=(block,rng.getrandbits(32))
            elif name in ('flk5_count','flk5_size'):args=(block,)
            else:args=(block,[0,1,2,0x1000000,0xff000001][case%5])
            if case%3==0:mem.store(block,0x12345678)
            check(name,args,mem)
    bodies={int(i['address'],16) for f in packet['functions'] for i in f['instructions']}
    missed=sorted(bodies-coverage)
    malformed=[]
    for label,index,tag,expected_error in (('unaligned selected index',0xffffffff,0x60000000,'unaligned load'),
            ('unterminated linked records',0,0,'instruction limit')):
        mem=Memory(data,segments);flk(mem,links=1);mem.store(0xb00200,tag)
        d=FixupDriver(mem,lib);m=machine(d);m.hooks.pop(0x137ed0,None)
        m.reg[4]=0xb00000;m.reg[5]=index;m.reg[6]=4;m.reg[29]=FRAME+0x240
        try:m.run(0x137ed0,limit=3000)
        except ValueError as exc:
            if expected_error not in str(exc):raise
            malformed.append({'case':label,'result':'passed','observed_host_boundary':str(exc)})
        else:raise AssertionError('Malformed fixture did not reach expected bus/budget boundary')
    report={'schema':'hg-fixup-check/v1','result':'passed','cases':counts,'total_cases':sum(counts.values()),
        'byte_check':byte_check,'malformed_model_cases':malformed,'instruction_coverage':{'executed':len(bodies&coverage),'total':len(bodies),
            'not_executed':[f'0x{x:08x}' for x in missed]},
        'binding':receipt(ROOT,{'elf':a.elf,'shared_verifier':a.ps2dinfo/'tools/verify_elf_evidence.py',
            **{f'export_{entry:08x}':select_export(a.exports,entry) for entry,_,_ in FUNCTIONS.values()},
            **({'tested_c_source':a.source} if a.source else {})},
            'differential',['verify_resource_fixup.py'],[
                'External 001386e0 geometry/material and 00152ae0 upload are argument-traced mocks.',
                'Ordinary RAM compared; scratch stack excluded; original callee-saved registers checked.',
                'Finite fixtures; malformed nontermination/unmapped accesses are tested separately.',
                'Original-byte and model checks alone do not establish game runtime equivalence.'])}
    with a.output.open('x') as f:json.dump(report,f,indent=2);f.write('\n')
    print('PASS',report['total_cases'],'comparisons;',len(bodies&coverage),'/',len(bodies),'instruction addresses; missed',report['instruction_coverage']['not_executed'])
if __name__=='__main__':main()
