"""Synthetic coverage for the bounded M8 instruction oracle, not retail proof."""
import struct
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from ee_subset import Machine,MASK64,fp_ee_add

def pack(lanes):return sum(x<<(32*i) for i,x in enumerate(lanes))
def lanes(value):return [(value>>(32*i))&0xffffffff for i in range(4)]
def vu(fn,fs=1,ft=2,fd=3,mask=15):return (0x12<<26)|(16<<21)|(mask<<21)|(ft<<16)|(fs<<11)|(fd<<6)|fn

def program(words):
 m=Machine();m.put(0x1000,struct.pack('<'+'I'*(len(words)+2),*words,0x03e00008,0));return m

class PlayerMathModelTests(unittest.TestCase):
 def test_ee_cancellation_discards_alignment_tail(self):
  # One EE alignment guard bit cannot retain the tiny negative operand.
  # IEEE exact-add/chop would yield the immediately preceding value instead.
  self.assertEqual(fp_ee_add(0x3f800000,0xb2800000),0x3f800000)
  self.assertEqual(fp_ee_add(0xbf800000,0x32800000),0xbf800000)
  # A two-exponent alignment drops only the smaller operand's final bit.
  self.assertEqual(fp_ee_add(0x3f800000,0xbe800001),0x3f400000)
  self.assertEqual(fp_ee_add(0x3f800000,0x3e800001,True),0x3f400000)
  self.assertEqual(fp_ee_add(0x3f800000,0xbf800000),0)
 def test_signed_branches_keep_delay_slot(self):
  for op,rt,taken in [(1,0,[-1]),(1,1,[0,1]),(6,0,[-1,0]),(7,0,[1])]:
   for a in [-1,0,1]:
    m=program([(op<<26)|(4<<21)|(rt<<16)|2,0x24420001,0x24420004])
    m.reg[4]=a&MASK64;m.run(0x1000)
    self.assertEqual(m.reg[2],1 if a in taken else 5)
 def test_branch_likely_annuls_only_untaken_delay(self):
  for op,taken in [(0x14,[0]),(0x15,[-1,1]),(0x16,[-1,0]),(0x17,[1])]:
   for a in [-1,0,1]:
    m=program([(op<<26)|(4<<21)|2,0x24420001,0x24420004]);m.reg[4]=a&MASK64;m.run(0x1000)
    self.assertEqual(m.reg[2],1 if a in taken else 4)
 def test_conditional_moves_preserve_upper_lane(self):
  for fn in [10,11]:
   for flag in [0,1]:
    m=program([(4<<21)|(5<<16)|(2<<11)|fn]);m.reg[4]=123;m.reg[5]=flag;m.reg[2]=(987<<64)|55
    m.run(0x1000);self.assertEqual(m.reg[2],(987<<64)|(123 if ((flag==0)==(fn==10)) else 55))
 def test_xori_and_addi_overflow_domain(self):
  m=program([(14<<26)|(4<<21)|(2<<16)|0xffff]);m.reg[4]=0x12340000;m.run(0x1000);self.assertEqual(m.reg[2],0x1234ffff)
  m=program([(8<<26)|(4<<21)|(2<<16)|1]);m.reg[4]=0x7fffffff
  with self.assertRaisesRegex(ValueError,'ADDI overflow'):m.run(0x1000)
 def test_variable_word_shifts_mask_amount_and_extend_sign(self):
  for fn in (4,6,7):
   m=program([(4<<21)|(5<<16)|(2<<11)|fn]);m.reg[4]=63;m.reg[5]=0x80000001;m.reg[2]=1<<64
   m.run(0x1000)
   if fn==4:want=(1<<64)|0xffffffff80000000
   elif fn==6:want=(1<<64)|1
   else:want=(1<<64)|MASK64
   self.assertEqual(m.reg[2],want)
 def test_variable_doubleword_shifts_preserve_upper_lane(self):
  for fn,want in [(0x14,0x8000000000000000),(0x16,1),(0x17,MASK64)]:
   m=program([(4<<21)|(5<<16)|(2<<11)|fn]);m.reg[4]=127;m.reg[5]=0x8000000000000001;m.reg[2]=1<<64
   m.run(0x1000);self.assertEqual(m.reg[2],(1<<64)|want)
 def test_finite_conversions_and_signed_accumulator(self):
  m=program([(0x11<<26)|(20<<21)|(1<<11)|(2<<6)|0x20]);m.fpr[1]=0xfffffffd;m.run(0x1000);self.assertEqual(m.fpr[2],0xc0400000)
  m=program([(0x11<<26)|(16<<21)|(1<<11)|(2<<6)|0x24]);m.fpr[1]=0xc0700000;m.run(0x1000);self.assertEqual(m.fpr[2],0xfffffffd)
  m=program([(0x11<<26)|(16<<21)|(2<<16)|(1<<11)|0x1a,(0x11<<26)|(16<<21)|(2<<16)|(1<<11)|(3<<6)|0x1c]);m.fpr[1]=0xbf800000;m.fpr[2]=0x3fc00000
  m.run(0x1000);self.assertEqual(m.acc,0xbfc00000);self.assertEqual(m.fpr[3],0xc0400000)
 def test_vector_mask_broadcast_alias_and_inactive_nonfinite(self):
  m=program([vu(0,fs=1,ft=2,fd=1,mask=8)])
  m.vf[1]=pack([0x3f800000,0x7f800000,0xdeadbeef,0x12345678]);m.vf[2]=pack([0xbf000000,0,0,0])
  m.run(0x1000);self.assertEqual(lanes(m.vf[1]),[0x3f000000,0x7f800000,0xdeadbeef,0x12345678])
 def test_vector_accumulator_and_destination_masks(self):
  # Synthetic vmulax.xyzw ACC,vf1,vf2; vmadday.xyzw; vmaddz.x vf3.
  m=program([vu(0x3c,fs=1,ft=2,fd=6),vu(0x3d,fs=1,ft=2,fd=2),vu(0x0a,fs=1,ft=2,fd=3,mask=8)])
  m.vf[1]=pack([0x3f800000,0xbf800000,0x40000000,0]);m.vf[2]=pack([0x40000000,0x3f800000,0xbf800000,0]);m.vf[3]=pack([0,11,22,33])
  m.run(0x1000);self.assertEqual(m.vacc,[0x40400000,0xc0400000,0x40c00000,0]);self.assertEqual(lanes(m.vf[3]),[0x40000000,11,22,33])
 def test_vector_rotate_mask_and_constant_register(self):
  m=program([vu(0x3d,fs=1,ft=1,fd=12,mask=1),vu(0x28,fs=1,ft=1,fd=0)])
  m.vf[1]=pack([0x3f800000,0x40000000,0x40400000,0x40800000]);m.run(0x1000)
  self.assertEqual(lanes(m.vf[1]),[0x3f800000,0x40000000,0x40400000,0x3f800000]);self.assertEqual(lanes(m.vf[0]),[0,0,0,0x3f800000])
 def test_vector_load_store_and_sqrt_broadcast_q(self):
  m=program([(0x36<<26)|(4<<21)|(2<<16),vu(0x3d,fs=0,ft=2,fd=14,mask=12),vu(0x20,fs=0,ft=0,fd=3,mask=8),(0x3e<<26)|(4<<21)|(3<<16)|16])
  m.reg[4]=0x8000;m.put(0x8000,struct.pack('<IIII',0,0,0,0x40800000));m.run(0x1000)
  self.assertEqual(m.load(0x8010,4),0x40000000)
 def test_unknown_vector_and_nonfinite_active_lane_fail(self):
  m=program([vu(0x30)])
  with self.assertRaisesRegex(ValueError,'unsupported COP2'):m.run(0x1000)
  m=program([vu(0x28)]);m.vf[1]=pack([0x7f800000,0,0,0])
  with self.assertRaisesRegex(ValueError,'non-finite'):m.run(0x1000)
if __name__=='__main__':unittest.main()
