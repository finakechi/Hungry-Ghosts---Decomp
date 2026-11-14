#!/usr/bin/env python3
"""Read candidate MWo3 overlay headers; leave unverified fields uninterpreted."""
import argparse
import json
import struct
from pathlib import Path
from hg_identify import ISO, read_at, hashes


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('image',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();records=[]
    with a.image.open('rb') as fp:
        iso=ISO(fp)
        for path,rec in sorted(iso.files.items()):
            if not path.startswith('/AREA') or not path.endswith('.BIN'):continue
            header=read_at(fp,rec['lba']*2048,32)
            records.append(dict(path=path,size_bytes=rec['size_bytes'],lba=rec['lba'],
                                sha256=hashes(fp,rec['lba']*2048,rec['size_bytes'])['sha256'],magic=header[:4].decode('ascii'),
                                header_words={f'0x{n:02x}':f'0x{struct.unpack_from("<I",header,n)[0]:08x}' for n in range(4,32,4)}))
    result=dict(schema='hg-overlay-header/v1',classification='byte-verified headers; overlay semantics inferred',
                interpretation='MWo3 files share header +8 = 00a87800, corresponding to empty PT_LOAD segments. Field meanings otherwise unasserted.',records=records)
    with a.output.open('x') as fp:json.dump(result,fp,indent=2);fp.write('\n')
    print(f'{len(records)} AREA headers; MWo3/common arena: '+str(all(r['magic']=='MWo3' and r['header_words']['0x08']=='0x00a87800' for r in records)))


if __name__=='__main__':main()
