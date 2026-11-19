#!/usr/bin/env python3
"""Exact paused-retail comparison of connected M8 C with explicit modeled leaves.

Owner captures are comparison inputs, never replayed as calculated output.
The instruction oracle is finite/chop and functional: flags, timing, exceptional
FP values, and PS2 hardware are outside its contract. A mismatch is a failure.
"""
import argparse
import ctypes as C
import hashlib
import json
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'tests')]
from ee_subset import Machine
from resource_harness import Driver,ResourceEnv,library
from verify_resource_loader import EXPECTED,shared_module
from hg_evidence_safety import digest

class CapturedMemory:
    """Full EE RAM plus scratchpad; reject uncaptured scratch reads before writes."""
    def __init__(self,ram,scratch=None):
        if len(ram)!=0x2000000:raise ValueError('Require complete 32 MiB EE RAM')
        self.raw=bytearray(ram);self.initial=bytes(ram)
        self.scratch=bytearray(scratch or bytes(0x4000))
        self.scratch_known=set(range(len(scratch or b'')))
        self.writes=set()
        self.watch=();self.watched_reads=set()
    def get(self,address,size):
        for start,end in self.watch:
            if address<end and start<address+size:self.watched_reads.add(address)
        if 0<=address<=len(self.raw)-size:return bytes(self.raw[address:address+size])
        if 0x70000000<=address<=0x70004000-size:
            offset=address-0x70000000
            if any(i not in self.scratch_known for i in range(offset,offset+size)):
                raise ValueError(f'Read of uncaptured scratchpad {address:#x}+{size}')
            return bytes(self.scratch[offset:offset+size])
        raise ValueError(f'Unmapped comparison memory {address:#x}+{size}')
    def put(self,address,data):
        if 0<=address<=len(self.raw)-len(data):self.raw[address:address+len(data)]=data
        elif 0x70000000<=address<=0x70004000-len(data):
            offset=address-0x70000000;self.scratch[offset:offset+len(data)]=data
            self.scratch_known.update(range(offset,offset+len(data)))
        else:raise ValueError(f'Unmapped comparison store {address:#x}+{len(data)}')
        self.writes.update(range(address,address+len(data)))
    def __getitem__(self,address):return self.get(address,1)[0]
    def __setitem__(self,address,value):self.put(address,bytes([value]))
    def update(self,pairs):
        for address,value in pairs:self[address]=value
    def load(self,address,width=4):return int.from_bytes(self.get(address,width),'little')
    def store(self,address,value,width=4):self.put(address,(value&((1<<(width*8))-1)).to_bytes(width,'little'))
    @classmethod
    def load_capture(cls,path):
        data=json.loads(path.read_text());meta=data['metadata_before']
        if meta!=data['metadata_after'] or meta['status']!=1:raise ValueError('Require stable paused capture')
        if meta['serial']!='SCPS-15046' or meta['uuid'].lower()!='91aa4b28':raise ValueError('Wrong retail runtime')
        if 'invalid_for_comparison' in data:raise ValueError('Capture explicitly quarantined')
        ram=None;scratch=None
        for sample in data['samples']:
            raw=bytes.fromhex(sample['bytes']);address=int(sample['address'],16)
            if len(raw)!=sample['size_bytes'] or hashlib.sha256(raw).hexdigest()!=sample['sha256']:
                raise ValueError('Capture sample digest mismatch')
            if address==0 and len(raw)==0x2000000:
                if ram is not None:raise ValueError('Duplicate full RAM sample')
                ram=raw
            elif address==0x70000000 and len(raw)<=0x4000:scratch=raw
            else:raise ValueError('Unexpected capture range')
        if ram is None:raise ValueError('Missing complete RAM capture')
        return cls(ram,scratch)

