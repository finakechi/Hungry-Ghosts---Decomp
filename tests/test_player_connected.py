"""Synthetic player wiring checks; retail observations are compared separately."""
import ctypes as C
import unittest
from resource_harness import Driver, Memory, ResourceEnv, ROOT, library

FUNCTIONS={0x158a10:('hg_player_initialize',1),0x139fe0:('hg_actor_set_animation_flag',2),
    0x158ce0:('hg_player_unlink_effects',2),0x15e190:('hg_player_select_animation',3),
    0x13a2a0:('hg_actor_copy_node_matrix',3),0x159570:('hg_player_active_update',2),
    0x158d90:('hg_player_ordinary_transition',1)}
# FP seam operands precede the original integer arguments. 120830 returns raw f0.
ARITIES={0x138370:2,0x139d20:3,0x139fc0:2,0x1235c0:2,0x267300:2,0x139ff0:3,
    0x1b7f00:1,0x196ad0:2,0x144340:3,0x13a240:3,0x267450:1,0x13a2a0:3,
    0x139fe0:2,0x159380:1,0x13a060:4,0x15d770:1,0x119910:3,0x119ac8:3,
    0x1594f0:0,0x159ad0:1,0x32b3c0:1,0x175190:1,0x176fb0:1,0x1bcd00:1,
    0x1e5060:1,0x15a0d0:1,0x17fad0:1,0x15de80:1,0x15c410:3,0x15c540:4,
    0x12c030:4,0x120830:2,0x188550:2,0x188650:1,0x15bf30:1,0x17d930:3,
    0x15fe00:2,0x15c160:1,0x174900:2,0x15e190:3,0x158d30:1}
RETURNS={0x1b7f00:0x600100,0x15de80:0x600104,0x120830:0x600108,0x15fe00:0x60010c}
DISPATCHES={0x159ad0,0x32b3c0,0x175190,0x176fb0,0x1bcd00,0x1e5060,0x15a0d0}


def configure(lib):
    for entry,(name,n) in FUNCTIONS.items():
        f=getattr(lib,name);f.argtypes=[C.POINTER(ResourceEnv)]+[C.c_uint32]*n
        f.restype=C.c_uint32 if entry==0x15e190 else None
    lib.hg_player_equipment_enter.argtypes=[C.POINTER(ResourceEnv),C.c_uint32]
    lib.hg_player_equipment_enter.restype=C.c_uint32
    return lib


class ConnectedDriver(Driver):
    def __init__(self,mem=None,mutation=None):
        super().__init__(mem);self.mutation=mutation;self.input_at_dispatch=[]
    def external(self,pc,a=0,b=0,c=0,d=0):
        self.calls.append((pc,*(a,b,c,d)[:ARITIES[pc]]))
        if pc==0x119910:
            self.mem.put(a,self.mem.get(b,c));return a
        if pc==0x119ac8:
            self.mem.put(a,bytes([b&255])*c);return a
        if pc in DISPATCHES:
            self.input_at_dispatch.append(self.mem.get(0x3eaf00,0x3c))
            if self.mutation is not None:
                offset,value,width=self.mutation;self.mem.store(a+offset,value,width)
        if pc in RETURNS:return self.mem.load(RETURNS[pc])
        if pc==0x12c030:return self.mem.load(0x600110+(b==0x3f9950)*4)
        if pc==0x138370:return 0x610000+(b&0xff)*16
        if pc==0x196ad0:return 0x620000+a*16
        return 0


class IntegratedDriver(Driver):
    """Synthetic math/engine leaves for exercising the real C composition.

    These leaves construct deterministic test state from inputs; no retail
    output bytes or expected snapshots are used to calculate any result.
    """
    def external(self,pc,a=0,b=0,c=0,d=0):
        arities={0x1330f0:2,0x139ff0:3,0x1b7f00:1,0x196ad0:2,
            0x144340:3,0x13a240:3,0x267450:1,0x13a060:4,0x15d770:1,
            0x13a050:2,0x15fe00:2,0x161110:2,0x188650:1,0x15bf30:1,
            0x120830:2,0x188550:2,0x17d930:3,0x120490:2,0x174900:2,
            0x119ac8:3,0x119910:3}
        if pc not in arities:raise AssertionError(f'Unexpected unresolved dependency {pc:#x}')
        self.calls.append((pc,*(a,b,c,d)[:arities[pc]]))
        if pc==0x119ac8:self.mem.put(a,bytes([b&255])*c);return a
        if pc==0x119910:self.mem.put(a,self.mem.get(b,c));return a
        if pc==0x1330f0:self.mem.store(a+0x18,b,1)
        elif pc==0x139ff0:self.mem.store(a+0x26,c,2)
        elif pc==0x13a060:self.mem.store(c+0x26,d,2)
        elif pc==0x144340:
            for i in range(16):self.mem.store(a+i*4,0x3f800000 if i%5==0 else 0)
            self.mem.put(a+0x30,self.mem.get(b,16))
        elif pc==0x13a240:
            for i in range(self.mem.load(a+0x54)):
                node=self.mem.load(self.mem.load(a+0x58)+i*4)
                self.mem.put(node,self.mem.get(b,64))
        return 0


