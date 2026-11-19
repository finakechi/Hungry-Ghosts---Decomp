#!/usr/bin/env python3
"""M12 connected grounded movement: paused-retail comparison of C 0015fc10/0015e420.

One continuous original-game run walks obliquely into a corridor side wall, slides,
turns in contact and releases. Each frame starts from an independently captured original
dispatcher-entry state (with calculated player/camera structures carried from the
previous frame). The connected C dispatcher, walk/turn, C 0015ebc0 constraint, C 0015fc10
gravity and C 0015e420 floor response, history copy and camera run on one bus; scene and
floor queries, trig and the remaining leaves execute authenticated original instructions.
Expected captures are comparison inputs only. Exceptions and driver errors are errors,
never control rejections.
"""
import argparse
import ctypes as C
import gc
import json
import struct
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'tools')]
import verify_player_connected_runtime as V
from verify_player_turn_runtime import Captures, compare, f
from verify_resource_loader import EXPECTED, shared_module
from resource_harness import ResourceEnv, library
from hg_evidence_safety import digest

P, CAM = 0x3f92d0, 0x3f9860
OBSERVATION = ROOT / 'evidence/m12/grounded-observation.json'
CONTINUITY = ((P, 0x540), (CAM, 0x150))
STATUS_VALUES = (0x10000, 0x0)
# Host guest-frame seam events (bytes, enter) -> stage reached at that point.
FRAME_STAGES = {(0x70, 1): 'constraint-entry', (0x70, 0): 'constraint-return', (0x20, 1): 'grounded-entry',
                (0x30, 1): 'floor-entry', (0x30, 0): 'floor-return', (0x20, 0): 'grounded-return'}
# Native stop -> C stages whose memory must equal it. No store executes between the
# 0015ebc0 return and 0015fc10 entry (1603f8 move, 1603fc jal, 160400 move), so the
# grounded-entry capture is also the constraint-return checkpoint.
NATIVE = {'constraint-entry': ('constraint-entry',), 'grounded-entry': ('constraint-return', 'grounded-entry'),
          'floor-entry': ('floor-entry',), 'floor-query-return': ('floor-query-return',),
          'floor-return': ('floor-return',), 'grounded-return': ('grounded-return',),
          'history-copy': ('history-copy',), 'camera-entry': ('camera-entry',), 'camera-return': ('camera-return',)}
GRAVITY_STEP = 0xbda3d70a   # -0.08: 0 - 0.08 with +4ad clear


class GroundedDriver(V.ProbeDriver):
    def __init__(self, mem, elfdata, segments, status):
        super().__init__(mem, elfdata, segments)
        self.cop0_status = status; self.constant_t0 = {0x156ad0: 1}
        self.stage = {}; self.pushes = []; self.queries = []; self.floor_query = None
        self.history = None; self.camera_entry = None; self.sound = None; self.status_read = False
        self.direction = None

    def snap(self):
        copy = V.CapturedMemory(bytes(self.mem.raw), bytes(self.mem.scratch))
        copy.scratch_known = set(self.mem.scratch_known); copy.writes = set(self.mem.writes)
        copy.watched_reads = set(self.mem.watched_reads)
        return copy

    def external(self, pc, a=0, b=0, c=0, d=0):
        if pc == V.GUEST_FRAME and b == 1 and (a, b) in FRAME_STAGES: self.stage[FRAME_STAGES[(a, b)]] = self.snap()
        if pc == 0x15e1d0 and self.camera_entry is None: self.camera_entry = self.snap()
        if pc == 0x156ad0: self.sound = [hex(v) for v in (a, b, c, d)] + ['t0=0x1']
        if pc == 0x10cca0 or pc == 0x156ad0: self.status_read = True
        result = super().external(pc, a, b, c, d)
        if pc == V.GUEST_FRAME and b == 0 and (a, b) in FRAME_STAGES: self.stage[FRAME_STAGES[(a, b)]] = self.snap()
        if pc == 0x145630:
            self.floor_query = result; self.stage['floor-query-return'] = self.snap()
        if pc == 0x1446a0 and self.guest_frame: self.queries.append(result)
        if pc == 0x120580 and 'constraint-entry' not in self.stage: self.direction = result
        return result

    def _load(self, _, address, width):
        # First C read of the push vector inside 0015ebc0 = the native push-branch stop.
        if address == 0x3f39b0 and width == 4 and self.guest_frame == 0x70: self.pushes.append(self.snap())
        return super()._load(_, address, width)

    def _store(self, _, address, width, value):
        result = super()._store(_, address, width, value)
        if address == 0x3f9f6e: self.history = self.snap()
        return result