# Host bus seams for modeled original leaves called from reconstructed C:
# entry -> (leading raw-bit FP operands placed in f12.., result in f0?).
# Remaining bus operands go to a0.. in order. Verified against each listing.
FP_SEAMS={0x13a050:(1,False),0x15e190:(1,False),0x188550:(1,False),0x17d930:(1,False),
    0x13a060:(2,False),0x120830:(0,True),0x1204e0:(1,True),
    0x143560:(1,True),0x143620:(1,True),0x143c60:(1,False),0x143d30:(1,False),0x143b90:(1,False),
    0x120580:(2,True),0x15ebc0:(1,False),0x160420:(3,False),
    0x145630:(1,False)}  # M12: C 0015e420 floor probe, f12 = segment rise
# Host-only fail-closed signal from C paths outside the reconstructed domain.
DOMAIN_FAULT=0xfffffff0
# Host-only M11 seam: (bytes, 1) enters and (bytes, 0) leaves a reconstructed
# function's original guest frame, so modeled callees run at its stack depth.
GUEST_FRAME=0xfffffff1

class ConnectedRuntimeDriver(Driver):
    """Original-address engine leaves execute actual input instructions on one bus."""
    def __init__(self,mem,elfdata,segments):
        super().__init__(mem);self.elfdata=elfdata;self.segments=segments
        self.modeled_calls=[];self.executed=set();self.model_depth=0
        # Opt-in M11 contracts; defaults preserve the M8-M10 behaviour.
        self.guest_frame=0;self.cop0_status=None;self.constant_t0={}
    def verify_executed(self,addresses):
        for address in addresses:
            offsets=[off+address-start for start,end,off in self.segments if start<=address and address+4<=end]
            if len(offsets)!=1:raise ValueError(f'Modeled code outside authenticated resident ELF {address:#x}')
            offset=offsets[0]
            if self.mem.initial[address:address+4]!=self.elfdata[offset:offset+4]:
                raise ValueError(f'Runtime instruction differs from original ELF {address:#x}')
    def external(self,pc,a=0,b=0,c=0,d=0):
        self.calls.append((hex(pc),*[hex(v) for v in (a,b,c,d)]))
        if pc==0x119ac8:
            if c>0x2000000:raise ValueError('Unbounded memset')
            self.mem.put(a,bytes([b&255])*c);return a
        if pc==0x119910:
            if c>0x2000000:raise ValueError('Unbounded memcpy')
            self.mem.put(a,self.mem.get(b,c));return a
        if pc==DOMAIN_FAULT:raise ValueError(f'C path outside reconstructed domain (code {a:#x})')
        if pc==GUEST_FRAME:
            if b not in (0,1) or a&15 or (b==0 and self.guest_frame<a):raise ValueError('Invalid guest frame seam')
            self.guest_frame+=a if b else -a;return 0
        machine=Machine();machine.mem=self.mem;machine.reg[28]=0x3e7f70
        machine.cop0_status=self.cop0_status
        # Each nested modeled call receives separate scratch stack storage.
        machine.reg[29]=0x1ff0800-self.guest_frame-self.model_depth*0x1000
        if self.model_depth==0 and pc in self.constant_t0:machine.reg[8]=self.constant_t0[pc]
        fp_count,fp_result=FP_SEAMS.get(pc,(0,False))
        operands=(a,b,c,d)
        for index,value in enumerate(operands[:fp_count]):machine.fpr[12+index]=value
        for index,value in enumerate(operands[fp_count:],4):machine.reg[index]=value
        def memcall(cpu,target):
            return self.external(target,*(cpu.reg[i]&0xffffffff for i in (4,5,6,7)))
        machine.hooks={target:(lambda cpu,target=target:memcall(cpu,target)) for target in (0x119910,0x119ac8)}
        self.model_depth+=1
        try:
            value=machine.run(pc,limit=2000000)
        finally:self.model_depth-=1
        self.verify_executed(machine.executed);self.executed.update(machine.executed)
        self.modeled_calls.append(dict(entry=hex(pc),arguments=[hex(v) for v in (a,b,c,d)],
            instructions=machine.steps,instruction_addresses=len(machine.executed)))
        return machine.fpr[0] if fp_result else value&0xffffffff

# Guest call frames differ between host C and instruction execution. These
# explicit transient stack bytes are not gameplay state and are omitted.
STACK_EXCLUSION=(0x1fe0000,0x2000000)

