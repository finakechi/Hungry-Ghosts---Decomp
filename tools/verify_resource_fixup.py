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

def evidence(exports,geometry_exports=None):
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
    if geometry_exports:
        from test_resource_geometry import ENTRIES
        packet['provenance']['limitations'] = [
            'M6 geometry uses the selected finite-normal FP domain; exceptional flags and VU timing are outside scope.',
            'Upload 00152ae0 remains external; M4 isolated tests retain their geometry mock.']
        for name,entry in ENTRIES.items():
            selected=select_export(geometry_exports,entry);r=json.loads(selected.read_text())
            packet['functions'].append(dict(entry=r['entry'],name='hg_resource_'+name,complete=True,
                body_ranges=r['body_ranges'],instructions=r['instructions'],
                references=[{**v,'ghidra_type':v['type'],'type':'call' if 'CALL' in v['type'] else 'unknown'} for v in r['references_to']],
                interpretation='M6 selected geometry/material processing; finite-normal FP scope in docs/M6.md.',
                verification_status='original-byte-verified',source='src/resource_loader.c',
                original_listing_sha256=r['listing_sha256'],snapshot_sha256=digest(selected)))
    return packet


def execute_geometry(lib,name,args,mem,coverage):
    """Execute original geometry/dependencies, including original fixup callers.

    No geometry/sqrt/material hooks: their actual authenticated instructions run.
    """
    from test_resource_geometry import GeometryDriver, ENTRIES
    from ee_subset import sx, MASK64
    cd=GeometryDriver(clone(mem),lib);md=GeometryDriver(clone(mem),lib)
    if name in ('descriptor_address','sqrt'):
        result=getattr(lib,'hg_resource_'+name)(*args)
    else:result=getattr(lib,'hg_resource_'+name)(C.byref(cd.env),*args)
    m=machine(md);m.hooks.pop(0x137ed0,None)
    for n,v in enumerate(args[:3] if name=='fixup' else args,4):
        m.reg[n]=v if name=='descriptor_address' and n==4 else sx(v,32)&MASK64
    if name=='fixup':m.reg[29]=args[3]+0x240
    if name=='sqrt':m.fpr[12]=args[0]
    saved={n:(0x123456789abcdef0<<64)|(n*0x1234567) for n in list(range(16,24))+[30]}
    for n,v in saved.items():m.reg[n]=v
    saved.update({28:m.reg[28],29:m.reg[29]})
    m.run(0x137ed0 if name=='fixup' else ENTRIES[name],limit=1000000)
    for n,v in saved.items():
        if m.reg[n]!=v:raise AssertionError(f'{name}: original ABI register {n} not restored')
    if name!='geometry':
        answer=m.fpr[0] if name=='sqrt' else m.reg[2] & (MASK64 if name=='descriptor_address' else 0xffffffff)
        if result!=answer:raise AssertionError(f'{name}: return mismatch C {result:x} EE {answer:x}')
    if name=='sqrt' and (m.q!=m.fpr[0] or m.vf[4]&0xffffffff!=m.fpr[0]):
        raise AssertionError('Original sqrt register transfer differs')
    compare(cd,md,'M6 '+name+str(args));coverage.update(m.executed)


