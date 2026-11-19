#!/usr/bin/env python3
"""Build the bounded M12 grounded/floor instruction evidence packet."""
import argparse
import json
from pathlib import Path
from hg_m9_evidence import select
from hg_evidence_safety import digest
from verify_resource_loader import EXPECTED, shared_module

ROWS = [
    (0x15fc10, 'hg_player_grounded', 'src/resource_loader.c', True,
     'Reconstructed in C: vertical speed +430 gravity step (0.08, or 0.03 while +4ad), -2.0 floor, '
     'y += speed, 0015e420 floor routine, speed cleared on a floor result.'),
    (0x15e420, 'hg_player_floor', 'src/resource_loader.c', False,
     'Reconstructed in C for floor hits: probe origin (position - 2 y), +483/3f9ff2 resets, state 2 -> 1, '
     'ground flag +2c3, surface bits +47c, +47e/+480/+481/+483 from the hit record, whole-quad snap to '
     'the 3f39a0 hit. No-floor, hazard (5/6/7), special-surface (0x1e00) and +47e == 3 spawn paths fail closed.'),
    (0x145630, 'floor_segment_query', 'docs/M12.md', True,
     'Modeled original instructions, not reconstructed: segment from origin + f12 (6.0) y down to the origin '
     'through 00144820 (mode 0x2c0 | 1); maintains +2c1/+248 moving-floor fields.'),
    (0x144820, 'scene_segment_query_mode', 'docs/M12.md', True,
     'Modeled original instructions, not reconstructed: static-geometry and object segment query used by '
     '00145630 (and by the unsupported 0015e420 paths).'),
]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf', type=Path, required=True)
    p.add_argument('--ps2dinfo', type=Path, required=True)
    p.add_argument('--exports', type=Path, nargs='+', required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if digest(a.elf) != EXPECTED: raise ValueError('Wrong original ELF')
    record = json.loads(Path('analysis/player_turn_camera/functions.json').read_text())
    record['functions'] = []
    for entry, name, source, complete, interpretation in ROWS:
        path, row = select(a.exports, entry)
        record['functions'].append(dict(entry=row['entry'], name=name, complete=complete,
            body_ranges=row['body_ranges'], instructions=row['instructions'],
            references=[{**r, 'ghidra_type': r['type'], 'type': 'call' if 'CALL' in r['type'] else 'unknown'}
                        for r in row['references_to']],
            interpretation=interpretation, verification_status='original-byte-verified', source=source,
            original_listing_sha256=row['listing_sha256'], snapshot_sha256=digest(path)))
    record['provenance'].update(observation_date='2026-11-18',
        source='Fresh read-only, no-analysis HGExport snapshots of the existing M2 Ghidra project for M12; '
               'verified against the original Japanese ELF.',
        limitations=['0015fc10 reconstructed completely; 0015e420 reconstructed for floor hits only, other paths fail closed.',
                     '00145630 and 00144820 are instruction evidence for modeled leaves, not reconstructions.'])
    print(json.dumps(shared_module(a.ps2dinfo).verify(a.elf.read_bytes(), record, require_all_bytes=True)))
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.output.open('x') as f: json.dump(record, f, indent=2); f.write('\n')


if __name__ == '__main__': main()