# libpad receive buffers are written asynchronously by the IOP through SIF DMA.
# Original 0010d840 (port open) records each 64-byte-aligned 2 x 0x80 buffer in
# the table at 003f0a50 + port*0x70 + slot*0x1c (+0 buffer, +0x10 open flag);
# 0010dae0 selects the newer half by frame counter +0x58/+0xd8. A difference
# there is accepted only when modeled EE execution neither read nor wrote it.
PAD_TABLE=0x3f0a50
def pad_dma_buffers(memory):
    buffers=[]
    for port in range(2):
        for slot in range(4):
            entry=PAD_TABLE+port*0x70+slot*0x1c
            if memory.load(entry+0x10)==1:
                base=memory.load(entry)
                if base&0x3f or not 0<base<0x2000000-0x100:raise ValueError(f'Invalid libpad buffer {base:#x}')
                buffers.append(dict(port=port,slot=slot,start=base,end=base+0x100))
    return buffers

def compare_memory(actual,expected,external=()):
    lower,upper=STACK_EXCLUSION
    def outside(i):return any(e['start']<=i<e['end'] for e in external)
    if len(actual.raw)!=len(expected.raw):raise ValueError('Comparison sizes differ')
    raw=[]
    for base in range(0,len(actual.raw),0x1000):  # scan only differing pages
        left=actual.raw[base:base+0x1000];right=expected.raw[base:base+0x1000]
        if left!=right:
            raw+=[base+i for i,(a,b) in enumerate(zip(left,right)) if a!=b and not lower<=base+i<upper]
    dma=[i for i in raw if outside(i)];differences=[i for i in raw if not outside(i)]
    touched=sorted(i for i in actual.writes|actual.watched_reads if outside(i))
    words=sorted({i&~3 for i in differences})
    return dict(result='passed' if not differences and not touched else 'failed',
        compared_bytes=len(actual.raw)-(upper-lower),different_bytes=len(differences),different_words=len(words),
        stack_excluded=dict(start=hex(lower),end_exclusive=hex(upper)),
        external_dma=dict(domain='libpad IOP SIF-DMA receive buffers, not EE-written',
            buffers=[dict(port=e['port'],slot=e['slot'],start=hex(e['start']),end_exclusive=hex(e['end'])) for e in external],
            different_bytes=len(dma),modeled_accesses=[hex(i) for i in touched[:32]],
            differences=[dict(address=hex(i),actual=hex(actual.load(i)),expected=hex(expected.load(i))) for i in sorted({i&~3 for i in dma})[:32]]),
        differences=[dict(address=hex(i),actual=hex(actual.load(i)),expected=hex(expected.load(i))) for i in words[:128]],
        differences_truncated=len(words)>128)

def run_initializer(lib,elfdata,segments,capture_dir):
    before=capture_dir/'native-player-init-entry-001.json';after=capture_dir/'native-player-init-return-001.json'
    initial=CapturedMemory.load_capture(before);expected=CapturedMemory.load_capture(after)
    external=pad_dma_buffers(initial);initial.watch=[(e['start'],e['end']) for e in external]
    driver=ConnectedRuntimeDriver(initial,elfdata,segments)
    lib.hg_player_initialize_fixed.argtypes=[C.POINTER(ResourceEnv),C.c_uint32]
    lib.hg_player_initialize_fixed.restype=None
    lib.hg_player_initialize_fixed(C.byref(driver.env),0x3f92d0)
    if driver.errors:raise AssertionError(driver.errors)
    comparison=compare_memory(driver.mem,expected,external)
    comparison.update(case='player-initializer',before_sha256=digest(before),after_sha256=digest(after),
        modeled_leaves=driver.modeled_calls,modeled_addresses=len(driver.executed),
        actor_matches=driver.mem.get(0x3f92d0,0x540)==expected.get(0x3f92d0,0x540),
        node_pool_matches=driver.mem.get(0x4d8140,8)==expected.get(0x4d8140,8))
    return comparison

