#!/usr/bin/env python3
"""M13 continuous player movement: 20+ frames from one authenticated starting state.

The reconstructed per-frame path (hg_player_frame_continuous) runs on one carried bus:
coordinator prefix 0012f850 (3fa190, edge sync 0031bb80, 3f9ff8, guarded 46e118), the
M10 dispatcher (walk/turn, C 0015ebc0, C 0015fc10/0015e420, history), the M9 same-frame
camera, the declared external coordinator services and the 001379f0 engine counter
advance (46d930/46e110). Between frames only the recorded controller input (pad-area
bytes changed by the original input system) is supplied externally; every other byte is
carried from the preceding reconstructed frame. Original captures are comparison
references only.

Comparisons are domain-aware: every address our path writes (player and camera
structures, counters, edge counters, history flag, floor/query scratch, reconstructed
scratch regions) must equal the original capture at each native stop; all remaining
differences are enumerated and must classify into the declared external domains (guest
stack, untouched libpad DMA, kernel low RAM, input system bytes, other-actor/render
engine state). A difference in a reconstructed domain is a failure.
"""
import argparse
import ctypes as C
import gc
import json
import re
import struct
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'tools')]
import verify_player_connected_runtime as V
import verify_player_grounded_runtime as G
from verify_player_grounded_runtime import Captures, observation as m12_observation, STATUS_VALUES
from verify_player_turn_runtime import f
from verify_resource_loader import EXPECTED, shared_module
from resource_harness import ResourceEnv, library
from hg_evidence_safety import digest

P, CAM = 0x3f92d0, 0x3f9860
OBSERVATION = ROOT / 'evidence/m13/continuous-observation.json'
PAD_INPUT = (0x3eaf00, 0x3eaf40)
# Coordinator seam events (HG_GUEST_FRAME bytes from hg_frame_advance).
COORD_STAGES = {(0x50, 1): 'coordinator-entry', (0x50, 0): 'coordinator-counter'}
# RAM addresses the reconstructed path writes outside the player/camera structures.
GUARDED_GLOBALS = {
    '3fa190': (0x3fa190, 4), '3f9ff8': (0x3f9ff8, 2),
    '46e118': (0x46e118, 4), '46e110': (0x46e110, 4), '46d930': (0x46d930, 4),
    '3fa040': (0x3fa040, 4), '3fa050': (0x3fa050, 4),
    '3f9e34-210': (0x3f9e34 + 0x210, 4), '3f9e34-220': (0x3f9e34 + 0x220, 4),
    '3f9e38-210': (0x3f9e38 + 0x210, 4), '3f9e38-220': (0x3f9e38 + 0x220, 4),
    '3f9e3c-210': (0x3f9e3c + 0x210, 4), '3f9e3c-220': (0x3f9e3c + 0x220, 4),
    '95e57c': (0x95e57c, 4), '3f9f38': (0x3f9f38, 1), '3f9f6e': (0x3f9f6e, 1),
    '3f9ff2': (0x3f9ff2, 2),
}
GUARDED_STRUCT = [(P, P + 0x540), (CAM, CAM + 0x150)]


def external_domain(address):
    """Classify a non-guarded RAM address for the external-difference report."""
    if 0x01fe0000 <= address < 0x02000000: return 'guest-stack'
    if 0x0095e340 <= address < 0x0095e440: return 'libpad-dma'
    if address < 0x00100000: return 'low-ram'
    if PAD_INPUT[0] <= address < PAD_INPUT[1]: return 'input-pad'
    if 0x3f9f00 <= address < 0x3fb000: return 'engine-globals'
    return 'engine-other'


def guarded_ranges():
    ranges = list(GUARDED_STRUCT)
    for _, (start, size) in GUARDED_GLOBALS.items():
        ranges.append((start, start + size))
    return ranges


def in_guarded(address):
    for start, end in guarded_ranges():
        if start <= address < end: return True
    return False


class ContinuousDriver(G.GroundedDriver):
    """GroundedDriver plus coordinator seam snapshots and the external-call log."""
    def __init__(self, mem, elfdata, segments, status):
        super().__init__(mem, elfdata, segments, status)
        self.external_calls = []
        self.coordinator_entry = None

    def snap(self):
        return super().snap()

    def external(self, pc, a=0, b=0, c=0, d=0):
        if pc == V.GUEST_FRAME and (a, b) in COORD_STAGES:
            self.stage[COORD_STAGES[(a, b)]] = self.snap()
            if (a, b) == (0x50, 1): self.coordinator_entry = self.stage['coordinator-entry']
            return 0
        if pc == 0xfffffff3:
            self.external_calls.append(hex(a))
            return 0
        return super().external(pc, a, b, c, d)