def observation():
    return json.loads(OBSERVATION.read_text())


def execute(lib, caps, frame, status, carry=None, mutate=None, function='hg_player_grounded_camera_fixed'):
    mem = caps.load(frame['captures']['dispatch-entry']['name'])
    if carry:
        for address, size in CONTINUITY: mem.put(address, carry.get(address, size))
    for address, (value, width) in (mutate or {}).items(): mem.store(address, value, width)
    mem.initial = bytes(mem.raw); mem.writes = set()
    ext = V.pad_dma_buffers(mem); mem.watch = [(e['start'], e['end']) for e in ext]
    driver = GroundedDriver(mem, caps.elfdata, caps.segments, status)
    fn = getattr(lib, function); fn.argtypes = [C.POINTER(ResourceEnv), C.c_uint32]; fn.restype = None
    fn(C.byref(driver.env), 0x1ff8000)
    if driver.errors: raise AssertionError(driver.errors)
    if driver.guest_frame != 0: raise AssertionError('Unbalanced guest frame')
    if driver.camera_entry is None or driver.history is None: raise AssertionError('Missing stage coverage')
    driver.stage['history-copy'] = driver.history; driver.stage['camera-entry'] = driver.camera_entry
    driver.stage['camera-return'] = driver.mem
    return driver


def stage_pairs(driver, frame, strict=True):
    pairs = []
    for native, calculated in NATIVE.items():
        row = frame['captures'][native]
        for label in calculated: pairs.append((label, driver.stage.get(label), row))
    if strict and len(driver.pushes) != len(frame['pushes']):
        raise AssertionError(f'{frame["counter"]}: {len(driver.pushes)} C pushes, {len(frame["pushes"])} native push stops')
    for index, (snapshot, row) in enumerate(zip(driver.pushes, frame['pushes'])):
        pairs.append((f'push-{index}-{row["tier"]}', snapshot, row))
    return pairs


def summary(driver, frame):
    st = driver.stage; g = lambda name, off, w=4: st[name].load(P + off, w)
    return dict(frame=frame['counter'], stick_x=f(driver.mem.load(0x3eaf0c)), stick_y=f(driver.mem.load(0x3eaf10)),
        speed=f(driver.mem.load(P + 0x438)), turn_rate=f(driver.mem.load(P + 0x440)), heading=f(driver.mem.load(P + 0x44)),
        proposed=[f(g('constraint-entry', o)) for o in (0x30, 0x34, 0x38)],
        constrained=[f(g('constraint-return', o)) for o in (0x30, 0x34, 0x38)],
        after_gravity_y=f(g('floor-entry', 0x34)), speed_after_gravity=hex(g('floor-entry', 0x430)),
        floor_query_result=hex(driver.floor_query), floor_hit=[f(st['floor-query-return'].load(0x3f39a0 + 4 * i)) for i in range(3)],
        floor_type=st['floor-query-return'].load(0x3f39c6, 2), floor_normal_y=f(st['floor-query-return'].load(0x3f3a14)),
        committed=[f(g('grounded-return', o)) for o in (0x30, 0x34, 0x38)],
        committed_bits=[hex(g('grounded-return', o)) for o in (0x30, 0x34, 0x38)],
        speed_after_floor=hex(g('grounded-return', 0x430)), ground_2c3=g('grounded-return', 0x2c3, 1),
        surface_47c=hex(g('grounded-return', 0x47c, 2)), result_49a=hex(driver.mem.load(P + 0x49a, 2)),
        head_queries=[hex(q) for q in driver.queries[:5]], pushes=len(driver.pushes),
        history=[f(driver.mem.load(P + 0x390 + 4 * i)) for i in range(3)],
        history_matches_committed=driver.mem.get(P + 0x390, 16) == st['grounded-return'].get(P + 0x30, 16),
        scratch_eye=[f(driver.mem.load(0x70000040 + 4 * i)) for i in range(3)],
        sound_request=driver.sound, modeled_leaves=sorted({c['entry'] for c in driver.modeled_calls}))


