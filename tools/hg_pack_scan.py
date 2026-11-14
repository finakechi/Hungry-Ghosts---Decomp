#!/usr/bin/env python3
"""Measure PACK directory boundary and member bounds without extracting assets.

Independent implementation; hgtools' 2048/64/16 layout is a research lead.
Directory count is derived from the earliest member offset, not hardcoded.
"""
import argparse
import json
import struct
from pathlib import Path
from hg_identify import ISO, read_at, hashes


def scan(fp, base, size):
    directories, earliest, index = [], size, 0
    while index * 2048 < earliest:
        if (index + 1) * 2048 > size:
            raise ValueError('directory exceeds PACK')
        block = read_at(fp, base + index * 2048, 2048)
        ident, count, *unknown = struct.unpack_from('<9I', block)
        if ident != index or count > (2048 - 64) // 16:
            raise ValueError(f'invalid directory {index}')
        members = []
        for item in range(count):
            offset, length, flags, reserved = struct.unpack_from('<4I', block, 64 + item * 16)
            if offset + length > size:
                raise ValueError('member exceeds PACK')
            if length:
                earliest = min(earliest, offset)
            magic = read_at(fp, base + offset, min(length, 4))
            members.append(dict(index=item, offset=offset, size_bytes=length, flags=flags,
                                reserved=reserved, flk5=magic == b'FLK5'))
        directories.append(dict(index=index, count=count, header_words_2_to_8=unknown, members=members))
        index += 1
    if earliest != index * 2048:
        raise ValueError('directory/member boundary is not exact')
    for directory in directories:
        for m in directory['members']:
            if m['size_bytes'] and m['offset'] < earliest:
                raise ValueError('member overlaps directory table')
    return dict(schema='hg-pack-scan/v1', directory_count=index, table_size_bytes=earliest,
                member_count=sum(d['count'] for d in directories),
                flk5_count=sum(m['flk5'] for d in directories for m in d['members']),
                directories=directories)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('iso', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with args.iso.open('rb') as fp:
        iso = ISO(fp)
        rec = iso.files['/DATA/PACK.DAT']
        result = scan(fp, rec['lba'] * 2048, rec['size_bytes'])
        result['source'] = dict(path_on_disc='/DATA/PACK.DAT', lba=rec['lba'], size_bytes=rec['size_bytes'],
                                sha256=hashes(fp,rec['lba']*2048,rec['size_bytes'])['sha256'])
    with args.output.open('x') as fp:
        json.dump(result, fp, indent=2)
        fp.write('\n')
    print({k: v for k, v in result.items() if k != 'directories'})


if __name__ == '__main__':
    main()
