#!/usr/bin/env python3
"""M11 original-instruction differential for C 0015ebc0 on perturbed retail scene state.

Starts from one owner-captured constraint-entry state (real collision scene), then
perturbs position, movement heading, facing heading and constraint fields. For each
case the C reconstruction and the authenticated original 0015ebc0 instructions run
from identical memory at the same guest stack depth; complete EE RAM (stack
excluded) and scratchpad plus the return value must match. Cases where the C fails
closed (special surfaces) are counted as unsupported, never as passes. These are
model differential results, not additional retail observations.
"""
import argparse
import ctypes as C
import gc
import json
import math
import random
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
BASE_STAGE = ('constraint-entry', 4283)
def bits(x): return struct.unpack('<I', struct.pack('<f', x))[0]


def case_state(rng, index):
    """Positions biased to within ~5 units of the corridor's three static walls."""
    region = index % 4
    if region == 0: x, z = rng.uniform(24.5, 33.0), rng.uniform(252.0, 274.0)      # end wall (slanted)
    elif region == 1: x, z = rng.uniform(30.0, 90.0), rng.uniform(249.6, 254.5)    # -Z side wall
    elif region == 2: x, z = rng.uniform(30.0, 90.0), rng.uniform(271.5, 276.4)    # +Z side wall
    else: x, z = rng.uniform(25.0, 34.0), rng.uniform(249.6, 254.0)                # corner
    return dict(x=x, z=z, heading=rng.uniform(-math.pi, math.pi), facing=rng.uniform(-math.pi, math.pi),
                f4ad=1 if rng.random() < 0.25 else 0, f49c=rng.choice((0, 2)),
                f508=0 if rng.random() < 0.7 else rng.randrange(1, 81), f49a=0x100 if rng.random() < 0.1 else 0)


def prepare(base, s):
    mem = V.CapturedMemory(bytes(base.raw), bytes(base.scratch)); mem.scratch_known = set(base.scratch_known)
    mem.store(P + 0x30, bits(s['x'])); mem.store(P + 0x38, bits(s['z'])); mem.store(P + 0x44, bits(s['facing']))
    mem.store(P + 0x4ad, s['f4ad'], 1); mem.store(P + 0x49c, s['f49c'], 1); mem.store(P + 0x508, s['f508'], 1)
    mem.store(P + 0x49a, s['f49a'], 2)
    mem.initial = bytes(mem.raw); mem.writes = set()
    ext = V.pad_dma_buffers(mem); mem.watch = [(e['start'], e['end']) for e in ext]
    return mem, ext


def classify_refusal(errors, balanced, original_addresses):
    refusal = repr(ValueError('C path outside reconstructed domain (code 0x40)'))
    original_special = 0x15eaf0 in original_addresses
    if not balanced:
        return dict(result='error', detail='Unbalanced constraint guest frame')
    if errors and any(not error.startswith("ValueError('C path outside reconstructed domain (code ") for error in errors):
        return dict(result='error', detail=errors)
    if errors == [refusal] and original_special:
        return dict(result='unsupported', refusal_code='0x40', original_special=True)
    if errors or original_special:
        return dict(result='failed', failure='refusal-mismatch', errors=errors, original_special=original_special)
    return None


