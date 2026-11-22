#!/usr/bin/env python3
"""M12 original-instruction differential for C 0015fc10/0015e420 on perturbed retail state.

Starts from owner-captured grounded-entry states (native stop at 0015fc10 in the real
corridor scene), then perturbs position, height above the floor, vertical speed and the
floor-result fields that the floor routine reads or writes. For each case the C
reconstruction and the authenticated original 0015fc10 instructions run from identical
memory at the same guest stack depth; complete EE RAM (stack excluded), scratchpad and
the vertical-speed/position results must match. A case where the C fails closed is
'unsupported' only if its single refusal code names exactly the original branch taken
(0x50 no-floor, 0x51 type-7/type-6 by the hit type, 0x52 special-0x1e00, 0x53 type-5,
0x54 spawn); a wrong or extra code is a 'failed' refusal-mismatch. Only a completed
comparison with differing bytes is a measured divergence; refusal mismatches, unrefused
unsupported branches, DMA-touch-only results and harness errors never are. These are
model differential results, not additional retail observations.
"""
import argparse
import ctypes as C
import gc
import json
import random
import re
import struct
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'tools')]
import verify_player_connected_runtime as V
from verify_resource_loader import EXPECTED, shared_module
from resource_harness import ResourceEnv, library
from hg_evidence_safety import digest

P = 0x3f92d0
OBSERVATION = ROOT / 'evidence/m12/grounded-observation.json'
# Original 0015e420 addresses that only unsupported (fail-closed) branches reach.
UNSUPPORTED_ENTRIES = {0x15e888: 'no-floor', 0x15e4ac: 'type-7', 0x15e554: 'type-6',
                       0x15e594: 'special-0x1e00', 0x15e6cc: 'type-5', 0x15e7e4: 'spawn'}
def bits(x): return struct.unpack('<I', struct.pack('<f', x))[0]


def case_state(rng, index):
    """Positions over the sloped corridor floor. Four in five cases place the post-gravity
    probe 0.1 inside its window (y' - floor in [-3.9, 1.9]; the segment runs y'+4 .. y'-2); the fifth is broad
    and includes misses, which must fail closed exactly where the original takes no-floor."""
    x, z = rng.uniform(24.5, 62.0), rng.uniform(249.6, 276.4)
    f4ad = rng.choice((0, 0, 0, 1, 2)); gravity = 0.03 if f4ad else 0.08
    speed = 0.0 if rng.random() < 0.35 else rng.uniform(-2.6, 0.6)
    after = max(speed - gravity, -2.0)
    offset = rng.uniform(-7.0, 4.5) if index % 5 == 4 else rng.uniform(-3.9, 1.9)   # y' - floor; probe spans [-4, 2]
    return dict(x=x, z=z, dy=offset - after, speed=speed, f4ad=f4ad,
                state0=rng.choice((1, 1, 2)), f47c=rng.randrange(0x10000), f47e=rng.randrange(0x10000),
                f480=rng.randrange(256), f481=rng.randrange(256), f483=rng.randrange(256), f2c3=rng.randrange(4),
                f4f4=rng.randrange(1 << 32), s9ff2=rng.randrange(0x10000), d88c=rng.randrange(1 << 32))


def floor_estimate(x):
    """Discovery fit of the corridor floor (y = 37.9709 + 0.003316 (x - 26)); only centres dy."""
    return 37.9709 + 0.003316 * (x - 26.0)


def prepare(base, s):
    mem = V.CapturedMemory(bytes(base.raw), bytes(base.scratch)); mem.scratch_known = set(base.scratch_known)
    mem.store(P + 0x30, bits(s['x'])); mem.store(P + 0x34, bits(floor_estimate(s['x']) + s['dy']))
    mem.store(P + 0x38, bits(s['z'])); mem.store(P + 0x430, bits(s['speed']))
    mem.store(P + 0x4ad, s['f4ad'], 1); mem.store(P, s['state0'], 1)
    mem.store(P + 0x47c, s['f47c'], 2); mem.store(P + 0x47e, s['f47e'], 2); mem.store(P + 0x480, s['f480'], 1)
    mem.store(P + 0x481, s['f481'], 1); mem.store(P + 0x483, s['f483'], 1); mem.store(P + 0x2c3, s['f2c3'], 1)
    mem.store(P + 0x4f4, s['f4f4']); mem.store(0x3f9ff2, s['s9ff2'], 2); mem.store(0x46d88c, s['d88c'])
    mem.initial = bytes(mem.raw); mem.writes = set()
    ext = V.pad_dma_buffers(mem); mem.watch = [(e['start'], e['end']) for e in ext]
    return mem, ext


