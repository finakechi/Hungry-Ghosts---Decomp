#!/usr/bin/env python3
"""Read investigated M1/M2 instruction ranges and use the shared byte verifier."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
from hg_pine_read import Pine
from verify_resource_loader import shared_module


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--socket',type=Path,required=True);p.add_argument('--elf',type=Path,required=True)
    p.add_argument('--ps2dinfo',type=Path,required=True);p.add_argument('--record',type=Path,action='append',required=True)
    p.add_argument('--private-output',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();packet=copy.deepcopy(json.loads(a.record[0].read_text()))
    for path in a.record[1:]:packet['functions'].extend(json.loads(path.read_text())['functions'])
    client=Pine(a.socket)
    try:
        before=client.metadata()
        if before['serial']!='SCPS-15046' or before['uuid'].lower()!='91aa4b28':raise ValueError('Wrong runtime')
        for function in packet['functions']:
            blocks=[]
            for span in function['body_ranges']:
                start,end=int(span['start'],16),int(span['end_exclusive'],16)
                blocks.append((start,client.read(start,end-start)))
            for ins in function['instructions']:
                address=int(ins['address'],16)
                for start,data in blocks:
                    if start<=address<start+len(data):ins['bytes']=data[address-start:address-start+4].hex();break
                else:raise ValueError('Instruction outside captured body')
        after=client.metadata()
    finally:client.close()
    if before!=after or before['status']!=0:raise ValueError('Runtime changed identity/status during capture')
    checked=shared_module(a.ps2dinfo).verify(a.elf.read_bytes(),packet,require_all_bytes=True)
    with a.private_output.open('x') as fp:json.dump(packet,fp,indent=2);fp.write('\n')
    result=dict(schema='hg-loading-runtime-code/v1',metadata=before,functions=len(packet['functions']),
                verified=checked,capture_sha256=hashlib.sha256(a.private_output.read_bytes()).hexdigest(),
                limitation='Code-presence comparison; actual loading activity is separately recorded in runtime-loading.json.')
    with a.output.open('x') as fp:json.dump(result,fp,indent=2);fp.write('\n')
    print(f'PASS: {len(packet["functions"])} runtime function bodies match {checked["instructions_verified"]} original instruction words')


if __name__=='__main__':main()