def run_case(lib, elf, segments, base, s, status):
    cm, ext = prepare(base, s); om, _ = prepare(base, s)
    cd = V.ConnectedRuntimeDriver(cm, elf, segments); cd.cop0_status = status; cd.constant_t0 = {0x156ad0: 1}
    fn = lib.hg_player_static_constraint_fixed; fn.argtypes = [C.POINTER(ResourceEnv), C.c_uint32, C.c_uint32]; fn.restype = C.c_uint32
    c_result = fn(C.byref(cd.env), bits(s['heading']), P)
    od = V.ConnectedRuntimeDriver(om, elf, segments); od.cop0_status = status
    o_result = od.external(0x15ebc0, bits(s['heading']), P)
    if od.errors: raise AssertionError(od.errors)
    refusal = classify_refusal(cd.errors, cd.guest_frame == 0, od.executed)
    if refusal is not None:
        return refusal, od.executed
    r = V.compare_memory(cd.mem, od.mem, ext)
    scratch = [i for i in range(0x4000) if cd.mem.scratch[i] != od.mem.scratch[i]]
    ok = r['result'] == 'passed' and not scratch and (c_result & 0xffff) == (o_result & 0xffff)
    return dict(result='passed' if ok else 'failed', flags=hex(o_result & 0xffff), c_flags=hex(c_result & 0xffff),
                different_bytes=r['different_bytes'], scratch_bytes=len(scratch), differences=r['differences'][:8],
                sound=any(c['entry'] == '0x156ad0' for c in cd.modeled_calls)), od.executed


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf', type=Path, required=True); p.add_argument('--ps2dinfo', type=Path, required=True)
    p.add_argument('--capture-dir', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--cases', type=int, default=400); p.add_argument('--seed', type=int, default=11)
    a = p.parse_args()
    if a.output.exists() or a.output.with_suffix('.so').exists(): raise FileExistsError('Fresh output required')
    if digest(a.elf) != EXPECTED: raise ValueError('Wrong original ELF')
    obs = json.loads((ROOT / 'evidence/m11/constraint-observation.json').read_text())
    row = [fr for fr in obs['frames'] if fr['counter'] == BASE_STAGE[1]][0]['captures'][BASE_STAGE[0]]
    path = a.capture_dir / (row['name'] + '.json')
    if digest(path) != row['sha256']: raise ValueError('Base capture digest mismatch')
    base = V.CapturedMemory.load_capture(path)
    elf = a.elf.read_bytes(); segments, _ = shared_module(a.ps2dinfo).segments_from_elf(elf)
    lib = library(output=a.output.with_suffix('.so'))
    rng = random.Random(a.seed); rows = []; executed = set()
    for index in range(a.cases):
        s = case_state(rng, index); status = (0x10000, 0)[index % 2]
        try:
            outcome, addresses = run_case(lib, elf, segments, base, s, status)
            if addresses: executed |= addresses
        except Exception as exc: outcome = dict(result='error', detail=str(exc)[:300])
        rows.append(dict(index=index, status=hex(status), **{k: (round(v, 6) if isinstance(v, float) else v) for k, v in s.items()}, **outcome))
        if index % 50 == 49: print(index + 1, {r: sum(1 for x in rows if x['result'] == r) for r in ('passed', 'failed', 'unsupported', 'error')}, flush=True)
        gc.collect()
    flags = {}
    for r in rows:
        if r['result'] == 'passed': flags[r['flags']] = flags.get(r['flags'], 0) + 1
    tier_bits = dict(head=sum(1 for r in rows if r['result'] == 'passed' and int(r['flags'], 16) & 1),
                     middle=sum(1 for r in rows if r['result'] == 'passed' and int(r['flags'], 16) & 0x30),
                     foot_or_rear=sum(1 for r in rows if r['result'] == 'passed' and int(r['flags'], 16) & 0x100),
                     with_4ad=sum(1 for r in rows if r['result'] == 'passed' and r['f4ad'] and int(r['flags'], 16)),
                     sound=sum(1 for r in rows if r['result'] == 'passed' and r['sound']))
    own = {x for x in executed if 0x15ebc0 <= x < 0x15fc0c}
    counts = {r: sum(1 for x in rows if x['result'] == r) for r in ('passed', 'failed', 'unsupported', 'error')}
    out = dict(schema='hg-player-constraint-differential/v1', result='passed' if counts['failed'] == counts['error'] == 0 and counts['passed'] else 'failed',
               elf_sha256=EXPECTED, base_capture=row['name'], base_capture_sha256=row['sha256'], seed=a.seed, counts=counts,
               result_flags=flags, coverage=tier_bits, original_15ebc0_addresses_executed=len(own),
               verification_sources={n: digest(ROOT / n) for n in ('src/resource_loader.c', 'tools/ee_subset.py',
                   'tools/verify_player_constraint_differential.py', 'tools/verify_player_connected_runtime.py')},
               limitations=['Synthetic perturbations of one retail scene state; model differential, not retail observation.',
                            'Special-surface hits fail closed in C and are counted as unsupported.'],
               cases=rows)
    with a.output.open('x') as f: json.dump(out, f, indent=1); f.write('\n')
    print(out['result'], counts, flags, tier_bits, 'addresses', len(own))
    if out['result'] != 'passed': raise SystemExit(1)


if __name__ == '__main__': main()
