#!/usr/bin/env python3
"""Summarize validated private M16 receipts without copying RAM or scratch images.

Inputs are the owner-local receipts of one source state: the continuous v6
verifier with negative controls, and the M14 native window, register-contract
and counter-guard verifiers re-run on the same tracked sources. The summary
keeps counts, hashes and control outcomes only.
"""
import argparse
import json
from pathlib import Path

from hg_evidence_safety import digest
from verify_resource_loader import EXPECTED

ROOT = Path(__file__).resolve().parents[1]
COUNTERS = tuple(range(10164, 10186))
PACKETS = (ROOT / 'analysis/render_nodes/functions.json', ROOT / 'analysis/model_draw/functions.json',
           ROOT / 'analysis/late_frame/functions.json', ROOT / 'analysis/late_frame/area.json')
M16_STAGES = ('camera-chain', 'model-draw', 'render-node-tail', 'late-frame', 'rng-continuity')
M16_CONTROLS = {
    'corrupt-render-node-carry', 'null-capture-restoration-substate-1',
    'anchor-offset-sign', 'billboard-translation-sign', 'missing-anchor-row', 'anchor-texture-order',
    'quad-emission-order', 'unconditional-anchor-draw', 'camera-return-projection-store',
    'shadow-scale-sign', 'light-unnormalized', 'missing-light-w', 'light-column-order',
    'late-frame-unconditional-scratch-store', 'late-frame-omitted-area-call', 'late-frame-extra-rng',
    'late-frame-mark-after-area', 'late-frame-wrong-order', 'div1-quotient-plus-one',
    'late-frame-missing-frame-tick', 'late-frame-double-frame-tick'}


def load(path): return json.loads(path.read_text())


