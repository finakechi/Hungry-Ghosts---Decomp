"""Strict integer R5900 subset for investigated M1/M2 functions, not an emulator.

Supports only observed operations; unknown opcodes and unmapped reads fail.
Models word sign extension, low 64-bit arithmetic, 128-bit lq/sq, and delay slots.
External CDVD calls must be supplied by the caller; no PS2 hardware is emulated.
"""
MASK64 = (1 << 64) - 1


def fp_value(bits):
    """Bounded finite EE/VU arithmetic oracle, independent of host floats.

    M6 uses normal inputs, positive square/add results and chop rounding.
    No exceptional flags, non-finite arithmetic or VU timing is modeled.
    """
    from fractions import Fraction
    exponent = (bits >> 23) & 255
    if exponent == 255:
        raise ValueError('non-finite FP operand outside investigated subset')
    if exponent == 0:
        return Fraction(0)  # default captured profile: denormals are zero
    value = Fraction((bits & 0x7fffff) | 0x800000) * Fraction(2) ** (exponent - 150)
    return -value if bits & 0x80000000 else value


def fp_chop(value, root=False):
    """Search representable values using exact rational comparisons."""
    if value < 0:
        raise ValueError('negative arithmetic outside investigated FP subset')
    low, high = 0, 0x7f7fffff
    while low < high:
        middle = (low + high + 1) // 2
        candidate = fp_value(middle)
        if (candidate * candidate if root else candidate) <= value:
            low = middle
        else:
            high = middle - 1
    return low if low >= 0x800000 else 0


def sx(value, bits):
    value &= (1 << bits) - 1
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


