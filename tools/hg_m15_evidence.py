#!/usr/bin/env python3
"""Extract the M15 coordinator render services' original words from the pinned Japanese ELF.

Default rows cover the coordinator tail service 00143150 (called at 0012f900)
and the two observed render-node callbacks reconstructed in C; --model-draw
selects the player model draw 0012bc70 (called at 0012f8f0) and the four
callees on its observed path that are reconstructed in C. Bytes come from the
file-backed EE PT_LOAD; the text column is an analysis aid from the private
local/m15 R5900 decoder (base ISA, COP1, COP2 macro, MMI). PS2DInfo verifies
every byte against the ELF (--require-all-bytes)."""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

from hg_evidence_safety import digest
from hg_m13_evidence import elf_segments, word_at
from verify_resource_loader import EXPECTED, shared_module

DECODER = Path(__file__).resolve().parent.parent / 'local/m15/r5900dis.py'

ROWS = (
    (0x143150, 0x1431a0, 'render_node_list_00143150',
     'Reconstructed as hg_render_node_list. Walks the list at 00539458: the +24 next pointer is '
     'loaded in the jalr delay slot (00143178) before the +1c callback runs with a0 = node; '
     'halfword gp+2f68 is cleared in the beq delay slot (00143168) and counts nodes. Observed '
     'callbacks: 0019c8a0 and 002f7830 (C), 001a6dc0 and 002a20b0 (original instructions).'),
    (0x19c8a0, 0x19cd78, 'camera_anchor_node_0019c8a0',
     'Reconstructed as hg_camera_anchor_node for state +18 == 1 (sub-states +19 = 0, 1, 2). '
     'Active sub-state: +60 += 0.016, +6c += 0.8, node+30 = view matrix 70000000 x (0,-1.5,1.2,1) '
     'plus 0.03 * +6c * camera forward 003f9890; 70000900 = (+60, 1, 0, +90/+94 texture word), '
     '70000910..93f identity, 70000940 = node+30; two 001503f0 sprites (f12 = +68). Sub-state 0 '
     'draws two 0011ad58 values ((r >> 16) << 7 >> 15). Other states fail closed.'),
    (0x2f7830, 0x2f8ae4, 'camera_particle_field_002f7830',
     'Reconstructed as hg_camera_particle_field for state +18 == 1, variant +08 == 0. Three layer '
     'directions (identity rotated by +40/+44/+48 times (0,0,1,1)); layer offsets +60.. advance by '
     'the camera delta plus direction x speed and wrap to [-64, 64]. 4 groups x 32 table particles '
     '(003c9ad0 x +/-64 x 003c9c90 group signs) are wrapped around the camera, culled by '
     'dot(003f9890, p) < 0.16 and distance outside (34, 64), faded by 00143560, and emitted as two '
     'quads (camera pitch/yaw billboard, pitch pi/2 horizontal) of 003c9c50 corners into '
     '700007c0..7ff through 0014d4f0 with a3 = 0x20064405dd413200 (64-bit). Node+50 = camera.'),
)

MODEL_ROWS = (
    (0x12bc70, 0x12bcf4, 'player_model_draw_0012bc70',
     'Reconstructed as hg_player_model_draw. Skipped while 003f92d1 is zero; player +e0 bits '
     '0x30000000 are cleared while gp+2f78, 003f9e3a or 003f9f3d is set and set otherwise; then '
     '00123ee0(a0 = 003f92d0, a1 = 0).'),
    (0x123ee0, 0x12400c, 'model_draw_00123ee0',
     'Reconstructed as model_draw. Requires +e0 & 0xc0000000; a1 == 1 (0018c9c0 test) fails closed; '
     '+90+4c == 0 returns drawn; +90+50 & 0x0f000000 selects 00125d60(1/5/2, ctx+310, owner); then '
     '00124010(owner+90).'),
    (0x124010, 0x125d54, 'model_draw_type_00124010',
     'Observed type (+50 & ff) 3 only: 00128de0(f12 = 1.0, a0 = type word), 00125f30, 00126180, '
     '00128e80; gp+2a24 must be zero at 00125d10. Other types fail closed.'),
    (0x125f30, 0x1260f4, 'model_draw_mesh_00125f30',
     'Reconstructed as model_draw_mesh. +50 & 00f00000 in {00100000, 00200000} -> 00129020(model, '
     '70000c80, 70000cc0); +50 & 30000000 == 30000000 -> 00129e40(model, gp+2a50); +14 != 0 with '
     '+50 bit 24 clear -> 00152aa0 and a 0x30 packet header (gp-7890) on stream gp+2a50.'),
    (0x129020, 0x129600, 'model_light_setup_00129020',
     'Reconstructed as model_light_setup for type != {40,20,e,d,2} and +1ac == 2.0: 00151c40 on the '
     'sp+70 parameter block; 70000700 = model matrix (gp+2a40)+50 x (+190..198, 0); 70000710.xyz = '
     'v * rsqrt(v.v), .w = 0 + v.w (VU0); light column +8/+18/+28; attenuation (1 - |v| / (+1a8 + '
     'light+20)) x +19c..1a4 x +40..48; +3c = 2^23 + 128 x +4c. Point lights (count > 0) fail closed.'),
    (0x129e40, 0x129fe0, 'model_shadow_quad_00129e40',
     'Reconstructed as model_shadow_quad. 70000500 = identity scaled 4.2 (x, y) rotated -pi/2 by '
     '00143c60, row 3 = bone (+58)[+0d]+30; four 0037bab0 corners transformed to sp+30; colour '
     '70000700 = (2, 2, 2, 124); 0014a0a0(s, 2, 2, 0), 0014d4f0(s, sp+30, 70000700, a3 = '
     '0x20072a2599423880), 0014a0a0(s, 0, 0, 0).'),
)


