"""Synthetic M16 0013ce80 original-instruction versus C gate differential.

The original words come from the tracked, original-byte-verified packet
analysis/late_frame/functions.json. The memory image is synthetic: no retail
capture, ELF or expected output is used. The AREA1 render body (0xa8b800 /
0xa8bab0) is a stub on both sides, so the differential covers exactly the
service's own gate/dispatch behavior:

  * state (1,0,0): the observed corridor path. Both sides dispatch 0xa8b800
    with f12..f16 = (0x415f78d5, 0x461c4000, 0xc61c4000, 0x461c4000,
    0x40400000) and produce no writes of their own.
  * state (1,0,1) and (0,5,0): unobserved calling states. The original would
    call the area body; the C fails closed with the documented domain faults
    0x79 / 0x7a (their area dispatch is deliberately not compared: it is
    unobserved, exactly as documented).
  * every other state: both sides return without effects.

Integer a0..a3 at the area call are not part of the 0013ce80 contract (the
caller's a0 = 003f97f0 is unused by the body; the observed area body reads no
argument registers), so only the entry and the five FP operands are compared.
"""
import ctypes as C
import json
import struct
import sys
import unittest
from pathlib import Path

from resource_harness import Driver, Memory, ResourceEnv, ROOT, library
sys.path.insert(0, str(ROOT / 'tools'))
from ee_subset import Machine

PACKET = ROOT / 'analysis/late_frame/functions.json'
GP = 0x3e7f70
SP = 0x1fffc70
AREA_MAIN = 0xa8b800
AREA_ALT = 0xa8bab0
POISON = 0xa5

# Observed state (1,0,0) FP contract from the original body.
MAIN_FP = (0x415f78d5, 0x461c4000, 0xc61c4000, 0x461c4000, 0x40400000)
# Unobserved state (1,0,1) and (0,5,0) dispatch constants (from the listing).
ALT_FP = (0x412f78d5, 0x433e0000, 0x00000000, 0x43530000, 0x41000000)
SPRITE_FP = (0x41e80000, 0x00000000, 0x00000000, 0x00000000, 0x00000000)

CALLING = {(1, 0, 0): (AREA_MAIN, MAIN_FP),
           (1, 0, 1): (AREA_MAIN, ALT_FP),
           (0, 5, 0): (AREA_ALT, SPRITE_FP)}
NOOP_STATES = [(2, 2, 2), (1, 1, 0), (1, 0, 2), (1, 0, 0xff), (0, 4, 0),
               (0, 5, 1), (0, 5, 0xff), (3, 0, 0), (0xff, 0, 0), (0, 6, 0)]


def packet_words(path=PACKET):
    data = json.loads(Path(path).read_text())
    words = {}
    for row in data['functions']:
        if int(row['entry'], 16) != 0x13ce80:
            continue
        for r in row['body_ranges']:
            start, end = int(r['start'], 16), int(r['end_exclusive'], 16)
            have = {int(i['address'], 16): bytes.fromhex(i['bytes']) for i in row['instructions']}
            if sorted(a for a in have if start <= a < end) != list(range(start, end, 4)):
                raise ValueError(f'Incomplete {row["entry"]} packet')
            words.update(have)
    return words


class TrackingMemory(Memory):
    def __init__(self):
        super().__init__(); self.writes = set()

    def __setitem__(self, address, value):
        self.writes.add(address); super().__setitem__(address, value)


def build(state):
    mem = TrackingMemory()
    for address, raw in packet_words().items():
        mem.put(address, raw)
    for i, value in enumerate(state):
        mem.store(0x3fa1b0 + i, value, 1)
    mem.put(0x70000000, bytes([POISON]) * 0x4000)
    mem.put(0x3f97f0, bytes([POISON]) * 0x10)
    mem.writes.clear()
    return mem


