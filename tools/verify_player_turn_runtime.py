#!/usr/bin/env python3
"""Exact paused-retail comparison of the M9 turn and camera response.

One continuous original run (evidence/m9/player-turn-camera-observation.json)
stopped natively at the player update, its turn call, its return, and the
camera update entry/return on every frame of a held-then-released left-stick
turn. Reconstructed C runs from each entry capture; full EE RAM and the 16 KiB
scratchpad are compared to the following original stop. Owner captures are
comparison inputs only, never replayed as calculated output.
"""
import argparse
import ctypes as C
import hashlib
import importlib.util
import json
import struct
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'tests')]
import verify_player_connected_runtime as V
from resource_harness import ResourceEnv, library
from verify_resource_loader import EXPECTED, shared_module
from hg_evidence_safety import digest
from ee_subset import fp_value, fp_ee_add

PLAYER,CAMERA=0x3f92d0,0x3f9860
STICK=0x3eaf0c
FRAMES=('neutral','turn1','turn2','release1','release2','release3','release4')
# Last bus accesses before original 00160510's first instruction: walk's
# |forward| read, then 0015fcd0's +4ad read on entry (see 001608b0 listing).
TURN_ENTRY=[('load',PLAYER+0x438,4),('load',PLAYER+0x4ad,1)]
TURN_FIELDS=(('yaw_44',0x44,4),('rate_440',0x440,4),('level_504',0x504,1),('pitch_3c0',0x3c0,4),
    ('pitch_rate_43c',0x43c,4),('side_434',0x434,4),('forward_438',0x438,4),('walk_level_45c',0x45c,1),
    ('x_30',0x30,4),('y_34',0x34,4),('z_38',0x38,4))
CAMERA_FIELDS=(('camera_pitch_d0',0xd0,4),('camera_yaw_d4',0xd4,4))


class Captures:
    """Each capture is parsed and digest-checked once; every run gets a fresh copy."""
    def __init__(self,folder):self.folder=folder;self.cache={}
    def path(self,name):return self.folder/(name+'.json')
    def load(self,name):
        if name not in self.cache:
            m=V.CapturedMemory.load_capture(self.path(name))
            self.cache[name]=(bytes(m.raw),bytes(m.scratch),digest(self.path(name)))
        raw,scratch,_=self.cache[name];return V.CapturedMemory(raw,scratch)
    def sha(self,name):self.load(name);return self.cache[name][2]


def fields(memory,base,table):
    return {name:hex(memory.load(base+off,width)) for name,off,width in table}


def compare(actual,expected):
    """Exact RAM (M8 rules) plus the complete 16 KiB scratchpad."""
    external=V.pad_dma_buffers(expected)
    result=V.compare_memory(actual,expected,external)
    scratch=[i for i in range(0x4000) if actual.scratch[i]!=expected.scratch[i]]
    result['scratchpad']=dict(compared_bytes=0x4000,different_bytes=len(scratch),
        differences=[hex(0x70000000+i) for i in scratch[:32]])
    if scratch:result['result']='failed'
    return result


def execute(lib,captures,entry_name,function,mutate=None,probes=None):
    initial=captures.load(entry_name)
    if mutate:
        for address,value in mutate.items():initial.store(address,value)
        initial.initial=bytes(initial.raw);initial.writes=set()
    external=V.pad_dma_buffers(initial);initial.watch=[(e['start'],e['end']) for e in external]
    driver=V.ProbeDriver(initial,captures.elfdata,captures.segments,probes)
    fn=getattr(lib,function)
    if function=='hg_player_active_update_fixed':
        fn.argtypes=[C.POINTER(ResourceEnv),C.c_uint32,C.c_uint32];fn.restype=None
        fn(C.byref(driver.env),PLAYER,0x1ff8000)
    else:
        fn.argtypes=[C.POINTER(ResourceEnv)];fn.restype=None;fn(C.byref(driver.env))
    if driver.errors:raise AssertionError(driver.errors)
    return driver