DOMAIN = re.compile(r'outside reconstructed domain \(code (0x[0-9a-f]+)\)')
# C refusal code -> the one original 0015e420 branch it stands for. 0x51 covers both
# hazards; the hit type the C itself read (3f39c6) selects type-7 or type-6.
REFUSAL_BRANCH = {0x50: 'no-floor', 0x52: 'special-0x1e00', 0x53: 'type-5', 0x54: 'spawn'}
HAZARD_BRANCH = {7: 'type-7', 6: 'type-6'}
# 'failed' sub-kinds. Only measured-divergence is a completed comparison with a genuine
# difference; the others fail equivalence but never count as negative-control divergence.
FAILURES = ('measured-divergence', 'refusal-mismatch', 'unsupported-branch-not-refused', 'external-dma-access')


def expected_branch(code, floor_type):
    return HAZARD_BRANCH.get(floor_type) if code == 0x51 else REFUSAL_BRANCH.get(code)


def classify(c_errors, c_balanced, c_floor_type, reached, comparison=None, scratch_bytes=0):
    """One case: 'passed', 'failed' (with a failure sub-kind), 'unsupported' or 'error'.

    c_errors: driver errors from the C run; reached: original unsupported branches executed;
    comparison: compare_memory result, present only when the C completed."""
    codes = [int(m.group(1), 16) for e in c_errors for m in [DOMAIN.search(str(e))] if m]
    other = [str(e)[:200] for e in c_errors if not DOMAIN.search(str(e))]
    if other or not c_balanced:
        return dict(result='error', detail=other or ['Unbalanced guest frame'])
    if codes:
        expected = expected_branch(codes[0], c_floor_type) if len(codes) == 1 else None
        row = dict(refusal_codes=[hex(c) for c in codes], expected_branch=expected, original_branches=reached)
        if expected is not None and reached == [expected]: return dict(result='unsupported', **row)
        return dict(result='failed', failure='refusal-mismatch', **row)
    if comparison is None:
        return dict(result='error', detail=['C completed but no comparison was made'])
    genuine = comparison['different_bytes'] > 0 or scratch_bytes > 0
    row = dict(original_branches=reached, different_bytes=comparison['different_bytes'], scratch_bytes=scratch_bytes,
               differences=comparison['differences'][:8])
    accesses = comparison['external_dma']['modeled_accesses']
    if accesses:
        return dict(result='failed', failure='external-dma-access', modeled_accesses=accesses[:8], **row)
    if genuine: return dict(result='failed', failure='measured-divergence', **row)
    if reached: return dict(result='failed', failure='unsupported-branch-not-refused', **row)
    if comparison['result'] != 'passed':
        return dict(result='error', detail=['Comparison failed without measured differences or DMA access'])
    return dict(result='passed', **row)


def tally(rows):
    counts = {r: sum(1 for x in rows if x['result'] == r) for r in ('passed', 'failed', 'unsupported', 'error')}
    counts['failures'] = {k: sum(1 for x in rows if x.get('failure') == k) for k in FAILURES}
    return counts


def control_verdict(counts, expect):
    """'divergence': at least one completed comparison with a genuine difference and no
    errors. 'refusal-mismatch': a wrong refusal code is rejected by classification (it
    is deliberately not counted as divergence)."""
    if counts['error']: return 'error'
    return 'rejected' if counts['failures'][{'divergence': 'measured-divergence', 'refusal-mismatch': 'refusal-mismatch'}[expect]] > 0 else 'ESCAPED'


