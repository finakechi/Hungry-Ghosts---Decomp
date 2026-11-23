#!/usr/bin/env python3
"""Dump disassembly of ELF address ranges using the independent R5900 decoder.

Unknown/unsupported encodings (e.g. VU macro words) are rendered as raw
.word entries, never silently misreported.
"""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from r5900 import disasm_one


def _words(data):
    for i in range(0, len(data) - 3, 4):
        yield struct.unpack_from('<I', data, i)[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('elf', type=Path)
    parser.add_argument('--range', action='append', required=True,
                        metavar='START:END', help='hex address range, exclusive end')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()

    data = args.elf.read_bytes()
    phoff = struct.unpack_from('<I', data, 28)[0]
    phnum = struct.unpack_from('<H', data, 44)[0]
    loads = []
    for i in range(phnum):
        kind, offset, va, pa, filesz = struct.unpack_from('<5I', data, phoff + i * 32)
        if kind == 1:
            loads.append((va, offset, filesz))

    def read_va(addr, size):
        for va, offset, filesz in loads:
            if va <= addr and addr + size <= va + filesz:
                return data[offset + addr - va: offset + addr - va + size]
        raise ValueError(f'address {addr:#x} not file-backed')

    lines = []
    ranges = []
    for spec in args.range:
        start, end = (int(x, 16) for x in spec.split(':'))
        if end - start > 0x200000:
            raise ValueError('range too large')
        blob = read_va(start, end - start)
        for i, word in enumerate(_words(blob)):
            addr = start + i * 4
            try:
                text = disasm_one(addr, word)
            except ValueError:
                text = f'.word 0x{word:08x}'
            lines.append(f'{addr:08x}  {word:08x}  {text}')
        ranges.append({'start': f'0x{start:08x}', 'end_exclusive': f'0x{end:08x}'})
    result = {'elf_sha256': hashlib.sha256(data).hexdigest(), 'ranges': ranges}
    if args.output:
        if args.output.suffix == '.json':
            args.output.write_text(json.dumps(result, indent=1) + '\n')
        else:
            args.output.write_text('\n'.join(lines) + '\n')
    else:
        print('\n'.join(lines))


if __name__ == '__main__':
    main()
