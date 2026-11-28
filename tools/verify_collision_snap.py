#!/usr/bin/env python3
"""Replay the SCPS-15046 floor snap between the two recorded M25 stops."""

import argparse
from collections import Counter
from hashlib import sha256
from itertools import groupby
import json
from pathlib import Path
import struct
import zlib

from ee_subset import Machine
from hg_identify import elf32, va_to_offset
from verify_player_connected_runtime import CapturedMemory, STACK_EXCLUSION

ROOT = Path(__file__).resolve().parents[1]
ELF_ID = 'a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61'
LIVE_ID = '815855b6d1d13f34cfd17c2da0b05aa0343f2699874c9facf6e69e5fc76e0521'
PLAYER = 0x3F92D0
ENTRY, CALLER, RETURN = 0x1E3440, 0x1585C8, 0x1585E0
SCRATCH = 0x70000000


def h(value):
    return f'{value:08x}'


def checked(data, expected, label):
    if sha256(data).hexdigest() != expected:
        raise ValueError(f'{label}: SHA-256 mismatch')
    return data


def read_stop(live, receipt, name, pc):
    stop, = (row for row in receipt['stops'] if row['name'] == name)
    if stop['pc'] != h(pc):
        raise ValueError(f'{name}: wrong stop PC')
    base = live / 'full'
    images = []
    for ext, size in [('ee', 0x2000000), ('sp', 0x4000)]:
        data = zlib.decompress((base / f'{name}.{ext}.z').read_bytes())
        if len(data) != size:
            raise ValueError(f'{name}: wrong {ext} size')
        images.append(checked(data, stop[f'{ext}_sha256'], name))
    checked((base / f'{name}-debugger.png').read_bytes(), stop['debugger_png_sha256'], name)
    return tuple(images)


def runs(addresses):
    for _, group in groupby(enumerate(sorted(addresses)), key=lambda row: row[1] - row[0]):
        cluster = [pc for _, pc in group]
        yield cluster[0], cluster[-1] + 1


def target(pc, word):
    return ((pc + 4) & 0xf0000000) | ((word & 0x03ffffff) << 2) if word >> 26 == 3 else None


def position(memory, pointer):
    return list(struct.unpack('<4f', memory.peek(pointer, 16)))


def replay(elf, mapping, before, *, offsets=None):
    memory = CapturedMemory(*before)
    machine = Machine()
    machine.mem = memory
    machine.reg[28], machine.reg[29], machine.reg[16] = 0x3e7f70, 0x1ff0800, PLAYER
    calls = Counter()
    arguments = {}

    def trace(cpu):
        pc = cpu.pc
        offset = va_to_offset(mapping, pc)
        word = memory.peek_word(pc)
        if word != struct.unpack_from('<I', elf, offset)[0]:
            raise ValueError(f'executed word differs from authenticated ELF at {h(pc)}')
        callee = target(pc, word)
        if callee is not None:
            calls[pc, callee] += 1
        if pc == 0x144820:
            arguments.update(player=h(cpu.reg[4] & 0xffffffff), mask=f'{cpu.reg[7] & 0xffffffff:#x}',
                             start=position(memory, cpu.reg[5] & 0xffffffff),
                             end=position(memory, cpu.reg[6] & 0xffffffff))

    machine.trace = trace
    start = CALLER
    if offsets is not None:
        start = ENTRY
        machine.reg[4] = PLAYER
        machine.fpr[12], machine.fpr[13] = struct.unpack('<2I', struct.pack('<2f', *offsets))
    machine.run(start, stop=RETURN, limit=500_000)
    instruction_ranges = [(a * 4, b * 4) for a, b in runs(pc // 4 for pc in machine.executed)]
    fingerprint = sha256()
    for low, high in instruction_ranges:
        offset = va_to_offset(mapping, low, high - low)
        fingerprint.update(elf[offset:offset + high - low])
    return memory, dict(query=arguments, return_v0=h(machine.reg[2] & 0xffffffff),
                        position=position(memory, PLAYER + 0x30), steps=machine.steps,
                        distinct_instruction_addresses=len(machine.executed),
                        executed_words_sha256=fingerprint.hexdigest(),
                        executed_ranges=[dict(start=h(low), end_exclusive=h(high))
                                         for low, high in instruction_ranges],
                        direct_calls=[dict(site=h(pc), target=h(callee), calls=count)
                                      for (pc, callee), count in calls.items()])


def verify(elf_path, receipt_path, live):
    elf = checked(elf_path.read_bytes(), ELF_ID, 'original ELF')
    mapping = elf32(elf)
    receipt = json.loads(checked(receipt_path.read_bytes(), LIVE_ID, 'M25 receipt'))
    before = read_stop(live, receipt, 's1-00158a10-return', CALLER)
    after = read_stop(live, receipt, 's2-001e3440-return', RETURN)
    first, last = CapturedMemory(*before), CapturedMemory(*after)

    memory, same = replay(elf, mapping, before)
    lo, hi = STACK_EXCLUSION
    writes = {pc for pc in memory.writes if not lo <= pc < hi}
    write_runs = []
    for start, end in runs(writes):
        size = end - start
        observed = last.peek(start, size)
        if memory.peek(start, size) != observed:
            raise ValueError(f'replay disagrees with snapshot at {h(start)}')
        changed = sum(a != b for a, b in zip(first.peek(start, size), observed))
        write_runs.append(dict(start=h(start), end_exclusive=h(end), observed_changed_bytes=changed))
    changed = {i for i, (a, b) in enumerate(zip(before[0], after[0])) if a != b and not lo <= i < hi}
    changed.update(SCRATCH + i for i, (a, b) in enumerate(zip(before[1], after[1])) if a != b)
    if changed - writes:
        raise ValueError('unexpected native memory changes outside the replay writes')
    if same['return_v0'] != '00000100' or same['position'] != position(last, PLAYER + 0x30):
        raise ValueError('unexpected collision hit or player position')

    controls = []
    for offsets in ((-8., -3.), (8., 2.), (0., 0.)):
        _, case = replay(elf, mapping, before, offsets=offsets)
        if case['return_v0'] != '00000000' or case['position'] != position(first, PLAYER + 0x30):
            raise ValueError('perturbed segment did not exercise the expected miss')
        controls.append({key: case[key] for key in ('query', 'return_v0', 'position', 'steps')})
    sites = []
    for segment in mapping['pt_load']:
        if not segment['flags'] & 1:
            continue
        offset, size, va = segment['offset'], segment['filesz'], segment['vaddr']
        for i, (word,) in enumerate(struct.iter_unpack('<I', elf[offset:offset + size])):
            pc = va + i * 4
            if target(pc, word) == ENTRY:
                sites.append(h(pc))

    return dict(schema='hg-collision-snap/v1', elf_sha256=ELF_ID, m25_receipt_sha256=LIVE_ID,
                direct_jal_word_sites=sites,
                same_run=dict(passed=True, before=h(CALLER), after=h(RETURN),
                              initial_position=position(first, PLAYER + 0x30),
                              nonstack_write_ranges=write_runs, **same),
                perturbed_segments=controls)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('elf', 'm25-receipt', 'live', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.elf, args.m25_receipt, args.live)
    with args.output.open('x') as output:
        output.write(json.dumps(result, indent=2) + '\n')
    print('PASS: authenticated M25 stop pair, original hit replay and three perturbed misses')


if __name__ == '__main__':
    main()
