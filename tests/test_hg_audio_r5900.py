"""Validate the independent R5900 disassembler against known-good listings.

The repository's M1-M7 analysis/*/functions.json files contain Ghidra-derived
instruction text for thousands of instructions. This test normalizes both
Ghidra's formatting (whitespace, '$'/'_' decorations, pseudo-instruction
spellings) and the disassembler's canonical output, then requires semantic
equality for every non-COP2 instruction. COP2 (VU macro) words are counted
and skipped: the audio subsystem never uses them and they need a separate
VU macro decoder.
"""
import glob
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools' / 'hg_audio'))
from r5900 import disasm_one  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

_IMM4 = ('lui', 'ori', 'andi', 'xori', 'addiu', 'addi', 'sltiu', 'slti')


GPR_INDEX = {'zero': 0, 'at': 1, 'v0': 2, 'v1': 3, 'a0': 4, 'a1': 5,
             'a2': 6, 'a3': 7, 't0': 8, 't1': 9, 't2': 10, 't3': 11,
             't4': 12, 't5': 13, 't6': 14, 't7': 15, 's0': 16, 's1': 17,
             's2': 18, 's3': 19, 's4': 20, 's5': 21, 's6': 22, 's7': 23,
             't8': 24, 't9': 25, 'k0': 26, 'k1': 27, 'gp': 28, 'sp': 29,
             'fp': 30, 'ra': 31}


def _normalize(t):
    t = t.lstrip('_').replace(chr(36), '')
    t = re.sub(r'\s+', '', t)
    t = t.lower()
    t = re.sub(r's8(?=[^a-z0-9])', 'fp', t)
    m = re.fullmatch(r'li([a-z0-9]+),(-?0x[0-9a-f]+)', t)
    if m:
        t = 'addiu%s,zero,%s' % (m[1], m[2])
    m = re.fullmatch(r'move([a-z0-9]+),([a-z0-9]+)', t)
    if m:
        t = 'daddu%s,%s,zero' % (m[1], m[2])
    m = re.fullmatch(r'b(0x[0-9a-f]+)', t)
    if m:
        t = 'beqzero,zero,%s' % m[1]
    m = re.fullmatch(r'clearf(\d+)', t)
    if m:
        t = 'mtc1zero,f%s' % m[1]
    # Ghidra sometimes names FP operands of lwc1/swc1 with GPR names
    m = re.match(r'(lwc1|swc1)([a-z0-9]+),', t)
    if m and m[2] in GPR_INDEX:
        t = '%sf%d,' % (m[1], GPR_INDEX[m[2]]) + t[len(m[1]) + len(m[2]) + 1:]
    if t in ('sllzero,zero,0', 'sllzero,zero,0x0'):
        t = 'nop'
    # Ghidra sign-extends ori/andi/xori immediates; render unsigned
    t = re.sub(r'^(ori|andi|xori)([a-z0-9]+),([a-z0-9]+),-0x([0-9a-f]{1,4})$',
               lambda m: '%s%s,%s,0x%04x' % (m[1], m[2], m[3], (-int(m[4], 16)) & 0xffff), t)
    # four-digit immediates (lui-style): pad to 4 hex digits
    t = re.sub(r'^(%s)([a-z0-9]+),(0x)([0-9a-f]{1,3})$' % '|'.join(_IMM4),
               lambda m: '%s%s,0x%s' % (m[1], m[2], m[4].zfill(4)), t)
    t = re.sub(r'^(%s)([a-z0-9]+),([a-z0-9]+),(0x)([0-9a-f]{1,3})$' % '|'.join(_IMM4),
               lambda m: '%s%s,%s,0x%s' % (m[1], m[2], m[3], m[5].zfill(4)), t)
    # short shift amounts: render as plain decimal before any padding
    t = re.sub(r',(0x)([0-9a-f]{1,2})$',
               lambda m: ',%d' % int(m[2], 16), t)
    # branch/jump targets: pad to 8 hex digits
    t = re.sub(r',(0x[0-9a-f]{1,7})$',
               lambda m: ',' + m.group(1)[:2] + '0' * (8 - len(m.group(1))) + m.group(1)[2:], t)
    t = t.replace('-0x', 'neg')
    t = re.sub(r',zero,zero$', '', t)
    t = re.sub(r'jalr([a-z0-9]+)$', r'jalrra,\1', t)
    return t


def _samples():
    for path in sorted(glob.glob(str(REPO / 'analysis' / '*' / 'functions.json'))):
        with open(path) as fp:
            d = json.load(fp)
        for f in d.get('functions', []):
            for ins in f.get('instructions', []):
                yield ins


class SyncFormatting(unittest.TestCase):
    """SPECIAL funct 0x0f: stype 0 prints bare, nonzero stype keeps it.

    Reference spellings come from the Ghidra listings: 'sync' for word
    0x0000000f (analysis/game_lifecycle, 0x0011df68) and 'sync 0x10' for
    word 0x0000040f (analysis/player_constraint).
    """

    def test_zero_stype_is_bare(self):
        self.assertEqual(disasm_one(0x0011df68, 0x0000000f), 'sync')

    def test_nonzero_stype_keeps_operand(self):
        self.assertEqual(disasm_one(0, 0x0000040f), 'sync 0x10')
        self.assertEqual(disasm_one(0, 0x0000004f), 'sync 0x1')
        self.assertEqual(disasm_one(0, 0x000007cf), 'sync 0x1f')

    def test_zero_and_nonzero_stay_distinct(self):
        self.assertNotEqual(_normalize(disasm_one(0, 0x0000000f)),
                            _normalize(disasm_one(0, 0x0000040f)))


class R5900Validation(unittest.TestCase):
    def test_matches_known_listings(self):
        total = mismatch = cop2 = 0
        for ins in _samples():
            if ins['text'].startswith('???') or ins['text'].startswith('cop1 '):
                continue  # Ghidra marks inline data / unsupported COP1 words
            addr = int(ins['address'], 16)
            word = int.from_bytes(bytes.fromhex(ins['bytes']), 'little')
            if word >> 26 == 0x12:
                cop2 += 1
                continue
            try:
                mine = disasm_one(addr, word)
            except ValueError as e:
                self.fail(f"{ins['address']}: {e} [ghidra: {ins['text']}]")
            total += 1
            if _normalize(ins['text']) != _normalize(mine):
                mismatch += 1
                self.fail(
                    f"semantic mismatch at {ins['address']} ({ins['bytes']}):\n"
                    f"  ghidra: {ins['text']!r}\n  mine:   {mine!r}")
        self.assertGreater(total, 10000, 'validation corpus unexpectedly small')
        self.assertGreater(cop2, 0, 'COP2 words expected in validation corpus')
        self.assertEqual(mismatch, 0)


if __name__ == '__main__':
    unittest.main()
