#!/usr/bin/env python3
"""Compare M7's bounded engine preparation with independent paused game states.

Uses authenticated disc reads and reconstructed C. The existing audio callback
has a separately observed resource-memory contract; IOP/audio state is excluded.
PINE supplies bytes. PCs/registers are manual screenshot transcriptions.
"""
import argparse
import ctypes as C
import json
import struct
from pathlib import Path
from hg_evidence_safety import digest, receipt
from hg_identify import ISO, read_at
from verify_resource_loader import ROOT, EXPECTED, shared_module
from verify_fixup_runtime import IMAGE, require, samples
from verify_area_runtime import check_profile
from resource_harness import Driver, Memory, library
from test_engine_preparation import configure, TASK
from test_resource_fixup import FRAME
from verify_engine_preparation import validate_packet

EVENTS = {
    'retained_entry':0x130020, 'general6b_entry':0x130900, 'general6b_return':0x131414,
    'general4c_entry':0x130900, 'general4c_return':0x131414,
    'context_entry':0x12fe90, 'context_dispatch':0x12fde0,
    'general85_entry':0x130900, 'general85_return':0x131414, 'engine_return':0x130378,
    'finish':0x1434f0, 'exit':0x103da0,
    'bank6b_entry':0x156a50, 'bank6b_return':0x156ab8,
    'bank85_entry':0x156a50, 'bank85_return':0x156ab8,
}
STATE = (0x3eace0,0x3f3b00,0x3f4300,0x3f4d3c,0x3f8e00,0x3f9fe0,0x3fa1b0,0x46e100)


def word(data, offset=0):
    return int.from_bytes(data[offset:offset+4],'little')


def audio_resource_contract(raw, address):
    """Only the resource-facing success contract of external 00156a50.

    Selected one-bank containers; not a sound driver. No capture-derived bytes
    or return constants. Bank offsets come from the authenticated descriptor;
    six pointer fields use 002b4df0's sentinel/rebasing rule. Zero while busy is
    allowed at the production interface and intentionally omitted here.
    """
    require(len(raw)>=32 and word(raw)==len(raw),'Invalid audio container size')
    require(word(raw,12)==1,'Unverified audio bank count')
    descriptor=32+word(raw,8)*16
    require(descriptor+16<=len(raw),'Audio descriptor outside input')
    bank=word(raw,descriptor+4)
    require(bank+0x80<=len(raw) and word(raw,bank+12)==0x64685353,'Invalid selected bank header')
    result=bytearray(raw)
    for i in range(6):
        offset=word(raw,bank+16+i*4)
        value=0 if offset==0xffffffff else (address+bank+offset)&0xffffffff
        struct.pack_into('<I',result,bank+48+i*4,value)
    endpoint=((address+word(raw,16)+64)&0xffffffff)&0xffffffc0
    return bytes(result),endpoint


def get_bytes(blocks,address,length):
    for base,data in blocks.items():
        if base<=address and address+length<=base+len(data):
            return data[address-base:address-base+length]
    raise ValueError(f'Missing observed range {address:08x}+{length}')


def load_observations(root, emulator, profile, contract_profile):
    path=root/'observation.json';obs=json.loads(path.read_text())
    require(obs['elf_sha256']==EXPECTED and obs['image_sha256']==IMAGE,'Observation build differs')
    require(obs['emulator_sha256']==digest(emulator),'Emulator changed')
    require(obs['profile_sha256']==digest(profile) and obs['contract_profile_sha256']==digest(contract_profile),'Profile changed')
    check_profile(profile,28052);check_profile(contract_profile,28053)
    require([(e['event'],int(e['pc'],16)) for e in obs['observations']]==list(EVENTS.items()),'Missing or reordered checkpoints')
    captured={};inputs={'observation':path}
    for event in obs['observations']+obs['supplemental']:
        for kind in ('capture','screenshot'):
            name=event[kind];p=(root/name).resolve()
            require(p.is_relative_to(root.resolve()) and digest(p)==event[kind+'_sha256'],'Changed or unsafe debugger artifact')
            inputs[name]=p
        captured[event['event']]=samples(root/event['capture'])
    return obs,captured,inputs