def sequence(lib, caps, obs):
    stages, handoffs, registers, values, invariance = [], [], [], [], []
    prior = None; executed = set()
    for frame in obs['frames']:
        entry = caps.load(frame['captures']['dispatch-entry']['name'])
        if prior is not None:
            diff = [hex(a + i) for a, n in CONTINUITY for i in range(n) if prior.get(a + i, 1) != entry.get(a + i, 1)]
            handoffs.append(dict(frame=frame['counter'], result='passed' if not diff else 'failed', differences=diff[:64]))
        driver = execute(lib, caps, frame, STATUS_VALUES[0], carry=prior)
        if driver.status_read:
            other = execute(lib, caps, frame, STATUS_VALUES[1], carry=prior)
            same = other.mem.raw == driver.mem.raw and other.mem.scratch == driver.mem.scratch
            invariance.append(dict(frame=frame['counter'], result='passed' if same else 'failed')); del other
        for label, actual, row in stage_pairs(driver, frame):
            if actual is None: raise AssertionError(f'Missing {frame["counter"]}/{label} coverage')
            r = compare(actual, caps.load(row['name']))
            stages.append(dict(frame=frame['counter'], stage=label, expected=row['name'], expected_sha256=row['sha256'], **r))
        regs = frame['captures']
        # Movement direction: 120580 result, or facing +44 when the move is below 0.001.
        direction = driver.direction if driver.direction is not None else entry.load(P + 0x44)
        checks = [('floor-query-v0', regs['floor-query-return']['registers']['v0_low'], f'{driver.floor_query:08x}'),
                  ('floor-return-v0', regs['floor-return']['registers']['v0_low'], '00000001'),
                  ('constraint-entry-f12', regs['constraint-entry']['registers']['f12'], f'{direction:08x}')]
        for name, native, calculated in checks:
            registers.append(dict(frame=frame['counter'], register=name, native=native['value'], method=native['method'],
                                  calculated=calculated, result='passed' if native['value'] == calculated else 'failed'))
        values.append(summary(driver, frame))
        executed.update(driver.executed); prior = driver.mem
        print(frame['counter'], 'PASSED' if all(s['result'] == 'passed' for s in stages + registers if s['frame'] == frame['counter']) else 'FAILED', flush=True)
        for row in list(caps.cache): del caps.cache[row]
        del driver; gc.collect()
    return dict(stages=stages, handoffs=handoffs, registers=registers, status_invariance=invariance,
                operation_values=values, modeled_instruction_addresses=len(executed)), executed


def operation_checks(obs, values):
    by = {v['frame']: v for v in values}; ph = {fr['counter']: fr['phase'] for fr in obs['frames']}
    free = [n for n in by if ph[n] == 'approach']; contact = [n for n in by if ph[n] in ('contact', 'contact-turn')]
    turning = [n for n in by if ph[n] == 'contact-turn']; release = [n for n in by if ph[n] == 'release']
    frames = [v['frame'] for v in values]
    def slid(n): return by[n]['constrained'][2] < by[n]['proposed'][2] and n - 1 in by and by[n]['committed'][0] < by[n - 1]['committed'][0]
    return dict(
        consecutive_frames=frames == list(range(frames[0], frames[0] + len(frames))),
        phases_present=bool(free) and bool(contact) and bool(turning) and bool(release),
        approach_unconstrained=all(by[n]['result_49a'] == '0x0' and by[n]['pushes'] == 0 and by[n]['constrained'] == by[n]['proposed'] for n in free),
        oblique_contact_slides=all(by[n]['pushes'] > 0 and by[n]['result_49a'] != '0x0' for n in contact)
                               and all(slid(n) for n in contact if by[n]['stick_y'] != 0),
        turning_in_contact=all(by[n]['stick_x'] != 0 and by[n]['turn_rate'] != 0 for n in turning),
        release_input_zero=all(by[n]['stick_y'] == 0 and by[n]['stick_x'] == 0 for n in release),
        gravity_applied_every_frame=all(v['speed_after_gravity'] == hex(GRAVITY_STEP) for v in values),
        floor_hit_every_frame=all(int(v['floor_query_result'], 16) & 0x100 and v['ground_2c3'] == 1 for v in values),
        floor_snap_every_frame=all(v['committed'][1] == v['floor_hit'][1] and v['committed'][1] != v['after_gravity_y'] for v in values),
        vertical_speed_cleared=all(v['speed_after_floor'] == '0x0' for v in values),
        floor_changes_height=sum(1 for v in values if v['committed'][1] != v['constrained'][1]) >= 2,
        history_is_floor_corrected=all(v['history_matches_committed'] for v in values),
        camera_consumes_floor_corrected=all(v['scratch_eye'][0] == v['committed'][0] and v['scratch_eye'][2] == v['committed'][2] for v in values))