def decoder():
    sys.path.insert(0, str(DECODER.parent))
    import r5900dis
    return r5900dis


def row(elf, segments, entry, end, dis):
    words = []
    for va in range(entry, end, 4):
        w = word_at(elf, segments, va)
        if w is None:
            raise ValueError(f'no file data at {va:08x}')
        words.append(dict(address=f'0x{va:08x}', text=dis(va, w), bytes=w.to_bytes(4, 'little').hex()))
    listing = '\n'.join(f"{r['address']} {r['bytes']} {r['text']}" for r in words) + '\n'
    return dict(body_ranges=[dict(start=f'0x{entry:08x}', end_exclusive=f'0x{end:08x}')],
                instructions=words, listing_sha256=hashlib.sha256(listing.encode()).hexdigest())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf', type=Path, required=True)
    p.add_argument('--ps2dinfo', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--model-draw', action='store_true', help='0012bc70 chain rows instead of 00143150')
    args = p.parse_args()
    if args.output.exists(): raise FileExistsError(args.output)
    if digest(args.elf) != EXPECTED: raise ValueError('Wrong Japanese original executable')
    elf = args.elf.read_bytes(); segments = elf_segments(elf)
    dis = decoder().dis
    record = dict(schema='ps2-evidence/v1',
                  program=dict(game='Hungry Ghosts', build_id='japan', executable='SCPS_150.46',
                               sha256=EXPECTED, processor='EE/R5900',
                               reported_language='r5900:LE:32:default', endianness='little',
                               address_space='ram', instruction_encoding='ee-mips32',
                               load_address='0x00100000',
                               address_context='Original unrelocated file-backed EE PT_LOAD; M15 synthesized rows.'),
                  provenance=dict(classification='observed', observation_date='2026-11-22',
                                  source='Authenticated Japanese ELF and the M13 obs3 continuous observation; '
                                         'rows decoded by the private M15 R5900 decoder.',
                                  tool='local/m15/r5900dis.py', tool_version='M15',
                                  tool_sha256=digest(DECODER),
                                  retrieval_method='file-backed EE PT_LOAD instruction extraction',
                                  truncated=False,
                                  limitations=['Observed bounded paths only; interpreter and C equivalence '
                                               'are separate runtime and synthetic checks.',
                                               'The text column is an analysis aid; bytes are authoritative.']),
                  functions=[])
    for entry, end, name, interpretation in (MODEL_ROWS if args.model_draw else ROWS):
        r = row(elf, segments, entry, end, dis)
        record['functions'].append(dict(entry=f'0x{entry:08x}', name=name, complete=False,
                                        body_ranges=r['body_ranges'], instructions=r['instructions'],
                                        references=[], interpretation=interpretation,
                                        verification_status='original-byte-verified',
                                        source='src/resource_loader.c',
                                        original_listing_sha256=r['listing_sha256'], snapshot_sha256=EXPECTED))
    print(json.dumps(shared_module(args.ps2dinfo).verify(elf, record, require_all_bytes=True)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2) + '\n')


if __name__ == '__main__':
    main()