def stage(lib,captures,frame,kind,mutate=None):
    entry,ret,function={'update':('update-entry','update-return','hg_player_active_update_fixed'),
        'camera':('camera-entry','camera-return','hg_camera_update_fixed')}[kind]
    entry,ret=f's3-{frame}-{entry}',f's3-{frame}-{ret}'
    driver=execute(lib,captures,entry,function,mutate,{'turn_entry':TURN_ENTRY} if kind=='update' else None)
    expected=captures.load(ret);result=compare(driver.mem,expected)
    result.update(stage=f'{frame}-{kind}',entry=entry,entry_sha256=captures.sha(entry),expected=ret,
        expected_sha256=captures.sha(ret),modeled_leaves=len(driver.modeled_calls),
        modeled_addresses=len(driver.executed),
        player_reconstructed=fields(driver.mem,PLAYER,TURN_FIELDS),player_original=fields(expected,PLAYER,TURN_FIELDS),
        camera_reconstructed=fields(driver.mem,CAMERA,CAMERA_FIELDS),camera_original=fields(expected,CAMERA,CAMERA_FIELDS))
    if kind=='update':
        result['turn_entry_hits']=driver.hits['turn_entry']
        if driver.hits['turn_entry']!=1:result['result']='failed'
        else:
            probe=compare(driver.snapshots['turn_entry'],captures.load(f's3-{frame}-turn-entry'))
            result['turn_entry_probe']=dict(capture=f's3-{frame}-turn-entry',capture_sha256=captures.sha(f's3-{frame}-turn-entry'),
                result=probe['result'],different_bytes=probe['different_bytes'],
                scratch_different_bytes=probe['scratchpad']['different_bytes'],differences=probe['differences'][:8])
            if probe['result']!='passed':result['result']='failed'
    result['_driver']=driver
    return result


def public(row):
    return {k:v for k,v in row.items() if not k.startswith('_')}


def sequence(lib,captures):
    """Every frame's update and camera stage, then cross-stage handoffs."""
    stages=[];drivers={}
    for frame in FRAMES:
        for kind in ('update','camera'):
            row=stage(lib,captures,frame,kind);drivers[(frame,kind)]=row.pop('_driver');stages.append(row)
    # release5: entry and turn probe only (no later stop; see observation record).
    driver=execute(lib,captures,'s3-release5-update-entry','hg_player_active_update_fixed',probes={'turn_entry':TURN_ENTRY})
    probe=compare(driver.snapshots['turn_entry'],captures.load('s3-release5-turn-entry')) if driver.hits['turn_entry']==1 else None
    tail=dict(stage='release5-update',expected='no return stop (condition false)',turn_entry_hits=driver.hits['turn_entry'],
        turn_entry_probe=None if probe is None else dict(result=probe['result'],different_bytes=probe['different_bytes'],
            scratch_different_bytes=probe['scratchpad']['different_bytes']),
        player_reconstructed=fields(driver.mem,PLAYER,TURN_FIELDS),stick=hex(driver.mem.load(STICK)))
    # The absent return stop requires stick 0 and rate 0 at return.
    tail['result']='passed' if probe and probe['result']=='passed' and tail['player_reconstructed']['rate_440']=='0x0' \
        and tail['stick']=='0x0' else 'failed'
    stages.append(tail);drivers[('release5','update')]=driver
    handoffs=[]
    order=list(FRAMES)+['release5']
    for i,frame in enumerate(FRAMES):
        produced=drivers[(frame,'update')].mem;camera_in=captures.load(f's3-{frame}-camera-entry')
        nxt=captures.load(f's3-{order[i+1]}-update-entry')
        rec=dict(frame=frame,
            update_to_camera=[n for n,o,w in TURN_FIELDS if produced.load(PLAYER+o,w)!=camera_in.load(PLAYER+o,w)],
            update_to_next_frame=[n for n,o,w in TURN_FIELDS if produced.load(PLAYER+o,w)!=nxt.load(PLAYER+o,w)],
            camera_to_next_frame=[n for n,o,w in CAMERA_FIELDS
                if drivers[(frame,'camera')].mem.load(CAMERA+o,w)!=nxt.load(CAMERA+o,w)],
            actor_bytes_changed_outside_scope=[hex(j) for j in range(0x540)
                if produced.raw[PLAYER+j]!=camera_in.raw[PLAYER+j]])
        rec['result']='passed' if not (rec['update_to_camera'] or rec['update_to_next_frame'] or rec['camera_to_next_frame']) else 'failed'
        handoffs.append(rec)
    return stages,handoffs,drivers


