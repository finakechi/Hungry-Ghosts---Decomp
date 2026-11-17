#!/usr/bin/env python3
"""Differential check of M9 turn/camera C against original EE instructions.

Every dependency is a synthetic leaf whose returns and memory effects come from
per-case random slots, identically for both sides. 12c790 and 12bd00 are
compared on their observed branches; their other branch must fail closed in C.
"""
import argparse
import ctypes as C
import json
import random
import struct
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'tests')]
from resource_harness import Memory, machine, library
from test_player_turn_camera import FUNCTIONS, ARITIES, FP_SEAMS, PLAYER, CAMERA, TurnDriver, configure
from verify_resource_loader import shared_module, EXPECTED, clone, compare
from hg_evidence_safety import digest
from ee_subset import sx, MASK64

SLOTS=0x600100
LEAVES=sorted(ARITIES)
PARTIAL={0x12c790:'3eaed4 != 0',0x12bd00:'player +47a != 0'}
# Statically unreachable switch defaults: the level register a1 is assigned only
# 0..3 by the threshold ladder (no reload) before `beq a1,3/2/1/zero`.
UNREACHABLE={0x160510:{0x16063c,0x160640},0x1608b0:{0x160aac,0x160ab0}}
FP_FIELDS=[0x30,0x34,0x38,0x44,0x48,0x380,0x384,0x388,0x38c,0x390,0x398,0x3c0,0x434,0x438,0x43c,0x440,0x4dc]


def f(x):return struct.unpack('<I',struct.pack('<f',x))[0]


class SlotDriver(TurnDriver):
    """Leaf results and effects are read from case slots, never computed from C."""
    def external(self,pc,a=0,b=0,c=0,d=0):
        if pc not in ARITIES and pc!=0xfffffff0:raise AssertionError(f'unexpected call {pc:#x}')
        result=super().external(pc,a,b,c,d)
        slot=lambda i:self.mem.load(SLOTS+0x100+i*4)
        if pc==0x15fe70:self.mem.store(a+0x4ad,slot(0)&3,1)
        elif pc in (0x160c60,0x1608b0):
            self.mem.store(PLAYER+0x434,slot(1));self.mem.store(PLAYER+0x438,slot(2))
            self.mem.store(PLAYER+0x45c,slot(0)>>2&3,1)
        elif pc==0x160510:self.mem.store(PLAYER+0x44,slot(3));self.mem.store(PLAYER+0x504,slot(0)>>4&3,1)
        elif pc==0x143560:self.mem.store(PLAYER+0x44,slot(4))
        elif pc==0x12bd00:self.mem.put(CAMERA+0xe0,self.mem.get(SLOTS+0x180,32))
        elif pc==0x15fd30:self.mem.store(PLAYER+0x3c0,slot(5))
        elif pc in (0x143c60,0x143d30,0x143b90):  # rotations: arbitrary finite matrix
            self.mem.put(b,self.mem.get(SLOTS+0x200+(pc&0xf0),64))
        return self.mem.load(SLOTS+LEAVES.index(pc)*4)


def fp(rng,lo=100,hi=133):
    return (rng.getrandbits(1)<<31)|(rng.randrange(lo,hi)<<23)|rng.getrandbits(23)


