"""Synthetic M14 00198f60 original-instruction versus C differential.

The original words come from the tracked, original-byte-verified packet. The
memory image is synthetic: no retail capture, ELF or expected output is used.
Packet and scratch buffers start poisoned (0xA5), so a store the C omits or
widens cannot be hidden by stale buffer contents. Callees are host stubs that
clobber caller-saved registers and, in one profile, advance the packet cursor.
"""
import ctypes as C
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from resource_harness import Driver, Memory, ResourceEnv, ROOT, library
sys.path.insert(0, str(ROOT / 'tools'))
import predispatch_differential as PD

CTX = 0x500000
BUFFER = 0x510000
SP = PD.NATIVE_SP
RECORDS = 0x39e960
POISON = 0xa5
REG_POISON = 0xdeadbeef_a5a5a5a5_5a5a5a5a_c0dec0de
FP_POISON = 0x41a5a5a5  # finite


def bits(x): return struct.unpack('<I', struct.pack('<f', x))[0]


def records():
    """Twelve observed-shape records: indices 0..3, flags 0/3/9, signed steps
    covering no wrap, negative wrap, above-limit wrap and zero step."""
    rows = []
    steps = (-1.0, -70.0, 5.5, 100.0, 0.0, -0.25, 63.0, -64.0, 1.0, 0.5, -3.0, 2.0)
    for i in range(12):
        rows.append(dict(index=i % 4, flag=(0, 3, 9)[i % 3], tex=(i % 5) - 1,
                         color=bits(10.0 + i), limit=bits(64.0),
                         phase=bits((5.0, 0.5, 60.0, 30.0, 12.0, 0.0, 2.0, 1.0,
                                     63.5, 32.0, 2.0, 40.0)[i]),
                         step=bits(steps[i]), height=bits(4.0 + i)))
    return rows


def build(mem):
    mem.store(PD.GP + 0x3098, CTX)
    mem.store(CTX + 0x550, BUFFER)
    mem.store(CTX + 0x5c0, 0xfffe, 2)  # lh sign extension
    mem.put(BUFFER - 0x40, bytes([POISON]) * 0x2000)
    mem.put(0x70000b40, bytes([POISON]) * 0x100)
    for i, r in enumerate(records()):
        a = RECORDS + 0x20 * i
        mem.put(a, bytes([r['index'], 0, r['flag'], r['tex'] & 0xff]))
        for off, key in ((4, 'color'), (8, 'limit'), (0xc, 'phase'), (0x10, 'step'), (0x14, 'height')):
            mem.store(a + off, r[key])
        mem.store(a + 0x18, 0x11110000 + i)
    mem.store(RECORDS + 12 * 0x20, 0xff, 1)


def effect(mem, pc, cursor_effects):
    """Shared stub effect: 0014a0a0 appends an 8-byte marker packet."""
    if cursor_effects and pc == 0x14a0a0:
        ctx = mem.load(PD.GP + 0x3098)
        at = mem.load(ctx + 0x550)
        mem.store(at, 0x14a0a000 | (at >> 4 & 0xff))
        mem.store(at + 4, 0x5a5a5a5a)
        mem.store(ctx + 0x550, at + 0x10)
    return 0x1234


def original(cursor_effects):
    mem = Memory()
    build(mem)
    PD.install_service(mem, PD.packet_words())
    calls = []

    def hook(pc):
        def run(cpu):
            calls.append((pc, PD.contract_values(cpu, pc)))
            value = effect(mem, pc, cursor_effects)
            for n in list(range(1, 16)) + [24, 25]:
                cpu.reg[n] = REG_POISON
            for n in range(20):
                cpu.fpr[n] = FP_POISON
            return value
        return run
    PD.run_original(mem, SP, hooks={pc: hook(pc) for pc in PD.CALLEES})
    return mem, calls


class StubDriver(Driver):
    def __init__(self, cursor_effects):
        super().__init__()
        build(self.mem)
        PD.install_service(self.mem, PD.packet_words())  # identical image; C never reads it
        self.cursor_effects = cursor_effects
        self.calls = []; self.faults = []; self.events = []; self.pending = None
    def external(self, pc, a=0, b=0, c=0, d=0):
        if pc == 0xfffffff0: self.faults.append(a); return 0
        if pc == 0xfffffff6: self.events.append((a, b)); return 0
        if pc == 0xfffffff5:
            if self.pending is not None: raise ValueError('unconsumed t-register seam')
            self.pending = {8: a, 9: b, 10: c}; return 0
        if pc not in PD.CONTRACT: raise ValueError(f'unexpected call {pc:#x}')
        extra, self.pending = self.pending, None
        self.calls.append((pc, PD.c_contract(pc, a, b, c, d, extra)))
        return effect(self.mem, pc, self.cursor_effects)


def c_run(lib, cursor_effects):
    d = StubDriver(cursor_effects)
    f = lib.hg_pre_dispatch_service
    f.argtypes = [C.POINTER(ResourceEnv)]; f.restype = None
    f(C.byref(d.env))
    if d.errors: raise AssertionError(d.errors)
    return d


