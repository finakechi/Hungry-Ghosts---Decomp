#!/usr/bin/env python3
"""Build the bounded M13 frame-advance instruction evidence packet.

Ghidra 12.1.4 was not available on MainPC for this session. The three routine rows are
synthesized from the authenticated original Japanese ELF: every instruction word is read
from the file-backed EE PT_LOAD and decoded by the project's independent R5900 decoder
(the same decode path used by the M13 analysis). PS2DInfo's verify_elf_evidence with
--require-all-bytes validates every word against the ELF, and the project verifier
checks bytes and JAL targets. Function boundaries were established from the surrounding
code (prologue, epilogue jr ra with delay slot, and the next routine's entry), matching
the M13 inter-frame evidence.
"""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path
from hg_evidence_safety import digest
from verify_resource_loader import EXPECTED, shared_module

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'local/m13'))
import r5900dump as D


def elf_segments(elf):
    e_phoff = struct.unpack_from('<I', elf, 0x1c)[0]
    e_phentsize = struct.unpack_from('<H', elf, 0x2a)[0]
    e_phnum = struct.unpack_from('<H', elf, 0x2c)[0]
    segments = []
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        p_type, p_offset, p_vaddr, _, p_filesz, *_ = struct.unpack_from('<8I', elf, off)
        if p_type == 1:
            segments.append((p_vaddr, p_offset, p_filesz))
    return segments


def word_at(elf, segments, va):
    for v, o, s in segments:
        if v <= va < v + s:
            off = o + (va - v)
            return struct.unpack_from('<I', elf, off)[0]
    return None


def synth_row(elf, segments, entry, end_exclusive):
    words = []
    for va in range(entry, end_exclusive, 4):
        w = word_at(elf, segments, va)
        if w is None:
            raise ValueError(f'no file data at {va:08x}')
        words.append(dict(address=f'0x{va:08x}', text=D.fmt(w, va), bytes=w.to_bytes(4, 'little').hex()))
    listing = '\n'.join(f"{r['address']} {r['bytes']} {r['text']}" for r in words) + '\n'
    return dict(body_ranges=[dict(start=f'0x{entry:08x}', end_exclusive=f'0x{end_exclusive:08x}')],
                instructions=words, listing_sha256=hashlib.sha256(listing.encode()).hexdigest())


ROWS = [
    (0x12f850, 0x12f948, 'frame_coordinator_0012f850', True,
     'Reconstructed for the observed corridor path: 3fa190 += 1 with the negative-guard reset, the '
     '0031bb80 edge sync, halfword 3f9ff8 = 0 and the guarded (byte 0x2f78(gp)) 46e118 += 1, then the '
     'M10 dispatcher call 00158560 and the M9 camera call 0012c790. The remaining coordinator service '
     'calls (00198f60, 0017f6d0, 0017f2d0, 0013b6e0, 001430a0, 00143100, 0012bc70, 0018c520, 00143150, '
     '001431a0, 0025c170, 00182480, 0013ce80, 00137890, 00189be0) are declared external host seams in '
     'the M13 composition; their memory effects are outside every reconstructed domain in the observed '
     'frames (inter-frame write evidence). The sibling 0012f950 is not observed (selector byte 3f9f3e = 0).'),
    (0x137890, 0x137ab4, 'engine_frame_routine_00137890', False,
     'Listed for its 001379f0..00137aa4 body, reconstructed in C as hg_counter_advance: guarded 46d930 += 1 '
     'and 46e110 += 1 with the -1 clamp, and the 108000-frame mega-counter path (46d908 with the '
     'negative-guard reset, 46d930 = 0). The earlier bodies (3fa03b timer, service calls) are engine work '
     'outside the reconstructed domains and fail closed; the mega-counter path is differential-only (never '
     'reached in the observed frames, 46d930 about 1200).'),
    (0x31bb80, 0x31bbd8, 'frame_edge_sync_0031bb80', True,
     'Reconstructed in C as hg_frame_edge_sync: the four per-frame edge counters are copied to their '
     'previous slots and cleared (3fa040 -> 3fa050; 3f9e34/3f9e38/3f9e3c +0x210 -> +0x220).'),
]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf', type=Path, required=True)
    p.add_argument('--ps2dinfo', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if digest(a.elf) != EXPECTED: raise ValueError('Wrong original ELF')
    elf = a.elf.read_bytes()
    segments = elf_segments(elf)
    record = dict(schema='ps2-evidence/v1',
                  program=dict(game='Hungry Ghosts', build_id='japan', executable='SCPS_150.46',
                               sha256=EXPECTED, processor='EE/R5900', reported_language='r5900:LE:32:default',
                               endianness='little', address_space='ram',
                               address_context='Original unrelocated file-backed EE PT_LOAD; M13 synthesized rows.',
                               instruction_encoding='ee-mips32', load_address='0x00100000'),
                  provenance=dict(classification='observed', observation_date='2026-11-20',
                                  source=('Synthesized from the authenticated original Japanese ELF with the '
                                          'project independent R5900 decoder (local/m13/r5900dump.py); Ghidra 12.1.4 '
                                          'was not installed on MainPC for this session. Every word is validated '
                                          'against the ELF by PS2DInfo --require-all-bytes and the project verifier.'),
                                  tool='local/m13/r5900dump.py', tool_version='M13',
                                  retrieval_method='file-backed EE PT_LOAD instruction extraction',
                                  truncated=False,
                                  limitations=[
                                      '0012f850 reconstructed for the observed prefix through 0012f8a4 only; the tail service calls are declared external.',
                                      '00137890 reconstructed for the 001379f0..00137aa4 counter body only; earlier bodies fail closed.',
                                      '0031bb80 reconstructed completely.']),
                  functions=[])
    for entry, end, name, complete, interpretation in ROWS:
        row = synth_row(elf, segments, entry, end)
        record['functions'].append(dict(entry=f'0x{entry:08x}', name=name, complete=complete,
                                        body_ranges=row['body_ranges'], instructions=row['instructions'],
                                        references=[], interpretation=interpretation,
                                        verification_status='original-byte-verified',
                                        source='src/resource_loader.c',
                                        original_listing_sha256=row['listing_sha256'],
                                        snapshot_sha256=digest(a.elf)))
    print(json.dumps(shared_module(a.ps2dinfo).verify(elf, record, require_all_bytes=True)))
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.output.open('x') as f:
        json.dump(record, f, indent=2); f.write('\n')


if __name__ == '__main__':
    main()
