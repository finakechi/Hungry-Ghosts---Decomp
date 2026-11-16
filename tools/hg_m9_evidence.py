#!/usr/bin/env python3
"""Assemble M9's turn/camera packet from read-only HGExport snapshots.

Several snapshot folders may hold the same entry; their listings must agree.
Structure and original bytes are checked by the pinned PS2DInfo verifier.
"""
from hg_evidence_safety import select_export, digest
import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from verify_resource_loader import EXPECTED, shared_module

# entry: (C name, complete, interpretation)
FUNCTIONS={
    0x15fe00:('hg_player_movement_gate',True,'Movement gate: dispatch 15fe70, then report/clear +4ad for a selected +2c3.'),
    0x15fe70:('hg_player_movement',True,'Movement/look dispatcher: counters, look or turn, walk, idle timers, heading from 70000b80/b90.'),
    0x1608b0:('hg_player_walk',True,'Forward speed from left-stick Y 3eaf10, turn call, position advance by sin/cos of heading.'),
    0x160510:('hg_player_turn',True,'Turn: left-stick X 3eaf0c level/row 39a3f0, rate +440 approach or decay, heading +44 = wrap(+44 - rate).'),
    0x160420:('hg_player_rate_approach',True,'Rate approach toward a signed limit with opposite-sign braking; raw FP operands first.'),
    0x15fcd0:('hg_player_speed_class',True,'Speed class 1..3 from +4ad, +4d4, +416 and mode bit 4.'),
    0x160c20:('hg_player_move_limit',True,'Movement limit for +2c3 = 2 and 3f9ff2 = 0x68 or 0x8e.'),
    0x1204e0:('hg_angle_wrap',True,'Wrap an angle into (-pi, pi] by repeated EE add/subtract of 2*pi.'),
    0x12c790:('hg_camera_update',False,'Observed 3eaed4 == 0 branch only: node refresh, 12d130 follow, eye, view matrix; other branch fails closed.'),
    0x12d130:('hg_camera_follow',True,'Follow head matrix; without shake the eye/target pair is replaced before look-at 12c3d0.'),
    0x12bd00:('hg_camera_head_matrix',False,'Observed +47a == 0 branch only: pitch/heading/roll head matrix, eye and target; other branch fails closed.'),
}


def select(roots,entry):
    rows=[]
    for root in roots:
        try:path=select_export(root,entry)
        except ValueError as exc:
            if 'found 0' in str(exc):continue
            raise
        rows.append((path,json.loads(path.read_text())))
    if not rows:raise ValueError(f'No export for {entry:08x}')
    if len({r['listing_sha256'] for _,r in rows})!=1:raise ValueError(f'Disagreeing listings for {entry:08x}')
    return rows[0]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True);p.add_argument('--ps2dinfo',type=Path,required=True)
    p.add_argument('--exports',type=Path,nargs='+',required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if digest(a.elf)!=EXPECTED:raise ValueError('Wrong original ELF')
    functions=[]
    for entry,(name,complete,interpretation) in FUNCTIONS.items():
        path,row=select(a.exports,entry)
        functions.append(dict(entry=row['entry'],name=name,complete=complete,body_ranges=row['body_ranges'],
            instructions=row['instructions'],
            references=[{**r,'ghidra_type':r['type'],'type':'call' if 'CALL' in r['type'] else 'unknown'} for r in row['references_to']],
            interpretation=interpretation,verification_status='original-byte-verified',source='src/resource_loader.c',
            original_listing_sha256=row['listing_sha256'],snapshot_sha256=digest(path)))
    record=dict(schema='ps2-evidence/v1',program=dict(game='Hungry Ghosts',build_id='japan',executable='SCPS_150.46',
        sha256=EXPECTED,processor='EE/R5900',reported_language='r5900:LE:32:default',endianness='little',
        address_space='ram',address_context='Original unrelocated file-backed EE PT_LOAD; named M2 project copied from preserved M1 working project.',
        instruction_encoding='ee-mips32',load_address='0x00100000'),
        provenance=dict(classification='observed',observation_date='2026-11-16',
            source='Read-only HGExport snapshots; authenticated against Japanese original ELF.',
            tool='Ghidra HGExport.java',tool_version='12.1.4',retrieval_method='headless Ghidra -readOnly -noanalysis',truncated=False,
            limitations=['Names are analyst assigned.',
                '12c790 and 12bd00 are reconstructed for their observed branches only; the other branches call a host fail-closed fault.',
                'Math, rotation, look-at, strafe and node leaves remain original-instruction-modeled dependencies.']),
        functions=functions)
    verification=shared_module(a.ps2dinfo).verify(a.elf.read_bytes(),record,require_all_bytes=True)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x') as fp:json.dump(record,fp,indent=2);fp.write('\n')
    print(json.dumps(verification))
    print('Functions:',len(functions),'complete:',sum(f['complete'] for f in functions),
          'bytes:',sum(len(f['instructions'])*4 for f in functions))


if __name__=='__main__':main()