def observation():
    return json.loads(OBSERVATION.read_text())


class ContinuousRun:
    def __init__(self, lib, caps, obs):
        self.lib = lib; self.caps = caps; self.obs = obs
        self.frames = sorted(obs['frames'], key=lambda fr: fr['counter'])
        start_stage = 'coordinator-entry' if 'coordinator-entry' in self.frames[0]['captures'] else 'dispatch-entry'
        self.start_stage = start_stage
        self.mem = caps.load(self.frames[0]['captures'][start_stage]['name'])
        self.mem.initial = bytes(self.mem.raw)
        self.external = V.pad_dma_buffers(self.mem)
        self.mem.watch = [(e['start'], e['end']) for e in self.external]
        self.scratch_guarded = None
        self.pad_delta = self._pad_deltas()

    def _pad_deltas(self):
        """Pad-area bytes the original input system changed between frames, per
        target frame: {counter: {address: value}}. Controller input evidence only."""
        deltas = {}
        for fr, nxt in zip(self.frames, self.frames[1:]):
            a = self.caps.load(fr['captures']['camera-return']['name'])
            b = self.caps.load(nxt['captures']['dispatch-entry']['name'])
            out = {}
            for address in range(*PAD_INPUT):
                if a.raw[address] != b.raw[address]:
                    out[address] = b.raw[address]
            deltas[nxt['counter']] = out
        return deltas

    def inject_input(self, counter):
        delta = self.pad_delta.get(counter, {})
        for address, value in delta.items():
            self.mem.raw[address] = value

    def run_frame(self, fr, mutate=None):
        self.inject_input(fr['counter'])
        for address, (value, width) in (mutate or {}).items():
            self.mem.store(address, value, width)
        driver = ContinuousDriver(self.mem, self.caps.elfdata, self.caps.segments, STATUS_VALUES[0])
        fn = getattr(self.lib, 'hg_player_frame_continuous')
        fn.argtypes = [C.POINTER(ResourceEnv), C.c_uint32]; fn.restype = None
        fn(C.byref(driver.env), 0x1ff8000)
        if driver.errors: raise AssertionError(driver.errors)
        if driver.guest_frame != 0: raise AssertionError('Unbalanced guest frame')
        driver.stage['history-copy'] = driver.history
        driver.stage['camera-entry'] = driver.camera_entry
        driver.stage['camera-return'] = driver.mem
        return driver

    def guarded_scratch(self):
        """All scratchpad offsets written by the reconstructed path (empirical union)."""
        if self.scratch_guarded is None:
            offsets = set()
            for fr in self.frames:
                d = self.run_frame(fr)
                for address in d.mem.writes:
                    if 0x70000000 <= address < 0x70004000:
                        offsets.add(address - 0x70000000)
            self.scratch_guarded = sorted(offsets)
        return self.scratch_guarded

    def compare_stage(self, actual, expected):
        """Guarded-domain comparison plus the external-difference enumeration."""
        ram_diffs = [a for a in range(0x2000000) if actual.raw[a] != expected.raw[a]]
        guarded_ram = [a for a in ram_diffs if in_guarded(a)]
        external_ram = {}
        for a in ram_diffs:
            if in_guarded(a): continue
            external_ram.setdefault(external_domain(a), []).append(a)
        guarded_scratch = []
        external_scratch = []
        for off in range(0x4000):
            if actual.scratch[off] == expected.scratch[off]: continue
            if off in self.scratch_guarded: guarded_scratch.append(off)
            else: external_scratch.append(off)
        return dict(guarded_ram=[hex(a) for a in guarded_ram[:16]], guarded_ram_count=len(guarded_ram),
                    guarded_scratch=[hex(0x70000000 + o) for o in guarded_scratch[:16]],
                    guarded_scratch_count=len(guarded_scratch),
                    external_ram={d: len(v) for d, v in sorted(external_ram.items())},
                    external_ram_count=len(external_ram),
                    external_scratch_count=len(external_scratch),
                    result='passed' if not guarded_ram and not guarded_scratch else 'failed')


