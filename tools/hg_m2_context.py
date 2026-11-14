#!/usr/bin/env python3
"""Verify bounded origin/wake/dependency functions without promoting them to source."""
from hg_evidence_safety import select_export, digest, receipt
import argparse
import json
from pathlib import Path
from verify_resource_loader import shared_module


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True);p.add_argument('--ps2dinfo',type=Path,required=True)
    p.add_argument('--exports',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();base=json.loads(Path('analysis/resource_loader/functions.json').read_text())
    descriptions={0x121810:'Main callback phase 0 requests (kind=0,id=0); phase 1 waits for 003eacec to clear.',
        0x143240:'Main-context callback dispatch, separate from kernel resource thread execution.',
        0x1432c0:'Registers main callback and resets its context.',
        0x11df10:'Frame interrupt increments counters and wakes active normal resource thread.',
        0x11df80:'Other interrupt wakes priority-kind-100 context; normal worker is not used here.',
        0x130900:'General PACK loader dependency; selected mode 8 calls AREA read helper before directory load.',
        0x119ac8:'Memset-style library dependency, including MMI byte replication; not reconstructed here.'}
    raw=[];result=[]
    for entry,description in descriptions.items():
        selected=select_export(a.exports,entry)
        row=json.loads(selected.read_text());raw.append(row)
        calls=[]
        for ins in row['instructions']:
            word=int.from_bytes(bytes.fromhex(ins['bytes']),'little');at=int(ins['address'],16)
            if word>>26 in (2,3):
                target=((at+4)&0xf0000000)|((word&0x3ffffff)<<2)
                calls.append(dict(site=ins['address'],target=f'0x{target:08x}',kind='call' if word>>26==3 else 'tail/absolute jump'))
        result.append(dict(entry=row['entry'],interpretation=description,status='analyzed-partial-byte-checked',
                           body_ranges=row['body_ranges'],listing_sha256=row['listing_sha256'],direct_edges=calls,
                           instruction_rows=len(row['instructions'])))
    base['functions']=[dict(entry=r['entry'],instructions=r['instructions']) for r in raw]
    verified=shared_module(a.ps2dinfo).verify(a.elf.read_bytes(),base,require_all_bytes=True)
    packet=dict(schema='hg-resource-context/v1',elf_sha256=base['program']['sha256'],functions=result,verified=verified,
                callback_edges=[dict(source='0x0011d830',registers_callback_via='0x001432c0',callback='0x00121810',dispatch='0x00143240'),
                                dict(request='0x00130150',scheduler='0x00143360',entry_argument='0x00130220',descriptor_field='+0x04',
                                     kernel_create='0x00103d60',kernel_start='0x00103d80'),
                                dict(wake_handler='0x0011df10',active_field='0x003f8e30',thread_id_field='0x003f8e34',wake_call='0x00104e10')],
                limitations='Callback relationships are derived from arguments/descriptor writes; live sampling is not a PC trace. Context functions are not counted as new reconstructions.')
    packet['binding']=receipt(Path(__file__).resolve().parents[1],{'elf':a.elf,'shared_verifier':a.ps2dinfo/'tools/verify_elf_evidence.py',**{f'export_{e:08x}':select_export(a.exports,e) for e in descriptions}},'original-bytes',['hg_m2_context.py'],[packet['limitations']])
    with a.output.open('x') as fp:json.dump(packet,fp,indent=2);fp.write('\n')
    print(f'PASS: {len(raw)} context functions original-byte checked')


if __name__=='__main__':main()
