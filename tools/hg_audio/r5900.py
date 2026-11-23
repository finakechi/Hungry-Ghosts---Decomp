#!/usr/bin/env python3
"""Independent R5900 (PS2 EE) disassembler subset.

Canonical lowercase text output, validated against the repository's existing
Ghidra-derived instruction listings for M1-M7 functions (thousands of
instructions). No third-party code. Unknown encodings raise ValueError so a
mistake can never be silently misreported.

Text conventions:
  - registers: zero, at, v0, v1, a0-a3, t0-t9, s0-s7, k0, k1, gp, sp, fp, ra
  - fp: f0-f31; cop2: vf0-vf31, vi0-vi31, ACC
  - hex immediates/offsets with 0x prefix; decimal registers untouched
"""
import struct

MASK32 = 0xFFFFFFFF

REG = ['zero', 'at', 'v0', 'v1', 'a0', 'a1', 'a2', 'a3',
       't0', 't1', 't2', 't3', 't4', 't5', 't6', 't7',
       's0', 's1', 's2', 's3', 's4', 's5', 's6', 's7',
       't8', 't9', 'k0', 'k1', 'gp', 'sp', 'fp', 'ra']


def r(n):
    return REG[n]


def imm16(word):
    return word & 0xFFFF


def simm16(word):
    v = word & 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def off(base, disp):
    v = (base + disp) & MASK32
    return f'0x{v:x}'


def li(word):
    return f'0x{(word & 0xffff):x}'


def _fmt_mem(base, offset, mnemonic, rt=None):
    if rt is None:
        return f'{mnemonic} {off(base, offset)}'
    return f'{mnemonic} {r(rt)},{off(base, offset)}'


def _fmt_mem_signed(base, offset, mnemonic, rt=None):
    v = base + offset
    sign = '' if offset >= 0 else '-'
    if rt is None:
        return f'{mnemonic} {sign}0x{abs(offset):x}({r(base)})'
    return f'{mnemonic} {r(rt)},{sign}0x{abs(offset):x}({r(base)})'


def _simm(v):
    return f'0x{v:x}' if v >= 0 else f'-0x{-v:x}'


def _target(addr, word):
    return f'0x{(addr + 4 + (simm16(word) << 2)) & MASK32:08x}'

def _branch(addr, word, mnemonic, rs, rt):
    if rs == 0 and rt == 0 and mnemonic in ('beq', 'beql'):
        return f'b {_target(addr, word)}'
    return f'{mnemonic} {r(rs)},{r(rt)},{_target(addr, word)}'


def _branch1(addr, word, mnemonic, rs):
    return f'{mnemonic} {r(rs)},{_target(addr, word)}'


