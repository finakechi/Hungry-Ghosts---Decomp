import ctypes as C
import unittest
from resource_harness import Driver, library, ResourceEnv

class RebaseDriver(Driver):
    def external(self,pc,a=0,b=0,c=0,d=0):
        if pc != 0x1389d0:raise ValueError('Unexpected dependency')
        self.calls.append((pc,a));return 0

class Rebase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib=library();cls.fn=cls.lib.hg_resource_rebase
        cls.fn.argtypes=[C.POINTER(ResourceEnv),C.c_uint32,C.c_uint32];cls.fn.restype=None
    def test_linked_rebase_and_signed_step(self):
        d=RebaseDriver();p=0xb00000
        d.mem.store(p,7);d.mem.store(p+8,0x100);d.mem.store(p+12,0x121)
        d.mem.store(p+0x34,0xffffc123);d.mem.store(p+0x50,0xffffffff);d.mem.store(p+0x54,65)
        q=p+128;d.mem.store(q,0x60000000);d.mem.store(q+0x34,0xabcdefff)
        self.fn(C.byref(d.env),p,0x3200)
        self.assertEqual(d.errors,[]);self.assertEqual(d.calls,[(0x1389d0,0x3240)])
        self.assertEqual(d.mem.load(p+8),0x3200);self.assertEqual(d.mem.load(p+12),0x3221)
        self.assertEqual(d.mem.load(p+0x34),0xfffff200)
        self.assertEqual(d.mem.load(q+0x34),(0xabcdefff&0xffffc000)|0x31ff)

    def test_mult_signed_word_lanes(self):
        from ee_subset import Machine, MASK64
        m=Machine();m.reg[4]=0xffffffff;m.reg[5]=2
        # mult v1,a0,a1; jr ra; nop
        m.put(0x1000,((4<<21)|(5<<16)|(3<<11)|0x18).to_bytes(4,'little')+bytes.fromhex('0800e00300000000'))
        m.run(0x1000)
        self.assertEqual(m.reg[3]&MASK64,MASK64-1)
        self.assertEqual(m.lo,MASK64-1);self.assertEqual(m.hi,MASK64)
