#!/usr/bin/env python3
"""M25 world initialization and state continuity verifier.

Two strictly separated modes:

  --synthetic
      Builds tests/world_session_driver.c with the M25 session model, runs it
      (optionally under ASan/UBSan/LSan), then re-derives every claimed
      property in Python from the emitted per-frame trace: counter
      continuity, camera geometry, bounds, floor response, movement phases,
      cycle-relative parity, coordinator order, control detection and the
      invalid-input table. No retail input is read. The report also states
      whether it equals the tracked receipt evidence/m25/world-session.json.

  --inspect-authentic
      Read-only owner-local research on the authenticated Japanese SCPS-15046
      ISO/ELF and private captures: identity beyond the ELF hash, an
      original-bytes characterization of the 00158560 state-0 path, its tail
      and 0012f850, a field-level comparison of area1-request, area1-active,
      obs3-000 and the M8 00158a10 entry/return pair, the M8 cross-run bridge,
      original-instruction (functional EE subset) execution of the remaining
      state-0 chain, the rotation-helper conventions, the 00158560 fault 0x30
      reproduction, and the earliest unsupported dependency. Separate runs are
      compared domain by domain and are never presented as one continuous run.
"""
import argparse
import ctypes as C
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'tests')]

EVIDENCE = ROOT / 'evidence/m25/world-session.json'
AUTHENTIC_EVIDENCE = ROOT / 'evidence/m25/authentic-dependencies.json'
C_SOURCES = (
    'tests/world_session_driver.c', 'src/scene/world_init.c', 'src/front_end.c',
    'src/game_lifecycle.c', 'src/resource_loader.c', 'src/disc_read.c',
)
PLAYER, CAMERA = 0x3f92d0, 0x3f9860
SPAWN_BIAS = 0.05235988
SEGMENTS = ('neutral', 'walk-floor', 'turn', 'wall-slide', 'release', 'return-turn', 'hold')


# ----------------------------------------------------------------------------
# Synthetic mode
# ----------------------------------------------------------------------------

def build_driver(output: Path, compiler: str, sanitize: bool) -> None:
    flags = ['-std=c11', '-Wall', '-Wextra', '-Werror', '-pedantic', '-ffp-contract=off']
    flags += (['-g', '-O1', '-fno-omit-frame-pointer', '-fsanitize=address,undefined',
               '-fno-sanitize-recover=all'] if sanitize else ['-O2'])
    cmd = [compiler, *flags, '-I', str(ROOT / 'src'), *[str(ROOT / s) for s in C_SOURCES],
           '-lm', '-o', str(output)]
    subprocess.run(cmd, cwd=ROOT, check=True, timeout=180)


def _f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


def _floor(scene, x, z):
    y = scene['floor_y']
    for t in scene['tiers']:
        if t[0] <= x <= t[1] and t[2] <= z <= t[3] and t[4] > y:
            y = t[4]
    return y


def _wrap(a):
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a