def disasm_one(addr, word):
    """Disassemble a single 32-bit instruction word at address addr."""
    op = word >> 26
    rs = (word >> 21) & 31
    rt = (word >> 16) & 31
    rd = (word >> 11) & 31
    sa = (word >> 6) & 31
    f = word & 63
    # SPECIAL
    if op == 0:
        if word == 0:
            return 'nop'
        if f == 0x21:
            if rt == 0:
                return f'move {r(rd)},{r(rs)}'
            return f'addu {r(rd)},{r(rs)},{r(rt)}'
        if f == 0x2d:
            if rt == 0:
                return f'move {r(rd)},{r(rs)}'
            return f'daddu {r(rd)},{r(rs)},{r(rt)}'
        if f == 0x20:
            return f'add {r(rd)},{r(rs)},{r(rt)}'
        if f == 0x23:
            return f'subu {r(rd)},{r(rs)},{r(rt)}'
        if f == 0x22:
            return f'sub {r(rd)},{r(rs)},{r(rt)}'
        if f == 0x2c:
            return f'dadd {r(rd)},{r(rs)},{r(rt)}'
        if f == 0x2f:
            return f'dsubu {r(rd)},{r(rs)},{r(rt)}'
        if f == 0x2e:
            return f'dsub {r(rd)},{r(rs)},{r(rt)}'
        if f == 0x25:
            return f'or {r(rd)},{r(rs)},{r(rt)}'
        if f == 0x24:
            return f'and {r(rd)},{r(rs)},{r(rt)}'
        if f == 0x26:
            return f'xor {r(rd)},{r(rs)},{r(rt)}'
        if f == 0x27:
            return f'nor {r(rd)},{r(rs)},{r(rt)}'
        if f == 0x2a:
            return f'slt {r(rd)},{r(rs)},{r(rt)}'
        if f == 0x2b:
            return f'sltu {r(rd)},{r(rs)},{r(rt)}'
        if f == 0x0a:
            return f'movz {r(rd)},{r(rs)},{r(rt)}'
        if f == 0x0b:
            return f'movn {r(rd)},{r(rs)},{r(rt)}'
        if f == 0:
            return f'sll {r(rd)},{r(rt)},0x{sa:x}'
        if f == 2:
            return f'srl {r(rd)},{r(rt)},0x{sa:x}'
        if f == 3:
            return f'sra {r(rd)},{r(rt)},0x{sa:x}'
        if f == 0x38:
            return f'dsll {r(rd)},{r(rt)},0x{sa:x}'
        if f == 0x3a:
            return f'dsrl {r(rd)},{r(rt)},0x{sa:x}'
        if f == 0x3b:
            return f'dsra {r(rd)},{r(rt)},0x{sa:x}'
        if f == 0x3c:
            return f'dsll32 {r(rd)},{r(rt)},{sa}'
        if f == 0x3e:
            return f'dsrl32 {r(rd)},{r(rt)},{sa}'
        if f == 0x3f:
            return f'dsra32 {r(rd)},{r(rt)},{sa}'
        if f == 4:
            return f'sllv {r(rd)},{r(rt)},{r(rs)}'
        if f == 6:
            return f'srlv {r(rd)},{r(rt)},{r(rs)}'
        if f == 7:
            return f'srav {r(rd)},{r(rt)},{r(rs)}'
        if f == 0x14:
            return f'dsllv {r(rd)},{r(rt)},{r(rs)}'
        if f == 0x16:
            return f'dsrlv {r(rd)},{r(rt)},{r(rs)}'
        if f == 0x17:
            return f'dsrav {r(rd)},{r(rt)},{r(rs)}'
        if f == 8:
            if rd == 0:
                return f'jr {r(rs)}'
            return f'jalr {r(rd)},{r(rs)}'
        if f == 9:
            if rd == 31:
                return f'jalr {r(rs)}'
            return f'jalr {r(rd)},{r(rs)}'
        if f == 0x0c:
            return 'syscall 0x%x' % (word & 0xFFFFF)
        if f == 0x0d:
            return 'break 0x%x' % (word & 0xFFFFF)
        if f == 0x0f:
            return f'sync 0x{sa:x}'
        if f == 0x10:
            return f'mfhi {r(rd)}'
        if f == 0x11:
            return f'mthi {r(rs)}'
        if f == 0x12:
            return f'mflo {r(rd)}'
        if f == 0x13:
            return f'mtlo {r(rs)}'
        if f == 0x18:
            return f'mult {r(rd)},{r(rs)},{r(rt)}' if rd else f'mult {r(rs)},{r(rt)}'
        if f == 0x19:
            return f'multu {r(rd)},{r(rs)},{r(rt)}' if rd else f'multu {r(rs)},{r(rt)}'
        if f == 0x1a:
            return f'div {r(rd)},{r(rs)},{r(rt)}' if rd else f'div {r(rs)},{r(rt)}'
        if f == 0x1b:
            return f'divu {r(rd)},{r(rs)},{r(rt)}' if rd else f'divu {r(rs)},{r(rt)}'
        if f == 0x1c:
            return f'dmult {r(rs)},{r(rt)}'
        if f == 0x1d:
            return f'dmultu {r(rs)},{r(rt)}'
        if f == 0x1e:
            return f'ddiv {r(rs)},{r(rt)}'
        if f == 0x1f:
            return f'ddivu {r(rs)},{r(rt)}'
        if f == 0x30:
            return f'tge {r(rs)},{r(rt)}'
        if f == 0x31:
            return f'tgeu {r(rs)},{r(rt)}'
        if f == 0x32:
            return f'tlt {r(rs)},{r(rt)}'
        if f == 0x33:
            return f'tltu {r(rs)},{r(rt)}'
        if f == 0x34:
            return f'teq {r(rs)},{r(rt)}'
        if f == 0x36:
            return f'tne {r(rs)},{r(rt)}'
        raise ValueError(f'special {f:#x} at {addr:#x}')
    if op == 1:
        # REGIMM
        if rt == 1:
            return _branch1(addr, word, 'bgez', rs)
        if rt == 0x11:
            return _branch1(addr, word, 'bgezal', rs)
        if rt == 0:
            return _branch1(addr, word, 'bltz', rs)
        if rt == 0x10:
            return _branch1(addr, word, 'bltzal', rs)
        raise ValueError(f'regimm rt={rt:#x} at {addr:#x}')
    if op == 2:
        return f'j 0x{(addr & 0xF0000000) | ((word & 0x3FFFFFF) << 2):08x}'
    if op == 3:
        return f'jal 0x{(addr & 0xF0000000) | ((word & 0x3FFFFFF) << 2):08x}'
    if op == 4:
        return _branch(addr, word, 'beq', rs, rt)
    if op == 5:
        return _branch(addr, word, 'bne', rs, rt)
    if op == 6:
        return _branch1(addr, word, 'blez', rs)
    if op == 7:
        return _branch1(addr, word, 'bgtz', rs)
    if op == 8:
        return f'addi {r(rt)},{r(rs)},{_simm(simm16(word))}'
    if op == 9:
        if rs == 0:
            return f'li {r(rt)},{_simm(simm16(word))}'
        return f'addiu {r(rt)},{r(rs)},{_simm(simm16(word))}'
    if op == 0x19:
        return f'daddiu {r(rt)},{r(rs)},{_simm(simm16(word))}'
    if op == 0x0a:
        return f'slti {r(rt)},{r(rs)},0x{simm16(word):x}'
    if op == 0x0b:
        return f'sltiu {r(rt)},{r(rs)},0x{imm16(word):x}'
    if op == 0x0c:
        return f'andi {r(rt)},{r(rs)},0x{imm16(word):x}'
    if op == 0x0d:
        return f'ori {r(rt)},{r(rs)},0x{imm16(word):x}'
    if op == 0x0e:
        return f'xori {r(rt)},{r(rs)},0x{imm16(word):x}'
    if op == 0x0f:
        return f'lui {r(rt)},0x{imm16(word):x}'
    if op == 0x10:
        rs = (word >> 21) & 31
        rt = (word >> 16) & 31
        rd = (word >> 11) & 31
        if rs == 0x10:
            funct = word & 63
            if funct == 0x38:
                return 'ei'
            if funct == 0x39:
                return 'di'
            raise ValueError(f'cop0 co funct={funct:#x} at {addr:#x}')
        if (word & 0x7ff) or (word & 0x1c0000):
            raise ValueError(f'cop0 op {word:#x} at {addr:#x}')
        if rs == 0:
            return f'mfc0 {r(rt)},{_cop0(rd)}'
        if rs == 4:
            return f'mtc0 {r(rt)},{_cop0(rd)}'
        raise ValueError(f'cop0 rs={rs:#x} at {addr:#x}')
    if op == 0x11:
        return _cop1(addr, word)
    if op == 0x12:
        return _cop2(addr, word)
    if op == 0x14:
        return _branch(addr, word, 'beql', rs, rt)
    if op == 0x15:
        return _branch(addr, word, 'bnel', rs, rt)
    if op == 0x16:
        return _branch1(addr, word, 'blezl', rs)
    if op == 0x17:
        return _branch1(addr, word, 'bgtzl', rs)
    if op == 0x1c:
        return _mmi2(word)  # SPECIAL2: mult/div/madd/mfhi1 family
    if op == 0x1d:
        return _mmi0(word)  # MMI0: parallel integer ops
    if op == 0x1e:
        return f'lq {r(rt)},{_simm(simm16(word))}({r(rs)})'
    if op == 0x1f:
        return f'sq {r(rt)},{_simm(simm16(word))}({r(rs)})'
    if op == 0x20:
        return f'lb {r(rt)},{_simm(simm16(word))}({r(rs)})'
    if op == 0x21:
        return f'lh {r(rt)},{_simm(simm16(word))}({r(rs)})'
    if op == 0x22:
        return f'lwl {r(rt)},{_simm(simm16(word))}({r(rs)})'
    if op == 0x23:
        return f'lw {r(rt)},{_simm(simm16(word))}({r(rs)})'
    if op == 0x24:
        return f'lbu {r(rt)},{_simm(simm16(word))}({r(rs)})'
    if op == 0x25:
        return f'lhu {r(rt)},{_simm(simm16(word))}({r(rs)})'
    if op == 0x26:
        return f'lwr {r(rt)},{_simm(simm16(word))}({r(rs)})'
    if op == 0x27:
        return f'lwu {r(rt)},{_simm(simm16(word))}({r(rs)})'
    if op == 0x28:
        return f'sb {r(rt)},{_simm(simm16(word))}({r(rs)})'
    if op == 0x29:
        return f'sh {r(rt)},{_simm(simm16(word))}({r(rs)})'
    if op == 0x2a:
        return f'swl {r(rt)},{_simm(simm16(word))}({r(rs)})'
    if op == 0x2b:
        return f'sw {r(rt)},{_simm(simm16(word))}({r(rs)})'
    if op == 0x2e:
        return f'swr {r(rt)},{_simm(simm16(word))}({r(rs)})'
    if op == 0x2f:
        return f'cache {rt},{_simm(simm16(word))}({r(rs)})'
    if op == 0x30:
        return f'll {r(rt)},{_simm(simm16(word))}({r(rs)})'
    if op == 0x31:
        return f'lwc1 $f{rt},{_simm(simm16(word))}({r(rs)})'
    if op == 0x36:
        return f'lqc2 $vf{rt},{_simm(simm16(word))}({r(rs)})'
    if op == 0x37:
        return f'ld {r(rt)},{_simm(simm16(word))}({r(rs)})'
    if op == 0x38:
        return f'sc {r(rt)},{_simm(simm16(word))}({r(rs)})'
    if op == 0x39:
        return f'swc1 $f{rt},{_simm(simm16(word))}({r(rs)})'
    if op == 0x3a:
        return f'sdl {r(rt)},{_simm(simm16(word))}({r(rs)})'
    if op == 0x3b:
        return f'sdr {r(rt)},{_simm(simm16(word))}({r(rs)})'
    if op == 0x3e:
        return f'sqc2 $vf{rt},{_simm(simm16(word))}({r(rs)})'
    if op == 0x3f:
        return f'sd {r(rt)},{_simm(simm16(word))}({r(rs)})'
    raise ValueError(f'opcode {op:#x} at {addr:#x}')


