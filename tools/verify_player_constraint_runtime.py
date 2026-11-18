#!/usr/bin/env python3
"""M11 static movement constraint: paused-retail comparison of the C 0015ebc0.

Each frame starts from an independently captured original dispatcher-entry
state (with calculated player/camera structures carried from the previous
frame). The connected C dispatcher, walk, C constraint, history copy and camera
run on one bus; scene queries, trig, RNG, sound and floor leaves execute
authenticated original instructions. Expected captures are comparison inputs
only. Exceptions and driver errors are errors, never control rejections.
"""
import argparse
import ctypes as C
import gc
import json
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
OBSERVATION = ROOT / 'evidence/m11/constraint-observation.json'
CONTINUITY = ((P, 0x540), (CAM, 0x150))
# CP0 Status is not captured in RAM; the sound request's DI helper reads it.
# Both interrupt-enable states are run and must give identical memory.
STATUS_VALUES = (0x10000, 0x0)
FREE, CONTACT = (4278, 4279, 4280, 4281), (4282, 4283, 4284)
FOOT_QUERIES = range(10, 15)   # 1446a0 calls 11..15: five head, five middle, then foot


class ConstraintDriver(V.ProbeDriver):
    def __init__(self, mem, elfdata, segments, status, probes=None):
        super().__init__(mem, elfdata, segments, probes)
        self.cop0_status = status; self.constant_t0 = {0x156ad0: 1}
        self.stage = {}; self.queries = []; self.sound = None; self.history = None; self.camera_entry = None
        self.frame_events = []

    def snap(self):
        copy = V.CapturedMemory(bytes(self.mem.raw), bytes(self.mem.scratch))
        copy.scratch_known = set(self.mem.scratch_known); copy.writes = set(self.mem.writes)
        copy.watched_reads = set(self.mem.watched_reads)
        return copy

    def external(self, pc, a=0, b=0, c=0, d=0):
        if pc == V.GUEST_FRAME: self.frame_events.append((a, b, self.guest_frame))
        if pc == V.GUEST_FRAME and b == 1: self.stage['constraint-entry'] = self.snap()
        if pc == 0x15e1d0 and self.camera_entry is None: self.camera_entry = self.snap()
        if pc == 0x11ad58 and self.guest_frame: self.stage['bump-sound-call'] = self.snap()
        if pc == 0x156ad0 and self.guest_frame: self.sound = [hex(v) for v in (a, b, c, d)] + ['t0=0x1']
        result = super().external(pc, a, b, c, d)
        if pc == V.GUEST_FRAME and b == 0: self.stage['constraint-return'] = self.snap()
        if pc == 0x1446a0 and self.guest_frame:
            index = len(self.queries)
            self.queries.append(dict(index=index, result=result, snapshot=self.snap() if index in FOOT_QUERIES else None))
        return result

    def _store(self, _, address, width, value):
        result = super()._store(_, address, width, value)
        if address == 0x3f9f6e: self.history = self.snap()
        return result


def observation():
    return json.loads(OBSERVATION.read_text())


def execute(lib, caps, frame, status, carry=None, mutate=None, function='hg_player_constraint_camera_fixed'):
    mem = caps.load(frame['captures']['dispatch-entry']['name'])
    if carry:
        for address, size in CONTINUITY: mem.put(address, carry.get(address, size))
    for address, (value, width) in (mutate or {}).items(): mem.store(address, value, width)
    mem.initial = bytes(mem.raw); mem.writes = set()
    ext = V.pad_dma_buffers(mem); mem.watch = [(e['start'], e['end']) for e in ext]
    driver = ConstraintDriver(mem, caps.elfdata, caps.segments, status, {'foot-push': [('load', 0x3f39b0, 4)]})
    fn = getattr(lib, function); fn.argtypes = [C.POINTER(ResourceEnv), C.c_uint32]; fn.restype = None
    fn(C.byref(driver.env), 0x1ff8000)
    if driver.errors: raise AssertionError(driver.errors)
    if driver.guest_frame != 0: raise AssertionError('Unbalanced guest frame')
    if driver.camera_entry is None or driver.history is None: raise AssertionError('Missing stage coverage')
    return driver


def stage_pairs(driver, frame):
    caps = frame['captures']
    pairs = [('constraint-entry', driver.stage.get('constraint-entry'), caps['constraint-entry']),
             ('constraint-return', driver.stage.get('constraint-return'), caps['constraint-return']),
             ('history-copy', driver.history, caps['history-copy']),
             ('camera-entry', driver.camera_entry, caps['camera-entry']),
             ('camera-return', driver.mem, caps['camera-return'])]
    if 'bump-sound-call' in caps: pairs.append(('bump-sound-call', driver.stage.get('bump-sound-call'), caps['bump-sound-call']))
    foot = [q for q in driver.queries if q['index'] in FOOT_QUERIES]
    for q, row in zip(foot, frame['foot_query_returns']):
        pairs.append((f'foot-query-return-{row["foot_probe"]}', q['snapshot'], row))
    for row in frame['foot_pushes']: pairs.append(('foot-push', driver.snapshots.get('foot-push'), row))
    return pairs


