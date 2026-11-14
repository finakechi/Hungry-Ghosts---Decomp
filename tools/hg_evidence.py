#!/usr/bin/env python3
"""Build attributable metadata/evidence from actual Ghidra exports and retail ELF.

Public output contains a small investigated code group, not whole-game listings.
Refuses a wrong ELF and verifies every exported instruction against PT_LOAD bytes.
"""
import argparse
import hashlib
import json
from pathlib import Path
from hg_identify import elf32, va_to_offset

EXPECTED='a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61'
NAMES={0x1318f0:'hg_read_poll',0x1319c0:'hg_read_setup',0x1319e0:'hg_read_continue',0x131bf0:'hg_read_start'}
MEANING={0x1318f0:'Poll singleton read state, start requests and restart after errors.',
         0x1319c0:'Install request arguments and clear the first two state words.',
         0x1319e0:'Poll completion/error or issue the next sector chunk.',
         0x131bf0:'Calculate sector range and issue the first read when permitted.'}


def checked_function(row, data, elf):
    for ins in row['instructions']:
        at=int(ins['address'],16);b=bytes.fromhex(ins['bytes'])
        off=va_to_offset(elf,at,len(b))
        if data[off:off+len(b)]!=b:raise ValueError(f'ELF mismatch {at:08x}')
        # Independently verify immediate JAL destinations in Ghidra's text.
        word=int.from_bytes(b,'little')
        if word>>26==3:
            target=((at+4)&0xf0000000)|((word&0x3ffffff)<<2)
            if f'0x{target:08x}' not in ins['text']:raise ValueError('JAL target disagreement')
    return row


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True)
    p.add_argument('--export',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--inventory-output',type=Path,required=True)
    p.add_argument('--map-output',type=Path,required=True)
    p.add_argument('--context',type=Path,help='Optional byte-verified caller context from hg_context.py')
    a=p.parse_args();data=a.elf.read_bytes()
    if hashlib.sha256(data).hexdigest()!=EXPECTED:raise ValueError('wrong ELF')
    elf=elf32(data);functions=[]
    for entry,name in NAMES.items():
        raw=checked_function(json.loads((a.export/f'{entry:08x}.json').read_text()),data,elf)
        functions.append(dict(entry=f'0x{entry:08x}',name=name,complete=True,
                              body_ranges=raw['body_ranges'],instructions=raw['instructions'],
                              references=[{**r,'type':'call' if 'CALL' in r['type'] else 'unknown'} for r in raw['references_to']],
                              interpretation=MEANING[entry],
                              verification_status='reconstructed-model-differential-tested',
                              byte_sha256=raw['listing_sha256'],
                              source='src/disc_read.c',
                              questions=['External CDVD/IOP implementations and concurrent behavior not reconstructed.']))
    record=dict(schema='ps2-evidence/v1',program=dict(game='Hungry Ghosts',build_id='japan',executable='SCPS_150.46',
                    sha256=EXPECTED,processor='EE/R5900',reported_language='r5900:LE:32:default',endianness='little',
                    address_space='ram',address_context='Unrelocated EE virtual addresses; first PT_LOAD offset 0x800, VA 0x100000.',
                    instruction_encoding='ee-mips32',load_address='0x00100000'),
                provenance=dict(classification='observed',observation_date='2026-11-14',tool='Ghidra HGExport.java',tool_version='12.1.4',
                    source='Local HGExport JSON from isolated Japanese working database; verified against original ELF by tools/hg_evidence.py.',
                    retrieval_method='headless Java Ghidra API',truncated=False,
                    limitations=['Analyst-assigned names, not retail symbols.','Original Ghidra pseudocode retained in ignored local exports.',
                                 'Differential test uses mocked CDVD and a restricted interpreter.','No full-game equivalence or matching claim.']),
                functions=functions,verification=dict(local_byte_check='passed',local_differential='evidence/disc-read-differential.json'))
    a.output.parent.mkdir(parents=True,exist_ok=True)
    for path,obj in [(a.output,record)]:
        with path.open('x') as fp:json.dump(obj,fp,indent=2);fp.write('\n')
    inventory=json.loads((a.export/'functions.json').read_text())
    context={}
    if a.context:
        ctx=json.loads(a.context.read_text())
        if ctx['elf_sha256']!=EXPECTED:raise ValueError('wrong context build')
        context={f['entry']:f for f in ctx['functions']}
    for f in inventory:
        entry=int(f['entry'],16)
        if entry in NAMES:
            f['analyst_name']=NAMES[entry];f['status']='reconstructed-model-differential-tested'
        elif f['entry'] in context:
            c=context[f['entry']]
            if c['body_ranges']!=f['body_ranges']:raise ValueError('context range mismatch')
            f['status']='analyzed-partial-byte-checked'
            f['analyst_description']=c['description']
        f['byte_matching']='not-tested'
        f['gameplay_runtime_verification']='not-tested'
    obj=dict(schema='hg-function-inventory/v1',elf_sha256=EXPECTED,ghidra_version='12.1.4',
             discovery='Automatic Ghidra analysis of boot ELF after RAM-map correction; not an exhaustive function boundary oracle.',
             count=len(inventory),status_counts={s:sum(f['status']==s for f in inventory) for s in sorted({f['status'] for f in inventory})},functions=inventory)
    with a.inventory_output.open('x') as fp:json.dump(obj,fp,indent=2);fp.write('\n')
    mapdata=json.loads((a.export/'map.json').read_text())
    mapdata['limitations']=['Extension-generated mirror/BIOS maps are retained as tool output and are not validated hardware maps.',
                           'Address spaces distinguish nonloaded ELF metadata overlays from EE ram.',
                           'AREA overlays are not imported into this baseline map.']
    with a.map_output.open('x') as fp:json.dump(mapdata,fp,indent=2);fp.write('\n')
    print(f'ELF-verified {sum(len(f["instructions"]) for f in functions)} instructions in {len(functions)} functions; inventory {len(inventory)}')


if __name__=='__main__':main()
