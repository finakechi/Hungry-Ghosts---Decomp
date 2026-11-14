#!/usr/bin/env python3
"""Reanalyse private paused EE debugger evidence against original disc and C.

PC/register values are screenshot transcriptions, authenticated by file hashes;
PINE supplies bytes, not a PC trace. Does not promote a saved capture to fresh.
"""
import argparse
import ctypes as C
import hashlib
import json
import struct
import sys
from pathlib import Path
from hg_identify import ISO, read_at
from hg_evidence_safety import digest, receipt
from verify_resource_loader import ROOT, EXPECTED, shared_module
sys.path.insert(0,str(ROOT/'tests'))
from resource_harness import Memory, library
from test_resource_fixup import FRAME, configure, FixupDriver

IMAGE='8ca1513c8417c0a40b838ff5edf22a2360f43bce1b0458693a95a6efdafd994e'
def require(condition,message):
    if not condition:raise ValueError(message)
def samples(path):
    row=json.loads(path.read_text());before=row['metadata_before'];after=row['metadata_after']
    require(before==after and before['serial']=='SCPS-15046' and before['uuid'].lower()=='91aa4b28'
        and before['status']==1,'Capture must be paused and have original identity')
    result={}
    for s in row['samples']:
        b=bytes.fromhex(s['bytes']);require(len(b)==s['size_bytes'] and hashlib.sha256(b).hexdigest()==s['sha256'],'Bad sample digest')
        result[int(s['address'],16)]=b
    return row,result

