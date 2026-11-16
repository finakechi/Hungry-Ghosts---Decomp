"""Synthetic M9 turn/camera checks; retail observations are compared separately.

All tables, sticks and leaf results are synthetic. Expected values come from
the bounded FP oracle in tools/ee_subset.py, never from retail captures.
"""
import ctypes as C
import random
import struct
import sys
import unittest
from resource_harness import Driver, ResourceEnv, ROOT, library
sys.path.insert(0,str(ROOT/'tools'))
from ee_subset import fp_value, fp_signed_chop, fp_ee_add

DOMAIN_FAULT=0xfffffff0
PLAYER,CAMERA=0x3f92d0,0x3f9860
FUNCTIONS={0x15fe00:('hg_player_movement_gate',2,True),0x15fe70:('hg_player_movement',2,True),
    0x1608b0:('hg_player_walk',1,False),0x160510:('hg_player_turn',2,False),
    0x160420:('hg_player_rate_approach',4,True),0x15fcd0:('hg_player_speed_class',2,True),
    0x160c20:('hg_player_move_limit',1,True),0x1204e0:('hg_angle_wrap',1,True),
    0x12c790:('hg_camera_update',0,False),0x12d130:('hg_camera_follow',2,False),
    0x12bd00:('hg_camera_head_matrix',3,True)}
# Bus arity (raw FP operands first, then integer arguments) of every call the
# M9 functions make. FP_SEAMS: entry -> (FP operands in f12.., result in f0).
ARITIES={0x15fe70:2,0x160ff0:1,0x15b260:1,0x15fd30:1,0x1606e0:2,0x160510:2,0x1204e0:1,
    0x160c60:1,0x1608b0:1,0x15aea0:2,0x120830:2,0x120580:3,0x15ebc0:2,0x15fc10:1,
    0x15fcd0:2,0x160c20:1,0x160420:4,0x143560:1,0x143620:1,0x15e1d0:1,0x13a240:2,
    0x13a2a0:3,0x267450:1,0x12d130:2,0x143c60:3,0x143d30:3,0x143b90:3,0x12bd00:3,
    0x12c640:2,0x12cce0:1,0x12c3d0:1}
FP_SEAMS={0x160420:(3,False),0x1204e0:(1,True),0x143560:(1,True),0x143620:(1,True),
    0x120830:(0,True),0x120580:(2,True),0x15ebc0:(1,False),0x143c60:(1,False),
    0x143d30:(1,False),0x143b90:(1,False)}


def f(x):return struct.unpack('<I',struct.pack('<f',x))[0]
def neg(b):return b^0x80000000
def mul(a,b):return fp_signed_chop(fp_value(a)*fp_value(b))
def add(a,b):return fp_ee_add(a,b)
def sub(a,b):return fp_ee_add(a,b,True)
def le(a,b):return fp_value(a)<=fp_value(b)
PI,TWO_PI=0x40490fdb,0x40c90fdb


def configure(lib):
    for entry,(name,n,returns) in FUNCTIONS.items():
        fn=getattr(lib,name);fn.argtypes=[C.POINTER(ResourceEnv)]+[C.c_uint32]*n
        fn.restype=C.c_uint32 if returns else None
    lib.hg_ee_fp.argtypes=[C.c_uint32]*3+[C.POINTER(C.c_uint32)];lib.hg_ee_fp.restype=C.c_uint32
    lib.hg_camera_update_fixed.argtypes=[C.POINTER(ResourceEnv)];lib.hg_camera_update_fixed.restype=None
    return lib


class TurnDriver(Driver):
    """Records bus calls; returns are synthetic slots or composed C functions."""
    def __init__(self,mem=None,lib=None,compose=()):
        super().__init__(mem);self.lib=lib;self.compose=set(compose);self.returns={};self.effects={}
    def external(self,pc,a=0,b=0,c=0,d=0):
        if pc==DOMAIN_FAULT:
            self.calls.append(('fault',a));raise ValueError(f'domain fault {a:#x}')
        args=(a,b,c,d)[:ARITIES[pc]];self.calls.append((pc,*args))
        if pc in self.effects:self.effects[pc](*args)
        if pc in self.compose:
            name,n,_=FUNCTIONS[pc];return getattr(self.lib,name)(C.byref(self.env),*args[:n]) or 0
        return self.returns.get(pc,0)


