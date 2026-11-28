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

    def test_jalr_reads_target_before_overlapping_link_and_runs_delay(self):
        # jalr t0,t0; delay slot copies the new link. The target is the old t0.
        m=self.run_words([(8<<21)|(8<<11)|9, (8<<21)|(4<<11)|0x2d,
                          0x03e00008, 0])
        m.reg[8]=0x2000
        m.put(0x2000,struct.pack('<II',(8<<21)|8,0))
        m.run(0x1000)
        self.assertEqual(m.reg[4],0x1008)

    def test_quadword_preserves_upper_lane(self):
        m=self.run_words([0x7c820000,0x78830000,0x03e00008,0])
        m.reg[4]=0x8000;m.reg[2]=(0x123456789abcdef0<<64)|0xdeadbeef
        m.run(0x1000)
        self.assertEqual(m.reg[3],m.reg[2])

    def test_doubleword_shift32_extracts_low24_and_preserves_upper_lane(self):
        m=self.run_words([(4<<16)|(2<<11)|(8<<6)|0x3c,
                          (2<<16)|(2<<11)|(8<<6)|0x3e,0x03e00008,0])
        m.reg[4]=(0xabcdef<<64)|0xfedcba9876543210
        m.reg[2]=0x12345678<<64
        m.run(0x1000)
        self.assertEqual(m.reg[2],(0x12345678<<64)|0x543210)
    def test_unknown_and_unmapped_fail(self):
        m=self.run_words([0x44000000])
        with self.assertRaises(ValueError):m.run(0x1000)
        with self.assertRaises(ValueError):m.get(0x9999,1)

    def test_signed_helper(self):
        self.assertEqual(sx(0xffffffff,32),-1)
        self.assertEqual(sx(0x80000000,32),-2147483648)

    def test_halfword_load_signedness_and_store_width(self):
        m=self.run_words([0x84820000,0xa4820002,0x94830000,0x03e00008,0])
        m.reg[4]=0x8000;m.put(0x8000,b'\0\x80\xaa\xbb')
        self.assertEqual(m.run(0x1000),-32768)
        self.assertEqual(m.reg[3],32768);self.assertEqual(m.get(0x8002,2),b'\0\x80')

    def test_lwu_zero_extends_low_lane_and_preserves_upper_lane(self):
        m = self.run_words([(0x27 << 26) | (4 << 21) | (2 << 16) | 4, 0x03e00008, 0])
        m.reg[4] = 0x8000
        m.reg[2] = (0x123456789abcdef0 << 64) | 0xffffffffffffffff
        m.put(0x8004, struct.pack('<I', 0x80001234))
        m.run(0x1000)
        self.assertEqual(m.reg[2], (0x123456789abcdef0 << 64) | 0x80001234)

    def test_andi_zero_extends_immediate_and_tail_jump_preserves_link(self):
        m=self.run_words([0x3082ffff,0x08000800,0x24420001])
        m.reg[4]=0xffff00008000
        m.hooks[0x2000]=lambda cpu:cpu.reg[2]
        self.assertEqual(m.run(0x1000),32769)

    def test_sltu_compares_full_unsigned_doubleword(self):
        m=self.run_words([0x0085102b,0x03e00008,0]);m.reg[4]=(1<<64)-1;m.reg[5]=1
        self.assertEqual(m.run(0x1000),0)

    def test_finite_scalar_arithmetic_rounding_and_sign(self):
        # add/sub/mul chop; div.S rounds to nearest (PCSX2 default EE division
        # rounding, confirmed by M8 captures). No retail instruction words.
        cases=[(0,0x3f800000,0xbfc00000,0xbf000000),
               (1,0x3f800000,0x3fc00000,0xbf000000),
               (2,0xbfc00000,0x40000000,0xc0400000),
               (3,0xbf800000,0x40400000,0xbeaaaaab),  # -1/3 rounds away from chop
               (3,0x40a00000,0x40400000,0x3fd55555),  # 5/3 nearest equals chop
               (0,0x3f800000,0x33800000,0x3f800000)]
        for fn,x,y,want in cases:
            m=self.run_words([(0x11<<26)|(16<<21)|(2<<16)|(1<<11)|(3<<6)|fn,0x03e00008,0])
            m.fpr[1]=x;m.fpr[2]=y;m.run(0x1000)
            self.assertEqual(m.fpr[3],want)
        for x,y in ((0x7f800000,0x3f800000),(0x3f800000,0)):
            m=self.run_words([(0x11<<26)|(16<<21)|(2<<16)|(1<<11)|(3<<6)|3])
            m.fpr[1]=x;m.fpr[2]=y
            with self.assertRaises(ValueError):m.run(0x1000)


    def test_mul_zero_product_keeps_operand_sign_xor(self):
        # M12: +0 * negative stores -0, as PCSX2 does; add/sub zero results stay +0.
        cases=[(2,0x00000000,0xbf333333,0x80000000),(2,0x80000000,0xbf333333,0x00000000),
               (2,0x80000000,0x3f000000,0x80000000),(2,0x00000001,0xbf800000,0x80000000),  # denormal operand
               (2,0x00800000,0x80800000,0x80000000),                                        # flushed underflow
               (1,0x80000000,0x00000000,0x00000000)]
        for fn,x,y,want in cases:
            m=self.run_words([(0x11<<26)|(16<<21)|(2<<16)|(1<<11)|(3<<6)|fn,0x03e00008,0])
            m.fpr[1]=x;m.fpr[2]=y;m.run(0x1000)
            self.assertEqual(m.fpr[3],want,(fn,hex(x),hex(y)))

    def test_exact_rational_chop_sqrt_and_nearest_boundaries(self):
        from fractions import Fraction
        from ee_subset import fp_chop, fp_signed_nearest, fp_value
        from verify_player_turn_runtime import MODEL_DEFECTS

        # Exact reference binary-search oracle over Fraction.
        def ref_chop(value, root=False):
            low, high = 0, 0x7f7fffff
            while low < high:
                mid = (low + high + 1) // 2
                c = fp_value(mid)
                if (c * c if root else c) <= value:
                    low = mid
                else:
                    high = mid - 1
            return low if low >= 0x800000 else 0

        vals = [
            Fraction(0), Fraction(1, 1 << 130), Fraction(1, 1 << 127), Fraction(1, 1 << 126),
            Fraction(1, 1 << 126) - Fraction(1, 1 << 200), Fraction(1, 1 << 126) + Fraction(1, 1 << 200),
            Fraction(1, 3), Fraction(2, 3), Fraction(5, 3), Fraction(7, 4),
            Fraction((0x800000 << 1) + 1, 1 << 24), Fraction((0x800001 << 1) + 1, 1 << 24),
            Fraction((0xffffff << 1) + 1, 1 << 24), Fraction(((1 << 24) - 1) << 104, 1),
            Fraction((((1 << 24) - 1) << 104) + 1, 1), Fraction(1 << 128, 1),
        ]
        for v in vals:
            self.assertEqual(fp_chop(v, False), ref_chop(v, False))
            self.assertEqual(fp_chop(v, True), ref_chop(v, True))
        # Negative values fail closed.
        with self.assertRaises(ValueError):
            fp_chop(Fraction(-1, 2))
        # Exact tie-to-even checks for DIV.S nearest rounding.
        self.assertEqual(fp_signed_nearest(Fraction((0x800000 << 1) + 1, 1 << 24)), 0x3f800000)
        self.assertEqual(fp_signed_nearest(Fraction((0x800001 << 1) + 1, 1 << 24)), 0x3f800002)
        self.assertEqual(fp_signed_nearest(-Fraction((0x800001 << 1) + 1, 1 << 24)), 0xbf800002)
        # Preserve every turn-runtime model-defect mutation anchor verbatim.
        model_text = (Path(__file__).resolve().parents[1] / 'tools/ee_subset.py').read_text()
        for name, old, _ in MODEL_DEFECTS:
            self.assertEqual(model_text.count(old), 1, f'Missing model anchor {name}')


if __name__=='__main__':unittest.main()
