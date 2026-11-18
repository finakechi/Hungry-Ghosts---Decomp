"""Synthetic M11 static-constraint contracts. No retail captures or expected output bytes."""
import ctypes as C
import math
import random
import struct
import sys
import unittest
from resource_harness import Driver, ResourceEnv, ROOT, library
sys.path.insert(0, str(ROOT / 'tools'))
import ee_subset
from ee_subset import Machine, fp_value, fp_signed_chop, fp_signed_nearest, fp_chop

P = 0x3f92d0
def bits(x): return struct.unpack('<I', struct.pack('<f', x))[0]
def fl(b): return struct.unpack('<f', struct.pack('<I', b & 0xffffffff))[0]


class SceneDriver(Driver):
    """Fake scene: a vertical wall plane x = wall, or hits at chosen query indexes.

    The hit normal defaults to the reverse of the queried segment direction."""
    def __init__(self, wall=None, flags=0x100, attr=2, heights=(-1e9, 1e9), hit_indexes=None, normal=None):
        super().__init__(); self.wall = wall; self.flags = flags; self.attr = attr; self.heights = heights
        self.hit_indexes = hit_indexes; self.normal = normal; self.queries = []; self.frames = []; self.sound = []
        self.random_value = 0x7fff0000
    def vec(self, address): return [fl(self.mem.load(address + 4 * i)) for i in range(4)]
    def external(self, pc, a=0, b=0, c=0, d=0):
        if pc == 0xfffffff0: self.calls.append(('fault', a)); return 0
        if pc == 0xfffffff1: self.frames.append((a, b)); return 0
        if pc == 0x143560: return bits(math.sin(fl(a)))
        if pc == 0x143620: return bits(math.cos(fl(a)))
        if pc == 0x11ad58: self.calls.append(('random',)); return self.random_value
        if pc == 0x156ad0: self.sound.append((a, b, c, d)); return 0
        if pc == 0x1446a0:
            frm, to = self.vec(b), self.vec(c); index = len(self.queries)
            self.queries.append((a, frm, to, d))
            hit = self.wall is not None and to[0] < self.wall <= frm[0] and self.heights[0] <= frm[1] <= self.heights[1]
            if self.hit_indexes is not None: hit = index in self.hit_indexes
            if not hit: return 0
            push = self.wall - to[0] if self.wall is not None else 0.125
            for i, v in enumerate((push, 0.0, 0.0, 0.0)): self.mem.store(0x3f39b0 + 4 * i, bits(v))
            normal = self.normal
            if normal is None:
                delta = [t - f for t, f in zip(to[:3], frm[:3])]; length = math.sqrt(sum(v * v for v in delta))
                normal = tuple(-v / length for v in delta)
            for i, v in enumerate(normal + (0.0,)): self.mem.store(0x3f3a10 + 4 * i, bits(v))
            self.mem.store(0x3f39c4, self.attr, 2)
            return self.flags
        raise AssertionError(hex(pc))


class ConstraintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = library(output=ROOT / 'local/build/player_constraint.so')
        f = cls.lib.hg_player_static_constraint_fixed
        f.argtypes = [C.POINTER(ResourceEnv), C.c_uint32, C.c_uint32]; f.restype = C.c_uint32

    def run_case(self, d, x=30.0, z=265.0, heading=-math.pi / 2, f4ad=0, f49c=0, f508=0, volume=1.0):
        m = d.mem
        for off, v in ((0x30, x), (0x34, 38.0), (0x38, z), (0x3c, 1.0), (0x44, math.pi / 2)): m.store(P + off, bits(v))
        m.store(P + 0x4ad, f4ad, 1); m.store(P + 0x49c, f49c, 1); m.store(P + 0x508, f508, 1)
        m.store(0x3eaa78, bits(volume)); m.store(0x3e0856, 0x1234, 2)
        result = self.lib.hg_player_static_constraint_fixed(C.byref(d.env), bits(heading), P)
        self.assertEqual(d.frames[0], (0x70, 1)); self.assertEqual(d.frames[-1], (0x70, 0)); self.assertEqual(len(d.frames), 2)
        return result

    def test_unobstructed_leaves_proposed_position(self):
        d = SceneDriver(); r = self.run_case(d)
        self.assertFalse(d.errors); self.assertEqual(r, 0)
        self.assertEqual(fl(d.mem.load(P + 0x30)), 30.0); self.assertEqual(d.mem.load(P + 0x49a, 2), 0)
        self.assertEqual(len(d.queries), 16)
        self.assertEqual([q[3] for q in d.queries], [0x207] * 16)
        self.assertEqual(d.mem.load(P + 0x508, 1), 0)

    def test_foot_wall_push_restarts_probe_and_arms_bump(self):
        d = SceneDriver(wall=26.25, heights=(40.0, 42.0)); r = self.run_case(d)
        self.assertFalse(d.errors); self.assertEqual(r, 0x100); self.assertEqual(d.mem.load(P + 0x49a, 2), 0x100)
        self.assertAlmostEqual(fl(d.mem.load(P + 0x30)), 30.25, places=5)
        self.assertEqual(fl(d.mem.load(0x70000b80)), fl(d.mem.load(P + 0x30)))   # origin restarted
        self.assertEqual(d.mem.load(P + 0x508, 1), 0x4f)                           # armed, then decremented
        self.assertEqual(d.sound, []); self.assertNotIn(('random',), d.calls)      # +49c clear
        foot = d.queries[10:15]
        self.assertTrue(all(abs(q[1][1] - 41.0) < 1e-5 for q in foot))            # +3.0 foot height
        self.assertAlmostEqual(foot[2][1][0] - foot[2][2][0], 6.0, places=4)       # 2 behind to 4 ahead

    def test_facing_threshold_rejects_parallel_normal(self):
        d = SceneDriver(wall=26.25, heights=(40.0, 42.0), normal=(0.0, 0.0, 1.0)); r = self.run_case(d)
        self.assertFalse(d.errors); self.assertEqual(r, 0); self.assertEqual(fl(d.mem.load(P + 0x30)), 30.0)

    def test_attribute_gate_ignores_0x100_without_bit_2(self):
        d = SceneDriver(wall=26.25, attr=0); r = self.run_case(d)
        self.assertFalse(d.errors); self.assertEqual(r, 0)

    def test_head_tier_assigns_side_flag(self):
        d = SceneDriver(hit_indexes={0, 2}); r = self.run_case(d)
        self.assertFalse(d.errors); self.assertEqual(r, 1)          # last head push (straight) assigns 0|1
        d = SceneDriver(hit_indexes={2, 4}); r = self.run_case(d)
        self.assertFalse(d.errors); self.assertEqual(r, 3)          # side probe 2|1
        d = SceneDriver(hit_indexes={2, 7}); r = self.run_case(d)
        self.assertFalse(d.errors); self.assertEqual(r, 0x31)       # middle ORs 0x30

    def test_special_surface_fails_closed_with_balanced_frame(self):
        d = SceneDriver(wall=26.25, flags=0x200); self.run_case(d)
        self.assertIn(('fault', 0x40), d.calls)

    def test_rear_probe_count_and_foot_threshold_follow_4ad(self):
        d = SceneDriver(); self.run_case(d, f4ad=1)
        self.assertFalse(d.errors); self.assertEqual(len(d.queries), 18)
        # dot -0.5 passes -0.3 but not the -0.7 threshold used by foot probes with +4ad.
        n = (0.5, 0.0, math.sqrt(0.75))
        d = SceneDriver(hit_indexes={12}, normal=n); self.assertEqual(self.run_case(d, f4ad=0), 0x100)
        d = SceneDriver(hit_indexes={12}, normal=n); self.assertEqual(self.run_case(d, f4ad=1), 0)

    def test_bump_sound_selection_and_volume(self):
        d = SceneDriver(wall=26.25, heights=(40.0, 42.0)); r = self.run_case(d, f49c=2, volume=1.0)
        self.assertFalse(d.errors); self.assertEqual(r, 0x100)
        self.assertEqual(d.sound, [(0x1234, 0x1000, 127, 0x40)])   # offset ((0x7fff<<2)>>15)<<1 = 6
        d = SceneDriver(wall=26.25, heights=(40.0, 42.0)); self.run_case(d, f49c=2, f508=5)
        self.assertEqual(d.sound, []); self.assertEqual(d.mem.load(P + 0x508, 1), 4)

    def test_volume_outside_verified_range_fails_closed(self):
        d = SceneDriver(wall=26.25, heights=(40.0, 42.0)); self.run_case(d, f49c=2, volume=1.5)
        self.assertIn(('fault', 0x41), d.calls); self.assertEqual(d.sound, [])

    def test_forward_motion_does_not_bump_on_head_hit(self):
        d = SceneDriver(hit_indexes={2}); self.run_case(d, heading=math.pi / 2)
        self.assertEqual(d.mem.load(P + 0x508, 1), 0)              # result 1 and |dh| < 2.967


class OracleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = library(output=ROOT / 'local/build/player_constraint_fp.so')
        cls.lib.hg_ee_fp.argtypes = [C.c_uint32, C.c_uint32, C.c_uint32, C.POINTER(C.c_uint32)]
        cls.lib.hg_ee_fp.restype = C.c_uint32
    def c(self, op, a, b):
        fault = C.c_uint32(); v = self.lib.hg_ee_fp(op, a, b, C.byref(fault)); return v, fault.value
    def samples(self, seed):
        rng = random.Random(seed)
        out = [0x3f800000, 0x40490fdb, 0x43340000, 0xc2b40000, 0x42340000, 0x00800000, 0x7f7fffff, 0x3f7fffff, 0x00000000, 0x80000000]
        for _ in range(1500):
            e = rng.choice((rng.randrange(1, 255), rng.randrange(100, 160)))
            out.append((rng.getrandbits(1) << 31) | (e << 23) | rng.getrandbits(23))
        return out
    def test_div_nearest_matches_oracle(self):
        xs = self.samples(1); ys = self.samples(2)
        for a, b in zip(xs, ys):
            v, fault = self.c(8, a, b)
            if (b >> 23) & 255 == 0: self.assertEqual(fault, 1); continue
            self.assertEqual(fault, 0)
            self.assertEqual(v, fp_signed_nearest(fp_value(a) / fp_value(b)), (hex(a), hex(b)))
    def test_vu_rsqrt_matches_oracle(self):
        xs = self.samples(3); ys = [y & 0x7fffffff for y in self.samples(4)]
        for a, b in zip(xs, ys):
            v, fault = self.c(9, a, b)
            if (b >> 23) & 255 == 0: self.assertEqual(fault, 1); continue
            root = fp_chop(fp_value(b), root=True)
            self.assertEqual(fault, 0); self.assertEqual(v, fp_signed_chop(fp_value(a) / fp_value(root)), (hex(a), hex(b)))
    def test_negative_radicand_fails_closed(self):
        self.assertEqual(self.c(9, 0x3f800000, 0xbf800000)[1], 1)


class ModelInstructionTests(unittest.TestCase):
    def machine(self, words):
        m = Machine()
        for i, w in enumerate(words + [0x03e00008, 0]): m.store(0x1000 + 4 * i, w, 4)
        return m
    def test_multu_mult1_hi_lo(self):
        # multu v1,v0 ; mflo a2 ; mfhi t0 ; mult1 a0,a0,v0
        m = self.machine([0x00620019, 0x00003012, 0x00004010, 0x70822018])
        m.reg[3] = 0xffffffff; m.reg[2] = 2; m.reg[4] = 0xfffffffe
        m.run(0x1000, stop=0x1018)
        self.assertEqual(m.reg[6], 0xfffffffffffffffe); self.assertEqual(m.reg[8], 1)
        self.assertEqual(m.reg[4] & 0xffffffffffffffff, 0xfffffffffffffffc); self.assertEqual(m.lo1, 0xfffffffffffffffc)
    def test_64bit_ops_and_sync(self):
        # daddiu v1,v0,1 ; dsrl a0,v1,4 ; dsra a1,v1,4 ; dsubu a2,v1,v0 ; sync.p
        m = self.machine([0x64430001, 0x0003213a, 0x0003293b, 0x0062302f, 0x0000040f])
        m.reg[2] = 0x80000000000000ff
        m.run(0x1000, stop=0x101c)
        self.assertEqual(m.reg[3], 0x8000000000000100); self.assertEqual(m.reg[4], 0x0800000000000010)
        self.assertEqual(m.reg[5], 0xf800000000000010); self.assertEqual(m.reg[6], 1)
    def test_cp0_status_requires_supplied_value(self):
        m = self.machine([0x40036000])            # mfc0 v1,Status
        with self.assertRaises(ValueError): m.run(0x1000, stop=0x1004)
        m = self.machine([0x40036000, 0x42000039, 0x40026000, 0x42000038, 0x40046000])   # mfc0; di; mfc0; ei; mfc0
        m.cop0_status = 0x10001; m.run(0x1000, stop=0x101c)
        self.assertEqual((m.reg[3], m.reg[2], m.reg[4]), (0x10001, 1, 0x10001))


class GuestFrameSeamTests(unittest.TestCase):
    def test_balanced_frames_and_rejections(self):
        import verify_player_connected_runtime as V
        d = V.ConnectedRuntimeDriver(V.CapturedMemory(bytes(0x2000000)), b'', [])
        d.external(V.GUEST_FRAME, 0x70, 1); self.assertEqual(d.guest_frame, 0x70)
        d.external(V.GUEST_FRAME, 0x70, 0); self.assertEqual(d.guest_frame, 0)
        for args in ((0x70, 0), (0x71, 1), (0x70, 2)):
            with self.assertRaises(ValueError): d.external(V.GUEST_FRAME, *args)


if __name__ == '__main__': unittest.main()
