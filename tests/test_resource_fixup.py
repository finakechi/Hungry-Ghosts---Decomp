"""Synthetic M4 fixtures; no original data. Guest scratch keeps malformed
indices in the bus, rather than indexing outside native C arrays."""
import ctypes as C
import struct
import unittest
from resource_harness import Driver, Memory, ResourceEnv, library, setup_pack, synthetic_pack

FRAME=0x1ff05c0
FUNCTIONS={
    'fixup':(0x137ed0,4,True), 'map_address':(0x1389d0,2,True),
    'flk5_entry':(0x138af0,2,True),'flk5_type':(0x138b30,2,True),
    'flk5_count':(0x138b70,1,True),'flk5_size':(0x138b90,1,True),
    'flk5_disable':(0x138560,2,False),'base_delta':(0x1385a0,2,True),
}
def configure(lib):
    for name,(_,arity,returns) in FUNCTIONS.items():
        symbol='hg_resource_'+name if name in ('fixup','map_address','base_delta') else 'hg_'+name
        fn=getattr(lib,symbol);fn.argtypes=[C.POINTER(ResourceEnv)]+[C.c_uint32]*arity
        fn.restype=C.c_uint32 if returns else None
    lib.hg_resource_worker_fixed.argtypes=[C.POINTER(ResourceEnv),C.c_uint32]
    lib.hg_resource_worker_fixed.restype=None
    return lib

def flk(mem,block=0xb00000,types=(9,),links=2,size=0x401):
    mem.store(block,0x354b4c46);mem.store(block+4,len(types));mem.store(block+8,size)
    for i,kind in enumerate(types):
        off=0x200+i*0x400;mem.store(block+16+i*4,(kind<<24)|off)
        p=block+off
        if kind==9:
            mem.store(p+8,0x100);mem.store(p+12,0x143)
            for j in range(links):
                q=p+j*0x80;mem.store(q,0x60000000 if j==links-1 else 7)
                mem.store(q+0x34,0xabcdd123+j);mem.store(q+0x50,64+j);mem.store(q+0x54,4)
        elif kind==1:mem.store(p,0xffffffff) # model sentinel avoids external work
    return block

class FixupDriver(Driver):
    def __init__(self,mem=None,lib=None):super().__init__(mem);self.lib=lib
    def external(self,pc,a=0,b=0,c=0,d=0):
        if pc==0x1389d0:
            # For direct fixup/rebase C calls, resolve the helper on the same bus.
            return self.lib.hg_resource_map_address(C.byref(self.env),a,FRAME-0x50)
        if pc in (0x1386e0,0x152ae0):
            self.calls.append(('call',pc,*((a,b) if pc==0x1386e0 else (a,))))
            return 0
        return super().external(pc,a,b,c,d)