def summary(driver, frame):
    entry, ret = driver.stage['constraint-entry'], driver.stage['constraint-return']
    return dict(frame=frame['counter'], stick_y=f(driver.mem.load(0x3eaf10)), speed=f(driver.mem.load(P + 0x438)),
        proposed_x=f(entry.load(P + 0x30)), proposed_z=f(entry.load(P + 0x38)),
        committed_x=f(ret.load(P + 0x30)), committed_z=f(ret.load(P + 0x38)),
        proposed_bits=hex(entry.load(P + 0x30)), committed_bits=hex(ret.load(P + 0x30)),
        result_49a=hex(driver.mem.load(P + 0x49a, 2)), timer_508=driver.mem.load(P + 0x508, 1),
        history_x=f(driver.mem.load(P + 0x390)), history_matches_committed=driver.mem.get(P + 0x390, 16) == driver.mem.get(P + 0x30, 16),
        scratch_eye_x=f(driver.mem.load(0x70000040)), camera_eye_x=f(driver.mem.load(CAM)),
        query_results=[hex(q['result']) for q in driver.queries], sound_request=driver.sound,
        modeled_leaves=sorted({c['entry'] for c in driver.modeled_calls}))


def sequence(lib, caps, obs):
    stages, handoffs, values, invariance = [], [], [], []
    prior = None; executed = set()
    for frame in obs['frames']:
        entry = caps.load(frame['captures']['dispatch-entry']['name'])
        if prior is not None:
            diff = [hex(a + i) for a, n in CONTINUITY for i in range(n) if prior.get(a + i, 1) != entry.get(a + i, 1)]
            handoffs.append(dict(frame=frame['counter'], result='passed' if not diff else 'failed', differences=diff[:64]))
        runs = {}
        for status in STATUS_VALUES:
            runs[status] = execute(lib, caps, frame, status, carry=prior)
        a, b = runs[STATUS_VALUES[0]], runs[STATUS_VALUES[1]]
        same = a.mem.raw == b.mem.raw and a.mem.scratch == b.mem.scratch
        invariance.append(dict(frame=frame['counter'], result='passed' if same else 'failed'))
        driver = a
        for label, actual, row in stage_pairs(driver, frame):
            if actual is None: raise AssertionError(f'Missing {frame["counter"]}/{label} coverage')
            r = compare(actual, caps.load(row['name']))
            stages.append(dict(frame=frame['counter'], stage=label, expected=row['name'], expected_sha256=row['sha256'], **r))
        for q, row in zip([q for q in driver.queries if q['index'] in FOOT_QUERIES], frame['foot_query_returns']):
            stages.append(dict(frame=frame['counter'], stage=f'foot-query-register-{row["foot_probe"]}',
                result='passed' if f'{q["result"]:08x}' == row['registers']['v0_low'] else 'failed',
                calculated=hex(q['result']), original_v0_low='0x' + row['registers']['v0_low']))
        values.append(summary(driver, frame))
        executed.update(driver.executed); prior = driver.mem
        print(frame['counter'], 'PASSED' if all(s['result'] == 'passed' for s in stages if s['frame'] == frame['counter']) else 'FAILED', flush=True)
        for row in list(caps.cache): del caps.cache[row]
        del runs, a, b, driver; gc.collect()
    by = {v['frame']: v for v in values}
    disp = lambda v: v['committed_x'] - v['proposed_x']
    checks = dict(
        consecutive_frames=[v['frame'] for v in values] == list(range(4278, 4285)),
        identical_input=all(v['stick_y'] == -1.0 for v in values),
        identical_speed=len({v['speed'] for v in values}) == 1,
        unobstructed_unchanged=all(by[n]['proposed_bits'] == by[n]['committed_bits'] and by[n]['result_49a'] == '0x0' for n in FREE),
        constrained_corrected=all(disp(by[n]) > 0 and by[n]['result_49a'] == '0x100' for n in CONTACT),
        unobstructed_queries_miss=all(set(by[n]['query_results']) == {'0x0'} for n in FREE),
        contact_only_straight_foot_probe_hits=all(by[n]['query_results'][10:15] == ['0x0', '0x0', '0x100', '0x0', '0x0'] for n in CONTACT),
        history_is_committed=all(v['history_matches_committed'] for v in values),
        camera_consumes_committed=all(v['scratch_eye_x'] == v['committed_x'] for v in values)
                                  and all(by[n]['scratch_eye_x'] != by[n]['proposed_x'] for n in CONTACT),
        bump_once=by[4282]['sound_request'] is not None and by[4283]['sound_request'] is None and by[4284]['sound_request'] is None,
        status_invariant=all(r['result'] == 'passed' for r in invariance))
    return dict(stages=stages, handoffs=handoffs, status_invariance=invariance, operation_checks=checks,
                operation_values=values, modeled_instruction_addresses=len(executed)), executed


