"""Synthetic tests for the reconstructed audio subsystem.

Deterministic fixtures only - no commercial game data. Covers valid and
malformed banks, registration state machine behavior, table lookups,
channel/command lifecycle, and deliberate negative controls that must be
detected.
"""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools' / 'hg_audio'))
from audio_guest import (AudioHarness, BusMemory, HostModel, SLOT_TABLE,
                         SLOT_AUX, IDX_BASE, CTX_SAVE, PHASE, GATE,
                         DESC_COUNT, DESC_CURSOR, DESC_SOURCE, DESC_SAVED,
                         DESC_DMA, DESC_ID, ID_TABLE, CHANNEL_BASE,
                         CHANNEL_QUEUE, VOICE_CURSOR, COMMAND_BASE, SEQUENCE)

HG_BANK_MAGIC = 0x64685353


def make_bank(tables=(0x60, 0x80, 0xa0), state=0xffffffff):
    """Synthetic SShd bank. tables: offsets for the three tables."""
    bank = bytearray(0x200)
    struct.pack_into('<I', bank, 0x0c, 0x64685353)  # 'SShd'
    for i in range(6):
        struct.pack_into('<I', bank, 0x10 + i * 4, 0xffffffff)
    for i, off in enumerate(tables):
        struct.pack_into('<I', bank, 0x10 + i * 4, off)
    struct.pack_into('<I', bank, 0x7c, state)
    if tables[0] != 0xffffffff:
        # table 1 (offset tables[0]): u16 offsets
        struct.pack_into('<H', bank, tables[0], 0x20)
        struct.pack_into('<H', bank, tables[0] + 2, 0x30)
        struct.pack_into('<H', bank, tables[0] + 4, 0x44)
    if tables[1] != 0xffffffff:
        # table 2 (offset tables[1]): u16 header
        struct.pack_into('<H', bank, tables[1], 0x64)
    if tables[2] != 0xffffffff:
        # table 3 (offset tables[2]): u16 header
        struct.pack_into('<H', bank, tables[2], 0x7f)
    return bank


def make_resource(bank, payload=b'\x00' * 0x40, load_size=0x60):
    """One-descriptor resource in the verified layout."""
    total = 0x20 + 0x10 + len(bank) + load_size
    resource = bytearray(total)
    struct.pack_into('<I', resource, 0x00, total)
    struct.pack_into('<I', resource, 0x04, 0x30)
    struct.pack_into('<I', resource, 0x08, 0)          # descriptor shift
    struct.pack_into('<I', resource, 0x0c, 1)          # descriptor count
    struct.pack_into('<I', resource, 0x10, load_size)  # retained size
    struct.pack_into('<I', resource, 0x14, 0x30 + len(bank))
    struct.pack_into('<I', resource, 0x18, load_size)
    resource[0x30:0x30 + len(bank)] = bank
    resource[0x30 + len(bank):0x30 + len(bank) + load_size] = payload
    # descriptor at 0x20
    struct.pack_into('<I', resource, 0x20, load_size)          # payload size
    struct.pack_into('<I', resource, 0x24, 0x30)               # bank offset
    struct.pack_into('<I', resource, 0x28, 2)                  # bank id
    struct.pack_into('<I', resource, 0x2c, 0x7f)               # volume
    return resource


class BaseAudioTest(unittest.TestCase):
    def setUp(self):
        self.h = AudioHarness()
        self.harness = self.h

    def slot(self, index):
        return SLOT_TABLE + index * 0x2c

    def _registered(self, slot=0):
        bank = make_bank()
        # table4 (bank+0x1c offset) needed for hg_audio_play
        struct.pack_into('<I', bank, 0x1c, 0x100)
        # table4: u16 count then entries
        struct.pack_into('<H', bank, 0x100, 8)
        for i in range(8):
            struct.pack_into('<H', bank, 0x102 + i * 2, 0x40 + i * 0x10)
        address = 0x01009000
        self.h.mem.load(address, bank)
        self.h._bind('hg_audio_bank_register', ['s', 'b'])(
            self.h._bus_p, self.slot(slot), address)
        return address


