#!/usr/bin/env python3
"""M14 register contracts and poisoned-buffer differential for the pre-dispatch window.

Owner-local retail check. For each selected native frame (post-counter stop):

1. Window oracle: original instructions 0012f8a8 -> 00158560 at the verified
   sp (PD.NATIVE_SP) against the dispatch-entry stop, all 32 MiB RAM and the
   full scratchpad.
2. Callee register contracts: at every call the original makes from 00198f60
   to a bridged callee, and at the coordinator's calls to 0017f6d0/0017f2d0,
   the callee is re-executed on a copy of the memory at its entry with
     original  - the original caller's complete register state;
     poison-1/2 - only the declared contract registers kept, every other GPR,
                 FPR, VU0, HI/LO and accumulator state poisoned, bridge sp;
     bridge    - the exact host bridge state (declared registers, others zero);
   plus necessity probes that drop one declared non-ABI register. Non-stack RAM,
   scratch and the returned v0/v1/f0 must equal the original run for every
   non-probe variant.
3. Poisoned differential: the four packet streams and scratch 0xb40..0xc3f are
   filled with 0xA5 in one copy of the capture; the original window and the C
   composition (C 00198f60 plus interpreted 0017f6d0/0017f2d0) run on it and are
   compared everywhere except the guest stack below NATIVE_SP. A compiled
   pre-fix store-width defect is run on the same image and must be rejected.
"""
import argparse
import ctypes as C
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'tests')]
from ee_subset import Machine
from hg_evidence_safety import digest
from resource_harness import ResourceEnv, library
from verify_resource_loader import EXPECTED, shared_module
from verify_player_connected_runtime import CapturedMemory, pad_dma_buffers
from verify_player_continuous import ContinuousDriver, observation
from verify_player_grounded_runtime import STATUS_VALUES
import predispatch_differential as PD

COORDINATOR = {0x17f6d0: ('a0',), 0x17f2d0: ('a0',)}  # 0012f8b0/0012f8bc calls from C
CONTRACTS = dict(PD.CONTRACT) | COORDINATOR
BRIDGE_SP = 0x1ff0800  # ConnectedRuntimeDriver modeled-call stack at depth 0
POISON = (dict(gpr=0xdeadbeef_a5a5a5a5_5a5a5a5a_c0dec0de, fpr=0x41a5a5a5, vf=0x3fa5a5a5, cond=True),
          dict(gpr=0x0badf00d_3c3c3c3c_c3c3c3c3_0ff1ce00, fpr=0xc0c3c3c3, vf=0x40c3c3c3, cond=False))
SOURCES = ('src/resource_loader.c', 'src/resource_loader.h', 'tools/verify_predispatch_contracts.py',
           'tools/predispatch_differential.py', 'tools/verify_player_connected_runtime.py', 'tools/ee_subset.py')
PROBES = {0x14f060: ('t0', 't1', 't2'), 0x152e90: ('a2', 'a3')}


def state(cpu):
    return dict(reg=list(cpu.reg), fpr=list(cpu.fpr), hi=cpu.hi, lo=cpu.lo, hi1=cpu.hi1, lo1=cpu.lo1,
                acc=cpu.acc, cond=cpu.fp_condition, vf=list(cpu.vf), vacc=list(cpu.vacc), q=cpu.q,
                cop0=cpu.cop0_status, ra=cpu.reg[31] & 0xffffffff)


def declared(st, pc):
    out = {}
    for name in CONTRACTS[pc]:
        out[name] = st['fpr'][12] if name == 'f12' else st['reg'][PD.GPR[name]]
    return out


def machine_for(st, pc, mode, drop=()):
    m = Machine()
    if mode == 'original':
        m.reg = list(st['reg']); m.fpr = list(st['fpr']); m.hi, m.lo = st['hi'], st['lo']
        m.hi1, m.lo1 = st['hi1'], st['lo1']; m.acc = st['acc']; m.fp_condition = st['cond']
        m.vf = list(st['vf']); m.vacc = list(st['vacc']); m.q = st['q']
    else:
        if mode.startswith('poison'):
            p = POISON[int(mode[-1]) - 1]
            m.reg = [0] + [p['gpr']] * 31
            m.fpr = [p['fpr']] * 32
            m.hi = m.lo = m.hi1 = m.lo1 = p['gpr'] & 0xffffffffffffffff
            m.acc = p['fpr']; m.fp_condition = p['cond']; m.q = p['fpr']
            lane = p['vf']; m.vf = [m.vf[0]] + [lane | lane << 32 | lane << 64 | lane << 96] * 31
            m.vacc = [lane] * 4
        m.reg[28] = PD.GP; m.reg[29] = BRIDGE_SP
        for name, value in declared(st, pc).items():
            if name in drop: value = 0
            if name == 'f12': m.fpr[12] = value
            else: m.reg[PD.GPR[name]] = value
    m.cop0_status = st['cop0']
    return m