def rederive(report: dict, coordinator_order: list | None) -> dict:
    """Independent Python re-derivation of the driver's claims from its trace."""
    problems = []

    def need(cond, msg):
        if not cond:
            problems.append(msg)

    scenes = {s['name']: s for s in report['scenes']}
    need(len(scenes) == 2, 'two scenes')
    a, b = report['scenes']
    need(a['spawn'] != b['spawn'] and a['player_bones'] != b['player_bones']
         and a['props'] != b['props'] and a['player_arena'] != b['player_arena']
         and a['tiers'] != b['tiers'], 'scenes do not differ in spawn, geometry and allocation')
    script = report['script']
    need(sum(s['frames'] for s in script) >= 120, 'script shorter than 120 updates')
    if coordinator_order is not None:
        need(report['checker_coordinator_order'] == coordinator_order,
             'driver coordinator order differs from the ELF-derived order')

    derived_cycles = []
    for c in report['cycles']:
        scene = scenes[c['scene']]
        fields = c['trace_fields']
        rows = [dict(zip(fields, r)) for r in c['trace']]
        tag = f"{c['scene']}#{c['cycle']}"
        need(c['failures'] == [], f'{tag}: driver reported failures')
        need(c['enter'] == 'OK' and c['step'] == 'OK' and c['teardown'] == 'OK', f'{tag}: API errors')
        ready = [r for r in rows if r['post_ready'] > 0]
        fade = [r for r in rows[1:] if r['post_ready'] == 0]
        need(len(ready) >= 120 and ready[-1]['post_ready'] == len(ready), f'{tag}: {len(ready)} post-readiness updates')
        need(55 <= len(fade) <= 70 and rows[0]['fade'] != 0 and fade[-1]['fade'] == 0, f'{tag}: fade of {len(fade)} frames')
        need(rows[0]['readiness'] == 5 and all(r['readiness'] == 6 for r in ready), f'{tag}: readiness history')
        per_seg = {}
        for r in ready:
            per_seg[SEGMENTS[r['segment']]] = per_seg.get(SEGMENTS[r['segment']], 0) + 1
        want = {}
        for s in script:
            want[s['segment']] = want.get(s['segment'], 0) + s['frames']
        need(per_seg == want, f'{tag}: segment frame counts {per_seg}')
        floor_changes = wall_frames = slide_frames = 0
        for i, r in enumerate(rows):
            eye = (r['eye_x'], r['eye_y'], r['eye_z'])
            head = (r['x'], _f32(r['y'] + scene['head_offset']), r['z'])
            need(all(abs(e - h) <= 1e-3 for e, h in zip(eye, head)), f'{tag} step {r["step"]}: eye off head')
            d = (r['tgt_x'] - eye[0], r['tgt_y'] - eye[1], r['tgt_z'] - eye[2])
            boom = (20 * math.sin(r['yaw']) * math.cos(r['pitch']), -20 * math.sin(r['pitch']),
                    20 * math.cos(r['yaw']) * math.cos(r['pitch']))
            need(all(abs(x - y) <= 2e-3 for x, y in zip(d, boom)), f'{tag} step {r["step"]}: boom geometry')
            need(abs(r['pitch'] - SPAWN_BIAS) <= 1e-4, f'{tag} step {r["step"]}: pitch')
            need(abs(_wrap(r['yaw'] - r['rot_y'])) <= 2e-2, f'{tag} step {r["step"]}: camera heading')
            w = scene['walls']
            need(w[0] - 0.05 <= r['x'] <= w[1] + 0.05 and w[2] - 0.05 <= r['z'] <= w[3] + 0.05,
                 f'{tag} step {r["step"]}: outside walls')
            if r['grounded'] == 1:
                need(abs(r['y'] - _floor(scene, r['x'], r['z'])) <= 1e-4, f'{tag} step {r["step"]}: floor')
            if i:
                p = rows[i - 1]
                need(r['c3fa190'] == p['c3fa190'] + 1 and r['coord'] == p['coord'] + 1
                     and r['c3eaef0'] == p['c3eaef0'] + 1, f'{tag} step {r["step"]}: dropped update')
                step = math.hypot(r['x'] - p['x'], r['z'] - p['z'])
                need(step <= 1.0 and abs(r['y'] - p['y']) <= 1.8, f'{tag} step {r["step"]}: discontinuity')
                if r['walls'] > p['walls']:
                    wall_frames += 1
                    slide_frames += step > 0.01
                floor_changes += abs(_floor(scene, r['x'], r['z']) - _floor(scene, p['x'], p['z'])) > 1e-4
        need(slide_frames > 0 and floor_changes > 0, f'{tag}: no wall slide or floor response')
        need({'wall_contact_frames': wall_frames, 'slide_frames': slide_frames, 'floor_changes': floor_changes}
             == c['motion'], f'{tag}: motion summary differs from the trace')
        td = c['after_teardown']
        need(td == {'free_nodes': 2048, 'owned_nodes': 0, 'pool_free': [286, 40, 256]}, f'{tag}: teardown pools')
        need(c['first_tick']['player_nodes'] == scene['player_bones']
             and c['first_tick']['owned_nodes'] == scene['player_bones'] + sum(p[0] for p in scene['props'])
             and c['first_tick']['free_nodes'] == 2048 - c['first_tick']['owned_nodes'], f'{tag}: first-tick nodes')
        need(c['first_tick']['main'] == scene['root_actors'] + sum(1 for p in scene['props'] if p[1] == 0)
             and c['first_tick']['priority'] == sum(1 for p in scene['props'] if p[1] == 1)
             and c['first_tick']['type2'] == 3, f'{tag}: first-tick actors')
        need(c['setup_effects_in_order'] == 12 and c['coordinator_ticks_checked'] == len(rows),
             f'{tag}: setup or coordinator order')
        derived_cycles.append(dict(scene=c['scene'], cycle=c['cycle'], post_ready_updates=len(ready),
                                   fade_frames=len(fade), floor_changes=floor_changes,
                                   slide_frames=slide_frames,
                                   trace_sha256=hashlib.sha256(json.dumps(c['trace']).encode()).hexdigest()))

    def rebased(c):
        rows = [dict(zip(c['trace_fields'], r)) for r in c['trace']]
        base = rows[0]
        keys = ('c3fa190', 'c46e118', 'c46d930', 'c3eaef0', 'coord', 'walls', 'floors')
        return [{k: (r[k] - base[k] if k in keys else r[k]) for k in r} for r in rows]

    cyc = report['cycles']
    parity = {}
    for i, j in ((0, 2), (1, 3)):
        same = rebased(cyc[i]) == rebased(cyc[j]) and cyc[i]['ledger_by_op'] == cyc[j]['ledger_by_op']
        parity[cyc[i]['scene']] = same
        need(same, f'{cyc[i]["scene"]}: cycle 2 differs from cycle 1')

    controls = report['controls']
    need(len(controls) == 14, 'control count')
    for k in controls:
        fired = k['fired'].split(',') if k['fired'] else []
        ok = k['intended'] in fired and (k['forbidden'] is None or k['forbidden'] not in fired)
        need(ok and k['detected'] is True, f'control {k["name"]} not detected as intended')
    invalid = report['invalid_inputs']
    need(len(invalid) >= 30 and all(i['expected'] == i['actual'] for i in invalid), 'invalid input table')
    need(report['summary']['ok'] is True and report['ledgers']['provenance']['unknown'] == 0, 'summary/ledger')
    own = report['ledgers']['ownership']
    need(own['actor_alloc'] == own['actor_release'] and own['node_alloc'] == own['node_release'],
         'ownership ledger unbalanced')
    return dict(problems=problems, cycles=derived_cycles, parity=parity,
                controls_detected=sum(1 for k in controls if k['detected']),
                invalid_inputs_rejected=sum(1 for i in invalid if i['expected'] == i['actual']))