def ranges(offsets):
    values = sorted(int(a, 16) - 0x70000000 for a in offsets)
    rows, start, prev = [], None, None
    for v in values:
        if start is None: start = prev = v
        elif v == prev + 1: prev = v
        else: rows.append((start, prev)); start = prev = v
    if start is not None: rows.append((start, prev))
    return [f'{0x70000000 + a:08x}' + ('' if a == b else f'..{0x70000000 + b:08x}') for a, b in rows]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--continuous', type=Path, required=True)
    p.add_argument('--native', type=Path, required=True)
    p.add_argument('--contracts', type=Path, required=True)
    p.add_argument('--counter', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists(): raise FileExistsError(args.output)
    continuous, native, contracts, counter = (load(x) for x in (args.continuous, args.native, args.contracts, args.counter))
    receipts = dict(continuous=continuous, native=native, contracts=contracts, counter=counter)
    for label, receipt in receipts.items():
        for name, value in receipt['verification_sources'].items():
            if digest(ROOT / name) != value: raise ValueError(f'{label} receipt built from a different {name}')
        if receipt.get('smoke_max_frames') is not None: raise ValueError(f'{label} is a smoke receipt')
        if receipt['elf_sha256'] != EXPECTED: raise ValueError('ELF identity mismatch')
        if receipt['result'] != 'passed': raise ValueError(f'{label} verifier failed')
    if continuous['schema'] != 'hg-player-continuous/v6' or continuous['frame_count'] != 22 or \
            [f['counter'] for f in continuous['frames']] != list(COUNTERS):
        raise ValueError('Continuous receipt is not the 22-frame v6 sequence')
    if continuous['node_unexplained'] != 0: raise ValueError('Unexplained render-node inter-frame bytes')
    if len(native['frames']) != 22 or len(contracts['frames']) != 22: raise ValueError('Missing M14 frame')
    if not all(x['detected'] for x in native['negative_controls']): raise ValueError('Native control escaped')
    controls = continuous.get('negative_controls') or []
    if not controls or any(c['result'] != ('clean' if c['kind'] == 'null-control' else 'rejected') for c in controls):
        raise ValueError('Continuous negative control escaped or errored')
    missing = M16_CONTROLS - {c['control'] for c in controls}
    if missing: raise ValueError(f'Missing M16 controls {sorted(missing)}')
    frames = []
    for fr in continuous['frames']:
        stages = fr['stages']
        if any(s.get('result') not in ('passed', 'skipped') for s in stages.values()):
            raise ValueError(f'{fr["counter"]} stage failed')
        if stages['rng-continuity']['result'] == 'skipped' and fr['counter'] != COUNTERS[-1]:
            raise ValueError(f'{fr["counter"]} rng-continuity skipped without a final-frame reason')
        for name in M16_STAGES:
            if name not in stages: raise ValueError(f'{fr["counter"]} missing {name}')
        tail, chain, model, late = (stages[n] for n in ('render-node-tail', 'camera-chain', 'model-draw', 'late-frame'))
        rng = stages['rng-continuity']
        frames.append(dict(
            counter=fr['counter'], phase=fr['phase'], stages=len(stages),
            camera_chain_compared_writes=chain['compared_writes'], camera_chain_oracle_steps=chain['oracle_steps'],
            model_draw_compared_writes=model['compared_writes'], model_draw_oracle_steps=model['oracle_steps'],
            render_node_compared_writes=tail['compared_writes'], render_node_oracle_steps=tail['oracle_steps'],
            late_frame_compared_writes=late['compared_writes'], late_frame_oracle_steps=late['oracle_steps'],
            late_frame_rng_calls=late['oracle_rng_calls'],
            interpreted_rng_draws=rng['interpreted_draws'], native_rng_draws=rng['native_draws'],
            min_native_scratch_compared=min(s['scratch_compared'] for s in stages.values() if 'scratch_compared' in s),
            camera_return_scratch_excluded=stages['camera-return']['external_scratch_count'],
            identity_mismatches=sum(len(stages[n]['identity_mismatches']) for n in M16_STAGES[:4])))
    result = dict(
        schema='hg-m16-results/v1', result='passed', elf_sha256=EXPECTED,
        packet_sha256={str(x.relative_to(ROOT)): digest(x) for x in PACKETS},
        source_sha256=digest(ROOT / 'src/resource_loader.c'),
        header_sha256=digest(ROOT / 'src/resource_loader.h'),
        library_sha256={k: r.get('library_sha256') for k, r in receipts.items()},
        receipts={k: digest(path) for k, path in (('continuous', args.continuous), ('native', args.native),
                                                   ('contracts', args.contracts), ('counter', args.counter))},
        comparison_domain=dict(
            same_input='camera chain 0012c790, player model draw 0012bc70, render-node list 00143150 and the '
                       'late-frame render service 0013ce80 (its AREA1 body executes authentic original '
                       'instructions): reconstruction vs original instructions on the identical carried input; '
                       'write sets, values, all RAM below the guest stack 01fe0000 and the full scratchpad',
            rng='every native 0011ad58 draw between camera return and the next coordinator entry is produced '
                'by the connected execution (0012bc70 + 00143150 + 0013ce80 trees); no external draw-count '
                'replay remains',
            native='M13/M14 stages: guarded RAM domains and every scratch byte except inter-frame writes '
                   'of unreconstructed coordinator services (enumerated below), excluded only until reconstructed '
                   'code rewrites them',
            inter_frame_external_scratch=ranges(continuous['inter_frame_external_scratch'])),
        rng_native_draws=continuous['rng_total'],
        area_sha256=continuous['area_sha256'], area_base=continuous['area_base'],
        frames=frames,
        negative_controls=[dict(control=c['control'], kind=c['kind'], frame=c['frame'], result=c['result'],
                                stage=c.get('first_divergent_stage')) for c in controls],
        m14_regression=dict(
            native_frames=len(native['frames']), native_controls_detected=sum(1 for x in native['negative_controls'] if x['detected']),
            native_controls=len(native['negative_controls']),
            contract_frames=len(contracts['frames']),
            counter_result=counter['result']),
    )
    with args.output.open('x') as out:
        json.dump(result, out, indent=1); out.write('\n')
    print('PASS: M16 results', len(frames), 'frames', len(controls), 'controls')


if __name__ == '__main__':
    main()
