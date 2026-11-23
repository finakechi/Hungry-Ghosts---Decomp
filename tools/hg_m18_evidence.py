#!/usr/bin/env python3
"""Extract the M18 front-end layer's original words.

ELF rows cover the fade/transition services (00138fa0..00139240), the
controller service (0013d910..0013e8f0), the new-game state (00141810,
001418b0, 00141980), the menu clear 00147f10, the pause menu 00162750 with
its cursor 00161cf0 and the title card scan 002cf020. Overlay rows cover the
title menu 00a87880 and card poll 00a87de0 of /AREA0/A0_B09.BIN (linked at
00a87800). Bytes come from the file-backed EE PT_LOAD or the authenticated
overlay; the text column is an analysis aid from the private local/m15 R5900
decoder. The jump/data tables read by the C are emitted with their ELF file
offsets (tables.json).

    python3 -B tools/hg_m18_evidence.py --elf "$HG_ELF" --overlay local/m18/ovl/A0_B09.BIN \\
        --ps2dinfo "$HG_PS2DINFO" --output analysis/front_end/functions.json \\
        --overlay-output analysis/front_end/overlay_functions.json \\
        --tables analysis/front_end/tables.json
    python3 -B tools/hg_m18_evidence.py --verify --elf "$HG_ELF" --overlay local/m18/ovl/A0_B09.BIN
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path

from hg_evidence_safety import digest
from hg_m13_evidence import elf_segments, word_at
from hg_m15_evidence import decoder
from verify_resource_loader import EXPECTED, shared_module

ROOT = Path(__file__).resolve().parent.parent
DECODER = ROOT / 'local/m15/r5900dis.py'
C = 'src/front_end.c'
OVERLAY_SHA256 = 'ef257df984b273e0482c1b6575c1f4a1898e7f4a33861b8127001cc5a769e935'
OVERLAY_BASE = 0xa87800
ROWS = (
    (0x138fa0, 0x138fc4, 'fade_reset_00138fa0', 'hg_fade_reset: clears the fade block gp+2f40.', True),
    (0x138fd0, 0x139008, 'fade_clear_00138fd0', 'hg_fade_clear(blend, guard): phase 0, level 0 '
     'unless the 00a6bf95 guard is set.', True),
    (0x139010, 0x139034, 'fade_black_00139010', 'hg_fade_black(blend, layer): phase 2, kind 1, level 0xff.', True),
    (0x139040, 0x1390a8, 'fade_out_00139040', 'hg_fade_out(step, blend, mode, layer): phase 3, kind '
     '4/5/7 for mode 1/2/3 else 3; mode 1 also sets the pause-capture flag 700000d4 = 1.', True),
    (0x1390b0, 0x1390f0, 'fade_in_001390b0', 'hg_fade_in(step, blend, layer, guard): phase 1, kind 2.', True),
    (0x1390f0, 0x139140, 'music_fade_2_001390f0', 'hg_music_fade_2: state 2, target level*64/128.', True),
    (0x139140, 0x139188, 'music_fade_1_00139140', 'hg_music_fade_1.', True),
    (0x139190, 0x1391e8, 'music_fade_6_00139190', 'hg_music_fade_6.', True),
    (0x1391f0, 0x139238, 'music_fade_5_001391f0', 'hg_music_fade_5.', True),
    (0x139240, 0x13968c, 'fade_service_00139240', 'hg_fade_service: per-frame fade step by kind (table '
     '003db1f0) and the fade quad draws 0014a0a0/0014f370 (bus calls).', True),
    (0x13d910, 0x13d970, 'pad_clear_buttons_0013d910', 'hg_pad_clear_buttons.', True),
    (0x13d970, 0x13da7c, 'pad_service_0013d970', 'hg_pad_service: port state machine, read and the '
     'game copies gp+2f90..2fc8.', True),
    (0x13dad0, 0x13dae4, 'pad_clear_port_0013dad0', 'hg_pad_clear_port.', True),
    (0x13dc30, 0x13e088, 'pad_port_0013dc30', 'hg_pad_port: libpad port state machine (11 states, '
     'table 003db210); libpad entries are bus calls (IOP boundary).', True),
    (0x13e090, 0x13e4f8, 'pad_read_0013e090', 'hg_pad_read: scePadRead buffer to held/edge/repeat '
     'words, pressure and analog conversion (0013e5b0 is a bus call).', True),
    (0x13e500, 0x13e550, 'pad_stick_dpad_0013e500', 'hg_pad_stick_dpad.', True),
    (0x13e550, 0x13e5ac, 'pad_digital_pressure_0013e550', 'hg_pad_digital_pressure (masks 003db240).', True),
    (0x13e8f0, 0x13e980, 'pad_pressure_0013e8f0', 'hg_pad_pressure: byte to [0,1] between f12/f13 (f0).', True),
    (0x141810, 0x1418a8, 'options_to_game_00141810', 'hg_options_to_game.', True),
    (0x1418b0, 0x1418d4, 'controls_apply_001418b0', 'hg_controls_apply.', True),
    (0x141980, 0x141b08, 'new_game_state_00141980', 'hg_new_game_state: area triple (1,0,0), empty '
     'name (0011af00, format 003db280), default options and controls.', True),
    (0x147f10, 0x147f58, 'menu_clear_00147f10', 'hg_menu_clear.', True),
    (0x161cf0, 0x161dc0, 'menu_cursor_00161cf0', 'hg_menu_cursor(cell, limit): Up/Down bounded cursor.', True),
    (0x162750, 0x16334c, 'pause_menu_00162750', 'hg_pause_menu, state = mode +c (table 003db630): '
     'states 0, 1, 2, 4, 6, 7 and 0x11 reconstructed (resume, title, teardown/quit and the '
     'confirmation); the other states fail closed with a domain fault.', False),
    (0x2cf020, 0x2cf324, 'title_card_scan_002cf020', 'hg_title_card_scan: memory-card presence scan '
     '(00966500..0096651c), 1 save present, 2 no card/save; sceMc entries are bus calls.', True),
)
OVERLAY_ROWS = (
    (0xa87880, 0xa87de0, 'title_menu_00a87880', 'hg_title_menu: title presentation and menu by minor '
     '(mode +9); returns 1 on a confirmed choice (mode +11), 3 on the attract timeout. Without a '
     'save the cursor skips the Continue row.', True),
    (0xa87de0, 0xa87ecc, 'title_card_poll_00a87de0', 'hg_title_card_poll(hold): mode +b card poll; '
     'returns 1 in state 2.', True),
)
TABLES = ((0x3db1f0, 8, '00139240 fade kind (jump table)'),
          (0x3db210, 11, '0013dc30 pad port state (jump table)'),
          (0x3db240, 16, '0013e550 digital pressure masks (data)'),
          (0x3db630, 18, '00162750 pause menu state (jump table)'))


def listing(words):
    text = '\n'.join(f"{r['address']} {r['bytes']} {r['text']}" for r in words) + '\n'
    return hashlib.sha256(text.encode()).hexdigest()


def elf_row(elf, segments, entry, end, dis):
    words = []
    for va in range(entry, end, 4):
        w = word_at(elf, segments, va)
        if w is None:
            raise ValueError(f'no file data at {va:08x}')
        words.append(dict(address=f'0x{va:08x}', text=dis(va, w), bytes=w.to_bytes(4, 'little').hex()))
    return words


def overlay_row(data, entry, end, dis):
    words = []
    for va in range(entry, end, 4):
        w = struct.unpack_from('<I', data, va - OVERLAY_BASE)[0]
        words.append(dict(address=f'0x{va:08x}', text=dis(va, w), bytes=w.to_bytes(4, 'little').hex()))
    return words


def function(entry, end, name, interpretation, complete, words, snapshot, source=C):
    return dict(entry=f'0x{entry:08x}', name=name, complete=complete,
                body_ranges=[dict(start=f'0x{entry:08x}', end_exclusive=f'0x{end:08x}')],
                instructions=words, references=[], interpretation=interpretation,
                verification_status='original-byte-verified', source=source,
                original_listing_sha256=listing(words), snapshot_sha256=snapshot)


def verify(args):
    """Re-check every committed row against the original ELF and overlay bytes."""
    elf = args.elf.read_bytes()
    if hashlib.sha256(elf).hexdigest() != EXPECTED:
        raise ValueError('Wrong Japanese original executable')
    ovl = args.overlay.read_bytes()
    if hashlib.sha256(ovl).hexdigest() != OVERLAY_SHA256:
        raise ValueError('overlay is not the authenticated /AREA0/A0_B09.BIN')
    segments = elf_segments(elf)
    n = 0
    for path, read, sha in ((args.output, lambda va: word_at(elf, segments, va), EXPECTED),
                            (args.overlay_output,
                             lambda va: struct.unpack_from('<I', ovl, va - OVERLAY_BASE)[0], OVERLAY_SHA256)):
        record = json.loads(path.read_text())
        for f in record['functions']:
            if f['snapshot_sha256'] != sha or listing(f['instructions']) != f['original_listing_sha256']:
                raise ValueError(f"{f['name']}: digest mismatch")
            for ins in f['instructions']:
                if read(int(ins['address'], 16)).to_bytes(4, 'little').hex() != ins['bytes']:
                    raise ValueError(f"{f['name']}: byte mismatch at {ins['address']}")
                n += 1
    tables = json.loads(args.tables.read_text())
    for t in tables['tables']:
        base = int(t['address'], 16)
        if [f'0x{word_at(elf, segments, base + 4 * i):08x}' for i in range(len(t['words']))] != t['words']:
            raise ValueError(f"table {t['address']} mismatch")
    print(f'PASS: {n} front-end instruction words and {len(tables["tables"])} tables match the original ELF/overlay')


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--elf', type=Path, required=True)
    p.add_argument('--overlay', type=Path, required=True)
    p.add_argument('--ps2dinfo', type=Path)
    p.add_argument('--output', type=Path, default=ROOT / 'analysis/front_end/functions.json')
    p.add_argument('--overlay-output', type=Path, default=ROOT / 'analysis/front_end/overlay_functions.json')
    p.add_argument('--tables', type=Path, default=ROOT / 'analysis/front_end/tables.json')
    p.add_argument('--verify', action='store_true')
    args = p.parse_args()
    if args.verify:
        return verify(args)
    for out in (args.output, args.overlay_output, args.tables):
        if out.exists():
            raise FileExistsError(out)
    if digest(args.elf) != EXPECTED:
        raise ValueError('Wrong Japanese original executable')
    ovl = args.overlay.read_bytes()
    if hashlib.sha256(ovl).hexdigest() != OVERLAY_SHA256:
        raise ValueError('overlay is not the authenticated /AREA0/A0_B09.BIN')
    elf = args.elf.read_bytes()
    segments = elf_segments(elf)
    dis = decoder().dis
    program = dict(game='Hungry Ghosts', build_id='japan', executable='SCPS_150.46',
                   sha256=EXPECTED, processor='EE/R5900', reported_language='r5900:LE:32:default',
                   endianness='little', address_space='ram', instruction_encoding='ee-mips32',
                   load_address='0x00100000',
                   address_context='Original unrelocated file-backed EE PT_LOAD; M18 synthesized rows.')
    provenance = dict(classification='observed', observation_date='2026-11-23',
                      source='Authenticated Japanese ELF; rows decoded by the private M15 R5900 decoder.',
                      tool='local/m15/r5900dis.py', tool_version='M15', tool_sha256=digest(DECODER),
                      retrieval_method='file-backed EE PT_LOAD instruction extraction', truncated=False,
                      limitations=['Original-instruction versus C equivalence is a separate differential '
                                   '(tools/verify_front_end_differential.py) and native lifecycle run '
                                   '(tools/run_front_end_native.py).',
                                   'The text column is an analysis aid; bytes are authoritative.'])
    record = dict(schema='ps2-evidence/v1', program=program, provenance=provenance,
                  functions=[function(*r, elf_row(elf, segments, r[0], r[1], dis), EXPECTED) for r in ROWS])
    print(json.dumps(shared_module(args.ps2dinfo).verify(elf, record, require_all_bytes=True)))
    overlay = dict(schema='hg-m18-overlay-evidence/v1',
                   overlay=dict(path='/AREA0/A0_B09.BIN', resource_id='0x0a', sha256=OVERLAY_SHA256,
                                size=len(ovl), link_address=f'0x{OVERLAY_BASE:08x}'),
                   provenance={**provenance, 'source': 'Authenticated title overlay extracted from the '
                               'original disc image; rows decoded by the private M15 R5900 decoder.',
                               'retrieval_method': 'overlay file words at the link address'},
                   functions=[function(*r, overlay_row(ovl, r[0], r[1], dis), OVERLAY_SHA256)
                              for r in OVERLAY_ROWS])
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
    args.overlay_output.write_text(json.dumps(overlay, indent=2) + '\n')
    args.tables.write_text(json.dumps(dict(schema='hg-m18-jump-tables/v1', elf_sha256=EXPECTED,
                                           tables=tables), indent=2) + '\n')


if __name__ == '__main__':
    main()
