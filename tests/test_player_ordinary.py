"""Synthetic ordinary-player seams; original observations are tested separately."""
import ctypes as C
import unittest
from resource_harness import Driver, ResourceEnv, ROOT, library

FUNCTIONS={0x159ad0:'hg_player_ordinary_update',0x159380:'hg_player_apply_transition',
           0x158d30:'hg_player_reset_transition'}
ARITIES={0x13a050:2,0x159d00:1,0x15e190:3,0x15fe00:1,0x159380:1,
         0x161110:2,0x188650:1,0x15bf30:1,0x187e10:1,0x15b1e0:2,
         0x158d90:1,0x159350:1,0x158dc0:1,0x158e20:1,0x158ce0:2}

def configure(lib):
    for name in FUNCTIONS.values():
        f=getattr(lib,name);f.argtypes=[C.POINTER(ResourceEnv),C.c_uint32];f.restype=None
    return lib

class OrdinaryDriver(Driver):
    def external(self,pc,a=0,b=0,c=0,d=0):
        self.calls.append(('call',pc,*(a,b,c,d)[:ARITIES[pc]]))
        if pc in (0x13a050,0x15fe00,0x187e10):
            return self.mem.load({0x13a050:0x600100,0x15fe00:0x600104,0x187e10:0x600108}[pc])
        return 0

class OrdinaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.lib=configure(library(output=ROOT/'local/build/player_ordinary.so'))

    def test_edge_control_and_priority(self):
        for edge,target,want in [(0,0,255),(128,0,1),(160,0,255),(160,0x700000,255)]:
            with self.subTest(edge=edge,target=target):
                d=OrdinaryDriver();a=0x500000
                d.mem.store(a+0x19,1,1);d.mem.store(a+0x1a,1,1);d.mem.store(a+0x449,255,1)
                d.mem.store(0x3eaf04,edge,2);d.mem.store(0x3eaec8,32,2);d.mem.store(0x3eaed0,128,2)
                d.mem.store(0x600108,target)
                self.lib.hg_player_ordinary_update(C.byref(d.env),a)
                self.assertFalse(d.errors,d.errors);self.assertEqual(d.mem.load(a+0x449,1),want)
                self.assertEqual(any(c[1]==0x159380 for c in d.calls),want!=255)
                self.assertEqual(d.mem.load(a+0x44b,1),2 if target and edge&32 else 0)

    def test_dispatch_keeps_neighboring_bytes_and_actor(self):
        for state,pc in [(0,0x158d90),(1,0x158e20),(2,0x158e20),(3,0x158dc0),(4,0x159350),(255,0x158d90)]:
            d=OrdinaryDriver();a=0x500000;d.mem.store(a+0x449,state,1)
            d.mem.store(0x3f9f38,0xaabbccdd)
            self.lib.hg_player_apply_transition(C.byref(d.env),a)
            self.assertFalse(d.errors,d.errors);self.assertEqual(d.mem.load(0x3f9f38),0xaabbcc00)
            self.assertEqual(d.calls,[('call',pc,a)])

    def test_reset_orders_dependency_and_animation(self):
        d=OrdinaryDriver();a=0x500000;d.mem.put(a,bytes([0x55])*0x540)
        self.lib.hg_player_reset_transition(C.byref(d.env),a)
        self.assertFalse(d.errors,d.errors)
        self.assertEqual(d.calls,[('call',0x158ce0,a,0),('call',0x15e190,0,a,0x06000000)])
        for off in [0x19,0x1a,0x1b,0x44d,0x4ac,0x44b]:self.assertEqual(d.mem.load(a+off,1),0)
        self.assertEqual(d.mem.load(a+0x449,1),255)

if __name__=='__main__':unittest.main()