def execute(mem, st, pc, mode, drop=()):
    copy = CapturedMemory(bytes(mem.raw), bytes(mem.scratch))
    m = machine_for(st, pc, mode, drop)
    m.mem = copy
    try:
        m.run(pc, st['ra'], limit=400000)
    except (ValueError, KeyError) as exc:
        return dict(fault=str(exc)[:200])
    return dict(memory=copy, v0=m.reg[2] & PD_MASK64, v1=m.reg[3], f0=m.fpr[0], steps=m.steps)


PD_MASK64 = (1 << 64) - 1


def same(a, b):
    if 'fault' in a or 'fault' in b:
        return dict(equal=False, fault=a.get('fault') or b.get('fault'))
    ram = len(PD.changed(a['memory'], b['memory'], PD.STACK[0]))
    scratch = sum(x != y for x, y in zip(a['memory'].scratch, b['memory'].scratch))
    # 00198f60 overwrites v0/v1/f0 before reading them after every call, and
    # the next callee's entry state is itself contract-checked: informational.
    regs = [r for r in ('v0', 'v1', 'f0') if a[r] & PD_MASK64 != b[r] & PD_MASK64]
    return dict(equal=not ram and not scratch, ram_differences=ram,
                scratch_differences=scratch, return_differences=regs)


def contracts(capture, limit_calls=None):
    """Re-execute each observed bridged call under the contract variants."""
    mem = CapturedMemory(bytes(capture.raw), bytes(capture.scratch))
    rows = []
    def observe(cpu):
        pc, ra = cpu.pc, cpu.reg[31] & 0xffffffff
        direct = (pc in PD.CONTRACT and PD.SERVICE <= ra < PD.SERVICE_END) or \
                 (pc in COORDINATOR and PD.WINDOW[0] <= ra < 0x12f8d0)
        if not direct or (limit_calls is not None and len(rows) >= limit_calls): return
        st = state(cpu)
        base = execute(cpu.mem, st, pc, 'original')
        row = dict(entry=hex(pc), return_address=hex(ra),
                   declared={k: hex(v & 0xffffffff) for k, v in declared(st, pc).items()},
                   original_steps=base.get('steps'), original_fault=base.get('fault'))
        for mode in ('poison-1', 'poison-2', 'bridge'):
            row[mode] = same(base, execute(cpu.mem, st, pc, mode))
        for name in PROBES.get(pc, ()):
            row[f'drop-{name}'] = same(base, execute(cpu.mem, st, pc, 'bridge', (name,)))
        rows.append(row)
    m = Machine(); m.mem = mem; m.reg[28] = PD.GP; m.reg[29] = PD.NATIVE_SP; m.trace = observe
    m.run(PD.WINDOW[0], PD.WINDOW[1], limit=400000)
    return rows


def poisoned(capture):
    mem = CapturedMemory(bytes(capture.raw), bytes(capture.scratch))
    ctx = mem.peek_word(PD.GP + 0x3098)
    expected_end = {}
    # Stream extents from an unpoisoned original run of the same window.
    reference = PD.window_oracle(capture)
    for k in PD.CURSORS:
        start, end = mem.peek_word(ctx + k), reference['memory'].peek_word(ctx + k)
        expected_end[k] = (start, end)
        mem.raw[start:end + 0x40] = bytes([0xa5]) * (end + 0x40 - start)
    mem.scratch[0xb40:0xc40] = bytes([0xa5]) * 0x100
    return mem, {hex(k): [hex(a), hex(b)] for k, (a, b) in expected_end.items()}


def c_window(lib, image, elf, segments):
    memory = CapturedMemory(bytes(image.raw), bytes(image.scratch))
    dma = pad_dma_buffers(memory); memory.watch = [(r['start'], r['end']) for r in dma]
    driver = ContinuousDriver(memory, elf, segments, STATUS_VALUES[0])
    fn = lib.hg_pre_dispatch_service; fn.argtypes = [C.POINTER(ResourceEnv)]; fn.restype = None
    fn(C.byref(driver.env))
    driver.external(0x17f6d0, 0x3f9e30, 0, 0, 0)
    driver.external(0x17f2d0, 0, 0, 0, 0)
    if driver.errors: raise AssertionError(driver.errors)
    return memory