def _cop0(rd):
    names = {0: 'Index', 1: 'Random', 2: 'EntryLo0', 3: 'EntryLo1', 8: 'BadVAddr',
             9: 'Count', 12: 'Status', 13: 'Cause', 14: 'EPC', 16: 'Config',
             23: 'BadPAddr', 24: 'Debug', 30: 'ErrorEPC'}
    return names.get(rd, str(rd))


def _cop1(addr, word):
    rs = (word >> 21) & 31
    ft = (word >> 16) & 31
    ndtf = word & 3
    f = word & 63
    if rs in (0x10, 0x11):  # fmt S (PS2 FPU uses both codes)
        fd = (word >> 6) & 31
        fs = (word >> 11) & 31
        if f == 0:
            return f'add.s $f{fd},$f{fs},$f{ft}'
        if f == 1:
            return f'sub.s $f{fd},$f{fs},$f{ft}'
        if f == 2:
            return f'mul.s $f{fd},$f{fs},$f{ft}'
        if f == 3:
            return f'div.s $f{fd},$f{fs},$f{ft}'
        if f == 4:
            return f'sqrt.s $f{fd},$f{fs}'
        if f == 5:
            return f'abs.s $f{fd},$f{fs}'
        if f == 6:
            return f'mov.s $f{fd},$f{fs}'
        if f == 7:
            return f'neg.s $f{fd},$f{fs}'
        if f == 0x18:
            return f'adda.s $f{fd},$f{fs},$f{ft}'
        if f == 0x19:
            return f'suba.s $f{fd},$f{fs},$f{ft}'
        if f == 0x1a:
            return f'mula.s $f{fs},$f{ft}'
        if f == 0x1c:
            return f'madd.s $f{fd},$f{fs},$f{ft}'
        if f == 0x1d:
            return f'msub.s $f{fd},$f{fs},$f{ft}'
        if f == 0x1e:
            return f'madda.s $f{fd},$f{fs},$f{ft}'
        if f == 0x1f:
            return f'msuba.s $f{fd},$f{fs},$f{ft}'
        if f == 0x24:
            return f'cvt.w.s $f{fd},$f{fs}'
        if f == 0x28:
            return f'max.s $f{fd},$f{fs},$f{ft}'
        if f == 0x29:
            return f'min.s $f{fd},$f{fs},$f{ft}'
        if f == 0x0d:
            return f'trunc.w.s $f{fd},$f{fs}'
        if f == 0x0f:
            return f'floor.w.s $f{fd},$f{fs}'
        if f == 0x0e:
            return f'ceil.w.s $f{fd},$f{fs}'
        if f == 0x0c:
            return f'round.w.s $f{fd},$f{fs}'
        if f == 0x15:
            return f'recip.s $f{fd},$f{fs}'
        if f == 0x16:
            return f'rsqrt.s $f{fd},$f{fs}'
        if f == 0x30:
            return f'c.f.s $f{fs},$f{ft}'
        if f == 0x38:
            return f'c.sf.s $f{fs},$f{ft}'
        if f in (0x32, 0x3a):
            return f'c.eq.s $f{fs},$f{ft}'
        if f in (0x34, 0x3c):
            return f'c.lt.s $f{fs},$f{ft}'
        if f in (0x36, 0x3e):
            return f'c.le.s $f{fs},$f{ft}'
        raise ValueError(f'cop1 fmt-s f={f:#x} at {addr:#x}')
    if rs == 0x14:  # fmt W
        fd = (word >> 6) & 31
        fs = (word >> 11) & 31
        if f == 0x20:
            return f'cvt.s.w $f{fd},$f{fs}'
        raise ValueError(f'cop1 fmt-w f={f:#x} at {addr:#x}')
    if rs == 0x08:  # BC
        cc = (word >> 18) & 7
        ndtf = (word >> 16) & 3
        if cc == 0:
            if ndtf == 0:
                return f'bc1f {_target(addr, word)}'
            if ndtf == 1:
                return f'bc1t {_target(addr, word)}'
            if ndtf == 2:
                return f'bc1fl {_target(addr, word)}'
            if ndtf == 3:
                return f'bc1tl {_target(addr, word)}'
        raise ValueError(f'cop1 bc cc={cc} ndtf={ndtf} at {addr:#x}')
    if rs == 0:
        rt = (word >> 16) & 31
        if word & 0x7ff == 0:
            return f'mfc1 {r(rt)},$f{(word >> 11) & 31}'
        raise ValueError(f'cop1 mfc1 at {addr:#x}')
    if rs == 4:
        rt = (word >> 16) & 31
        if word & 0x7ff == 0:
            if rt == 0:
                return f'clear f{(word >> 11) & 31}'
            return f'mtc1 {r(rt)},$f{(word >> 11) & 31}'
        raise ValueError(f'cop1 mtc1 at {addr:#x}')
    if rs == 2:
        rt = (word >> 16) & 31
        if word & 0x7ff == 0:
            return f'cfc1 {r(rt)},$f{(word >> 11) & 31}'
        raise ValueError(f'cop1 cfc1 at {addr:#x}')
    if rs == 6:
        rt = (word >> 16) & 31
        if word & 0x7ff == 0:
            return f'ctc1 {r(rt)},$f{(word >> 11) & 31}'
        raise ValueError(f'cop1 ctc1 at {addr:#x}')
    if rs == 0x11:  # fmt S comparisons
        fd = (word >> 6) & 31
        fs = (word >> 11) & 31
        if f == 0x30 and ndtf == 0:
            return f'c.f.s $f{fs},$f{ft}'
        if f == 0x38 and ndtf == 0:
            return f'c.sf.s $f{fs},$f{ft}'
        if f in (0x32, 0x3a) and ndtf == 0:
            return f'c.eq.s $f{fs},$f{ft}'
        if f in (0x34, 0x3c) and ndtf == 0:
            return f'c.lt.s $f{fs},$f{ft}'
        if f in (0x36, 0x3e) and ndtf == 0:
            return f'c.le.s $f{fs},$f{ft}'
        raise ValueError(f'cop1 cmp f={f:#x} at {addr:#x}')
    raise ValueError(f'cop1 rs={rs:#x} at {addr:#x}')
    raise ValueError(f'cop1 rs={rs:#x} at {addr:#x}')


