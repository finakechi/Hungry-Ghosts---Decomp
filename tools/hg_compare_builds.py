#!/usr/bin/env python3
"""Summarize separately verified builds without applying any patches."""
import argparse
import hashlib
import json
from pathlib import Path
from hg_identify import elf32, va_to_offset


def diff(a,b,elf):
    if len(a)!=len(b):raise ValueError('different ELF lengths; explicit remapping required')
    changed=[i for i,(x,y) in enumerate(zip(a,b)) if x!=y]
    file_backed=sum(any(p['offset']<=i<p['offset']+p['filesz'] for p in elf['pt_load']) for i in changed)
    return dict(changed_bytes=len(changed),changed_aligned_words=len({i//4 for i in changed}),
                changed_bytes_in_pt_load=file_backed,changed_bytes_outside_pt_load=len(changed)-file_backed,
                changed_files=None)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('japan','english-standard','english-modern'):
        p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--manifest-dir',type=Path,default=Path('evidence/builds'))
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();data={};manifests={}
    for n in ('japan','english-standard','english-modern'):
        data[n]=getattr(args,n.replace('-','_')).read_bytes()
        manifests[n]=json.loads((args.manifest_dir/(n+'.json')).read_text())
        if hashlib.sha256(data[n]).hexdigest()!=manifests[n]['elf']['sha256']:raise ValueError('build hash mismatch')
    elf=elf32(data['japan']);comparisons=[]
    for left,right in [('japan','english-standard'),('japan','english-modern'),('english-standard','english-modern')]:
        if elf32(data[left])!=elf32(data[right]):raise ValueError('different ELF mappings; explicit review required')
        result=diff(data[left],data[right],elf)
        l={f['path']:f['sha256'] for f in manifests[left]['files']}
        r={f['path']:f['sha256'] for f in manifests[right]['files']}
        result['changed_files']=[path for path in sorted(l.keys()|r.keys()) if l.get(path)!=r.get(path)]
        comparisons.append(dict(left=left,right=right,**result))
    # Published control patch address: an entry-site lead, not a gameplay label.
    address=0x10e6d8;offset=va_to_offset(elf,address)
    result=dict(schema='hg-build-comparison/v1',build_hashes={k:manifests[k]['elf']['sha256'] for k in data},
                comparisons=comparisons,lead=dict(address=f'0x{address:08x}',file_offset=f'0x{offset:x}',
                words={k:f'0x{int.from_bytes(v[offset:offset+4],"little"):08x}' for k,v in data.items()},
                interpretation='Original entry is an RPC-style controller-library wrapper; patch hook does not by itself identify the player movement routine.'))
    with args.output.open('x') as fp:json.dump(result,fp,indent=2);fp.write('\n')
    print(json.dumps(result))


if __name__=='__main__':main()
