"""Deterministic tests for the M16 interpreter additions: MMI div1 and mflo1.

Reference values are computed here by independent integer arithmetic
(truncated-toward-zero division), never by the interpreter itself. Deliberate
wrong-result controls must be detected as failures, not as errors.
"""
import unittest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools')]
from ee_subset import Machine, MASK64, sx


def div1_word(rs, rt, rd=0):
    return (0x1c << 26) | (rs << 21) | (rt << 16) | (rd << 11) | 0x1a


def mflo1_word(rd):
    return (0x1c << 26) | (rd << 11) | 0x12


def reference_div1(left, right):
    """Independent reference: Python's truncated integer division."""
    left, right = sx(left, 32), sx(right, 32)
    quotient = abs(left) // abs(right) * (1 if (left < 0) == (right < 0) else -1)
    remainder = left - quotient * right
    return quotient, remainder


class Div1ReferenceTests(unittest.TestCase):
    """div1 must match an independently computed truncated quotient/remainder."""

    def make(self, left, right, rd=0):
        m = Machine()
        m.put(0, div1_word(5, 4, rd).to_bytes(4, 'little'))
        m.reg[5] = left & MASK64
        m.reg[4] = right & MASK64
        # Preload unrelated state that must be preserved.
        m.hi, m.lo = 0x1111111111111111, 0x2222222222222222
        m.reg[3], m.reg[6] = 0x3333333333333333, 0x4444444444444444
        return m

    def run_pair(self, left, right, rd=0):
        m = self.make(left, right, rd)
        m.run(0, stop=0x04)
        quotient, remainder = reference_div1(left, right)
        return m, quotient, remainder

    def check(self, left, right, rd=0):
        m, quotient, remainder = self.run_pair(left, right, rd)
        self.assertEqual(m.lo1, sx(quotient, 32) & MASK64,
                         f'LO1 quotient for {sx(left,32)}/{sx(right,32)}')
        self.assertEqual(m.hi1, sx(remainder, 32) & MASK64,
                         f'HI1 remainder for {sx(left,32)}/{sx(right,32)}')
        if rd:
            self.assertEqual(m.reg[rd], sx(quotient, 32) & MASK64,
                             f'rd quotient for {sx(left,32)}/{sx(right,32)}')
        # Unrelated state preservation.
        self.assertEqual(m.hi, 0x1111111111111111, 'HI untouched by div1')
        self.assertEqual(m.lo, 0x2222222222222222, 'LO untouched by div1')
        self.assertEqual(m.reg[3], 0x3333333333333333)
        self.assertEqual(m.reg[6], 0x4444444444444444)
        self.assertEqual(m.reg[0], 0)

    def test_positive_positive(self):
        self.check(10, 3)
        self.check(100, 7)
        self.check(9, 9)

    def test_negative_positive(self):
        self.check(-10, 3)
        self.check(-9, 3)
        self.check(-2, 5)

    def test_positive_negative(self):
        self.check(10, -3)
        self.check(9, -3)
        self.check(2, -5)

    def test_negative_negative(self):
        self.check(-10, -3)
        self.check(-9, -3)
        self.check(-2, -5)

    def test_three_operand_form(self):
        m = self.make(0x1234, 10, rd=8)
        m.put(0, div1_word(5, 4, 8).to_bytes(4, 'little'))
        m.run(0, stop=0x04)
        quotient, remainder = reference_div1(0x1234, 10)
        self.assertEqual(m.reg[8], sx(quotient, 32) & MASK64)
        self.assertEqual(m.lo1, sx(quotient, 32) & MASK64)
        self.assertEqual(m.hi1, sx(remainder, 32) & MASK64)

    def test_rd_zero_writes_no_register(self):
        # rd == 0: the quotient must not be written to any GPR. Registers 3
        # and 6 are preloaded sentinels that must remain untouched, reg 31
        # holds the interpreter's stop sentinel, and 4/5 are the operands.
        m, quotient, _ = self.run_pair(77, 13, rd=0)
        for n in list(range(3)) + [7] + list(range(8, 31)):
            self.assertEqual(m.reg[n], 0, f'GPR {n} written by rd==0 form')
        self.assertEqual(m.reg[3], 0x3333333333333333)
        self.assertEqual(m.reg[6], 0x4444444444444444)

    def test_unsigned_operand_encoding(self):
        # Operands are taken as signed 32-bit values even if encoded unsigned.
        self.check(0xfffffffb & MASK64, 3)  # -5 / 3
        self.check(5, 0xfffffffd & MASK64)  # 5 / -3
        self.check(0xffffffff & MASK64, 2)  # -1 / 2

    def test_observed_corridor_case(self):
        # 001537f0: a1 = lh [gp+3098]+0x5c0 (render width), a0 = 10.
        for width in (0x78, 0x80, 0x280, 0x2d0, 0x2c8, 0x2b0):
            self.check(width, 10)

    def test_zero_divisor_fails_closed(self):
        m = self.make(5, 0)
        with self.assertRaises(ValueError):
            m.run(0, stop=0x04)

    def test_overflow_fails_closed(self):
        m = self.make(-(1 << 31), -1)
        with self.assertRaises(ValueError):
            m.run(0, stop=0x04)

    def test_zero_dividend(self):
        m, quotient, remainder = self.run_pair(0, 7)
        self.assertEqual(quotient, 0)
        self.assertEqual(remainder, 0)