PLAYER=0x3f92d0
# Named player fields observed to change through the selected operation.
GAMEPLAY_FIELDS=(('state_19',0x19,1),('state_1a',0x1a,1),('mode_448',0x448,1),
    ('request_449',0x449,1),('mode_copy_44a',0x44a,1),('equipment_450',0x450,4),('flag_45f',0x45f,1))
# Game pad block consumed by 00159570/00159ad0; edge/held halfwords for Square.
INPUT_BLOCK=(0x3eaf00,0x3c)
def gameplay_state(memory):
    return {name:hex(memory.load(PLAYER+offset,width)) for name,offset,width in GAMEPLAY_FIELDS}
def input_state(memory):
    start,size=INPUT_BLOCK
    return dict(edge_3eaf00=hex(memory.load(start,2)),held_3eaf04=hex(memory.load(start+4,2)),
        word_3eaf06=hex(memory.load(start+6,2)),block_sha256=hashlib.sha256(memory.get(start,size)).hexdigest())

class ProbeDriver(ConnectedRuntimeDriver):
    """Snapshot C bus state when an exact access signature identifies a callee entry.

    Each signature ends with the callee's first bus access, so the snapshot is the
    state at the original jal target. Modeled leaves use their own memory path."""
    def __init__(self,mem,elfdata,segments,probes=None):
        super().__init__(mem,elfdata,segments)
        self.probes=probes or {};self.recent=[];self.snapshots={};self.hits={name:0 for name in self.probes}
    def _event(self,event):
        self.recent=(self.recent+[event])[-8:]
        for name,signature in self.probes.items():
            if self.recent[-len(signature):]==signature:
                self.hits[name]+=1
                if name not in self.snapshots:
                    copy=CapturedMemory(bytes(self.mem.raw),bytes(self.mem.scratch))
                    copy.scratch_known=set(self.mem.scratch_known);copy.writes=set(self.mem.writes)
                    copy.watched_reads=set(self.mem.watched_reads);self.snapshots[name]=copy
    def _load(self,_,address,width):
        self._event(('load',address,width));return super()._load(_,address,width)
    def _store(self,_,address,width,value):
        super()._store(_,address,width,value);self._event(('store',address,width,value&((1<<(8*width))-1)))

TRANSITION_ENTRY=[('store',0x3f9f38,1,0),('load',PLAYER+0x449,1),('load',PLAYER+0x50c,1)]
CONSUMER_ENTRY=[('store',0x95e57c,4,1),('load',0x3fa200,1),('load',0x3fa200,1),('load',PLAYER+0x19,1)]

