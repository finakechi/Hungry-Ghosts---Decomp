#!/usr/bin/env python3
"""Read-only ISO9660/ELF32 inventory. No third-party code or game data required."""
import argparse
import hashlib
import json
import re
import struct
import zlib
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

SECTOR = 2048


def read_at(fp, offset, size):
    if offset < 0 or size < 0:
        raise ValueError('negative range')
    fp.seek(offset)
    data = fp.read(size)
    if len(data) != size:
        raise ValueError(f'truncated range at {offset:#x}, expected {size}')
    return data


def both32(data, offset):
    le = struct.unpack_from('<I', data, offset)[0]
    if le != struct.unpack_from('>I', data, offset + 4)[0]:
        raise ValueError('ISO both-endian mismatch')
    return le


class ISO:
    """Single-extent ISO9660 logical files; rejects unsupported interleaving."""
    def __init__(self, fp):
        self.fp = fp
        fp.seek(0, 2)
        self.size = fp.tell()
        pvd = None
        for sector in range(16, min(self.size // SECTOR, 256)):
            descriptor = read_at(fp, sector * SECTOR, SECTOR)
            if descriptor[1:7] != b'CD001\x01':
                raise ValueError('invalid ISO descriptor')
            if descriptor[0] == 1:
                pvd = descriptor
                break
            if descriptor[0] == 255:
                break
        if pvd is None:
            raise ValueError('no primary ISO descriptor')
        if struct.unpack_from('<H', pvd, 128)[0] != SECTOR or struct.unpack_from('>H', pvd, 130)[0] != SECTOR:
            raise ValueError('unsupported logical block size')
        self.volume_blocks = both32(pvd, 80)
        if self.volume_blocks * SECTOR > self.size:
            raise ValueError('volume exceeds image')
        self.volume_id = pvd[40:72].decode('ascii').rstrip(' \0')
        self.files = {}
        self._walk(self._record(pvd[156:190]), '', set())

    def _record(self, raw):
        if len(raw) < 34 or raw[0] != len(raw) or 33 + raw[32] > len(raw):
            raise ValueError('invalid directory record')
        if raw[1] or raw[25] & 0x80 or raw[26] or raw[27]:
            raise ValueError('extended, multi-extent or interleaved record unsupported')
        lba, size = both32(raw, 2), both32(raw, 10)
        if lba * SECTOR + size > self.volume_blocks * SECTOR:
            raise ValueError('file extent exceeds volume')
        return {'lba': lba, 'size_bytes': size, 'directory': bool(raw[25] & 2),
                'name': raw[33:33 + raw[32]]}

    def _walk(self, directory, prefix, ancestors):
        key = (directory['lba'], directory['size_bytes'])
        if key in ancestors or len(ancestors) > 32:
            raise ValueError('cyclic/deep ISO directory')
        if directory['size_bytes'] > 16 * 1024 * 1024:
            raise ValueError('oversized directory')
        data = read_at(self.fp, key[0] * SECTOR, key[1])
        at = 0
        while at < len(data):
            length = data[at]
            if not length:
                at = (at // SECTOR + 1) * SECTOR
                continue
            if at + length > len(data) or at % SECTOR + length > SECTOR:
                raise ValueError('directory record crosses boundary')
            record = self._record(data[at:at + length])
            at += length
            if record['name'] in (b'\0', b'\1'):
                continue
            name = record.pop('name').decode('ascii').split(';')[0]
            if name in ('', '.', '..') or '/' in name or '\\' in name:
                raise ValueError('invalid ISO filename')
            path = prefix + '/' + name
            if record['directory']:
                self._walk(record, path, ancestors | {key})
            else:
                if path in self.files:
                    raise ValueError('duplicate ISO path')
                self.files[path] = record

    def read(self, path):
        rec = self.files[path]
        return read_at(self.fp, rec['lba'] * SECTOR, rec['size_bytes'])


def hashes(fp, offset, size):
    fp.seek(offset)
    hs = {name: hashlib.new(name) for name in ('sha256', 'sha1', 'md5')}
    crc, remaining = 0, size
    while remaining:
        block = fp.read(min(4 * 1024 * 1024, remaining))
        if not block:
            raise ValueError('truncated hash input')
        for h in hs.values():
            h.update(block)
        crc = zlib.crc32(block, crc)
        remaining -= len(block)
    return {**{name: h.hexdigest() for name, h in hs.items()}, 'crc32': f'{crc:08x}'}


def elf32(data):
    if len(data) < 52 or data[:7] != b'\x7fELF\x01\x01\x01':
        raise ValueError('expected little-endian ELF32 v1')
    typ, machine, version, entry, phoff, shoff, flags, ehsize, phsize, phnum, shsize, shnum, shstr = struct.unpack_from('<HHIIIIIHHHHHH', data, 16)
    if machine != 8 or version != 1 or ehsize != 52 or phsize != 32 or phoff + phnum * phsize > len(data):
        raise ValueError('invalid MIPS ELF header/program table')
    loads = []
    for i in range(phnum):
        kind, offset, va, pa, filesz, memsz, fl, align = struct.unpack_from('<8I', data, phoff + i * phsize)
        if offset + filesz > len(data):
            raise ValueError('segment outside ELF')
        if kind == 1:
            if filesz > memsz or va + memsz > 2**32:
                raise ValueError('invalid PT_LOAD range')
            loads.append(dict(offset=offset, vaddr=va, paddr=pa, filesz=filesz,
                              memsz=memsz, flags=fl, align=align))
    if typ == 2 and not any(p['vaddr'] <= entry < p['vaddr'] + p['memsz'] for p in loads):
        raise ValueError('entry outside load segments')
    return dict(type=typ, machine=machine, flags=f'0x{flags:08x}', entry=f'0x{entry:08x}',
                pt_load=loads, section_count=shnum, section_table_offset=shoff)


def va_to_offset(elf, address, size=4):
    matches = [p['offset'] + address - p['vaddr'] for p in elf['pt_load']
               if p['vaddr'] <= address and address + size <= p['vaddr'] + p['filesz']]
    if len(matches) != 1:
        raise ValueError('address not in exactly one file-backed PT_LOAD')
    return matches[0]


def identify(image, build, extract=None):
    with image.open('rb') as fp:
        iso = ISO(fp)
        cnf = iso.read('/SYSTEM.CNF')
        text = cnf.decode('ascii')
        match = re.search(r'(?im)^BOOT2\s*=\s*cdrom0:\\([^;\r\n]+);1\s*$', text)
        if not match:
            raise ValueError('unsupported/missing BOOT2')
        boot = '/' + match[1].replace('\\', '/')
        serial = re.fullmatch(r'/([A-Z]{4})_(\d{3})\.(\d{2})', boot)
        executable = iso.read(boot)
        elf = elf32(executable)
        xor = 0
        for at in range(0, len(executable) - 3, 4):
            xor ^= struct.unpack_from('<I', executable, at)[0]
        inventory = []
        for path, rec in sorted(iso.files.items()):
            item = {'path': path, 'lba': rec['lba'], 'size_bytes': rec['size_bytes']}
            item.update(hashes(fp, rec['lba'] * SECTOR, rec['size_bytes']))
            inventory.append(item)
        result = dict(schema='hg-build/v1', build=build, observation_date=datetime.now(ZoneInfo('America/New_York')).date().isoformat(),
                      evidence_class='byte-verified',
                      image={'size_bytes': iso.size, **hashes(fp, 0, iso.size)},
                      iso9660={'volume_id': iso.volume_id, 'volume_blocks': iso.volume_blocks},
                      system_cnf={'text': text, 'sha256': hashlib.sha256(cnf).hexdigest()},
                      serial=f'{serial[1]}-{serial[2]}{serial[3]}' if serial else None,
                      boot_path=boot, elf={**elf, 'sha256': hashlib.sha256(executable).hexdigest(),
                                           'size_bytes': len(executable), 'word_xor_crc': f'{xor:08X}'},
                      files=inventory)
        if extract:
            extract.mkdir(parents=True, exist_ok=True)
            with (extract / boot.lstrip('/')).open('xb') as out:
                out.write(executable)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--build', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--extract-boot', type=Path)
    args = parser.parse_args()
    result = identify(args.image, args.build, args.extract_boot)
    with args.output.open('x') as fp:
        json.dump(result, fp, indent=2)
        fp.write('\n')
    print(json.dumps({'build': args.build, 'serial': result['serial'], 'elf': result['elf']}))


if __name__ == '__main__':
    main()
