#!/usr/bin/env python3
"""M12 C 0015fc10/0015e420 on the independent M11 retail end-wall frames 4278-4284.

Runs the M12 composition (C constraint, C grounded/floor) from each M11 dispatcher-entry
capture with calculated player/camera structures carried forward, and compares every M11
checkpoint at or after the floor step (history copy, camera entry, camera return) plus the
constraint stages, which now precede C 0015fc10. Expected captures are comparison inputs only.
"""
import argparse
import gc
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'tools')]
import verify_player_connected_runtime as V
import verify_player_constraint_runtime as R
from verify_player_turn_runtime import Captures, compare
from verify_resource_loader import EXPECTED, shared_module
from resource_harness import library
from hg_evidence_safety import digest


class M11FrameDriver(R.ConstraintDriver):
    """M11 stage hooks keyed to the 0x70-byte 0015ebc0 frame only (M12 adds 0x20/0x30 frames)."""
    def external(self, pc, a=0, b=0, c=0, d=0):
        if pc == V.GUEST_FRAME and a != 0x70: return V.ConnectedRuntimeDriver.external(self, pc, a, b, c, d)
        return super().external(pc, a, b, c, d)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf', type=Path, required=True); p.add_argument('--ps2dinfo', type=Path, required=True)
    p.add_argument('--capture-dir', type=Path, default=ROOT / 'local/m11/captures'); p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.output.exists() or a.output.with_suffix('.so').exists(): raise FileExistsError('Fresh output required')
    if digest(a.elf) != EXPECTED: raise ValueError('Wrong original ELF')
    obs = R.observation(); caps = Captures(a.capture_dir); caps.elfdata = a.elf.read_bytes()
    caps.segments, _ = shared_module(a.ps2dinfo).segments_from_elf(caps.elfdata)
    R.ConstraintDriver = M11FrameDriver   # R.execute constructs the driver by this name
    lib = library(output=a.output.with_suffix('.so')); prior = None; stages = []; handoffs = []
    for fr in obs['frames']:
        for row in fr['captures'].values():
            if digest(caps.path(row['name'])) != row['sha256']: raise ValueError('Observation/capture digest mismatch')
        entry = caps.load(fr['captures']['dispatch-entry']['name'])
        if prior is not None:
            diff = [hex(x + i) for x, n in R.CONTINUITY for i in range(n) if prior.get(x + i, 1) != entry.get(x + i, 1)]
            handoffs.append(dict(frame=fr['counter'], result='passed' if not diff else 'failed', differences=diff[:32]))
        d = R.execute(lib, caps, fr, R.STATUS_VALUES[0], carry=prior, function='hg_player_grounded_camera_fixed')
        pairs = [('constraint-entry', d.stage.get('constraint-entry')), ('constraint-return', d.stage.get('constraint-return')),
                 ('history-copy', d.history), ('camera-entry', d.camera_entry), ('camera-return', d.mem)]
        if 'bump-sound-call' in fr['captures']: pairs.append(('bump-sound-call', d.stage.get('bump-sound-call')))
        for label, actual in pairs:
            row = fr['captures'][label]; r = compare(actual, caps.load(row['name']))
            stages.append(dict(frame=fr['counter'], stage=label, expected=row['name'], expected_sha256=row['sha256'], **r))
        if '0x145630' not in {c['entry'] for c in d.modeled_calls} or '0x15fc10' in {c['entry'] for c in d.modeled_calls}:
            raise AssertionError('M12 C grounded/floor path not exercised')
        print(fr['counter'], 'PASSED' if all(s['result'] == 'passed' for s in stages if s['frame'] == fr['counter']) else 'FAILED', flush=True)
        prior = d.mem
        for k in list(caps.cache): del caps.cache[k]
        del d; gc.collect()
    ok = all(s['result'] == 'passed' for s in stages + handoffs)
    out = dict(schema='hg-player-grounded-m11-frames/v1', result='passed' if ok else 'failed', elf_sha256=EXPECTED,
               observation_sha256=digest(R.OBSERVATION), stages=stages, handoffs=handoffs,
               verification_sources={n: digest(ROOT / n) for n in ('src/resource_loader.c', 'tools/verify_player_grounded_m11_frames.py',
                                                                    'tools/verify_player_constraint_runtime.py')},
               limitations=['M11 captures have no native stop inside 0015fc10; its effects are checked at the history-copy and camera stops.'])
    with a.output.open('x') as f: json.dump(out, f, indent=2); f.write('\n')
    print(out['result'], sum(s['result'] == 'passed' for s in stages), '/', len(stages), 'stages')
    if not ok: raise SystemExit(1)


if __name__ == '__main__': main()
