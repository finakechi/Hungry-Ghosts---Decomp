#!/usr/bin/env python3
"""Local differential check of original instructions versus reconstructed C.

CDVD operations are deterministic mocks, not real IOP/hardware validation.
Retail bytes are read from --elf and are never embedded into synthetic tests.
"""
import argparse
import ctypes as C
import hashlib
import json
import random
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from disc_harness import State, Driver, library
from ee_subset import Machine
from hg_identify import elf32, va_to_offset

STATE = 0x3eacf0
EXPECTED = 'a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61'
FUNCTIONS = {'poll':0x1318f0,'setup':0x1319c0,'continue':0x1319e0,'start':0x131bf0}
SEED = 0x15046
CASES_PER_FUNCTION = 350
CHUNKS = (0, 1)
LENGTHS = (0, 2048, 2049, 128*2048, 129*2048, 600000)
MAX_TICKS = 25

def original(data, elf, state, drv, flags):
    m = Machine()
    for lo,hi in ((0x1318f0,0x1319b4),(0x1319c0,0x1319dc),(0x1319e0,0x131be4),(0x131bf0,0x131da0)):
        at = va_to_offset(elf,lo,hi-lo)
        m.put(lo,data[at:at+hi-lo])
    m.put(STATE,bytes(state))
    m.put(0x600000,bytes(0x200))
    m.reg[29] = 0x600100
    m.reg[28] = 0x3e7f70
    m.reg[4] = STATE
    for a,v in zip((0x3eace4,0x3eace8,0x3f2c80,0x3f2fa0),flags):m.store(a,v,1)
    # read32 isn't a guest library call: original code dereferences file records.
    m.store(0x500000,drv.lba,4);m.store(0x500004,drv.size,4)
    m.hooks = {0x10f520:lambda mm:drv.sync(mm.reg[4]&0xffffffff),
               0x10fd28:lambda mm:drv.ready(mm.reg[4]&0xffffffff),
               0x110268:lambda mm:drv.error(),
               0x10ff30:lambda mm:drv.read(*(mm.reg[i]&0xffffffff for i in (4,5,6)),mm.get(mm.reg[7]&0xffffffff,3))}
    return m

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--elf',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    data=args.elf.read_bytes()
    if hashlib.sha256(data).hexdigest()!=EXPECTED:raise ValueError('wrong original ELF')
    elf=elf32(data);lib=library();rng=random.Random(SEED)
    randomized = {}
    boundary_lengths=[0,1,2047,2048,2049,0x40000,0x40001,0xffffffff,0x80000000,0x7fffffff,0x7ffff801]
    for name,entry in FUNCTIONS.items():
        randomized[name] = 0
        for i in range(CASES_PER_FUNCTION):
            vals=[rng.getrandbits(32) for _ in range(11)]
            vals[0]=(rng.getrandbits(24)<<8)|rng.choice([0,0,1,1,2,255])
            vals[2]=0x500000;vals[3]=0x800000
            vals[4]=rng.choice([0,2047,2048,0xffffffff,0x80000000,rng.getrandbits(32)])
            vals[5]=rng.choice(boundary_lengths+[rng.getrandbits(32)])
            vals[7]=rng.choice([0,1,127,128,129,257,0xffffffff])
            vals[8]=rng.choice([0,1,127,128,129,257,0xffffffff])
            vals[10]=rng.choice([0,1,2])
            initial=State(*vals)
            cfg=dict(file_lba=rng.getrandbits(32),file_size=rng.getrandbits(32),
                     sync=rng.choice([0,0,0,1]),ready=rng.choice([2,2,0]),error=rng.choice([0,0,0x31,5]),failures=rng.randrange(3))
            flags=(rng.choice([0,1,2,255]),rng.randrange(2),rng.randrange(2),rng.randrange(2))
            cd,md=Driver(**cfg),Driver(**cfg)
            env=cd.environment(*flags)
            cs=State.from_buffer_copy(bytes(initial));m=original(data,elf,initial,md,flags)
            if name=='setup':
                values=[0x500000,rng.getrandbits(32),rng.getrandbits(32),rng.getrandbits(32)]
                lib.hg_read_setup(C.byref(cs),*values)
                for n,v in enumerate(values,4):m.reg[n]=v
                m.run(entry);expected=None;actual=None
            else:
                expected=getattr(lib,'hg_read_'+name)(C.byref(cs),C.byref(env))
                actual=m.run(entry)
            # C read32 callback is a host-address abstraction, not a CDVD call.
            c_calls=[x for x in cd.calls if x[0]!='read32']
            if expected!=actual or bytes(cs)!=m.get(STATE,44) or env.active!=m.load(0x3eace8,1) or c_calls!=md.calls:
                raise AssertionError(json.dumps(dict(function=name,case=i,expected=expected,actual=actual,
                    initial=vals,
                    c_bytes=bytes(cs).hex(),m_bytes=m.get(STATE,44).hex(),c_calls=str(c_calls),m_calls=str(md.calls))))
            randomized[name] += 1
    lifecycle = []
    for chunk in CHUNKS:
        for length in LENGTHS:
            cs=State();lib.hg_read_setup(C.byref(cs),0x500000,0x800000,0,length)
            cd,md=Driver(failures=2),Driver(failures=2)
            flags=(0,0,chunk,0);env=cd.environment(*flags)
            m=original(data,elf,cs,md,flags)
            for tick in range(MAX_TICKS):
                # Inject one completion error, then permit the whole-read retry.
                cd.error_result=md.error_result=0x31 if tick==1 else 0
                expected=lib.hg_read_poll(C.byref(cs),C.byref(env));actual=m.run(FUNCTIONS['poll'])
                if expected!=actual or bytes(cs)!=m.get(STATE,44) or env.active!=m.load(0x3eace8,1) or [x for x in cd.calls if x[0]!='read32']!=md.calls:
                    raise AssertionError(f'lifecycle mismatch chunk={chunk},length={length},tick={tick}')
                if actual==1:
                    lifecycle.append(dict(chunk=chunk, length=length, steps=tick+1))
                    break
            else:raise AssertionError('lifecycle did not complete')
    count = sum(randomized.values())
    sequence_steps = sum(row['steps'] for row in lifecycle)
    result=dict(schema='hg-disc-read-differential/v2',elf_sha256=EXPECTED,seed=hex(SEED),cases=count+sequence_steps,
                randomized_cases=count,randomized_cases_per_function=CASES_PER_FUNCTION,
                randomized_by_function=randomized,lifecycle=lifecycle,
                lifecycle_sequences=len(lifecycle),lifecycle_steps=sequence_steps,
                result='passed',function_entries=FUNCTIONS,
                limitations=['CDVD operations mocked','Restricted interpreter independently implemented for this group; not a hardware oracle',
                             'No interrupts or concurrent mutation','Does not establish matching or full-game equivalence'])
    with args.output.open('x') as fp:json.dump(result,fp,indent=2);fp.write('\n')
    print(json.dumps(result))

if __name__=='__main__':main()
