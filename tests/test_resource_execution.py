"""Host scheduling tests with synthetic sectors and real loader/processing C."""
import ctypes as C
import struct
import unittest

from resource_harness import ROOT, library  # also puts tools/ on sys.path
from hg_verify_build import build_path
from run_front_end_native import Mem
from resource_execution import BUSY, ResourceScheduler, TASK, TASK_ACTIVE, TASK_ID
import lifecycle_differential as L

HANDLE = 0x3f4300
PACK = 1000


def pack_fixture():
    data = bytearray(0x110 * 2048)
    # Four directories share tiny payloads. Directory 0 carries an actual
    # FLK5 texture fixup, and #0x50 uses the special upload-only group.
    for index, (ident, member) in enumerate(((0, 4), (1, 0x9a), (0x4c, 0xa0), (0x6b, 0xa1),
                                            (0x50, 0xa2), (0xc6, 0xa3), (0xc7, 0xa4), (5, 0xab))):
        offset = (0x104 + index) * 2048
        sector = ident * 2048
        struct.pack_into('<9I', data, sector, ident, 1, 1, offset, offset, 2048,
                         0, 0, 1 if ident == 0 else 0)
        struct.pack_into('<4I', data, sector + 64, offset, 2048, member, 0)
        struct.pack_into('<I', data, offset, 0x100000 + ident)
        if ident == 0:
            struct.pack_into('<5I', data, offset, 0x354b4c46, 1, 2048, 0, 0x09000200)
            struct.pack_into('<4I', data, offset + 0x200, 0x60000000, 0, 0x100, 0x140)
        if ident == 0x50:
            struct.pack_into('<I', data, sector + 8, 0)
            struct.pack_into('<I', data, sector + 20, 0)
            struct.pack_into('<H', data, sector + 0x1c, 1)
            struct.pack_into('<4I', data, offset, 0x60000000, 0, 0x100, 0x140)
    return data


def area_fixture(tag):
    data = bytearray(2048)
    struct.pack_into('<6I', data, 0, tag, 0, 0xa87800, 0, 0, 16)
    return data


class Harness:
    def __init__(self, lib, latency=3, original=None):
        self.lib = lib
        self.mem = Mem(bytes(0x2000000), bytes(0x4000))
        if original:
            elf, ranges = original
            for start, end, offset in ranges:
                self.mem.write(start, elf[offset:offset + end - start])
        self.calls, self.errors = [], []
        self.files = {PACK: pack_fixture(), 500: area_fixture(0x5449544c), 501: area_fixture(0x41524541)}
        self.env = L.Env(None, L.LOAD(self._load), L.STORE(self._store), L.CALL(self._call), None, None)
        self.sched = ResourceScheduler(lib, self.env, self.mem, self.read, latency=lambda _lba, _size: latency)
        lib.hg_resource_request_raw.argtypes = [C.POINTER(L.Env), C.c_uint32, C.c_uint32]
        lib.hg_resource_request_raw.restype = None
        self.store(0x3ead20, PACK)
        self.store(0x3ead24, len(self.files[PACK]))
        self.store(0x3dff04, 0xa87800)
        self.store(0x3e01b0, 0)
        self.store(0x3e01b4, 1)
        for i, lba in enumerate((500, 501)):
            self.store(0x3f3a30 + i * 8, lba)
            self.store(0x3f3a34 + i * 8, 2048)
        self.store(0x3fa201, 255, 1)

    def read(self, lba, count):
        for base, data in self.files.items():
            start = (lba - base) * 2048
            if 0 <= start < len(data):
                return data[start:start + count * 2048]
        raise ValueError(f'invalid synthetic LBA {lba}')

    def load(self, at, width=4):
        return int.from_bytes(self.mem.read(at, width), 'little')

    def store(self, at, value, width=4):
        self.mem.write(at, value.to_bytes(width, 'little'))

    def _load(self, _, at, width):
        try:
            return self.load(at, width)
        except ValueError as exc:
            self.errors.append(str(exc))
            return 0

    def _store(self, _, at, width, value):
        try:
            self.store(at, value & ((1 << (8 * width)) - 1), width)
        except ValueError as exc:
            self.errors.append(str(exc))

    def external(self, pc, a, b, c, d):
        if pc in (0x103d60, 0x103d80, 0x103da0, 0x104e10):
            if pc == 0x103da0:
                self.calls.append((pc, self.load(BUSY, 1), self.load(TASK_ACTIVE, 2)))
            return self.sched.service(pc, a, b)
        if pc == 0x119ac8:
            self.calls.append((pc, a, b, c))
            self.mem.write(a, bytes([b & 0xff]) * c)
            return a
        if pc in (0x1041e0, 0x152ae0):
            self.calls.append((pc, a))
            return 0
        raise ValueError(f'unmodeled test call {pc:#x}: {a:#x}, {b:#x}, {c:#x}, {d:#x}')

    def _call(self, _, pc, a, b, c, d):
        try:
            return self.external(pc, a, b, c, d)
        except ValueError as exc:
            self.errors.append(str(exc))
            return 0

    def request(self, kind, directory):
        self.lib.hg_resource_request_raw(C.byref(self.env), kind, directory)

    def tick(self):
        self.sched.tick()
        if self.load(TASK_ACTIVE, 2):
            self.sched.service(0x104e10, self.load(TASK_ID), 0)

    def drain(self):
        for _ in range(256):
            if self.sched.thread is None:
                break
            self.tick()
        if self.errors or self.sched.fault or self.sched.thread is not None:
            raise AssertionError((self.errors, self.sched.fault, self.sched.thread))


class ResourceExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Attempt-local under the runner (HG_VERIFY_BUILD_DIR); local/build otherwise.
        cls.lib = library(output=build_path(ROOT / 'local/build/m19-resource.so'))

    def test_bootstrap_waits_retry_request_preserves_job_and_publishes(self):
        d = Harness(self.lib)
        d.request(0, 0)
        self.assertEqual(d.load(BUSY, 1), 1)
        self.assertEqual(d.load(TASK + 0x39, 1), 1)
        self.assertFalse(d.sched.reads)
        d.tick()
        self.assertEqual(d.load(0x3eace8, 1), 1)
        d.request(1, 1)  # original busy gate drops this request
        d.tick()
        d.tick()
        self.assertEqual(d.load(TASK + 0x44), 0)
        self.assertEqual(d.load(TASK + 0x39, 1), 1)
        self.assertEqual(d.load(0x3f3b04), 0)
        self.assertEqual(d.load(HANDLE + 16), 0)
        self.assertEqual(len(d.sched.requests), 1)
        d.drain()
        self.assertEqual(d.load(HANDLE + 16), 0xb00000)
        self.assertEqual(d.load(0xb00208), 0x3200)
        self.assertEqual(d.load(0x3f4d3c), 0xb00800)
        self.assertEqual(d.calls[-1], (0x103da0, 0, 0))
        self.assertEqual(len(d.sched.finished), 1)

    def test_area_change_order_handle_clear_and_retention(self):
        d = Harness(self.lib)
        d.request(0, 0)
        d.drain()
        d.store(0x700000c4, 2)
        d.store(0x3fa1b0, 1, 1)
        d.store(HANDLE + 0x99 * 4, 0x1deadbee)
        d.mem.write(0xa88000, b'\xff' * 16)
        d.request(2, 0xc6)
        d.drain()
        self.assertEqual(d.mem.read(0xa87800, 2048), bytes(d.files[501]))
        self.assertEqual(d.mem.read(0xa88000, 16), b'\0' * 16)
        self.assertEqual(d.load(HANDLE + 0x99 * 4), 0)
        self.assertEqual(d.load(0x3f9fef, 1), 0x4c)
        self.assertEqual(d.load(0x46e10c, 1), 1)
        self.assertEqual([r['lba'] for r in d.sched.reads[d.sched.requests[1]['first_read']:]
                          if r['destination'] in (0xa87800, 0x3f3b00)],
                         [501, PACK + 0xc6, PACK + 5, PACK + 0xc7, PACK + 0x6b, PACK + 0x4c])
        prev = len(d.sched.reads)
        d.request(8, 0)
        d.drain()
        self.assertEqual(len(d.sched.reads), prev)
        self.assertEqual(d.load(TASK_ID), 3)
        self.assertEqual(d.load(HANDLE + 0xa0 * 4), d.load(0x3f4d48))

    def test_upload_only_postprocess_and_cache_hit(self):
        d = Harness(self.lib)
        d.request(0, 0)
        d.drain()
        d.request(1, 0x50)
        d.drain()
        self.assertEqual(d.load(0x1e00008), 0x1e00)
        self.assertIn((0x152ae0, 0x1e00000), d.calls)
        d.store(0x3e0210 + 4, 0x50)
        d.request(6, 1)
        d.drain()
        submits = len(d.sched.reads)
        d.request(6, 1)
        d.drain()
        self.assertEqual(len(d.sched.reads), submits)

    def test_bad_transport_holds_completion(self):
        d = Harness(self.lib)
        d.request(0, 0)
        d.files.clear()
        for _ in range(10):
            d.tick()
        self.assertIn('invalid synthetic LBA', d.sched.fault)
        self.assertEqual(d.load(BUSY, 1), 1)
        self.assertFalse(d.sched.finished)
        self.assertEqual(d.load(HANDLE + 16), 0)

    def test_wake_identity_and_active_state(self):
        d = Harness(self.lib)
        d.request(0, 0)
        with self.assertRaisesRegex(ValueError, 'unexpected loader thread'):
            d.sched.service(0x104e10, d.load(TASK_ID) + 1, 0)
        self.assertEqual(d.load(TASK + 0x39, 1), 1)
        d.drain()
        with self.assertRaisesRegex(ValueError, 'unexpected loader thread'):
            d.sched.service(0x104e10, d.load(TASK_ID), 0)


if __name__ == '__main__':
    unittest.main()
