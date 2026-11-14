#!/usr/bin/env python3
"""Verify the analyzed callers and retain hashes/ranges/direct-call addresses."""
import argparse
import hashlib
import json
from pathlib import Path
from hg_identify import elf32
from hg_evidence import EXPECTED,checked_function

ENTRIES={0x100008:'startup',0x11d830:'main-loop candidate',0x11db30:'IOP initialization',
         0x12fbf0:'disc file lookup',0x130900:'PACK directory loader candidate',
         0x131810:'AREA read path',0x131dd0:'AREA post-read helper',0x10e6d8:'controller RPC-style hook lead'}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True);p.add_argument('--export-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();data=a.elf.read_bytes()
    if hashlib.sha256(data).hexdigest()!=EXPECTED:raise ValueError('wrong original ELF')
    elf=elf32(data);records=[]
    for entry,description in ENTRIES.items():
        paths=sorted(a.export_root.glob(f'*/{entry:08x}.json'))
        if not paths:raise ValueError(f'missing export {entry:08x}')
        row=checked_function(json.loads(paths[0].read_text()),data,elf);calls=[]
        for ins in row['instructions']:
            word=int.from_bytes(bytes.fromhex(ins['bytes']),'little');at=int(ins['address'],16)
            if word>>26==3:
                calls.append(dict(site=ins['address'],target=f'0x{((at+4)&0xf0000000)|((word&0x3ffffff)<<2):08x}'))
        records.append(dict(entry=f'0x{entry:08x}',description=description,status='analyzed-partial-not-reconstructed',
                            body_ranges=row['body_ranges'],listing_sha256=row['listing_sha256'],
                            byte_verified_instructions=len(row['instructions']),direct_calls=calls))
    result=dict(schema='hg-caller-context/v1',elf_sha256=EXPECTED,functions=records,
                limitations='Original listings and pseudocode remain in ignored local exports; partial interpretation is not full semantic verification.')
    with a.output.open('x') as fp:json.dump(result,fp,indent=2);fp.write('\n')
    print(f'Original-byte verified {len(records)} analyzed context functions')

if __name__=='__main__':main()