def turn_tables(mem,step=f(2**-10),limit=f(2**-5),brake=f(2.0),decay=f(2**-9)):
    """Synthetic turn (39a3f0) and walk (39a3b0) rows and speed scales."""
    for base in (0x39a3f0,0x39a3b0):
        mem.store(base+4,brake);mem.store(base+8,decay)
        for level in (1,2,3):mem.store(base+level*16+8,step);mem.store(base+level*16+12,limit)
    for base in (0x3e0870,0x3e0860):
        mem.store(base+4,f(1.0));mem.store(base+8,f(0.5));mem.store(base+12,f(1.0))


class FpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.lib=configure(library(output=ROOT/'local/build/player_turn_camera.so'))
    def fp(self,op,a,b):
        fault=C.c_uint32(0);value=self.lib.hg_ee_fp(op,a,b,C.byref(fault));return value,fault.value
    def test_helpers_match_bounded_oracle(self):
        rng=random.Random(0x160510);special=[0,0x80000000,1,0x807fffff,0x800000,0x7f7fffff,0xff7fffff,
            f(1.0),f(-1.0),PI,TWO_PI,f(0.4),f(-0.8),f(16.5),0x3f9a25c1]
        def value():
            if rng.random()<0.2:return rng.choice(special)
            return (rng.getrandbits(1)<<31)|(rng.randrange(1,255) if rng.random()<0.1 else rng.randrange(100,160))<<23|rng.getrandbits(23)
        oracle={0:add,1:sub,2:mul,3:lambda a,b:int(fp_value(a)<fp_value(b)),4:lambda a,b:int(le(a,b)),
            5:lambda a,b:int(fp_value(a)==fp_value(b)),
            6:lambda a,b:fp_signed_chop(fp_value(a)+fp_value(b)),7:lambda a,b:fp_signed_chop(fp_value(a)-fp_value(b))}
        for case in range(6000):
            a,b=value(),value()
            # Nearby exponents exercise the EE guard mask and cancellation.
            if case%3==0:b=(b&0x807fffff)|((((a>>23)&255)+rng.randrange(-3,4))%255)<<23
            for op,expected in oracle.items():
                self.assertEqual(self.fp(op,a,b),(expected(a,b),0),(op,hex(a),hex(b)))
    def test_guard_bit_differs_from_exact_add(self):
        # The control: an exact VU-style add of these operands differs from EE.
        a,b=0x3f800000,0xb3800001
        self.assertNotEqual(self.fp(0,a,b)[0],self.fp(6,a,b)[0])
        self.assertEqual(self.fp(0,a,b)[0],add(a,b))
    def test_non_finite_operands_fault(self):
        for op in range(8):
            for bad in (0x7f800000,0xff800000,0x7fc00000):
                self.assertEqual(self.fp(op,bad,f(1.0))[1],1);self.assertEqual(self.fp(op,f(1.0),bad)[1],1)
        self.assertEqual(self.fp(9,0,0)[1],1)


class TurnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.lib=configure(library(output=ROOT/'local/build/player_turn_camera.so'))
    def driver(self,stick,rate=0,heading=0):
        d=TurnDriver(lib=self.lib,compose=(0x15fcd0,0x160420,0x1204e0));turn_tables(d.mem)
        d.mem.store(0x3eaf0c,stick);d.mem.store(PLAYER+0x440,rate);d.mem.store(PLAYER+0x44,heading)
        return d
    def turn(self,d,mode=1):
        self.lib.hg_player_turn(C.byref(d.env),PLAYER,mode);self.assertFalse(d.errors,d.errors)
        return d.mem.load(PLAYER+0x440),d.mem.load(PLAYER+0x44)
    def test_stick_sign_selects_turn_direction(self):
        step=f(2**-10);heading=f(0.25)
        for stick,rate in [(f(-1.0),neg(step)),(f(1.0),step),(f(-0.5),neg(step)),(f(0.9),step)]:
            with self.subTest(stick=hex(stick)):
                d=self.driver(stick,heading=heading);got_rate,got_heading=self.turn(d)
                self.assertEqual(got_rate,rate);self.assertEqual(got_heading,sub(heading,rate))
                self.assertEqual(d.calls[:2],[(0x15fcd0,PLAYER,1),(0x160420,*([neg(step),neg(f(2**-5))] if stick>>31 else [step,f(2**-5)]),f(2.0),PLAYER+0x440)])
                self.assertEqual(d.calls[-1][0],0x1204e0)
    def test_stick_levels_and_dead_zone(self):
        for stick,level in [(f(0.39),0),(f(0.4),1),(f(-0.6),2),(f(0.79),2),(f(0.8),3),(f(-1.0),3)]:
            with self.subTest(stick=hex(stick)):
                d=self.driver(stick);self.turn(d);self.assertEqual(d.mem.load(PLAYER+0x504,1),level)
                self.assertEqual(any(c[0]==0x160420 for c in d.calls),level!=0)
    def test_rate_ramps_and_clamps_at_limit(self):
        d=self.driver(f(-1.0));rates=[]
        for _ in range(40):rates.append(self.turn(d)[0])
        self.assertEqual(rates[0],neg(f(2**-10)));self.assertEqual(rates[31],neg(f(2**-5)))
        self.assertEqual(set(rates[31:]),{neg(f(2**-5))})
        self.assertTrue(all(le(b,a) for a,b in zip(rates,rates[1:])))
    def test_opposite_rate_is_braked(self):
        d=self.driver(f(-1.0),rate=f(2**-6));self.turn(d)
        self.assertEqual(d.mem.load(PLAYER+0x440),add(f(2**-6),mul(neg(f(2**-10)),f(2.0))))
    def test_release_decays_without_sign_crossing(self):
        for rate,want in [(neg(f(3*2**-10)),neg(f(2**-10))),(neg(f(2**-10)),0),(f(2**-10),0),(0,0)]:
            with self.subTest(rate=hex(rate)):
                d=self.driver(0,rate=rate);got,_=self.turn(d)
                self.assertEqual(got,want);self.assertNotIn(0x160420,[c[0] for c in d.calls])
    def test_heading_wraps_into_half_open_pi_interval(self):
        for heading,rate in [(f(3.14),neg(f(0.01))),(f(-3.14),f(0.01)),(PI,0),(neg(PI),0)]:
            with self.subTest(heading=hex(heading)):
                value=sub(heading,rate)
                while not le(value,PI):value=sub(value,TWO_PI)
                while le(value,neg(PI)):value=add(value,TWO_PI)
                w=self.lib.hg_angle_wrap(C.byref(TurnDriver().env),sub(heading,rate))
                self.assertEqual(w,value);self.assertTrue(le(neg(PI),w) and le(w,PI) and w!=neg(PI))
    def test_angle_wrap_faults_on_non_finite(self):
        d=TurnDriver();self.lib.hg_angle_wrap(C.byref(d.env),0x7f800000)
        self.assertEqual(d.calls[:1],[('fault',5)])  # first operation is c.le.s (op 4)
    def test_walk_selects_turn_mode_from_speed(self):
        for forward,mode in [(0,4),(f(0.5),1)]:
            d=TurnDriver(lib=self.lib,compose=(0x15fcd0,0x160420));turn_tables(d.mem)
            d.mem.store(0x3eaf10,0);d.mem.store(PLAYER+0x438,forward);d.mem.store(PLAYER+0x434,0)
            d.returns.update({0x143560:f(0.5),0x143620:f(-1.0)})
            self.lib.hg_player_walk(C.byref(d.env),PLAYER);self.assertFalse(d.errors,d.errors)
            self.assertIn((0x160510,PLAYER,mode),d.calls)
    def test_gate_reports_and_clears_selected_flag(self):
        for flag,select,result,after in [(0,1,0,0),(1,0,0,1),(1,1,1,1),(2,1,0,0)]:
            d=TurnDriver();d.mem.store(PLAYER+0x4ad,flag,1);d.mem.store(PLAYER+0x2c3,select,1)
            self.assertEqual(self.lib.hg_player_movement_gate(C.byref(d.env),PLAYER,1),result)
            self.assertEqual(d.calls,[(0x15fe70,PLAYER,1)]);self.assertEqual(d.mem.load(PLAYER+0x4ad,1),after)
    def test_movement_turns_and_walks_only_when_allowed(self):
        for mode,busy,walks in [(1,0,True),(0,0,False),(1,1,False)]:
            with self.subTest(mode=mode,busy=busy):
                d=TurnDriver(lib=self.lib,compose=(0x1204e0,));d.mem.store(0x3eaee8,busy,1);d.mem.store(PLAYER+0x434,f(1.0));d.mem.store(PLAYER+0x438,f(1.0))
                d.mem.store(PLAYER+0x3c0,f(2.0));d.mem.store(PLAYER+0x43c,f(0.25))
                d.returns[0x120830]=f(1.0)
                self.lib.hg_player_movement(C.byref(d.env),PLAYER,mode);self.assertFalse(d.errors,d.errors)
                called=[c[0] for c in d.calls]
                self.assertEqual(0x1608b0 in called,walks);self.assertEqual(0x160c60 in called,walks)
                if not walks:self.assertEqual((d.mem.load(PLAYER+0x434),d.mem.load(PLAYER+0x438)),(0,0))
                self.assertEqual(d.mem.load(PLAYER+0x3c0),0x3f9a25c1);self.assertEqual(d.mem.load(PLAYER+0x43c),0)
                self.assertEqual(called[-2:],[0x15ebc0,0x15fc10])
    def test_ordinary_update_allows_movement(self):
        """M8 passed a1=0 to 0015fe00; the 00159b08 delay slot sets a1=1."""
        from test_player_ordinary import OrdinaryDriver, configure as ordinary
        lib=ordinary(library(output=ROOT/'local/build/player_ordinary.so'))
        d=OrdinaryDriver();a=0x500000
        d.mem.store(a+0x19,1,1);d.mem.store(a+0x1a,1,1);d.mem.store(a+0x449,255,1)
        lib.hg_player_ordinary_update(C.byref(d.env),a)
        gates=[c for c in d.calls if c[1]==0x15fe00]
        self.assertTrue(gates);self.assertEqual(set(gates),{('call',0x15fe00,a,1)})


class CameraTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.lib=configure(library(output=ROOT/'local/build/player_turn_camera.so'))
    def test_follow_order_and_shake_keeps_pair(self):
        for shake in (0,1):
            d=TurnDriver();d.returns={0x12bd00:0x70000500,0x12cce0:shake}
            d.mem.store(CAMERA+0x140,1,1)
            d.mem.put(CAMERA,bytes(range(32)));d.mem.put(CAMERA+0xe0,bytes(range(100,132)))
            self.lib.hg_camera_follow(C.byref(d.env),PLAYER,CAMERA);self.assertFalse(d.errors,d.errors)
            self.assertEqual(d.calls,[(0x12bd00,PLAYER,CAMERA,1),(0x12c640,CAMERA,0x70000500),(0x12cce0,CAMERA),(0x12c3d0,CAMERA)])
            self.assertEqual(d.mem.get(CAMERA,32),bytes(range(32)) if shake else bytes(range(100,132)))
    def test_head_matrix_eye_target_and_rotation_order(self):
        d=TurnDriver();ident=[f(1.0) if i%5==0 else 0 for i in range(16)]
        for i,v in enumerate(ident):d.mem.store(0x3db2b0+i*4,v)
        for off,v in [(0x3c0,f(0.1)),(0x44,f(0.2)),(0x48,f(0.3)),(0x380,f(1.0)),(0x384,f(2.0)),(0x388,f(3.0)),(0x38c,f(1.0))]:
            d.mem.store(PLAYER+off,v)
        self.assertEqual(self.lib.hg_camera_head_matrix(C.byref(d.env),PLAYER,CAMERA,0),0x70000500)
        self.assertFalse(d.errors,d.errors)
        self.assertEqual(d.calls,[(0x143c60,f(0.1),0x70000500,0x70000500),(0x143d30,f(0.2),0x70000500,0x70000500),
            (0x143b90,f(0.3),0x70000500,0x70000500)])
        self.assertEqual([d.mem.load(CAMERA+0xe0+i*4) for i in range(4)],[f(1.0),f(2.0),f(3.0),f(1.0)])
        self.assertEqual([d.mem.load(CAMERA+0xf0+i*4) for i in range(4)],[f(1.0),f(2.0),f(23.0),f(1.0)])
    def test_head_matrix_offset_heading_and_kept_eye(self):
        d=TurnDriver(lib=self.lib,compose=(0x1204e0,));d.mem.store(PLAYER+0x49f,1,1)
        d.mem.store(PLAYER+0x44,f(3.0));d.mem.store(PLAYER+0x4dc,f(0.5));d.mem.put(CAMERA+0xe0,bytes(range(16)))
        self.lib.hg_camera_head_matrix(C.byref(d.env),PLAYER,CAMERA,1);self.assertFalse(d.errors,d.errors)
        self.assertEqual(d.calls[1][:2],(0x143c60,0));self.assertEqual(d.calls[2][1],sub(add(f(3.0),f(0.5)),TWO_PI))
        self.assertEqual(d.mem.get(CAMERA+0xe0,16),bytes(range(16)))
    def test_unobserved_camera_branches_fail_closed(self):
        d=TurnDriver();d.mem.store(PLAYER+0x47a,1,1)
        self.lib.hg_camera_head_matrix(C.byref(d.env),PLAYER,CAMERA,0);self.assertEqual(d.calls,[('fault',0x20)])
        d=TurnDriver();d.mem.store(0x3eaed4,1,1);self.lib.hg_camera_update(C.byref(d.env))
        self.assertEqual(d.calls[-1],('fault',0x21));self.assertNotIn(0x12d130,[c[0] for c in d.calls])
    def test_camera_update_follows_then_builds_view(self):
        d=TurnDriver();ident=[f(1.0) if i%5==0 else 0 for i in range(16)]
        for i,v in enumerate(ident):d.mem.store(0x3db2b0+i*4,v)
        d.mem.put(CAMERA,bytes(range(1,17)));d.mem.store(CAMERA+0x140,1,1)
        for off,v in [(0x30,f(5.0)),(0x34,f(1.5)),(0x38,f(-7.0))]:d.mem.store(PLAYER+off,v)
        d.mem.store(CAMERA+0xd0,f(0.1));d.mem.store(CAMERA+0xd4,f(-0.2))
        self.lib.hg_camera_update(C.byref(d.env));self.assertFalse(d.errors,d.errors)
        self.assertEqual([c[0] for c in d.calls],[0x15e1d0,0x13a240,0x13a2a0,0x13a2a0,0x13a2a0,0x267450,0x12d130,0x143c60,0x143d30])
        self.assertEqual(d.calls[6],(0x12d130,PLAYER,CAMERA))
        self.assertEqual(d.calls[7:],[(0x143c60,f(0.1),0x70000000,0x70000000),(0x143d30,f(-0.2),0x70000000,0x70000000)])
        self.assertEqual([d.mem.load(0x70000040+i*4) for i in range(3)],[f(5.0),add(f(16.5),f(1.5)),f(-7.0)])
        self.assertEqual(d.mem.get(0x70000030,16),bytes(range(1,17)));self.assertEqual(d.mem.load(CAMERA+0x140,1),0)


if __name__=='__main__':unittest.main()