# Compiled source defects. Each must produce a measured full-memory divergence.
DEFECTS = [
    ('omitted-correction', 4282, 'w32(e,a+0x30,fadd(e,r32(e,a+0x30),r32(e,0x3f39b0)));\n    w32(e,a+0x38,fadd(e,r32(e,a+0x38),r32(e,0x3f39b8)));', ''),
    ('wrong-push-vector', 4283, 'fadd(e,r32(e,a+0x30),r32(e,0x3f39b0))', 'fadd(e,r32(e,a+0x30),r32(e,0x3f39a0))'),
    ('wrong-query-origin', 4283, 'if((hit=constraint_hit(e,a,0x70000c10))==2)return constraint_leave(e,0);\n        if(hit!=0 && constraint_facing(e,a,1)) {constraint_push(e,a,foot);result|=0x100u;}\n    }\n    constraint_origin(e,a,foot);',
     'if((hit=constraint_hit(e,a,0x70000b80))==2)return constraint_leave(e,0);\n        if(hit!=0 && constraint_facing(e,a,1)) {constraint_push(e,a,foot);result|=0x100u;}\n    }\n    constraint_origin(e,a,foot);'),
    ('wrong-direction-facing-heading', 4282, '    call(e,HG_GUEST_FRAME,0x70,1,0,0);\n    constraint_origin(e,a,head);', '    call(e,HG_GUEST_FRAME,0x70,1,0,0);heading=r32(e,a+0x44);\n    constraint_origin(e,a,head);'),
    ('inverted-facing-test', 4283, 'return fle(e,dot,threshold);', 'return !fle(e,dot,threshold);'),
    ('wrong-result-flag', 4282, '{constraint_push(e,a,foot);result|=0x100u;}\n    }\n    constraint_origin(e,a,foot);', '{constraint_push(e,a,foot);result|=0x30u;}\n    }\n    constraint_origin(e,a,foot);'),
    ('omitted-bump-timer', 4282, 'w8(e,a+0x508,0x50);', ''),
    ('omitted-sound-request', 4282, 'call(e,0x156ad0,r16(e,0x3e0850u+offset),0x1000,player_sound_volume_127(volume),0x40);', '(void)offset;'),
    ('history-before-correction', 4282, '    call(e,HG_GUEST_FRAME,0x70,1,0,0);\n    constraint_origin(e,a,head);', '    call(e,HG_GUEST_FRAME,0x70,1,0,0);copy_quad(e,a+0x390,a+0x30);\n    constraint_origin(e,a,head);'),
    ('camera-uses-proposed-position', 4283, '    call(e,HG_GUEST_FRAME,0x70,1,0,0);\n    constraint_origin(e,a,head);', '    call(e,HG_GUEST_FRAME,0x70,1,0,0);m11_proposed_x=r32(e,a+0x30);\n    constraint_origin(e,a,head);'),
    ('missing-guest-frame', 4283, 'call(e,HG_GUEST_FRAME,0x70,1,0,0);', ''),
]
# Two-site mutations complete a defect whose second edit is elsewhere.
SECOND = {
    'history-before-correction': ('copy_quad(e,a+0x390,a+0x30);\n}', '/* history recorded at constraint entry */\n}'),
    'camera-uses-proposed-position': ('    w32(e,0x70000040,r32(e,player+0x30));', '    w32(e,0x70000040,m11_proposed_x);'),
    'missing-guest-frame': ('    call(e,HG_GUEST_FRAME,0x70,0,0,0);\n    return result;', '    (void)e;\n    return result;'),
}
PRELUDE = {'camera-uses-proposed-position': ('#include "resource_loader.h"\n', '#include "resource_loader.h"\nstatic uint32_t m11_proposed_x;\n')}
INPUTS = [('released-at-contact', 4283, {0x3eaf10: (0, 4)}), ('reversed-at-contact', 4282, {0x3eaf10: (0x3f800000, 4)})]