def differences(a, b):
    skip = set(PD.frame_excluded(SP))
    out = []
    for page in sorted(set(a.pages) | set(b.pages)):
        base = page << 12
        x, y = a.get(base, 4096), b.get(base, 4096)
        if x != y:
            out.extend(base + i for i in range(4096) if x[i] != y[i] and base + i not in skip)
    return out




class PreDispatchDifferentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.source = (ROOT / 'src/resource_loader.c').read_text()
        cls.lib = library(output=Path(cls.tmp.name) / 'baseline.so')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def mutant(self, name):
        path = Path(self.tmp.name) / f'{name}.c'
        path.write_text(PD.mutant_source(self.source, name))
        return library(source=path, output=Path(self.tmp.name) / f'{name}.so')

    def test_c_matches_original_instructions_on_poisoned_buffers(self):
        for cursor_effects in (False, True):
            with self.subTest(cursor_effects=cursor_effects):
                mem, calls = original(cursor_effects)
                d = c_run(self.lib, cursor_effects)
                self.assertEqual(d.faults, [])
                self.assertEqual(d.events, [(PD.SERVICE, 1), (PD.SERVICE, 0)])
                self.assertEqual(differences(mem, d.mem), [])
                self.assertEqual(d.calls, calls)
                self.assertEqual(len(calls), 3 + 12 * 2 + 2)
                # per record: quad, 0x30 packet, quad; tail: 0x30, quad, 0x30, 0x60
                end = BUFFER + 12 * (0x40 + 0x10 + 0x40) + 0x10 + 0x40 + 0x10 + 0x10
                end += 13 * 0x10 if cursor_effects else 0  # 13 stub 0014a0a0 markers
                self.assertEqual(d.mem.load(CTX + 0x550), end)

    def test_original_quad_packet_widths_and_untouched_header_bytes(self):
        mem, _ = original(False)
        first = BUFFER  # the 00155350 stub leaves the cursor unchanged
        self.assertEqual(mem.get(first + 0x10, 12), bytes(12))  # sq $zero,0x10
        self.assertEqual(mem.load(first + 0x1c), 0x50000002)
        self.assertEqual(mem.load(first + 0x20, 8), 0x1000000000008001)
        self.assertEqual(mem.load(first + 0x28, 8), 14)
        self.assertEqual(mem.load(first + 0x38, 8), 0x40)
        self.assertEqual(mem.get(first + 8, 8), bytes([POISON]) * 8)     # header +8..+f
        self.assertEqual(mem.get(first + 0x42, 1), bytes([POISON]))      # next header byte 2

    def test_152e90_and_14f060_receive_original_caller_registers(self):
        mem, calls = original(False)
        d = c_run(self.lib, False)
        final = [row for pc, row in calls if pc == 0x152e90]
        self.assertEqual(len(final), 1)
        # a2 is the final 0x30 packet's own address; a3 the context.
        self.assertEqual(final[0]['a3'], CTX)
        self.assertEqual(mem.get(final[0]['a2'] + 3, 1), b'\x30')
        self.assertEqual(mem.load(final[0]['a2'] + 4), 0x381880)
        t_rows = [row for pc, row in calls if pc == 0x14f060]
        self.assertEqual([(r['t0'], r['t1'], r['t2']) for r in t_rows],
                         [(0x70000ba0, CTX + 0x5b0, RECORDS + 0x20 * i + 0x18) for i in range(12)])
        self.assertEqual([row for pc, row in d.calls if pc == 0x14f060], t_rows)

    def test_pre_fix_store_widths_are_rejected_by_325_bytes(self):
        lib = self.mutant('store-width-pre-fix')
        mem, calls = original(False)
        d = c_run(lib, False)
        diff = differences(mem, d.mem)
        self.assertEqual(len(diff), 325)  # 25 quad packets x (12 at +10..+1b, 1 at +42)
        self.assertEqual(d.calls, calls)

    def test_missing_sq_zero_store_is_rejected(self):
        lib = self.mutant('missing-sq-zero')
        mem, _ = original(True)
        self.assertEqual(len(differences(mem, c_run(lib, True).mem)), 25 * 12)

    def test_negative_wrap_branch_defect_is_rejected(self):
        lib = self.mutant('negative-wrap-skipped')
        mem, _ = original(False)
        diff = differences(mem, c_run(lib, False).mem)
        self.assertTrue(diff)
        self.assertTrue(all(RECORDS <= a < RECORDS + 12 * 0x20 for a in diff), [hex(a) for a in diff])

    def test_phase_arithmetic_and_wrap_defects_are_rejected(self):
        mem, _ = original(False)
        for name in ('phase-step-sign', 'wrap-test-inverted'):
            with self.subTest(name=name):
                self.assertTrue(differences(mem, c_run(self.mutant(name), False).mem))

    def test_every_shared_mutant_targets_one_site(self):
        self.assertEqual(sorted(PD.mutants(self.source)),
                         sorted(['store-width-pre-fix', 'missing-sq-zero', 'negative-wrap-skipped',
                                 'phase-step-sign', 'wrap-test-inverted', 't1-omitted',
                                 '152e90-pre-fix-arguments']))

    def test_omitted_t1_register_is_rejected(self):
        lib = self.mutant('t1-omitted')
        _, calls = original(False)
        self.assertNotEqual(c_run(lib, False).calls, calls)

    def test_pre_fix_152e90_arguments_are_rejected(self):
        lib = self.mutant('152e90-pre-fix-arguments')
        _, calls = original(False)
        self.assertNotEqual(c_run(lib, False).calls, calls)


