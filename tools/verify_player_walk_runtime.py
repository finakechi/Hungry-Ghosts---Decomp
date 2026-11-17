#!/usr/bin/env python3
"""M10 walking-specific paused-retail comparison, with calculated state handoffs.

Reuses only generic M9 capture/diff helpers. No turn-specific assertions apply.
Expected capture bytes are comparison inputs, never calculated outputs.
"""
import argparse
import ctypes as C
import gc
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tests'),str(ROOT/'tools')]
import verify_player_connected_runtime as V
from verify_player_turn_runtime import Captures, compare, fields, f
from verify_resource_loader import EXPECTED, shared_module
from resource_harness import ResourceEnv, library
from hg_evidence_safety import digest
P,CAM=0x3f92d0,0x3f9860
FRAMES=tuple(f'f{i:02}' for i in range(12))
# Persisted, calculated fields checked at every next-frame entry. Other frame
# work (input polling, animation/timers, actors, rendering) stays external.
CONTINUITY=((P,0x540),(CAM,0x150))
PF=(('x',0x30,4),('y',0x34,4),('z',0x38,4),('previous_x',0x390,4),('previous_z',0x398,4),
    ('speed',0x438,4),('side',0x434,4),('yaw',0x44,4),('level',0x45c,1),('clear_428',0x428,4))
CF=(('eye_x',0,4),('eye_y',4,4),('eye_z',8,4),('target_x',0x10,4),('target_z',0x18,4))

class WalkingDriver(V.ProbeDriver):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.before_clear=None;self.history=None;self.camera_entry=None
    def external(self,pc,a=0,b=0,c=0,d=0):
        if pc==0x15e1d0 and self.camera_entry is None:
            self.camera_entry=V.CapturedMemory(bytes(self.mem.raw),bytes(self.mem.scratch))
        return super().external(pc,a,b,c,d)

    def _store(self,_,address,width,value):
        # The C dispatcher performs this write just after active update returns.
        if address==P+0x428 and width==4 :
            self.before_clear=V.CapturedMemory(bytes(self.mem.raw),bytes(self.mem.scratch))
        result=super()._store(_,address,width,value)
        if address==0x3f9f6e:
            self.history=V.CapturedMemory(bytes(self.mem.raw),bytes(self.mem.scratch))
        return result


def capture_name(caps,frame,stage):return caps.observation['frames'][int(frame[1:])]['captures'][stage]['name']

def observed(caps,frame,stage):return caps.load(capture_name(caps,frame,stage))

def execute(lib,caps,frame,mutate=None,carry=None,probes=False):
    mem=observed(caps,frame,'dispatch-entry')
    if carry:
        for address,size in CONTINUITY:mem.put(address,carry.get(address,size))
    if mutate:
        for address,value in mutate.items():mem.store(address,value)
    mem.initial=bytes(mem.raw);mem.writes=set()
    ext=V.pad_dma_buffers(mem);mem.watch=[(e['start'],e['end']) for e in ext]
    signatures={'active':[('store',P+1,1,1),('load',0x3eaef0,4)],
        'walk':[('load',0x3eaf10,4)]} if probes else {}
    driver=WalkingDriver(mem,caps.elfdata,caps.segments,signatures)
    fn=lib.hg_player_walk_camera_fixed;fn.argtypes=[C.POINTER(ResourceEnv),C.c_uint32];fn.restype=None
    fn(C.byref(driver.env),0x1ff8000)
    if driver.errors:raise AssertionError(driver.errors)
    if driver.camera_entry is None:raise AssertionError('Missing camera entry coverage')
    return driver,driver.camera_entry


def summarize(mem):return dict(player=fields(mem,P,PF),camera=fields(mem,CAM,CF))