def controls(lib, caps, obs, folder):
    folder.mkdir(parents=True, exist_ok=False); rows = []
    source = (ROOT / 'src/resource_loader.c').read_text()
    frames = {fr['counter']: fr for fr in obs['frames']}
    def measure(name, frame, d, kind):
        fr = frames[frame]
        first = None
        for label, actual, row in stage_pairs(d, fr):
            if actual is None: continue
            r = compare(actual, caps.load(row['name']))
            if r['result'] == 'failed' and first is None: first = dict(stage=label, different_bytes=r['different_bytes'], scratchpad=r['scratchpad']['different_bytes'])
        final = compare(d.mem, caps.load(fr['captures']['camera-return']['name']))
        ret = d.stage.get('constraint-return')
        return dict(control=name, kind=kind, frame=frame, result='rejected' if final['result'] == 'failed' else 'ESCAPED',
                    first_divergent_stage=first, comparison=final,
                    committed_x=f(ret.load(P + 0x30)) if ret else None, scratch_eye_x=f(d.mem.load(0x70000040)),
                    history_x=f(d.mem.load(P + 0x390)), result_49a=hex(d.mem.load(P + 0x49a, 2)))
    for name, frame, old, new in DEFECTS:
        try:
            mutated = source
            for o, n in [edit for edit in ((old, new), SECOND.get(name), PRELUDE.get(name)) if edit is not None]:
                if mutated.count(o) != 1: raise AssertionError(f'{name}: target not unique')
                mutated = mutated.replace(o, n)
            path = folder / (name + '.c'); path.open('x').write(mutated)
            mlib = library(source=path, output=folder / (name + '.so'))
            row = measure(name, frame, execute(mlib, caps, frames[frame], STATUS_VALUES[0]), 'compiled-defect')
        except Exception as exc: row = dict(control=name, kind='compiled-defect', frame=frame, result='error', detail=str(exc)[:500])
        rows.append(row); print(name, row['result'], flush=True)
        with (folder / (name + '.result.json')).open('x') as out: json.dump(row, out, indent=2); out.write('\n')
        for k in list(caps.cache): del caps.cache[k]
        gc.collect()
    for name, frame, mutate in INPUTS:
        try: row = measure(name, frame, execute(lib, caps, frames[frame], STATUS_VALUES[0], mutate=mutate), 'input-counterfactual')
        except Exception as exc: row = dict(control=name, kind='input-counterfactual', frame=frame, result='error', detail=str(exc)[:500])
        rows.append(row); print(name, row['result'], flush=True)
        with (folder / (name + '.result.json')).open('x') as out: json.dump(row, out, indent=2); out.write('\n')
        for k in list(caps.cache): del caps.cache[k]
        gc.collect()
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
        rows = list(fr['captures'].values()) + fr['foot_query_returns'] + fr['foot_pushes']
        for row in rows:
            if digest(caps.path(row['name'])) != row['sha256']: raise ValueError('Observation/capture digest mismatch')
            for shot in row['screenshots']:
                if digest(a.capture_dir.parent / 'stops' / shot['name']) != shot['sha256']: raise ValueError('Screenshot digest mismatch')
    caps.segments, _ = shared_module(a.ps2dinfo).segments_from_elf(caps.elfdata)
    for fr in obs['frames']:
        if caps.load(fr['captures']['dispatch-entry']['name']).load(0x46e118) != fr['counter']: raise ValueError('Wrong capture frame')
    caps.cache.clear()
    lib = library(output=a.output.with_suffix('.so')); result, executed = sequence(lib, caps, obs)
    with a.output.with_suffix('.baseline.json').open('x') as out: json.dump(result, out, indent=2); out.write('\n')
    if a.negative_controls: result['negative_controls'] = controls(lib, caps, obs, a.output.parent / (a.output.stem + '-controls'))
    ok = all(s['result'] == 'passed' for s in result['stages'] + result['handoffs'] + result['status_invariance'])
    ok &= all(result['operation_checks'].values())
    ok &= all(c['result'] == 'rejected' for c in result.get('negative_controls', []))
    result.update(schema='hg-player-constraint-runtime/v1', result='passed' if ok else 'failed', elf_sha256=EXPECTED,
        observation_sha256=digest(OBSERVATION),
        verification_sources={n: digest(ROOT / n) for n in ('src/resource_loader.c', 'src/resource_loader.h', 'tools/ee_subset.py',
            'tools/verify_player_constraint_runtime.py', 'tools/verify_player_connected_runtime.py', 'tools/verify_player_turn_runtime.py')},
        continuity_ranges=[dict(address=hex(x), size=n) for x, n in CONTINUITY],
        limitations=['Hybrid C and authenticated original-instruction-modeled leaves; not whole-frame reconstruction.',
            'Each frame starts from an independent original dispatcher-entry capture with calculated player/camera structures carried forward.',
            'Guest stack 01fe0000..02000000 excluded; asynchronous libpad DMA excluded only if untouched by execution.',
            'CP0 Status (uncaptured) is supplied with EIE set and clear; memory must be identical.',
            'PCSX2 v2.8.2 functional FP defaults; no exceptional FP, flags or hardware timing.'])
    with a.output.open('x') as out: json.dump(result, out, indent=2); out.write('\n')
    print(result['result'], result['operation_checks'])
    if not ok: raise SystemExit(1)


if __name__ == '__main__': main()
