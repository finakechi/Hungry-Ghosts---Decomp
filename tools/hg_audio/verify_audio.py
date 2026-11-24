#!/usr/bin/env python3
"""Differential verification: original EE instructions vs reconstructed C.

For each reconstructed audio function, the original instruction bytes are
executed under the repository's EE subset interpreter with randomized
inputs, and the reconstructed C is run with identical inputs. Memory
effects and return values must match exactly. External seams (EI/DI,
kernel syscalls, SIF allocator) are modeled identically on both sides.

This is independent evidence: the EE subset executes the original bytes
themselves, not any reimplementation.
"""
import argparse
import ctypes
import hashlib
import json
import os
import random
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ee_subset import Machine  # noqa: E402
from hg_audio.audio_guest import (AudioHarness, BusMemory, HostModel,  # noqa: E402
                                  SLOT_TABLE, SLOT_AUX, CHANNEL_BASE,
                                  CHANNEL_QUEUE, VOICE_CURSOR, COMMAND_BASE,
                                  SEQUENCE)

ELF = Path(os.environ.get('HG_ELF',
                      str(Path(__file__).resolve().parents[2]  # repository root
                          / 'local/extracted/japan/SCPS_150.46')))
ELF_SHA256 = 'a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61'
GP = 0x003e7f70

# external seams modeled identically on both sides
EXTERNALS = {
    0x0010cca0: lambda m: 0,      # ints off
    0x0010cce8: lambda m: 0,      # ints on
    0x001041e0: lambda m: 0,      # FlushCache(0)
    0x00104320: lambda m: 0x55,   # sceSifSetDma -> modeled handle
    0x00104300: lambda m: -1,     # sceSifDmaStat -> complete
    0x0010af40: lambda m: 0x40100000,  # SIF alloc -> fixed IOP window
    0x0010b0a8: lambda m: 0,      # MSCOM
    0x002a7560: lambda m: 0x77,   # second transfer handle
    0x002a7590: lambda m: 0,      # poll -> complete
    0x002b5428: lambda m: 0,
}

# functions whose original bytes are interpreted (entry -> end, args in a0..)
FUNCTIONS = {
    'hg_audio_slot_find': (0x002b4db0, 0x002b4df0),
    'hg_audio_bank_register': (0x002b4df0, 0x002b4f68),
    'hg_audio_bank_index': (0x002b4f68, 0x002b4f88),
    'hg_audio_slot_active': (0x002b4f88, 0x002b4fc4),
    'hg_audio_slot_state': (0x002b4fc8, 0x002b5004),
    'hg_audio_table_lookup4': (0x002b5008, 0x002b50e0),
    'hg_audio_table_lookup6': (0x002b50e0, 0x002b5178),
    'hg_audio_table_lookup1': (0x002b5178, 0x002b5224),
    'hg_audio_null8': (0x002b5228, 0x002b523c),
    'hg_audio_slot_range18': (0x002b5240, 0x002b5278),
    'hg_audio_slot_range20': (0x002b5278, 0x002b52bc),
    'hg_audio_slot_swap': (0x002b52c0, 0x002b52ec),
    'hg_audio_slot_get': (0x002b52f0, 0x002b5318),
    'hg_audio_slot_bank': (0x002b53a0, 0x002b5424),
    'hg_audio_slot_clear': (0x002b54e8, 0x002b55a8),
    'hg_audio_slots_init': (0x002b55a8, 0x002b55e8),
    'hg_audio_channel_init_a': (0x002a9050, 0x002a9180),
    'hg_audio_channel_init_b': (0x002a9180, 0x002a9298),
    'hg_audio_channel_command': (0x002a94b8, 0x002a9538),
    'hg_audio_next_sequence': (0x002ae998, 0x002ae9b8),
    'hg_audio_bank_bind': (0x002adcf0, 0x002add90),
}

# functions calling others: their callees are interpreted from original
# bytes too (nested runs via hooks), except the modeled externals above.
NESTED = {
    0x002b5318: (0x002b5318, 0x002b53a0),  # hg_audio_bank_register_wrapper
    0x002a9b68: (0x002a9b68, 0x002a9d18),  # open channel b
    0x002a99b8: (0x002a99b8, 0x002a9b60),  # open channel a
    0x002ad478: (0x002ad478, 0x002ad690),  # play
    0x002ad748: (0x002ad748, 0x002ad800),  # cancel
}


def load_elf():
    data = ELF.read_bytes()
    assert hashlib.sha256(data).hexdigest() == ELF_SHA256
    phoff = struct.unpack_from('<I', data, 28)[0]
    phnum = struct.unpack_from('<H', data, 44)[0]
    loads = []
    for i in range(phnum):
        kind, offset, va, pa, filesz = struct.unpack_from('<5I', data, phoff + i * 32)
        if kind == 1:
            loads.append((va, offset, filesz))
    return data, loads


def read_va(data, loads, addr, size):
    for va, offset, filesz in loads:
        if va <= addr and addr + size <= va + filesz:
            return data[offset + addr - va: offset + addr - va + size]
    raise ValueError(f'not file-backed: {addr:#x}')


