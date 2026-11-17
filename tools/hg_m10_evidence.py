#!/usr/bin/env python3
"""Build the bounded M10 dispatcher/coordinator instruction evidence packet."""
import argparse
import json
from pathlib import Path
from hg_m9_evidence import select
from hg_evidence_safety import digest
from verify_resource_loader import EXPECTED, shared_module


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True)
    p.add_argument('--ps2dinfo',type=Path,required=True)
    p.add_argument('--exports',type=Path,nargs='+',required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if digest(a.elf)!=EXPECTED:raise ValueError('Wrong original ELF')
    record=json.loads(Path('analysis/player_turn_camera/functions.json').read_text())
    record['functions']=[]
    for entry,name,interpretation in [
        (0x158560,'hg_player_dispatch','State-1 active path prefix and common tail reconstructed; other states fail closed. Includes post-update +428 clear and 16-byte current-to-previous-position copy.'),
        (0x12f850,'gameplay_coordinator_observed','Survey only, not reconstructed. Calls 158560 at 12f8c4 before 12c790 at 12f8cc; surrounding calls remain outside M10.')]:
        path,row=select(a.exports,entry)
        record['functions'].append(dict(entry=row['entry'],name=name,complete=False,
            body_ranges=row['body_ranges'],instructions=row['instructions'],
            references=[{**r,'ghidra_type':r['type'],'type':'call' if 'CALL' in r['type'] else 'unknown'} for r in row['references_to']],
            interpretation=interpretation,verification_status='original-byte-verified',
            source='src/resource_loader.c' if entry==0x158560 else 'docs/M10.md',
            original_listing_sha256=row['listing_sha256'],snapshot_sha256=digest(path)))
    record['provenance'].update(source='Existing read-only M8 HGExport snapshots reused for M10; verified against the original Japanese ELF.',
        limitations=['158560 reconstructed only on the observed state-1 active path.',
                      '12f850 is instruction evidence for ordering and a bounded survey, not a reconstructed coordinator.'])
    print(json.dumps(shared_module(a.ps2dinfo).verify(a.elf.read_bytes(),record,require_all_bytes=True)))
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x') as f:json.dump(record,f,indent=2);f.write('\n')

if __name__=='__main__':main()
