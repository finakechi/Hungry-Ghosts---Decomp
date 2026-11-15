"""M7 synthetic directory preparation; no original files or captures."""
import ctypes as C
import struct
import unittest
from resource_harness import Driver, Memory, ResourceEnv, library, ROOT
from test_resource_geometry import configure as geometry_configure, geometry
from test_resource_fixup import FRAME

TASK = 0x3f8e00
FUNCTIONS = {
    'load_indexed': (0x12fcf0, 'hg_resource_load_indexed'),
    'load_context': (0x12fde0, 'hg_resource_load_context'),
    'prepare_context': (0x12fe90, 'hg_resource_prepare_context'),
    'prepare_retained': (0x130020, 'hg_resource_prepare_retained'),
}


def configure(lib):
    geometry_configure(lib)
    lib.hg_resource_prepare_engine_fixed.argtypes=[C.POINTER(ResourceEnv),C.c_uint32,C.c_uint32]
    lib.hg_resource_prepare_engine_fixed.restype=None
    for _, symbol in FUNCTIONS.values():
        fn = getattr(lib, symbol)
        fn.argtypes = [C.POINTER(ResourceEnv), C.c_uint32]
        fn.restype = None
    return lib


class EngineOperationDriver(Driver):
    def external(self, pc, a=0, b=0, c=0, d=0):
        if pc == 0x156a50:
            self.calls.append(('call',pc,a))
            return ((a+self.mem.load(a+0x10)+0x40)&0xffffffff)&0xffffffc0
        return super().external(pc,a,b,c,d)


class EngineDriver(Driver):
    """Original external loader seam, including phase state at the call."""
    def __init__(self, mem=None):
        super().__init__(mem)
        self.change_selector = None

    def external(self, pc, a=0, b=0, c=0, d=0):
        if pc == 0x130900:
            self.calls.append(('general', a, self.mem.load((a+0x44)&0xffffffff), self.mem.get((a+0x38)&0xffffffff, 8)))
            if self.change_selector is not None:
                self.mem.store(0x3fa1b0, self.change_selector, 1)
            return 0
        return super().external(pc, a, b, c, d)


def invoke(lib, d, name, task=TASK):
    getattr(lib, FUNCTIONS[name][1])(C.byref(d.env), task)
    if d.errors:
        raise AssertionError(d.errors)


def engine_pack(d):
    """Empty kind-3 alternative followed by three real geometry/texture loads.

    The invented special member exercises the narrow audio endpoint contract;
    the other members use real reconstructed geometry and texture processing.
    """
    data = bytearray(0x100000)
    struct.pack_into('<9I', data, 3*2048, 3, 0, 0, 0, 0, 0, 0, 0, 0)
    for index, ident in enumerate((0x6b, 0x4c, 0x85)):
        offset = 0x90000+index*2048
        struct.pack_into('<9I', data, ident*2048, ident, 1, 1, offset, offset, 2048, 0, 0, 1)
        struct.pack_into('<4I', data, ident*2048+64, offset, 2048, 0x200+index, 0)
        m = Memory(); p = 0xb00000
        m.put(p, struct.pack('<6I', 0x354b4c46, 2, 2048, 0, 0x01000020, 0x09000400))
        geometry(m, p+32, packets=1, vertices=1)
        m.store(p+1024, 0x60000000); m.store(p+1032, 0x100)
        m.store(p+1036, 0x120); m.store(p+1076, 0x18)
        m.store(p+1104, 64); m.store(p+1108, 4)
        data[offset:offset+2048] = m.get(p, 2048)
        if ident == 0x6b:
            struct.pack_into('<9I',data,ident*2048,ident,1,0,offset,offset+2048,0,1,0,0)
            struct.pack_into('<4I',data,ident*2048+64,offset,2048,0x51,0)
            struct.pack_into('<I',data,offset+0x10,0x780)
    d.files[1000] = bytes(data)
    d.mem.store(0x3ead20, 1000); d.mem.store(0x3ead24, len(data))
    d.mem.store(0x700000c4, 2); d.mem.store(0x3fa1b0, 1, 1)
    d.mem.store(0x3fa200, 0, 1); d.mem.store(0x3fa201, 0, 1)
    d.mem.store(0x3e01c0, 0x85)
    for at, value in ((0x3f4d44,0xc00000), (0x3f4d48,0xc91000),
                      (0x3f4d4c,0xd00000), (0x3f4d50,0xd00000),
                      (0x3f4d54,0xd00000), (0x3f4d5c,0xe00000)):
        d.mem.store(at, value)
    d.mem.store(TASK+0x30, 1, 2); d.mem.store(TASK+0x32, 3, 2)
    d.mem.store(TASK+0x44, 3)


class EnginePreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = configure(library(output=ROOT/'local/build/engine.so'))

    def test_connected_worker_prepares_three_directories_and_retains_resources(self):
        d = EngineOperationDriver(); engine_pack(d)
        d.mem.put(0xc00800, b'UNTOUCHED'); d.mem.put(0xe00000, b'RETAINED')
        self.lib.hg_resource_worker_fixed(C.byref(d.env), FRAME)
        self.assertFalse(d.errors)
        self.assertEqual(d.mem.load(0x3f9fef,1), 0x4c)
        self.assertEqual(d.mem.load(0x46e10c,1), 1)
        self.assertEqual(d.mem.load(TASK+0x44), 0x85)
        self.assertEqual(d.mem.load(TASK+0x30,2), 0)
        self.assertEqual(d.mem.load(0x3eacec,1), 0)
        self.assertEqual(d.mem.get(0xe00000,8), b'RETAINED')
        self.assertEqual(d.mem.get(0xc00800,9), b'UNTOUCHED')
        self.assertEqual(d.mem.load(0x3f4444),0xc917c0)
        self.assertEqual(d.mem.load(0x3f4d48),0xc91800)
        for index, base in ((1,0xc91800),(2,0xc00000)):
            self.assertEqual(d.mem.load(0x3f4300+(0x200+index)*4), base)
            self.assertEqual(d.mem.load(base+64), 0x40a00000)
        self.assertEqual([x[1] for x in d.calls if x[0]=='read'],
                         [1003, 1000+0x6b, 1000+0x90000//2048,
                          1000+0x4c, 1000+0x90800//2048,
                          1000+0x85, 1000+0x91000//2048])
        # Kind 8 runs retained preparation only: no reload, preserved ready byte.
        before = d.mem.get(0xc00000,2048); d.calls.clear()
        d.mem.store(TASK+0x30,1,2); d.mem.store(TASK+0x32,8,2)
        self.lib.hg_resource_worker_fixed(C.byref(d.env),FRAME)
        self.assertFalse(d.errors)
        self.assertFalse(any(x[0]=='read' for x in d.calls))
        self.assertEqual(d.mem.get(0xc00000,2048), before)

    def test_retained_gate_and_cache_hit(self):
        for context, override, cached, count in ((0,0,0,0),(1,1,0,2),(2,0,0x4c,0)):
            d=EngineDriver(); d.mem.store(0x700000c4,context)
            d.mem.store(0x3fa1ba,override,1);d.mem.store(0x3f9fef,cached,1)
            d.mem.put(TASK+0x38,b'PHASES!!')
            invoke(self.lib,d,'prepare_retained')
            self.assertEqual(len(d.calls),count)
            self.assertEqual(d.mem.load(0x46e10c,1),int(context==2 or override!=0))
            if count:self.assertTrue(all(x[3]==bytes(8) for x in d.calls))
            else:self.assertEqual(d.mem.get(TASK+0x38,8),b'PHASES!!')

    def test_special_selector_and_reload_after_first_loader(self):
        d=EngineDriver();d.mem.store(0x700000c4,2)
        d.mem.store(0x3fa1b1,3,1);d.mem.store(0x3fa1b2,3,1)
        d.change_selector=2
        invoke(self.lib,d,'prepare_retained')
        self.assertEqual([x[2] for x in d.calls],[0x4a,0x4d])
        self.assertEqual(d.mem.load(0x3f9fef,1),0x4d)

    def test_context_reset_and_dispatch_sentinels(self):
        for mode,sub,pc in ((0,255,0x12fde0),(2,255,0x12fde0),(2,7,0x12fcf0)):
            d=EngineDriver();d.mem.store(0x700000c4,2)
            d.mem.put(0x46e130,b'12345678');d.mem.store(0x46e12d,mode,1)
            d.mem.store(0x3fa202,sub,1);invoke(self.lib,d,'prepare_context')
            self.assertEqual(d.mem.get(0x46e130,8),bytes(8))
            self.assertEqual(d.calls,[('call',pc,TASK)])
            if pc==0x12fcf0:self.assertEqual(d.mem.load(TASK+0x44),0x207)
        d=EngineDriver();d.mem.put(0x46e130,b'12345678')
        invoke(self.lib,d,'prepare_context');self.assertEqual(d.mem.get(0x46e130,8),b'12345678')

    def test_context_table_paths_and_invalid_directory(self):
        for kind,sub,directory in ((0,2,0x85),(2,1,0xffffffff),(3,4,0x91),(255,0,0x42)):
            d=EngineDriver();d.mem.store(0x3fa200,kind,1);d.mem.store(0x3fa201,sub,1)
            at=0x3e01c0+kind*12+sub*4 if kind<3 else 0x3e01e4+kind*4
            d.mem.store(at,directory);invoke(self.lib,d,'load_context')
            self.assertEqual(d.mem.load(TASK+0x44),directory)
            self.assertEqual(len(d.calls),int(directory!=0xffffffff))
        d=EngineDriver();d.mem.store(0x3fa201,255,1);d.mem.store(TASK+0x44,123)
        invoke(self.lib,d,'load_context');self.assertEqual(d.mem.load(TASK+0x44),123)

    def test_index_ring_repeat_width_and_arithmetic_high_byte(self):
        for ident,slot,result in ((0x207,0,19),(0xffffff07,2,20),(0x307,0xffff,0x10013),(0x207,0,0xffffffff)):
            d=EngineDriver();d.mem.store(TASK+0x44,ident)
            high=(ident if ident<0x80000000 else ident-0x100000000)>>8
            d.mem.store((0x3e0200+(high-2)*4)&0xffffffff,0x500000)
            d.mem.store(0x500000+7*4,result);d.mem.store(0x46e136,slot,2)
            d.mem.put(0x500100,b'SENTINEL')
            invoke(self.lib,d,'load_indexed')
            self.assertEqual(d.mem.load(TASK+0x44),result)
            if result!=0xffffffff:
                expected=(slot+1)&0xffff
                if expected>=3:expected=0
                self.assertEqual(d.mem.load(0x46e136,2),expected)
                self.assertEqual(d.mem.load(0x46e130+expected*2,2),result&0xffff)
                # Only an exact u32/u16 comparison suppresses a repeated load.
                d.mem.store(TASK+0x44,ident);invoke(self.lib,d,'load_indexed')
                self.assertEqual(len(d.calls),1 if result<=0xffff else 2)
            else:self.assertFalse(d.calls)
            self.assertEqual(d.mem.get(0x500100,8),b'SENTINEL')


class EngineContractTests(unittest.TestCase):
    def test_packet_requires_every_function_and_disjoint_delay_slot(self):
        import copy
        import json
        import sys
        sys.path.insert(0,str(ROOT/'tools'))
        from verify_engine_preparation import validate_packet
        packet=json.loads((ROOT/'analysis/engine_preparation/functions.json').read_text())
        validate_packet(packet)
        missing=copy.deepcopy(packet);missing['functions'].pop()
        with self.assertRaisesRegex(ValueError,'function body'):validate_packet(missing)
        missing=copy.deepcopy(packet);missing['functions'][-1]['instructions'].pop()
        with self.assertRaisesRegex(ValueError,'body/ranges'):validate_packet(missing)

    def test_audio_contract_uses_offsets_sentinels_and_word_wrap(self):
        import sys
        sys.path.insert(0,str(ROOT/'tools'))
        from verify_engine_runtime import audio_resource_contract
        raw=bytearray(256)
        struct.pack_into('<I',raw,0,len(raw));struct.pack_into('<I',raw,12,1)
        struct.pack_into('<I',raw,16,0xc0);struct.pack_into('<I',raw,36,48)
        struct.pack_into('<I',raw,60,0x64685353)
        for i,value in enumerate((0xffffffff,0x80,0xffffffff,0xa0,0xb0,0xc0)):
            struct.pack_into('<I',raw,64+i*4,value)
        result,end=audio_resource_contract(bytes(raw),0xfffffff0)
        self.assertEqual(end,0xc0)
        self.assertEqual(struct.unpack_from('<6I',result,96),(0,0xa0,0,0xc0,0xd0,0xe0))
        self.assertEqual(result[:96],raw[:96]);self.assertEqual(result[120:],raw[120:])

    def test_audio_contract_rejects_unverified_shapes(self):
        import sys
        sys.path.insert(0,str(ROOT/'tools'))
        from verify_engine_runtime import audio_resource_contract
        for raw in (b'',bytes(32),struct.pack('<8I',32,0,0,2,0,0,0,0),
                    struct.pack('<8I',32,0,100,1,0,0,0,0)):
            with self.assertRaises(ValueError):audio_resource_contract(raw,0x500000)

    def test_task_address_wraps_in_guest_bus(self):
        import sys
        sys.path.insert(0,str(ROOT/'tools'))
        from verify_engine_preparation import WordBus
        lib=configure(library(output=ROOT/'local/build/engine_wrap.so'))
        d=EngineDriver(WordBus());d.mem.store(0x700000c4,2)
        invoke(lib,d,'prepare_retained',0xfffffff0)
        self.assertEqual([x[2] for x in d.calls],[0x6b,0x4c])
        self.assertTrue(all(x[3]==bytes(8) for x in d.calls))
        self.assertEqual(d.mem.load(0x34),0x4c)