def receipt(report: dict, derived: dict) -> dict:
    """Tracked receipt: the report minus raw traces, plus their digests."""
    slim = json.loads(json.dumps(report))
    for c, d in zip(slim['cycles'], derived['cycles']):
        rows = [dict(zip(c['trace_fields'], r)) for r in c['trace']]
        marks = [0]
        for i in range(1, len(rows)):
            if rows[i]['segment'] != rows[i - 1]['segment'] or rows[i]['readiness'] != rows[i - 1]['readiness']:
                marks.append(i - 1)
                marks.append(i)
        marks.append(len(rows) - 1)
        c['trace_sha256'] = d['trace_sha256']
        c['sampled_rows'] = {str(i): c['trace'][i] for i in sorted(set(marks))}
        del c['trace']
    slim['rederived'] = {k: v for k, v in derived.items() if k != 'problems'}
    return slim


def tracked_coordinator_order():
    if AUTHENTIC_EVIDENCE.exists():
        return json.loads(AUTHENTIC_EVIDENCE.read_text())['original_bytes']['coordinator_0012f850']['order']
    return None


def verify_synthetic(compiler='cc', sanitize=False, build_dir=None) -> dict:
    base = build_dir or Path(os.environ.get('HG_VERIFY_BUILD_DIR', ROOT / 'local/build'))
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='m25-world-session-', dir=base) as tmp:
        binary = Path(tmp) / 'world_session_driver'
        build_driver(binary, compiler, sanitize)
        env = dict(os.environ)
        if sanitize:
            env['ASAN_OPTIONS'] = 'detect_leaks=1:halt_on_error=1'
            env['UBSAN_OPTIONS'] = 'halt_on_error=1:print_stacktrace=1'
        proc = subprocess.run([str(binary), '--json'], capture_output=True, text=True, env=env, timeout=300)
    row = dict(schema='hg-m25-world-session-verification/v1', compiler=Path(compiler).name, sanitized=sanitize,
               exit_code=proc.returncode, stderr=proc.stderr, failure=None)
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        row.update(failure=f'driver output is not JSON: {exc}', report=None, matches_evidence=False)
        return row
    derived = rederive(report, tracked_coordinator_order())
    slim = receipt(report, derived)
    row['report'] = slim
    row['problems'] = derived['problems']
    if derived['problems']:
        row['failure'] = f'{len(derived["problems"])} re-derived checks failed'
    row['matches_evidence'] = EVIDENCE.exists() and json.loads(EVIDENCE.read_text()) == slim
    return row


# ----------------------------------------------------------------------------
# Authentic mode
# ----------------------------------------------------------------------------

def _word(elf, va):
    return struct.unpack_from('<I', elf, va - 0xff800)[0]


def _decode(w):
    op, rs, rt = w >> 26, (w >> 21) & 31, (w >> 16) & 31
    imm, simm = w & 0xffff, struct.unpack('<h', struct.pack('<H', w & 0xffff))[0]
    return op, rs, rt, imm, simm


LOADS = {32: 'lb', 33: 'lh', 36: 'lbu', 37: 'lhu', 35: 'lw', 30: 'lq'}
STORES = {40: 'sb', 41: 'sh', 43: 'sw', 31: 'sq'}


def _mem_ops(elf, start, end):
    """Absolute or s0-relative memory operations with width and signedness."""
    ops, lui = [], {}
    for va in range(start, end, 4):
        w = _word(elf, va)
        op, rs, rt, imm, simm = _decode(w)
        if op == 15:
            lui[rt] = imm << 16
            continue
        name = LOADS.get(op) or STORES.get(op)
        if not name:
            continue
        if rs == 1 and 1 in lui:
            target = f'{(lui[1] + simm) & 0xffffffff:08x}'
        elif rs == 16:
            target = f'PLAYER+{simm:#x}'
        elif rs == 28:
            target = f'gp+{simm:#x}'
        else:
            target = f'r{rs}+{simm:#x}'
        ops.append(dict(va=f'{va:08x}', op=name, target=target))
    return ops


def _jals(elf, start, end):
    out = []
    for va in range(start, end, 4):
        w = _word(elf, va)
        if w >> 26 == 3:
            target = ((va + 4) & 0xf0000000) | ((w & 0x03ffffff) << 2)
            slot = _word(elf, va + 4)
            out.append(dict(va=f'{va:08x}', target=f'{target:08x}', delay_slot=f'{slot:08x}'))
    return out