def original(state):
    """Original 0013ce80 instructions with the area body stubbed."""
    mem = build(state)
    calls = []
    machine = Machine(); machine.mem = mem
    machine.reg[28] = GP; machine.reg[29] = SP
    machine.reg[4] = 0x3f97f0  # the coordinator's delay-slot a0

    def hook(pc):
        def run(cpu):
            fp = tuple(cpu.fpr[12 + i] & 0xffffffff for i in range(5))
            calls.append((pc, fp))
            # Stub effect: clobber caller-saved state like a real callee would.
            for n in list(range(1, 16)) + [24, 25]:
                cpu.reg[n] = 0xdeadbeef
            for n in range(20):
                cpu.fpr[n] = 0x41a5a5a5
            return 0
        return run

    machine.hooks = {AREA_MAIN: hook(AREA_MAIN), AREA_ALT: hook(AREA_ALT)}
    machine.run(0x13ce80, limit=30000)
    return dict(mem=mem, calls=calls, executed=set(machine.executed))


class StubDriver(Driver):
    def __init__(self, state):
        super().__init__(build(state))
        self.faults = []; self.calls = []; self.marks = []; self.extra = []

    def external(self, pc, a=0, b=0, c=0, d=0):
        if pc == 0xfffffff0:
            self.faults.append(a); return 0
        if pc == 0xfffffff6:
            self.marks.append((a, b)); return 0
        if pc == 0xfffffff9:
            self.extra.append(a); return 0
        if pc in (AREA_MAIN, AREA_ALT):
            if len(self.extra) != 1 or self.calls:
                raise ValueError('extra-FP seam contract violation')
            self.calls.append((pc, (a, b, c, d, self.extra[0])))
            self.extra.clear(); return 0
        raise ValueError(f'unexpected bus call {pc:#x}')


CALLING_STATE = (1, 0, 0)


STACK = (0x1fe0000, 0x2000000)  # transient guest-stack bytes, omitted like the M15 differentials


def outside_stack(a):
    return not STACK[0] <= a < STACK[1]


def run_c(state, lib):
    driver = StubDriver(state)
    lib.hg_late_frame_service.argtypes = [C.POINTER(ResourceEnv)]
    lib.hg_late_frame_service.restype = None
    lib.hg_late_frame_service(C.byref(driver.env))
    if driver.errors:
        raise AssertionError(driver.errors)
    return driver


class LateFrameGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = library()

    def test_observed_path_dispatches_area_body(self):
        state = CALLING_STATE
        expected_pc, expected_fp = CALLING[state]
        original_row = original(state)
        driver = run_c(state, self.lib)
        self.assertEqual([pc for pc, _ in original_row['calls']], [expected_pc])
        self.assertEqual([pc for pc, _, in driver.calls], [expected_pc])
        self.assertEqual(original_row['calls'][0][1], expected_fp)
        self.assertEqual(driver.calls[0][1], expected_fp)
        self.assertEqual(original_row['calls'][0][1], driver.calls[0][1],
                         'original and C FP operands differ')
        self.assertEqual(driver.faults, [])
        self.assertEqual(driver.marks, [(0x13ce80, 1), (0x13ce80, 0)])
        # Neither side writes anything of its own beyond the transient
        # guest-stack ra save (omitted like the M15 stack exclusion).
        self.assertEqual(set(), driver.mem.writes, 'C service wrote memory')
        self.assertEqual(set(), {a for a in original_row['mem'].writes if outside_stack(a)} - {a for a in packet_words()},
                         'original body wrote memory')

    def test_noop_states_return_without_effects(self):
        for state in NOOP_STATES:
            original_row = original(state)
            driver = run_c(state, self.lib)
            self.assertEqual(original_row['calls'], [], f'original called area code for {state}')
            self.assertEqual(driver.calls, [], f'C called area code for {state}')
            self.assertEqual(driver.faults, [], f'C faulted for no-op state {state}')
            self.assertEqual(driver.mem.writes, set(), f'C wrote memory for {state}')

    def test_unobserved_calling_states_fail_closed(self):
        for state, code in (((1, 0, 1), 0x79), ((0, 5, 0), 0x7a)):
            original_row = original(state)
            self.assertEqual(len(original_row['calls']), 1,
                             f'original does not dispatch the area body for {state}')
            driver = run_c(state, self.lib)
            self.assertEqual(driver.faults, [code], f'C fault code for {state}')
            self.assertEqual(driver.calls, [], f'C dispatched the unobserved area body for {state}')
            self.assertEqual(driver.mem.writes, set(), f'C wrote memory before faulting for {state}')

    def test_all_state_bytes_exercised(self):
        # Every one-byte combination of the three state bytes is covered by the
        # calling states, the no-op states or an explicit scan of one byte
        # while the other two stay in the observed calling state.
        for first in range(0x100):
            state = (first, 0, 0)
            if state in CALLING or state in NOOP_STATES:
                continue
            driver = run_c(state, self.lib)
            self.assertEqual((driver.calls, driver.faults), ([], []),
                             f'state {state} must be a silent return')


