#!/usr/bin/env python3
"""Substantiate loading activity with original PACK bytes and observed states."""
import argparse
import hashlib
import json
import struct
import configparser
from pathlib import Path
from hg_identify import ISO,read_at
from hg_evidence_safety import digest, receipt


def u32(data,offset):return struct.unpack_from('<I',data,offset)[0]
def u16(data,offset):return struct.unpack_from('<H',data,offset)[0]


def verify(raw,post,image,profile):
    for meta in [raw['metadata_before'],raw['metadata_after'],post['metadata_before'],post['metadata_after']]:
        if meta['serial']!='SCPS-15046' or meta['uuid'].lower()!='91aa4b28' or meta['status']!=0:
            raise ValueError('Runtime identity/status mismatch')
    manifest=json.loads((Path(__file__).resolve().parents[1]/'evidence/builds/japan.json').read_text())
    if digest(image)!=manifest['image']['sha256']:raise ValueError('Original image hash mismatch')
    with image.open('rb') as fp:
        iso=ISO(fp);rec=iso.files['/DATA/PACK.DAT'];base=rec['lba']*2048
        header=read_at(fp,base,2048);bulk_offset=u32(header,16);bulk_size=u32(header,20)
        members=[struct.unpack_from('<4I',header,64+i*16) for i in range(u32(header,4))]
        first_payload=read_at(fp,base+bulk_offset,16)
    samples={int(s['address'],16):bytes.fromhex(s['bytes']) for s in post['samples']}
    if samples[0x3f3b00]!=header:raise ValueError('Loaded PACK header does not match original directory 0')
    expected_handles={flags:0xb00000+offset-bulk_offset for offset,size,flags,reserved in members}
    for ident,address in expected_handles.items():
        if u32(samples[0x3f4300],ident*4)!=address:raise ValueError('Runtime resource handle mapping mismatch')
    # The original fixup dependency changes this FLK5 header's +8 word after
    # loading. Do not mistake transformed resource bytes for a failed read.
    if samples[0xb00000][:8]!=first_payload[:8]:raise ValueError('First payload magic/member count differs')
    expected_end=(0xb00000+bulk_size+0x7ff)&0xfffff800
    highwater=samples[0x3f4d3c]
    for off in (0,4,8):
        if u32(highwater,off)!=expected_end:raise ValueError('Arena high-water mismatch')
    for off in (16,20,24):
        if u32(highwater,off)!=expected_end+0x2f8000:raise ValueError('Derived arena pointer mismatch')
    if u32(highwater,32)!=expected_end+0x348000:raise ValueError('Secondary arena pointer mismatch')
    phases=set();special=set();requests={};transitions=[];previous=None;active_times=[];done_times=[]
    for row in raw['samples']:
        task=bytes.fromhex(row['task']);g=bytes.fromhex(row['read_and_globals'])
        active,kind=u16(task,0x30),u16(task,0x32)
        if active==1:
            if kind!=0 or u32(task,4)!=0x130220 or u32(task,0x44)!=0:raise ValueError('Observed worker is not bootstrap directory 0')
            phases.add(task[0x39]);special.add(task[0x3b]);active_times.append(row['elapsed'])
            if u32(g,0x18)==0x3ead20:
                req=(u32(g,0x1c),u32(g,0x20),u32(g,0x24))
                requests[req]=requests.get(req,0)+1
        if active_times and active==0 and g[0xc]==0:done_times.append(row['elapsed'])
        key=(active,kind,u32(task,0x44),task[0x39],task[0x3b],g[0xc],g[0x10])
        if key!=previous:
            transitions.append(dict(elapsed_seconds=row['elapsed'],active=active,kind=kind,directory=u32(task,0x44),
                                    loader_phase=task[0x39],special_phase=task[0x3b],request_busy=g[0xc],disc_state=g[0x10]))
            previous=key
    if not {1,2,3,4,5}<=phases or not {3,7,11}<=special or not done_times:
        raise ValueError('Required loading and completion transitions were not observed')
    expected_requests=[(0x3f3b00,0,2048),(0xb00000,bulk_offset,bulk_size)]
    if any(requests.get(req,0)<2 for req in expected_requests):raise ValueError('Directory and bulk reads not repeatedly observed')
    task=samples[0x3f8e00];g=samples[0x3eace0]
    if u16(task,0x30)!=0 or g[0xc]!=0 or task[0x39]!=5:raise ValueError('Final worker completion state mismatch')
    c=configparser.ConfigParser(interpolation=None);c.read(profile)
    keys=['EnablePatches','EnableCheats','EnableWideScreenPatches','EnableNoInterlacingPatches']
    settings={key:c.getboolean('EmuCore',key) for key in keys}
    if any(settings.values()):raise ValueError('Optional patches enabled')
    return dict(schema='hg-loading-runtime-verification/v1',metadata=raw['metadata_before'],timestamp_utc=raw['timestamp_utc'],
                samples=len(raw['samples']),requested_hz=raw['requested_hz'],transitions=transitions,
                active_interval_seconds=[min(active_times),max(active_times)],completion_first_observed_seconds=min(done_times),
                observed_bootstrap_phases=sorted(phases),observed_special_phases=sorted(special),
                requests=[dict(destination=f'0x{dst:08x}',byte_offset=off,byte_size=size,observations=requests[(dst,off,size)],
                               expected_start_lba=rec['lba']+off//2048) for dst,off,size in expected_requests],
                original_pack=dict(lba=rec['lba'],size_bytes=rec['size_bytes'],directory=0,member_count=len(members),
                                   header_sha256=hashlib.sha256(header).hexdigest(),header_matches=True),
                handles=[dict(index=i,address=f'0x{a:08x}',matches=True) for i,a in sorted(expected_handles.items())],
                expected_arena_end=f'0x{expected_end:08x}',arena_pointers_match=True,payload_magic_and_count_match=True,
                flk5_header_word_08=dict(original=u32(first_payload,8),observed=u32(samples[0xb00000],8),
                    interpretation='Observed post-read mutation; full asset-fixup semantics are not reconstructed.'),
                settings=settings,inputs='Saved capture reanalysis; no fresh run or input-history claim.',
                raw_capture_sha256=hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest(),
                limitations=['Sequential live sampling is not atomic; short states can be missed.',
                             'Observed loading states and loaded data support the bootstrap operation; no PC/breakpoint call trace.',
                             'No runtime claim for other worker jobs, AREA operation, all error paths, or mocked asset-fixup semantics.'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('raw','post','image','profile','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--emulator',type=Path,required=True,help='Executable used for the saved capture')
    a=p.parse_args();result=verify(json.loads(a.raw.read_text()),json.loads(a.post.read_text()),a.image,a.profile)
    raw=json.loads(a.raw.read_text());post=json.loads(a.post.read_text())
    result['binding']=receipt(Path(__file__).resolve().parents[1],{k:getattr(a,k) for k in ('raw','post','image','profile','emulator')},'saved-runtime',['verify_loading_runtime.py'],result['limitations'],dict(before=raw['metadata_before'],after=raw['metadata_after'],post_before=post['metadata_before'],post_after=post['metadata_after'],settings=result['settings']))
    with a.output.open('x') as fp:json.dump(result,fp,indent=2);fp.write('\n')
    print(f'PASS: active directory/bulk reads, loader phases, completion, {len(result["handles"])} handles and arena pointers match original PACK')


if __name__=='__main__':main()
