"""Synthetic M6 fixtures. Original files and captures are never used in CI."""
import ctypes as C
import struct
import unittest
from resource_harness import Memory, ResourceEnv, library, ROOT
from test_resource_fixup import FixupDriver, FRAME, configure as fixup_configure
from test_area_lifecycle import configure as area_configure, setup_alternative_pair, TASK, DEST

ENTRIES = {'geometry':0x1386e0, 'material_next':0x138690,
           'descriptor_address':0x138a80, 'sqrt':0x143530}


def configure(lib):
    fixup_configure(lib)
    area_configure(lib)
    lib.hg_resource_geometry.argtypes = [C.POINTER(ResourceEnv), C.c_uint32, C.c_uint32]
    lib.hg_resource_geometry.restype = None
    lib.hg_resource_material_next.argtypes = [C.POINTER(ResourceEnv), C.c_uint32]
    lib.hg_resource_material_next.restype = C.c_uint32
    lib.hg_resource_descriptor_address.argtypes = [C.c_uint64, C.c_uint32]
    lib.hg_resource_descriptor_address.restype = C.c_uint64
    lib.hg_resource_sqrt.argtypes = [C.c_uint32]
    lib.hg_resource_sqrt.restype = C.c_uint32
    return lib


class GeometryDriver(FixupDriver):
    def __init__(self, mem=None, lib=None):
        super().__init__(mem, lib)
        self.geometry_calls = []

    def external(self, pc, a=0, b=0, c=0, d=0):
        if pc == 0x1386e0:
            self.geometry_calls.append((a,b))
            self.lib.hg_resource_geometry(C.byref(self.env),a,b)
            return 0
        if pc == 0x152ae0:
            raise ValueError('Unexpected upload in selected M6 processing')
        return super().external(pc,a,b,c,d)


def geometry(mem, block=0xb00000, kind=0, packets=2, vertices=2, bones=2):
    """Two invented tagged records or fixed kind-2 records; 3/4/5 radius."""
    mem.store(block,packets)
    mem.store(block+0x10,kind,1)
    mem.store(block+0x11,bones,1)
    for offset,value in ((0x14,-3.),(0x24,2.),(0x1c,-1.),(0x2c,4.)):
        mem.put(block+offset,struct.pack('<f',value))
    cursor=block+0x40
    descriptors=[]
    if kind == 2:
        for _ in range(packets):
            p=cursor+12;descriptors.append(p)
            mem.store(p,0x100);mem.store(p+4,(0x200<<5)|0x18)
            cursor+=128
    elif kind in (0,3,4):
        for _ in range(packets):
            mem.store(cursor,0x10000000);mem.store(cursor+4,0x60000000)
            p=cursor+8;descriptors.append(p)
            mem.store(p,0x100);mem.store(p+4,(0x200<<5)|0x18)
            mem.store(p+8,vertices)
            cursor=p+36+vertices*36
            if kind in (3,4):cursor+=8+bones*vertices*(12 if kind==3 else 16)
        mem.store(cursor,0x13000000)
    return descriptors


def thirteen_pack(d):
    """Reuse M5's kind-2 AREA/worker composition, with thirteen model+textures."""
    setup_alternative_pair(d)
    base=0x2000
    data=bytearray(base+13*2048)
    struct.pack_into('<9I',data,2*2048,2,13,13,base,base,13*2048,0,0,13)
    struct.pack_into('<9I',data,3*2048,3,0,0,0,0,0,0,0,0)
    for i in range(13):
        ident=0x200+i
        struct.pack_into('<4I',data,2*2048+64+i*16,base+i*2048,2048,ident,0)
        mem=Memory();p=0xb00000
        mem.put(p,struct.pack('<6I',0x354b4c46,2,2048,0,0x01000020,0x09000400))
        geometry(mem,p+32,kind=(0,3,4)[i%3],packets=1,vertices=1,bones=1)
        texture=p+1024
        mem.store(texture,0x60000000);mem.store(texture+8,0x100)
        mem.store(texture+12,0x120);mem.store(texture+0x34,0x18)
        mem.store(texture+0x50,64);mem.store(texture+0x54,4)
        data[base+i*2048:base+(i+1)*2048]=mem.get(p,2048)
    d.files[1000]=bytes(data);d.mem.store(0x3ead24,len(data))


class GeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.lib=configure(library(output=ROOT/'local/build/geometry.so'))

    def test_scanned_and_fixed_materials_and_radius(self):
        for kind in (0,2,3,4):
            d=GeometryDriver(lib=self.lib);locations=geometry(d.mem,kind=kind)
            self.lib.hg_resource_geometry(C.byref(d.env),0xb00000,0x3200)
            self.assertFalse(d.errors)
            self.assertEqual(d.mem.load(0xb00020),0x40a00000)
            for p in locations:
                self.assertEqual(d.mem.load(p),0x3300)
                self.assertEqual(d.mem.load(p+4),0x3400<<5)

    def test_empty_and_nonmaterial_kinds_still_update_radius(self):
        for kind in (0,1,2,3,4,5,255):
            d=GeometryDriver(lib=self.lib);geometry(d.mem,kind=kind,packets=0)
            self.lib.hg_resource_geometry(C.byref(d.env),0xb00000,0)
            self.assertFalse(d.errors)
            self.assertEqual(d.mem.load(0xb00020),0x40a00000)

    def test_descriptor_carry_and_word_sign_extension(self):
        f=self.lib.hg_resource_descriptor_address
        self.assertEqual(f(0,0x80000000),0xffffffff80000000)
        self.assertEqual(f(0x3fff,1),0x2000004000)
        self.assertEqual(f(0xffffffffffffffff,0),0xffffffffffffffff)

    def test_root_is_chopped_and_preserves_exact_squares(self):
        f=self.lib.hg_resource_sqrt
        self.assertEqual(f(0),0)
        self.assertEqual(f(0x41c80000),0x40a00000)  # sqrt(25) = 5
        self.assertEqual(f(0x40000000),0x3fb504f3)  # floor-binary32 sqrt(2)
        self.assertEqual(f(0x3f800001),0x3f800000)

    def test_thirteen_calls_through_area_worker_and_publication(self):
        d=GeometryDriver(lib=self.lib);thirteen_pack(d)
        d.invoke(self.lib,'request_raw',2,2)
        self.lib.hg_resource_worker_fixed(C.byref(d.env),FRAME)
        self.assertFalse(d.errors)
        for i in range(13):
            p=DEST+i*2048
            self.assertEqual(d.mem.load(p+0x40),0x40a00000)
            self.assertEqual(d.mem.load(p+8),0x3100)
            self.assertEqual(d.mem.load(p+1024+8),0x3200)
            self.assertEqual(d.mem.load(0x3f4300+(0x200+i)*4),p)
        self.assertFalse(d.geometry_calls)  # resolved inside the C composition
        self.assertFalse(any(c[:2]==('call',0x137ed0) for c in d.calls))
        self.assertEqual((d.mem.load(TASK+0x30,2),d.mem.load(0x3eacec,1)),(0,0))


if __name__ == '__main__':unittest.main()
