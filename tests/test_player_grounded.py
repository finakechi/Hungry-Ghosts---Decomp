"""Synthetic M12 grounded/floor contracts. No retail captures, original bytes or expected output."""
import ctypes as C
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from resource_harness import Driver, ResourceEnv, ROOT, library
sys.path.insert(0, str(ROOT / 'tools'))
import verify_player_grounded_differential as GD
import verify_player_grounded_runtime as GR

P = 0x3f92d0
def bits(x): return struct.unpack('<I', struct.pack('<f', x))[0]
def fl(b): return struct.unpack('<f', struct.pack('<I', b & 0xffffffff))[0]


class FloorDriver(Driver):
    """Fake 00145630: a horizontal floor at height `floor` with chosen result/record fields."""
    def __init__(self, floor=38.0, flags=0x101, kind=2, sub=0, material=0, reach=(-2.0, 4.0), shift_xz=(0.0, 0.0)):
        super().__init__(); self.floor = floor; self.flags = flags; self.kind = kind; self.sub = sub
        self.material = material; self.reach = reach; self.shift_xz = shift_xz
        self.frames = []; self.faults = []; self.queries = []; self.returns = []
    def external(self, pc, a=0, b=0, c=0, d=0):
        if pc == 0xfffffff0: self.faults.append(a); return 0
        if pc == 0xfffffff1: self.frames.append((a, b)); return 0
        if pc == 0xfffffff2: self.returns.append((a, b)); return 0
        if pc == 0x145630:
            origin = [fl(self.mem.load(c + 4 * i)) for i in range(4)]
            self.queries.append((fl(a), b, origin, d, sum(x for x, _ in self.frames if _ == 1) - sum(x for x, _ in self.frames if _ == 0)))
            low, high = origin[1], origin[1] + fl(a)
            if not low <= self.floor <= high: return 0
            for i, v in enumerate((origin[0] + self.shift_xz[0], self.floor, origin[2] + self.shift_xz[1], origin[3])):
                self.mem.store(0x3f39a0 + 4 * i, bits(v))
            self.mem.store(0x3f39c6, self.kind, 2); self.mem.store(0x3f39c8, self.sub, 2); self.mem.store(0x3f39cc, self.material, 2)
            return self.flags
        raise AssertionError(hex(pc))


class GroundedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build = tempfile.TemporaryDirectory()
        cls.addClassCleanup(build.cleanup)
        cls.lib = library(output=Path(build.name) / 'player_grounded.so')
        f = cls.lib.hg_player_grounded_fixed; f.argtypes = [C.POINTER(ResourceEnv), C.c_uint32]; f.restype = None

    def run_case(self, d, y=38.0, speed=0.0, f4ad=0, state0=1, f47c=0x8004, f483=0xff, d88c=0):
        m = d.mem
        for off, v in ((0x30, 30.5), (0x34, y), (0x38, 265.0), (0x3c, 1.0), (0x430, speed)): m.store(P + off, bits(v))
        m.store(P + 0x4ad, f4ad, 1); m.store(P, state0, 1); m.store(P + 0x47c, f47c, 2); m.store(P + 0x483, f483, 1)
        m.store(P + 0x4f4, 0x12345678); m.store(0x3f9ff2, 0xbeef, 2); m.store(0x46d88c, d88c)
        self.lib.hg_player_grounded_fixed(C.byref(d.env), P)
        self.assertEqual(d.frames, [(0x20, 1), (0x30, 1), (0x30, 0), (0x20, 0)])
        return m

    def test_grounded_frame_applies_gravity_then_snaps_to_floor(self):
        d = FloorDriver(floor=37.995); m = self.run_case(d, y=38.0)
        self.assertFalse(d.errors); self.assertFalse(d.faults)
        rise, player, origin, mode, depth = d.queries[0]
        self.assertEqual((rise, player, mode, depth), (6.0, P, 0x2c0, 0x50))     # nested guest frames
        self.assertAlmostEqual(origin[1], 38.0 - 0.08 - 2.0, places=5)           # probe from y' - 2
        self.assertEqual(fl(m.load(P + 0x34)), fl(bits(37.995)))                 # snapped to the floor
        self.assertEqual(m.load(P + 0x430), 0)                                     # speed cleared
        self.assertEqual(m.load(P + 0x2c3, 1), 1)
        self.assertEqual(m.load(P + 0x47c, 2), 0x8000 | (1 << 2))                 # keep bit 15, set type bit
        self.assertEqual(m.load(P + 0x47e, 2), 0); self.assertEqual(m.load(P + 0x483, 1), 0xfd)
        self.assertEqual(m.load(0x3f9ff2, 2), 0); self.assertEqual(m.load(P + 0x4f4), 0x12345678)

    def test_floor_snap_copies_the_whole_hit_quad(self):
        d = FloorDriver(floor=38.5, shift_xz=(0.25, -0.5)); m = self.run_case(d, y=38.0)
        self.assertEqual([fl(m.load(P + o)) for o in (0x30, 0x34, 0x38)], [30.75, 38.5, 264.5])

    def test_alternate_gravity_and_clamp(self):
        d = FloorDriver(floor=10.0); self.run_case(d, y=38.0, speed=-1.99, f4ad=0)
        self.assertEqual(d.faults, [0x50])                                          # airborne fails closed
        self.assertEqual(fl(d.mem.load(P + 0x430)), -2.0)                           # clamped, not cleared
        self.assertAlmostEqual(fl(d.mem.load(P + 0x34)), 36.0, places=5)
        d = FloorDriver(floor=37.0); m = self.run_case(d, y=38.0, f4ad=1)
        self.assertAlmostEqual(d.queries[0][2][1], 38.0 - 0.03 - 2.0, places=5)
        self.assertEqual(m.load(P + 0x430), 0)

    def test_material_fields_and_state_restore(self):
        d = FloorDriver(material=0x0701); m = self.run_case(d, state0=2, d88c=1)
        self.assertEqual((m.load(P + 0x480, 1), m.load(P + 0x481, 1)), (1, 7))
        self.assertEqual(m.load(P + 0x483, 1), 0xff)                                # cleared then set again
        self.assertEqual(m.load(P, 1), 1)
        d = FloorDriver(sub=4, material=0x0701); m = self.run_case(d)
        self.assertEqual((m.load(P + 0x47e, 2), m.load(P + 0x480, 1), m.load(P + 0x481, 1)), (4, 0, 0))

    def test_non_floor_hit_sets_slope_state(self):
        d = FloorDriver(flags=0x1); m = self.run_case(d)
        self.assertFalse(d.faults); self.assertEqual(m.load(P + 0x2c3, 1), 2); self.assertEqual(m.load(P + 0x4f4), 0)
        self.assertEqual(m.load(P + 0x47c, 2), 0x8004); self.assertEqual(m.load(P + 0x430), 0)

    def test_unsupported_floor_paths_fail_closed(self):
        for kwargs, code in ((dict(kind=7), 0x51), (dict(kind=6), 0x51), (dict(flags=0x301), 0x52),
                             (dict(kind=5), 0x53), (dict(sub=3), 0x54)):
            d = FloorDriver(**kwargs); m = self.run_case(d)
            self.assertEqual(d.faults, [code], kwargs)
            self.assertNotEqual(m.load(P + 0x430), 0, kwargs)                       # no grounded result
        d = FloorDriver(sub=3); d.mem.store(0x3fa4d7, 1, 1); m = self.run_case(d)
        self.assertFalse(d.faults); self.assertEqual(m.load(P + 0x47e, 2), 3)      # spawn suppressed: supported

    def test_floor_return_is_reported_from_the_c_callee(self):
        d = FloorDriver(); self.run_case(d)
        self.assertEqual(d.returns, [(0x15e420, 1)])
        d = FloorDriver(floor=10.0); self.run_case(d)                               # no floor: fails closed
        self.assertEqual((d.faults, d.returns), ([0x50], [(0x15e420, 0)]))