def sequence(lib,caps):
    stages=[];handoffs=[];values=[];prior=None;all_calls=[];addresses=set()
    for fr in FRAMES:
        entry=observed(caps,fr,'dispatch-entry')
        if prior:
            differences=[hex(a+i) for a,n in CONTINUITY for i in range(n) if prior.get(a+i,1)!=entry.get(a+i,1)]
            handoffs.append(dict(frame=fr,result='passed' if not differences else 'failed',differences=differences))
        driver,dispatched=execute(lib,caps,fr,carry=prior,probes=True)
        for label,actual,expected in [
            ('active-prefix',driver.snapshots.get('active'),'update-entry'),
            ('walk-entry',driver.snapshots.get('walk'),'walk-entry'),
            ('active-return',driver.before_clear,'update-return'),
            ('history-copy',driver.history,'history-copy'),
            ('dispatcher',dispatched,'camera-entry'),
            ('connected-camera',driver.mem,'camera-return')]:
            if actual is None:raise AssertionError(f'Missing {fr}/{label} coverage')
            result=compare(actual,observed(caps,fr,expected))
            stages.append(dict(frame=fr,stage=label,expected=capture_name(caps,fr,expected),**result))
        final=driver.mem
        history=final.get(P+0x390,16)==final.get(P+0x30,16)
        values.append(dict(frame=fr,counter=entry.load(0x46e118),stick=f(entry.load(0x3eaf10)),
            speed=f(final.load(P+0x438)),x=f(final.load(P+0x30)),z=f(final.load(P+0x38)),
            eye_x=f(final.load(CAM)),history_matches=history,
            **summarize(final)))
        prior=final;all_calls+=driver.modeled_calls;addresses.update(driver.executed)
        gc.collect()
        print(fr,'PASSED' if all(s['result']=='passed' for s in stages[-6:]) else 'FAILED',flush=True)
    checks=dict(input_sequence=[r['stick'] for r in values]==[0,0]+[1]*7+[0]*3,
        consecutive_frames=[r['counter'] for r in values]==list(range(0x2cb,0x2d7)),
        neutral_stationary=values[0]['speed']==values[1]['speed']==0 and values[0]['x']==values[1]['x'],
        held_speed_ramps=all(0<a['speed']<b['speed'] for a,b in zip(values[2:8],values[3:9])),
        forward_displacement=all(a['x']<b['x'] for a,b in zip(values[1:9],values[2:10])),
        release_brakes=0<values[9]['speed']<values[8]['speed'],
        reaches_zero=values[10]['speed']==values[11]['speed']==0,
        following_update_stationary=values[10]['x']==values[11]['x'],
        camera_x_tracks_player=all(r['eye_x']==r['x'] for r in values),
        previous_position_all_four_lanes=all(r['history_matches'] for r in values))
    return dict(stages=stages,handoffs=handoffs,operation_checks=checks,operation_values=values,
        modeled_leaves=sorted({r['entry'] for r in all_calls}),modeled_instruction_addresses=len(addresses)),addresses


# Each control runs a complete selected frame and measures the full final state.
# An exception or driver error is an error, never a successful rejection.
DEFECTS=[
 ('wrong-stick-source','f02','uint32_t axis=fabs_ee(e,r32(e,0x3eaf10));','uint32_t axis=fabs_ee(e,r32(e,0x3eaf0c));'),
 ('wrong-forward-sign','f02','if(flt(e,r32(e,0x3eaf10),0))','if(!flt(e,r32(e,0x3eaf10),0))'),
 ('speed-scaling','f02','uint32_t scale=r32(e,0x3e0860+speed*4),row=','uint32_t scale=fmul(e,0x40000000u,r32(e,0x3e0860+speed*4)),row='),
 ('missing-position','f02','w32(e,a+0x30,fadd(e,r32(e,a+0x30),fmul(e,r32(e,a+0x438),s)));','w32(e,a+0x30,fadd(e,r32(e,a+0x30),fmul(e,0,s)));'),
 ('missing-history','f02','copy_quad(e,a+0x390,a+0x30);','copy_quad(e,a+0x390,a+0x390);'),
 ('early-history','f03','call(e,0x159570,a,0,0,0);','copy_quad(e,a+0x390,a+0x30);call(e,0x159570,a,0,0,0);'),
 ('skipped-active','f02','call(e,0x159570,a,0,0,0);','call(e,0x1204e0,a,0,0,0);'),
 ('camera-eye-translation','f03','copy_quad(e,0x70000030,camera);','w32(e,camera,r32(e,camera)+1u);copy_quad(e,0x70000030,camera);'),
 ('camera-lags-player','f03','call(e,0x12d130,player,camera,0,0);','w32(e,player+0x30,fsub(e,r32(e,player+0x30),r32(e,player+0x438)));call(e,0x12d130,player,camera,0,0);'),
 ('missing-post-update-clear','f02','call(e,0x159570,a,0,0,0);\n    w32(e,a+0x428,0);','call(e,0x159570,a,0,0,0);'),
 ('camera-before-dispatch','f03','hg_player_dispatch(&p.env);\n    hg_camera_update(&p.env);','hg_camera_update(&p.env);\n    hg_player_dispatch(&p.env);'),
 ('no-release-braking','f09','} else decay_rate(e,a+0x438,step);','} else decay_rate(e,a+0x438,0);')]


