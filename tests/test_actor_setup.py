"""M8 setup orchestration; dependency behavior here is explicitly synthetic."""
import ctypes as C
import unittest
import copy
import json
import sys
from resource_harness import Driver, ResourceEnv, ROOT, library
sys.path.insert(0,str(ROOT/'tools'))

FUNCTIONS = {
    0x18a330: ('hg_actor_setup', 6),
    0x18a4c0: ('hg_actor_setup_resources', 4),
    0x18a5b0: ('hg_actor_setup_animation', 3),
    0x139fc0: ('hg_actor_set_mode', 2),
    0x1235c0: ('hg_actor_set_palette', 2),
    0x18c280: ('hg_actor_active', 1),
    0x15de80: ('hg_player_input_transitions', 1),
    0x138370: ('hg_actor_resource_lookup', 2),
    0x141e40: ('hg_actor_allocate_nodes', 2),
    0x141de0: ('hg_actor_take_node', 0),
    0x139d20: ('hg_actor_bind_resources', 3),
    0x13a0a0: ('hg_actor_default_pose', 1),
    0x267300: ('hg_actor_bind_attachment', 2),
}
ARITIES = {0x138370:2, 0x139d20:3, 0x1235c0:2, 0x267300:2,
           0x139fc0:2, 0x139ff0:3, 0x13a0a0:1}
ARITIES.update({0x13a240:3,0x267450:1,0x18c9c0:2,0x120110:1,
                0x123660:2,0x1235f0:3,0x18b6c0:2,0x18c120:2,
                0x18c1c0:2,0x119910:3,0x15e0c0:1})
ARITIES.update({0x138b70:1,0x138af0:2,0x138b30:2,0x141de0:0})
ARITIES.update({0x141e40:2,0x120490:2,0x1330f0:2,0x143e60:2,
                0x266850:0,0x266a20:1,0x266c40:1})


def configure(lib):
    for name, arity in FUNCTIONS.values():
        f=getattr(lib,name)
        f.argtypes=[C.POINTER(ResourceEnv)]+[C.c_uint32]*arity
        f.restype=C.c_uint32 if name in ('hg_player_input_transitions','hg_actor_resource_lookup','hg_actor_allocate_nodes','hg_actor_take_node') else None
    lib.hg_area1_actor_fixed.argtypes=[C.POINTER(ResourceEnv),C.c_uint32]
    lib.hg_area1_actor_fixed.restype=None
    return lib


class SetupDriver(Driver):
    def external(self,pc,a=0,b=0,c=0,d=0):
        if pc not in ARITIES:return super().external(pc,a,b,c,d)
        self.calls.append(('call',pc,*(a,b,c,d)[:ARITIES[pc]]))
        if pc==0x138370:return (a+(b&0xffff)*16)&0xffffffff
        # Binding may change the actor's handle index. Animation setup MUST
        # reload the handle after the resource setup, rather than cache it.
        if pc==0x139d20:self.mem.store(a+0x2a,7,2)
        if pc==0x139fc0:self.mem.store(a+0xc,b,1)
        if pc==0x119910:
            self.mem.put(a,self.mem.get(b,c))
            self.mem.store(0x3eb054,self.mem.load(0x3eb054)+3)
        if pc==0x18c9c0:return self.mem.load(0x600100)
        if pc==0x18b6c0:
            # A callee may replace publication globals; consumers reload them.
            self.mem.store(0x3eb058,0x500400)
            self.mem.store(0x3eb05c,0x600400)
            return self.mem.load(0x600104)
        if pc==0x138b70:return self.mem.load(a)
        if pc==0x138af0:return self.mem.load(a+0x100+(b&15)*8)
        if pc==0x138b30:return self.mem.load(a+0x104+(b&15)*8)
        if pc==0x141de0:
            # The allocator can mutate the base index before the caller reads it.
            base=self.mem.load(0x600108)
            self.mem.store(base+0x54,self.mem.load(base+0x54)+1)
            return 0x700000+len(self.calls)*0x100
        if pc==0x141e40:
            self.mem.store(a+0x54,2)
            for i in range(4):self.mem.store(a+0x68+i*4,0x700000+i*0x100)
            return b
        if pc in (0x266850,0x266a20,0x266c40):
            return self.mem.load(0x600110)+{0x266850:0,0x266a20:0x1000,0x266c40:0x2000}[pc] if self.mem.load(0x600110) else 0
        return 0


class ActorSetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib=configure(library(output=ROOT/'local/build/actor_setup.so'))

    def test_six_arguments_and_reloaded_handle(self):
        d=SetupDriver();a=0x500000
        d.mem.store(a+0x2a,3,2)
        d.mem.store(0x3f4300+3*4,0x600000)
        d.mem.store(0x3f4300+7*4,0x700000)
        self.lib.hg_actor_setup(C.byref(d.env),a,0x1000000,0x9000001,0,0x1234,0x8765)
        self.assertEqual(d.calls,[('call',0x138370,0x600000,0x1000000),
            ('call',0x138370,0x600000,0x9000001),
            ('call',0x139d20,a,0x600000,0x600010),
            ('call',0x1235c0,a+0x90,0),('call',0x267300,a+0x250,0),
            ('call',0x139fc0,a+0x90,0x1234),('call',0x139ff0,a+0x90,0x700000,0x8765)])
        self.assertEqual(d.mem.load(a+0x9c,1),0x34)
        self.assertFalse(d.errors)

    def test_zero_resource_ids_and_default_sequence(self):
        d=SetupDriver();a=0x500000
        self.lib.hg_actor_setup(C.byref(d.env),a,0,0,0,0,0)
        self.assertEqual(d.calls,[('call',0x139d20,a,0,0),
            ('call',0x1235c0,a+0x90,0),('call',0x267300,a+0x250,0),
            ('call',0x139fc0,a+0x90,0),('call',0x13a0a0,a+0x90)])

    def test_palette_and_mode_memory_effects(self):
        d=Driver();a=0x500000;d.mem.store(0x37b9a0+20,0xabcdef12)
        self.lib.hg_actor_set_palette(C.byref(d.env),a,5)
        self.lib.hg_actor_set_mode(C.byref(d.env),a,0x12345678)
        self.assertEqual(d.mem.load(a+0xc,1),0x78)
        self.assertEqual(d.mem.load(a+0x50),0xabcdef12)
        for off in range(0x40,0x50,4):self.assertEqual(d.mem.load(a+off),0x3f800000)
        self.assertFalse(d.calls)

    def test_input_transition_followed_by_held_release_and_expiry(self):
        d=SetupDriver();a=0x500000;d.mem.store(0x3f9fee,16,1)
        step=lambda:self.lib.hg_player_input_transitions(C.byref(d.env),a)
        d.mem.store(0x3eaf1c,0xbf400000) # negative analog magnitude .75
        self.assertEqual(step(),0)
        self.assertEqual(d.mem.load(a+0x4a4,1),1)
        self.assertEqual(d.mem.load(a+0x4a8,2),40)
        self.assertEqual(step(),0) # held input changes timer, not phase
        self.assertEqual(d.mem.load(a+0x4a4,1),1)
        self.assertEqual(d.mem.load(a+0x4a8,2),39)
        d.mem.store(0x3eaf1c,0x3f000000) # exactly .5 is released
        step();self.assertEqual(d.mem.load(a+0x4a4,1),2)
        d.mem.store(0x3eaf04,0x20,2)
        self.assertEqual(step(),1)
        self.assertEqual(d.mem.load(a+0x4a6,1),1)
        d.mem.store(0x3eaf04,0,2)
        for _ in range(40):step()
        # The update that decrements 1 to zero still retains the counters.
        self.assertEqual(d.mem.load(a+0x4a4,1),2)
        self.assertEqual(step(),0)
        self.assertEqual(d.mem.load(a+0x4a4,1),0)
        self.assertEqual(d.mem.load(a+0x4a6,1),0)

    def test_input_forced_completion_skips_helper(self):
        d=SetupDriver();d.mem.store(0x3fa067,1,1)
        self.assertEqual(self.lib.hg_player_input_transitions(C.byref(d.env),0x500000),0x80000000)
        self.assertEqual(d.mem.load(0x3f9fee,1),16)
        self.assertFalse(d.calls)

    def test_connected_area_setup_and_active_dispatch(self):
        class IntegrationDriver(Driver):
            def external(self,pc,a=0,b=0,c=0,d=0):
                arities={0x1330f0:2,0x143e60:2,0x144340:3,
                         0x123660:2,0x139c90:2,0x18b6c0:2,0x18c120:2}
                if pc not in arities:return super().external(pc,a,b,c,d)
                self.calls.append(('call',pc,*(a,b,c,d)[:arities[pc]]))
                return 1 if pc==0x18b6c0 else 0
        d=IntegrationDriver();a=0x500000;block=0x600000;model=block+0x1000
        d.mem.store(0x3f4300+0x233*4,block)
        for off,value in ((0,0x354b4c46),(4,2),(16,0x01001000),(20,0x09002000)):
            d.mem.store(block+off,value)
        d.mem.store(model+8,2);d.mem.store(model+0xc,0x40)
        d.mem.store(0x4d8140,0x700008);d.mem.store(0x4d8144,2)
        d.mem.store(0x700000,0x710100);d.mem.store(0x700004,0x710000)
        d.mem.store(0x37b9a0+20,0x12345678)
        self.lib.hg_area1_actor_fixed(C.byref(d.env),a)
        self.assertFalse(d.errors)
        self.assertEqual(d.mem.load(a+0x18,1),1)
        self.assertEqual(d.mem.load(a+0xa0),model)
        self.assertEqual(d.mem.load(a+0xa4),block+0x2000)
        self.assertEqual(d.mem.load(a+0xe4),2)
        self.assertEqual(d.mem.load(a+0xe8),a+0xf0)
        self.assertEqual(d.mem.load(a+0xf0),0x710000)
        self.assertEqual(d.mem.load(a+0xf4),0x710100)
        self.assertEqual(d.mem.load(0x4d8144),0)

        self.assertEqual(d.mem.load(a+0xe0),0x12345678)
        self.assertEqual(d.mem.load(a+0x274),0)
        before=len(d.calls)
        self.lib.hg_area1_actor_fixed(C.byref(d.env),a)
        self.assertFalse(d.errors)
        self.assertIn(('call',0x18b6c0,a,a+0x2d0),d.calls[before:])
        self.assertIn(('call',0x18c120,a,a+0x2d0),d.calls[before:])
        self.assertEqual(d.mem.load(a+0xa0),model)
        self.assertEqual(d.mem.load(0x4d8144),0)

    def test_instruction_packet_rejects_missing_body_and_delay_slot(self):
        from verify_actor_setup import validate_packet
        packet=json.loads((ROOT/'analysis/actor_setup/functions.json').read_text())
        validate_packet(packet)
        for mode in ('function','delay','range'):
            bad=copy.deepcopy(packet)
            if mode=='function':bad['functions'].pop()
            elif mode=='delay':bad['functions'][0]['instructions'].pop()
            else:bad['functions'][0]['body_ranges'][0]['end_exclusive']='0x0018a378'
            with self.assertRaises(ValueError):validate_packet(bad)