def characterize(elf: bytes) -> dict:
    need = []

    def expect(cond, msg):
        if not cond:
            need.append(msg)

    # 001585c0 state-0 call sequence.
    jals = _jals(elf, 0x1585c0, 0x1586f8)
    targets = [j['target'] for j in jals]
    expect(targets[:2] == ['00158a10', '001e3440'], 'state-0 first calls')
    expect(_word(elf, 0x1585c4) == 0x0200202d and _word(elf, 0x1585dc) == 0x0200202d,
           'a0 = s0 (PLAYER, 64-bit daddu) in both delay slots')
    lui_f12 = _decode(_word(elf, 0x1585c8))[3] << 16
    lui_f13 = _decode(_word(elf, 0x1585cc))[3] << 16
    expect(lui_f12 == 0x41000000 and lui_f13 == 0xc0400000, 'floor-snap constants')
    expect(_word(elf, 0x1585d0) >> 21 == 0x224 and _word(elf, 0x1585d4) >> 21 == 0x224, 'mtc1 before the jal')
    slot_158470 = _word(elf, 0x1586c4)
    expect(_word(elf, 0x1586c0) >> 26 == 3 and slot_158470 == 0xa2030018, 'phase store in 00158470 delay slot')
    expect(_word(elf, 0x1585e4) == 0x24030001, 'v1 = 1 before the selector')
    selector = [o for o in _mem_ops(elf, 0x1585e0, 0x1586f8) if o['op'] in ('lbu', 'lb', 'sb')]
    # Common tail 00158968..00158a0c.
    tail_ops = _mem_ops(elf, 0x158968, 0x158a10)
    tail_widths = {o['target']: o['op'] for o in tail_ops}
    expect(tail_widths.get('PLAYER+0x4ac') == 'lb' and tail_widths.get('PLAYER+0x45a') == 'lh'
           and tail_widths.get('PLAYER+0x464') == 'lbu' and tail_widths.get('003f9f6e') == 'sb',
           'tail widths and signedness')
    expect(_word(elf, 0x15897c) == 0x46000b01 and _jals(elf, 0x158978, 0x15897c)[0]['target'] == '001204e0',
           'sub.s f12 in the 001204e0 delay slot')
    expect(_word(elf, 0x1589f8) >> 26 == 30 and _word(elf, 0x1589fc) >> 26 == 31, 'lq/sq history copy')
    # 0012f850 coordinator: linear jal sequence, only a counter guard branch.
    cj = _jals(elf, 0x12f850, 0x12f948)
    branches = []
    for va in range(0x12f850, 0x12f948, 4):
        op, rs, rt, imm, simm = _decode(_word(elf, va))
        if op in (1, 4, 5, 6, 7, 20, 21, 22, 23) or (op == 0 and (_word(elf, va) & 0x3f) in (8, 9)):
            branches.append(dict(va=f'{va:08x}', target=f'{(va + 4 + simm * 4) & 0xffffffff:08x}' if op else 'jr'))
    jal_vas = [int(j['va'], 16) for j in cj]
    # A forward branch skips [va + 8, target); no jal may lie in that range.
    expect(all(b['target'] == 'jr' or not any(int(b['va'], 16) + 8 <= v < int(b['target'], 16) for v in jal_vas)
               for b in branches), 'a branch skips coordinator services')
    delay_args = {j['target']: j['delay_slot'] for j in cj}
    return dict(
        problems=need,
        state0_001585c0=dict(
            jump_table_003db540_0=f'{_word(elf, 0x3db540):08x}',
            calls=jals,
            a0_delay_slots='daddu a0, s0, zero (PLAYER, 64-bit move) at 001585c4 and 001585dc',
            floor_snap_arguments=dict(f12=f'{lui_f12:08x}', f13=f'{lui_f13:08x}', f12_value=8.0, f13_value=-3.0,
                                      loaded='lui/mtc1 at 001585c8..001585d4, before jal 001e3440 at 001585d8'),
            selector_memory_ops=selector,
            ordinary_entry='jal 00158470 at 001586c0 with sb v1(=1), 0x18(s0) in its delay slot',
        ),
        tail_00158968=dict(memory_ops=tail_ops,
                           f12='sub.s f12, f1, f0 (+3c0 - 0x3d567750) in the jal 001204e0 delay slot',
                           history_copy='lq/sq +30 -> +390 (four lanes)'),
        coordinator_0012f850=dict(order=[j['target'] for j in cj], calls=cj, branches=branches,
                                  delay_slot_arguments={k: v for k, v in delay_args.items()
                                                        if k in ('0017f6d0', '0017f2d0', '0013b6e0', '0013ce80')}),
    )