def setup(rng,data,segments,entry,case):
    mem=Memory(data,segments);r=rng.randrange
    mem.put(PLAYER,bytes(r(256) for _ in range(0x540)));mem.put(CAMERA,bytes(r(256) for _ in range(0x150)))
    stick=lambda:rng.choice([0,0,f(0.3),f(-0.39),f(0.4),f(-0.4),f(0.5),f(-0.6),f(0.7),f(0.79),f(-0.8),f(1.0),f(-1.0),fp(rng,110,128)])
    for off in FP_FIELDS:mem.store(PLAYER+off,fp(rng))
    for off in list(range(0,0x20,4))+[0xd0,0xd4]+list(range(0xe0,0x100,4)):mem.store(CAMERA+off,fp(rng))
    for off,choices in [(0x4ad,[0,0,1,2]),(0x2c3,[0,1,2]),(0x4d4,[0,0,1,2,5]),(0x49f,[0,0,1,2,3]),(0x449,[0,0,3,255]),
            (0x461,[0,0,1]),(0x504,[0,1,3]),(0x505,[0,2]),(0x45c,[0,1,3]),(0x45d,[0,2]),(0x506,[0,0x14,0x15,0x7f,0x80]),
            (0x47a,[0]*9+[1]),(0x465,[0,1])]:mem.store(PLAYER+off,rng.choice(choices),1)
    mem.store(PLAYER+0x416,rng.choice([0,0,1,0x14,0x15,0x77,0x78,0x8c,0xffff]),2)
    mem.store(CAMERA+0x140,rng.choice([0,1]),1)
    for address,choices in [(0x3eaeb0,[0]*7+[1]),(0x3f9f3c,[0,1]),(0x3eaee8,[0]*3+[1]),(0x3eaed4,[0]*9+[1])]:
        mem.store(address,rng.choice(choices),1)
    mem.store(0x3eaf00,rng.choice([0,4,8,0xc,r(65536)]),2);mem.store(0x3eaf04,rng.choice([0,8,r(65536)]),2)
    mem.store(0x3eaecc,rng.choice([0,0x10,r(65536)]),2);mem.store(0x3f9ff2,rng.choice([0x68,0x8e,0,0x69]),2)
    mem.store(0x3eaf0c,stick());mem.store(0x3eaf10,stick())
    if case%4==0:mem.store(PLAYER+0x434,0)
    if case%5==0:mem.store(PLAYER+0x438,0)
    if case%7==0:mem.store(PLAYER+0x434,f(0.066))
    for i,pc in enumerate(LEAVES):
        value=fp(rng) if pc in FP_SEAMS and FP_SEAMS[pc][1] else rng.choice([0,0,1,2,3,0x70000500,r(1<<32)])
        if pc==0x15fcd0:value=rng.choice([1,2,3])
        if pc==0x120830:value=rng.choice([f(0.001),f(0.00101),f(0.0009),fp(rng)])
        mem.store(SLOTS+i*4,value)
    for i in range(6):mem.store(SLOTS+0x100+i*4,fp(rng) if i else r(64))
    for i in range(8):mem.store(SLOTS+0x180+i*4,fp(rng))
    for i in range(0x40):mem.store(SLOTS+0x200+i*4,fp(rng,118,132))
    args=[]
    if entry in (0x15fe00,0x15fe70):args=[PLAYER,rng.choice([0,1,4,5,8,9,0xd,r(16)])]
    elif entry in (0x1608b0,0x160c20):args=[PLAYER]
    elif entry in (0x160510,0x15fcd0):args=[PLAYER,rng.choice([0,1,4,8,0xc])]
    elif entry==0x160420:
        step,limit,brake=fp(rng,115,128),fp(rng,118,128),fp(rng,126,130)
        field=0x601000;choices=[limit,limit^0x80000000,0,0x80000000,fp(rng,115,128),limit+1,limit-1]
        mem.store(field,rng.choice(choices));args=[step,limit,brake,field]
    elif entry==0x1204e0:
        args=[rng.choice([f(3.14159274),f(-3.14159274),f(9.42477798),f(-9.42477798),f(3.1415925),fp(rng,100,133)])]
    elif entry in (0x12d130,):args=[PLAYER,CAMERA]
    elif entry==0x12bd00:args=[PLAYER,CAMERA,rng.choice([0,1])]
    return mem,args


class Mismatch(AssertionError):
    """A measured C/original divergence: call trace, memory, return or saved registers.

    Only this counts as a negative-control rejection. Driver errors, domain
    faults on compared paths, coverage gaps and any other exception do not."""


