#!/usr/bin/env python3
"""Deliberately break disposable evidence/C/captures and require rejection."""
import argparse
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from verify_resource_loader import ROOT,shared_module
from verify_loading_runtime import verify


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('elf','ps2dinfo','raw','post','image','profile','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();folder=ROOT/'local/m2/negative-controls';folder.mkdir(exist_ok=True)
    results=[];packet=json.loads((ROOT/'analysis/resource_loader/functions.json').read_text())
    ins=packet['functions'][0]['instructions'][0];word=bytearray.fromhex(ins['bytes']);word[0]^=1;ins['bytes']=word.hex()
    shared=shared_module(a.ps2dinfo)
    try:shared.verify(a.elf.read_bytes(),packet,require_all_bytes=True)
    except shared.EvidenceError as exc:
        if 'byte mismatch' not in str(exc):raise
        results.append(dict(control='changed original-byte row',detected=True,oracle='PS2DInfo byte mismatch'))
    else:raise AssertionError('Byte negative control was not detected')
    source=(ROOT/'src/resource_loader.c').read_text()
    mutations=[('busy_gate','if(r16(e,task+0x30)!=0)return;','if(r16(e,task+0x30)==0)return;'),
               ('directory_sector_shift','r32(e,task+0x44)<<11','r32(e,task+0x44)<<10'),
               ('completion_busy_clear','w8(e,0x3eacec,0);hg_resource_finish(e);','w8(e,0x3eacec,1);hg_resource_finish(e);'),
               ('member_handle_offset','(r32(e,cursor)-r32(e,header+0x10))','(r32(e,cursor)+r32(e,header+0x10))'),
               ('arena_alignment','(a+0x7ffu)&0xfffff800u','(a+0x7ffu)&0xfffff000u'),
               ('area_cache_argument','call(e,0x1041e0,2,0,0,0);','call(e,0x1041e0,1,0,0,0);')]
    for name,old,new in mutations:
        if source.count(old)!=1:raise ValueError('Mutation target not unique: '+name)
        path=folder/(name+'.c');path.write_text(source.replace(old,new))
        command=[sys.executable,str(ROOT/'tools/verify_resource_loader.py'),'--elf',str(a.elf),
                 '--ps2dinfo',str(a.ps2dinfo),'--source',str(path),'--output',str(folder/(name+'.json'))]
        run=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=90)
        log=run.stdout+run.stderr;(folder/(name+'.log')).write_text(log)
        if run.returncode==0 or 'AssertionError:' not in log or not any(marker in log for marker in ['external call trace differs','memory differs','return ']):
            raise AssertionError(f'{name}: expected differential assertion, got {run.returncode}')
        results.append(dict(control=name,detected=True,returncode=run.returncode,oracle='Original-instruction differential assertion',
                            log_sha256=hashlib.sha256(log.encode()).hexdigest()))
    raw=json.loads(a.raw.read_text());post=json.loads(a.post.read_text())
    for name in ('runtime_handle','runtime_missing_activity'):
        r,postcopy=copy.deepcopy(raw),copy.deepcopy(post)
        if name=='runtime_handle':
            sample=next(s for s in postcopy['samples'] if int(s['address'],16)==0x3f4300)
            b=bytearray.fromhex(sample['bytes']);b[0]^=1;sample['bytes']=b.hex()
        else:
            for row in r['samples']:
                b=bytearray.fromhex(row['task']);b[0x30:0x32]=b'\0\0';row['task']=b.hex()
        try:verify(r,postcopy,a.image,a.profile)
        except ValueError as exc:
            expected='handle mapping mismatch' if name=='runtime_handle' else 'transitions were not observed'
            if expected not in str(exc):raise
            results.append(dict(control=name,detected=True,oracle=str(exc)))
        else:raise AssertionError(name+' was not detected')
    result=dict(schema='hg-negative-controls/v1',controls=results,count=len(results),result='passed',
                source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                safety='Only disposable source/evidence/capture copies were mutated. Original media, source and databases were unchanged.')
    with a.output.open('x') as fp:json.dump(result,fp,indent=2);fp.write('\n')
    print(f'PASS: all {len(results)} deliberate defects detected')


if __name__=='__main__':main()