def _field_summary(ee: bytes, sp: bytes | None) -> dict:
    r32 = lambda a: struct.unpack_from('<I', ee, a)[0]
    f = lambda a: round(struct.unpack_from('<f', ee, a)[0], 6)

    def lst(head):
        out, cur = [], r32(head)
        while cur and len(out) < 64:
            out.append(f'{cur:08x}')
            cur = r32(cur + 0x24)
        return out
    return {
        'mode': [f'{r32(0x3ead34):08x}', ee[0x3ead38], ee[0x3ead39], ee[0x3ead3a]],
        'area_triple': list(ee[0x3fa1b0:0x3fa1b3]),
        'selector_0046e106': ee[0x46e106], 'selector_3fa1bb': ee[0x3fa1bb],
        'player': {
            '+00': ee[PLAYER], '+01': ee[PLAYER + 1], '+18': ee[PLAYER + 0x18], '+19': ee[PLAYER + 0x19],
            '+2a': struct.unpack_from('<H', ee, PLAYER + 0x2a)[0],
            'pos': [f(PLAYER + 0x30 + 4 * i) for i in range(4)],
            'rot': [f(PLAYER + 0x40 + 4 * i) for i in range(3)],
            '+3c0': f(PLAYER + 0x3c0), 'eye_380': [f(PLAYER + 0x380 + 4 * i) for i in range(3)],
            'hist_390': [f(PLAYER + 0x390 + 4 * i) for i in range(3)],
            'model': f'{r32(PLAYER + 0xa0):08x}', 'nodes': r32(PLAYER + 0xe4),
            '+2c3': ee[PLAYER + 0x2c3], '+464': ee[PLAYER + 0x464], '+4a0': ee[PLAYER + 0x4a0],
        },
        'camera': {'eye': [f(CAMERA + 4 * i) for i in range(3)], 'target': [f(CAMERA + 0x10 + 4 * i) for i in range(3)],
                   'pitch': f(CAMERA + 0xd0), 'yaw': f(CAMERA + 0xd4)},
        'input_block_3f9f6e': ee[0x3f9f6e],
        'handles': {k: f'{r32(0x3f4300 + i * 4):08x}' for k, i in (('0x3f', 0x3f), ('0x96', 0x96), ('0x22f', 0x22f))},
        'nodes': {'free': r32(0x4d8144), 'top': f'{r32(0x4d8140):08x}'},
        'actor_free': [r32(0x53943c + 4 * i) for i in range(3)],
        'lists': {'main': lst(0x539448), 'priority': lst(0x539450), 'type2': lst(0x539458)},
        'fade': list(ee[0x3eaeb0:0x3eaeb8]),
        'counters': {'3fa190': r32(0x3fa190), '46e118': r32(0x46e118), '46d930': r32(0x46d930)},
        'scratch_sampled': sp is not None,
    }


DOMAINS = {
    'player_003f92d0': (0x3f92d0, 0x520), 'camera_003f9860': (0x3f9860, 0x150),
    'world_env_003f9e30': (0x3f9e30, 0x250), 'block_003f99b0': (0x3f99b0, 0x480),
    'area_map_003f97f0': (0x3f97f0, 0x64), 'handle_table_003f4300': (0x3f4300, 0xa3c),
    'node_pool_and_stack': (0x46e140, 0x4d8148 - 0x46e140), 'actor_pools': (0x4d8150, 0x539468 - 0x4d8150),
    'fade_003eaeb0': (0x3eaeb0, 8), 'mode_003ead30': (0x3ead30, 0x10), 'area1_overlay_00a87800': (0xa87800, 0x7800),
}
BRIDGE_DOMAINS = ('player_003f92d0', 'camera_003f9860', 'world_env_003f9e30', 'handle_table_003f4300',
                  'node_pool_and_stack', 'actor_pools')


def _diff(x, y, names):
    out = {}
    for name in names:
        a, n = DOMAINS[name]
        d = sum(1 for i in range(n) if x[a + i] != y[a + i])
        out[name] = dict(bytes=n, differing=d)
    return out


