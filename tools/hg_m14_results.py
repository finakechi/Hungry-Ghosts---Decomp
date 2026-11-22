#!/usr/bin/env python3
"""Summarize validated private M14 receipts without copying their RAM images."""
import argparse
import json
from pathlib import Path

from hg_evidence_safety import digest
from verify_resource_loader import EXPECTED

ROOT=Path(__file__).resolve().parents[1]
COUNTERS=tuple(range(10164,10186))
SERVICES=[('0x198f60','0x0'),('0x17f6d0','0x3f9e30'),('0x17f2d0','0x0')]


def load(path):return json.loads(path.read_text())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--trace-dir',type=Path,required=True)
    p.add_argument('--native',type=Path,required=True)
    p.add_argument('--continuous',type=Path,required=True)
    p.add_argument('--counter',type=Path,required=True)
    p.add_argument('--contracts',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    native=load(args.native);continuous=load(args.continuous);counter=load(args.counter)
    contracts=load(args.contracts)
    receipts=(native,continuous,counter,contracts)
    # Every receipt must have been produced from the current tracked sources.
    for receipt in receipts:
        for name,value in receipt['verification_sources'].items():
            if digest(ROOT/name)!=value:raise ValueError(f'Receipt built from a different {name}')
        if receipt.get('smoke_max_frames') is not None:raise ValueError('Smoke receipt is not a milestone receipt')
    source_sha=digest(ROOT/'src/resource_loader.c')
    if any(r['elf_sha256']!=EXPECTED for r in receipts):
        raise ValueError('ELF identity mismatch')
    if any(r['result']!='passed' for r in receipts):
        raise ValueError('A required verifier failed')
    if len(native['frames'])!=22 or len(continuous['frames'])!=22 or continuous['frame_count']!=22 or \
            len(contracts['frames'])!=22 or contracts.get('contract_frames') is not None:
        raise ValueError('Missing continuous frame')
    if len(native['phase_handoffs'])!=21 or not all(x['phase_matches'] for x in native['phase_handoffs']):
        raise ValueError('Missing phase handoff')
    if len(native['negative_controls'])!=9 or not all(x['detected'] for x in native['negative_controls']):
        raise ValueError('Native negative control escaped')
    if continuous['schema'] != 'hg-player-continuous/v4' or len(continuous['negative_controls'])!=12 or any(
            x['result']!=('clean' if x['kind']=='null-control' else 'rejected')
            for x in continuous['negative_controls']):
        raise ValueError('Continuous negative control escaped')
    handoff_control = next((row for row in continuous['negative_controls']
                            if row['control'] == 'omitted-packet-bank-switch'), None)
    if not handoff_control or handoff_control['frame'] != 10165 or \
            handoff_control['first_divergent_stage'] != 'render-handoff' or \
            handoff_control['detail']['window']['diagnosis']['kind'] != 'carried-input-difference':
        raise ValueError('Render handoff control did not reproduce the carried-input defect')
    frames=[]
    for number,n,c in zip(COUNTERS,native['frames'],continuous['frames']):
        path=args.trace_dir/f'original-window-{number}-v3.json'
        t=load(path)
        if n['counter']!=number or c['counter']!=number or t['elf_sha256']!=EXPECTED:
            raise ValueError(f'Frame identity mismatch {number}')
        if t['execution']!='reached-dispatch' or t['first_stop_pc']!='0x158560' or \
           t['ram_diff_nonstack_count'] or t['scratch_diff_count'] or t['instruction_identity_mismatches']:
            raise ValueError(f'Original window failed {number}')
        if [(x['pc'],x['a0']) for x in t['service_entries']]!=SERVICES:
            raise ValueError(f'Caller arguments/order mismatch {number}')
        if n['result']!='passed' or n['ram_difference_count'] or n['scratch_difference_count'] or \
                n['window']['result']!='passed' or n['oracle']['full_ram_differences'] or \
                n['oracle']['scratch_differences']:
            raise ValueError(f'C window failed {number}')
        k=contracts['frames'][number-COUNTERS[0]]
        if k['counter']!=number or k['contract_failures'] or k['poisoned_differential']['result']!='passed':
            raise ValueError(f'Register contract or poisoned differential failed {number}')
        stage={name:dict(result=row['result'],guarded_ram_count=row.get('guarded_ram_count'),
                         guarded_scratch_count=row.get('guarded_scratch_count'),
                         window_absolute_outputs=row.get('absolute_outputs'),
                         window_scratch_outputs=row.get('scratch_outputs'))
               for name,row in c['stages'].items()}
        if not {'pre-dispatch', 'pre-dispatch-window', 'render-handoff'} <= stage.keys() or \
                c['stages']['pre-dispatch-window']['oracle_capture_differences'] != dict(ram=0, scratch=0) or \
                any(x['result']!='passed' for x in stage.values()):
            raise ValueError(f'Continuous checkpoint failed {number}')
        frames.append(dict(counter=number,original_trace_sha256=digest(path),
                           original_steps=t['steps'],original_executed_addresses=t['executed_count'],
                           original_nonstack_difference_count=t['ram_diff_nonstack_count'],
                           original_scratch_difference_count=t['scratch_diff_count'],
                           original_instruction_mismatches=len(t['instruction_identity_mismatches']),
                           native_ram_difference_count=n['ram_difference_count'],
                           native_scratch_difference_count=n['scratch_difference_count'],
                           native_dma_difference_count=n['dma_difference_count'],
                           native_excluded_stack_difference_count=n['excluded_stack_difference_count'],
                           oracle_full_ram_difference_count=n['oracle']['full_ram_differences'],
                           window_write_set=dict(absolute=n['window']['absolute_outputs'],
                                                 scratch=n['window']['scratch_outputs'],
                                                 streams=[s['written_offsets'] for s in n['window']['streams']]),
                           poisoned_differential=k['poisoned_differential'],
                           phase=n['sprite_phase_0039e98c'],continuous_stages=stage))
    out=dict(schema='hg-m14-results/v1',result='passed',elf_sha256=EXPECTED,
             packet_sha256=digest(ROOT/'analysis/pre_dispatch/functions.json'),
             source_sha256=source_sha,
             build_sha256={name:r['library_sha256'] for name,r in
                           zip(('native','continuous','counter','contracts'),receipts)},
             receipts={str(path.name):digest(path) for path in (args.native,args.continuous,args.counter,args.contracts)},
             comparison_domain=dict(native_ram='0x00000000..0x01ffffff minus captured libpad DMA buffers and the '
                                               'guest stack below the verified 0012f8a8 sp 0x01fffc70',
                                    excluded_stack='0x01fe0000..0x01fffc6f: transient callee frames (counted)',
                                    native_scratch='complete 0x70000000..0x70003fff',
                                    window='exact original-instruction write set: absolute outputs, scratch and '
                                           'four ctx packet streams compared relative to their cursors',
                                    continuous='M13 domains, 12 sprite phases, and the pre-dispatch window write set'),
             contract_summary=contracts['contract_summary'],
             frames=frames,phase_handoffs=native['phase_handoffs'],
             native_negative_controls=[dict(name=x['name'],detected=x['detected'],
                                            first_ram_differences=x['detail'].get('first_ram_differences',[])[:4],
                                            first_scratch_differences=x['detail'].get('first_scratch_differences',[])[:4])
                                       for x in native['negative_controls']],
             continuous_negative_controls=[dict(name=x['control'],result=x['result'],
                                                first_divergent_stage=x.get('first_divergent_stage'))
                                           for x in continuous['negative_controls']],
             counter_guard=counter['cases'],
             limitations=['The host models the observed alternating GS field from the starting state. '
                          'General GS timing and DMA submission remain outside the reproduced handoff.',
                          'Live registers were not captured. gp and the caller a0 values are verified; '
                          'sp=0x01fffc70 is inferred from an exact full-RAM match of the original window; '
                          'other entry registers are zero in the oracle.',
                          'Capture RAM and scratchpad reads were sequential rather than atomic.',
                          'Inter-frame render, actor, kernel and tail-service state remains external.'])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(out,indent=2)+'\n')
    print('passed',len(frames),'frames',len(out['native_negative_controls'])+len(out['continuous_negative_controls']),'controls')


if __name__=='__main__':main()