def run_case(lib, elf, segments, base, s):
    cm, ext = prepare(base, s); om, _ = prepare(base, s)
    cd = V.ConnectedRuntimeDriver(cm, elf, segments)
    fn = lib.hg_player_grounded_fixed; fn.argtypes = [C.POINTER(ResourceEnv), C.c_uint32]; fn.restype = None
    fn(C.byref(cd.env), P)
    od = V.ConnectedRuntimeDriver(om, elf, segments)
    od.external(0x15fc10, P)
    if od.errors: raise AssertionError(od.errors)
    reached = sorted(name for address, name in UNSUPPORTED_ENTRIES.items() if address in od.executed)
    completed = not cd.errors and cd.guest_frame == 0
    comparison = V.compare_memory(cd.mem, od.mem, ext) if completed else None
    scratch = [i for i in range(0x4000) if cd.mem.scratch[i] != od.mem.scratch[i]] if completed else []
    row = classify(cd.errors, cd.guest_frame == 0, cm.peek_word(0x3f39c6, 2), reached, comparison, len(scratch))
    row.update(c_floor_returns=[hex(v) for _, v in cd.call_returns])
    if completed:
        row.update(speed_out=hex(od.mem.peek_word(P + 0x430)), ground_2c3=od.mem.peek_word(P + 0x2c3, 1),
                   y_out=hex(od.mem.peek_word(P + 0x34)), surface_47c=hex(od.mem.peek_word(P + 0x47c, 2)),
                   f483_out=hex(od.mem.peek_word(P + 0x483, 1)))
    return row, od.executed


# Differential-level negative controls: branches retail frames do not reach. 'divergence'
# mutants must fail some cases by a completed comparison with a genuine memory difference,
# with no errors. The 'refusal-mismatch' mutant (a wrong fail-closed code) produces no
# memory difference by construction; it must be rejected by exact code-to-branch matching.
CONTROLS = [
    ('omitted-speed-clamp', 'divergence', '    if(flt(e,speed,0xc0000000u))w32(e,a+0x430,0xc0000000u);\n', ''),
    ('wrong-alternate-gravity', 'divergence', 'uint32_t gravity=r8(e,a+0x4ad)==0?0x3da3d70au:0x3cf5c28fu;',
     'uint32_t gravity=r8(e,a+0x4ad)==0?0x3da3d70au:0x3da3d70au;'),
    ('omitted-state-restore', 'divergence', '            if(r8(e,a)==2)w8(e,a,1);\n', ''),
    ('wrong-refusal-code', 'refusal-mismatch', 'if(hit==0)fault=0x50;', 'if(hit==0)fault=0x54;'),
]


def run_cases(lib, elf, segments, states, count, seed):
    rng = random.Random(seed); rows = []; executed = set()
    for index in range(count):
        s = case_state(rng, index); frame, row, base = states[index % len(states)]
        try:
            outcome, addresses = run_case(lib, elf, segments, base, s)
            executed |= addresses
        except Exception as exc: outcome = dict(result='error', detail=[str(exc)[:300]])
        rows.append(dict(index=index, base_frame=frame, **{k: (round(v, 6) if isinstance(v, float) else v) for k, v in s.items()}, **outcome))
        if index % 50 == 49: print(index + 1, tally(rows), flush=True)
        gc.collect()
    return rows, executed


