#!/usr/bin/env python3
"""M14 native pre-dispatch differential over authenticated M13 frame pairs.

Each frame starts from the captured post-counter stop, runs the C 00198f60
and the interpreted 0017f6d0/0017f2d0 with their caller arguments, and is
compared with the captured dispatch-entry stop in two ways:

* memory domain: EE RAM except libpad DMA buffers and the guest stack below
  the verified 0012f8a8 sp (PD.NATIVE_SP); the live caller frames at and above
  it and the full 16 KiB scratchpad are compared. Excluded stack differences
  are counted, never silently dropped.
* window outputs: the reconstruction's write set must equal the original-
  instruction write set of the same window (PD.window_oracle), with equal
  values, including the packet streams. This is discriminating even where the
  native packet buffer already holds stale identical bytes.
"""
import argparse
import ctypes as C
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'tests')]
from hg_evidence_safety import digest
from resource_harness import ResourceEnv,library
from verify_resource_loader import EXPECTED,shared_module
from verify_player_connected_runtime import CapturedMemory,pad_dma_buffers
from verify_player_continuous import ContinuousDriver,observation
from verify_player_grounded_runtime import STATUS_VALUES
import predispatch_differential as PD

STACK_START=PD.STACK[0]
SOURCES=('src/resource_loader.c','src/resource_loader.h','tools/verify_predispatch_window.py',
         'tools/predispatch_differential.py','tools/verify_player_connected_runtime.py','tools/ee_subset.py')
# Compiled defects that must be rejected; and those whose only effect is on
# dead callee arguments, which a memory comparison cannot observe by design.
REQUIRED_COMPILED=('store-width-pre-fix','missing-sq-zero','phase-step-sign','wrap-test-inverted','t1-omitted')
DIAGNOSTIC_COMPILED=('152e90-pre-fix-arguments',)
REQUIRED_RUNTIME=('omit-service','wrong-order','post-run-phase-mutation','wrong-a0-17f2d0')
# A skipped 0012f8b8 delay slot leaves a0 = 0x60. On the captured states both
# 0x3f9e30+0x10d and 0x60+0x10d are 0, so 0017f6d0 selects the same jump-table
# entry (0017f828): memory cannot observe it. The synthetic routing test locks
# the call-site argument instead. Recorded, not required.
DIAGNOSTIC_RUNTIME=('missing-delay-slot-17f6d0',)


def compare(actual,expected,initial):
    dma=pad_dma_buffers(initial)
    excluded=lambda a:any(row['start']<=a<row['end'] for row in dma)
    differences=[];dma_diff=0;stack_diff=0
    for base in range(0,0x2000000,0x10000):
        if actual.raw[base:base+0x10000]==expected.raw[base:base+0x10000]:continue
        for off,(x,y) in enumerate(zip(actual.raw[base:base+0x10000],expected.raw[base:base+0x10000])):
            if x==y:continue
            a=base+off
            if STACK_START<=a<PD.NATIVE_SP:stack_diff+=1
            elif excluded(a):dma_diff+=1
            else:differences.append(a)
    scratch=[i for i in range(0x4000) if actual.scratch[i]!=expected.scratch[i]]
    dma_access=[a for a in actual.writes|actual.watched_reads if excluded(a)]
    return dict(result='passed' if not differences and not scratch and not dma_access else 'failed',
                ram_compared_bytes=0x2000000-(PD.NATIVE_SP-STACK_START)-sum(row['end']-row['start'] for row in dma),
                ram_difference_count=len(differences),
                first_ram_differences=[hex(a) for a in differences[:32]],
                scratch_compared_bytes=0x4000,scratch_difference_count=len(scratch),
                first_scratch_differences=[hex(0x70000000+a) for a in scratch[:32]],
                excluded_stack=[hex(STACK_START),hex(PD.NATIVE_SP)],
                excluded_stack_difference_count=stack_diff,
                compared_caller_frames=[hex(PD.NATIVE_SP),'0x2000000'],
                dma_buffer_count=len(dma),dma_difference_count=dma_diff,
                dma_modeled_accesses=[hex(a) for a in sorted(dma_access)[:32]])


