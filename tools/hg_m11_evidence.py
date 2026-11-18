#!/usr/bin/env python3
"""Build the bounded M11 static-constraint instruction evidence packet."""
import argparse
import json
from pathlib import Path
from hg_m9_evidence import select
from hg_evidence_safety import digest
from verify_resource_loader import EXPECTED, shared_module

ROWS = [
    (0x15ebc0, 'hg_player_static_constraint', 'src/resource_loader.c',
     'Reconstructed in C: four probe tiers, query-result filtering, VU0 facing test, push '
     'application and probe restart, result flags, bump timer and sound selection. Hits '
     'flagged 0x1e00 (special surfaces, 0015eaf0 path) fail closed.'),
    (0x1446a0, 'scene_segment_query', 'docs/M11.md',
     'Modeled original instructions, not reconstructed: combines static-geometry 00145740 and '
     '00267ed0 results and their 3f39a0 hit record.'),
    (0x145740, 'static_geometry_query', 'docs/M11.md',
     'Modeled, not reconstructed. For a triangle hit, 145b10..145b1c copies an unwritten '
     'fourth-vertex stack quad into 3f3a00; the M11 host guest-frame contract preserves it.'),
    (0x11ad58, 'random_next', 'docs/M11.md', 'Modeled RNG wrapper used by the observed bump branch.'),
    (0x114010, 'random_multiply', 'docs/M11.md', 'Modeled 64-bit LCG multiply (multu, mult, mult1).'),
    (0x10cca0, 'interrupt_disable', 'docs/M11.md',
     'Modeled DI helper reached by the sound request; CP0 Status is a supplied, varied input.'),
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
    for entry, name, source, interpretation in ROWS:
        path, row = select(a.exports, entry)
        record['functions'].append(dict(entry=row['entry'], name=name, complete=entry != 0x15ebc0,
            body_ranges=row['body_ranges'], instructions=row['instructions'],
            references=[{**r, 'ghidra_type': r['type'], 'type': 'call' if 'CALL' in r['type'] else 'unknown'}
                        for r in row['references_to']],
            interpretation=interpretation, verification_status='original-byte-verified', source=source,
            original_listing_sha256=row['listing_sha256'], snapshot_sha256=digest(path)))
    record['provenance'].update(observation_date='2026-11-17',
        source='Fresh read-only, no-analysis HGExport snapshots of the existing M2 Ghidra project for M11; '
               'verified against the original Japanese ELF.',
        limitations=['0015ebc0 reconstructed except the unobserved special-surface (0x1e00) branch, which fails closed.',
                     'Remaining functions are instruction evidence for modeled leaves, not reconstructions.'])
    print(json.dumps(shared_module(a.ps2dinfo).verify(a.elf.read_bytes(), record, require_all_bytes=True)))
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.output.open('x') as f: json.dump(record, f, indent=2); f.write('\n')


if __name__ == '__main__': main()