class RetailEngineDriver(Driver):
    def __init__(self,mem,fp):
        super().__init__(mem);self.fp=fp;self.audio=[]

    def read(self,lba,count,dst,mode):
        require(0<count<=1024 and mode==b'\0\1\0','Unexpected CDVD contract')
        self.calls.append(('read',lba,count,dst,mode))
        self.mem.put(dst,read_at(self.fp,lba*2048,count*2048));return 1

    def external(self,pc,a=0,b=0,c=0,d=0):
        if pc==0x156a50:
            size=self.mem.load(a)
            require(0<size<=1024*1024,'Unexpected audio container')
            raw=self.mem.get(a,size);changed,end=audio_resource_contract(raw,a)
            # Only the externally owned fields are written, not replayed inputs.
            for offset in range(0,size,4):
                if raw[offset:offset+4]!=changed[offset:offset+4]:
                    self.mem.put(a+offset,changed[offset:offset+4])
            self.calls.append(('call',pc,a));self.audio.append((a,end));return end
        return super().external(pc,a,b,c,d)


class ObservedEngineDriver(RetailEngineDriver):
    """Observe original call seams while delegating every loader calculation to C."""
    def __init__(self,mem,fp,lib,checkpoints):
        super().__init__(mem,fp);self.lib=lib;self.checkpoints=checkpoints;self.generals=[]

    def external(self,pc,a=0,b=0,c=0,d=0):
        if pc==0x130900:
            ident=self.mem.load(a+0x44);name='general'+f'{ident:02x}'
            require(ident in (0x6b,0x4c,0x85),'Unexpected selected directory')
            compare_state(self.mem,self.checkpoints[name+'_entry'],name+' entry')
            self.generals.append((pc,a,ident))
            self.lib.hg_pack_general(C.byref(self.env),a)
            compare_state(self.mem,self.checkpoints[name+'_return'],name+' return')
            return 0
        if pc in (0x12fcf0,0x12fde0):
            getattr(self.lib,'hg_resource_load_indexed' if pc==0x12fcf0 else 'hg_resource_load_context')(C.byref(self.env),a)
            return 0
        if pc==0x137ed0:return self.lib.hg_resource_fixup(C.byref(self.env),a,b,c,FRAME)
        if pc==0x1386e0:self.lib.hg_resource_geometry(C.byref(self.env),a,b);return 0
        if pc==0x1389d0:return self.lib.hg_resource_map_address(C.byref(self.env),a,FRAME-0x50)
        return super().external(pc,a,b,c,d)


def compare_state(mem, observed, label):
    for address in STATE:
        expected=observed[address]
        require(mem.get(address,len(expected))==expected,f'{label}: state differs at {address:08x}')


def verify_contracts(fp,pack,c,regs):
    result=[]
    for ident,address in ((0x6b,0x1009000),(0x85,0xf78000)):
        prefix='bank'+f'{ident:02x}'
        before,after=c[prefix+'_entry'],c[prefix+'_return']
        require(regs[prefix+'_entry']=={'a0':address},'Wrong audio argument')
        header=read_at(fp,(pack['lba']+ident)*2048,2048)
        offset,size,member,_=struct.unpack_from('<4I',header,64)
        raw=read_at(fp,pack['lba']*2048+offset,size)
        require(get_bytes(before,address,size)==raw,'Audio input differs from disc')
        transformed,end=audio_resource_contract(raw,address)
        require(regs[prefix+'_return']=={'v0':end},'Audio endpoint differs from original return')
        require(get_bytes(after,address,size)==transformed,'Audio resource-memory contract differs')
        changed=[i for i in range(0,size,4) if raw[i:i+4]!=transformed[i:i+4]]
        require(len(changed)==6,'Unexpected selected audio net-write range')
        result.append(dict(directory=hex(ident),member=hex(member),input_bytes=size,
            input_sha256=__import__('hashlib').sha256(raw).hexdigest(),argument=hex(address),
            endpoint=hex(end),net_changed_word_offsets=[hex(i) for i in changed],
            comparison='Complete original input and complete container at successful return; endpoint in v0.',
            excluded='Audio globals, bank table, IOP transfers/results, RPC, timing and playback.'))
    return result