def execute(lib,entry,args,mem):
    name,n,returns=FUNCTIONS[entry]
    cd=SlotDriver(clone(mem));md=SlotDriver(clone(mem))
    result=getattr(lib,name)(C.byref(cd.env),*args)
    if cd.errors:
        if entry in PARTIAL and cd.calls and cd.calls[-1][0]=='fault':return None
        raise AssertionError(f'{name}{args}: C failed {cd.errors}')
    m=machine(md)
    for pc in ARITIES:
        if pc==entry:continue
        def hook(cpu,pc=pc):
            nfp,fresult=FP_SEAMS.get(pc,(0,False))
            ints=[cpu.reg[i]&0xffffffff for i in (4,5,6,7)]
            value=md.external(pc,*[cpu.fpr[12+i] for i in range(nfp)],*ints[:ARITIES[pc]-nfp])
            if fresult:cpu.fpr[0]=value;return 0
            return sx(value,32)&MASK64
        m.hooks[pc]=hook
    nfp,fresult=FP_SEAMS.get(entry,(0,False)) if entry in (0x160420,0x1204e0) else (0,False)
    for i in range(nfp):m.fpr[12+i]=args[i]
    for i,value in enumerate(args[nfp:],4):m.reg[i]=value
    saved={i:((i*0x12345678)<<64)|i for i in list(range(16,24))+[30]}
    saved.update({28:m.reg[28],29:m.reg[29]})
    for i,value in saved.items():m.reg[i]=value
    model=m.run(entry,limit=200000)
    if any(m.reg[i]!=value for i,value in saved.items()):raise Mismatch(f'{name}: callee-saved registers')
    if cd.errors or md.errors:raise AssertionError(f'{name}{args}: driver errors {cd.errors} {md.errors}')
    if returns:
        expected=m.fpr[0] if fresult else model&0xffffffff
        if result!=expected:raise Mismatch(f'{name}{args}: return C {result:#x} EE {expected:#x}')
    try:compare(cd,md,f'{name}/{[hex(x) for x in args]}')
    except AssertionError as exc:raise Mismatch(str(exc)) from None  # errors were excluded above
    return m.executed


def run(lib,data,segments,packet,cases,coverage_required=True):
    rng=random.Random(0x160510012c790);coverage={};rows=[]
    for entry,(name,_,_) in FUNCTIONS.items():
        compared=faulted=0;executed=set()
        for case in range(cases):
            mem,args=setup(rng,data,segments,entry,case)
            got=execute(lib,entry,args,mem)
            if got is None:faulted+=1
            else:compared+=1;executed|=got
        coverage[entry]=executed
        rows.append(dict(entry=f'0x{entry:08x}',name=name,compared=compared,failed_closed=faulted,
            complete_function=entry not in PARTIAL))
    missing={};excluded={}
    for fn in packet['functions']:
        entry=int(fn['entry'],16);addresses={int(i['address'],16) for i in fn['instructions']}
        unreachable=UNREACHABLE.get(entry,set())
        if not unreachable<=addresses or unreachable&coverage[entry]:
            raise AssertionError(f'{fn["entry"]}: static exclusion is not an unexecuted body address')
        left=addresses-coverage[entry]-unreachable
        if entry in PARTIAL:excluded[fn['entry']]=dict(branch=PARTIAL[entry],addresses=len(left))
        elif left:missing[fn['entry']]=[hex(x) for x in sorted(left)]
    if missing and coverage_required:raise AssertionError(f'Unexecuted complete-function addresses: {missing}')
    for row in rows:
        if row['entry'] in excluded:
            if row['failed_closed']==0:raise AssertionError(f"{row['name']}: unobserved branch never exercised")
            row['unexecuted_unobserved_branch_addresses']=excluded[row['entry']]['addresses']
    total=sum(len(fn['instructions']) for fn in packet['functions'])
    return dict(result='passed',functions=rows,cases=sum(r['compared'] for r in rows),
        failed_closed=sum(r['failed_closed'] for r in rows),instruction_addresses=total,
        executed_addresses=sum(len(v) for v in coverage.values()),partial_exclusions=excluded,
        static_exclusions={f'0x{e:08x}':dict(reason='switch default; level register a1 is 0..3 by construction',
            addresses=[hex(x) for x in sorted(v)]) for e,v in UNREACHABLE.items()})


