#!/usr/bin/env python3
"""Extract the M16 coordinator late-frame render service's original words.

Rows cover the service 0013ce80 (called at 0012f924) and the grid-layout leaf
001537f0 (the observed div1 site at 00153844). Bytes come from the file-backed
EE PT_LOAD; the text column is an analysis aid from the private local/m15 R5900
decoder. With --area, the AREA1-resident render body 0xa8b800..0xa8bf0c is
additionally emitted as a raw AREA record (not an ELF PT_LOAD), byte-verified
against the authenticated A1_B00.BIN read from the original image and
structure-validated with the pinned PS2DInfo validator.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from hg_evidence_safety import digest
from hg_m13_evidence import elf_segments, word_at
from hg_m15_evidence import decoder
from verify_area_lifecycle import AREA_BASE, AREA_PATH, AREA_SHA256, load_area1
from verify_resource_loader import EXPECTED, shared_module

DECODER = Path(__file__).resolve().parent.parent / 'local/m15/r5900dis.py'

ROWS = (
    (0x13ce80, 0x13cfc4, 'late_frame_service_0013ce80',
     'Reconstructed as hg_late_frame_service. Reads the three state bytes 003fa1b0/b1/b2 and '
     'dispatches the AREA1 render body: state (1,0,0) calls 0xa8b800 with f12 = 0x415f78d5, '
     'f13 = 0x461c4000, f14 = 0xc61c4000, f15 = 0x461c4000 (mov.s f15,f13), f16 = 0x40400000; '
     'state (1,0,1) calls 0xa8b800 with f12 = 0x412f78d5, f13 = 0x433e0000, f14 = 0, '
     'f15 = 0x43530000, f16 = 0x41000000; state (0,5,0) calls 0xa8bab0 with f12 = 0x41e80000, '
     'f13 = 0. Every other state returns without effects. Only (1,0,0) is observed in the '
     '10164..10185 corridor; the other two calling states fail closed in C.'),
    (0x1537f0, 0x153950, 'late_frame_grid_layout_001537f0',
     'Interpreter-backed leaf of the 0013ce80 area body. Builds a 0x10-stride grid of cell '
     'positions at [gp+3090]: +8/+c = (width / a0) with the same quotient computed through '
     'both multiply/divide pipelines (div at 00153818, div1 at 00153844, the M16 interpreter '
     'extension), then per-cell (x, y) = (row*stride - size, col*stride - size) clipped at '
     '0. Executed as original instructions in the connected pipeline.'),
    (0x121bf4, 0x121c08, 'main_loop_frame_tick_00121bf4',
     'The main-loop frame tick reconstructed at the end of the connected frame: '
     'lw v1, 0x2f80(gp); addiu v1, v1, 1; sw v1, 0x2f80(gp). Executed between dispatch and '
     'the next coordinator-entry; the identical sequence appears at 0012d208..0012d218. '
     'The AREA1 render body reads the cell for its three-draw condition.'),
    (0x12d208, 0x12d21c, 'main_loop_frame_tick_0012d208',
     'The corridor main-loop dispatcher copy of the frame tick (identical to '
     '00121bf4..00121c04).'),
)

AREA_ROW = (0xa8b800, 0xa8bf0c, 'late_frame_render_body_00a8b800',
            'The AREA1-resident render body dispatched by 0013ce80 (state (1,0,0); 0xa8bab0 is '
            'an interior entry for state (0,5,0)). Builds the matrix 70000780..7bf from '
            '003eb2b0 with a clamped row 3, mirrors 70000b90..bcf, copies both to 00963a40/90, '
            'emits packet records on the render-context streams +0x550/+0x540, lays out a grid '
            'through 001537f0/00154100 into the 0053cd50.. region, and draws the shared LCG '
            '0011ad58 once, or three times when [gp+2f80] % (draw % 180 + 40) == 0 '
            '(frame 10178). Executed as original instructions; identity-checked against the '
            'authenticated AREA1/A1_B00.BIN.')


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


def area_row(raw, entry, end, dis):
    words = []
    for va in range(entry, end, 4):
        offset = va - AREA_BASE
        w = int.from_bytes(raw[offset:offset + 4], 'little')
        words.append(dict(address=f'0x{va:08x}', text=dis(va, w), bytes=w.to_bytes(4, 'little').hex()))
    listing = '\n'.join(f"{r['address']} {r['bytes']} {r['text']}" for r in words) + '\n'
    return dict(body_ranges=[dict(start=f'0x{entry:08x}', end_exclusive=f'0x{end:08x}')],
                instructions=words, listing_sha256=hashlib.sha256(listing.encode()).hexdigest())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf', type=Path, required=True)
    p.add_argument('--ps2dinfo', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--area-output', type=Path, default=None,
                   help='also emit the raw AREA body record here')
    p.add_argument('--image', type=Path, default=None,
                   help='authenticated original image (required with --area-output)')
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
                               address_context='Original unrelocated file-backed EE PT_LOAD; M16 synthesized rows.'),
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
    for entry, end, name, interpretation in ROWS:
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
    if args.area_output:
        if args.area_output.exists(): raise FileExistsError(args.area_output)
        if args.image is None: raise ValueError('--area-output requires --image')
        raw = load_area1(args.image)
        entry, end, name, interpretation = AREA_ROW
        r = area_row(raw, entry, end, dis)
        packet = dict(schema='ps2-evidence/v1', program=dict(game='Hungry Ghosts',
            build_id='japan-area1-a1-b00', executable=None, sha256=AREA_SHA256,
            processor='EE/R5900', reported_language='r5900:LE:32:default',
            endianness='little', instruction_encoding='ee-mips32', address_space='ram',
            load_address=f'0x{AREA_BASE:08x}',
            address_context='Raw AREA1/A1_B00.BIN mapped at independently measured retail destination; not an ELF PT_LOAD.',
            raw_resource=dict(iso_path=AREA_PATH, lba=1402368, size_bytes=30720,
                              source_elf_sha256=EXPECTED)),
            provenance=dict(classification='observed', observation_date='2026-11-22',
                source='Read-only original-image AREA1/A1_B00.BIN bytes; M16 synthesized row.',
                tool='local/m15/r5900dis.py', tool_version='M15', tool_sha256=digest(DECODER),
                retrieval_method='Authenticated original image read at the recorded extent',
                truncated=False, limitations=[
                    'Raw file mapping is checked directly; ELF-only verifier does not apply to this row.',
                    'An instruction match alone does not prove retail control reached the mapped entry.']),
            functions=[])
        packet['functions'].append(dict(entry=f'0x{entry:08x}', name=name, complete=False,
            body_ranges=r['body_ranges'], instructions=r['instructions'], references=[],
            interpretation=interpretation, verification_status='original-byte-verified',
            source='src/resource_loader.c', original_listing_sha256=r['listing_sha256'],
            snapshot_sha256=AREA_SHA256))
        import importlib.util
        pins = json.loads((Path(__file__).resolve().parent.parent / 'evidence/m2/shared-tools.json').read_text())
        validator = args.ps2dinfo / 'tools/validate_evidence.py'
        if digest(validator) != pins['tools']['validate_evidence.py']:
            raise ValueError('Shared structural validator hash mismatch')
        spec = importlib.util.spec_from_file_location('ps2_area_structure', validator)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        errors = module.validate(packet)
        if errors: raise ValueError('Invalid raw AREA evidence structure: ' + str(errors))
        if hashlib.sha256(raw).hexdigest() != AREA_SHA256:
            raise ValueError('Selected AREA file digest mismatch')
        count = 0
        for function in packet['functions']:
            for instruction in function['instructions']:
                address = int(instruction['address'], 16)
                encoded = bytes.fromhex(instruction['bytes'])
                offset = address - AREA_BASE
                if len(encoded) != 4 or offset < 0 or offset + 4 > len(raw) or raw[offset:offset + 4] != encoded:
                    raise ValueError(f'Raw AREA instruction byte mismatch at {address:08x}')
                count += 1
        print(json.dumps(dict(schema='hg-area-raw-byte-check/v1', result='passed',
            raw_sha256=AREA_SHA256, instructions_verified=count, instructions_unknown=0)))
        args.area_output.parent.mkdir(parents=True, exist_ok=True)
        args.area_output.write_text(json.dumps(packet, indent=2) + '\n')


if __name__ == '__main__':
    main()