if __name__ == '__main__':
    unittest.main()


class WindowComparisonTests(unittest.TestCase):
    """Synthetic checks of predispatch_differential.compare_window."""
    def image(self, cursor):
        from verify_player_connected_runtime import CapturedMemory
        m = CapturedMemory(bytes(0x2000000), bytes(0x4000))
        m.store(PD.GP + 0x3098, CTX)
        for k in PD.CURSORS:
            m.store(CTX + k, cursor + k * 0x100)
        return m

    def native(self):
        before = self.image(0x600000)
        after = self.image(0x600000)
        writes = set()
        def put(a, v):
            after.raw[a] = v; writes.add(a)
        for k in PD.CURSORS:
            start = 0x600000 + k * 0x100
            for o in (0, 1, 3, 0x10):
                put(start + o, 0x40 + o)
            after.store(CTX + k, start + 0x20); writes.update(range(CTX + k, CTX + k + 4))
        put(0x3eb040, 7); put(0x1fffb40, 9)          # absolute output; stack temporary
        after.scratch[0xb90] = 5; writes.add(0x70000b90)
        return before, after, writes

    def reconstruction(self, base=0x700000, skip=None, value=None, delta=0x20):
        before = self.image(base)
        after = self.image(base)
        writes = set()
        for k in PD.CURSORS:
            start = base + k * 0x100
            for o in (0, 1, 3, 0x10):
                if (k, o) == skip: continue
                after.raw[start + o] = 0x40 + o; writes.add(start + o)
            after.store(CTX + k, start + delta)
        after.raw[0x3eb040] = 7 if value is None else value; writes.add(0x3eb040)
        after.raw[0x1ff0700] = 1; writes.add(0x1ff0700)
        after.scratch[0xb90] = 5; writes.add(0x70000b90)
        return before, after, writes

    def check(self, **kw):
        nb, na, nw = self.native()
        rb, ra, rw = self.reconstruction(**kw)
        return PD.compare_window(rb, ra, rw, nb, na, nw)

    def test_relocated_streams_and_outputs_match(self):
        result = self.check()
        self.assertEqual(result['result'], 'passed', result)
        self.assertEqual([r['written_offsets'] for r in result['streams']], [4] * 4)

    def test_unwritten_stream_byte_is_rejected_even_when_value_equal(self):
        nb, na, nw = self.native()
        rb, ra, rw = self.reconstruction(skip=(0x550, 0x10))
        ra.raw[0x700000 + 0x550 * 0x100 + 0x10] = 0x50  # stale byte happens to equal
        result = PD.compare_window(rb, ra, rw, nb, na, nw)
        self.assertEqual(result['result'], 'failed')
        self.assertEqual(result['streams'][3]['missing_offsets'], 1)
        self.assertEqual(result['streams'][3]['value_differences'], 0)

    def test_absolute_output_and_cursor_delta_defects_are_rejected(self):
        self.assertEqual(self.check(value=8)['absolute_value_differences'], 1)
        result = self.check(delta=0x30)
        self.assertEqual(result['result'], 'failed')
        self.assertFalse(any(r['delta_matches'] for r in result['streams']))

    def test_scratch_write_outside_oracle_is_rejected(self):
        nb, na, nw = self.native()
        rb, ra, rw = self.reconstruction()
        na.scratch[0xba0] = 1
        result = PD.compare_window(rb, ra, rw, nb, na, nw)
        self.assertEqual(result['result'], 'failed')
        self.assertEqual(result['first_unmodeled_native_scratch'], ['0x70000ba0'])

    def test_window_differences_keep_original_writer_and_absolute_addresses(self):
        nb, na, nw = self.native()
        rb, ra, rw = self.reconstruction(skip=(0x550, 0x10), value=8)
        result = PD.compare_window(rb, ra, rw, nb, na, nw)
        native_packet = nb.peek_word(CTX + 0x550) + 0x10
        reconstructed_packet = rb.peek_word(CTX + 0x550) + 0x10
        writers = {0x3eb040: (11, 0x1815b4), native_packet: (7, 0x1992a0)}
        rows = PD.window_difference_writers(result, ra, na, writers)
        self.assertEqual([(r['original_writer'], r['actual_address'], r['expected_address'])
                          for r in rows],
                         [('0x1992a0', hex(reconstructed_packet), hex(native_packet)),
                          ('0x1815b4', '0x3eb040', '0x3eb040')])
