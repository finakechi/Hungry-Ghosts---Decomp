import struct
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from ee_subset import Machine, sx


class SubsetTests(unittest.TestCase):
    def run_words(self,words):
        m=Machine();m.put(0x1000,struct.pack('<'+'I'*len(words),*words));return m

    def test_taken_branch_and_return_delay_slots(self):
        # Hand-authored synthetic program, not game instructions.
        m=self.run_words([0x24020001,0x10000002,0x24420002,0x24420040,0x03e00008,0x24420004])
        self.assertEqual(m.run(0x1000),7)

    def test_untaken_branch_executes_delay_slot(self):
        m=self.run_words([0x24020001,0x10400002,0x24420002,0x24420004,0x03e00008,0])
        self.assertEqual(m.run(0x1000),7)

    def test_jal_link_and_delay(self):
        m=self.run_words([0x0c000800,0x24040005,0x0080102d,0x00600008,0x24420001])
        m.reg[3]=0xfff000
        m.hooks[0x2000]=lambda cpu: self.assertEqual(cpu.reg[4],5) or 99
        self.assertEqual(m.run(0x1000),6)

    def test_word_wrap_sign_extension_and_shift(self):
        m=self.run_words([0x24420001,0x00021043,0x03e00008,0]);m.reg[2]=0x7fffffff
        self.assertEqual(m.run(0x1000),-0x40000000)

    def test_quadword_preserves_upper_lane(self):
        m=self.run_words([0x7c820000,0x78830000,0x03e00008,0])
        m.reg[4]=0x8000;m.reg[2]=(0x123456789abcdef0<<64)|0xdeadbeef
        m.run(0x1000)
        self.assertEqual(m.reg[3],m.reg[2])

    def test_unknown_and_unmapped_fail(self):
        m=self.run_words([0x44000000])
        with self.assertRaises(ValueError):m.run(0x1000)
        with self.assertRaises(ValueError):m.get(0x9999,1)

    def test_signed_helper(self):
        self.assertEqual(sx(0xffffffff,32),-1)
        self.assertEqual(sx(0x80000000,32),-2147483648)


if __name__=='__main__':unittest.main()