def differential(lib, image, elf, segments):
    original = PD.window_oracle(image, elf, segments)
    c = c_window(lib, image, elf, segments)
    # Everything except the guest stack below the 0012f8a8 sp (host frames).
    ram = PD.changed(original['memory'], c, PD.STACK[0]) | \
        PD.changed(original['memory'], c, 0x2000000, start=PD.NATIVE_SP & ~(PD.CHUNK - 1))
    ram = {a for a in ram if not PD.STACK[0] <= a < PD.NATIVE_SP}
    scratch = [i for i in range(0x4000) if original['memory'].scratch[i] != c.scratch[i]]
    return dict(result='passed' if not ram and not scratch else 'failed',
                ram_differences=len(ram), first_ram_differences=[hex(a) for a in sorted(ram)[:16]],
                scratch_differences=len(scratch), first_scratch_differences=[hex(0x70000000 + i) for i in scratch[:16]],
                identity_mismatches=len(original['identity_mismatches']))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf', type=Path, required=True)
    p.add_argument('--ps2dinfo', type=Path, required=True)
    p.add_argument('--capture-dir', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--max-frames', type=int, default=None)
    p.add_argument('--contract-frames', type=int, default=None,
                   help='run the per-call register variants on the first N selected frames only')
    args = p.parse_args()
    if args.output.exists() or args.output.with_suffix('.so').exists(): raise FileExistsError(args.output)
    if digest(args.elf) != EXPECTED: raise ValueError('Wrong Japanese original executable')
    elf = args.elf.read_bytes(); segments, _ = shared_module(args.ps2dinfo).segments_from_elf(elf)
    lib_path = args.output.with_suffix('.so'); lib = library(output=lib_path)
    folder = args.output.parent / (args.output.stem + '-mutants'); folder.mkdir(parents=True, exist_ok=False)
    source = (ROOT / 'src/resource_loader.c').read_text()
    mutant_path = folder / 'store-width-pre-fix.c'; mutant_path.write_text(PD.mutant_source(source, 'store-width-pre-fix'))
    mutant = library(source=mutant_path, output=folder / 'store-width-pre-fix.so')
    frames = sorted(observation()['frames'], key=lambda fr: fr['counter'])[:args.max_frames]
    rows = []; ok = True
    for index, fr in enumerate(frames):
        cap = CapturedMemory.load_capture(args.capture_dir / (fr['captures']['coordinator-counter']['name'] + '.json'))
        exp = CapturedMemory.load_capture(args.capture_dir / (fr['captures']['dispatch-entry']['name'] + '.json'))
        oracle = PD.window_oracle(cap, elf, segments)
        row = dict(counter=fr['counter'], entry_capture=fr['captures']['coordinator-counter']['name'],
                   dispatch_capture=fr['captures']['dispatch-entry']['name'],
                   oracle=dict(sp=hex(PD.NATIVE_SP), steps=oracle['steps'],
                               identity_mismatches=len(oracle['identity_mismatches']),
                               full_ram_differences=len(PD.changed(oracle['memory'], exp, 0x2000000)),
                               scratch_differences=sum(x != y for x, y in zip(oracle['memory'].scratch, exp.scratch))))
        ok &= row['oracle']['identity_mismatches'] == 0 and row['oracle']['full_ram_differences'] == 0 \
            and row['oracle']['scratch_differences'] == 0
        del oracle
        if args.contract_frames is None or index < args.contract_frames:
            calls = contracts(cap)
            row['calls'] = calls
            row['contract_failures'] = [(c['entry'], m) for c in calls for m in ('poison-1', 'poison-2', 'bridge')
                                        if not c[m]['equal'] or c['original_fault']]
            ok &= not row['contract_failures'] and len(calls) == 31
        image, extents = poisoned(cap)
        row['poison_streams'] = extents
        row['poisoned_differential'] = differential(lib, image, elf, segments)
        ok &= row['poisoned_differential']['result'] == 'passed'
        if index == 0:
            row['poisoned_store_width_pre_fix'] = differential(mutant, image, elf, segments)
            ok &= row['poisoned_store_width_pre_fix']['result'] == 'failed'
        rows.append(row)
        print(fr['counter'], 'oracle', row['oracle']['full_ram_differences'], row['oracle']['scratch_differences'],
              'contracts', len(row.get('calls', [])), row.get('contract_failures'),
              'poisoned', row['poisoned_differential']['result'], row['poisoned_differential']['ram_differences'],
              'mutant', row.get('poisoned_store_width_pre_fix', {}).get('ram_differences'), flush=True)
    summary = {}
    for row in rows:
        for c in row.get('calls', []):
            entry = summary.setdefault(c['entry'], dict(calls=0, sufficient=0, probes={}))
            entry['calls'] += 1
            entry['sufficient'] += all(c[m]['equal'] for m in ('poison-1', 'poison-2', 'bridge'))
            for key in c:
                if key.startswith('drop-'):
                    probe = entry['probes'].setdefault(key, dict(observable=0, calls=0))
                    probe['calls'] += 1; probe['observable'] += not c[key]['equal']
    result = dict(schema='hg-m14-predispatch-contracts/v1', result='passed' if ok else 'failed',
                  elf_sha256=EXPECTED, native_sp=hex(PD.NATIVE_SP), bridge_sp=hex(BRIDGE_SP),
                  contracts={hex(k): v for k, v in CONTRACTS.items()}, contract_summary=summary,
                  verification_sources={n: digest(ROOT / n) for n in SOURCES},
                  library_sha256=digest(lib_path), mutant_library_sha256=digest(folder / 'store-width-pre-fix.so'),
                  smoke_max_frames=args.max_frames, contract_frames=args.contract_frames, frames=rows)
    args.output.write_text(json.dumps(result, indent=1) + '\n')
    print(result['result'], json.dumps(summary))
    return 0 if ok else 1


if __name__ == '__main__': sys.exit(main())