class Fixup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.lib=configure(library())
    def invoke(self,d,p,index=0,id=4):
        answer=self.lib.hg_resource_fixup(C.byref(d.env),p,index,id,FRAME)
        self.assertFalse(d.errors);return answer
    def test_linked_rebase_and_aligned_return(self):
        d=FixupDriver(lib=self.lib);p=flk(d.mem)
        self.assertEqual(self.invoke(d,p),p+0x480)
        self.assertEqual(d.mem.load(p+8),0x3100)
        self.assertEqual(d.mem.load(p+0x208),0x3200)
        self.assertEqual(d.mem.load(p+0x20c),0x3243)
        self.assertEqual(d.mem.load(p+0x234),(0xabcdd123&0xffffc000)|0x3200)
        self.assertEqual(d.mem.load(p+0x2b4),(0xabcdd124&0xffffc000)|0x3204)
    def test_ids_and_selected_texture_delta(self):
        for id,base in ((4,0x3200),(0x96,0x1e00),(0x22f,0x1e00),(0x7c,0x3b00)):
            d=FixupDriver(lib=self.lib);p=flk(d.mem,types=(9,8,9,6,1))
            d.mem.store(p+0xa08,0x120)
            answer=self.invoke(d,p,1,id)
            self.assertEqual(d.mem.load(p+8),base-0x120)
            self.assertEqual(d.mem.load(p+0x208),base)
            self.assertEqual(d.mem.load(p+0xa08),base)
            self.assertEqual(answer,p+0x200 if id==0x7c else p+0x480)
            if id==0x7c:
                self.assertEqual(d.mem.load(p+16),0xff000000)
                self.assertIn(('call',0x152ae0,p+0xa00),d.calls)
    def test_once_only_sentinel_and_ignored_types(self):
        d=FixupDriver(lib=self.lib);p=flk(d.mem,types=(9,8,6,1))
        self.assertEqual(self.invoke(d,p,id=999999),0)
        self.assertEqual(d.mem.load(p+12,1),1)
        self.assertEqual(d.mem.load(p+8),0x401)
        self.assertEqual(self.invoke(d,p,id=999999),0)
        d=FixupDriver(lib=self.lib);p=flk(d.mem,types=(8,6,0,255))
        self.assertEqual(self.invoke(d,p),0)
    def test_raw_rebase_ids_and_upload_boundary(self):
        for id,base in ((0x3d,0x3f00),(0x7d,0x3b00),(0x92,0x3600)):
            d=FixupDriver(lib=self.lib);flk(d.mem);p=0xb00200
            self.assertEqual(self.invoke(d,p,id=id),p)
            self.assertEqual(d.mem.load(p+8),base)
            self.assertEqual(d.calls,[('call',0x152ae0,p)])
    def test_geometry_boundary_and_link_reload(self):
        d=FixupDriver(lib=self.lib);p=flk(d.mem,types=(1,9));entry=p+0x200
        d.mem.store(entry,1);d.mem.store(entry+0x30,0x100)
        self.invoke(d,p)
        self.assertEqual(d.calls[:2],[('call',0x1386e0,entry,0x3100),('call',0x1386e0,entry+0x100,0x3100)])
    def test_malformed_index_remains_guest_memory(self):
        d=FixupDriver(lib=self.lib);p=flk(d.mem)
        # Index 16 aliases models[0], initially zero. Original reads guest +8.
        d.mem.store(8,0x200)
        self.invoke(d,p,16)
        self.assertEqual(d.mem.load(p+8),0x3000)
        self.assertEqual(self.lib.hg_flk5_entry(C.byref(d.env),p,0x1000000),p+0x200)
    def test_loader_passes_nondefault_member_id(self):
        d=FixupDriver(lib=self.lib);pack=synthetic_pack();tmp=Memory();flk(tmp,size=0x800)
        pack[2048:4096]=tmp.get(0xb00000,2048)
        struct.pack_into('<I',pack,72,0x96);setup_pack(d,pack=pack)
        d.invoke(self.lib,'request_raw',0,0)
        self.lib.hg_resource_worker_fixed(C.byref(d.env),FRAME)
        self.assertFalse(d.errors)
        self.assertEqual(d.mem.load(0xb00208),0x1e00)
        self.assertEqual(d.mem.load(0x3f4300+0x96*4),0xb00000)
    def test_combined_special_read_fixup_then_bulk(self):
        d=FixupDriver(lib=self.lib);pack=synthetic_pack();tmp=Memory();flk(tmp,size=0x800)
        pack[2048:4096]=tmp.get(0xb00000,2048)
        for off,value in ((8,1),(16,4096),(20,2048),(32,0),(72,0x96)):
            struct.pack_into('<I',pack,off,value)
        struct.pack_into('<H',pack,0x1a,1);setup_pack(d,pack=pack)
        d.invoke(self.lib,'request_raw',0,0)
        self.lib.hg_resource_worker_fixed(C.byref(d.env),FRAME)
        self.assertFalse(d.errors)
        self.assertEqual(d.mem.load(0xb00208),0x1e00)
        self.assertEqual(d.mem.load(0x3f4558),0xb00000)
        self.assertEqual(d.mem.load(0x3f431c),0xb00800)
        self.assertEqual(d.mem.get(0xb00800,4),b'DATA')
        self.assertEqual(d.mem.load(0x3f4d3c),0xb01000)
    def test_stack_array_capacity_edges(self):
        for types in ((9,)*16,(9,)*17,(1,)*64,(1,)*65):
            d=FixupDriver(lib=self.lib);p=flk(d.mem,types=types)
            answer=self.invoke(d,p)
            self.assertEqual(answer,p+0x480 if types[0]==9 else 0)
    def test_malformed_unaligned_index_is_bus_fault(self):
        class AlignedMemory(Memory):
            def load(self,address,width=4):
                if address%width:raise ValueError('Unaligned guest load')
                return super().load(address,width)
        d=FixupDriver(AlignedMemory(),self.lib);p=flk(d.mem)
        self.lib.hg_resource_fixup(C.byref(d.env),p,0xffffffff,4,FRAME)
        self.assertTrue(any('Unaligned guest load' in e for e in d.errors))
    def test_signed_counts_empty_and_negative(self):
        for count in (0,0x80000000,0xffffffff):
            d=FixupDriver(lib=self.lib);p=flk(d.mem);d.mem.store(p+4,count)
            self.assertEqual(self.invoke(d,p),0)
    def test_map_negative_uses_guest_stack_bytes(self):
        d=FixupDriver(lib=self.lib)
        d.mem.put(0x3e0260,bytes(range(32)))
        d.mem.store(FRAME-1,0xfe,1)
        self.assertEqual(self.lib.hg_resource_map_address(C.byref(d.env),0xffffffff,FRAME),0xfffffffe)
        for a,expected in ((0,0),(1,1),(7,7),(8,32),(31,103),(32,8),(127,127),(128,128),(0x7fffffff,0x7fffffff)):
            self.assertEqual(self.lib.hg_resource_map_address(C.byref(d.env),a,FRAME),expected)
    def test_combined_request_worker_load_fixup_publish(self):
        d=FixupDriver(lib=self.lib);pack=synthetic_pack();tmp=Memory();flk(tmp,size=0x800)
        pack[2048:4096]=tmp.get(0xb00000,2048);setup_pack(d,pack=pack)
        d.invoke(self.lib,'request_raw',0,0)
        self.lib.hg_resource_worker_fixed(C.byref(d.env),FRAME)
        self.assertFalse(d.errors)
        self.assertEqual(d.mem.load(0x3f4310),0xb00000)
        self.assertEqual(d.mem.load(0xb00008),0x3100)
        self.assertEqual(d.mem.load(0xb00208),0x3200)
        self.assertEqual(d.mem.load(0x3f4d3c),0xb01000)
        self.assertEqual(d.mem.load(0x3f8e30,2),0)
        self.assertFalse(any(x[:2]==('call',0x137ed0) for x in d.calls))

if __name__=='__main__':unittest.main()