def _cop2(addr, word):
    if word == 0x7000003f:  # break fallback not used
        raise ValueError(f'cop2 at {addr:#x}')
    if (word >> 25) & 1:  # lqc2/sqcc2 handled by opcode 0x36/0x3e already
        raise ValueError(f'cop2 word {word:#x} at {addr:#x}')
    op = (word >> 21) & 0x1f
    rt = (word >> 16) & 31
    rd = (word >> 11) & 31
    # qmfc2 / qmtc2
    if word & 0x7ff == 0:
        if op == 0:
            return f'qmfc2 {r(rt)},$vf{rd}'
        if op == 4:
            return f'qmtc2 {r(rt)},$vf{rd}'
        raise ValueError(f'cop2 qm at {addr:#x}')
    # vcallms / vcallmsr
    if (word >> 6) & 0x1f == 0 and word & 0x1f == 0:
        if op == 0x18:
            return f'vcallms {(word >> 11) & 0x7fff:#x}'
        if op == 0x19:
            return f'vcallmsr $vi{(word >> 11) & 31}'
        raise ValueError(f'cop2 vcall at {addr:#x}')
    raise ValueError(f'cop2 op={op:#x} word={word:#x} at {addr:#x}')


def _mmi2(word):
    """SPECIAL2 (opcode 0x1c): EE multiply/divide/MAC/64-bit family.

    Funct codes derived from the corpus: 0x09 pcpyld, 0x12 mflo1,
    0x18 mult1, 0x1a div1 are directly observed; the remaining entries
    follow the standard EE layout and stay unobserved-but-decodable.
    """
    f = word & 63
    rd = (word >> 11) & 31
    rs = (word >> 21) & 31
    rt = (word >> 16) & 31
    if f == 0x00:
        return f'madd {r(rd)},{r(rs)},{r(rt)}' if rd else f'madd {r(rs)},{r(rt)}'
    if f == 0x01:
        return f'maddu {r(rd)},{r(rs)},{r(rt)}' if rd else f'maddu {r(rs)},{r(rt)}'
    if f == 0x04:
        return f'mult {r(rd)},{r(rs)},{r(rt)}' if rd else f'mult {r(rs)},{r(rt)}'
    if f == 0x05:
        return f'multu {r(rd)},{r(rs)},{r(rt)}' if rd else f'multu {r(rs)},{r(rt)}'
    if f == 0x06:
        return f'div {r(rd)},{r(rs)},{r(rt)}' if rd else f'div {r(rs)},{r(rt)}'
    if f == 0x07:
        return f'divu {r(rd)},{r(rs)},{r(rt)}' if rd else f'divu {r(rs)},{r(rt)}'
    if f == 0x08:
        return f'mfhi1 {r(rd)}'
    if f == 0x09:
        return f'pcpyld {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x0a:
        return f'mflo1 {r(rd)}'
    if f == 0x0b:
        return f'mtlo1 {r(rs)}'
    if f == 0x10:
        return f'mfhi1 {r(rd)}'
    if f == 0x11:
        return f'mthi1 {r(rs)}'
    if f == 0x12:
        return f'mflo1 {r(rd)}'
    if f == 0x13:
        return f'mtlo1 {r(rs)}'
    if f == 0x18:
        return f'mult1 {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x19:
        return f'multu1 {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x1a:
        return f'div1 {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x1b:
        return f'divu1 {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x20:
        return f'madd1 {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x21:
        return f'maddu1 {r(rd)},{r(rs)},{r(rt)}'
    raise ValueError(f'mmi2 f={f:#x} word={word:#x}')