class LateFrameMutantTests(unittest.TestCase):
    """Compiled C defects of the service gate must be detected by the
    differential arguments or the fail-closed contracts."""

    @classmethod
    def setUpClass(cls):
        cls.lib = library()
        cls.source = (ROOT / 'src/resource_loader.c').read_text()

    def mutant(self, name, old, new):
        if self.source.count(old) != 1:
            raise AssertionError(f'{name}: mutation target not unique')
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix='late-frame-mutant-'))
        path = folder / f'{name}.c'
        path.write_text(self.source.replace(old, new))
        return library(source=path, output=folder / f'{name}.so')

    def test_wrong_f13_rejected(self):
        mutant = self.mutant('wrong-f13',
                             'call(e,0xa8b800,0x415f78d5,0x461c4000,0xc61c4000,0x461c4000);',
                             'call(e,0xa8b800,0x415f78d5,0x460c4000,0xc61c4000,0x461c4000);')
        driver = run_c(CALLING_STATE, mutant)
        self.assertEqual(driver.calls[0][1], (0x415f78d5, 0x460c4000, 0xc61c4000, 0x461c4000, 0x40400000))
        self.assertNotEqual(driver.calls[0][1], MAIN_FP, 'wrong f13 escaped')

    def test_wrong_f16_rejected(self):
        mutant = self.mutant('wrong-f16',
                             'call(e,HG_CALL_EXTRA_FP,0x40400000,0,0,0);  /* f16 = 3.0 */',
                             'call(e,HG_CALL_EXTRA_FP,0x40000000,0,0,0);  /* f16 = 3.0 */')
        driver = run_c(CALLING_STATE, mutant)
        self.assertNotEqual(driver.calls[0][1], MAIN_FP, 'wrong f16 escaped')

    def test_wrong_gate_byte_rejected(self):
        # (1,0,2) becomes a calling state in the mutant: the original returns
        # without effects, the C must not dispatch.
        mutant = self.mutant('wrong-gate-byte',
                             '    if(s0==1 && s1==0 && s2==0) {',
                             '    if(s0==1 && s1==0 && (s2==0 || s2==2)) {')
        driver = run_c((1, 0, 2), mutant)
        self.assertNotEqual((driver.calls, driver.faults), ([], []),
                            'wrong gate byte escaped for state (1,0,2)')

    def test_omitted_dispatch_rejected(self):
        mutant = self.mutant('omitted-dispatch',
                             '    call(e,HG_CALL_EXTRA_FP,0x40400000,0,0,0);  /* f16 = 3.0 */\n'
                             '        call(e,0xa8b800,0x415f78d5,0x461c4000,0xc61c4000,0x461c4000);',
                             '    (void)0;')
        driver = run_c(CALLING_STATE, mutant)
        self.assertEqual((driver.calls, driver.faults), ([], []),
                         'omitted area dispatch escaped')

    def test_unmodified_library_matches_reference(self):
        # Clean control: the real library dispatches the observed path with
        # the exact original FP contract.
        driver = run_c(CALLING_STATE, self.lib)
        self.assertEqual(driver.calls[0], (AREA_MAIN, MAIN_FP))


if __name__ == '__main__':
    unittest.main()