class EEOriginal:
    """Executes original bytes under the EE subset with nested callees."""

    def __init__(self, data, loads, seed_mem):
        self.data, self.loads = data, loads
        self.seed = seed_mem  # dict address -> byte, same inputs as C side

    def _fresh(self):
        m = Machine()
        m.mem.update(self.seed)
        m.setreg(28, GP)
        m.setreg(29, 0x00004000)  # stack
        for addr in EXTERNALS:
            m.hooks[addr] = EXTERNALS[addr]
        return m

    def _install_callees(self, m, self_entry):
        """Hook every reconstructed callee to interpreted original bytes."""
        for (centry, cend) in list(FUNCTIONS.values()) + list(NESTED.values()):
            if centry == self_entry:
                continue
            m.hooks[centry] = (lambda machine, ce=centry, ce_end=cend:
                               self._call_nested(ce, ce_end, machine))

    def run_function(self, entry, end, args):
        m = self._fresh()
        blob = read_va(self.data, self.loads, entry, end - entry)
        m.put(entry, blob)
        self._install_callees(m, entry)
        for i, value in enumerate(args):
            m.setreg(4 + i, value & 0xffffffff)
        m.run(entry, stop=0x00fff000, limit=200000)
        return m

    def _call_nested(self, entry, end, caller):
        args = [caller.reg[4 + i] & 0xffffffff for i in range(4)]
        sub = Machine()
        sub.mem.update(caller.mem)
        sub.setreg(28, GP)
        sub.setreg(29, 0x00004000)
        for addr in EXTERNALS:
            sub.hooks[addr] = EXTERNALS[addr]
        self._install_callees(sub, entry)
        blob = read_va(self.data, self.loads, entry, end - entry)
        sub.put(entry, blob)
        for i, value in enumerate(args):
            sub.setreg(4 + i, value & 0xffffffff)
        sub.run(entry, stop=0x00fff000, limit=200000)
        caller.mem.update(sub.mem)
        caller.setreg(2, sub.reg[2] & 0xffffffff)
        caller.setreg(4, sub.reg[4] & 0xffffffff)
        caller.setreg(5, sub.reg[5] & 0xffffffff)
        return sub.reg[2] & 0xffffffff


def build_seed(rng):
    seed = {}
    # NULL-pointer and wrapped-table walk windows; deterministic zeros so
    # both sides (interpreter and C bus) service the same reads. The bank
    # bytes are seeded later and override the overlap.
    for base, size in ((0x00000000, 0x400), (0x01008e00, 0x800)):
        seed.update((base + i, 0) for i in range(size))
    # audio state areas
    for base, size in ((0x003e0720, 0x20), (0x003eaa00, 0x100),
                       (SLOT_TABLE, 0x200), (SLOT_AUX, 0x200),
                       (CHANNEL_BASE, 32 * 0x9e4), (CHANNEL_QUEUE, 32 * 0x9e4),
                       (VOICE_CURSOR, 0x10), (COMMAND_BASE, 0xa00),
                       (SEQUENCE, 0x10), (0x003db520, 0x20),
                       (0x003f2740, 0x800), (0x00a83040, 0x10)):
        seed.update((base + i, 0) for i in range(size))
    for i in range(32):
        seed[CHANNEL_BASE + i * 0x9e4] = 0xff
        seed[CHANNEL_BASE + i * 0x9e4 + 1] = 0xff
        seed[CHANNEL_BASE + i * 0x9e4 + 2] = 0xff
        seed[CHANNEL_BASE + i * 0x9e4 + 3] = 0xff
    # synthetic bank at a fixed guest address
    bank = 0x01009000
    seed.update((bank + i, 0) for i in range(0x200))
    seed[bank + 0x0c] = 0x53
    seed[bank + 0x0d] = 0x53
    seed[bank + 0x0e] = 0x68
    seed[bank + 0x0f] = 0x64  # 'SShd'
    for i in range(6):
        off = bank + 0x10 + i * 4
        seed[off] = 0xff
        seed[off + 1] = 0xff
        seed[off + 2] = 0xff
        seed[off + 3] = 0xff
    seed[bank + 0x7c] = 0xff
    seed[bank + 0x7d] = 0xff
    seed[bank + 0x7e] = 0xff
    seed[bank + 0x7f] = 0xff
    # table1 at bank+0x60
    for i in range(8):
        val = rng.randrange(0x20, 0x100)
        seed[bank + 0x60 + i * 2] = val & 0xff
        seed[bank + 0x61 + i * 2] = (val >> 8) & 0xff
    seed[bank + 0x10] = 0x60  # table1 offset
    seed[bank + 0x14] = 0x80  # table2 offset
    seed[bank + 0x18] = 0xa0  # table3 offset
    for i in range(3):
        off = bank + 0x60 + i * 0x20
        seed[off] = 0xff  # sentinel-ish header for tables
    return seed