def f(b):return struct.unpack('<f',struct.pack('<I',b))[0]


def operation_checks(drivers,captures):
    """Turn semantics over the reconstructed sequence; no capture output is used."""
    def p(frame,o,w=4):return drivers[(frame,'update')].mem.load(PLAYER+o,w)
    rates=[p(fr,0x440) for fr in FRAMES]+[p('release5',0x440)]
    sticks=[captures.load(f's3-{fr}-update-entry').load(STICK) for fr in list(FRAMES)+['release5']]
    yaws=[captures.load(f's3-{fr}-update-entry').load(PLAYER+0x44) for fr in list(FRAMES)+['release5']]
    out=[p(fr,0x44) for fr in list(FRAMES)+['release5']]
    ramp=[fp_value(r) for r in rates[:6]]
    view0=[drivers[(fr,'camera')].mem.get(0x70000000,16) for fr in FRAMES]
    camera_yaw=[drivers[(fr,'camera')].mem.load(CAMERA+0xd4) for fr in FRAMES]
    checks=dict(
        stick_held_turn1_to_release3=[hex(s) for s in sticks]==['0x0']+['0xbf800000']*5+['0x0','0x0'],
        neutral_rate_stays_zero=rates[0]==0,
        rate_ramps_negative_while_held=all(b<a<0 for a,b in zip(ramp[1:],ramp[2:])) and ramp[1]<0,
        rate_decays_after_release=fp_value(rates[5])<fp_value(rates[6])<0 and rates[7]==0,
        yaw_is_entry_minus_rate=all(o==fp_ee_add(y,r,True) for y,r,o in zip(yaws,rates,out)),
        level_3_while_held=[p(fr,0x504,1) for fr in FRAMES]==[0,3,3,3,3,3,0],
        camera_yaw_follows_turn=len(set(camera_yaw))==len(camera_yaw),
        view_row0_rotates=view0[0]!=view0[-1] and len(set(view0))==len(view0))
    values=dict(rates=[f(r) for r in rates],yaw_in=[f(y) for y in yaws],yaw_out=[f(y) for y in out],
        camera_yaw=[f(y) for y in camera_yaw])
    return checks,values


def counterfactuals(lib,captures):
    """Changed inputs from real entry states must change the result."""
    rows=[]
    for name,frame,kind,mutate,want in [
            ('turn1-stick-zero','turn1','update',{STICK:0},lambda d:d.mem.load(PLAYER+0x440)==0),
            ('turn1-stick-flipped','turn1','update',{STICK:0x3f800000},lambda d:0<fp_value(d.mem.load(PLAYER+0x440))),
            ('release4-stick-held','release4','update',{STICK:0xbf800000},
                lambda d:fp_value(d.mem.load(PLAYER+0x440))<fp_value(captures.load('s3-release4-update-entry').load(PLAYER+0x440))),
            ('turn2-camera-neutral-yaw','turn2','camera',{PLAYER+0x44:captures.load('s3-neutral-camera-entry').load(PLAYER+0x44)},
                lambda d:d.mem.get(0x70000000,16)!=captures.load('s3-turn2-camera-return').get(0x70000000,16))]:
        row=stage(lib,captures,frame,kind,mutate);driver=row.pop('_driver')
        ok=row['different_bytes']+row['scratchpad']['different_bytes']>0 and want(driver)
        rows.append(dict(control=name,mutation={hex(a):hex(v) for a,v in mutate.items()},
            different_bytes=row['different_bytes'],scratch_different_bytes=row['scratchpad']['different_bytes'],
            expected_behavior_observed=want(driver),result='passed' if ok else 'failed',
            player_reconstructed=row['player_reconstructed']))
    return rows


def model_only_camera(captures):
    """The whole original 0012c790 executed in the instruction model (no C)."""
    rows=[]
    for frame in FRAMES:
        initial=captures.load(f's3-{frame}-camera-entry');external=V.pad_dma_buffers(initial)
        initial.watch=[(e['start'],e['end']) for e in external]
        driver=V.ConnectedRuntimeDriver(initial,captures.elfdata,captures.segments);driver.external(0x12c790)
        r=compare(driver.mem,captures.load(f's3-{frame}-camera-return'))
        rows.append(dict(frame=frame,result=r['result'],different_bytes=r['different_bytes'],
            scratch_different_bytes=r['scratchpad']['different_bytes'],instructions=driver.modeled_calls[-1]['instructions']))
    return rows


