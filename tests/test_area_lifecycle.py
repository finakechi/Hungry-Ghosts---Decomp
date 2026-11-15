"""Synthetic general-PACK/AREA loading tests; no retail activation claim."""
import ctypes as C
import struct
import unittest
from resource_harness import Driver, ResourceEnv, ROOT, library, synthetic_pack

TASK=0x3f8e00
AREA=0xa87800
DEST=0xc00000
CALL6=C.CFUNCTYPE(C.c_uint32,C.c_void_p,*([C.c_uint32]*7))


def configure(lib):
    for name,arity,returns in (
        ('pack_general',1,False),('pack_general_prepare',1,False),
        ('pack_general_step',1,True),('resource_clear_dynamic_handles',0,False),
        ('resource_clear_selected_handles',0,False),('resource_worker_fixed',1,False),
        ('pack_alternative',1,False),('pack_alternative_step',1,True)):
        fn=getattr(lib,'hg_'+name)
        fn.argtypes=[C.POINTER(ResourceEnv)]+[C.c_uint32]*arity
        fn.restype=C.c_uint32 if returns else None
    lib.hg_area1_actor.argtypes=[C.POINTER(ResourceEnv),C.c_uint32,CALL6]
    lib.hg_area1_actor.restype=None
    lib.hg_actor_data_init.argtypes=[C.POINTER(ResourceEnv),C.c_uint32,C.c_uint32]
    lib.hg_actor_data_init.restype=None
    return lib


def invoke(lib,d,name,*args):
    result=getattr(lib,'hg_'+name)(C.byref(d.env),*args)
    if d.errors:raise AssertionError(d.errors)
    return result


def setup_area_pack(d,id=0x102,marker=0x35,fixup=False):
    pack=synthetic_pack(id)
    # Publish one texture-bearing synthetic FLK5 through the existing M4 path.
    struct.pack_into('<I',pack,id*2048+32,int(fixup))
    if fixup:
        base=(id+1)*2048
        struct.pack_into('<5I',pack,base,0x354b4c46,1,2048,0,0x09000020)
        struct.pack_into('<I',pack,base+32,0x60000000)
        struct.pack_into('<2I',pack,base+40,0x100,0x121)
        struct.pack_into('<I',pack,id*2048+72,0x96)
    d.files[1000]=bytes(pack)
    d.mem.store(0x3ead20,1000);d.mem.store(0x3ead24,len(pack))
    d.mem.store(0x3dff04,AREA);d.mem.store(0x3f4d50,DEST)
    # Selection uses a table base plus the second state byte, not the PACK ID.
    d.mem.store(0x3fa1b0,1,1);d.mem.store(0x3fa1b1,2,1)
    d.mem.store(0x3e01b4,3)
    d.mem.store(0x3f3a30+5*8,5000);d.mem.store(0x3f3a34+5*8,2049)
    data=bytearray([marker])*2049;data[:4]=b'MWo3'
    struct.pack_into('<I',data,0x14,37)
    d.files[5000]=bytes(data)
    return data


def setup_alternative_pair(d):
    setup_area_pack(d,id=2)
    d.mem.store(0x3fa1b0,0,1);d.mem.store(0x3fa1b1,0,1)
    d.mem.store(0x3fa1b2,0,1);d.mem.store(0x3e01b0,5)
    d.mem.store(0x3f4d4c,DEST)
    pack=bytearray(0x4000)
    for id,base,member in ((2,0x2000,0x96),(3,0x3000,0x97)):
        header=id*2048
        struct.pack_into('<9I',pack,header,id,2,2,base,base,4096,0,0,0)
        struct.pack_into('<4I',pack,header+64,base,2048,member,0)
        struct.pack_into('<4I',pack,header+80,base+2048,2048,member+8,0)
        pack[base:base+4]=bytes([id])*4
    d.files[1000]=bytes(pack);d.mem.store(0x3ead24,len(pack))


class ActorDriver(Driver):
    ARITIES={0x1235c0:2,0x123660:2,0x144340:3,0x139c90:2,0x18c280:1,0x142e90:1}
    def __init__(self):
        super().__init__();self.setup=CALL6(self._setup)
    def _setup(self,_,pc,a,b,c,d,e,f):
        self.calls.append(('call6',pc,a,b,c,d,e,f));return 0
    def external(self,pc,a=0,b=0,c=0,d=0):
        if pc in self.ARITIES:
            self.calls.append(('call',pc,*(a,b,c,d)[:self.ARITIES[pc]]))
            return 0
        return super().external(pc,a,b,c,d)


class GeneralPackAreaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib=configure(library(output=ROOT/'local/build/area_loader.so'))

    def test_request_worker_area_pack_and_real_flk5_fixup(self):
        d=Driver();area=setup_area_pack(d,fixup=True)
        d.mem.put(AREA+2049,b'X'*37)
        d.invoke(self.lib,'request_raw',1,0x102)
        invoke(self.lib,d,'resource_worker_fixed',0x1ff05c0)
        self.assertEqual(d.mem.get(AREA,2049),bytes(area))
        self.assertEqual(d.mem.get(AREA+2049,37),bytes(37))
        self.assertEqual(d.mem.load(DEST+40),0x1e00)
        self.assertEqual(d.mem.load(DEST+44),0x1e21)
        self.assertEqual(d.mem.load(DEST+8),0x1d00)
        self.assertEqual(d.mem.load(0x3f4558),DEST)
        self.assertEqual((d.mem.load(TASK+0x30,2),d.mem.load(0x3eacec,1)),(0,0))
        self.assertEqual((d.mem.load(TASK+0x39,1),d.mem.load(TASK+0x3a,1)),(7,0))
        self.assertEqual([x[1:4] for x in d.calls if x[0]=='read'],
                         [(5000,2,AREA),(1258,1,0x3f3b00),(1259,2,DEST)])
        self.assertEqual(d.mem.load(0x3f4d50),DEST)
        self.assertEqual(d.mem.load(0x3f4d54),DEST+4096)
        self.assertEqual(d.mem.load(0x3f4d5c),DEST+4096+0x50000)

    def test_repeated_request_reuses_destinations_and_replaces_handles(self):
        d=Driver();setup_area_pack(d,marker=0x21)
        d.mem.put(DEST+0x2000,b'RETAINED')
        d.mem.store(0x3f4300+0x28e*4,0x11111111)
        for id,marker in ((0x102,0x21),(0x103,0x42)):
            setup_area_pack(d,id,marker)
            d.invoke(self.lib,'request_raw',1,id)
            invoke(self.lib,d,'resource_worker_fixed',0x1ff05c0)
            self.assertEqual(d.mem.load(0x3f4310),DEST)
            self.assertEqual(d.mem.load(0x3f4d50),DEST)
            self.assertEqual(d.mem.get(AREA+24,8),bytes([marker])*8)
            self.assertEqual(d.mem.load(0x3f4300+0x28e*4),0)
        self.assertEqual(d.mem.get(DEST+0x2000,8),b'RETAINED')
        self.assertEqual(len([x for x in d.calls if x[0]=='read']),6)

    def test_clear_helpers_include_delay_slot_tail_and_preserve_neighbors(self):
        d=Driver();d.mem.put(0x3f4300+0x95*4,b'X'*(0x28f-0x95+1)*4)
        invoke(self.lib,d,'resource_clear_dynamic_handles')
        self.assertEqual(d.mem.load(0x3f4300+0x95*4),0x58585858)
        self.assertEqual(d.mem.get(0x3f4558,(0x28e-0x96+1)*4),bytes((0x28e-0x96+1)*4))
        self.assertEqual(d.mem.load(0x3f4300+0x28f*4),0x58585858)
        slots=(0x3f45f8,0x3f4c84,0x3f4c88,0x3f4c8c,0x3f460c)
        for slot in slots:d.mem.store(slot,0xabcdef00)
        d.mem.store(0x3f45fc,0x10203040)
        invoke(self.lib,d,'resource_clear_selected_handles')
        self.assertEqual([d.mem.load(slot) for slot in slots],[0]*5)
        self.assertEqual(d.mem.load(0x3f45fc),0x10203040)

    def test_phase_zero_falls_through_to_area_setup_and_completion_yields(self):
        d=Driver();setup_area_pack(d);d.mem.store(TASK+0x44,0x102)
        invoke(self.lib,d,'pack_general_prepare',TASK)
        self.assertEqual(invoke(self.lib,d,'pack_general_step',TASK),0)
        self.assertEqual((d.mem.load(TASK+0x39,1),d.mem.load(TASK+0x3a,1)),(1,1))
        self.assertEqual(d.mem.load(0x3eacf8),0x3f3a58)
        while d.mem.load(TASK+0x39,1)==1:
            self.assertEqual(invoke(self.lib,d,'pack_general_step',TASK),0)
        self.assertEqual((d.mem.load(TASK+0x39,1),d.mem.load(TASK+0x3a,1)),(2,0))
        self.assertEqual(d.mem.load(0x3eacf8),0x3f3a58)
        invoke(self.lib,d,'pack_general_step',TASK)
        self.assertEqual(d.mem.load(0x3eacf8),0x3ead20)

    def test_retry_and_busy_reads_keep_the_connected_operation_live(self):
        d=Driver();setup_area_pack(d);d.sync_busy=3;d.read_failures=2
        d.invoke(self.lib,'request_raw',1,0x102)
        invoke(self.lib,d,'resource_worker_fixed',0x1ff05c0)
        self.assertEqual(d.mem.load(TASK+0x30,2),0)
        self.assertEqual(len([x for x in d.calls if x[0]=='read']),5)
        self.assertEqual(d.mem.get(DEST,4),b'TEST')

    def test_empty_pack_completes_after_area_and_preserves_arena_end(self):
        d=Driver();setup_area_pack(d)
        pack=bytearray(d.files[1000]);struct.pack_into('<I',pack,0x102*2048+4,0)
        d.files[1000]=bytes(pack);d.mem.store(0x3f4d54,0x12345678)
        d.invoke(self.lib,'request_raw',1,0x102)
        invoke(self.lib,d,'resource_worker_fixed',0x1ff05c0)
        self.assertEqual(d.mem.load(0x3f4d54),0x12345678)
        self.assertEqual(len([x for x in d.calls if x[0]=='read']),2)

    def test_invalid_phase_and_variant_preserve_original_nonprogress_behavior(self):
        d=Driver();d.mem.store(TASK+0x39,255,1)
        self.assertEqual(invoke(self.lib,d,'pack_general_step',TASK),0)
        self.assertFalse(d.calls)
        d.mem.store(TASK+0x44,0x13);d.mem.store(TASK+0x3e,0x1234,2)
        d.mem.store(0x46e136,3,2);d.mem.store(0x3f4d44,0xffffff00)
        invoke(self.lib,d,'pack_general_prepare',TASK)
        self.assertEqual(d.mem.load(TASK+0x3e,2),0x1234)
        self.assertEqual(d.mem.load(TASK+0x48),(TASK+0xffffff00)&0xffffffff)

    def test_mode_eight_rounding_and_wrap_preserve_other_arena_pointers(self):
        for dst,size in ((DEST,0),(DEST,1),(DEST,2047),(DEST,2048),(DEST,2049),(0xfffffff0,32)):
            d=Driver();d.mem.store(TASK+0x39,7,1);d.mem.store(TASK+0x3e,8,2)
            d.mem.store(TASK+0x48,dst);d.mem.store(0x3f3b14,size)
            d.mem.store(0x3f4d50,0x12345678)
            self.assertEqual(invoke(self.lib,d,'pack_general_step',TASK),1)
            end=(dst+size+2047)&0xfffff800
            self.assertEqual(d.mem.load(0x3f4d54),end)
            self.assertEqual(d.mem.load(0x3f4d5c),(end+0x50000)&0xffffffff)
            self.assertEqual(d.mem.load(0x3f4d50),0x12345678)

    def test_arena_variant_initialization_and_non_area_phase_skip(self):
        for context,old_tail in ((0,0),(1,0),(1,0xdead00),(2,0xdead00),(3,0)):
            d=Driver();d.mem.store(TASK+0x44,7);d.mem.store(0x3f4d40,DEST)
            d.mem.store(0x700000c4,context);d.mem.store(0x3f4d5c,old_tail)
            d.mem.store(0x3f4d54,0x11223344)
            invoke(self.lib,d,'pack_general_prepare',TASK)
            self.assertEqual(d.mem.load(TASK+0x3e,2),9)
            updated=context==2 or (context==1 and old_tail==0)
            self.assertEqual(d.mem.load(0x3f4d5c),DEST+0x748000 if updated else old_tail)
            self.assertEqual(d.mem.load(0x3f4d54),DEST+0x6f8000 if updated else 0x11223344)
            invoke(self.lib,d,'pack_general_step',TASK)
            self.assertEqual(d.mem.load(TASK+0x39,1),2)
            self.assertFalse(d.calls)

    def test_kind_two_worker_loads_area_then_appends_kind_three_resources(self):
        d=Driver();setup_alternative_pair(d)
        d.invoke(self.lib,'request_raw',2,2)
        invoke(self.lib,d,'resource_worker_fixed',0x1ff05c0)
        self.assertEqual(d.mem.load(TASK+0x32,2),3)
        self.assertEqual(d.mem.load(TASK+0x44),3)
        self.assertEqual(d.mem.load(0x3eaf88),2)
        self.assertEqual(d.mem.load(0x3f4300+0x96*4),DEST)
        self.assertEqual(d.mem.load(0x3f4300+0x97*4),DEST+4096)
        self.assertEqual(d.mem.get(DEST,4),b'\x02'*4)
        self.assertEqual(d.mem.get(DEST+4096,4),b'\x03'*4)
        self.assertEqual([x[1:4] for x in d.calls if x[0]=='read'],
                         [(5000,2,AREA),(1002,1,0x3f3b00),(1004,2,DEST),
                          (1003,1,0x3f3b00),(1006,2,DEST+4096)])
        self.assertEqual(d.mem.load(0x3f4d50),DEST+4096)
        self.assertEqual(d.mem.load(0x3f4d54),DEST+8192)
        self.assertEqual(d.mem.load(0x3f4d5c),DEST+8192+0x50000)
        self.assertEqual(d.mem.load(0x3eace0,1),1)
        self.assertEqual((d.mem.load(TASK+0x30,2),d.mem.load(0x3eacec,1)),(0,0))
        self.assertEqual(d.calls[-3:],[('call',0x130020,TASK),('call',0x12fe90,TASK),('call',0x103da0)])

    def test_alternative_retains_raw_end_and_special_state_reservations(self):
        for kind in (2,3):
            for state in ((0,0,0),(0,2,1),(1,2,1)):
                d=Driver();d.mem.store(TASK+0x39,7,1);d.mem.store(TASK+0x32,kind,2)
                d.mem.store(TASK+0x48,0xfffffff0);d.mem.store(0x3f3b14,33)
                d.mem.store(0x3f4d58,0x12345678)
                for i,value in enumerate(state):d.mem.store(0x3fa1b0+i,value,1)
                self.assertEqual(invoke(self.lib,d,'pack_alternative_step',TASK),1)
                if kind==2:
                    self.assertEqual(d.mem.load(0x3f4d50),17)
                    self.assertEqual(d.mem.load(0x3f4d58),0x12345678)
                else:
                    self.assertEqual(d.mem.load(0x3f4d54),17)
                    self.assertEqual(d.mem.load(0x3f4d5c),17+(0x250000 if state==(0,2,1) else 0x50000))
                    self.assertEqual(d.mem.load(0x3f4d58),17+0x50000 if state==(0,2,1) else 0x12345678)

    def test_empty_alternative_flag_and_arena_behavior_depends_on_kind(self):
        for kind in (2,3,0xffff):
            d=Driver();d.mem.store(TASK+0x39,4,1);d.mem.store(TASK+0x32,kind,2)
            d.mem.store(TASK+0x48,DEST+1);d.mem.store(0x3f4d50,0x77777777)
            self.assertEqual(invoke(self.lib,d,'pack_alternative_step',TASK),1)
            self.assertEqual(d.mem.load(0x3eace0,1),0 if kind==2 else 1)
            self.assertEqual(d.mem.load(0x3f4d50),DEST+1 if kind==2 else 0x77777777)
            if kind!=2:
                self.assertEqual(d.mem.load(0x3f4d54),DEST+1)
                self.assertEqual(d.mem.load(0x3f4d5c),DEST+1+0x50000)

    def test_alternative_phase_zero_selects_area_only_for_kind_two(self):
        for kind in (2,3,0xffff):
            d=Driver();setup_area_pack(d);d.mem.store(TASK+0x32,kind,2)
            self.assertEqual(invoke(self.lib,d,'pack_alternative_step',TASK),0)
            self.assertEqual(d.mem.load(TASK+0x39,1),1 if kind==2 else 2)
            self.assertEqual(d.mem.load(TASK+0x3a,1),1 if kind==2 else 0)
        d=Driver();d.mem.store(TASK+0x39,255,1)
        self.assertEqual(invoke(self.lib,d,'pack_alternative_step',TASK),0)
        self.assertFalse(d.calls)

    def test_loaded_area_actor_initialization_and_retained_resource(self):
        d=ActorDriver();setup_alternative_pair(d)
        d.invoke(self.lib,'request_raw',2,2)
        invoke(self.lib,d,'resource_worker_fixed',0x1ff05c0)
        area=d.mem.get(AREA,2049+37)
        d.calls=[]
        actor=0x5f0000;data=actor+0x2d0
        d.mem.put(actor,bytes([0xa5])*0x400);d.mem.store(actor+0x18,0,1)
        self.lib.hg_area1_actor(C.byref(d.env),actor,d.setup)
        self.assertFalse(d.errors)
        self.assertEqual(d.mem.get(AREA,2049+37),area)
        self.assertEqual((d.mem.load(actor,1),d.mem.load(actor+0x18,1)),(0xa5,1))
        self.assertEqual(d.mem.load(data),0x24)
        self.assertEqual(d.mem.load(actor+0x2a,2),0x233)
        self.assertEqual(d.mem.load(actor+4),8)
        self.assertEqual(d.mem.load(actor+0x2bc),data)
        self.assertEqual(d.mem.load(actor+0xa8),data+0x30)
        self.assertEqual(d.mem.load(actor+0xdc),0x3f800000)
        self.assertEqual([d.mem.load(data+x) for x in (4,0x1c,0x20)],[0x3f800000]*3)
        self.assertEqual([d.mem.load(data+x) for x in (0x30,0x34,0x38,0x3c)],[0,0,0xbecccccd,0])
        self.assertEqual([d.mem.load(0x70000b90+i*4) for i in range(4)],
                         [0x41c00000,0x41c00000,0x41c00000,0x3f800000])
        self.assertEqual(d.calls,[('call',0x119ac8,data,0,0x30),
            ('call6',0x18a330,actor,0x1000000,0x9000001,0,0,0),
            ('call',0x144340,actor+0x50,actor+0x30,actor+0x40),
            ('call',0x119ac8,data+0x30,0,0x10),('call',0x1235c0,actor+0x90,5),
            ('call',0x123660,actor+0x90,1),('call',0x139c90,actor,0x70000b90)])

    def test_actor_entry_other_states_keep_external_boundaries_explicit(self):
        actor=0x5f0000
        for state in (1,2,3,4,128,255):
            d=ActorDriver();d.mem.store(actor+0x18,state,1)
            d.mem.store(actor+0x2bc,0x33445566);d.mem.put(0x3eb058,b'X'*12)
            self.lib.hg_area1_actor(C.byref(d.env),actor,d.setup)
            self.assertFalse(d.errors)
            if state==1:
                self.assertEqual([d.mem.load(0x3eb058+i*4) for i in range(3)],
                                 [actor,0x33445566,0])
                self.assertEqual(d.calls,[('call',0x119ac8,0x3eb058,0,12),('call',0x18c280,1)])
            elif state==2:
                self.assertEqual(d.mem.load(actor+0x18,1),3);self.assertFalse(d.calls)
            elif state==3:self.assertEqual(d.calls,[('call',0x142e90,actor)])
            else:self.assertFalse(d.calls)

    def test_later_non_area_request_retains_loaded_area_and_reuses_actor_callback(self):
        d=ActorDriver();setup_alternative_pair(d)
        d.invoke(self.lib,'request_raw',2,2)
        invoke(self.lib,d,'resource_worker_fixed',0x1ff05c0)
        actor=0x5f0000
        d.mem.store(actor+0x18,1,1);d.mem.store(actor+0x2bc,actor+0x2d0)
        self.lib.hg_area1_actor(C.byref(d.env),actor,d.setup)
        area=d.mem.get(AREA,2086);arenas=d.mem.get(0x3f4d3c,36)
        publication=d.mem.get(0x3eb058,12)
        pack=synthetic_pack(0x69)
        struct.pack_into('<I',pack,0x69*2048+32,0)
        d.files[1000]=bytes(pack);d.mem.store(0x3ead24,len(pack))
        d.calls=[]
        d.invoke(self.lib,'request_raw',1,0x69)
        invoke(self.lib,d,'resource_worker_fixed',0x1ff05c0)
        self.assertEqual(d.mem.get(AREA,2086),area)
        self.assertEqual(d.mem.get(0x3f4d3c,36),arenas)
        self.assertEqual(d.mem.get(0x3eb058,12),publication)
        reads=[x[1:4] for x in d.calls if x[0]=='read']
        self.assertEqual(reads,[(1105,1,0x3f3b00),(1106,2,DEST+8192+0x50000)])
        self.assertFalse(any(x[:2]==('call',0x1041e0) for x in d.calls))
        d.mem.put(0x3eb058,b'X'*12)
        self.lib.hg_area1_actor(C.byref(d.env),actor,d.setup)
        self.assertFalse(d.errors)
        self.assertEqual(d.mem.get(0x3eb058,12),publication)
        self.assertEqual(d.mem.get(AREA,2086),area)

    def test_actor_initializer_reloads_state_after_external_callback(self):
        d=ActorDriver();actor=0x5f0000;original=d.external
        def callback(pc,*args):
            result=original(pc,*args)
            if pc==0x139c90:d.mem.store(actor+0x18,255,1)
            return result
        d.external=callback
        self.lib.hg_area1_actor(C.byref(d.env),actor,d.setup)
        self.assertFalse(d.errors)
        self.assertEqual(d.mem.load(actor+0x18,1),0)


if __name__=='__main__':unittest.main()