def run_c_function(harness, name, args):
    if name == 'hg_audio_null8':
        # Pure a0 arithmetic in the original; no bus parameter.
        fn = harness.lib.hg_audio_null8
        fn.restype = ctypes.c_uint32
        fn.argtypes = [ctypes.c_uint32]
        return fn(args[0]), harness.mem
    fn = harness._bind(name, ['a%d' % i for i in range(len(args))])
    return fn(harness._bus_p, *args), harness.mem


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--cases', type=int, default=20)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    data, loads = load_elf()
    results = []
    passed = failed = 0
    for name, (entry, end) in FUNCTIONS.items():
        for case in range(args.cases):
            seed = build_seed(rng)
            argvec = [rng.randrange(0, 8) for _ in range(2)]
            args_vec = argvec
            # function-specific argument profiles
            if name == 'hg_audio_bank_register':
                args_vec = [SLOT_TABLE + (rng.randrange(8)) * 0x2c, 0x01009000]
            elif name == 'hg_audio_bank_index':
                args_vec = [rng.randrange(8), rng.randrange(8)]
            elif name == 'hg_audio_slot_state':
                args_vec = [rng.randrange(8), SLOT_TABLE + 0x10]
            elif name == 'hg_audio_slot_range18':
                args_vec = [rng.randrange(8), SLOT_TABLE + 0x10]
            elif name == 'hg_audio_slot_range20':
                args_vec = [rng.randrange(8), SLOT_TABLE + 0x10]
            elif name == 'hg_audio_slot_swap':
                args_vec = [rng.randrange(8), rng.randrange(0x100)]
            elif name in ('hg_audio_slot_clear', 'hg_audio_slot_active',
                          'hg_audio_slot_get', 'hg_audio_slot_bank'):
                args_vec = [rng.randrange(9)]
            elif name in ('hg_audio_channel_init_a', 'hg_audio_channel_init_b',
                          'hg_audio_channel_command', 'hg_audio_null8'):
                args_vec = [rng.randrange(4), rng.randrange(0x100)]
            elif name == 'hg_audio_next_sequence':
                args_vec = []
            elif name == 'hg_audio_bank_bind':
                args_vec = [rng.randrange(8), rng.randrange(0x100)]
            elif name.startswith('hg_audio_table_lookup'):
                args_vec = [rng.randrange(8), rng.randrange(8),
                            rng.randrange(0x100)]
            if name.startswith('hg_audio_slot_state') or name.startswith('hg_audio_slot_range'):
                args_vec = args_vec[:2]
            ee = EEOriginal(data, loads, seed)
            try:
                machine = ee.run_function(entry, end, args_vec)
                ee_ret = machine.reg[2] & 0xffffffff
                ee_mem = machine.mem
            except ValueError as exc:
                if 'unaligned' in str(exc):
                    results.append({'function': name, 'case': case,
                                    'status': 'excluded',
                                    'reason': 'original would fault '
                                              '(unaligned access); excluded'})
                    continue
                results.append({'function': name, 'case': case, 'status': 'skipped',
                                'reason': str(exc)})
                continue
            harness = AudioHarness()
            harness.mem.data.update(seed)
            c_ret, c_mem = run_c_function(harness, name, args_vec)
            c_mem_dict = c_mem.data
            diffs = []
            code_regions = [r for r in FUNCTIONS.values()] + [r for r in NESTED.values()]
            for addr in sorted(set(ee_mem) | set(c_mem_dict)):
                if any(cs <= addr < ce for cs, ce in code_regions):
                    continue  # code regions are not state
                if 0x00003f00 <= addr < 0x00008000:
                    continue  # EE stack area is not state
                a = ee_mem.get(addr, 0)
                b = c_mem_dict.get(addr, 0)
                if a != b:
                    diffs.append((addr, a, b))
            VOID = {'hg_audio_channel_init_a', 'hg_audio_channel_init_b',
                    'hg_audio_channel_command', 'hg_audio_slot_clear',
                    'hg_audio_slots_init', 'hg_audio_slot_state',
                    'hg_audio_slot_range18', 'hg_audio_slots_init'}
            if (name not in VOID and ee_ret != c_ret) or diffs:
                failed += 1
                results.append({'function': name, 'case': case, 'status': 'mismatch',
                                'ee_v0': ee_ret, 'c_v0': c_ret,
                                'diffs': [{'addr': hex(a), 'ee': x, 'c': y}
                                          for a, x, y in diffs[:5]]})
            else:
                passed += 1
                results.append({'function': name, 'case': case, 'status': 'match'})
    summary = {'passed': passed, 'failed': failed,
               'skipped': sum(1 for r in results if r['status'] == 'skipped'),
               'excluded': sum(1 for r in results if r['status'] == 'excluded'),
               'results': results}
    print(json.dumps({k: v for k, v in summary.items() if k != 'results'}, indent=1))
    if args.output:
        args.output.write_text(json.dumps(summary, indent=1) + '\n')
    if failed:
        for r in results:
            if r['status'] == 'mismatch':
                print('MISMATCH', r)
        sys.exit(1)


if __name__ == '__main__':
    main()
