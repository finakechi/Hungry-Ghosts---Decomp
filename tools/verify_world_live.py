#!/usr/bin/env python3
"""M25 live continuity verifier (owner-local, read-only).

Consumes one continuous original PCSX2 v2.8.2 run of the Japanese SCPS-15046
build (isolated profile, debugger execute breakpoints, read-only PINE) that
stopped at:

  s0  0012f850  first AREA1 coordinator entry (tick 0)
  s1  001585c8  return from 00158a10 (player initialization)
  s2  001585e0  return from 001e3440 (state-0 floor snap)
  s5  0012f8d4  return from 0012c790 (first camera follow, tick 0)
  t1..t3        0012f850 entries of ticks 1-3 (first state-1 frames)
  s7, s8        0012f850 entries of ticks 160 and 200 (walk window)
  walk.jsonl    regional records of every 0012f850 entry, ticks 114-200

Each full capture is a 32 MiB EE + 16 KiB scratchpad image whose SHA-256 and
debugger screenshot hash are recorded at capture time; this verifier
recomputes them. It then checks, from the captures alone:

  * the run's tick-0 stop equals M21 area1-active and the M8 00158a10 entry
    (separate runs) in every world-setup domain;
  * the same-run 00158a10 return equals the M8 return, and C
    hg_player_initialize_fixed applied to the same-run tick-0 state
    reproduces the same-run return;
  * original ELF instructions (functional EE subset) executed from the
    same-run tick-0 state through 00158560 and 0012c790 reproduce the
    same-run state at 0012f8d4 in every domain;
  * counter continuity across all stops and a contiguous walk window.

Stops at 001586c8 and 0012f8cc were passed without capture (a resume
misfire) and are reported as unobserved; nothing is spliced from other runs.
"""
import argparse
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

PLAYER = 0x3f92d0
STOPS = (
    ('s0-coordinator-t0', '0012f850', 'first AREA1 coordinator entry (tick 0)'),
    ('s1-00158a10-return', '001585c8', 'return from 00158a10'),
    ('s2-001e3440-return', '001585e0', 'return from 001e3440 floor snap'),
    ('s5-0012c790-return-t0', '0012f8d4', 'return from first 0012c790 camera follow'),
    ('s6-coordinator-t1', '0012f850', 'tick 1 coordinator entry'),
    ('s6-coordinator-t2', '0012f850', 'tick 2 coordinator entry'),
    ('s6-coordinator-t3', '0012f850', 'tick 3 coordinator entry'),
    ('s7-walk-t160', '0012f850', 'tick 160 coordinator entry (forward held)'),
    ('s8-release-t200', '0012f850', 'tick 200 coordinator entry'),
)
UNOBSERVED_STOPS = ('001586c8 (return from 00158470)', '0012f8cc (00158560 return before the camera follow)')
EXPECTED_COUNTERS = {'s0-coordinator-t0': 0, 's1-00158a10-return': 1, 's2-001e3440-return': 1,
                     's5-0012c790-return-t0': 1, 's6-coordinator-t1': 1, 's6-coordinator-t2': 2,
                     's6-coordinator-t3': 3, 's7-walk-t160': 160, 's8-release-t200': 200}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def load_stop(live: Path, name: str):
    full = live / 'full'
    meta = json.loads((full / f'{name}.json').read_text())
    ee = zlib.decompress((full / f'{name}.ee.z').read_bytes())
    sp = zlib.decompress((full / f'{name}.sp.z').read_bytes())
    png = (full / f'{name}-debugger.png').read_bytes()
    problems = []
    if len(ee) != 0x2000000 or len(sp) != 0x4000:
        problems.append('size')
    if _sha(ee) != meta['ee_sha256'] or _sha(sp) != meta['sp_sha256']:
        problems.append('image hash')
    if _sha(png) != meta['debugger_png_sha256']:
        problems.append('debugger screenshot hash')
    m = meta['metadata']
    if not (m == meta['metadata_after'] and m['status'] == 1 and m['serial'] == 'SCPS-15046'
            and m['uuid'] == '91aa4b28' and m['version'] == 'PCSX2 v2.8.2'):
        problems.append('emulator identity or paused state')
    return meta, ee, sp, problems


