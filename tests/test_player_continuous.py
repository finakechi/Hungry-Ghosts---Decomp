"""Synthetic M13 frame-advance contracts. No retail captures, original bytes or expected output."""
import ctypes as C
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from resource_harness import Driver, ResourceEnv, ROOT, library

G = 0x3eaee8   # byte 0x2f78(gp), gp = 0x3e7f70


class FrameDriver(Driver):
    """Bus driver for the M13 frame-advance routines; records seams and calls."""
    def __init__(self):
        super().__init__(); self.frames = []; self.calls = []; self.call_args = []; self.externals = []
        self._edge_sync = None; self.edge_calls = 0
        self.render_fields = []
        self.render_context = None
        self.next_field = 0
    def external(self, pc, a=0, b=0, c=0, d=0):
        if pc == 0xfffffff0: self.frames.append(('fault', a)); return 0
        if pc == 0xfffffff1: self.frames.append((a, b)); return 0
        if pc == 0xfffffff3: self.externals.append(a); return 0
        if pc == 0xfffffff8: self.render_fields.append(a); return self.next_field
        if pc == 0x148970:
            ctx = self.mem.load(0x3eb008)
            self.render_context = (self.mem.load(ctx + 0x598), self.mem.load(ctx + 0x59c))
        if pc == 0x31bb80 and self._edge_sync is not None:
            fn = self._edge_sync; fn.argtypes = [C.POINTER(ResourceEnv)]; fn.restype = None
            fn(C.byref(self.env)); self.edge_calls += 1; return 0
        self.calls.append(pc); self.call_args.append((pc,a,b,c,d)); return 0


class ContinuousTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build = tempfile.TemporaryDirectory()
        cls.addClassCleanup(build.cleanup)
        cls.lib = library(output=Path(build.name) / 'player_continuous.so')

    def run_edge_sync(self):
        d = FrameDriver()
        for addr, val in ((0x3fa040, 7), (0x3fa050, 0xdead), (0x3f9e34 + 0x210, 11), (0x3f9e34 + 0x220, 0xdead),
                          (0x3f9e38 + 0x210, 22), (0x3f9e38 + 0x220, 0xdead),
                          (0x3f9e3c + 0x210, 33), (0x3f9e3c + 0x220, 0xdead)):
            d.mem.store(addr, val)
        f = self.lib.hg_frame_edge_sync
        f.argtypes = [C.POINTER(ResourceEnv)]; f.restype = None
        f(C.byref(d.env))
        return d

    def test_edge_sync_copies_and_clears(self):
        d = self.run_edge_sync()
        self.assertEqual(d.mem.load(0x3fa050), 7)
        self.assertEqual(d.mem.load(0x3fa040), 0)
        for base in (0x3f9e34, 0x3f9e38, 0x3f9e3c):
            self.assertEqual(d.mem.load(base + 0x220), d.mem.load(base + 0x210) if False else {
                0x3f9e34: 11, 0x3f9e38: 22, 0x3f9e3c: 33}[base])
            self.assertEqual(d.mem.load(base + 0x210), 0)

    def test_edge_sync_only_touches_its_domains(self):
        d = self.run_edge_sync()
        self.assertEqual(d.mem.load(0x3f9e34 + 0x230), 0)  # untouched neighbour

    def test_counter_advance_increments_when_guard_clear(self):
        d = FrameDriver()
        d.mem.store(0x46d930, 1208); d.mem.store(0x46e110, 1207)
        f = self.lib.hg_counter_advance
        f.argtypes = [C.POINTER(ResourceEnv)]; f.restype = None
        f(C.byref(d.env))
        self.assertEqual(d.mem.load(0x46d930), 1209)
        self.assertEqual(d.mem.load(0x46e110), 1208)

    def test_counter_advance_guard_blocks(self):
        d = FrameDriver()
        d.mem.store(G, 1, 1); d.mem.store(0x46d930, 1208); d.mem.store(0x46e110, 1207)
        f = self.lib.hg_counter_advance
        f.argtypes = [C.POINTER(ResourceEnv)]; f.restype = None
        f(C.byref(d.env))
        self.assertEqual(d.mem.load(0x46d930), 1208)
        self.assertEqual(d.mem.load(0x46e110), 1207)

    def test_counter_guard_uses_original_gp_and_reaches_threshold(self):
        f = self.lib.hg_counter_advance
        f.argtypes = [C.POINTER(ResourceEnv)]; f.restype = None
        for guard, before, expected in ((0, 107998, 107999),
                                        (0, 107999, 0),
                                        (1, 107999, 107999),
                                        (1, 108000, 0)):
            d = FrameDriver()
            d.mem.store(0x3fcee8, 1, 1)  # former incorrect alias is irrelevant
            d.mem.store(G, guard, 1)
            d.mem.store(0x46d930, before)
            d.mem.store(0x46e110, 12)
            d.mem.store(0x46d908, 1)
            f(C.byref(d.env))
            self.assertEqual(d.mem.load(0x46d930), expected)
            self.assertEqual(d.mem.load(0x46e110), 12 if guard else 13)
            self.assertEqual(d.mem.load(0x46d908), 2 if expected == 0 else 1)

    def test_counter_advance_mega_counter(self):
        d = FrameDriver()
        d.mem.store(0x46d930, 108000); d.mem.store(0x46e110, 1)
        d.mem.store(0x46d908, -4)
        f = self.lib.hg_counter_advance
        f.argtypes = [C.POINTER(ResourceEnv)]; f.restype = None
        f(C.byref(d.env))
        self.assertEqual(d.mem.load(0x46d908), 0)  # -3 is negative: wrap resets to 0
        self.assertEqual(d.mem.load(0x46d930), 0)
        # a small non-negative mega value is kept
        d.mem.store(0x46d930, 108000); d.mem.store(0x46d908, 1)
        f(C.byref(d.env))
        self.assertEqual(d.mem.load(0x46d908), 2)
        # values at 3+ clamp to 3
        d.mem.store(0x46d930, 108000); d.mem.store(0x46d908, 3)
        f(C.byref(d.env))
        self.assertEqual(d.mem.load(0x46d908), 3)

    def test_frame_advance_positive_sequence(self):
        """3fa190 1209 -> 1210 (non-negative: kept), edge sync, 3f9ff8 clear and the
        guarded 46e118 increment."""
        d = FrameDriver(); d._edge_sync = self.lib.hg_frame_edge_sync
        d.mem.store(0x3fa190, 1209); d.mem.store(0x46e118, 1209); d.mem.store(0x3f9ff8, 9, 2)
        d.mem.store(0x3fa040, 5)
        f = self.lib.hg_frame_advance
        f.argtypes = [C.POINTER(ResourceEnv)]; f.restype = None
        f(C.byref(d.env))
        self.assertEqual(d.mem.load(0x3fa190), 1210)
        self.assertEqual(d.mem.load(0x46e118), 1210)
        self.assertEqual(d.mem.load(0x3f9ff8, 2), 0)
        self.assertEqual(d.mem.load(0x3fa050), 5)      # edge sync ran
        self.assertEqual(d.mem.load(0x3fa040), 0)
        self.assertEqual(d.frames, [(0x50, 1), (0x50, 0)])
        self.assertEqual(d.edge_calls, 1)

    def test_frame_advance_guard_blocks_counter(self):
        d = FrameDriver()
        d.mem.store(G, 1, 1); d.mem.store(0x46e118, 1209)
        f = self.lib.hg_frame_advance
        f.argtypes = [C.POINTER(ResourceEnv)]; f.restype = None
        f(C.byref(d.env))
        self.assertEqual(d.mem.load(0x46e118), 1209)

    def test_frame_advance_negative_resets(self):
        """The original's bgez guard: a negative incremented value resets to zero."""
        d = FrameDriver()
        d.mem.store(0x3fa190, 0xfffffffe)  # -2 -> -1 (negative: reset)
        f = self.lib.hg_frame_advance
        f.argtypes = [C.POINTER(ResourceEnv)]; f.restype = None
        f(C.byref(d.env))
        self.assertEqual(d.mem.load(0x3fa190), 0)

    def test_frame_advance_nonnegative_keeps(self):
        d = FrameDriver()
        d.mem.store(0x3fa190, 5)
        f = self.lib.hg_frame_advance
        f.argtypes = [C.POINTER(ResourceEnv)]; f.restype = None
        f(C.byref(d.env))
        self.assertEqual(d.mem.load(0x3fa190), 6)

    def test_pre_dispatch_phase_and_packet_cursor_carry(self):
        d = FrameDriver()
        ctx = 0x500000
        d.mem.store(0x3e7f70+0x3098,ctx)
        d.mem.store(ctx+0x550,0x510000)
        d.mem.store(ctx+0x5c0,0)
        for i in range(12):
            a=0x39e960+0x20*i
            d.mem.store(a,i//3,1)
            d.mem.store(a+3,1,1)
            d.mem.store(a+4,0x43000000)
            d.mem.store(a+8,0x43000000)
            d.mem.store(a+0xc,0x42800000 if i==0 else 0)
            d.mem.store(a+0x10,0xbf800000 if i==0 else 0)
        d.mem.store(0x39e960+12*0x20,0xff,1)
        f=self.lib.hg_pre_dispatch_service
        f.argtypes=[C.POINTER(ResourceEnv)];f.restype=None
        f(C.byref(d.env))
        self.assertEqual(d.mem.load(0x39e96c),0x427c0000) # 64 - 1 = 63
        self.assertEqual(d.mem.load(ctx+0x550),0x510730)
        self.assertEqual(d.mem.load(0x510000,1),3)
        self.assertEqual(d.calls.count(0xfffffff6),2)
        f(C.byref(d.env))
        self.assertEqual(d.mem.load(0x39e96c),0x42780000) # 63 - 1 = 62
        self.assertEqual(d.mem.load(ctx+0x550),0x510e60)
        self.assertFalse([event for event in d.frames if event[0]=='fault'])

    def test_pre_dispatch_rejects_unverified_array_shape(self):
        d=FrameDriver()
        d.mem.store(0x39e960+5*0x20,0xff,1)
        f=self.lib.hg_pre_dispatch_service
        f.argtypes=[C.POINTER(ResourceEnv)];f.restype=None
        f(C.byref(d.env))
        self.assertIn(('fault',0x61),d.frames)

    def test_frame_dispatch_routes_camera_helpers_to_interpreter(self):
        """Camera helpers and the two neighboring engine services route to the
        instruction interpreter; the bounded 00198f60 service and (M15) the
        00143150 render-node list and 0012bc70 player model draw execute in C.
        With an empty list 00539458 and no player draw bits they only enter
        and leave their guest frames."""
        d = FrameDriver()
        ctx = 0x500000
        d.mem.store(0x3eb008, ctx)
        d.mem.store(ctx + 0x59c, 1)
        d.next_field = 1  # the host can report a repeated field
        d.mem.store(0x3f92d0 + 0x18, 1, 1)  # player state byte: observed active path
        d.mem.store(0x39e960+12*0x20,0xff,1) # bounded service-array sentinel
        f = self.lib.hg_player_frame_continuous
        f.argtypes = [C.POINTER(ResourceEnv), C.c_uint32]; f.restype = None
        f(C.byref(d.env), 0x1ff8000)
        # 0012bc70 enters 00123ee0 (0x30), which returns: player +e0 has no draw bits.
        self.assertEqual(d.frames, [(0x50, 1), (0x50, 0), (0x10, 1), (0x30, 1), (0x30, 0), (0x10, 0),
                                    (0x20, 1), (0x20, 0)])  # no faults
        marks = [row for row in d.call_args if row[0] == 0xfffffff6 and row[1] == 0x143150]
        self.assertEqual(marks, [(0xfffffff6, 0x143150, 1, 0, 0), (0xfffffff6, 0x143150, 0, 0, 0)])
        self.assertIn((0xfffffff6, 0x12bc70, 1, 0, 0), d.call_args)
        for pc in (0x143c60, 0x143d30, 0x143b90):
            self.assertIn(pc, d.calls)
            self.assertNotIn(pc, d.externals)
        self.assertIn((0x17f6d0,0x3f9e30,0,0,0),d.call_args)
        self.assertIn((0x17f2d0,0,0,0,0),d.call_args)
        self.assertEqual(d.render_fields, [1])
        self.assertEqual(d.render_context, (1, 1))
        self.assertEqual(d.calls[-1], 0x148970)
        self.assertNotIn(0x198f60,d.calls+d.externals)
        coordinator = (0x13b6e0, 0x1430a0, 0x143100,
                       0x12bc70, 0x18c520, 0x143150, 0x1431a0, 0x25c170, 0x182480)
        self.assertEqual(d.externals, [0x13b6e0,
                                       0x1430a0, 0x143100, 0x18c520,
                                       0x1431a0, 0x25c170, 0x182480])
        for pc in coordinator:
            self.assertNotIn(pc, d.calls)
        # M16: 0013ce80 is a C service now (state bytes zero: a silent return);
        # the frame router consumes it before the bus, its marks are emitted
        # and no area dispatch happens.
        self.assertNotIn(0x13ce80, d.calls)
        self.assertNotIn(0x13ce80, d.externals)
        self.assertNotIn(0xa8b800, d.calls)
        self.assertNotIn(0xfffffff9, d.calls)
        late = [row for row in d.call_args if row[0] == 0xfffffff6 and row[1] == 0x13ce80]
        self.assertEqual(late, [(0xfffffff6, 0x13ce80, 1, 0, 0), (0xfffffff6, 0x13ce80, 0, 0, 0)])


if __name__ == '__main__':
    unittest.main()
