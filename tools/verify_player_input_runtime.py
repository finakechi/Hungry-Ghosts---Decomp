#!/usr/bin/env python3
"""Compare the ordinary-player input stage with independent native/PINE pairs.

This checks 159c44..159ce0 through its outgoing transition call, not setup,
transition body or following update equivalence. Raw owner captures stay local.
"""
import argparse
import ctypes as C
import hashlib
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'tests')]
from resource_harness import Driver, Memory, ResourceEnv, library
from verify_resource_loader import EXPECTED
from hg_evidence_safety import digest

def load_capture(path):
    data=json.loads(path.read_text());before=data['metadata_before']
    if before!=data['metadata_after'] or before['status']!=1:raise ValueError('Require stable paused metadata')
    if before['serial']!='SCPS-15046' or before['uuid'].lower()!='91aa4b28':raise ValueError('Wrong runtime build')
    mem=Memory();ranges=[]
    for s in data['samples']:
        raw=bytes.fromhex(s['bytes']);address=int(s['address'],16)
        if len(raw)!=s['size_bytes'] or hashlib.sha256(raw).hexdigest()!=s['sha256']:raise ValueError('Corrupt capture')
        mem.put(address,raw);ranges.append((address,len(raw)))
    return mem,ranges

class StageDriver(Driver):
    def external(self,pc,a=0,b=0,c=0,d=0):
        if pc!=0x159380:raise ValueError(f'Unobserved outgoing call {pc:#x}')
        self.calls.append((hex(pc),hex(a)));return 0

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True);p.add_argument('--capture-dir',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--source',type=Path)
    a=p.parse_args()
    if digest(a.elf)!=EXPECTED:raise ValueError('Wrong ELF')
    lib=library(source=a.source,output=a.output.with_suffix('.so'))
    lib.hg_player_ordinary_input_stage.argtypes=[C.POINTER(ResourceEnv),C.c_uint32]
    lib.hg_player_ordinary_input_stage.restype=None
    results=[]
    for label,pre,post,calls,edge in [
        ('neutral-control','native-ordinary-branch-001.json','native-neutral-consumed-001.json',[],0),
        ('square-edge','native-square-branch-001.json','native-square-helper-entry-001.json',[('0x159380','0x3f92d0')],128)]:
        initial,ranges=load_capture(a.capture_dir/pre);expected,afterranges=load_capture(a.capture_dir/post)
        compared=[r for r in ranges if r in afterranges]
        if compared!=[(0x3f92d0,0x540),(0x3eaeb0,0xb0),(0x3f9e30,0x120)]:raise ValueError('Unexpected comparison ranges')
        if initial.load(0x3eaf04,2)!=edge:raise ValueError('Wrong input edge')
        if initial.load(0x3eaee8,1)!=0 or initial.load(0x3f9719,1)!=255:raise ValueError('Unexpected input precondition')
        d=StageDriver(initial);lib.hg_player_ordinary_input_stage(C.byref(d.env),0x3f92d0)
        if d.errors:raise AssertionError(d.errors)
        if d.calls!=calls:raise AssertionError((d.calls,calls))
        for address,size in compared:
            if d.mem.get(address,size)!=expected.get(address,size):raise AssertionError(f'{label}: memory mismatch at {address:#x}')
        results.append(dict(case=label,result='passed',edge_mask=edge,
            before_sha256=digest(a.capture_dir/pre),after_sha256=digest(a.capture_dir/post),
            compared_bytes=sum(n for _,n in compared),transition_calls=calls))
    result=dict(schema='hg-player-input-runtime/v1',result='passed',elf_sha256=EXPECTED,cases=results,
        sources={name:digest(ROOT/name) for name in ['src/resource_loader.c','src/resource_loader.h','tools/verify_player_input_runtime.py']},
        limitations=['Independent paused original input-stage comparison only.',
        '159380 outgoing call checked; its body is outside this comparison.',
        'Earlier setup, movement/animation dependencies and following update are not reconstructed by this check.',
        'Native screenshots establish PCs separately; PINE alone does not establish execution.'])
    a.output.open('x').write(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))

if __name__=='__main__':main()