def run_observed(lib,elfdata,segments,capture_dir,label,before_name,after_name,function,
                 probes=None,scratch_seed=None,mutate=None,expect_match=True):
    before=capture_dir/(before_name+'.json');after=capture_dir/(after_name+'.json')
    initial=CapturedMemory.load_capture(before);expected=CapturedMemory.load_capture(after)
    provenance=None
    if scratch_seed is not None:
        source_name,start,end=scratch_seed;source=capture_dir/(source_name+'.json')
        if any(i in initial.scratch_known for i in range(start,end)):raise ValueError('Seed would replace captured scratch')
        donor=CapturedMemory.load_capture(source)
        if source==after:raise ValueError('Scratch seed must not be the expected output')
        initial.scratch[start:end]=donor.scratch[start:end];initial.scratch_known.update(range(start,end))
        provenance=dict(scratch_range=[hex(0x70000000+start),hex(0x70000000+end)],source=source.name,
            source_sha256=digest(source),bytes_sha256=hashlib.sha256(bytes(donor.scratch[start:end])).hexdigest())
    mutation=None
    if mutate is not None:
        changed={}
        for address,value in mutate.items():
            changed[hex(address)]=[hex(initial.raw[address]),hex(value)];initial.raw[address]=value
        initial.initial=bytes(initial.raw);mutation=changed
    external=pad_dma_buffers(initial);initial.watch=[(e['start'],e['end']) for e in external]
    driver=ProbeDriver(initial,elfdata,segments,probes)
    entry_state=gameplay_state(initial);entry_input=input_state(initial)
    args=[C.byref(driver.env),PLAYER]
    fn=getattr(lib,function);fn.argtypes=[C.POINTER(ResourceEnv),C.c_uint32]
    if function=='hg_player_active_update_fixed':
        fn.argtypes.append(C.c_uint32);args.append(0x1ff8000)
    fn.restype=None;fn(*args)
    if driver.errors:raise AssertionError(driver.errors)
    comparison=compare_memory(driver.mem,expected,external)
    if provenance is not None:
        start,end=scratch_seed[1],scratch_seed[2]
        provenance['written_by_update']=any(0x70000000+i in driver.mem.writes for i in range(start,end))
        if provenance['written_by_update']:comparison['result']='failed'
    comparison.update(case=label,before_sha256=digest(before),after_sha256=digest(after),
        modeled_leaves=driver.modeled_calls,modeled_addresses=len(driver.executed),
        actor_matches=driver.mem.get(PLAYER,0x540)==expected.get(PLAYER,0x540),
        node_pool_matches=driver.mem.get(0x4d8140,8)==expected.get(0x4d8140,8),
        input=entry_input,gameplay_entry=entry_state,gameplay_reconstructed=gameplay_state(driver.mem),
        gameplay_original=gameplay_state(expected),probe_hits=driver.hits)
    if provenance is not None:comparison['scratch_seed']=provenance
    if mutation is not None:comparison['input_mutation']=mutation
    if not expect_match:
        # Negative control: the reconstructed result must diverge from the original.
        comparison['control_expectation']='must differ from original output'
        comparison['result']='passed' if comparison['different_bytes']>0 else 'failed'
    comparison['_driver']=driver;comparison['_expected']=expected
    return comparison

def public(case):
    return {k:v for k,v in case.items() if not k.startswith('_')}

def probe_comparison(case,name,capture_dir,capture_name):
    driver=case['_driver'];path=capture_dir/(capture_name+'.json')
    if driver.hits.get(name)!=1:
        return dict(probe=name,result='failed',hits=driver.hits.get(name),capture=path.name)
    original=CapturedMemory.load_capture(path);snapshot=driver.snapshots[name]
    external=pad_dma_buffers(original)
    result=compare_memory(snapshot,original,external)
    result.update(probe=name,capture=path.name,capture_sha256=digest(path),hits=1,
        gameplay_reconstructed=gameplay_state(snapshot),gameplay_original=gameplay_state(original))
    return result

# Captures from one continuous original run, bound to native stops in
# evidence/m8/connected-gameplay-observation.json.
SCRATCH_SCENE=('resume-ready-neutral-return-001','resume-ready-square-entry-001','resume-square-edge-entry-001',
    'resume-equipment-transition-entry-001','resume-square-update-return-001','resume-equipment-next-entry-001',
    'resume-equipment-consumer-entry-001','resume-equipment-next-return-001')

NEUTRAL_SEED='resume-square-edge-entry-001'
def run_neutral(lib,elfdata,segments,capture_dir):
    """No-input control. Its entry capture predates scratchpad sampling. The 64
    bytes at 70000000 (a camera matrix read, never written, by this update) are
    supplied from a separate independent entry capture, never the expected
    output. Two values of that matrix occur in the scene; the supplied value must
    equal the same-frame return capture, and the other observed value is run as
    a recorded sensitivity probe. This is weaker than a fully captured entry."""
    blocks={name:bytes(CapturedMemory.load_capture(capture_dir/(name+'.json')).scratch[:0x40]) for name in SCRATCH_SCENE}
    variants=sorted(set(blocks.values()))
    seed=blocks[NEUTRAL_SEED]
    if seed!=blocks['resume-ready-neutral-return-001']:raise ValueError('Seed differs from same-frame return matrix')
    case=run_observed(lib,elfdata,segments,capture_dir,'neutral','resume-ready-neutral-entry-001',
        'resume-ready-neutral-return-001','hg_player_active_update_fixed',
        probes={'transition_entry':TRANSITION_ENTRY},scratch_seed=(NEUTRAL_SEED,0,0x40))
    sensitivity=[]
    for value in variants:
        if value==seed:continue
        donor=next(name for name,block in blocks.items() if block==value)
        other=run_observed(lib,elfdata,segments,capture_dir,'neutral-sensitivity','resume-ready-neutral-entry-001',
            'resume-ready-neutral-return-001','hg_player_active_update_fixed',scratch_seed=(donor,0,0x40))
        sensitivity.append(dict(source=donor,different_bytes=other['different_bytes'],
            differences=other['differences'][:8]))
    case['scratch_seed'].update(same_frame_return_equal=True,scene_variants=len(variants),
        scene_captures=list(SCRATCH_SCENE),alternative_value_runs=sensitivity,
        limitation='Entry scratchpad not captured; supplied from an independent capture of the same scene.')
    unchanged=case['gameplay_entry']==case['gameplay_reconstructed']==case['gameplay_original']
    case['gameplay_unchanged']=unchanged
    if not unchanged or case['probe_hits']['transition_entry']!=0:case['result']='failed'
    return public(case)

