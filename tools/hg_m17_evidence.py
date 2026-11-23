#!/usr/bin/env python3
"""Extract the M17 game lifecycle layer's original words.

Rows cover the main loop 0011d830, the mode dispatcher/setters 00143240,
00143300, 001432c0 and the gameplay-mode state machine (0012d1c0 and its
reconstructed callees). Bytes come from the file-backed EE PT_LOAD; the text
column is an analysis aid from the private local/m15 R5900 decoder. The jump
tables read by the mode state machines and the small data tables read by the
area/PACK selection and the render reset are emitted separately (tables.json)
as original data words with their ELF file offsets, so the synthetic
differential and lifecycle sequence can run without the ELF.

    python3 -B tools/hg_m17_evidence.py --elf "$HG_ELF" --ps2dinfo "$HG_PS2DINFO" \
        --output analysis/game_lifecycle/functions.json \
        --tables analysis/game_lifecycle/tables.json
"""
import argparse
import hashlib
import json
from pathlib import Path

from hg_evidence_safety import digest
from hg_m13_evidence import elf_segments, word_at
from hg_m15_evidence import decoder
from verify_resource_loader import EXPECTED, shared_module

DECODER = Path(__file__).resolve().parent.parent / 'local/m15/r5900dis.py'
C = 'src/game_lifecycle.c'
ROWS = (
    (0x11d830, 0x11da00, 'main_0011d830', 'hg_main_init (0011d830..0011d934) and hg_main_frame '
     '(loop body 0011d938..0011d9fc): engine initialization calls, initial mode 001432c0(00121810), '
     'the two 0011dfd0 waits, EE timer setup, then per frame 0095e308 = 0, the render/engine '
     'services, the mode dispatcher 00143240, the vblank wait on 0095e308, 00149890/001496e0 and '
     '[gp+2f84] += 1.'),
    (0x11df10, 0x11df80, 'vblank_start_handler_0011df10', 'hg_vblank_start_handler (INTC 2): DI helper '
     '0010cca0, 0095e308 += 1 (the main loop wait flag), 0095e300 += 1, wake the loader thread '
     '[003f8e34] when the block 003f8e00 is active.'),
    (0x11df80, 0x11dfc8, 'vblank_end_handler_0011df80', 'hg_vblank_end_handler (INTC 3): wake the '
     'priority loader thread [003f6db4] when the block 003f6d80 is active.'),
    (0x121810, 0x1219d4, 'boot_mode_00121810', 'hg_boot_mode: the initial mode installed by main '
     '(first resource request 00130150(0,0), fades, logo/movie steps, then mode 00121be0).'),
    (0x1219e0, 0x121bd4, 'logo_sequence_step_001219e0', 'hg_logo_sequence_step: one timed logo/notice '
     'sequence (300-frame timer at mode +1a, 00123370 draw, chained requests).'),
    (0x121be0, 0x12266c, 'lifecycle_mode_00121be0', 'hg_lifecycle_mode: title, new game (clears '
     '003fa180..0046e140 and sets area triple (0,9,0)), continue, load and area-select states; major 2 '
     'installs the gameplay mode 0012d1c0. Also performs the frame tick gp+2f80 at 00121bf4.'),
    (0x143240, 0x1432b4, 'mode_dispatch_00143240', 'hg_mode_dispatch: points 700000c0 at the mode '
     'block gp+2dc0, promotes state 1/4 to 2 and calls the handler at +4 for state 1, 2 or 4.'),
    (0x1432c0, 0x143300, 'mode_init_001432c0', 'hg_mode_init: writes handler, state 1 and clears '
     '+8..+27 of gp+2dc0 directly.'),
    (0x143300, 0x143358, 'mode_set_00143300', 'hg_mode_set: the same through the 700000c0 pointer.'),
    (0x12d1c0, 0x12d674, 'gameplay_mode_0012d1c0', 'hg_gameplay_mode: 700000c4 = 2, the float '
     'accumulator gp+2f7c (+1.0, reset above 100000.0), the main-loop frame tick gp+2f80 += 1, '
     'then the major state 0..5 (enter, title/menu, reload, new game, continue, play).'),
    (0x12d680, 0x12d7cc, 'gameplay_title_step_0012d680', 'hg_gameplay_title_step (major 1).'),
    (0x12d7d0, 0x12d84c, 'gameplay_enter_step_0012d7d0', 'hg_gameplay_enter_step (major 0 minor 1).'),
    (0x12d850, 0x12d884, 'gameplay_reset_services_0012d850', 'hg_gameplay_reset_services.'),
    (0x12d9a0, 0x12dbcc, 'gameplay_major5_0012d9a0', 'hg_gameplay_major5: minor 0 entry, 1 play '
     '0012e700, 2 0012f270, 3 003372a0, 4 teardown 0012f800, 5 area load 0012dd60, 6 reload.'),
    (0x12dbd0, 0x12dc60, 'area_pack_id_0012dbd0', 'hg_area_pack_id: PACK directory for the area '
     'triple 003fa1b0/b1/b2 from the tables gp-7e00/gp-7df0.'),
    (0x12dc90, 0x12dd54, 'gameplay_entry_step_0012dc90', 'hg_gameplay_entry_step (minor 0).'),
    (0x12dd60, 0x12e01c, 'area_load_step_0012dd60', 'hg_area_load_step (minor 5): render reset, '
     'player block clear, AREA request 00130150(1, table[area]), PACK request 00130150(2|3, '
     '0012dbd0), fades and the per-frame camera/player/area setup until the destination is ready.'),
    (0x12e020, 0x12e140, 'gameplay_reload_step_0012e020', 'hg_gameplay_reload_step.'),
    (0x12e6d0, 0x12e700, 'gameplay_event_frame_0012e6d0', 'hg_gameplay_event_frame.'),
    (0x12e700, 0x12f264, 'gameplay_frame_0012e700', 'hg_gameplay_frame (minor 1): sub 0 entry '
     'setup, sub 1 active frame (coordinator 0012f850/0012f950, event flags 0012fa00), sub 2 '
     'pause menu (resume, title, teardown), sub 3/4 menu and timed states, sub 5 warp to the '
     'destination triple 0046d92c, sub 6 restart in area 1 with preserved fields.'),
    (0x12f7d0, 0x12f7fc, 'gameplay_request_exit_0012f7d0', 'hg_gameplay_request_exit: minor 4.'),
    (0x12f800, 0x12f84c, 'gameplay_teardown_0012f800', 'hg_gameplay_teardown: services, '
     '700000f8 = 1, mode 00121be0.'),
    (0x12fa00, 0x12fb30, 'gameplay_event_flags_0012fa00', 'hg_gameplay_event_flags: pause (1), '
     'menu (2|4|8|0x10) requests.'),
)
TABLES = ((0x3dadf0, 6, '00121810 major (jump table)'), (0x3dae10, 6, '00121be0 major (jump table)'),
          (0x3daec0, 6, '0012d1c0 major (jump table)'), (0x3daee0, 7, '0012d9a0 minor (jump table)'),
          (0x3daf00, 7, '0012e700 sub (jump table)'),
          (0x3db290, 4, '0012dd60 render reset quad copied to [gp+3098]+3f0 (data)'),
          (0x3e0170, 4, '0012dbd0 PACK base table gp-7e00 (data)'),
          (0x3e0180, 4, '0012dbd0 PACK stride table gp-7df0 (data)'),
          (0x3e01a0, 4, '0012dd60 AREA directory halfwords gp-7dd0 (data)'))


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
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--elf', type=Path, required=True)
    p.add_argument('--ps2dinfo', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--tables', type=Path, required=True)
    args = p.parse_args()
    for out in (args.output, args.tables):
        if out.exists():
            raise FileExistsError(out)
    if digest(args.elf) != EXPECTED:
        raise ValueError('Wrong Japanese original executable')
    elf = args.elf.read_bytes()
    segments = elf_segments(elf)
    dis = decoder().dis
    record = dict(schema='ps2-evidence/v1',
                  program=dict(game='Hungry Ghosts', build_id='japan', executable='SCPS_150.46',
                               sha256=EXPECTED, processor='EE/R5900',
                               reported_language='r5900:LE:32:default', endianness='little',
                               address_space='ram', instruction_encoding='ee-mips32',
                               load_address='0x00100000',
                               address_context='Original unrelocated file-backed EE PT_LOAD; M17 synthesized rows.'),
                  provenance=dict(classification='observed', observation_date='2026-11-23',
                                  source='Authenticated Japanese ELF; rows decoded by the private M15 R5900 decoder.',
                                  tool='local/m15/r5900dis.py', tool_version='M15',
                                  tool_sha256=digest(DECODER),
                                  retrieval_method='file-backed EE PT_LOAD instruction extraction',
                                  truncated=False,
                                  limitations=['Original-instruction versus C equivalence is a separate '
                                               'differential (tools/verify_lifecycle_differential.py).',
                                               'The text column is an analysis aid; bytes are authoritative.']),
                  functions=[])
    for entry, end, name, interpretation in ROWS:
        r = row(elf, segments, entry, end, dis)
        record['functions'].append(dict(entry=f'0x{entry:08x}', name=name, complete=True,
                                        body_ranges=r['body_ranges'], instructions=r['instructions'],
                                        references=[], interpretation=interpretation,
                                        verification_status='original-byte-verified', source=C,
                                        original_listing_sha256=r['listing_sha256'], snapshot_sha256=EXPECTED))
    print(json.dumps(shared_module(args.ps2dinfo).verify(elf, record, require_all_bytes=True)))
    tables = []
    for address, count, user in TABLES:
        words = [word_at(elf, segments, address + 4 * i) for i in range(count)]
        if None in words:
            raise ValueError(f'table {address:08x} outside file data')
        offset = next(o + address - v for v, o, s in segments if v <= address < v + s)
        tables.append(dict(address=f'0x{address:08x}', file_offset=f'0x{offset:x}', user=user,
                           words=[f'0x{w:08x}' for w in words]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2) + '\n')
    args.tables.write_text(json.dumps(dict(schema='hg-m17-jump-tables/v1', elf_sha256=EXPECTED,
                                           tables=tables), indent=2) + '\n')


if __name__ == '__main__':
    main()