# Compiled source defects. Each must produce a measured full-memory divergence.
DEFECTS = [
    ('wrong-gravity-step', 'contact', 'gravity=r8(e,a+0x4ad)==0?0x3da3d70au:0x3cf5c28fu;', 'gravity=r8(e,a+0x4ad)==0?0x3cf5c28fu:0x3cf5c28fu;'),
    ('omitted-floor-snap', 'contact', 'copy_quad(e,a+0x30,0x3f39a0);                         /* snap to the floor hit */', ''),
    ('wrong-probe-origin', 'contact', 'w32(e,0x70000b84,fsub(e,r32(e,0x70000b84),0x40000000u)); /* y - 2.0 */',
     'w32(e,0x70000b84,fsub(e,r32(e,0x70000b84),0x3f800000u));'),
    ('speed-not-cleared', 'contact', 'if(call(e,0x15e420,a,0,0,0)!=0)w32(e,a+0x430,0);', 'call(e,0x15e420,a,0,0,0);'),
    ('wrong-ground-flag', 'contact', '                w8(e,a+0x2c3,1);\n', '                w8(e,a+0x2c3,2);\n'),
    ('wrong-surface-bit', 'contact', '((1u<<(type&31u))&0xffffu)', '((1u<<((type+1u)&31u))&0xffffu)'),
    ('floor-before-constraint', 'contact', '    uint32_t result=call(e,0x15ebc0,heading,a,0,0);\n    call(e,0x15fc10,a,0,0,0);',
     '    call(e,0x15fc10,a,0,0,0);\n    uint32_t result=call(e,0x15ebc0,heading,a,0,0);'),
    ('history-before-floor', 'contact', '    call(e,HG_GUEST_FRAME,0x20,1,0,0);\n    uint32_t gravity',
     '    call(e,HG_GUEST_FRAME,0x20,1,0,0);copy_quad(e,a+0x390,a+0x30);\n    uint32_t gravity'),
    ('camera-uses-unsnapped-height', 'contact', '    copy_quad(e,a+0x30,0x3f39a0);                         /* snap to the floor hit */',
     '    m12_unsnapped_y=r32(e,a+0x34);copy_quad(e,a+0x30,0x3f39a0);'),
    ('missing-floor-guest-frame', 'contact', '    call(e,HG_GUEST_FRAME,0x30,1,0,0);\n    w8(e,a+0x483', '    w8(e,a+0x483'),
]
# Second edits complete a defect whose other site is elsewhere.
SECOND = {
    'history-before-floor': ('copy_quad(e,a+0x390,a+0x30);\n}\nvoid hg_player_dispatch_fixed', '/* history recorded before 0015fc10 */\n}\nvoid hg_player_dispatch_fixed'),
    'camera-uses-unsnapped-height': ('    w32(e,0x70000044,fadd(e,0x41840000u,r32(e,player+0x34)));',
                                     '    w32(e,0x70000044,fadd(e,0x41840000u,m12_unsnapped_y));'),
    'missing-floor-guest-frame': ('    if(fault!=0) {domain_fault(e,fault);call(e,HG_GUEST_FRAME,0x30,0,0,0);return 0;}\n    copy_quad(e,a+0x30,0x3f39a0);                         /* snap to the floor hit */\n    call(e,HG_GUEST_FRAME,0x30,0,0,0);',
                                  '    if(fault!=0) {domain_fault(e,fault);return 0;}\n    copy_quad(e,a+0x30,0x3f39a0);'),
}
THIRD = {}
PRELUDE = {'camera-uses-unsnapped-height': ('#include "resource_loader.h"\n', '#include "resource_loader.h"\nstatic uint32_t m12_unsnapped_y;\n')}
# Input counterfactuals: measured divergence from a changed original input.
INPUTS = [('released-in-contact', 'contact', {0x3eaf10: (0, 4)}),
          ('turn-reversed-in-contact', 'contact-turn', {0x3eaf0c: (0xbf800000, 4)}),
          ('raised-above-floor', 'contact', {P + 0x34: (0x42200000, 4)})]   # y = 40: still within the probe