C_DEFECTS=(
    ('gate-a1-zero',[('if(call(e,0x15fe00,a,1,0,0)==0)w8(e,a+0x1a','if(call(e,0x15fe00,a,0,0,0)==0)w8(e,a+0x1a'),
        ('uint32_t blocked=call(e,0x15fe00,a,1,0,0);','uint32_t blocked=call(e,0x15fe00,a,0,0,0);')]),
    ('wrong-stick-source','uint32_t level=stick_level(e,fabs_ee(e,r32(e,0x3eaf0c)));',
        'uint32_t level=stick_level(e,fabs_ee(e,r32(e,0x3eaf10)));'),
    ('missing-sign-negation','if(flt(e,r32(e,0x3eaf0c),0)) {step=fneg(step);limit=fneg(limit);}',''),
    ('skipped-yaw-write','w32(e,a+0x44,call(e,0x1204e0,fsub(e,r32(e,a+0x44),r32(e,a+0x440)),0,0,0));',''),
    ('yaw-adds-rate','call(e,0x1204e0,fsub(e,r32(e,a+0x44),r32(e,a+0x440)),0,0,0)',
        'call(e,0x1204e0,fadd(e,r32(e,a+0x44),r32(e,a+0x440)),0,0,0)'),
    ('wrong-turn-scale','uint32_t scale=r32(e,0x3e0870+speed*4),row=0x39a3f0+level*16;',
        'uint32_t scale=r32(e,0x3e0860+speed*4),row=0x39a3f0+level*16;'),
    ('wrong-limit-column','uint32_t step=fmul(e,r32(e,row+8),scale),limit=fmul(e,r32(e,row+12),scale);',
        'uint32_t step=fmul(e,r32(e,row+8),scale),limit=fmul(e,r32(e,row+8),scale);'),
    ('camera-heading-from-rate',':r32(e,player+0x44);\n    for(uint32_t i=0;i<64;i+=16)copy_quad(e,0x70000500+i',
        ':r32(e,player+0x440);\n    for(uint32_t i=0;i<64;i+=16)copy_quad(e,0x70000500+i'),
    ('camera-skips-view-yaw','call(e,0x143d30,r32(e,camera+0xd4),0x70000000,0x70000000,0);',''),
    ('camera-eye-height','0x41840000u','0x41800000u'),
    ('ee-add-without-guard','case 0:case 1:return fp_add_bits(a,b,op==1,1,fault);',
        'case 0:case 1:return fp_add_bits(a,b,op==1,0,fault);'),
    ('vu-add-with-ee-guard','acc[lane]=fp_op(e,6,acc[lane],fmul(e,m[12+lane],v[3]));',
        'acc[lane]=fp_op(e,0,acc[lane],fmul(e,m[12+lane],v[3]));'))
MODEL_DEFECTS=(
    ('model-vopmsub-wrong-order','order=((1,2),(2,0),(0,1))\n                        out=[]',
        'order=((2,1),(0,2),(1,0))\n                        out=[]'),
    ('model-ee-add-without-guard','    if shift:\n        mask=','    if False:\n        mask='),
    ('model-vdiv-zero-raises','self.q=((dividend ^ divisor) & 0x80000000) | 0x7f7fffff',
        "raise ValueError('VDIV by zero')"))


def rejected(lib,captures):
    try:
        stages,handoffs,drivers=sequence(lib,captures);checks,_=operation_checks(drivers,captures)
        failed=[s['stage'] for s in stages if s['result']!='passed']+[h['frame']+'-handoff' for h in handoffs if h['result']!='passed']
        bad=[k for k,v in checks.items() if not v]
        return bool(failed or bad),dict(failed_stages=failed,failed_checks=bad)
    except Exception as error:
        return True,dict(execution_error=type(error).__name__,detail=str(error)[:200])