def integrated_fixture():
    d=IntegratedDriver();a=0x3f92d0;block=0x600000;model=block+0x1000
    d.mem.store(0x3f43fc,block)
    d.mem.store(block,0x354b4c46);d.mem.store(block+4,214)
    d.mem.store(block+16+211*4,0x01001000);d.mem.store(block+16+213*4,0x09002000)
    d.mem.store(model+8,3);d.mem.store(model+0x34,0x3000)
    d.mem.store(0x4d8140,0x70000c);d.mem.store(0x4d8144,3)
    for i in range(3):d.mem.store(0x700000+i*4,0x710200-i*0x100)
    d.mem.store(0x37b9a0+8,0x10203040)
    d.mem.put(a+0x30,bytes.fromhex('0000803f00000040000040400000803f'))
    d.mem.store(0x3fa202,255,1);d.mem.store(0x3eaec8,32,2);d.mem.store(0x3eaed0,128,2)
    d.mem.store(0x539430,0x720000);d.mem.store(0x53943c,1)
    return d


class ConnectedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.lib=configure(library(output=ROOT/'local/build/player_connected.so'))
    def run_active(self,d,a=0x500000):
        self.lib.hg_player_active_update(C.byref(d.env),a,0x1ff0790)
        self.assertFalse(d.errors,d.errors)
    def test_active_dispatch_uses_mode_and_context(self):
        for mode,context,pc in [(0,0,0x159ad0),(1,0,0x175190),(1,2,0x175190),
                (1,6,0x32b3c0),(1,4,0x176fb0),(2,0,0x1bcd00),(3,0,0x1e5060),(4,0,0x15a0d0)]:
            with self.subTest(mode=mode,context=context):
                d=ConnectedDriver();a=0x500000;d.mem.store(a+0x448,mode,1);d.mem.store(0x3fa200,context,1)
                self.run_active(d);self.assertEqual([c[0] for c in d.calls if c[0] in DISPATCHES],[pc])
                self.assertEqual(d.mem.load(0x95e57c),int(mode in (1,2)))
    def test_input_mask_is_restored_from_guest_frame(self):
        d=ConnectedDriver();a=0x500000;payload=bytes(range(60));d.mem.put(0x3eaf00,payload)
        d.mem.store(a+0x45a,2,2);d.mem.store(a+0x464,1,1)
        self.run_active(d)
        self.assertEqual(d.input_at_dispatch,[bytes(60)])
        self.assertEqual(d.mem.get(0x3eaf00,60),payload)
        self.assertEqual(d.mem.get(0x1ff07c0,60),payload)
        self.assertEqual(d.mem.load(a+0x45a,2),1);self.assertEqual(d.mem.load(a+0x464,1),0)
    def test_event_shortcut_skips_dispatch_and_followup(self):
        d=ConnectedDriver();a=0x500000;d.mem.store(0x3f9e3a,1,1);d.mem.store(a+0x2c3,1,1)
        d.mem.store(a+0x4d8,1,2);d.mem.store(0x3eaed4,7,1)
        self.run_active(d)
        self.assertEqual([c[0] for c in d.calls],[0x1b7f00,0x15d770])
        self.assertEqual(d.mem.load(a),2);self.assertEqual(d.mem.load(a+0x18,1),2)
        self.assertEqual(d.mem.load(a+0x4d8,2),1);self.assertEqual(d.mem.load(0x3eaed4,1),0)
    def test_timer_expiry_and_status_two_exemption(self):
        for status in (2,7):
            d=ConnectedDriver();a=0x500000;d.mem.store(a,status,1)
            for off in (0x4d8,0x4f0,0x416):d.mem.store(a+off,1,2)
            self.run_active(d)
            self.assertEqual(d.mem.load(a,1),status&~4 if status==2 else status&~6)
            for off in (0x4d8,0x4f0,0x416):self.assertEqual(d.mem.load(a+off,2),0)
    def test_animation_uses_signed_current_and_preserves_raw_blend(self):
        for current,sequence,called in [(0x75,0x06000075,False),(0x8000,0x06008000,True),(0x74,0x06000075,True)]:
            d=ConnectedDriver();a=0x500000;d.mem.store(a+0xb6,current,2)
            result=self.lib.hg_player_select_animation(C.byref(d.env),0x3eaaaaab,a,sequence)
            self.assertEqual(result,called);self.assertEqual(d.calls,[(0x13a060,0,0x3eaaaaab,a+0x90,sequence)] if called else [])
    def test_unlink_marks_referenced_effects_and_retains_flag(self):
        d=ConnectedDriver();a=0x500000
        d.mem.store(a+0x450,0x510000);d.mem.store(a+0x454,0x520000);d.mem.store(a+0x46d,9,1)
        d.mem.store(0x520019,8,1)
        self.lib.hg_player_unlink_effects(C.byref(d.env),a,1)
        self.assertEqual(d.mem.load(0x510018,1),3);self.assertEqual(d.mem.load(0x520018,1),3)
        self.assertEqual(d.mem.load(0x520019,1),0);self.assertEqual(d.mem.load(a+0x450),0)
        self.assertEqual(d.mem.load(a+0x454),0);self.assertEqual(d.mem.load(a+0x46d,1),9)
    def test_selected_equipment_split_rejects_other_entry_states(self):
        for state in (1,2,9,10,11,255):
            d=ConnectedDriver();a=0x500000;d.mem.store(a+0x19,state,1)
            before=d.mem.get(a,0x540);result=self.lib.hg_player_equipment_enter(C.byref(d.env),a)
            self.assertEqual(result,0);self.assertEqual(d.calls,[]);self.assertEqual(d.mem.get(a,0x540),before)
    def test_selected_equipment_split_uses_eligibility_control(self):
        for blocked in (0,1):
            d=ConnectedDriver();a=0x500000;d.mem.store(0x60010c,blocked)
            self.assertEqual(self.lib.hg_player_equipment_enter(C.byref(d.env),a),1)
            self.assertFalse(d.errors,d.errors);self.assertEqual(d.mem.load(a+0x19,1),11 if blocked else 1)
            animations=[c for c in d.calls if c[0]==0x15e190]
            self.assertEqual(animations,[] if blocked else [(0x15e190,0,a,0x0600007f),(0x15e190,0x41200000,a,0x06000075)])

    def test_integrated_setup_square_and_following_update_with_control(self):
        initialize=self.lib.hg_player_initialize_fixed
        initialize.argtypes=[C.POINTER(ResourceEnv),C.c_uint32];initialize.restype=None
        update=self.lib.hg_player_active_update_fixed
        update.argtypes=[C.POINTER(ResourceEnv),C.c_uint32,C.c_uint32];update.restype=None
        for edge in (0,128):
            with self.subTest(edge=edge):
                d=integrated_fixture();a=0x3f92d0;frame=0x1ff0790
                initialize(C.byref(d.env),a)
                self.assertFalse(d.errors,d.errors)
                self.assertEqual(d.mem.load(a+0xa0),0x601000)
                self.assertEqual(d.mem.load(a+0xe4),3);self.assertEqual(d.mem.load(0x4d8144),0)
                self.assertEqual(d.mem.load(a+0x449,1),255)
                # State0 initializes ordinary state1; the next update establishes
                # ordinary substate1 before testing the actual input edge.
                update(C.byref(d.env),a,frame);update(C.byref(d.env),a,frame)
                self.assertEqual(d.mem.load(a+0x19,1),1);self.assertEqual(d.mem.load(a+0x1a,1),1)
                d.calls.clear();d.mem.store(0x3eaf04,edge,2)
                update(C.byref(d.env),a,frame)
                self.assertFalse(d.errors,d.errors)
                self.assertEqual(d.mem.load(a+0x448,1),int(bool(edge)))
                self.assertEqual(d.mem.load(a+0x44a,1),int(bool(edge)))
                self.assertEqual(d.mem.load(a+0x449,1),255)
                self.assertEqual(d.mem.load(a+0x19,1),0 if edge else 1)
                self.assertEqual([c[0] for c in d.calls],
                    [0x1b7f00,0x15d770,0x13a050,0x15fe00,0x161110,0x188650,0x15bf30,0x120830])
                d.calls.clear();d.mem.store(0x3eaf04,0,2)
                update(C.byref(d.env),a,frame)
                self.assertFalse(d.errors,d.errors)
                self.assertEqual(d.mem.load(a+0x19,1),1)
                if edge:
                    child=d.mem.load(a+0x450)
                    self.assertEqual(child,0x720000);self.assertEqual(d.mem.load(0x539430),0)
                    self.assertEqual(d.mem.load(0x53943c),0);self.assertEqual(d.mem.load(0x539450),child)
                    self.assertEqual(d.mem.load(child+0x1c),0x16b0f0)
                    self.assertEqual(d.mem.load(child+2,1),1);self.assertEqual(d.mem.load(child,1),2)
                    self.assertEqual(d.mem.load(child+4),0);self.assertEqual(d.mem.load(child+8),0)
                    self.assertEqual(d.mem.load(child+0x3c),0x3f800000)
                    self.assertEqual(d.mem.load(a+0xb6,2),0x75)
                    self.assertEqual([c[0] for c in d.calls],
                        [0x1b7f00,0x15d770,0x188550,0x188650,0x15bf30,0x17d930,0x15fe00,
                         0x119ac8,0x120490,0x174900,0x13a060,0x13a060,0x120830])
                    self.assertIn((0x120490,child,a),d.calls)
                else:
                    self.assertEqual(d.mem.load(a+0x450),0);self.assertEqual(d.mem.load(0x539430),0x720000)
                    self.assertEqual(d.mem.load(0x53943c),1)
                    self.assertNotIn(0x188550,[c[0] for c in d.calls])


if __name__=='__main__':unittest.main()