def bases(capture_dir):
    obs = json.loads(OBSERVATION.read_text()); out = []
    for fr in obs['frames']:
        row = fr['captures']['grounded-entry']; path = capture_dir / (row['name'] + '.json')
        if digest(path) != row['sha256']: raise ValueError('Base capture digest mismatch')
        out.append((fr['counter'], row))
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf', type=Path, required=True); p.add_argument('--ps2dinfo', type=Path, required=True)
    p.add_argument('--capture-dir', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--cases', type=int, default=400); p.add_argument('--seed', type=int, default=12)
    p.add_argument('--bases', type=int, default=2, help='number of grounded-entry captures used (first and last frames)')
    p.add_argument('--source', type=Path, help='alternative C source (differential-level negative control)')
    p.add_argument('--negative-controls', type=int, default=0, metavar='CASES',
                   help='also run each CONTROLS mutant on CASES cases; each must diverge with no errors')
    p.add_argument('--expect-divergence', action='store_true',
                   help='negative control: pass only if the (mutated) source fails some cases by a completed comparison '
                        'with a genuine memory difference, with no errors (refusal mismatches do not count)')
    a = p.parse_args()
    if a.output.exists() or a.output.with_suffix('.so').exists(): raise FileExistsError('Fresh output required')
    if digest(a.elf) != EXPECTED: raise ValueError('Wrong original ELF')
    available = bases(a.capture_dir)
    chosen = [available[0], available[-1]][:a.bases] if a.bases <= 2 else available[:a.bases]
    states = [(n, row, V.CapturedMemory.load_capture(a.capture_dir / (row['name'] + '.json'))) for n, row in chosen]
    elf = a.elf.read_bytes(); segments, _ = shared_module(a.ps2dinfo).segments_from_elf(elf)
    lib = library(source=a.source, output=a.output.with_suffix('.so')) if a.source else library(output=a.output.with_suffix('.so'))
    rows, executed = run_cases(lib, elf, segments, states, a.cases, a.seed)
    counts = tally(rows)
    ok_rows = [r for r in rows if r['result'] == 'passed']
    coverage = dict(clamped_speed=sum(1 for r in ok_rows if r['speed'] - (0.03 if r['f4ad'] else 0.08) < -2.0),
                    alternate_gravity_4ad=sum(1 for r in ok_rows if r['f4ad']), state0_two=sum(1 for r in ok_rows if r['state0'] == 2),
                    rising_speed=sum(1 for r in ok_rows if r['speed'] - (0.03 if r['f4ad'] else 0.08) > 0),
                    surface_bit_kept_8000=sum(1 for r in ok_rows if r['f47c'] & 0x8000),
                    unsupported_branches={b: sum(1 for r in rows if b in r.get('original_branches', ())) for b in UNSUPPORTED_ENTRIES.values()})
    own = {x for x in executed if 0x15fc10 <= x < 0x15fcc8 or 0x15e420 <= x < 0x15eaec}
    if a.expect_divergence: ok = control_verdict(counts, 'divergence') == 'rejected'
    else: ok = counts['failed'] == counts['error'] == 0 and counts['passed'] > 0
    out = dict(schema='hg-player-grounded-differential/v1', result='passed' if ok else 'failed',
               mode='negative-control (divergence required)' if a.expect_divergence else 'equivalence',
               elf_sha256=EXPECTED, base_captures=[dict(frame=n, name=row['name'], sha256=row['sha256']) for n, row, _ in states],
               seed=a.seed, counts=counts, coverage=coverage, original_addresses_executed=len(own),
               original_addresses_executed_15fc10=len({x for x in own if x < 0x15fcc8 and x >= 0x15fc10}),
               verification_sources={n: digest(ROOT / n) for n in ('src/resource_loader.c', 'tools/ee_subset.py',
                   'tools/verify_player_grounded_differential.py', 'tools/verify_player_connected_runtime.py')},
               source_override=str(a.source) if a.source else None,
               limitations=['Synthetic perturbations of retail scene states; model differential, not retail observation.',
                            'Floor materials come from the captured corridor geometry only (type 2, +47e 0, cc 0).',
                            'No-floor, hazard (5/6/7), special-surface and +47e == 3 spawn paths fail closed in C and count as unsupported.'],
               cases=rows)
    if a.negative_controls:
        folder = a.output.parent / (a.output.stem + '-controls'); folder.mkdir(exist_ok=False)
        source = (ROOT / 'src/resource_loader.c').read_text(); results = []
        for name, expect, old, new in CONTROLS:
            try:
                if source.count(old) != 1: raise AssertionError(f'{name}: target not unique')
                path = folder / (name + '.c'); path.open('x').write(source.replace(old, new))
                mrows, _ = run_cases(library(source=path, output=folder / (name + '.so')), elf, segments, states, a.negative_controls, a.seed)
                c = tally(mrows); kind = {'divergence': 'measured-divergence', 'refusal-mismatch': 'refusal-mismatch'}[expect]
                row = dict(control=name, expect=expect, mutated_source_sha256=digest(path), counts=c,
                           result=control_verdict(c, expect),
                           # Shown for every mutant: how it would fare under the divergence rule.
                           divergence_verdict=control_verdict(c, 'divergence'),
                           first_failure=next((dict(index=x['index'], failure=x['failure'],
                                                    **{k: x[k] for k in ('differences', 'refusal_codes', 'expected_branch', 'original_branches') if k in x})
                                               for x in mrows if x.get('failure') == kind), None))
            except Exception as exc: row = dict(control=name, expect=expect, result='error', detail=str(exc)[:300])
            results.append(row); print(name, row['result'], row.get('counts'), flush=True)
        out['negative_controls'] = results
        if not all(r['result'] == 'rejected' for r in results): out['result'] = 'failed'
    with a.output.open('x') as f: json.dump(out, f, indent=1); f.write('\n')
    print(out['result'], counts, coverage, 'addresses', len(own))
    if out['result'] != 'passed': raise SystemExit(1)


if __name__ == '__main__': main()