class Mflo1ReferenceTests(unittest.TestCase):
    def test_mflo1_reads_lo1(self):
        m = Machine()
        m.put(0, div1_word(5, 4).to_bytes(4, 'little'))
        m.put(4, mflo1_word(2).to_bytes(4, 'little'))
        m.reg[5] = 0xfffffff6 & MASK64  # -10
        m.reg[4] = 3
        m.run(0, stop=0x08)
        quotient, remainder = reference_div1(-10, 3)
        self.assertEqual(m.reg[2], sx(quotient, 32) & MASK64)
        self.assertEqual(m.reg[2], m.lo1)
        self.assertEqual(m.hi1, sx(remainder, 32) & MASK64)

    def test_mflo1_rd_zero_is_nop(self):
        m = Machine()
        m.put(0, mflo1_word(0).to_bytes(4, 'little'))
        m.lo1 = 0x123456789abcdef0
        m.run(0, stop=0x04)
        for n in range(31):
            self.assertEqual(m.reg[n], 0, f'GPR {n} written by rd==0 mflo1')
        self.assertEqual(m.lo1, 0x123456789abcdef0)


class Div1ControlTests(unittest.TestCase):
    """Deliberate wrong-result interpretations of div1 must be detected.

    Each control patches the interpreter source to compute the division
    differently and asserts the results then differ from the independent
    reference. A control fails (the defect escapes) when the wrong result is
    silently accepted.
    """
    # Operand pairs with negative operands so floor/unsigned interpretations
    # differ from the signed truncated reference.
    OPERANDS = [(-0x64, 10), (0x64, -10), (-10, 3), (10, -3), (-5, -7)]

    # The div1 handler block, unique by its DIV1-specific edge checks.
    DIV1_BLOCK = (
        "left, right = sx(a,32), sx(b,32)\n"
        "                quotient, remainder = self.div1_semantics(left, right)\n"
        "                self.lo1, self.hi1 = sx(quotient,32) & MASK64, sx(remainder,32) & MASK64\n"
        "                if rd: wordreg(rd, quotient)")

    def load_namespace(self, replacements):
        import ee_subset
        source = Path(ee_subset.__file__).read_text()
        for old, new in replacements:
            if source.count(old) != 1:
                raise AssertionError(f'mutation target not unique: {old[:60]}')
            source = source.replace(old, new)
        namespace = {}
        exec(compile(source, ee_subset.__file__, 'exec'), namespace)
        return namespace

    def run_with(self, namespace, left, right):
        m = namespace['Machine']()
        m.put(0, div1_word(5, 4).to_bytes(4, 'little'))
        m.reg[5] = left & MASK64
        m.reg[4] = right & MASK64
        m.run(0, stop=0x04)
        return m

    def test_floor_division_detected(self):
        mutant = self.DIV1_BLOCK.replace(
            "quotient, remainder = self.div1_semantics(left, right)",
            "quotient, remainder = left // right, left - (left // right) * right  # deliberate floor defect")
        ns = self.load_namespace([(self.DIV1_BLOCK, mutant)])
        for left, right in self.OPERANDS:
            if left % right == 0 or left // right >= 0:
                continue  # exact division or positive quotient: floor equals truncation
            m = self.run_with(ns, left, right)
            q, r = reference_div1(left, right)
            self.assertNotEqual((m.lo1, m.hi1), (sx(q, 32) & MASK64, sx(r, 32) & MASK64),
                                 f'floor division escaped for {left}/{right}')

    def test_unsigned_division_detected(self):
        mutant = self.DIV1_BLOCK.replace(
            "quotient, remainder = self.div1_semantics(left, right)",
            "quotient, remainder = ((left & 0xffffffff) // (right & 0xffffffff), 0)  # unsigned defect")
        ns = self.load_namespace([(self.DIV1_BLOCK, mutant)])
        for left, right in self.OPERANDS:
            m = self.run_with(ns, left, right)
            q, r = reference_div1(left, right)
            self.assertNotEqual((m.lo1, m.hi1), (sx(q, 32) & MASK64, sx(r, 32) & MASK64),
                                 f'unsigned division escaped for {left}/{right}')

    def test_wrong_pipeline_detected(self):
        # Writing the result into LO/HI instead of LO1/HI1 must be detected:
        # LO1/HI1 must stay zero while LO/HI receive the quotient/remainder.
        mutant = self.DIV1_BLOCK.replace(
            "self.lo1, self.hi1 = sx(quotient,32) & MASK64, sx(remainder,32) & MASK64",
            "self.lo, self.hi = sx(quotient,32) & MASK64, sx(remainder,32) & MASK64  # wrong pipeline")
        ns = self.load_namespace([(self.DIV1_BLOCK, mutant)])
        for left, right in self.OPERANDS:
            m = self.run_with(ns, left, right)
            self.assertEqual((m.lo1, m.hi1), (0, 0),
                             f'wrong-pipeline defect left LO1/HI1 set for {left}/{right}')
            q, r = reference_div1(left, right)
            self.assertEqual((m.lo, m.hi), (sx(q, 32) & MASK64, sx(r, 32) & MASK64),
                             f'wrong-pipeline defect missed LO/HI for {left}/{right}')

    def test_correct_reference_still_passes(self):
        # The unmutated interpreter must match the independent reference for
        # the same operand set (clean control for the class above).
        for left, right in self.OPERANDS:
            m = Machine()
            m.put(0, div1_word(5, 4).to_bytes(4, 'little'))
            m.reg[5] = left & MASK64
            m.reg[4] = right & MASK64
            m.run(0, stop=0x04)
            q, r = reference_div1(left, right)
            self.assertEqual((m.lo1, m.hi1), (sx(q, 32) & MASK64, sx(r, 32) & MASK64),
                             f'reference mismatch for {left}/{right}')


if __name__ == '__main__':
    unittest.main()