class RetailDriver(FixupDriver):
    def __init__(self,mem,lib,fp):super().__init__(mem,lib);self.fp=fp
    def read(self,lba,count,dst,mode):
        self.calls.append(('read',lba,count,dst,mode))
        if count>512:raise ValueError('Unexpected transfer size')
        self.mem.put(dst,read_at(self.fp,lba*2048,count*2048));return 1

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('elf','image','runtime','ps2dinfo','emulator','profile','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args()
    require(digest(a.elf)==EXPECTED and digest(a.image)==IMAGE,'Wrong original ELF or full image')
    observation=json.loads((a.runtime/'observation.json').read_text())
    require(observation['elf_sha256']==EXPECTED and observation['image_sha256']==IMAGE,'Observation build mismatch')
    require(digest(a.emulator)==observation['emulator_sha256'],'Emulator changed')
    require(digest(a.profile)==observation['profile_sha256'],'Captured profile changed')
    sequence=['0x137ed0','0x1385b0','0x1389d0','0x130834','0x1308d8','0x1434f0','0x103da0']
    require([v['pc'] for v in observation['observations']]==sequence,'Missing debugger PC observations')
    captured={}
    for event in observation['observations']:
        for key in ('screenshot','capture'):
            require(digest(a.runtime/event[key])==event[key+'_sha256'],'Debugger artifact changed')
        captured[event['capture']]=samples(a.runtime/event['capture'])
    pre_meta,pre=captured['pre-fixup.json'];post_meta,post=captured['post-fixup.json']
    _,published=captured['post-bootstrap.json'];_,completed=captured['exit-thread-stepped.json']
    block=0xb00000
    require(observation['observations'][0]['registers']=={'a0':'0x00b00000','a1':'0x00000000','a2':'0x00000004'},'Wrong selected inputs')
    data=a.elf.read_bytes();shared=shared_module(a.ps2dinfo);segments,_=shared.segments_from_elf(data)
    lib=configure(library(output=ROOT/'local/m4/retail.so'))
    with a.image.open('rb') as fp:
        rec=ISO(fp).files['/DATA/PACK.DAT'];directory=read_at(fp,rec['lba']*2048,2048)
        offset,size,id,_=struct.unpack_from('<4I',directory,64)
        original=read_at(fp,rec['lba']*2048+offset,size)
        require((offset,size,id)==(559104,4352,4),'Selected member changed')
        require(original==pre[block] and directory==pre[0x3f3b00],'Disc bytes differ from paused input')
        # Direct reconstructed operation, compared across the entire member.
        direct=FixupDriver(Memory(data,segments),lib);direct.mem.put(block,original)
        answer=lib.hg_resource_fixup(C.byref(direct.env),block,0,id,FRAME)
        require(not direct.errors and not direct.calls,'Unexpected direct dependency or bus error')
        require(answer==int(observation['observations'][3]['registers']['v0'],16),'Return differs from debugger')
        require(direct.mem.get(block,size)==post[block],'Direct C bytes differ from original runtime')
        # Combined request -> M2 worker/CDVD read -> M4/M3 -> published state.
        d=RetailDriver(Memory(data,segments),lib,fp)
        d.mem.store(0x3ead20,rec['lba']);d.mem.store(0x3ead24,rec['size_bytes'])
        d.invoke(lib,'request_raw',0,0);lib.hg_resource_worker_fixed(C.byref(d.env),FRAME)
        require(not d.errors,'Integrated bus errors: '+str(d.errors))
        require(d.mem.get(block,size)==post[block],'Integrated C member differs from runtime')
        for address,length in ((0x3f4300,20),(0x3f4d3c,36)):
            require(d.mem.get(address,length)==published[address][:length],f'Published state differs at {address:08x}')
        require(d.mem.load(0x3f8e30,2)==int.from_bytes(completed[0x3f8e00][0x30:0x32],'little')==0,'Thread active differs')
        require(d.mem.load(0x3eacec,1)==completed[0x3eace0][12]==0,'Request busy differs')
        reads=[list(c[1:4]) for c in d.calls if c[0]=='read']
        require(reads==[[rec['lba'],1,0x3f3b00],[rec['lba']+(559104//2048),170,block]],'Wrong original read path')
        require(not any(c[:2]==('call',0x137ed0) for c in d.calls),'Integrated path used fixup mock')
    changes=[]
    for at in range(0,size,4):
        before=int.from_bytes(original[at:at+4],'little');after=int.from_bytes(post[block][at:at+4],'little')
        if before!=after:changes.append({'offset':hex(at),'before':hex(before),'after':hex(after)})
    require(changes==observation['resource']['changed_words'],'Unexpected memory effects')
    result={'schema':'hg-m4-runtime-comparison/v1','result':'passed','evidence_level':'saved-debugger-observation-reanalysis',
        'member_size':size,'member_before_sha256':hashlib.sha256(original).hexdigest(),
        'member_after_sha256':hashlib.sha256(post[block]).hexdigest(),'changed_words':changes,
        'debugger_sequence':[{'pc':e['pc'],'registers':e['registers']} for e in observation['observations']],
        'return':hex(answer),'full_member_comparison':'passed','integrated_read_ranges':reads,
        'handles_ids_0_to_4':[hex(d.mem.load(0x3f4300+i*4)) for i in range(5)],
        'arena_end':hex(d.mem.load(0x3f4d3c)),'request_busy':0,'thread_active':0,
        'binding':receipt(ROOT,{'elf':a.elf,'image':a.image,'emulator':a.emulator,'profile':a.profile,
            'raw':a.runtime/'pre-fixup.json','post':a.runtime/'post-fixup.json','observation':a.runtime/'observation.json',
            **{e['capture']:a.runtime/e['capture'] for e in observation['observations']},
            **{e['screenshot']:a.runtime/e['screenshot'] for e in observation['observations']}},
            'saved-runtime',['verify_fixup_runtime.py'],[
                'Actual debugger PC/register observations manually transcribed; raw screenshots stay private.',
                'Breakpoint snapshots establish selected call sequence, not exhaustive instruction execution.',
                'CDVD bytes read from original ISO in host integration; kernel/thread timing remains modeled.',
                'One original bootstrap member; no geometry/upload/AREA runtime-equivalence claim.'],
            runtime={'before':pre_meta['metadata_before'],'after':pre_meta['metadata_after'],
                     'post_before':post_meta['metadata_before'],'post_after':post_meta['metadata_after']})}
    with a.output.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print('PASS: full 4352-byte member, debugger return, integrated reads, 5 handles, arena and completion')
if __name__=='__main__':main()