def run_changed_input(lib,elfdata,segments,capture_dir):
    """Counterfactual: the Square entry with its edge/held halfwords cleared to
    the neutral values must not transition and must not match the Square result."""
    mutate={address:0 for address in (0x3eaf00,0x3eaf01,0x3eaf04,0x3eaf05,0x3eaf06,0x3eaf07)}
    case=run_observed(lib,elfdata,segments,capture_dir,'changed-input-control','resume-square-edge-entry-001',
        'resume-square-update-return-001','hg_player_active_update_fixed',
        probes={'transition_entry':TRANSITION_ENTRY},mutate=mutate,expect_match=False)
    if case['probe_hits']['transition_entry']!=0 or case['gameplay_reconstructed']!=case['gameplay_entry']:
        case['result']='failed'
    case['differences']=case['differences'][:16]
    return public(case)

def run_connected(lib,elfdata,segments,capture_dir):
    """Initializer -> active update with Square -> 158e20 transition -> next
    update consuming the transitioned state, each against original stops."""
    stages=[];ok=True
    init=run_initializer(lib,elfdata,segments,capture_dir);stages.append(dict(stage='initializer',
        result=init['result'],different_bytes=init['different_bytes'],actor_matches=init['actor_matches']))
    ok&=init['result']=='passed'
    square=run_observed(lib,elfdata,segments,capture_dir,'square-update','resume-square-edge-entry-001',
        'resume-square-update-return-001','hg_player_active_update_fixed',probes={'transition_entry':TRANSITION_ENTRY})
    mid=probe_comparison(square,'transition_entry',capture_dir,'resume-equipment-transition-entry-001')
    nxt=run_observed(lib,elfdata,segments,capture_dir,'next-update','resume-equipment-next-entry-001',
        'resume-equipment-next-return-001','hg_player_active_update_fixed',probes={'consumer_entry':CONSUMER_ENTRY,
        'transition_entry':TRANSITION_ENTRY})
    consumer=probe_comparison(nxt,'consumer_entry',capture_dir,'resume-equipment-consumer-entry-001')
    # The following update must observe exactly the gameplay state that the
    # reconstructed Square update produced; other actor bytes changed between
    # updates are listed as writes by code outside this reconstruction.
    produced=square['_driver'].mem;observed=nxt['_driver'].mem
    next_entry=CapturedMemory.load_capture(capture_dir/'resume-equipment-next-entry-001.json')
    between=[hex(i) for i in range(0x540) if produced.raw[PLAYER+i]!=next_entry.raw[PLAYER+i]]
    handoff=dict(stage='square-to-next-handoff',gameplay_produced=gameplay_state(produced),
        gameplay_observed_by_next=gameplay_state(next_entry),actor_bytes_changed_outside_scope=between)
    handoff['result']='passed' if handoff['gameplay_produced']==handoff['gameplay_observed_by_next'] else 'failed'
    square_input=square['input']
    checks=dict(
        square_edge_consumed=square_input['edge_3eaf00']=='0x80' and square_input['held_3eaf04']=='0x80',
        transition_called_once=square['probe_hits']['transition_entry']==1,
        mode_entered=square['gameplay_entry']['mode_448']=='0x0' and square['gameplay_reconstructed']['mode_448']=='0x1',
        next_sees_mode=nxt['gameplay_entry']['mode_448']=='0x1',
        next_consumer_called_once=nxt['probe_hits']['consumer_entry']==1,
        next_not_retransitioned=nxt['probe_hits']['transition_entry']==0,
        next_state_19_set=nxt['gameplay_entry']['state_19']=='0x0' and nxt['gameplay_reconstructed']['state_19']=='0x1')
    for name,case in (('square-update',square),('next-update',nxt)):
        stages.append(dict(stage=name,**{k:v for k,v in public(case).items() if k not in ('modeled_leaves','case')}))
    stages.insert(2,dict(stage='square-transition-entry',**mid))
    stages.append(dict(stage='next-consumer-entry',**consumer));stages.insert(4,handoff)
    ok&=all(s['result']=='passed' for s in stages) and all(checks.values())
    return dict(case='connected-gameplay-sequence',result='passed' if ok else 'failed',
        stages=stages,gameplay_checks=checks,
        modeled_leaves=dict(square=square['modeled_leaves'],next=nxt['modeled_leaves']))

