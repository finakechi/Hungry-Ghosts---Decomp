#!/usr/bin/env python3
"""Bounded original-instruction feasibility trace from an M13 post-counter stop.

The capture supplies EE RAM and scratchpad. The caller's live registers were not
captured; gp is verified from the existing modeled-call contract. The default
sp 0x1fffc70 reproduces every native stack byte of the window exactly
(M14 correction; the earlier 0x1ff8000 assumption left 113 stack differences).
Never infer retail equivalence from this trace alone.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'tests')]
from ee_subset import Machine
from verify_player_connected_runtime import CapturedMemory
from hg_evidence_safety import digest


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--capture', type=Path, required=True)
    p.add_argument('--dispatch', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--elf', type=Path, required=True)
    p.add_argument('--sp', type=lambda v: int(v, 0), default=0x1fffc70,
                   help='0012f8a8 stack pointer (default: verified native value)')
    p.add_argument('--full-trace', action='store_true',
                   help='retain ordered PCs and complete address sets for one diagnostic frame')
    args = p.parse_args()
    if args.output.exists(): raise FileExistsError(args.output)
    mem = CapturedMemory.load_capture(args.capture)
    expected = CapturedMemory.load_capture(args.dispatch)
    reads = set()
    original_get = mem.get
    def observed_get(address,size):
        reads.update(range(address,address+size))
        return original_get(address,size)
    mem.get = observed_get
    machine = Machine()
    machine.mem = mem
    machine.reg[28] = 0x3e7f70
    machine.reg[29] = args.sp
    calls = []
    service_entries = []
    indirect_transfers = []
    instruction_path = []
    def trace(cpu):
        pc = cpu.pc
        if args.full_trace:instruction_path.append(hex(pc))
        if pc in (0x198f60,0x17f6d0,0x17f2d0):
            service_entries.append(dict(pc=hex(pc),a0=hex(cpu.reg[4]&0xffffffff),
                                        a1=hex(cpu.reg[5]&0xffffffff),
                                        a2=hex(cpu.reg[6]&0xffffffff),
                                        a3=hex(cpu.reg[7]&0xffffffff)))
        word = cpu.mem.peek_word(pc)
        if word >> 26 == 3:
            calls.append(dict(pc=hex(pc), target=hex((word & 0x3ffffff) << 2),
                              a0=hex(cpu.reg[4] & 0xffffffff)))
        elif word >> 26 == 0 and word & 0x3f in (8,9):
            kind='jalr' if word & 0x3f == 9 else 'jr'
            target=hex(cpu.reg[(word >> 21) & 31] & 0xffffffff)
            indirect_transfers.append(dict(pc=hex(pc),target=target,kind=kind))
            if kind=='jalr':calls.append(dict(pc=hex(pc),target=target,
                                             a0=hex(cpu.reg[4]&0xffffffff),kind=kind))
    machine.trace = trace
    row = dict(schema='hg-m14-feasibility/v1', entry_capture_sha256=digest(args.capture),
               dispatch_capture_sha256=digest(args.dispatch), gp='0x3e7f70',
               sp=hex(args.sp), start='0x12f8a8', stop='0x158560')
    try:
        machine.run(0x12f8a8, 0x158560, limit=200000)
        row['execution'] = 'reached-dispatch'
    except (ValueError, KeyError) as exc:
        row['execution'] = 'stopped'
        row['error'] = str(exc)
    row['first_stop_pc'] = hex(machine.pc)
    row['calls'] = calls
    row['service_entries'] = service_entries
    row['indirect_transfers'] = indirect_transfers
    if args.full_trace:row['instruction_path'] = instruction_path
    row['steps'] = machine.steps
    row['executed_count'] = len(machine.executed)
    row['reached'] = {hex(pc): pc in machine.executed for pc in
                      (0x12f8a8, 0x198f60, 0x17f6d0, 0x17f2d0, 0x158560)}
    row['registers_at_stop'] = {str(i): hex(v) for i, v in enumerate(machine.reg) if v}
    row['writes'] = [hex(a) for a in sorted(mem.writes)[:128]]
    row['write_count'] = len(mem.writes)
    if args.full_trace:row['write_addresses']=[hex(a) for a in sorted(mem.writes)]
    code_reads = {pc+i for pc in machine.executed for i in range(4)}
    data_reads = sorted(reads-code_reads)
    row['data_read_count'] = len(data_reads)
    row['data_read_first'] = [hex(a) for a in data_reads[:128]]
    if args.full_trace:row['data_read_addresses']=[hex(a) for a in data_reads]
    elf = args.elf.read_bytes()
    phoff, = struct.unpack_from('<I', elf, 0x1c)
    phsize, phnum = struct.unpack_from('<HH', elf, 0x2a)
    segments = []
    for i in range(phnum):
        ptype, off, va, _, nbytes, *_ = struct.unpack_from('<8I', elf, phoff+i*phsize)
        if ptype == 1: segments.append((va, va+nbytes, off))
    mismatches = []
    for pc in machine.executed:
        matches = [off+pc-va for va,end,off in segments if va <= pc and pc+4 <= end]
        if len(matches) != 1 or mem.initial[pc:pc+4] != elf[matches[0]:matches[0]+4]:
            mismatches.append(hex(pc))
    row['elf_sha256'] = digest(args.elf)
    row['instruction_identity_mismatches'] = sorted(mismatches)
    row['executed_addresses'] = [hex(a) for a in sorted(machine.executed)]
    # This is a diagnostic comparison, not an exclusion-based equivalence claim.
    ram_diff = [i for i in range(0x2000000) if mem.raw[i] != expected.raw[i]]
    scratch_diff = [i for i in range(0x4000) if mem.scratch[i] != expected.scratch[i]]
    row['ram_diff_count'] = len(ram_diff)
    row['ram_diff_nonstack_count'] = sum(a < 0x1fe0000 for a in ram_diff)
    row['ram_diff_first'] = [hex(i) for i in ram_diff[:64]]
    row['scratch_diff_count'] = len(scratch_diff)
    row['scratch_diff_first'] = [hex(0x70000000+i) for i in scratch_diff[:64]]
    args.output.write_text(json.dumps(row, indent=2) + '\n')
    print(row['execution'], row.get('error', ''), 'at', row['first_stop_pc'])


if __name__ == '__main__': main()