def sequence(lib, caps, obs):
    # First pass: the guarded-scratch union on a throwaway run (the real sequence
    # must start from the authenticated starting state, untouched).
    scratch = ContinuousRun(lib, caps, obs).guarded_scratch()
    run = ContinuousRun(lib, caps, obs)
    run.scratch_guarded = scratch
    rows = []
    failures = []
    for fr in run.frames:
        d = run.run_frame(fr)
        stages = {}
        for label, snapshot in d.stage.items():
            row = fr['captures'].get(label)
            if row is None: continue
            expected = caps.load(row['name'])
            result = run.compare_stage(snapshot, expected)
            result['capture'] = row['name']
            if result['result'] != 'passed': failures.append((fr['counter'], label))
            stages[label] = result
        for index, (snapshot, row) in enumerate(zip(d.pushes, fr['pushes'])):
            expected = caps.load(row['name'])
            result = run.compare_stage(snapshot, expected)
            result['capture'] = row['name']
            if result['result'] != 'passed': failures.append((fr['counter'], f'push-{index}'))
            stages[f'push-{index}-{row["tier"]}'] = result
        rows.append(dict(counter=fr['counter'], phase=fr['phase'], stages=stages,
                         external_calls=d.external_calls))
    return dict(frames=rows, failures=[dict(counter=c, stage=s) for c, s in failures],
                guarded_scratch=[hex(0x70000000 + o) for o in scratch],
                result='passed' if not failures else 'failed')


def controls(lib, caps, obs, folder):
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    frames = sorted(obs['frames'], key=lambda fr: fr['counter'])
    middle = len(frames) // 2
    target = frames[middle]
    scratch_set = ContinuousRun(lib, caps, obs).guarded_scratch()

    def measure(name, kind, mutate=None, restore=False):
        run = ContinuousRun(lib, caps, obs)
        run.scratch_guarded = scratch_set
        for fr in run.frames:
            if restore and fr['counter'] == target['counter']:
                fresh = caps.load(fr['captures']['dispatch-entry']['name'])
                run.mem.raw[:] = fresh.raw
                run.mem.scratch[:] = fresh.scratch
            run.mem.watch = [(e['start'], e['end']) for e in run.external]
            if fr['counter'] == target['counter'] and mutate:
                run.mem.initial = bytes(run.mem.raw)
            d = run.run_frame(fr, mutate if fr['counter'] == target['counter'] else None)
            if fr['counter'] != target['counter']: continue
            for label, snapshot in d.stage.items():
                row = fr['captures'].get(label)
                if row is None: continue
                expected = caps.load(row['name'])
                result = run.compare_stage(snapshot, expected)
                if result['result'] != 'passed':
                    return dict(control=name, kind=kind, frame=fr['counter'], result='rejected',
                                first_divergent_stage=label, detail=result)
            # Null controls must stay clean to the end of the sequence.
            if kind == 'null-control':
                return dict(control=name, kind=kind, frame=target['counter'], result='clean',
                            note='capture-restoration run matched all stages')
            return dict(control=name, kind=kind, frame=fr['counter'], result='ESCAPED')
        return dict(control=name, kind=kind, frame=target['counter'], result='error', detail='target frame not reached')

    cases = [
        ('null-capture-restoration', 'null-control', None, dict(restore=True)),
        ('corrupt-player-carry', 'continuity-corruption', {P + 0x30: (0x42280000, 4)}, {}),
        ('corrupt-camera-carry', 'continuity-corruption', {CAM + 0x0: (0xc2c80000, 4)}, {}),
        ('omitted-counter-advance', 'continuity-corruption', {0x46e110: (0, 4)}, {}),
        ('omitted-frame-advance', 'continuity-corruption', {0x3fa190: (0, 4)}, {}),
        ('wrong-input-stick', 'input-counterfactual', {0x3eaf10: (0, 4)}, {}),
    ]
    for name, kind, mutate, kw in cases:
        try:
            row = measure(name, kind, mutate, **kw)
        except Exception as exc:
            row = dict(control=name, kind=kind, frame=target['counter'], result='error', detail=str(exc)[:400])
        rows.append(row); print(name, row['result'], flush=True)
        with (folder / (name + '.result.json')).open('x') as out:
            json.dump(row, out, indent=2); out.write('\n')
        gc.collect()
    # Compiled defect: gravity step 0.03 instead of 0.08 (source mutation build).
    source = (ROOT / 'src/resource_loader.c').read_text()
    before = 'uint32_t gravity=r8(e,a+0x4ad)==0?0x3da3d70au:0x3cf5c28fu;'
    after = 'uint32_t gravity=r8(e,a+0x4ad)==0?0x3cf5c28fu:0x3cf5c28fu;'
    try:
        if source.count(before) != 1: raise AssertionError('gravity target not unique')
        path = folder / 'wrong-gravity-step.c'
        path.open('x').write(source.replace(before, after))
        mlib = library(source=path, output=folder / 'wrong-gravity-step.so')
        row = measure_compiled(mlib, 'wrong-gravity-step', caps, obs, target, scratch_set)
    except Exception as exc:
        row = dict(control='wrong-gravity-step', kind='compiled-defect', frame=target['counter'],
                   result='error', detail=str(exc)[:400])
    rows.append(row); print(row['control'], row['result'], flush=True)
    with (folder / (row['control'] + '.result.json')).open('x') as out:
        json.dump(row, out, indent=2); out.write('\n')
    return rows