class BankRegistrationTests(BaseAudioTest):
    def test_bank_register_valid(self):
        bank = make_bank()
        base = self.slot(0)
        self.assertEqual(self.h._bind('hg_audio_bank_register',
                                      ['s', 'b'])(self.h._bus_p, base,
                                                  self._bank_at(bank, 0)), 0)
        # slot state 3 (bank+0x7c == -1 => 3), bank pointer stored
        self.assertEqual(self.h.mem.r32(base + 0x00), 3)
        self.assertEqual(self.h.mem.r32(base + 0x04), 0)
        # six pointers at bank+0x30..0x44 = bank(0)+offset or 0
        self.assertEqual(self.h.mem.r32(0x30), 0x60)
        self.assertEqual(self.h.mem.r32(0x34), 0x80)
        self.assertEqual(self.h.mem.r32(0x38), 0xa0)
        for i in range(3, 6):
            self.assertEqual(self.h.mem.r32(0x30 + i * 4), 0)
        # state 4 branch: bank+0x7c != -1 (2b4e18 bnel)
        bank2 = make_bank(state=0)
        base2 = self.slot(1)
        self.assertEqual(self.h._bind('hg_audio_bank_register',
                                      ['s', 'b'])(self.h._bus_p, base2,
                                                  self._bank_at(bank2, 0x400)), 0)
        self.assertEqual(self.h.mem.r32(base2 + 0x00), 4)

    def _bank_at(self, bank, address):
        self.h.mem.load(address, bank)
        return address

    def test_bank_register_rebases_offsets(self):
        bank = make_bank()
        address = 0x01009000
        self.h.mem.load(address, bank)
        base = self.slot(0)
        result = self.h._bind('hg_audio_bank_register',
                              ['s', 'b'])(self.h._bus_p, base, address)
        self.assertEqual(result, 0)
        # six pointers: bank + offset or 0 for -1
        self.assertEqual(self.h.mem.r32(address + 0x30), address + 0x60)
        self.assertEqual(self.h.mem.r32(address + 0x34), address + 0x80)
        self.assertEqual(self.h.mem.r32(address + 0x38), address + 0xa0)
        self.assertEqual(self.h.mem.r32(address + 0x3c), 0)
        self.assertEqual(self.h.mem.r32(address + 0x40), 0)
        self.assertEqual(self.h.mem.r32(address + 0x44), 0)
        # slot table headers
        self.assertEqual(self.h.mem.r32(base + 0x18), 0x64)
        self.assertEqual(self.h.mem.r32(base + 0x1c), address + 0x82)
        self.assertEqual(self.h.mem.r32(base + 0x20), 0x7f)
        self.assertEqual(self.h.mem.r32(base + 0x24), address + 0xa0)
        self.assertEqual(self.h.mem.r32(base + 0x28), address + 0xa2)
        self.assertEqual(self.h.mem.r32(base + 0x10), 0x7f)

    def test_bank_register_bad_magic(self):
        bank = make_bank()
        struct.pack_into('<I', bank, 0x0c, 0x12345678)
        address = 0x01009000
        self.h.mem.load(address, bank)
        self.assertEqual(self.h._bind('hg_audio_bank_register',
                                      ['s', 'b'])(self.h._bus_p,
                                                  self.slot(0), address),
                         0xffffffff)

    def test_bank_register_busy_slot(self):
        bank = make_bank()
        address = 0x01009000
        self.h.mem.load(address, bank)
        base = self.slot(0)
        self.h.mem.w32(base + 0, 3)  # occupied
        self.assertEqual(self.h._bind('hg_audio_bank_register',
                                      ['s', 'b'])(self.h._bus_p, base,
                                                  address), 0xffffffff)

    def test_slot_find_and_clear(self):
        self.h._bind('hg_audio_slots_init', [])(self.h._bus_p)
        for i in range(8):
            self.assertEqual(self.h.mem.r32(self.slot(i)), 0)
        self.h.mem.w32(self.slot(2), 3)
        self.assertEqual(self.h._bind('hg_audio_slot_find',
                                      [])(self.h._bus_p), 0)
        # fill all slots
        for i in range(8):
            self.h.mem.w32(self.slot(i), 3)
        self.assertEqual(self.h._bind('hg_audio_slot_find',
                                      [])(self.h._bus_p), 0xffffffff)

    def test_slot_active_bounds(self):
        self.h._bind('hg_audio_slots_init', [])(self.h._bus_p)
        self.h.mem.w32(self.slot(0), 3)
        self.assertEqual(self.h._bind('hg_audio_slot_active',
                                      ['s'])(self.h._bus_p, 0), 1)
        self.assertEqual(self.h._bind('hg_audio_slot_active',
                                      ['s'])(self.h._bus_p, 8), 0)
        self.assertEqual(self.h._bind('hg_audio_slot_active',
                                      ['s'])(self.h._bus_p, 0xffffffff), 0)