def run_case(lib,caps,elf,segments,frame,case=None,oracles=None):
    capture=frame['captures']['coordinator-counter'];dispatch=frame['captures']['dispatch-entry']
    initial=CapturedMemory.load_capture(caps/(capture['name']+'.json'))
    expected=CapturedMemory.load_capture(caps/(dispatch['name']+'.json'))
    key=frame['counter']
    if oracles is None or key not in oracles:
        row=PD.window_oracle(initial,elf,segments)
        oracle=dict(writes=row['writes'],steps=row['steps'],identity_mismatches=row['identity_mismatches'],
                    full_ram_differences=len(PD.changed(row['memory'],expected,0x2000000)),
                    scratch_differences=sum(row['memory'].scratch[i]!=expected.scratch[i] for i in range(0x4000)))
        if oracles is not None:oracles[key]=oracle
    else:oracle=oracles[key]
    memory=CapturedMemory(bytes(initial.raw),bytes(initial.scratch))
    dma=pad_dma_buffers(memory);memory.watch=[(row['start'],row['end']) for row in dma]
    driver=ContinuousDriver(memory,elf,segments,STATUS_VALUES[0])
    fn=lib.hg_pre_dispatch_service;fn.argtypes=[C.POINTER(ResourceEnv)];fn.restype=None
    try:
        if case=='wrong-order':
            driver.external(0x17f2d0,0,0,0,0)
            driver.external(0x17f6d0,0x3f9e30,0,0,0)
        if case!='omit-service':fn(C.byref(driver.env))
        if case=='post-run-phase-mutation':
            memory.store(0x39e98c,memory.load(0x39e98c)^0x80000000)
        calls=[(0x17f6d0,0x3f9e30),(0x17f2d0,0)]
        if case=='wrong-order':calls=[]
        # 0012f8b8 skipped: a0 keeps 00198f60's final `addiu a0,zero,0x60` (0x19942c).
        if case=='missing-delay-slot-17f6d0':calls[0]=(0x17f6d0,0x60)
        if case=='wrong-a0-17f2d0':calls[1]=(0x17f2d0,0x3f9e30)
        for pc,a0 in calls:driver.external(pc,a0,0,0,0)
        if driver.errors:raise AssertionError(driver.errors)
        if case!='omit-service' and (len(driver.declared_registers)!=12 or driver.pending_registers is not None):
            raise AssertionError('Incomplete 0014f060 non-ABI register contract')
        result=compare(memory,expected,initial)
        window=PD.compare_window(initial,memory,memory.writes,initial,expected,oracle['writes'],
                                 [(row['start'],row['end']) for row in dma])
        result['window']=window
        if window['result']!='passed':result['result']='failed'
    except (ValueError,AssertionError) as exc:
        result=dict(result='failed',error=str(exc)[:400])
    result.update(counter=frame['counter'],entry_capture=capture['name'],
                  entry_sha256=digest(caps/(capture['name']+'.json')),
                  dispatch_capture=dispatch['name'],
                  dispatch_sha256=digest(caps/(dispatch['name']+'.json')),
                  oracle=dict(sp=hex(PD.NATIVE_SP),steps=oracle['steps'],
                              identity_mismatches=[hex(a) for a in oracle['identity_mismatches']],
                              full_ram_differences=oracle['full_ram_differences'],
                              scratch_differences=oracle['scratch_differences'],
                              write_count=len(oracle['writes'])),
                  c_service_events=driver.service_c,
                  declared_registers=[(hex(pc),{str(k):hex(v) for k,v in regs.items()})
                                      for pc,regs in driver.declared_registers[:2]],
                  sprite_phase_0039e98c=dict(before=hex(initial.load(0x39e98c)),
                                              actual=hex(memory.load(0x39e98c)),
                                              expected=hex(expected.load(0x39e98c))),
                  interpreted_entries=[row['entry'] for row in driver.modeled_calls if row['entry'] in ('0x17f6d0','0x17f2d0')],
                  modeled_address_count=len(driver.executed))
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True)
    p.add_argument('--ps2dinfo',type=Path,required=True)
    p.add_argument('--capture-dir',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--negative-controls',action='store_true')
    p.add_argument('--max-frames',type=int,default=None,help='smoke run: first N frames only')
    args=p.parse_args()
    if args.output.exists() or args.output.with_suffix('.so').exists():raise FileExistsError(args.output)
    if digest(args.elf)!=EXPECTED:raise ValueError('Wrong Japanese original executable')
    elf=args.elf.read_bytes();segments,_=shared_module(args.ps2dinfo).segments_from_elf(elf)
    obs=observation();lib_path=args.output.with_suffix('.so');lib=library(output=lib_path)
    selected=sorted(obs['frames'],key=lambda fr:fr['counter'])[:args.max_frames]
    frames=[];oracles={}
    for frame in selected:
        row=run_case(lib,args.capture_dir,elf,segments,frame,oracles=oracles)
        frames.append(row)
        print(frame['counter'],row['result'],row.get('first_ram_differences',[])[:1],
              'window',row.get('window',{}).get('result'),'oracle-ram-diff',row['oracle']['full_ram_differences'],flush=True)
    controls=[];diagnostics=[]
    if args.negative_controls:
        first=selected[0]
        for name in REQUIRED_RUNTIME+DIAGNOSTIC_RUNTIME:
            row=run_case(lib,args.capture_dir,elf,segments,first,name,oracles)
            entry=dict(name=name,kind='runtime',detected=row['result']=='failed',detail=row)
            (controls if name in REQUIRED_RUNTIME else diagnostics).append(entry)
            print(name,'detected' if row['result']=='failed' else 'ESCAPED',flush=True)
        folder=args.output.parent/(args.output.stem+'-controls');folder.mkdir(parents=True,exist_ok=False)
        source=(ROOT/'src/resource_loader.c').read_text()
        for name in REQUIRED_COMPILED+DIAGNOSTIC_COMPILED:
            path=folder/f'{name}.c';path.write_text(PD.mutant_source(source,name))
            mlib=library(source=path,output=folder/f'{name}.so')
            row=run_case(mlib,args.capture_dir,elf,segments,first,None,oracles)
            entry=dict(name=name,kind='compiled-'+PD.mutants(source)[name][0],detected=row['result']=='failed',
                       memory_domain=row.get('result') if 'window' not in row else
                       ('passed' if not row['ram_difference_count'] and not row['scratch_difference_count'] else 'failed'),
                       window=row.get('window',{}).get('result'),detail=row)
            (controls if name in REQUIRED_COMPILED else diagnostics).append(entry)
            print(name,'detected' if entry['detected'] else 'ESCAPED','memory-domain',entry['memory_domain'],
                  'window',entry['window'],flush=True)
    ok=all(row['result']=='passed' and row['c_service_events']==[(0x198f60,1),(0x198f60,0)] and
           row['interpreted_entries']==['0x17f6d0','0x17f2d0'] and not row['oracle']['identity_mismatches'] and
           row['oracle']['full_ram_differences']==0 and row['oracle']['scratch_differences']==0 for row in frames)
    ok&=all(row['detected'] for row in controls)
    handoffs=[dict(from_counter=a['counter'],to_counter=b['counter'],
                   phase_matches=a['sprite_phase_0039e98c']['actual']==b['sprite_phase_0039e98c']['before'])
              for a,b in zip(frames,frames[1:])]
    ok &= all(row['phase_matches'] for row in handoffs)
    result=dict(schema='hg-m14-predispatch-window/v2',result='passed' if ok else 'failed',
                smoke_max_frames=args.max_frames,native_sp=hex(PD.NATIVE_SP),
                elf_sha256=EXPECTED,observation_sha256=digest(ROOT/'evidence/m13/continuous-observation.json'),
                source_sha256=digest(ROOT/'src/resource_loader.c'),
                verification_sources={n:digest(ROOT/n) for n in SOURCES},library_sha256=digest(lib_path),
                frames=frames,phase_handoffs=handoffs,negative_controls=controls,
                diagnostic_controls=diagnostics)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(result['result'],len(frames),'frames')
    return 0 if ok else 1


if __name__=='__main__':sys.exit(main())