def _mmi0(word):
    """MMI0 (opcode 0x1d): EE parallel integer operations."""
    f = word & 63
    rd = (word >> 11) & 31
    rs = (word >> 21) & 31
    rt = (word >> 16) & 31
    sa = (word >> 6) & 31
    if f == 0x00:
        return f'paddw {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x01:
        return f'psubw {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x02:
        return f'pcgtw {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x03:
        return f'pmaxw {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x04:
        return f'paddh {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x05:
        return f'psubh {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x06:
        return f'pcgth {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x07:
        return f'pmaxh {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x08:
        return f'paddb {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x09:
        return f'psubb {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x0a:
        return f'pcgtb {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x10:
        return f'paddsw {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x11:
        return f'psubsw {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x12:
        return f'pextlw {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x13:
        return f'ppacw {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x14:
        return f'paddsh {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x15:
        return f'psubsh {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x16:
        return f'pextlh {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x17:
        return f'ppach {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x18:
        return f'paddsb {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x19:
        return f'psubsb {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x1a:
        return f'pextl5 {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x1b:
        return f'ppac5 {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x1e:
        return f'pextub {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x1f:
        return f'ppacb {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x28:
        return f'pmfhi {r(rd)}'
    if f == 0x29:
        return f'pmflo {r(rd)}'
    if f == 0x2a:
        return f'pinth {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x2c:
        return f'pcpyld {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x2e:
        return f'pexcw {r(rd)},{r(rt)}'
    if f == 0x30:
        return f'pmfhl.lh {r(rd)}'
    if f == 0x31:
        return f'pmfhl.sh {r(rd)}'
    if f == 0x33:
        return f'psllh {r(rd)},{r(rt)},{sa}'
    if f == 0x35:
        return f'psrlh {r(rd)},{r(rt)},{sa}'
    if f == 0x37:
        return f'psrah {r(rd)},{r(rt)},{sa}'
    if f == 0x3c:
        return f'psllw {r(rd)},{r(rt)},{sa}'
    if f == 0x3e:
        return f'psrlw {r(rd)},{r(rt)},{sa}'
    if f == 0x2d:
        return f'pcpyud {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x0c:
        return f'pminw {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x0d:
        return f'paddw? 0x{word:x}'
    if f == 0x0e:
        return f'pminh {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x0f:
        return f'pminb {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x1c:
        return f'pextuw {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x1d:
        return f'pextuh {r(rd)},{r(rs)},{r(rt)}'
    if f == 0x2b:
        return f'pexeh {r(rd)},{r(rt)}'
    if f == 0x2f:
        return f'pexch {r(rd)},{r(rt)}'
    if f == 0x3b:
        return f'prevh {r(rd)},{r(rt)}'
    if f == 0x3d:
        return f'prot3w {r(rd)},{r(rt)}'
    raise ValueError(f'mmi0 f={f:#x} word={word:#x}')


def disasm_bytes(addr, data):
    """Yield (address, word, text) for each instruction word in data."""
    for i in range(0, len(data) - 3, 4):
        word = struct.unpack_from('<I', data, i)[0]
        yield addr + i, word, disasm_one(addr + i, word)
