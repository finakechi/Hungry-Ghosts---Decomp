#!/usr/bin/env python3
"""Bounded reached-dependency survey at neutral, held, released and zero M10 frames."""
import argparse
from collections import Counter
import json
from pathlib import Path
import verify_player_walk_runtime as W
from hg_evidence_safety import digest
from verify_resource_loader import EXPECTED, shared_module
from resource_harness import library


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True);p.add_argument('--ps2dinfo',type=Path,required=True)
    p.add_argument('--capture-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if digest(a.elf)!=EXPECTED:raise ValueError('Wrong ELF')
    caps=W.Captures(a.capture_dir);caps.elfdata=a.elf.read_bytes()
    caps.segments,_=shared_module(a.ps2dinfo).segments_from_elf(caps.elfdata)
    caps.observation=json.loads((W.ROOT/'evidence/m10/player-walk-observation.json').read_text())
    lib=library(output=a.output.with_suffix('.so'));saved=W.V.Machine;indirect=set();rows=[]
    class Traced(saved):
        def load(self,address,size):
            word=super().load(address,size)
            if address==self.pc and size==4 and word>>26==0 and word&63 in (8,9):
                rs=(word>>21)&31
                if word&63==9 or rs!=31:
                    indirect.add((address,'jalr' if word&63==9 else 'jr',self.reg[rs]&0xffffffff))
            return word
    W.V.Machine=Traced
    try:
        for frame in ('f00','f03','f09','f10'):
            d,_=W.execute(lib,caps,frame)
            comparison=W.compare(d.mem,W.observed(caps,frame,'camera-return'))
            if comparison['result']!='passed':raise AssertionError(comparison)
            rows.append(dict(frame=frame,result='passed',modeled_calls=d.modeled_calls,
                modeled_instruction_addresses=len(d.executed),
                bus_calls=d.calls,comparison=comparison))
            print(frame,'PASSED',flush=True)
            W.gc.collect()
    finally:W.V.Machine=saved
    # Changed bytes between the selected pair and the next coordinator iteration
    # are external writers, not silently incorporated into reconstructed output.
    external=[]
    for left,right in zip(W.FRAMES,W.FRAMES[1:]):
        before=W.observed(caps,left,'camera-return');after=W.observed(caps,right,'dispatch-entry')
        different=[]
        for page in range(0,0x2000000,4096):
            if before.raw[page:page+4096]!=after.raw[page:page+4096]:
                different.extend(page+i for i,(x,y) in enumerate(zip(before.raw[page:page+4096],after.raw[page:page+4096])) if x!=y)
        external.append(dict(from_frame=left,to_frame=right,changed_bytes=len(different),
            changed_pages=[dict(start=hex(page),changed_bytes=count) for page,count in sorted(Counter(address&~4095 for address in different).items())]))
    result=dict(schema='hg-m10-dependency-survey/v1',result='passed',elf_sha256=EXPECTED,representative_frames=rows,
        reached_indirect_transfers=[dict(address=hex(pc),instruction=op,target=hex(target)) for pc,op,target in sorted(indirect)],
        external_interframe_writers=external,
        limitations=['Four representative path stages, not a complete gameplay-frame or every callback survey.',
            'Model memory is restricted to captured EE RAM and scratchpad; all other hardware/unmapped accesses raise errors.',
            'External interframe changes are observed ranges, without invented writer attribution or simulated effects.',
            'Other coordinator calls are excluded, not reconstructed.'])
    with a.output.open('x') as out:json.dump(result,out,indent=2);out.write('\n')

if __name__=='__main__':main()
