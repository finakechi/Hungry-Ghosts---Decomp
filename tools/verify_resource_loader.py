#!/usr/bin/env python3
"""Original-instruction differential checks for M2, with explicit external mocks."""
import argparse
import hashlib
import importlib.util
import json
import random
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from resource_harness import Driver,Memory,ENTRIES,RETURNS,library,machine,setup_pack,synthetic_pack
EXPECTED='a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61'


def shared_module(path):
    spec=importlib.util.spec_from_file_location('ps2_shared_elf',path/'tools/verify_elf_evidence.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def clone(mem):
    result=Memory(mem.elf,mem.segments);result.pages={k:bytearray(v) for k,v in mem.pages.items()};return result


def compare(cd,md,label):
    if cd.calls!=md.calls:raise AssertionError(f'{label}: external call trace differs\nC={cd.calls}\nEE={md.calls}')
    for page in cd.mem.pages.keys()|md.mem.pages.keys():
        base=page<<12
        if 0x1ff0000<=base<0x1ff1000:continue # original stack scratch is outside compared game state
        a,b=cd.mem.get(base,4096),md.mem.get(base,4096)
        if a!=b:
            at=next(i for i in range(4096) if a[i]!=b[i])+base
            raise AssertionError(f'{label}: memory differs at {at:08x}: C {cd.mem.get(at,16).hex()}, EE {md.mem.get(at,16).hex()}')
    if cd.errors or md.errors:raise AssertionError((cd.errors,md.errors))


def pair(mem,cfg):
    cd,md=Driver(clone(mem)),Driver(clone(mem))
    for key,value in cfg.items():setattr(cd,key,value);setattr(md,key,value)
    return cd,md


def execute(lib,cd,md,key,args=()):
    c_result=cd.invoke(lib,key,*args);m=machine(md)
    for n,v in enumerate(args,4):m.reg[n]=v&0xffffffff
    m_result=m.run(ENTRIES[key],limit=200000)
    if key in RETURNS and c_result!=(m_result&0xffffffff):raise AssertionError(f'{key}: return {c_result} != {m_result}')
    compare(cd,md,key)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True);p.add_argument('--ps2dinfo',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source',type=Path,help='Disposable C source for a negative control')
    a=p.parse_args();data=a.elf.read_bytes()
    if hashlib.sha256(data).hexdigest()!=EXPECTED:raise ValueError('Wrong original ELF')
    segments,endian=shared_module(a.ps2dinfo).segments_from_elf(data)
    if endian!='little':raise ValueError('Unexpected original endian')
    lib=library(source=a.source,output=ROOT/'local/m2/differential.so');rng=random.Random(0x1504602)
    per_function={};total=0
    for key in ENTRIES:
        # Full worker/bootstrap paths have realistic valid file data. Single-step
        # helpers and request routines additionally exercise all invalid phases,
        # busy gates and error/width boundaries without hanging by design.
        count=35 if key in ('bootstrap','worker') else 100
        for case in range(count):
            mem=Memory(data,segments);base=0x3f8e00
            mem.put(base,bytes(rng.getrandbits(8) for _ in range(80)))
            mem.store(0x3eacec,rng.randrange(256),1);mem.store(0x3eace0,rng.randrange(256),1)
            mem.store(0x3fa1c4,rng.choice([0,1,0xffff]),2)
            for address in (0x3fa1b0,0x3fa1b1,0x3fa1b2):mem.store(address,rng.randrange(3),1)
            cfg=dict(sync_busy=rng.randrange(3),read_failures=rng.randrange(3),thread_id=rng.choice([17,-1,0]),audio_busy=rng.randrange(3))
            args=()
            if key in ('request','request_raw','schedule'):
                mem.store(base+0x30,rng.choice([0,0,1,2,0xffff]),2)
                args=(rng.choice([0,1,2,100,0x10064,0xffff]),rng.choice([0,9,0x4e,0x67,0x12345678]))
                if key=='schedule':args+= (0x130220,)
            elif key=='finish':mem.store(base+0x30,rng.choice([0,1,2,0xffff]),2)
            elif key in ('bootstrap','worker'):
                mem.put(base,bytes(80));mem.store(base+0x30,1,2)
                mem.store(base+0x32,rng.choice([0,0,0,1,2,3,4,5,6,7,8,9,0xffff]) if key=='worker' else 0,2)
                mem.store(base+0x44,case%3)
                if key=='bootstrap':args=(base,)
            elif key=='special':
                phase=rng.randrange(15);mem.store(base+0x3b,phase,1)
                mem.store(base+0x3c,rng.choice([0,1,2,0xffff]),2)
                mem.store(base+0x48,0xb00000);mem.store(base+0x4c,0x3f3b40)
                mem.put(0x3f3b00,bytes(2048));mem.store(0x3f3b18,rng.randrange(2),2)
                mem.store(0x3f3b1a,rng.randrange(2),2);mem.store(0x3f3b1c,rng.randrange(2),2)
                mem.store(0x3f3b40,2048);mem.store(0x3f3b44,2048);mem.store(0x3f3b48,rng.randrange(20))
                mem.store(0x3eacf0,rng.choice([0,1,2,255]));mem.store(0x3eacf8,0x3ead20)
                mem.store(0x3eacfc,0xb00000);mem.store(0x3ead04,2048)
                mem.store(0x3ead0c,1);mem.store(0x3ead10,1)
                cfg['error_result']=rng.choice([0,0,0x31,5]);args=(base,0x3f3b00,0x3f3b00)
            elif key in ('area_index','area_step'):
                mem.store(0x3fa1b0,rng.randrange(3),1);mem.store(0x3fa1b1,rng.randrange(3),1)
                for i in range(3):mem.store(0x3e01b0+4*i,i*3)
                mem.store(0x3dff04,0xa87800);mem.store(0xa87814,rng.choice([0,16,32]))
                for i in range(12):mem.store(0x3f3a30+8*i,2000);mem.store(0x3f3a34+8*i,2048)
                mem.store(base+0x3a,rng.choice([0,1,2,3,255]),1)
                mem.store(0x3eacf0,0);mem.store(0x3ead04,0)
                if key=='area_step':args=(base,)
            elif key=='area_finalize':
                mem.store(0xa87814,rng.choice([0,1,127,2048]));args=(0xa87800,rng.choice([2048,4096]))
            cd,md=pair(mem,cfg)
            if key in ('bootstrap','worker'):
                setup_pack(cd,id=case%3);setup_pack(md,id=case%3)
                if key=='bootstrap' and case%4==3:
                    for driver in (cd,md):
                        driver.mem.store(base+0x39,5,1)
                        driver.mem.store(base+0x48,0xfffffff0 if case%8==7 else 0xb00000)
                        driver.mem.store(0x3f3b14,[1,2047,2048,2049,32,0][(case//4)%6])
            else:
                cd.files[1000]=md.files[1000]=bytes(synthetic_pack())
                cd.mem.store(0x3ead20,1000);md.mem.store(0x3ead20,1000)
            execute(lib,cd,md,key,args);total+=1
        per_function[key]=count
    sequences=0
    # Exercise the original request -> scheduling -> worker -> PACK -> M1 path
    # with data-bearing synthetic files and retries, preserving state across calls.
    for seed in range(12):
        mem=Memory(data,segments);cd,md=pair(mem,dict(sync_busy=seed%3,read_failures=seed%3))
        setup_pack(cd,id=seed%3);setup_pack(md,id=seed%3)
        if seed%2:
            cd.mem.store(0x3f2c80,1,1);md.mem.store(0x3f2c80,1,1)
        execute(lib,cd,md,'request_raw',(0,seed%3));execute(lib,cd,md,'worker');sequences+=1
    result=dict(schema='hg-resource-differential/v1',elf_sha256=EXPECTED,seed='0x1504602',
                randomized_invocations=total,cases_per_function=per_function,end_to_end_sequences=sequences,
                result='passed',source='src/resource_loader.c + src/disc_read.c',
                comparison='Return values where defined, non-stack RAM/scratchpad, ordered defined external arguments and thread descriptors; includes nonzero directory IDs and arena rounding/wrap boundaries.',
                limitations=['Kernel threads, CDVD responses and asset fixups are deterministic mocks.',
                             'No concurrent interrupts or overlapping destination/context aliases modeled.',
                             'Bootstrap PACK path complete within these dependencies; other worker loader branches remain callbacks.'])
    with a.output.open('x') as fp:json.dump(result,fp,indent=2);fp.write('\n')
    print(json.dumps(result))


if __name__=='__main__':main()
