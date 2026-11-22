"""Synthetic M13 frame-advance contracts. No retail captures, original bytes or expected output."""
import ctypes as C
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from resource_harness import Driver, ResourceEnv, ROOT, library

G = 0x3fcee8   # byte 0x2f78(gp), the advance guard


class FrameDriver(Driver):
    """Bus driver for the M13 frame-advance routines; records seams and calls."""
    def __init__(self):
        super().__init__(); self.frames = []; self.calls = []; self.externals = []
        self._edge_sync = None; self.edge_calls = 0
    def external(self, pc, a=0, b=0, c=0, d=0):
        if pc == 0xfffffff0: self.frames.append(('fault', a)); return 0
        if pc == 0xfffffff1: self.frames.append((a, b)); return 0
        if pc == 0xfffffff3: self.externals.append(a); return 0
        if pc == 0x31bb80 and self._edge_sync is not None:
            fn = self._edge_sync; fn.argtypes = [C.POINTER(ResourceEnv)]; fn.restype = None
            fn(C.byref(self.env)); self.edge_calls += 1; return 0
        self.calls.append(pc); return 0


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

    def test_frame_dispatch_routes_camera_helpers_to_interpreter(self):
        """M13 dispatch contract: the camera-chain matrix helpers 00143c60/00143d30/
        00143b90 (reached from 0012bd00/0012c790) are NOT coordinator services -- they
        forward to the owner bus for original-instruction execution, exactly as in the
        M12 grounded path. Only the authenticated 0012f850 coordinator jal targets
        (0012f8a8..0012f92c) are declared external. Externalizing the helpers left the
        head matrix identity and diverged the camera struct from the first continuous
        frame."""
        d = FrameDriver()
        d.mem.store(0x3f92d0 + 0x18, 1, 1)  # player state byte: observed active path
        f = self.lib.hg_player_frame_continuous
        f.argtypes = [C.POINTER(ResourceEnv), C.c_uint32]; f.restype = None
        f(C.byref(d.env), 0x1ff8000)
        self.assertEqual(d.frames, [(0x50, 1), (0x50, 0)])  # no faults
        for pc in (0x143c60, 0x143d30, 0x143b90):
            self.assertIn(pc, d.calls)
            self.assertNotIn(pc, d.externals)
        coordinator = (0x198f60, 0x17f6d0, 0x17f2d0, 0x13b6e0, 0x1430a0, 0x143100,
                       0x12bc70, 0x18c520, 0x143150, 0x1431a0, 0x25c170, 0x182480,
                       0x13ce80)
        self.assertEqual(d.externals, [0x198f60, 0x17f6d0, 0x17f2d0, 0x13b6e0,
                                       0x1430a0, 0x143100, 0x12bc70, 0x18c520,
                                       0x143150, 0x1431a0, 0x25c170, 0x182480,
                                       0x13ce80])
        for pc in coordinator:
            self.assertNotIn(pc, d.calls)


if __name__ == '__main__':
    unittest.main()