DEFECTS=(
    ('missing-sign-negation','if(flt(e,r32(e,0x3eaf0c),0)) {step=fneg(step);limit=fneg(limit);}',''),
    ('decay-without-zero-clamp','if(flt(e,value,0))w32(e,field,0);',''),
    ('wrong-dead-zone','if(flt(e,magnitude,0x3ecccccdu))return 0;','if(flt(e,magnitude,0x3e800000u))return 0;'),
    ('swapped-brake-branch','if(!fle(e,current,0)) {w32(e,field,fadd(e,current,fmul(e,step,brake)));return 0;}',
        'if(!fle(e,current,0)) {w32(e,field,fsub(e,current,fmul(e,step,brake)));return 0;}'),
    ('camera-target-depth','w32(e,0x70000708,0x41a00000u)','w32(e,0x70000708,0x41a80000u)'),
    ('ee-add-without-guard','case 0:case 1:return fp_add_bits(a,b,op==1,1,fault);',
        'case 0:case 1:return fp_add_bits(a,b,op==1,0,fault);'),
    ('vu-add-with-ee-guard','acc[lane]=fp_op(e,6,acc[lane],fmul(e,m[12+lane],v[3]));',
        'acc[lane]=fp_op(e,0,acc[lane],fmul(e,m[12+lane],v[3]));'))


def negative_controls(data,segments,packet,folder,cases):
    source=(ROOT/'src/resource_loader.c').read_text();folder.mkdir(parents=True);rows=[]
    for name,old,new in DEFECTS:
        if source.count(old)!=1:raise AssertionError(f'{name}: mutation target must be unique')
        path=folder/(name+'.c');path.open('x').write(source.replace(old,new))
        lib=configure(library(source=path,output=folder/(name+'.so')))
        try:run(lib,data,segments,packet,cases,coverage_required=False);outcome='ESCAPED';reason=''
        except Mismatch as exc:outcome='rejected';reason=str(exc).split('\n')[0][:200]
        except Exception as exc:  # never a rejection: the control did not measure a divergence
            outcome='error';reason=f'{type(exc).__name__}: {str(exc)[:200]}'
        rows.append(dict(control=name,compiled=True,mutated_source_sha256=digest(path),result=outcome,reason=reason))
    return rows


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True);p.add_argument('--ps2dinfo',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--source',type=Path)
    p.add_argument('--cases',type=int,default=768)
    p.add_argument('--negative-controls',action='store_true',help='Compile defects that the differential must reject')
    a=p.parse_args()
    if digest(a.elf)!=EXPECTED:raise ValueError('Wrong original ELF')
    packet=json.loads((ROOT/'analysis/player_turn_camera/functions.json').read_text())
    shared=shared_module(a.ps2dinfo);data=a.elf.read_bytes()
    bytecheck=shared.verify(data,packet,require_all_bytes=True);segments,_=shared.segments_from_elf(data)
    lib=configure(library(source=a.source,output=a.output.with_suffix('.so')))
    result=run(lib,data,segments,packet,a.cases)
    if a.negative_controls:
        if a.source:p.error('--source cannot accompany --negative-controls')
        result['negative_controls']=negative_controls(data,segments,packet,a.output.parent/(a.output.stem+'-controls'),128)
        if any(c['result']!='rejected' for c in result['negative_controls']):result['result']='failed'
    result.update(original_bytes=bytecheck,sources={name:digest(ROOT/name) for name in
        ['src/resource_loader.c','src/resource_loader.h','tests/test_player_turn_camera.py',
         'tools/verify_player_turn_camera.py','tools/ee_subset.py']},
        limitations=['Synthetic leaf results and effects; no retail equivalence.',
            'Raw FP operands precede integer arguments on the host bus; FP results return in f0.',
            '12c790 and 12bd00: observed branch only; the other branch must call the host domain fault.',
            'Bounded finite FP oracle (chop, DAZ/FTZ, EE guard bit); no FP flags or timing.',
            'Callee-saved 128-bit registers checked; guest stack scratch excluded.'])
    a.output.open('x').write(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
    if result['result']!='passed':raise SystemExit(1)

if __name__=='__main__':main()