def native(frame=1212, query='00000101', floor='00000001', f12='3f000000'):
    reg = lambda v: dict(value=v, method='synthetic')
    return dict(counter=frame, captures={'floor-query-return': dict(registers=dict(v0_low=reg(query))),
                                         'floor-return': dict(registers=dict(v0_low=reg(floor))),
                                         'constraint-entry': dict(registers=dict(f12=reg(f12)))})


class Measured:
    def __init__(self, floor_returns, floor_query=0x101, direction=0x3f000000):
        self.floor_returns = floor_returns; self.floor_query = floor_query; self.direction = direction


NO_DIFFERENCE = dict(different_bytes=0, scratchpad=dict(different_bytes=0))


class RuntimeRegisterTests(unittest.TestCase):
    """The floor-return v0 check compares the value C hg_player_floor actually returned."""
    def floor_row(self, driver):
        return next(r for r in GR.register_checks(driver, native(), None) if r['register'] == 'floor-return-v0')

    def test_floor_returning_two_is_rejected_although_memory_matches(self):
        source = (ROOT / 'src/resource_loader.c').read_text()
        old = '    call(e,HG_GUEST_FRAME,0x30,0,0,0);\n    return 1;\n}'
        self.assertEqual(source.count(old), 1)
        def run(lib):
            d = FloorDriver(floor=37.995); m = d.mem
            for off, v in ((0x30, 30.5), (0x34, 38.0), (0x38, 265.0), (0x3c, 1.0), (0x430, 0.0)): m.store(P + off, bits(v))
            m.store(P + 0x4ad, 0, 1); m.store(P, 1, 1); m.store(P + 0x47c, 0x8004, 2); m.store(P + 0x483, 0xff, 1)
            f = lib.hg_player_grounded_fixed; f.argtypes = [C.POINTER(ResourceEnv), C.c_uint32]; f.restype = None
            f(C.byref(d.env), P)
            self.assertFalse(d.errors); self.assertFalse(d.faults)
            return d
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'floor-returns-two.c'; path.write_text(source.replace(old, old.replace('return 1', 'return 2')))
            good = run(library(output=Path(tmp) / 'baseline.so'))
            d = run(library(source=path, output=Path(tmp) / 'floor-returns-two.so'))
        self.assertEqual({k: bytes(v) for k, v in good.mem.pages.items()}, {k: bytes(v) for k, v in d.mem.pages.items()})
        self.assertEqual((good.returns, d.returns), ([(0x15e420, 1)], [(0x15e420, 2)]))
        good_rows = GR.register_checks(Measured([1]), native(), None)
        bad_rows = GR.register_checks(Measured([v for _, v in d.returns]), native(), None)
        self.assertTrue(all(r['result'] == 'passed' for r in good_rows))
        self.assertEqual([(r['register'], r['calculated'], r['result']) for r in bad_rows if r['result'] != 'passed'],
                         [('floor-return-v0', '00000002', 'failed')])
        self.assertEqual(GR.control_verdict(None, NO_DIFFERENCE, good_rows), ('ESCAPED', [], []))
        self.assertEqual(GR.control_verdict(None, NO_DIFFERENCE, bad_rows), ('rejected', ['register'], ['floor-return-v0']))

    def test_missing_floor_return_is_neither_passed_nor_divergence(self):
        for returns in ([], [1, 1]):
            row = self.floor_row(Measured(returns))
            self.assertEqual((row['calculated'], row['result']), (None, 'missing'))
        rows = GR.register_checks(Measured([]), native(), None)
        self.assertEqual(GR.control_verdict(None, NO_DIFFERENCE, rows), ('error', [], []))

    def test_memory_divergence_is_reported_with_its_basis(self):
        rows = GR.register_checks(Measured([1]), native(), None)
        first = dict(stage='floor-return', different_bytes=1, scratchpad=0)
        self.assertEqual(GR.control_verdict(first, NO_DIFFERENCE, rows), ('rejected', ['memory'], []))