def negative_controls(captures,folder,baseline):
    source=(ROOT/'src/resource_loader.c').read_text();folder.mkdir(parents=True);rows=[]
    for name,*change in C_DEFECTS:
        pairs=change[0] if len(change)==1 else [tuple(change)]
        mutated=source
        for old,new in pairs:
            if mutated.count(old)!=1:raise AssertionError(f'{name}: mutation target must be unique')
            mutated=mutated.replace(old,new)
        path=folder/(name+'.c');path.open('x').write(mutated)
        ok,reason=rejected(library(source=path,output=folder/(name+'.so')),captures)
        rows.append(dict(control=name,compiled=True,mutated_source_sha256=digest(path),
            result='rejected' if ok else 'ESCAPED',**reason))
    model=(ROOT/'tools/ee_subset.py').read_text();saved=V.Machine
    for name,old,new in MODEL_DEFECTS:
        if model.count(old)!=1:raise AssertionError(f'{name}: model mutation target must be unique')
        path=folder/(name.replace('-','_')+'.py');path.open('x').write(model.replace(old,new))
        spec=importlib.util.spec_from_file_location(path.stem,path);module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module);V.Machine=module.Machine
        try:ok,reason=rejected(baseline,captures)
        finally:V.Machine=saved
        rows.append(dict(control=name,compiled=False,mutated_model_sha256=digest(path),
            result='rejected' if ok else 'ESCAPED',**reason))
    return rows


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True);p.add_argument('--ps2dinfo',type=Path,required=True)
    p.add_argument('--capture-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--negative-controls',action='store_true')
    a=p.parse_args()
    if a.output.exists():raise FileExistsError('Require a fresh JSON output path')
    if digest(a.elf)!=EXPECTED:raise ValueError('Wrong original ELF')
    captures=Captures(a.capture_dir);captures.elfdata=a.elf.read_bytes()
    captures.segments,_=shared_module(a.ps2dinfo).segments_from_elf(captures.elfdata)
    build=Path(tempfile.mkdtemp(prefix='player-turn-',dir=ROOT/'local/build'))
    lib=library(output=build/'runtime.so')
    stages,handoffs,drivers=sequence(lib,captures)
    checks,values=operation_checks(drivers,captures)
    controls=counterfactuals(lib,captures)
    model=model_only_camera(captures)
    result=dict(schema='hg-player-turn-runtime/v1',elf_sha256=EXPECTED,
        stages=[public(s) for s in stages],handoffs=handoffs,operation_checks=checks,operation_values=values,
        input_counterfactuals=controls,original_camera_model=model)
    ok=all(s['result']=='passed' for s in stages) and all(h['result']=='passed' for h in handoffs) \
        and all(checks.values()) and all(c['result']=='passed' for c in controls) and all(m['result']=='passed' for m in model)
    if a.negative_controls:
        result['negative_controls']=negative_controls(captures,a.output.parent/(a.output.stem+'-controls'),lib)
        ok&=all(c['result']=='rejected' for c in result['negative_controls'])
    result['result']='passed' if ok else 'failed'
    result.update(verification_sources={name:digest(ROOT/name) for name in ('src/resource_loader.c','src/resource_loader.h',
            'tools/ee_subset.py','tools/verify_player_connected_runtime.py','tools/verify_player_turn_runtime.py')},
        captures={name:sha for name,(_,_,sha) in sorted(captures.cache.items())},
        limitations=['Hybrid: C turn/camera chain with original-instruction-modeled math, strafe, look-at and node leaves.',
            'Exact comparison over 32 MiB EE RAM and 16 KiB scratchpad; guest stack and libpad DMA buffers excluded as in M8.',
            'Finite functional FP model of PCSX2 v2.8.2 defaults; no FP flags, timing or hardware.',
            'Code between update return and camera entry is outside scope; handoffs check the turn fields only.',
            '12c790 and 12bd00 are reconstructed for their observed branches; others fail closed.'])
    a.output.open('x').write(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(result=result['result'],stages=[(s['stage'],s['result'],s.get('different_bytes'),
        s.get('scratchpad',{}).get('different_bytes')) for s in result['stages']],
        handoffs=[(h['frame'],h['result']) for h in handoffs],checks=checks,
        counterfactuals=[(c['control'],c['result']) for c in controls],model=[(m['frame'],m['result']) for m in model],
        negative_controls=[(c['control'],c['result']) for c in result.get('negative_controls',[])]),indent=1))
    if result['result']!='passed':raise SystemExit(1)

if __name__=='__main__':main()