def verify_runtime(lib,data,segments,fp,c,obs):
    regs={e['event']:{k:int(v,16) for k,v in e['registers'].items()} for e in obs['observations']}
    for name in ('retained_entry','general6b_entry','general4c_entry','context_entry','context_dispatch','general85_entry'):
        require(regs[name]=={'a0':TASK},'Unexpected defined loader argument')
    pack=ISO(fp).files['/DATA/PACK.DAT']
    contracts=verify_contracts(fp,pack,c,regs)
    before=c['retained_entry'];d=RetailEngineDriver(Memory(data,segments),fp)
    for at,blob in before.items():d.mem.put(at,blob)
    # All new resource reads come from the authenticated image. Entry snapshots
    # contain only preexisting state; never initialize from any post checkpoint.
    lib.hg_resource_prepare_engine_fixed(C.byref(d.env),TASK,FRAME)
    require(not d.errors,'Engine bus error: '+str(d.errors))
    compare_state(d.mem,c['engine_return'],'engine return')
    observed=ObservedEngineDriver(Memory(data,segments),fp,lib,c)
    for at,blob in before.items():observed.mem.put(at,blob)
    lib.hg_resource_prepare_retained(C.byref(observed.env),TASK)
    compare_state(observed.mem,c['context_entry'],'context entry')
    lib.hg_resource_prepare_context(C.byref(observed.env),TASK)
    require(not observed.errors,'Checkpoint driver error: '+str(observed.errors))
    compare_state(observed.mem,c['engine_return'],'observed engine return')
    require(observed.generals==[(0x130900,TASK,x) for x in (0x6b,0x4c,0x85)],'Ordered engine calls differ')

    require(d.mem.load(0x3f9fef,1)==0x4c and d.mem.load(0x46e10c,1)==1,'Publication differs')
    # Entire union of newly read resource sectors, including surviving read tails.
    ranges=((0x1009000,6144),(0x100a800,1016*2048),(0xf78000,23*2048))
    output=c['engine_output'];payloads=[]
    for at,size in ranges:
        expected=get_bytes(output,at,size)
        require(d.mem.get(at,size)==expected,f'Calculated resource bytes differ at {at:08x}')
        payloads.append(dict(address=hex(at),bytes=size,sha256=__import__('hashlib').sha256(expected).hexdigest()))
    for at in (0xa87800,0x1270000):
        expected=c['engine_return'][at]
        require(expected==before[at] and d.mem.get(at,len(expected))==expected,'Retained AREA/PACK bytes differ')
    transfers=[list(x[1:4]) for x in d.calls if x[0]=='read']
    expected_transfers=[]
    for ident in (0x6b,0x4c,0x85):
        header=read_at(fp,(pack['lba']+ident)*2048,2048)
        destination={0x6b:0x1009000,0x4c:0x100a800,0x85:0xf78000}[ident]
        expected_transfers.append([pack['lba']+ident,1,0x3f3b00])
        count=word(header,24)&0xffff
        if count:
            require(count==1,'Unexpected special member count')
            off,size,_,_=struct.unpack_from('<4I',header,64)
            expected_transfers.append([pack['lba']+(off>>11),(size+2047)>>11,destination])
            raw=read_at(fp,pack['lba']*2048+off,size)
            _,destination=audio_resource_contract(raw,destination)
        if word(header,20):expected_transfers.append([pack['lba']+(word(header,16)>>11),(word(header,20)+2047)>>11,destination])
    require(transfers==expected_transfers,'Ordered defined CDVD arguments differ')
    require(d.audio==[(int(v['argument'],16),int(v['endpoint'],16)) for v in contracts],'Ordered audio calls differ')
    # The pair exits before worker completion. Reconstruct its remaining busy /
    # active stores and compare the independently observed finish/exit states.
    d.mem.store(0x3eacec,0,1)
    require(d.mem.get(0x3f4300,2600)==c['finish'][0x3f4300],'Handles changed before finish')
    lib.hg_resource_finish(C.byref(d.env))
    require(d.mem.load(TASK+0x30,2)==word(c['exit'][TASK][0x30:0x32])==0,'Worker active differs')
    require(c['finish'][0x3eace0][12]==c['exit'][0x3eace0][12]==0,'Worker busy differs')
    for at in (0x3f4300,0x3f4d3c,0x1270000,0xa87800):
        require(c['engine_return'][at]==c['finish'][at]==c['exit'][at],'Resource changed through completion')
    # Repeat through the actual kind-8 worker composition: cached marker must
    # avoid all reads and preserve every newly loaded byte.
    calls=len(d.calls);d.mem.store(TASK+0x32,8,2);d.mem.store(TASK+0x30,1,2)
    lib.hg_resource_worker_fixed(C.byref(d.env),FRAME)
    require(not d.errors and not any(x[0]=='read' for x in d.calls[calls:]),'Retained worker unexpectedly reloads')
    for at,size in ranges:require(d.mem.get(at,size)==get_bytes(output,at,size),'Retained worker changes resource')
    return dict(new_read_union=payloads,new_read_bytes=sum(size for _,size in ranges),
        retained_ranges=[dict(address=hex(at),bytes=len(before[at])) for at in (0xa87800,0x1270000)],
        handles_bytes=2600,arena_bytes=36,task_bytes=80,transfers=transfers,audio_contracts=contracts,
        retained_marker='0x4c',ready_byte=1,synthetic_repeat_worker='passed')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('elf','image','runtime','ps2dinfo','emulator','profile','contract-profile','output'):
        p.add_argument('--'+key,required=True,type=Path)
    p.add_argument('--source',type=Path)
    a=p.parse_args()
    require(digest(a.elf)==EXPECTED and digest(a.image)==IMAGE,'Wrong original build')
    obs,captured,inputs=load_observations(a.runtime,a.emulator,a.profile,a.contract_profile)
    data=a.elf.read_bytes();shared=shared_module(a.ps2dinfo);segments,_=shared.segments_from_elf(data)
    packet=json.loads((ROOT/'analysis/engine_preparation/functions.json').read_text())
    validate_packet(packet)
    byte_check=shared.verify(data,packet,require_all_bytes=True)
    c={key:value[1] for key,value in captured.items()}
    actor=c['actor_boundary'][0x4d8150]
    require(actor[0x18]==0 and word(actor,0x1c)==0xa8a070,'Wrong later actor boundary')
    for fn in packet['functions']:
        for ins in fn['instructions']:
            require(get_bytes(c['retained_entry'],int(ins['address'],16),4)==bytes.fromhex(ins['bytes']),'Live instruction differs')
    lib=configure(library(source=a.source,output=a.output.with_suffix('.so')))
    with a.image.open('rb') as fp:checked=verify_runtime(lib,data,segments,fp,c,obs)
    limitations=['This verifies the selected engine directory preparation and resource-facing audio contract, not whole-game/audio/IOP equivalence.',
        'Callee-saved registers and alternate branches use strict instruction comparisons; observed entry a0 and audio success v0 are checked here.',
        'No actor state-0 construction, renderer, VU timing or optional upload equivalence; post-worker actor dispatch is separate.',
        'Memory differences are net effects, not an exhaustive store or PC trace.']
    inputs.update(elf=a.elf,image=a.image,emulator=a.emulator,profile=a.profile,contract_profile=a.contract_profile)
    first=captured['retained_entry'][0];last=captured['engine_return'][0]
    result=dict(schema='hg-engine-runtime/v1',result='passed',elf_sha256=EXPECTED,image_sha256=IMAGE,
        operation='00130020 -> 0012fe90, from 00130368 call boundary to 00130378; original kind-3/c7 completion',
        original_bytes=byte_check,checkpoints=len(EVENTS),checked=checked,limitations=limitations)
    result['binding']=receipt(ROOT,dict(inputs,raw=inputs['captures/stop17.json'],post=inputs['captures/stop26.json']),
        'saved-runtime',['verify_engine_runtime.py'],limitations,
        runtime=dict(before=first['metadata_before'],after=first['metadata_after'],post_before=last['metadata_before'],post_after=last['metadata_after']))
    with a.output.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps({k:v for k,v in result.items() if k!='binding'}))


if __name__=='__main__':main()
