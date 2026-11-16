"""M8 pool/list synthetic checks; callbacks deliberately remain external."""
import ctypes as C
import copy
import json
import unittest
from resource_harness import Driver, ResourceEnv, library, ROOT

FUNCTIONS = {
    0x142810: ('hg_actor_register_list', 3),
    0x1428f0: ('hg_actor_construct', 4),
    0x142970: ('hg_actor_construct_short', 4),
    0x1429b0: ('hg_actor_allocate', 3),
    0x1430a0: ('hg_actor_update_main', 0),
    0x143100: ('hg_actor_update_priority', 0),
    0x143150: ('hg_actor_update_type2', 0),
    0x1431a0: ('hg_actor_update_type2_priority', 0),
}


def configure(lib):
    for entry, (name, arity) in FUNCTIONS.items():
        f = getattr(lib, name)
        f.argtypes = [C.POINTER(ResourceEnv)] + [C.c_uint32]*arity
        f.restype = C.c_uint32 if entry == 0x1429b0 else None
    return lib


class ActorDriver(Driver):
    def external(self, pc, a=0, b=0, c=0, d=0):
        if pc in (0x100100, 0x100200):
            self.calls.append(('actor_callback', pc, a))
            # Delete current's link and modify the counter: the original uses
            # prefetched NEXT and increments the freshly loaded 16-bit counter.
            self.mem.store(a+0x24, 0)
            self.mem.store(0x3eaed8, 0xffff if pc == 0x100100 else 7, 2)
            return 0
        return super().external(pc, a, b, c, d)


class ActorListTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = configure(library(output=ROOT/'local/build/actor_lists.so'))

    def test_allocate_construct_and_dispatch(self):
        d = ActorDriver(); a, b = 0x500000, 0x500400
        d.mem.store(0x539430, a); d.mem.store(0x53943c, 2)
        d.mem.store(a+0x24, b)
        d.mem.put(a+0x90, bytes([0x66])*0x1c0)
        for actor, callback in ((a, 0x100100), (b, 0x100200)):
            self.assertEqual(self.lib.hg_actor_allocate(C.byref(d.env), 0, 0x123, callback), actor)
        self.assertEqual(d.mem.load(0x539430), 0)
        self.assertEqual(d.mem.load(0x53943c), 0)
        self.assertEqual(d.mem.load(a+0x24), b)
        self.assertEqual(d.mem.load(b+0x20), a)
        self.assertEqual(d.mem.load(a+2, 1), 0x23)
        self.assertEqual(d.mem.load(a+0x24c), 0x3e4ccccd)
        self.assertEqual(d.mem.load(a+0x245, 1), 255)
        d.mem.store(0x3f9772, 123, 1)
        self.lib.hg_actor_update_main(C.byref(d.env))
        self.assertEqual(d.calls[-2:], [('actor_callback', 0x100100, a), ('actor_callback', 0x100200, b)])
        self.assertEqual(d.mem.load(0x3eaed8, 2), 8)
        self.assertEqual(d.mem.load(0x3f9772, 1), 0)
        self.assertFalse(d.errors)

    def test_empty_pool_has_no_side_effect(self):
        d = Driver(); d.mem.store(0x53943c, 0xdeadbeef)
        self.assertEqual(self.lib.hg_actor_allocate(C.byref(d.env), 0x100, 4, 8), 0)
        self.assertEqual(d.mem.load(0x53943c), 0xdeadbeef)
        self.assertFalse(d.calls)

    def test_short_pool_and_type2_priority(self):
        d = Driver(); a = 0x500000
        d.mem.store(0x539438, a); d.mem.store(0x539444, 1)
        d.mem.store(a+0x29, 2, 1); d.mem.store(a+0x90, 0xdeadbeef)
        self.assertEqual(self.lib.hg_actor_allocate(C.byref(d.env), 0xff, 7, 0x100100), a)
        self.assertEqual(d.mem.load(0x539460), a)
        self.assertEqual(d.mem.load(0x539464), a)
        self.assertEqual(d.mem.load(a+0x28, 1), 0x80)
        self.assertEqual(d.mem.load(a+0x90), 0xdeadbeef)
        self.assertFalse(d.calls)

    def test_empty_dispatch_resets_count(self):
        for pc, (name, arity) in FUNCTIONS.items():
            if arity: continue
            d = Driver(); d.mem.store(0x3eaed8, 0x1234, 2)
            d.mem.store(0x3f9772, 99, 1)
            getattr(self.lib, name)(C.byref(d.env))
            self.assertEqual(d.mem.load(0x3eaed8, 2), 0)
            self.assertEqual(d.mem.load(0x3f9772, 1), 0 if pc == 0x1430a0 else 99)

    def test_evidence_requires_complete_bodies_and_delay_slots(self):
        from verify_actor_lists import validate_packet
        packet=json.loads((ROOT/'analysis/actor_lists/functions.json').read_text())
        validate_packet(packet)
        for mode in ('function','delay','range'):
            bad=copy.deepcopy(packet)
            if mode=='function':bad['functions'].pop()
            elif mode=='delay':bad['functions'][0]['instructions'].pop()
            else:bad['functions'][0]['body_ranges'][0]['end_exclusive']='0x001428ec'
            with self.assertRaises(ValueError):validate_packet(bad)
