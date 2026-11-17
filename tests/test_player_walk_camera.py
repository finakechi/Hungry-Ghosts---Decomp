"""Synthetic dispatcher contracts. No retail captures or expected output bytes."""
import ctypes as C
import unittest
from resource_harness import Driver, ResourceEnv, ROOT, library

P=0x3f92d0

class DispatchDriver(Driver):
    def external(self,pc,a=0,b=0,c=0,d=0):
        if pc==0xfffffff0:
            self.calls.append(('fault',a));return 0
        if pc==0x159570:
            self.calls.append(('active',a))
            if hasattr(self,'active_effects'):
                for offset,width,value in self.active_effects:self.mem.store(P+offset,value,width)
                return 0
            self.mem.store(P+0x30,0x42440001)
            self.mem.store(P+0x428,0xdeadbeef)
            self.mem.store(P+0x4ac,0xff,1)
            self.mem.store(P+0x45a,1,2)
            self.mem.store(P+0x464,1,1)
            return 0
        if pc==0x1204e0:
            self.calls.append(('wrap',a));return 0x3f123456
        raise AssertionError(hex(pc))


def configure(lib):
    lib.hg_player_dispatch.argtypes=[C.POINTER(ResourceEnv)]
    lib.hg_player_dispatch.restype=None
    return lib

class DispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.lib=configure(library(output=ROOT/'local/build/player_walk_camera.so'))
    def test_active_delay_clear_signed_sum_and_quad_history(self):
        d=DispatchDriver();m=d.mem;m.store(P+0x18,1,1)
        m.put(P+0x30,bytes(range(16)));m.store(P+0x3c0,0x3d567750)
        m.store(0x95e57c,99);self.lib.hg_player_dispatch(C.byref(d.env))
        self.assertFalse(d.errors)
        self.assertEqual(d.calls,[('active',P),('wrap',0)])
        self.assertEqual(m.load(P+0x428),0)
        self.assertEqual(m.load(P+0x40),0x3f123456)
        self.assertEqual(m.load(0x3f9f6e,1),0) # signed -1 + 1 cancels
        self.assertEqual(m.get(0x700000e0,16),bytes(range(16)))
        self.assertEqual(m.get(P+0x390,16),m.get(P+0x30,16))
        self.assertEqual(m.load(P+1,1),1)
        self.assertEqual(m.load(0x95e57c),0)
    def test_pause_with_clear_flag_reaches_active(self):
        d=DispatchDriver();d.mem.store(P+0x18,1,1);d.mem.store(0x3eaee8,1,1)
        self.lib.hg_player_dispatch(C.byref(d.env));self.assertFalse(d.errors)
        self.assertEqual(d.calls[0],('active',P))
    def test_other_states_fail_closed(self):
        for state,pause,flag in [(s,0,0) for s in (0,2,3,4,5,6,7,8,255)]+[(1,1,1)]:
            d=DispatchDriver();d.mem.store(P+0x18,state,1)
            d.mem.store(0x3eaee8,pause,1);d.mem.store(0x3f9f38,flag,1)
            self.lib.hg_player_dispatch(C.byref(d.env))
            self.assertEqual(d.calls,[('fault',0x30)])
