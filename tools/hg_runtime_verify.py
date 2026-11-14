#!/usr/bin/env python3
"""Compare a read-only PCSX2 capture to original executable/disc metadata."""
import argparse
import configparser
import hashlib
import json
import struct
from pathlib import Path
from hg_identify import elf32, va_to_offset
from hg_evidence import EXPECTED


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('capture','elf','manifest','emulator','profile','output'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();capture=json.loads(a.capture.read_text());manifest=json.loads(a.manifest.read_text())
    data=a.elf.read_bytes()
    if hashlib.sha256(data).hexdigest()!=EXPECTED or manifest['elf']['sha256']!=EXPECTED:raise ValueError('wrong original build')
    elf=elf32(data);checks=[]
    for key in ('metadata_before','metadata_after'):
        m=capture[key]
        if m['serial']!='SCPS-15046' or m['uuid'].lower()!='91aa4b28' or m['status']!=0:raise ValueError('runtime identity/status mismatch')
    samples={int(s['address'],16):s for s in capture['samples']}
    for address,size in [(0x1318f0,184),(0x1319c0,28),(0x1319e0,492),(0x131bf0,428)]:
        s=samples[address];b=bytes.fromhex(s['bytes']);offset=va_to_offset(elf,address,size)
        if len(b)!=size or b!=data[offset:offset+size]:raise ValueError('runtime instruction mismatch')
        checks.append(dict(address=f'0x{address:08x}',size_bytes=size,sha256=hashlib.sha256(b).hexdigest(),matches_original_elf=True))
    globals_data=bytes.fromhex(samples[0x3eace0]['bytes'])
    pack_lba,pack_size=struct.unpack_from('<II',globals_data,0x40)
    pack=next(f for f in manifest['files'] if f['path']=='/DATA/PACK.DAT')
    if (pack_lba,pack_size)!=(pack['lba'],pack['size_bytes']):raise ValueError('PACK lookup mismatch')
    area=bytes.fromhex(samples[0x3f3a30]['bytes'])
    area_pairs=list(struct.iter_unpack('<II',area))
    expected_pairs={(f['lba'],f['size_bytes']) for f in manifest['files'] if f['path'].startswith('/AREA')}
    if len(area_pairs)!=22 or not set(area_pairs)<=expected_pairs or set(area_pairs)!=expected_pairs:
        raise ValueError('AREA lookup mismatch')
    config=configparser.ConfigParser(interpolation=None)
    config.read(a.profile)
    settings={key:config.get('EmuCore',key) for key in ['EnablePatches','EnableCheats','EnableWideScreenPatches','EnableNoInterlacingPatches','EnablePINE']}
    if any(settings[k].lower()!='false' for k in settings if k!='EnablePINE'):raise ValueError('optional patches were enabled')
    result=dict(schema='hg-runtime-verification/v1',timestamp_utc=capture['timestamp_utc'],
                elf_sha256=EXPECTED,image_sha256=manifest['image']['sha256'],
                emulator=capture['metadata_before'],emulator_binary_sha256=hashlib.sha256(a.emulator.read_bytes()).hexdigest(),
                settings=settings,renderer=config.get('EmuCore/GS','Renderer'),
                inputs='Fast boot of original ISO; no gameplay input or save state loaded; memory cards disabled.',
                code_ranges=checks,pack_lookup=dict(lba=pack_lba,size_bytes=pack_size,matches_iso9660=True),
                area_lookup=dict(table_slots=22,distinct_disc_files=len(expected_pairs),all_lba_and_sizes_match=True),
                raw_capture_sha256=hashlib.sha256(a.capture.read_bytes()).hexdigest(),
                limitations=['Code presence and initialized file metadata verified at runtime, not function call coverage or behavioral equivalence.',
                             'Reads while running are sequential, not atomic.','No gameplay or rendering correctness asserted.'])
    with a.output.open('x') as fp:json.dump(result,fp,indent=2);fp.write('\n')
    print('Runtime: four function bodies (1132 bytes) and PACK/22 AREA lookups match original inputs')


if __name__=='__main__':main()
