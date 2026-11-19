"""Synthetic M12 grounded/floor contracts. No retail captures, original bytes or expected output."""
import ctypes as C
import struct
import unittest
from resource_harness import Driver, ResourceEnv, ROOT, library

P = 0x3f92d0
def bits(x): return struct.unpack('<I', struct.pack('<f', x))[0]
def fl(b): return struct.unpack('<f', struct.pack('<I', b & 0xffffffff))[0]


class FloorDriver(Driver):
    """Fake 00145630: a horizontal floor at height `floor` with chosen result/record fields."""
    def __init__(self, floor=38.0, flags=0x101, kind=2, sub=0, material=0, reach=(-2.0, 4.0), shift_xz=(0.0, 0.0)):
        super().__init__(); self.floor = floor; self.flags = flags; self.kind = kind; self.sub = sub
        self.material = material; self.reach = reach; self.shift_xz = shift_xz
        self.frames = []; self.faults = []; self.queries = []
    def external(self, pc, a=0, b=0, c=0, d=0):
        if pc == 0xfffffff0: self.faults.append(a); return 0
        if pc == 0xfffffff1: self.frames.append((a, b)); return 0
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
        cls.lib = library(output=ROOT / 'local/build/player_grounded.so')
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


if __name__ == '__main__':
    unittest.main()