def frozen(memory):
    copy = V.CapturedMemory(bytes(memory.raw), bytes(memory.scratch))
    copy.scratch_known = set(memory.scratch_known); copy.writes = set(memory.writes)
    copy.watched_reads = set(memory.watched_reads)
    return copy


def controls(lib, caps, obs, folder):
    folder.mkdir(parents=True, exist_ok=False); rows = []
    source = (ROOT / 'src/resource_loader.c').read_text()
    by_phase = {}
    for fr in obs['frames']: by_phase.setdefault(fr['phase'], []).append(fr)
    # The middle frame of a phase; computed calculated carry is not needed (entry capture only).
    pick = lambda phase: by_phase[phase][len(by_phase[phase]) // 2]
    def measure(name, fr, d, kind):
        # Each comparison uses a frozen copy: compare_memory's DMA reporting reads through
        # load(), which would otherwise record a spurious modeled access for a later compare.
        # Rejection requires differing EE RAM or scratchpad bytes at an authenticated checkpoint.
        final = compare(frozen(d.mem), caps.load(fr['captures']['camera-return']['name']))
        first = None; checkpoints = 0
        for label, actual, row in stage_pairs(d, fr, strict=False):
            if actual is None: continue
            r = compare(frozen(actual), caps.load(row['name'])); checkpoints += 1
            if (r['different_bytes'] or r['scratchpad']['different_bytes']) and first is None:
                first = dict(stage=label, different_bytes=r['different_bytes'], scratchpad=r['scratchpad']['different_bytes'])
        diverged = first is not None or final['different_bytes'] or final['scratchpad']['different_bytes']
        gr = d.stage.get('grounded-return')
        return dict(control=name, kind=kind, frame=fr['counter'], result='rejected' if diverged else 'ESCAPED',
                    checkpoints_compared=checkpoints, native_pushes=len(fr['pushes']), calculated_pushes=len(d.pushes),
                    first_divergent_stage=first, comparison=final,
                    committed_y=f(gr.load(P + 0x34)) if gr else None, eye_y=f(d.mem.load(0x70000044)),
                    history_y=f(d.mem.load(P + 0x394)), speed_430=hex(d.mem.load(P + 0x430)))
    # Null controls: the unmutated build under identical control conditions must not diverge.
    for phase in sorted({ph for _, ph, *_ in DEFECTS + INPUTS}):
        fr = pick(phase)
        try:
            row = measure(f'null-{phase}', fr, execute(lib, caps, fr, STATUS_VALUES[0]), 'null-control')
            row['result'] = 'clean' if row['result'] == 'ESCAPED' else 'DIVERGED'
        except Exception as exc: row = dict(control=f'null-{phase}', kind='null-control', frame=fr['counter'], result='error', detail=str(exc)[:500])
        rows.append(row); print(row['control'], row['result'], flush=True)
        with (folder / (row['control'] + '.result.json')).open('x') as out: json.dump(row, out, indent=2); out.write('\n')
        for k in list(caps.cache): del caps.cache[k]
        gc.collect()
    for name, phase, old, new in DEFECTS:
        fr = pick(phase)
        try:
            mutated = source
            for edit in ((old, new), SECOND.get(name), THIRD.get(name), PRELUDE.get(name)):
                if edit is None: continue
                o, n = edit
                if mutated.count(o) != 1: raise AssertionError(f'{name}: target not unique')
                mutated = mutated.replace(o, n)
            if mutated == source: raise AssertionError(f'{name}: no edit')
            path = folder / (name + '.c'); path.open('x').write(mutated)
            mlib = library(source=path, output=folder / (name + '.so'))
            row = measure(name, fr, execute(mlib, caps, fr, STATUS_VALUES[0]), 'compiled-defect')
        except Exception as exc: row = dict(control=name, kind='compiled-defect', frame=fr['counter'], result='error', detail=str(exc)[:500])
        rows.append(row); print(name, row['result'], flush=True)
        with (folder / (name + '.result.json')).open('x') as out: json.dump(row, out, indent=2); out.write('\n')
        for k in list(caps.cache): del caps.cache[k]
        gc.collect()
    for name, phase, mutate in INPUTS:
        fr = pick(phase)
        try: row = measure(name, fr, execute(lib, caps, fr, STATUS_VALUES[0], mutate=mutate), 'input-counterfactual')
        except Exception as exc: row = dict(control=name, kind='input-counterfactual', frame=fr['counter'], result='error', detail=str(exc)[:500])
        rows.append(row); print(name, row['result'], flush=True)
        with (folder / (name + '.result.json')).open('x') as out: json.dump(row, out, indent=2); out.write('\n')
        for k in list(caps.cache): del caps.cache[k]
        gc.collect()
    # Fail-closed control: lifted out of probe range, the C must refuse (no floor path).
    fr = pick('contact')
    try:
        execute(lib, caps, fr, STATUS_VALUES[0], mutate={P + 0x34: (0x42480000, 4)})   # y = 50
        row = dict(control='airborne-fails-closed', kind='fail-closed', frame=fr['counter'], result='ESCAPED')
    except AssertionError as exc:
        closed = 'code 0x50' in str(exc)
        row = dict(control='airborne-fails-closed', kind='fail-closed', frame=fr['counter'],
                   result='refused' if closed else 'error', detail=str(exc)[:300])
    rows.append(row); print(row['control'], row['result'], flush=True)
    with (folder / (row['control'] + '.result.json')).open('x') as out: json.dump(row, out, indent=2); out.write('\n')
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf', type=Path, required=True); p.add_argument('--ps2dinfo', type=Path, required=True)
    p.add_argument('--capture-dir', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--negative-controls', action='store_true'); a = p.parse_args()
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
        if caps.load(fr['captures']['dispatch-entry']['name']).load(0x46e118) != fr['counter']: raise ValueError('Wrong capture frame')
    caps.cache.clear()
    lib = library(output=a.output.with_suffix('.so')); result, executed = sequence(lib, caps, obs)
    result['operation_checks'] = operation_checks(obs, result['operation_values'])
    with a.output.with_suffix('.baseline.json').open('x') as out: json.dump(result, out, indent=2); out.write('\n')
    if a.negative_controls: result['negative_controls'] = controls(lib, caps, obs, a.output.parent / (a.output.stem + '-controls'))
    ok = all(s['result'] == 'passed' for s in result['stages'] + result['handoffs'] + result['registers'] + result['status_invariance'])
    ok &= all(result['operation_checks'].values())
    ok &= all(c['result'] == ('clean' if c['kind'] == 'null-control' else 'refused' if c['kind'] == 'fail-closed' else 'rejected')
              for c in result.get('negative_controls', []))
    result.update(schema='hg-player-grounded-runtime/v1', result='passed' if ok else 'failed', elf_sha256=EXPECTED,
        observation_sha256=digest(OBSERVATION),
        verification_sources={n: digest(ROOT / n) for n in ('src/resource_loader.c', 'src/resource_loader.h', 'tools/ee_subset.py',
            'tools/verify_player_grounded_runtime.py', 'tools/verify_player_connected_runtime.py', 'tools/verify_player_turn_runtime.py')},
        continuity_ranges=[dict(address=hex(x), size=n) for x, n in CONTINUITY],
        limitations=['Hybrid C and authenticated original-instruction-modeled leaves; not whole-frame reconstruction.',
            'Each frame starts from an independent original dispatcher-entry capture with calculated player/camera structures carried forward.',
            'Guest stack 01fe0000..02000000 excluded; asynchronous libpad DMA excluded only if untouched by execution.',
            'Floor routine: ordinary floor hits only; no-floor, hazard, special-surface and spawn paths fail closed.',
            'PCSX2 v2.8.2 functional FP defaults; no exceptional FP, flags or hardware timing.'])
    with a.output.open('x') as out: json.dump(result, out, indent=2); out.write('\n')
    print(result['result'], result['operation_checks'])
    if not ok: raise SystemExit(1)


if __name__ == '__main__': main()
