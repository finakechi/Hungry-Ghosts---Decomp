#!/usr/bin/env python3
"""Assemble M2's game-owned packet; delegate structural/ELF checks to PS2DInfo."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from resource_harness import ENTRIES,NAMES
from verify_resource_loader import EXPECTED,shared_module


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True);p.add_argument('--ps2dinfo',type=Path,required=True)
    p.add_argument('--exports',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();data=a.elf.read_bytes();functions=[]
    for key,entry in ENTRIES.items():
        candidates=sorted(a.exports.glob(f'*/{entry:08x}.json'))
        if not candidates:raise ValueError(f'Missing actual export {entry:08x}')
        row=json.loads(candidates[0].read_text())
        functions.append(dict(entry=row['entry'],name='hg_'+NAMES[key],complete=True,body_ranges=row['body_ranges'],
                              instructions=row['instructions'],references=[{**r,'ghidra_type':r['type'],'type':'call' if 'CALL' in r['type'] else 'unknown'} for r in row['references_to']],
                              interpretation='M2 reconstruction; exact behavior and dependencies in analysis/resource_loader/README.md.',
                              verification_status='reconstructed-model-differential-tested',source='src/resource_loader.c',
                              original_listing_sha256=row['listing_sha256']))
    record=dict(schema='ps2-evidence/v1',program=dict(game='Hungry Ghosts',build_id='japan',executable='SCPS_150.46',
                sha256=EXPECTED,processor='EE/R5900',reported_language='r5900:LE:32:default',endianness='little',
                address_space='ram',address_context='Original unrelocated file-backed EE PT_LOAD; named M2 project copied from preserved M1 working project.',
                instruction_encoding='ee-mips32',load_address='0x00100000'),
                provenance=dict(classification='observed',observation_date='2026-11-14',source='Actual local HGExport Java output, original ELF checked by pinned PS2DInfo verifier.',
                    tool='Ghidra HGExport.java',tool_version='12.1.4',retrieval_method='headless Ghidra API on isolated M2 copy',truncated=False,
                    limitations=['Names are analyst assigned.','Kernel threads, CDVD and asset fixups are dependencies in differential tests.',
                                 'Full other worker loaders are preserved as external calls.','Host memory bridge/resumable helper are not original ABI.']),
                functions=functions)
    verification=shared_module(a.ps2dinfo).verify(data,record,require_all_bytes=True)
    with a.output.open('x') as fp:json.dump(record,fp,indent=2);fp.write('\n')
    print(json.dumps(verification))
    print('New functions:',len(functions),'bytes:',sum(len(f['instructions'])*4 for f in functions))


if __name__=='__main__':main()
