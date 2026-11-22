#!/usr/bin/env python3
"""Extract the M14 service window's original words from the pinned Japanese ELF."""
import argparse
import json
from pathlib import Path

from hg_evidence_safety import digest
from hg_m13_evidence import elf_segments,synth_row
from verify_resource_loader import EXPECTED,shared_module


ROWS=(
    (0x198f60,0x199464,'pre_dispatch_sprite_service_00198f60',
     'Observed twelve-record path reconstructed as hg_pre_dispatch_service. '
     'Phase +0c of each 0039e960 record persists across frames. Packet headers '
     'store bytes 0-1 (sh), 3 (sb) and 4-7 (sw) only; each 0x40-byte quad packet '
     'stores sq $zero at +10, sw at +1c and 64-bit sd at +20/+28/+30/+38 '
     '(001991c0..d4, 001992a0..b4, 001993d0..e4). 0014f060 also receives '
     'non-ABI t0=70000ba0, t1=ctx+5b0, t2=record+18; 00152e90 receives '
     'a2=final 0x30 packet address and a3=ctx. Other array shapes fail closed.'),
    (0x17f6d0,0x17fac4,'pre_dispatch_neighbor_0017f6d0',
     'Observed caller a0=003f9e30, including the 0012f8b8 delay slot; '
     'executed by the original-instruction interpreter.'),
    (0x17f2d0,0x17f574,'pre_dispatch_neighbor_0017f2d0',
     'Observed caller a0=0, including the 0012f8c0 delay slot; '
     'executed by the original-instruction interpreter.'),
)

RENDER_ROWS = (
    (0x148970, 0x1493e0, 'render_packet_begin_00148970',
     'Executed in the instruction model. Rebuilds packet cursors from the active bank table; '
     '0014906c calls 00155330.'),
    (0x149870, 0x149884, 'render_bank_flip_00149870',
     'Tail of 001496e0: store 1 minus the previous render bank at ctx+0x598.'),
    (0x149940, 0x149970, 'render_field_sample_00149940',
     'Reads bit 13 from 0x12001000 and stores it at ctx+0x59c in the return delay slot. '
     'The reconstruction accepts the field as a host input.'),
    (0x155330, 0x155344, 'render_queue_reset_00155330',
     'Clears queue+0x2004 and stores the queue base at +0x2000 in the return delay slot.'),
)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True)
    p.add_argument('--ps2dinfo',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--render-handoff', action='store_true',
                   help='extract the inter-frame render setup and its handoff state writers')
    args=p.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    if digest(args.elf)!=EXPECTED:raise ValueError('Wrong Japanese original executable')
    elf=args.elf.read_bytes();segments=elf_segments(elf)
    record=dict(schema='ps2-evidence/v1',
                program=dict(game='Hungry Ghosts',build_id='japan',executable='SCPS_150.46',
                             sha256=EXPECTED,processor='EE/R5900',
                             reported_language='r5900:LE:32:default',endianness='little',
                             address_space='ram',instruction_encoding='ee-mips32',
                             load_address='0x00100000',
                             address_context='Original unrelocated file-backed EE PT_LOAD; M14 synthesized rows.'),
                provenance=dict(classification='observed',observation_date='2026-11-21',
                                source='Authenticated Japanese ELF and M13 continuous observation; '
                                       'rows decoded by the existing M13 R5900 decoder.',
                                tool='local/m13/r5900dump.py',tool_version='M13',
                                retrieval_method='file-backed EE PT_LOAD instruction extraction',
                                truncated=False,
                                limitations=['Observed bounded path only; interpreter and C equivalence '
                                             'are separate runtime checks.',
                                             'The private decoder renders sq/sd/lq/ld, COP1 and MMI words '
                                             'as ???; bytes are authoritative and the store widths are '
                                             'stated in the 00198f60 interpretation.']),functions=[])
    rows = RENDER_ROWS if args.render_handoff else ROWS
    c_entries = {0x198f60, 0x149870, 0x149940}
    for entry,end,name,interpretation in rows:
        row=synth_row(elf,segments,entry,end)
        record['functions'].append(dict(entry=f'0x{entry:08x}',name=name,complete=False,
                                        body_ranges=row['body_ranges'],instructions=row['instructions'],
                                        references=[],interpretation=interpretation,
                                        verification_status='original-byte-verified',
                                        source='src/resource_loader.c' if entry in c_entries else 'tools/ee_subset.py',
                                        original_listing_sha256=row['listing_sha256'],snapshot_sha256=EXPECTED))
    print(json.dumps(shared_module(args.ps2dinfo).verify(elf,record,require_all_bytes=True)))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(record,indent=2)+'\n')


if __name__=='__main__':main()