def controls(lib,caps,folder):
    folder.mkdir(parents=True,exist_ok=False);rows=[];source=(ROOT/'src/resource_loader.c').read_text()
    for name,frame,old,new in DEFECTS:
        if source.count(old)!=1:raise AssertionError(f'{name}: target not unique')
        mutated_source=source.replace(old,new)
        if name=='early-history':mutated_source=mutated_source.replace('copy_quad(e,a+0x390,a+0x30);\n}', '/* history was copied before the update */\n}')
        path=folder/(name+'.c');path.open('x').write(mutated_source)
        try:
            mutated=library(source=path,output=folder/(name+'.so'))
            d,_=execute(mutated,caps,frame);r=compare(d.mem,observed(caps,frame,'camera-return'))
            row=dict(control=name,frame=frame,result='rejected' if r['result']=='failed' else 'ESCAPED',comparison=r)
        except Exception as exc:row=dict(control=name,frame=frame,result='error',detail=str(exc))
        rows.append(row);print(name,row['result'],flush=True);gc.collect()
        with (folder/(name+'.result.json')).open('x') as out:json.dump(row,out,indent=2);out.write('\n')
    for name,frame,value in [('zero-forward','f02',0),('flipped-forward','f02',0xbf800000),('release-still-held','f09',0x3f800000)]:
        try:
            d,_=execute(lib,caps,frame,mutate={0x3eaf10:value});r=compare(d.mem,observed(caps,frame,'camera-return'))
            rows.append(dict(control=name,frame=frame,result='rejected' if r['result']=='failed' else 'ESCAPED',comparison=r))
        except Exception as exc:rows.append(dict(control=name,frame=frame,result='error',detail=str(exc)))
        with (folder/(name+'.result.json')).open('x') as out:json.dump(rows[-1],out,indent=2);out.write('\n')
        gc.collect()
    return rows


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True);p.add_argument('--ps2dinfo',type=Path,required=True)
    p.add_argument('--capture-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--negative-controls',action='store_true');a=p.parse_args()
    if a.output.exists() or a.output.with_suffix('.so').exists():raise FileExistsError('Fresh output required')
    if digest(a.elf)!=EXPECTED:raise ValueError('Wrong original ELF')
    caps=Captures(a.capture_dir);caps.elfdata=a.elf.read_bytes()
    caps.observation=json.loads((ROOT/'evidence/m10/player-walk-observation.json').read_text())
    for frame in caps.observation['frames']:
        for row in frame['captures'].values():
            if caps.sha(row['name'])!=row['sha256']:raise ValueError('Observation/capture digest mismatch')
            if caps.load(row['name']).load(0x46e118)!=frame['counter']:raise ValueError('Wrong capture frame')
            for shot in row['screenshots']:
                if digest(a.capture_dir.parent/'stops'/shot['name'])!=shot['sha256']:raise ValueError('Screenshot digest mismatch')
    caps.segments,_=shared_module(a.ps2dinfo).segments_from_elf(caps.elfdata)
    lib=library(output=a.output.with_suffix('.so'));result,addresses=sequence(lib,caps)
    with a.output.with_suffix('.baseline.json').open('x') as out:json.dump(result,out,indent=2);out.write('\n')
    gc.collect()
    # No instruction executed by modeled dependencies may differ from the ELF;
    # ConnectedRuntimeDriver checks every address on every modeled call.
    if a.negative_controls:result['negative_controls']=controls(lib,caps,a.output.parent/(a.output.stem+'-controls'))
    ok=all(s['result']=='passed' for s in result['stages']+result['handoffs']) and all(result['operation_checks'].values())
    ok &= all(c['result']=='rejected' for c in result.get('negative_controls',[]))
    result.update(schema='hg-player-walk-runtime/v1',result='passed' if ok else 'failed',elf_sha256=EXPECTED,
        captures={n:sha for n,(_,_,sha) in sorted(caps.cache.items())},
        verification_sources={n:digest(ROOT/n) for n in ('src/resource_loader.c','src/resource_loader.h','tools/ee_subset.py',
            'tools/verify_player_walk_runtime.py','tools/verify_player_connected_runtime.py')},
        continuity_ranges=[dict(address=hex(a),size=n) for a,n in CONTINUITY],
        limitations=['Hybrid C and authenticated original-instruction-modeled leaves; not whole-frame reconstruction.',
            'Guest stack 01fe0000..02000000 excluded; asynchronous libpad DMA excluded only if untouched by execution.',
            'Persisted calculated player/camera fields feed following updates; intervening external work comes from next entry capture.',
            'PCSX2 v2.8.2 functional FP defaults; no exceptional FP, flags or hardware timing.'])
    with a.output.open('x') as out:json.dump(result,out,indent=2);out.write('\n')
    print(result['result'],result['operation_checks'])
    if not ok:raise SystemExit(1)

if __name__=='__main__':main()