def domain(code): return repr(ValueError(f'C path outside reconstructed domain (code {code:#x})'))
def comparison(different=0, result='passed', accesses=()):
    return dict(result=result, different_bytes=different, differences=[], external_dma=dict(modeled_accesses=list(accesses)))


class DifferentialClassificationTests(unittest.TestCase):
    def test_refusal_code_must_name_the_exact_original_branch(self):
        for code, floor_type, branch in ((0x50, 0, 'no-floor'), (0x51, 7, 'type-7'), (0x51, 6, 'type-6'),
                                         (0x52, 2, 'special-0x1e00'), (0x53, 5, 'type-5'), (0x54, 2, 'spawn')):
            row = GD.classify([domain(code)], True, floor_type, [branch])
            self.assertEqual((row['result'], row['expected_branch']), ('unsupported', branch), hex(code))

    def test_wrong_refusal_code_fails_and_is_not_divergence(self):
        # Regression (M12 review): 0x50 changed to 0x54 was accepted because any original
        # unsupported branch made the case 'unsupported'.
        row = GD.classify([domain(0x54)], True, 0, ['no-floor'])
        self.assertEqual((row['result'], row['failure'], row['expected_branch']), ('failed', 'refusal-mismatch', 'spawn'))
        for args in (([domain(0x51)], True, 6, ['type-7']),      # hazard code, wrong hazard
                     ([domain(0x50)], True, 0, []),              # refusal where the original took no unsupported branch
                     ([domain(0x50), domain(0x52)], True, 0, ['no-floor'])):   # more than one refusal
            self.assertEqual(GD.classify(*args)['failure'], 'refusal-mismatch', args)
        counts = GD.tally([dict(row, index=0)])
        self.assertEqual(counts['failed'], 1)
        self.assertEqual(GD.control_verdict(counts, 'divergence'), 'ESCAPED')      # no completed comparison
        self.assertEqual(GD.control_verdict(counts, 'refusal-mismatch'), 'rejected')

    def test_only_completed_comparisons_with_differences_are_divergence(self):
        cases = [(GD.classify([], True, 2, [], comparison(3, 'failed')), 'failed', 'measured-divergence'),
                 (GD.classify([], True, 2, [], comparison(0), scratch_bytes=2), 'failed', 'measured-divergence'),
                 (GD.classify([], True, 2, ['spawn'], comparison(0)), 'failed', 'unsupported-branch-not-refused'),
                 (GD.classify([], True, 2, [], comparison(0, 'failed', ['0x95e400'])), 'failed', 'external-dma-access'),
                 (GD.classify([], True, 2, [], comparison(3, 'failed', ['0x95e400'])), 'failed', 'external-dma-access'),
                 (GD.classify([], True, 2, [], comparison(0)), 'passed', None),
                 (GD.classify([], True, 2, []), 'error', None),                                   # missing comparison
                 (GD.classify(["ValueError('boom')"], True, 2, [], comparison(0)), 'error', None),  # harness exception
                 (GD.classify([domain(0x50)], False, 0, ['no-floor']), 'error', None)]            # unbalanced frame
        for row, result, failure in cases: self.assertEqual((row['result'], row.get('failure')), (result, failure), row)
        not_measured = [row for row, _, failure in cases if failure != 'measured-divergence']
        self.assertEqual(GD.control_verdict(GD.tally([r for r in not_measured if r['result'] != 'error']), 'divergence'), 'ESCAPED')
        self.assertEqual(GD.control_verdict(GD.tally([cases[0][0]]), 'divergence'), 'rejected')
        self.assertEqual(GD.control_verdict(GD.tally([cases[0][0], next(row for row, result, _ in cases if result == 'error')]), 'divergence'), 'error')


if __name__ == '__main__':
    unittest.main()