class Machine:
    def __init__(self):
        self.mem = {}
        self.reg = [0] * 32
        self.hi = self.lo = 0
        self.pc = 0
        self.pending = None
        self.hooks = {}
        self.steps = 0
        self.executed = set()
        self.fpr = [0] * 32
        self.acc = 0
        self.fp_condition = False
        self.vf = [0] * 32
        self.vf[0] = 0x3f800000 << 96
        self.q = 0

    def put(self, address, data):
        self.mem.update((address + i, x) for i, x in enumerate(data))

    def get(self, address, size):
        try:
            return bytes(self.mem[address + i] for i in range(size))
        except KeyError as exc:
            raise ValueError(f'unmapped read {address:#x}+{size}') from exc

    def load(self, address, size):
        if size > 1 and address % size:
            raise ValueError('unaligned load')
        return int.from_bytes(self.get(address, size), 'little')

    def store(self, address, value, size):
        if size > 1 and address % size:
            raise ValueError('unaligned store')
        self.put(address, (value & ((1 << (size * 8)) - 1)).to_bytes(size,'little'))

    def setreg(self, n, value):
        if n:
            # Scalar writes affect the low 64-bit lane. Upper lane is preserved.
            self.reg[n] = (self.reg[n] & ~MASK64) | (value & MASK64)

    def run(self, start, stop=0x00fff000, limit=20000):
        self.pc, self.pending, self.steps = start, None, 0
        self.reg[31] = stop
        while self.pc != stop:
            self.steps += 1
            if self.steps > limit:
                raise ValueError('instruction limit (possible nonterminating read retry)')
            if self.pc in self.hooks:
                if self.pending is not None:
                    raise ValueError('hook in delay slot')
                self.setreg(2, self.hooks[self.pc](self))
                self.pc = self.reg[31] & MASK64
                continue
            pc = self.pc
            self.executed.add(pc)
            word = self.load(pc, 4)
            op, rs, rt, rd, sa, fn = word >> 26, (word >> 21) & 31, (word >> 16) & 31, (word >> 11) & 31, (word >> 6) & 31, word & 63
            imm = sx(word & 0xffff, 16)
            a, b = self.reg[rs] & MASK64, self.reg[rt] & MASK64
            target = None
            def wordreg(n, value):
                self.setreg(n, sx(value, 32))
            if op == 0:
                if fn == 0: wordreg(rd, b << sa)
                elif fn == 2: wordreg(rd, (b & 0xffffffff) >> sa)
                elif fn == 3: wordreg(rd, sx(b,32) >> sa)
                elif fn == 0x3c: self.setreg(rd,b << (sa+32))
                elif fn == 0x3e: self.setreg(rd,b >> (sa+32))
                elif fn == 0x3f: self.setreg(rd,sx(b,64) >> (sa+32))
                elif fn == 0x38: self.setreg(rd,b << sa)
                elif fn == 8: target = a
                elif fn == 9:  # jalr: target is read before writing link (rd may equal rs)
                    target = a
                    self.setreg(rd,pc+8)
                elif fn == 0x18:
                    product = sx(a,32) * sx(b,32)
                    self.lo, self.hi = sx(product,32) & MASK64, sx(product >> 32,32) & MASK64
                    wordreg(rd, product)
                elif fn == 0x21: wordreg(rd,a+b)
                elif fn == 0x23: wordreg(rd,a-b)
                elif fn == 0x25: self.setreg(rd,a|b)
                elif fn == 0x24: self.setreg(rd,a&b)
                elif fn == 0x2a: self.setreg(rd,int(sx(a,64)<sx(b,64)))
                elif fn == 0x2b: self.setreg(rd,int(a<b))
                elif fn == 0x2d: self.setreg(rd,a+b)
                else: raise ValueError(f'unsupported SPECIAL {word:08x} at {pc:08x}')
            elif op == 1 and rt == 1:
                target = pc + 4 + imm*4 if sx(a,64) >= 0 else pc + 8
            elif op in (2,3):
                target = ((pc+4) & 0xf0000000) | ((word & 0x3ffffff) << 2)
                if op == 3:self.setreg(31,pc+8)
            elif op in (4,5):
                take = a == b if op == 4 else a != b
                target = pc+4+imm*4 if take else pc+8
            elif op == 9: wordreg(rt,a+imm)
            elif op == 10: self.setreg(rt,int(sx(a,64)<imm))
            elif op == 11: self.setreg(rt, int(a < (imm & MASK64)))
            elif op == 12:self.setreg(rt,a & (word&0xffff))
            elif op == 13:self.setreg(rt,a | (word&0xffff))
            elif op == 15: wordreg(rt,(word & 0xffff)<<16)
            elif op == 0x1c and fn == 9 and sa == 14:  # pcpyld
                if rd:self.reg[rd] = (a << 64) | b
            elif op in (0x31, 0x39):  # lwc1 / swc1
                address = (a + imm) & 0xffffffff
                if op == 0x31:self.fpr[rt] = self.load(address,4)
                else:self.store(address,self.fpr[rt],4)
            elif op == 0x11:  # investigated COP1 operations only
                fs, ft, fd = rd, rt, sa
                if rs == 0:wordreg(rt,self.fpr[fs])
                elif rs == 4:self.fpr[fs] = self.reg[rt] & 0xffffffff
                elif rs == 8 and rt in (0,1):
                    take = self.fp_condition == bool(rt)
                    target = pc+4+imm*4 if take else pc+8
                elif rs == 16:
                    if fn == 6:self.fpr[fd] = self.fpr[fs]
                    elif fn == 7:self.fpr[fd] = self.fpr[fs] ^ 0x80000000
                    elif fn in (0x34,0x36):
                        x,y = fp_value(self.fpr[fs]),fp_value(self.fpr[ft])
                        self.fp_condition = x < y if fn == 0x34 else x <= y
                    elif fn in (0x1a,0x1c):
                        product = fp_chop(fp_value(self.fpr[fs])*fp_value(self.fpr[ft]))
                        if fn == 0x1a:self.acc = product
                        else:self.fpr[fd] = fp_chop(fp_value(product)+fp_value(self.acc))
                    else:raise ValueError(f'unsupported COP1 {word:08x} at {pc:08x}')
                else:raise ValueError(f'unsupported COP1 {word:08x} at {pc:08x}')
            elif op == 0x12:  # bounded macro VU sequence at 00143530
                if rs == 5:self.vf[rd] = self.reg[rt]  # qmtc2
                elif rs == 1:
                    if rt:self.reg[rt] = self.vf[rd]  # qmfc2 (full 128 bits)
                elif word == 0x4a0403bd:self.q = fp_chop(abs(fp_value(self.vf[4]&0xffffffff)),root=True)
                elif word == 0x4a0003bf:pass  # vwaitq: functional result already available
                elif word == 0x4b000120:
                    self.vf[4] = (self.vf[4] & ~0xffffffff) | self.q  # vf0.x + Q
                else:raise ValueError(f'unsupported COP2 {word:08x} at {pc:08x}')
            elif op in (0x20,0x24,0x21,0x25,0x23,0x37,0x1e):
                address = (a+imm) & 0xffffffff
                size = {0x20:1,0x24:1,0x21:2,0x25:2,0x23:4,0x37:8,0x1e:16}[op]
                v = self.load(address,size)
                if op == 0x1e: self.reg[rt] = v if rt else 0
                else: self.setreg(rt,sx(v,size*8) if op in (0x20,0x21,0x23) else v)
            elif op in (0x28,0x29,0x2b,0x3f,0x1f):
                size = {0x28:1,0x29:2,0x2b:4,0x3f:8,0x1f:16}[op]
                self.store((a+imm)&0xffffffff,self.reg[rt],size)
            else:
                raise ValueError(f'unsupported instruction {word:08x} at {pc:08x}')
            if self.pending is not None:
                if target is not None:
                    raise ValueError('branch in delay slot unsupported')
                self.pc, self.pending = self.pending, None
            else:
                self.pc, self.pending = pc+4, target
            self.reg[0] = 0
        return sx(self.reg[2],32)