def inspect_live(elf_path, native_dir, m8_entry, m8_return, live, ps2dinfo):
    import verify_world_session as W
    from resource_execution import ORIGINAL_ELF
    from verify_world_init import _load_m21_capture
    from verify_player_connected_runtime import CapturedMemory, ConnectedRuntimeDriver
    from verify_resource_loader import shared_module
    from resource_harness import ResourceEnv, library
    from ee_subset import Machine

    elf = elf_path.read_bytes()
    if _sha(elf) != ORIGINAL_ELF:
        raise ValueError('ELF is not the authenticated SCPS-15046 executable')
    stops, images = [], {}
    for name, pc, what in STOPS:
        meta, ee, sp, problems = load_stop(live, name)
        if problems:
            raise ValueError(f'{name}: {", ".join(problems)}')
        if meta['expected_pc'] != pc:
            raise ValueError(f'{name}: recorded stop {meta["expected_pc"]} is not {pc}')
        images[name] = (ee, sp)
        f = W._field_summary(ee, sp)
        stops.append(dict(name=name, pc=pc, stop=what, ee_sha256=meta['ee_sha256'], sp_sha256=meta['sp_sha256'],
                          debugger_png_sha256=meta['debugger_png_sha256'], fields=f))
    counters = {s['name']: s['fields']['counters']['3fa190'] for s in stops}
    continuity = dict(counters=counters, expected=EXPECTED_COUNTERS, ordered=counters == EXPECTED_COUNTERS,
                      emulator=json.loads((live / 'full' / f'{STOPS[0][0]}.json').read_text())['metadata']['version'])

    _, act_ee, _ = _load_m21_capture(native_dir, 'area1-active')
    m8e = bytes(CapturedMemory.load_capture(m8_entry).raw)
    m8r = bytes(CapturedMemory.load_capture(m8_return).raw)
    s0, sp0 = images['s0-coordinator-t0']
    s1 = images['s1-00158a10-return'][0]
    s5 = images['s5-0012c790-return-t0'][0]
    cross = dict(
        claim='same-run stops compared with separate-run captures; equality is checkpoint equivalence',
        tick0_vs_m21_area1_active=W._diff(s0, act_ee, W.DOMAINS),
        tick0_vs_m8_entry=W._diff(s0, m8e, W.DOMAINS),
        return_00158a10_vs_m8_return=W._diff(s1, m8r, W.BRIDGE_DOMAINS),
    )

    segments, _ = shared_module(ps2dinfo).segments_from_elf(elf)
    base = Path(os.environ.get('HG_VERIFY_BUILD_DIR', ROOT / 'local/build'))
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='m25-live-', dir=base) as tmp:
        lib = library(output=Path(tmp) / 'm25-live.so')
        mem = CapturedMemory(s0, sp0)
        drv = ConnectedRuntimeDriver(mem, elf, segments)
        lib.hg_player_initialize_fixed.argtypes = [C.POINTER(ResourceEnv), C.c_uint32]
        lib.hg_player_initialize_fixed.restype = None
        lib.hg_player_initialize_fixed(C.byref(drv.env), PLAYER)
        if drv.errors:
            raise ValueError(f'bridge driver errors: {drv.errors}')
        c_bridge = dict(claim='C 00158a10 applied to the same-run tick-0 state vs the same-run return',
                        leaves=[c['entry'] for c in drv.modeled_calls],
                        domains=W._diff(bytes(drv.mem.raw), s1, W.BRIDGE_DOMAINS))

    def oracle(ee, sp, pc):
        cm = CapturedMemory(ee, sp)
        d = ConnectedRuntimeDriver(cm, elf, segments)
        m = Machine()
        m.mem = cm
        m.reg[28], m.reg[29] = 0x3e7f70, 0x1ff0800
        m.hooks = {t: (lambda cpu, t=t: d.external(t, *(cpu.reg[i] & 0xffffffff for i in (4, 5, 6, 7))))
                   for t in (0x119910, 0x119ac8)}
        m.run(pc, limit=20_000_000)
        d.verify_executed(m.executed)
        return cm, dict(entry=f'{pc:08x}', instructions=m.steps, distinct_addresses=len(m.executed))
    a, run0 = oracle(s0, sp0, 0x158560)
    b, run1 = oracle(bytes(a.raw), bytes(a.scratch), 0x12c790)
    original = dict(claim='original ELF instructions from the same-run tick-0 state vs the same-run 0012f8d4 stop',
                    state0=run0, camera=run1, domains=W._diff(bytes(b.raw), s5, W.DOMAINS))

    walk_path = live / 'walk.jsonl'
    walk_bytes = walk_path.read_bytes()
    rows = [json.loads(line) for line in walk_bytes.splitlines() if line.strip()]
    ticks = [r['c3fa190'] for r in rows]
    table = []
    for r in rows:
        pl, pad, fl = bytes.fromhex(r['player']), bytes.fromhex(r['pad']), bytes.fromhex(r['flags'])
        x, y, z = struct.unpack_from('<3f', pl, 0x30)
        table.append([r['c3fa190'], round(x, 5), round(y, 5), round(z, 5),
                      round(struct.unpack_from('<f', pl, 0x44)[0], 5), fl[0xe],
                      round(struct.unpack_from('<f', pad, 0x0c)[0], 4), round(struct.unpack_from('<f', pad, 0x10)[0], 4)])
    steps = [((b[1] - a[1]) ** 2 + (b[3] - a[3]) ** 2) ** 0.5 for a, b in zip(table, table[1:])]
    dys = [b[2] - a[2] for a, b in zip(table, table[1:])]
    by_tick = {r['c3fa190']: r for r in rows}
    walk = dict(
        walk_sha256=_sha(walk_bytes), rows=len(rows), first=ticks[0], last=ticks[-1],
        contiguous=ticks == list(range(ticks[0], ticks[0] + len(ticks))),
        pc_panel_hashes=len({r['dis_sha256'] for r in rows}),
        matches_full_captures={t: bytes.fromhex(by_tick[t]['player']) ==
                               images[n][0][PLAYER:PLAYER + 0x520] for t, n in ((160, 's7-walk-t160'),
                                                                                (200, 's8-release-t200'))},
        columns=['tick', 'x', 'y', 'z', 'rot_y', 'input_block_3f9f6e', 'stick_x', 'stick_y'], table=table,
        max_step=round(max(steps), 5), max_abs_dy=round(max(abs(d) for d in dys), 5),
        floor_change_ticks=sum(1 for d in dys if abs(d) > 1e-4),
        input_phases='stick (003eaf0c, 003eaf10): forward 114-160, forward + left 161-185, forward 186-200 '
                     '(left released; the forward release was not delivered, so no neutral frame was observed)',
    )

    s = {x['name']: x['fields'] for x in stops}
    checks = dict(
        tick0_equals_area1_active=all(v['differing'] == 0 for v in cross['tick0_vs_m21_area1_active'].values()),
        tick0_equals_m8_entry=all(v['differing'] == 0 for v in cross['tick0_vs_m8_entry'].values()),
        same_run_return_equals_m8_return=all(v['differing'] == 0
                                             for v in cross['return_00158a10_vs_m8_return'].values()),
        c_00158a10_reproduces_same_run_return=all(v['differing'] == 0 for v in c_bridge['domains'].values()),
        original_instructions_reproduce_0012f8d4=all(v['differing'] == 0 for v in original['domains'].values()),
        counters_ordered=continuity['ordered'], walk_contiguous=walk['contiguous'] and walk['pc_panel_hashes'] == 1
        and all(walk['matches_full_captures'].values()),
        ordinary_entry=s['s5-0012c790-return-t0']['player']['+18'] == 1
        and images['s5-0012c790-return-t0'][0][0x3f9f39] == 0
        and images['s5-0012c790-return-t0'][0][0x3e7f70 + 0x2f78] == 0,
    )
    observations = dict(
        floor_snap_y=s['s2-001e3440-return']['player']['pos'][1],
        phase_18_after_state0=s['s5-0012c790-return-t0']['player']['+18'],
        input_block_3f9f6e_after_state0=s['s5-0012c790-return-t0']['input_block_3f9f6e'],
        history_390=s['s5-0012c790-return-t0']['player']['hist_390'],
        eye_380=s['s5-0012c790-return-t0']['player']['eye_380'],
        camera_after_first_follow=s['s5-0012c790-return-t0']['camera'],
        nodes_after_00158a10=s['s1-00158a10-return']['nodes'],
        nodes_tick2=s['s6-coordinator-t2']['nodes'],
        input_block_released_by_tick=walk['first'] if table[0][5] == 0 else None,
        first_state1_frames='ticks 1-3: position, phase 1 and camera held; eye y 54.54541/54.545532/54.54541',
    )
    return {
        'schema': 'hg-m25-live-continuity/v1', 'reference_build': 'SCPS-15046', 'pcsx2_crc': '91AA4B28',
        'elf_sha256': ORIGINAL_ELF, 'synthetic_only': False,
        'run': dict(claim='one continuous original PCSX2 run; every stop below is from this run',
                    emulator=continuity['emulator'], profile='isolated (memory cards disabled, save state on '
                    'shutdown disabled, audio muted)', method='debugger execute breakpoints, read-only PINE',
                    route='boot, no-card continue, title, New Game, name, no-save continue, AREA1'),
        'stops': stops, 'unobserved_stops': list(UNOBSERVED_STOPS),
        'continuity': continuity, 'cross_run': cross, 'c_bridge_same_run': c_bridge,
        'original_instructions_same_run': original, 'walk': walk, 'observations': observations, 'checks': checks,
        'passed': all(checks.values()),
        'remaining_unobserved': [
            '00158470 return register (branch effects observed: 003f9f39 = 0, gp+2f78 = 0)',
            'ticks 4-113 (walk start, fade end and input-block release)',
            'neutral stick release', 'wall contact on the retail collision mesh',
        ],
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ('elf', 'native', 'm8-entry', 'm8-return', 'live', 'ps2dinfo'):
        p.add_argument(f'--{name}', type=Path, required=True)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    result = inspect_live(a.elf, a.native, a.m8_entry, a.m8_return, a.live, a.ps2dinfo)
    text = json.dumps(result, indent=1) + '\n'
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(text)
    else:
        sys.stdout.write(text)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