def geometry_cases(lib,data,segments,coverage,image=None):
    from test_resource_geometry import geometry, configure
    configure(lib);rng=random.Random(0x1504606);counts={}
    def check(name,args,mem):
        execute_geometry(lib,name,args,mem,coverage);counts[name]=counts.get(name,0)+1
    for kind in (0,1,2,3,4,5,255):
        for case in range(24):
            mem=Memory(data,segments);block=0xb00000
            geometry(mem,block,kind,packets=case%5,vertices=case%4,bones=case%6)
            for offset in (0x14,0x24,0x1c,0x2c):
                bits=(rng.randrange(100,181)<<23)|rng.getrandbits(23)|(rng.randrange(2)<<31)
                mem.store(block+offset,bits)
            if kind==2 and case%7==0:mem.store(block,rng.choice([0x80000000,0xffffffff]))
            check('geometry',(block,rng.getrandbits(32)),mem)
    for case in range(128):
        mem=Memory(data,segments);block=0xb00000
        for i in range(case%16):mem.store(block+i*4,rng.getrandbits(24))
        mem.store(block+(case%16)*4,0x60000000 if case%2 else 0x13000000)
        check('material_next',(block,),mem)
        delta=[0,1,0x3fff,0x4000,0x7fffffff,0x80000000,0xffffffff][case%7]
        check('descriptor_address',(rng.getrandbits(64),delta),mem)
        value=[0,0x3f800000,0x3f800001,0x40000000,0x41c80000][case%5] if case<20 else (rng.randrange(40,220)<<23)|rng.getrandbits(23)
        check('sqrt',(value,),mem)
    if image:
        from hg_identify import ISO,read_at
        from verify_fixup_runtime import IMAGE
        if digest(image)!=IMAGE:raise ValueError('Wrong original image')
        with image.open('rb') as fp:
            base=ISO(fp).files['/DATA/PACK.DAT']['lba']*2048
            directory=read_at(fp,base+0xc6*2048,2048)
            if int.from_bytes(directory[32:36],'little')!=13:raise ValueError('Selected fixup count changed')
            for i in range(13):
                import struct
                offset,size,ident,_=struct.unpack_from('<4I',directory,64+i*16)
                block=0x1270000+offset-int.from_bytes(directory[16:20],'little')
                mem=Memory(data,segments);mem.put(block,read_at(fp,base+offset,size))
                check('fixup',(block,0,ident,FRAME),mem)
    return counts

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
    p.add_argument('--geometry-exports',type=Path,help='M6 read-only export snapshot')
    p.add_argument('--image',type=Path,help='Authenticated original for 13 connected instruction comparisons')
    a=p.parse_args()
    if digest(a.elf)!=EXPECTED:raise ValueError('Wrong original executable')
    shared=shared_module(a.ps2dinfo);data=a.elf.read_bytes();segments,_=shared.segments_from_elf(data)
    if a.image and not a.geometry_exports:raise ValueError('--image requires --geometry-exports')
    packet=evidence(a.exports,a.geometry_exports);byte_check=shared.verify(data,packet,require_all_bytes=True)
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
    m6=geometry_cases(lib,data,segments,coverage,a.image) if a.geometry_exports else {}
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
        'm6_cases':m6,'m6_total_cases':sum(m6.values()),
        'byte_check':byte_check,'malformed_model_cases':malformed,'instruction_coverage':{'executed':len(bodies&coverage),'total':len(bodies),
            'not_executed':[f'0x{x:08x}' for x in missed]},
        'binding':receipt(ROOT,{'elf':a.elf,'shared_verifier':a.ps2dinfo/'tools/verify_elf_evidence.py',
            **{f'export_{entry:08x}':select_export(a.exports,entry) for entry,_,_ in FUNCTIONS.values()},
            **({'tested_c_source':a.source} if a.source else {}),
            **({'original_image':a.image} if a.image else {}),
            **({f'geometry_export_{int(f["entry"],16):08x}':select_export(a.geometry_exports,int(f['entry'],16))
                for f in packet['functions'][len(FUNCTIONS):]} if a.geometry_exports else {})},
            'differential',['verify_resource_fixup.py'],[
                'M4 cases retain argument-traced geometry/upload mocks; M6 cases execute original geometry and all its reachable helpers without hooks.',
                'FP model covers the selected finite-normal chop domain, functional VU transfers; no exceptional flags or timing equivalence.',
                'Ordinary RAM compared; scratch stack excluded; original callee-saved registers checked.',
                'Finite fixtures; malformed nontermination/unmapped accesses are tested separately.',
                'Original-byte and model checks alone do not establish game runtime equivalence.'])}
    if a.geometry_exports:
        new_bodies={int(i['address'],16) for f in packet['functions'][len(FUNCTIONS):] for i in f['instructions']}
        report['m6_instruction_coverage']=dict(executed=len(new_bodies&coverage),total=len(new_bodies),
            not_executed=[hex(x) for x in sorted(new_bodies-coverage)])
    with a.output.open('x') as f:json.dump(report,f,indent=2);f.write('\n')
    print('PASS',report['total_cases'],'comparisons;',len(bodies&coverage),'/',len(bodies),'instruction addresses; missed',report['instruction_coverage']['not_executed'])
    if m6:print('PASS',sum(m6.values()),'M6 comparisons;',report['m6_instruction_coverage'])
if __name__=='__main__':main()