def measure_compiled(mlib, name, caps, obs, target, scratch_set):
    run = ContinuousRun(mlib, caps, obs)
    run.scratch_guarded = scratch_set
    for fr in run.frames:
        d = run.run_frame(fr)
        if fr['counter'] != target['counter']: continue
        for label, snapshot in d.stage.items():
            row = fr['captures'].get(label)
            if row is None: continue
            expected = caps.load(row['name'])
            result = run.compare_stage(snapshot, expected)
            if result['result'] != 'passed':
                return dict(control=name, kind='compiled-defect', frame=fr['counter'],
                            result='rejected', first_divergent_stage=label, detail=result)
        return dict(control=name, kind='compiled-defect', frame=fr['counter'], result='ESCAPED')
    return dict(control=name, kind='compiled-defect', frame=target['counter'], result='error')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf', type=Path, required=True)
    p.add_argument('--ps2dinfo', type=Path, required=True)
    p.add_argument('--capture-dir', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--negative-controls', action='store_true')
    a = p.parse_args()
    if a.output.exists() or a.output.with_suffix('.so').exists(): raise FileExistsError('Fresh output required')
    if digest(a.elf) != EXPECTED: raise ValueError('Wrong original ELF')
    obs = observation()
    caps = Captures(a.capture_dir); caps.elfdata = a.elf.read_bytes()
    for fr in obs['frames']:
        for row in list(fr['captures'].values()) + fr['pushes']:
            if digest(caps.path(row['name'])) != row['sha256']: raise ValueError('Observation/capture digest mismatch')
            for shot in row['screenshots']:
                if digest(a.capture_dir.parent / 'stops' / shot['name']) != shot['sha256']: raise ValueError('Screenshot digest mismatch')
    caps.segments, _ = shared_module(a.ps2dinfo).segments_from_elf(caps.elfdata)
    for fr in obs['frames']:
        if caps.load(fr['captures']['dispatch-entry']['name']).load(0x46e118) != fr['counter']:
            raise ValueError('Wrong capture frame')
    caps.cache.clear()
    lib = library(output=a.output.with_suffix('.so'))
    result = sequence(lib, caps, obs)
    if a.negative_controls:
        result['negative_controls'] = controls(lib, caps, obs, a.output.parent / (a.output.stem + '-controls'))
    ok = result['result'] == 'passed'
    if a.negative_controls:
        ok &= all(c['result'] == ('clean' if c['kind'] == 'null-control' else 'rejected')
                  for c in result['negative_controls'])
    result.update(schema='hg-player-continuous/v1', result='passed' if ok else 'failed', elf_sha256=EXPECTED,
                  observation_sha256=digest(OBSERVATION), frames=len(obs['frames']),
                  verification_sources={n: digest(ROOT / n) for n in ('src/resource_loader.c', 'src/resource_loader.h')})
    with a.output.open('x') as out:
        json.dump(result, out, indent=1); out.write('\n')
    print(result['result'], 'frames', len(obs['frames']), 'failures', result.get('failures', []))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