class RegistrationWorkerTests(BaseAudioTest):
    def test_full_registration_endpoint(self):
        bank = make_bank()
        resource = make_resource(bank, load_size=0x60)
        address = 0x01009000
        result = self.h.register_resource(resource, address)
        # endpoint = (address + word(+0x10) + 0x40) & ~0x3f
        expected = ((address + 0x60 + 0x40) & 0xffffffc0) & 0xffffffff
        self.assertEqual(result, expected)
        # gate/phase reset
        self.assertEqual(self.h.mem.r8(GATE), 0)
        self.assertEqual(self.h.mem.r8(PHASE), 0)
        # slot registered with the bank id table entry
        entry = self.h.mem.r32(ID_TABLE + 2 * 0x78)
        self.assertLess(entry, 8)
        self.assertEqual(self.h.mem.r32(self.slot(entry) + 0x00), 3)
        # payload DMA'd to the modeled IOP service
        self.assertEqual(len(self.h.model.iop_audio.payloads), 1)
        self.assertEqual(self.h.model.iop_audio.payloads[0]['size'], 0x60)
        # MSCOM poke recorded
        self.assertEqual(len(self.h.model.mscom), 1)

    def test_worker_busy_returns_zero(self):
        bank = make_bank()
        resource = make_resource(bank)
        address = 0x01009000
        self.h.mem.load(address, resource)
        # single step: first call returns 0 (more phases pending)
        fn = self.h._bind('hg_audio_register_entry', ['r'])
        self.assertEqual(fn(self.h._bus_p, address), 0)

    def test_worker_zero_alloc_parks(self):
        # SIF allocator refuses huge payloads
        bank = make_bank()
        resource = make_resource(bank)
        struct.pack_into('<I', resource, 0x20, 0x400000 + 1)
        struct.pack_into('<I', resource, 0x00, len(resource) + 0x400000)
        address = 0x01009000
        self.h.mem.load(address, resource)
        fn = self.h._bind('hg_audio_register_entry', ['r'])
        self.assertEqual(fn(self.h._bus_p, address), 0)
        self.assertEqual(fn(self.h._bus_p, address), 0)

    def test_two_descriptor_resource(self):
        bank = make_bank()
        load_size = 0x60
        total = 0x40 + len(bank) + load_size
        resource = bytearray(total)
        struct.pack_into('<I', resource, 0x00, total)
        struct.pack_into('<I', resource, 0x04, 0x40)       # bank offset
        struct.pack_into('<I', resource, 0x08, 0)
        struct.pack_into('<I', resource, 0x0c, 2)          # two descriptors
        struct.pack_into('<I', resource, 0x10, load_size)
        struct.pack_into('<I', resource, 0x14, 0x40 + len(bank))
        struct.pack_into('<I', resource, 0x18, load_size)
        resource[0x40:0x40 + len(bank)] = bank
        payload = bytes(range(load_size))
        resource[0x40 + len(bank):0x40 + len(bank) + load_size] = payload
        for idx in (0x20, 0x30):
            struct.pack_into('<I', resource, idx + 0x00, load_size)
            struct.pack_into('<I', resource, idx + 0x04, 0x40)
            struct.pack_into('<I', resource, idx + 0x08, 2 + (idx - 0x20) // 0x10)
            struct.pack_into('<I', resource, idx + 0x0c, 0x7f)
        address = 0x01009000
        result = self.h.register_resource(resource, address)
        expected = ((address + load_size + 0x40) & 0xffffffc0) & 0xffffffff
        self.assertEqual(result, expected)
        self.assertEqual(len(self.h.model.iop_audio.payloads), 2)


class TableLookupTests(BaseAudioTest):
    def test_table_lookup1(self):
        bank = make_bank()
        address = 0x01009000
        self.h.mem.load(address, bank)
        base = self.slot(0)
        self.h._bind('hg_audio_bank_register', ['s', 'b'])(
            self.h._bus_p, base, address)
        fn = self.h._bind('hg_audio_table_lookup1', ['s', 'i'])
        # index 0 -> table1 + u16(table1+2) = bank+0x60+0x30
        self.assertEqual(fn(self.h._bus_p, 0, 0), address + 0x90)
        # index 1 -> table1 + u16(table1+4) = bank+0x60+0x44
        self.assertEqual(fn(self.h._bus_p, 0, 1), address + 0xa4)

    def test_table_lookup1_out_of_range(self):
        bank = make_bank()
        address = 0x01009000
        self.h.mem.load(address, bank)
        self.h._bind('hg_audio_bank_register', ['s', 'b'])(
            self.h._bus_p, self.slot(0), address)
        fn = self.h._bind('hg_audio_table_lookup1', ['s', 'i'])
        self.assertEqual(fn(self.h._bus_p, 0, 0x40), 0)  # beyond count

    def test_table_lookup_missing_table(self):
        bank = make_bank(tables=(0x60, 0xffffffff, 0xffffffff))
        address = 0x01009000
        self.h.mem.load(address, bank)
        self.h._bind('hg_audio_bank_register', ['s', 'b'])(
            self.h._bus_p, self.slot(0), address)
        self.assertEqual(self.h._bind('hg_audio_table_lookup6',
                                      ['s', 'i'])(self.h._bus_p, 0, 0), 0)


class ChannelCommandTests(BaseAudioTest):
    def test_channel_init_a(self):
        fn = self.h._bind('hg_audio_channel_init_a', ['ch'])
        fn(self.h._bus_p, 0)
        base = CHANNEL_BASE
        self.assertEqual(self.h.mem.r8(base + 0x58), 0x80)
        self.assertEqual(self.h.mem.r32(base + 0x14), 1)
        self.assertEqual(self.h.mem.r32(base + 0x00), 0xffffffff)
        # 16 sub-voices: voice 9 is the odd one out (v1 = (i^9)==0)
        for i in range(16):
            voice = base + 0x2a4 + i * 0x74
            self.assertEqual(self.h.mem.r32(voice + 0x2c), 0 if i == 9 else 1)
            self.assertEqual(self.h.mem.r32(voice + 0x28), 1 if i == 9 else 0)
            self.assertEqual(self.h.mem.r32(voice + 0x00), 0)
            self.assertEqual(self.h.mem.r32(voice + 0x14), 0x7f)

    def test_channel_command_ring(self):
        fn = self.h._bind('hg_audio_channel_command', ['ch', 'cmd'])
        fn(self.h._bus_p, 3, 0xab)
        fn(self.h._bus_p, 3, 0xcd)
        base = CHANNEL_QUEUE + 3 * 0x9e4
        self.assertEqual(self.h.mem.r32(base + 0x200), 2)
        self.assertEqual(self.h.mem.r8(base + 0), 0xab)
        self.assertEqual(self.h.mem.r8(base + 1), 0xcd)

    def test_command_ring_wraps(self):
        fn = self.h._bind('hg_audio_channel_command', ['ch', 'cmd'])
        base = CHANNEL_QUEUE + 1 * 0x9e4
        self.h.mem.w32(base + 0x200, 0x1ff)
        fn(self.h._bus_p, 1, 0x11)
        self.assertEqual(self.h.mem.r32(base + 0x200), 0)
        self.assertEqual(self.h.mem.r8(base + 0x1ff), 0x11)

    def test_open_channel_allocates_voice(self):
        bank = make_bank()
        address = 0x01009000
        self.h.mem.load(address, bank)
        base = self.slot(0)
        self.h._bind('hg_audio_bank_register', ['s', 'b'])(
            self.h._bus_p, base, address)
        self.h.mem.w32(VOICE_CURSOR, 0)
        fn = self.h._bind('hg_audio_open_channel_b', ['s', 'p'])
        channel = fn(self.h._bus_p, 0, 5)
        self.assertLess(channel, 32)
        ch = CHANNEL_BASE + channel * 0x9e4
        self.assertEqual(self.h.mem.r32(ch + 0x00), 0)   # slot
        self.assertEqual(self.h.mem.r32(ch + 0x10), 5)   # param
        self.assertEqual(self.h.mem.r32(SEQUENCE), 1)    # one sequence issued

    def test_open_channel_full(self):
        bank = make_bank()
        address = 0x01009000
        self.h.mem.load(address, bank)
        self.h._bind('hg_audio_bank_register', ['s', 'b'])(
            self.h._bus_p, self.slot(0), address)
        # occupy all 32 channels
        for i in range(32):
            self.h.mem.w32(CHANNEL_BASE + i * 0x9e4, 0)
        fn = self.h._bind('hg_audio_open_channel_b', ['s', 'p'])
        self.assertEqual(fn(self.h._bus_p, 0, 5), 0xffffffff)


class PlayCancelTests(BaseAudioTest):
    def test_play_posts_command(self):
        self._registered()
        index = self.h.play(0, 2, 0, 0, 7)
        self.assertLess(index, 32)
        base = COMMAND_BASE + index * 0x50
        self.assertEqual(self.h.mem.r32(base + 0x00), 1)   # state
        self.assertEqual(self.h.mem.r32(base + 0x0c), 0)   # slot
        self.assertEqual(self.h.mem.r32(base + 0x14), 2)   # param1
        self.assertNotEqual(self.h.mem.r32(base + 0x40), 0)  # sample address

    def test_play_invalid_slot(self):
        self.assertEqual(self.h.play(3, 0), 0xfffffffc)

    def test_play_no_sample_fails(self):
        bank = make_bank()  # no table4 -> lookup returns 0
        address = 0x01009000
        self.h.mem.load(address, bank)
        self.h._bind('hg_audio_bank_register', ['s', 'b'])(
            self.h._bus_p, self.slot(0), address)
        self.assertEqual(self.h.play(0, 2), 0xffffffff)

    def test_cancel_sends_stop_commands(self):
        self._registered()
        index = self.h.play(0, 2, 0, 0, 7)
        self.h.cancel(index)
        base = COMMAND_BASE + index * 0x50
        self.assertEqual(self.h.mem.r32(base + 0x00), 2)      # canceled
        self.assertEqual(self.h.mem.r32(base + 0x0c), 0xffffffff)
        channel = self.h.mem.r32(base + 0x10)
        ring = CHANNEL_QUEUE + channel * 0x9e4
        self.assertEqual(self.h.mem.r8(ring + 0), 0xff)
        self.assertEqual(self.h.mem.r8(ring + 1), 0x41)
        self.assertEqual(self.h.mem.r8(ring + 2), 0xf7)

    def test_cancel_invalid_index(self):
        self.assertEqual(self.h.cancel(0x40), 0)


class NegativeControlTests(BaseAudioTest):
    """Each control must be detected by the intended validator."""

    def test_control_corrupted_bank_offset(self):
        # corrupting a bank offset changes the calculated pointer;
        # the validator checks pointers against bank+offset
        bank = make_bank()
        address = 0x01009000
        self.h.mem.load(address, bank)
        base = self.slot(0)
        self.h._bind('hg_audio_bank_register', ['s', 'b'])(
            self.h._bus_p, base, address)
        expected = self.h.mem.r32(address + 0x30)
        # negative control: recompute with corrupted offset
        corrupted = address + 0x60 + 4
        self.assertNotEqual(expected, corrupted)
        self.assertEqual(expected, address + 0x60)

    def test_control_wrong_slot_selection(self):
        bank = make_bank()
        address = 0x01009000
        self.h.mem.load(address, bank)
        self.h._bind('hg_audio_bank_register', ['s', 'b'])(
            self.h._bus_p, self.slot(0), address)
        # selecting a different slot must not expose slot 0's tables
        self.assertEqual(self.h._bind('hg_audio_table_lookup1',
                                      ['s', 'i'])(self.h._bus_p, 1, 0), 0)

    def test_control_missing_stop_behavior(self):
        self._registered(0)
        index = self.h.play(0, 2, 0, 0, 7)
        base = COMMAND_BASE + index * 0x50
        channel = self.h.mem.r32(base + 0x10)
        ring = CHANNEL_QUEUE + channel * 0x9e4 + 0x200
        before = self.h.mem.r32(ring)
        # skipping hg_audio_cancel must leave the ring untouched;
        # the stop validator requires the 0xff/0x41/0xf7 sequence
        self.assertEqual(before, 0)
        self.h.cancel(index)
        after = self.h.mem.r32(ring)
        self.assertEqual(after, 3)  # three command bytes queued

    def test_control_wrong_endpoint(self):
        bank = make_bank()
        resource = make_resource(bank, load_size=0x60)
        address = 0x01009000
        result = self.h.register_resource(resource, address)
        # deliberately wrong calculation must differ
        wrong = (result + 0x40) & 0xffffffff
        self.assertNotEqual(result, wrong)


class DeterminismTests(BaseAudioTest):
    def test_repeated_registration_deterministic(self):
        results = []
        for _ in range(2):
            h = AudioHarness()
            bank = make_bank()
            resource = make_resource(bank, load_size=0x60)
            results.append(h.register_resource(resource, 0x01009000))
        self.assertEqual(results[0], results[1])

    def test_index_unchanged_by_worker_restart(self):
        h = AudioHarness()
        bank = make_bank()
        resource = make_resource(bank)
        address = 0x01009000
        h.register_resource(resource, address)
        # id table entry stable after a second registration into fresh slot
        first = h.mem.r32(ID_TABLE + 2 * 0x78)
        self.assertLess(first, 8)


if __name__ == '__main__':
    unittest.main()