def inspect_authentic(elf_path, iso_path, native_dir, m13_dir, m8_entry, m8_return, ps2dinfo, compiler='cc') -> dict:
    from hg_identify import identify
    from resource_execution import ORIGINAL_ELF, ORIGINAL_ISO
    from verify_world_init import _load_m21_capture, _probe_dispatch_guard_on_area1_active
    from verify_player_grounded_runtime import Captures
    from verify_player_connected_runtime import CapturedMemory, ConnectedRuntimeDriver, pad_dma_buffers
    from verify_resource_loader import shared_module
    from resource_harness import ResourceEnv, library
    from ee_subset import Machine

    elf = elf_path.read_bytes()
    if hashlib.sha256(elf).hexdigest() != ORIGINAL_ELF:
        raise ValueError('ELF is not the authenticated SCPS-15046 executable')
    ident = identify(iso_path, 'SCPS-15046')
    if ident['image']['sha256'] != ORIGINAL_ISO or ident['elf']['sha256'] != ORIGINAL_ELF:
        raise ValueError('ISO identity mismatch')
    tracked = json.loads((ROOT / 'evidence/builds/japan.json').read_text())
    files = {f['path']: f for f in ident['files']}
    keys = ('lba', 'size_bytes', 'sha256', 'sha1', 'md5', 'crc32')
    identity = dict(
        iso_sha256=ident['image']['sha256'], elf_sha256=ident['elf']['sha256'],
        serial=ident['serial'], boot_path=ident['boot_path'], word_xor_crc=ident['elf']['word_xor_crc'],
        system_cnf_sha256=ident['system_cnf']['sha256'], system_cnf=ident['system_cnf']['text'],
        entry=ident['elf']['entry'],
        pt_load=[dict(vaddr=f'{p["vaddr"]:08x}', filesz=p['filesz'], memsz=p['memsz']) for p in ident['elf']['pt_load']],
        pack_dat_sha256=files['/DATA/PACK.DAT']['sha256'], area1_overlay_sha256=files['/AREA1/A1_B00.BIN']['sha256'],
        files=len(files),
        matches_tracked_build=all({k: files[p][k] for k in keys} == {k: t[k] for k in keys}
                                  for p, t in ((t['path'], t) for t in tracked['files']))
        and len(files) == len(tracked['files']) and ident['system_cnf'] == tracked['system_cnf']
        and ident['elf'] == tracked['elf'] and ident['image'] == tracked['image'],
        crc_alone_insufficient='SHA-256 of image, every file, SYSTEM.CNF and the ELF program headers checked',
    )
    if not identity['matches_tracked_build']:
        raise ValueError('ISO inventory differs from evidence/builds/japan.json')

    original = characterize(elf)
    if original['problems']:
        raise ValueError(f'original-byte characterization failed: {original["problems"]}')
    original.pop('problems')

    req_meta, req_ee, req_sp = _load_m21_capture(native_dir, 'area1-request')
    act_meta, act_ee, act_sp = _load_m21_capture(native_dir, 'area1-active')
    obs = Captures(m13_dir).load('obs3-000')
    obs_ee, obs_sp = bytes(obs.raw), bytes(obs.scratch)
    m8 = {}
    for name, path in (('native-player-init-entry-001', m8_entry), ('native-player-init-return-001', m8_return)):
        if path.name != f'{name}.json':
            raise ValueError(f'expected {name}.json, got {path.name}')
        m8[name] = (CapturedMemory.load_capture(path), hashlib.sha256(path.read_bytes()).hexdigest())
    m8_entry, m8_ret = bytes(m8['native-player-init-entry-001'][0].raw), bytes(m8['native-player-init-return-001'][0].raw)
    captures = {
        'area1-request': dict(run='M21 PCSX2 run', stop='0012dd60 area request', ee_sha256=req_meta['ee_sha256'],
                              fields=_field_summary(req_ee, req_sp)),
        'area1-active': dict(run='M21 PCSX2 run', stop='first 0012f850 entry', ee_sha256=act_meta['ee_sha256'],
                             fields=_field_summary(act_ee, act_sp)),
        'm8-player-init-entry': dict(run='M8 PCSX2 run (separate process)', stop='00158a10 entry, a0 = 003f92d0',
                                     capture_sha256=m8['native-player-init-entry-001'][1],
                                     fields=_field_summary(m8_entry, None)),
        'm8-player-init-return': dict(run='M8 PCSX2 run (separate process)', stop='001585c8 (return from 00158a10)',
                                      capture_sha256=m8['native-player-init-return-001'][1],
                                      fields=_field_summary(m8_ret, None)),
        'obs3-000': dict(run='M13 PCSX2 run (separate process)', stop='active corridor frame 10163',
                         ee_sha256=hashlib.sha256(obs_ee).hexdigest(), fields=_field_summary(obs_ee, obs_sp)),
    }
    cross_run = _diff(act_ee, m8_entry, DOMAINS)

    segments, _ = shared_module(ps2dinfo).segments_from_elf(elf)
    base = Path(os.environ.get('HG_VERIFY_BUILD_DIR', ROOT / 'local/build'))
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='m25-authentic-', dir=base) as tmp:
        lib = library(output=Path(tmp) / 'm25-bridge.so')
        mem = CapturedMemory(act_ee, act_sp)
        mem.watch = [(e['start'], e['end']) for e in pad_dma_buffers(mem)]
        drv = ConnectedRuntimeDriver(mem, elf, segments)
        lib.hg_player_initialize_fixed.argtypes = [C.POINTER(ResourceEnv), C.c_uint32]
        lib.hg_player_initialize_fixed.restype = None
        lib.hg_player_initialize_fixed(C.byref(drv.env), PLAYER)
        if drv.errors:
            raise ValueError(f'bridge driver errors: {drv.errors}')
        bridge = dict(
            claim='cross-run domain equivalence only; not one continuous run',
            input='M21 area1-active', reference='M8 00158a10 return',
            c_function='hg_player_initialize_fixed (00158a10) with original-instruction leaves',
            leaves=[c['entry'] for c in drv.modeled_calls],
            domains=_diff(bytes(drv.mem.raw), m8_ret, BRIDGE_DOMAINS),
        )

    def oracle(start_ee, start_sp, pc, watch):
        cm = CapturedMemory(start_ee, start_sp)
        d = ConnectedRuntimeDriver(cm, elf, segments)
        m = Machine()
        m.mem = cm
        m.reg[28], m.reg[29] = 0x3e7f70, 0x1ff0800
        seen = []
        m.trace = lambda cpu: seen.append(cpu.pc) if cpu.pc in watch and (not seen or seen[-1] != cpu.pc) else None
        m.hooks = {t: (lambda cpu, t=t: d.external(t, *(cpu.reg[i] & 0xffffffff for i in (4, 5, 6, 7))))
                   for t in (0x119910, 0x119ac8)}
        m.run(pc, limit=20_000_000)
        d.verify_executed(m.executed)
        return cm, dict(entry=f'{pc:08x}', instructions=m.steps, distinct_addresses=len(m.executed),
                        callees_in_order=[f'{x:08x}' for x in seen])
    watch = {0x158a10, 0x1e3440, 0x158470, 0x196ad0, 0x159380, 0x12d130, 0x15e1d0, 0x267450}
    after0, run0 = oracle(act_ee, act_sp, 0x158560, watch)
    after1, run1 = oracle(bytes(after0.raw), bytes(after0.scratch), 0x12c790, watch)
    pred = _field_summary(bytes(after1.raw), bytes(after1.scratch))
    branch_effects = dict(store_003f9f39=bytes(after0.raw)[0x3f9f39], gp_2f78=bytes(after0.raw)[0x3e7f70 + 0x2f78])
    oracle_chain = dict(
        claim='original ELF bytes executed by the functional EE subset; a prediction, not a runtime observation',
        start='M21 area1-active', state0=run0, camera=run1,
        predicted=dict(player=pred['player'], camera=pred['camera'], input_block_3f9f6e=pred['input_block_3f9f6e'],
                       nodes=pred['nodes'], lists=pred['lists']),
        entry_event_00158470='returned zero (0xb4 store to 003f9f39 and gp+2f78 = 2 not taken)'
        if branch_effects == dict(store_003f9f39=0, gp_2f78=0) else f'branch effects {branch_effects}',
    )

    # Rotation-helper conventions (original bytes on a test matrix).
    ram = bytearray(0x2000000)
    for start, end, off in segments:
        n = min(end - start, len(elf) - off)
        if n > 0:
            ram[start:start + n] = elf[off:off + n]
    helper = {}
    src = [1, 2, 3, 0, 4, 5, 6, 0, 7, 8, 9, 0, 10, 11, 12, 1]

    def run_helper(pc, f12, regs):
        cm = CapturedMemory(bytes(ram), bytes(0x4000))
        d = ConnectedRuntimeDriver(cm, elf, segments)
        for i, v in enumerate(src):
            cm.raw[0x1e00000 + 4 * i:0x1e00004 + 4 * i] = struct.pack('<f', v)
        cm.raw[0x1e00200:0x1e00220] = struct.pack('<8f', 1, 2, 3, 1, 0.3, 0.7, 0.2, 0)
        m = Machine()
        m.mem = cm
        m.reg[28], m.reg[29] = 0x3e7f70, 0x1ff0800
        if f12 is not None:
            m.fpr[12] = struct.unpack('<I', struct.pack('<f', f12))[0]
        for i, v in enumerate(regs):
            m.reg[4 + i] = v
        m.hooks = {}
        m.run(pc, limit=500000)
        d.verify_executed(m.executed)
        return [struct.unpack_from('<f', cm.raw, regs[0] + 4 * i)[0] for i in range(16)]
    c, s = math.cos(0.5), math.sin(0.5)
    rows = [src[i:i + 4] for i in range(0, 16, 4)]
    right = {
        '00143c60': [[r[0], r[1] * c - r[2] * s, r[1] * s + r[2] * c, r[3]] for r in rows],
        '00143d30': [[r[0] * c + r[2] * s, r[1], -r[0] * s + r[2] * c, r[3]] for r in rows],
        '00143b90': [[r[0] * c - r[1] * s, r[0] * s + r[1] * c, r[2], r[3]] for r in rows],
    }
    for name, pc in (('00143c60', 0x143c60), ('00143d30', 0x143d30), ('00143b90', 0x143b90)):
        out = run_helper(pc, 0.5, (0x1e00100, 0x1e00000))
        want = [v for r in right[name] for v in r]
        helper[name] = dict(convention='dst = src * R (column mixing)',
                            max_error=round(max(abs(a - b) for a, b in zip(out, want)), 6))
    built = run_helper(0x144340, None, (0x1e00300, 0x1e00200, 0x1e00210))
    M = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    for key, ang in (('00143c60', 0.3), ('00143d30', 0.7), ('00143b90', 0.2)):
        c, s = math.cos(ang), math.sin(ang)
        if key == '00143c60':
            M = [[r[0], r[1] * c - r[2] * s, r[1] * s + r[2] * c, r[3]] for r in M]
        elif key == '00143d30':
            M = [[r[0] * c + r[2] * s, r[1], -r[0] * s + r[2] * c, r[3]] for r in M]
        else:
            M = [[r[0] * c - r[1] * s, r[0] * s + r[1] * c, r[2], r[3]] for r in M]
    helper['00144340'] = dict(convention='identity * Rx(rot.x) * Ry(rot.y) * Rz(rot.z), row 3 = position',
                              max_error=round(max(abs(built[4 * i + j] - M[i][j]) for i in range(3) for j in range(3)), 6))
    if any(v['max_error'] > 1e-4 for v in helper.values()):
        raise ValueError(f'rotation helper convention mismatch: {helper}')

    guard = _probe_dispatch_guard_on_area1_active(act_ee, act_sp, compiler)

    bridge_ok = all(v['differing'] == 0 for v in bridge['domains'].values())
    same_class = all(v['differing'] == 0 for v in cross_run.values())
    return {
        'schema': 'hg-m25-authentic-dependencies/v1',
        'synthetic_only': False,
        'reference_build': 'SCPS-15046', 'pcsx2_crc': '91AA4B28',
        'elf_sha256': ORIGINAL_ELF, 'iso_sha256': ORIGINAL_ISO,
        'identity': identity,
        'original_bytes': original,
        'captures': captures,
        'cross_run_area1_active_vs_m8_entry': dict(
            claim='separate runs; byte equality of world-setup domains, not continuity', domains=cross_run,
            same_checkpoint_class=same_class),
        'bridge_00158a10': dict(bridge, passed=bridge_ok),
        'oracle_state0_and_first_camera': oracle_chain,
        'rotation_helpers': helper,
        'fault_00158560_area1_active': guard,
        'boundary': dict(
            last_supported_single_run_stop='0012f850 entry (M21 area1-active) for these inputs; the M25 live run '
                                           'extends single-run support (evidence/m25/live-continuity.json)',
            supported_by_cross_run_equivalence='00158a10 return (001585c8): M21 area1-active domains equal the M8 '
                                              'entry, and C 00158a10 on area1-active reproduces the M8 return'
            if (bridge_ok and same_class) else None,
            earliest_unsupported_dependency='001e3440(PLAYER, +8.0, -3.0) state-0 floor snap on the retail collision '
                                            'mesh: no C reconstruction; its return is observed only in the M25 live '
                                            'run',
            later_unobserved=['00158470 return register (branch effects observed in the M25 live run)',
                              'ticks 4-113 (walk start and input-block release)', 'neutral stick release',
                              'wall contact on the retail collision mesh (001e3850, 001e3c40, 001454a0)'],
            m23_conflicts=[
                '003f9f6e: M23 model writes 0 at state 0; original bytes and oracle give 1 when +464 == 0, '
                'and the M25 live run observes 1 after state 0',
                'camera pitch: M23 synthetic helpers left-multiply; original 00143c60/00143d30/00143b90 right-multiply '
                '(M23 boom pitch 0.0301 at yaw 0 and 0 at yaw pi/2; oracle 0.0524)',
                'M23.md cites local/m21/live/...; the captures are under local/m21/native/full/',
            ],
        ),
        'runtime_observations_pending': [
            'resolved by evidence/m25/live-continuity.json: same-run 00158a10 return, 001e3440 return, '
            'state-0 tail, first camera follow, first state-1 frames, walk ticks 114-200 with floor response',
            'still pending: 00158470 return register, ticks 4-113, neutral release, wall contact',
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--synthetic', action='store_true')
    parser.add_argument('--inspect-authentic', action='store_true')
    parser.add_argument('--compiler', default=os.environ.get('CC', 'cc'))
    parser.add_argument('--sanitize', action='store_true')
    parser.add_argument('--elf', type=Path)
    parser.add_argument('--iso', type=Path)
    parser.add_argument('--native', type=Path)
    parser.add_argument('--m13-captures', type=Path)
    parser.add_argument('--m8-entry', type=Path, help='M8 native-player-init-entry-001.json')
    parser.add_argument('--m8-return', type=Path, help='M8 native-player-init-return-001.json')
    parser.add_argument('--ps2dinfo', type=Path)
    parser.add_argument('--receipt', action='store_true', help='write only the tracked receipt form (synthetic)')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.synthetic == args.inspect_authentic:
        parser.error('choose exactly one of --synthetic or --inspect-authentic')
    if args.synthetic:
        result = verify_synthetic(args.compiler, args.sanitize)
        if args.receipt:
            result = result['report']
        status = 0 if (args.receipt or (result.get('failure') is None and result.get('exit_code') == 0)) else 1
    else:
        missing = [n for n in ('elf', 'iso', 'native', 'm13_captures', 'm8_entry', 'm8_return', 'ps2dinfo')
                   if not getattr(args, n)]
        if missing:
            parser.error(f'--inspect-authentic requires {", ".join("--" + m.replace("_", "-") for m in missing)}')
        result = inspect_authentic(args.elf, args.iso, args.native, args.m13_captures, args.m8_entry,
                                   args.m8_return, args.ps2dinfo, args.compiler)
        status = 0
    text = json.dumps(result, indent=1, sort_keys=False) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        sys.stdout.write(text)
    return status


if __name__ == '__main__':
    raise SystemExit(main())