# Compiled defects that the retail connected comparison must reject.
RUNTIME_DEFECTS=(
    ('wrong-square-edge-source','uint32_t edge=r16(e,0x3eaf04);','uint32_t edge=r16(e,0x3eaf08);'),
    ('lost-transition-actor','case 0x159380:hg_player_apply_transition(&p->env,a);return 0;',
        'case 0x159380:hg_player_apply_transition(&p->env,0);return 0;'),
    ('skipped-equipment-transition','case 0x158e20:hg_player_equipment_transition(&p->env,a);return 0;',
        'case 0x158e20:return 0;'),
    ('wrong-entered-mode','w8(e,a+0x448,1);w8(e,0x46e12d,1);','w8(e,a+0x448,2);w8(e,0x46e12d,1);'),
    ('wrong-subsequent-update-state','w8(e,a+0x19,1);\n    }\n    return 1;','w8(e,a+0x19,2);\n    }\n    return 1;'))

def runtime_negative_controls(elfdata,segments,capture_dir,folder,baseline_lib):
    import ee_subset
    source=(ROOT/'src/resource_loader.c').read_text();folder.mkdir(parents=True)
    controls=[]
    for name,old,new in RUNTIME_DEFECTS:
        if source.count(old)!=1:raise AssertionError(f'{name}: mutation target must be unique')
        path=folder/(name+'.c');path.open('x').write(source.replace(old,new))
        # M9 hardening: only a completed comparison with failed stages/checks is a
        # rejection; an exception is 'error' and fails the run instead.
        try:
            outcome=run_connected(library(source=path,output=folder/(name+'.so')),elfdata,segments,capture_dir)
            failed=[s['stage'] for s in outcome['stages'] if s['result']!='passed']
            checks=[k for k,v in outcome['gameplay_checks'].items() if not v]
            result='rejected' if outcome['result']=='failed' else 'ESCAPED'
            reason=dict(failed_stages=failed,failed_checks=checks)
        except Exception as error:
            result='error';reason=dict(execution_error=type(error).__name__,detail=str(error)[:200])
        controls.append(dict(control=name,compiled=True,mutated_source_sha256=digest(path),result=result,**reason))
    # Model control: the measured EE DIV.S rounding is required; chop must fail.
    saved=ee_subset.fp_signed_nearest
    ee_subset.fp_signed_nearest=ee_subset.fp_signed_chop
    try:
        chop=public(run_observed(baseline_lib,elfdata,segments,capture_dir,'next-with-chop-division',
            'resume-equipment-next-entry-001','resume-equipment-next-return-001','hg_player_active_update_fixed'))
    finally:ee_subset.fp_signed_nearest=saved
    controls.append(dict(control='div-s-chop-rounding',compiled=False,
        result='rejected' if chop['result']=='failed' else 'ESCAPED',different_words=chop['different_words']))
    return controls

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--elf',type=Path,required=True);parser.add_argument('--ps2dinfo',type=Path,required=True)
    parser.add_argument('--capture-dir',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--case',choices=['initializer','transition','neutral','square','next',
        'changed-input','connected','all'],default='all')
    parser.add_argument('--negative-controls',action='store_true',
        help='Also compile retail-rejected defects into a fresh sibling folder')
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError('Require a fresh JSON output path')
    cases=[]
    def attempt(label,operation):
        try:cases.append(operation())
        except Exception as error:
            detail=str(error)
            if isinstance(error,OSError):
                detail='Owner input or local execution unavailable'
            cases.append(dict(case=label,result='failed',execution_error=dict(
                type=type(error).__name__,detail=detail)))
    def setup():
        if digest(args.elf)!=EXPECTED:raise ValueError('Wrong original ELF')
        shared=shared_module(args.ps2dinfo);elfdata=args.elf.read_bytes();segments,_=shared.segments_from_elf(elfdata)
        build_root=ROOT/'local/build';build_root.mkdir(parents=True,exist_ok=True)
        build=Path(tempfile.mkdtemp(prefix='player-connected-',dir=build_root))/'runtime.so'
        lib=library(output=build)
        return lib,elfdata,segments
    try:lib,elfdata,segments=setup()
    except Exception as error:
        attempt('setup',lambda:(_ for _ in ()).throw(error))
        lib=None
    if lib is not None and args.case in ('initializer','all'):
        attempt('player-initializer',lambda:run_initializer(lib,elfdata,segments,args.capture_dir))
    observed={
        'transition':('resume-equipment-transition-entry-001','resume-equipment-transition-return-001','hg_player_equipment_transition_fixed'),
        'square':('resume-square-edge-entry-001','resume-square-update-return-001','hg_player_active_update_fixed'),
        'next':('resume-equipment-next-entry-001','resume-equipment-next-return-001','hg_player_active_update_fixed')}
    for label,(before,after,fn) in observed.items():
        if lib is not None and args.case in (label,'all'):
            attempt(label,lambda:public(run_observed(lib,elfdata,segments,args.capture_dir,label,before,after,fn)))
    special={'neutral':run_neutral,'changed-input':run_changed_input,'connected':run_connected}
    for label,operation in special.items():
        if lib is not None and args.case in (label,'all'):
            attempt(label,lambda:operation(lib,elfdata,segments,args.capture_dir))
    controls=None
    if lib is not None and args.negative_controls:
        try:controls=runtime_negative_controls(elfdata,segments,args.capture_dir,
            args.output.parent/(args.output.stem+'-controls'),lib)
        except Exception as error:controls=[dict(control='setup',result='ESCAPED',error=type(error).__name__,detail=str(error))]
    passed=all(c['result']=='passed' for c in cases) and (controls is None or all(c['result']=='rejected' for c in controls))
    result=dict(schema='hg-player-connected-runtime/v1',result='passed' if passed else 'failed',elf_sha256=EXPECTED,cases=cases,
        verification_sources={name:digest(ROOT/name) for name in ('src/resource_loader.c','src/resource_loader.h',
            'tools/ee_subset.py','tools/verify_player_connected_runtime.py')},
        limitations=['Hybrid C control/resource orchestration with original-instruction-modeled engine/math leaves; not all-C equivalence.',
            'Paused RAM samples do not establish PCs; native debugger screenshots bind observations separately.',
            'Exact comparison, no FP tolerance or expected-output replay. All reported mismatches remain failures.',
            'Finite functional math model omits exceptional FP behavior, flags, timing and hardware.',
            'Explicit guest transient stack interval omitted; scratchpad is used only when captured or computed.'])
    if controls is not None:result['runtime_negative_controls']=controls
    args.output.open('x').write(json.dumps(result,indent=2)+'\n')
    print(json.dumps([{k:v for k,v in case.items() if k not in ('modeled_leaves','differences')} for case in cases],indent=2))
    if result['result']!='passed':raise SystemExit(1)
if __name__=='__main__':main()
