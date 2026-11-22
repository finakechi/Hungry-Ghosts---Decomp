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


def fp_signed_chop(value):
    """Finite signed arithmetic with zero flushed as in the bounded oracle."""
    bits = fp_chop(abs(value))
    return bits | (0x80000000 if value < 0 and bits else 0)


def fp_signed_nearest(value):
    """Finite signed round-to-nearest-even, used only for EE DIV.S.

    The comparison reference is PCSX2 v2.8.2 with default settings, whose EE
    division rounding mode (Nearest) differs from its other FPU operations
    (Chop). M8 retail captures of quaternion normalization 001440c0 confirm it.
    """
    magnitude = abs(value)
    low = fp_chop(magnitude)
    if low < 0x7f7fffff and magnitude != fp_value(low):
        below, above = magnitude - fp_value(low), fp_value(low + 1) - magnitude
        if above < below or (above == below and low & 1):
            low += 1
    if low < 0x800000:
        low = 0  # denormal results flush to zero in the captured profile
    return low | (0x80000000 if value < 0 and low else 0)


def fp_ee_add(left, right, subtract=False):
    """Finite EE addition with one alignment guard bit, then chop rounding.

    EE cancellation differs from IEEE single precision: discarded alignment
    bits must not reappear when a difference is normalized. This is functional
    arithmetic only; exceptional values and FCR31 flags remain unsupported.
    """
    fp_value(left);fp_value(right)  # fail closed before masking an operand
    difference=((left >> 23)&255)-((right >> 23)&255)
    shift=abs(difference)
    if shift:
        mask=0x80000000 if shift>=25 else (0xffffffff << (shift-1))&0xffffffff
        if difference>0:right&=mask
        else:left&=mask
    x,y=fp_value(left),fp_value(right)
    return fp_signed_chop(x-y if subtract else x+y)


class Machine:
    def __init__(self):
        self.mem = {}
        self.reg = [0] * 32
        self.hi = self.lo = 0
        self.hi1 = self.lo1 = 0
        self.cop0_status = None
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
        self.vacc = [0] * 4
        self.q = 0
        self.trace = None  # optional read-only instruction-boundary observer

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
            if self.trace is not None:self.trace(self)
            self.executed.add(pc)
            word = self.load(pc, 4)
            op, rs, rt, rd, sa, fn = word >> 26, (word >> 21) & 31, (word >> 16) & 31, (word >> 11) & 31, (word >> 6) & 31, word & 63
            imm = sx(word & 0xffff, 16)
            a, b = self.reg[rs] & MASK64, self.reg[rt] & MASK64
            target = None
            annul_delay = False
            def wordreg(n, value):
                self.setreg(n, sx(value, 32))
            if op == 0:
                if fn == 0: wordreg(rd, b << sa)
                elif fn == 2: wordreg(rd, (b & 0xffffffff) >> sa)
                elif fn == 3: wordreg(rd, sx(b,32) >> sa)
                elif fn == 4: wordreg(rd,b << (a&31))
                elif fn == 6: wordreg(rd,(b&0xffffffff) >> (a&31))
                elif fn == 7: wordreg(rd,sx(b,32) >> (a&31))
                elif fn == 0x14:self.setreg(rd,b << (a&63))
                elif fn == 0x16:self.setreg(rd,b >> (a&63))
                elif fn == 0x17:self.setreg(rd,sx(b,64) >> (a&63))
                elif fn == 0x3c: self.setreg(rd,b << (sa+32))
                elif fn == 0x3e: self.setreg(rd,b >> (sa+32))
                elif fn == 0x3f: self.setreg(rd,sx(b,64) >> (sa+32))
                elif fn == 0x38: self.setreg(rd,b << sa)
                elif fn == 0x3a: self.setreg(rd,b >> sa)  # dsrl (M11 soft-float library)
                elif fn == 0x3b: self.setreg(rd,sx(b,64) >> sa)  # dsra
                elif fn == 0x2f: self.setreg(rd,a-b)  # dsubu
                elif fn == 8: target = a
                elif fn == 9:  # jalr: target is read before writing link (rd may equal rs)
                    target = a
                    self.setreg(rd,pc+8)
                elif fn in (0x0a,0x0b):  # movz / movn
                    if (b == 0) == (fn == 0x0a):self.setreg(rd,a)
                elif fn in (0x18,0x19):  # mult / multu (R5900 three-operand: rd = LO)
                    product = sx(a,32) * sx(b,32) if fn == 0x18 else (a & 0xffffffff) * (b & 0xffffffff)
                    self.lo, self.hi = sx(product,32) & MASK64, sx(product >> 32,32) & MASK64
                    wordreg(rd, product)
                elif fn in (0x1a,0x1b) and rd == 0 and sa == 0:
                    # div / divu (M15 0019c8a0/002f7830): LO = quotient truncated toward
                    # zero, HI = remainder, each sign-extended from 32 bits. The R5900
                    # result for a zero divisor or INT_MIN / -1 is not investigated.
                    left, right = (sx(a,32), sx(b,32)) if fn == 0x1a else (a & 0xffffffff, b & 0xffffffff)
                    if right == 0:raise ValueError('integer division by zero outside investigated subset')
                    if fn == 0x1a and left == -(1 << 31) and right == -1:
                        raise ValueError('integer division overflow outside investigated subset')
                    quotient = abs(left) // abs(right) * (1 if (left < 0) == (right < 0) else -1)
                    self.lo, self.hi = sx(quotient,32) & MASK64, sx(left - quotient*right,32) & MASK64
                elif fn == 0x0f and rs == rt == rd == 0 and sa in (0,0x10): pass  # sync / sync.p (M11 sound DI helper)
                elif fn == 0x10: self.setreg(rd,self.hi)  # mfhi (M11 RNG 00114010)
                elif fn == 0x12: self.setreg(rd,self.lo)  # mflo
                elif fn == 0x21: wordreg(rd,a+b)
                elif fn == 0x23: wordreg(rd,a-b)
                elif fn == 0x25: self.setreg(rd,a|b)
                elif fn == 0x24: self.setreg(rd,a&b)
                elif fn == 0x26: self.setreg(rd,a^b)  # xor (M9)
                elif fn == 0x27: self.setreg(rd,~(a|b)&MASK64)  # nor (M9)
                elif fn == 0x2a: self.setreg(rd,int(sx(a,64)<sx(b,64)))
                elif fn == 0x2b: self.setreg(rd,int(a<b))
                elif fn == 0x2d: self.setreg(rd,a+b)
                else: raise ValueError(f'unsupported SPECIAL {word:08x} at {pc:08x}')
            elif op == 1 and rt in (0,1):  # bltz / bgez
                take = sx(a,64) < 0 if rt == 0 else sx(a,64) >= 0
                target = pc + 4 + imm*4 if take else pc + 8
            elif op in (6,7) and rt == 0:  # blez / bgtz
                take = sx(a,64) <= 0 if op == 6 else sx(a,64) > 0
                target = pc + 4 + imm*4 if take else pc + 8
            elif op in (2,3):
                target = ((pc+4) & 0xf0000000) | ((word & 0x3ffffff) << 2)
                if op == 3:self.setreg(31,pc+8)
            elif op in (4,5):
                take = a == b if op == 4 else a != b
                target = pc+4+imm*4 if take else pc+8
            elif op in (0x14,0x15,0x16,0x17):  # branch-likely annuls untaken delay
                if op == 0x14:take=a==b
                elif op == 0x15:take=a!=b
                elif rt != 0:raise ValueError('invalid signed branch-likely encoding')
                else:take=sx(a,64)<=0 if op==0x16 else sx(a,64)>0
                if take:target=pc+4+imm*4
                else:annul_delay=True
            elif op in (8,9):  # addi / addiu, verified non-overflowing domain
                if op == 8 and not -(1 << 31) <= sx(a,32)+imm < (1 << 31):
                    raise ValueError('ADDI overflow outside investigated subset')
                wordreg(rt,a+imm)
            elif op == 0x19: self.setreg(rt,a+imm)  # daddiu (64-bit, non-trapping; M11 RNG)
            elif op == 10: self.setreg(rt,int(sx(a,64)<imm))
            elif op == 11: self.setreg(rt, int(a < (imm & MASK64)))
            elif op == 12:self.setreg(rt,a & (word&0xffff))
            elif op == 13:self.setreg(rt,a | (word&0xffff))
            elif op == 14:self.setreg(rt,a ^ (word&0xffff))
            elif op == 15: wordreg(rt,(word & 0xffff)<<16)
            elif op == 0x1c and fn == 0x18 and sa == 0:  # mult1: LO1/HI1 pipeline, rd = LO1 (M11 RNG)
                product = sx(a,32) * sx(b,32)
                self.lo1, self.hi1 = sx(product,32) & MASK64, sx(product >> 32,32) & MASK64
                wordreg(rd, product)
            elif op == 0x1c and fn == 9 and sa == 14:  # pcpyld
                if rd:self.reg[rd] = (a << 64) | b
            elif op == 0x1c and (fn,sa) in ((8,0x12),(0x28,0x12),(0x29,0x0e)):
                # M9 camera: pextlw / pextuw interleave words; pcpyud copies upper doublewords.
                full_s,full_t = self.reg[rs] if rs else 0,self.reg[rt] if rt else 0
                ws=[(full_s >> (32*i)) & 0xffffffff for i in range(4)]
                wt=[(full_t >> (32*i)) & 0xffffffff for i in range(4)]
                if fn == 8:out=[wt[0],ws[0],wt[1],ws[1]]
                elif fn == 0x28:out=[wt[2],ws[2],wt[3],ws[3]]
                else:out=[ws[2],ws[3],wt[2],wt[3]]
                if rd:self.reg[rd]=sum(w << (32*i) for i,w in enumerate(out))
            elif op in (0x31, 0x39):  # lwc1 / swc1
                address = (a + imm) & 0xffffffff
                if op == 0x31:self.fpr[rt] = self.load(address,4)
                else:self.store(address,self.fpr[rt],4)
            elif op in (0x36,0x3e):  # lqc2 / sqc2
                address = (a + imm) & 0xffffffff
                if op == 0x36:
                    value=self.load(address,16)
                    if rt:self.vf[rt] = value
                else:self.store(address,self.vf[rt],16)
            elif op == 0x10 and rs == 0 and rd == 12 and (word & 0x7ff) == 0:  # mfc0 rt, Status
                # CP0 state is not captured in RAM. The caller must supply it explicitly.
                if self.cop0_status is None:raise ValueError(f'CP0 Status read without supplied value at {pc:08x}')
                wordreg(rt, self.cop0_status)
            elif word in (0x42000038, 0x42000039):  # ei / di: Status.EIE only (no interrupt delivery)
                if self.cop0_status is None:raise ValueError(f'EI/DI without supplied CP0 Status at {pc:08x}')
                self.cop0_status = (self.cop0_status | 0x10000) if word == 0x42000038 else (self.cop0_status & ~0x10000)
            elif op == 0x11:  # investigated COP1 operations only
                fs, ft, fd = rd, rt, sa
                if rs == 0:wordreg(rt,self.fpr[fs])
                elif rs == 4:self.fpr[fs] = self.reg[rt] & 0xffffffff
                elif rs == 8 and rt in (0,1):
                    take = self.fp_condition == bool(rt)
                    target = pc+4+imm*4 if take else pc+8
                elif rs == 16:
                    if fn in (0,1,2,3):
                        x,y = fp_value(self.fpr[fs]),fp_value(self.fpr[ft])
                        if fn in (0,1):
                            self.fpr[fd]=fp_ee_add(self.fpr[fs],self.fpr[ft],fn==1)
                            value=None
                        elif fn == 2:value = x*y
                        elif y != 0:
                            self.fpr[fd]=fp_signed_nearest(x/y)  # see fp_signed_nearest
                            value=None
                        else:raise ValueError('FP division by zero outside investigated subset')
                        if value is not None:
                            magnitude = fp_chop(abs(value))
                            if magnitude:sign = 0x80000000 if value < 0 else 0
                            # M12: a zero or flushed product keeps sign(fs) ^ sign(ft), as PCSX2
                            # does (retail 0012c640: +0 * negative sin/cos stored as 0x80000000).
                            else:sign = (self.fpr[fs] ^ self.fpr[ft]) & 0x80000000
                            self.fpr[fd] = magnitude | sign
                    elif fn == 6:self.fpr[fd] = self.fpr[fs]
                    elif fn == 7:self.fpr[fd] = self.fpr[fs] ^ 0x80000000
                    elif fn == 0x24:  # cvt.w.s in the captured chop profile
                        value = int(fp_value(self.fpr[fs]))
                        if not -(1 << 31) <= value < (1 << 31):
                            raise ValueError('FP conversion overflow outside investigated subset')
                        self.fpr[fd] = value & 0xffffffff
                    elif fn in (0x32,0x34,0x36):
                        x,y = fp_value(self.fpr[fs]),fp_value(self.fpr[ft])
                        self.fp_condition = x == y if fn == 0x32 else (x < y if fn == 0x34 else x <= y)
                    elif fn in (0x18,0x19):  # adda.s / suba.s
                        self.acc = fp_ee_add(self.fpr[fs],self.fpr[ft],fn==0x19)
                    elif fn in (0x1a,0x1c,0x1d,0x1e,0x1f):
                        product = fp_signed_chop(fp_value(self.fpr[fs])*fp_value(self.fpr[ft]))
                        if fn == 0x1a:self.acc = product
                        else:
                            value = fp_ee_add(self.acc,product,fn in (0x1d,0x1f))
                            if fn in (0x1e,0x1f):self.acc = value
                            else:self.fpr[fd] = value
                    else:raise ValueError(f'unsupported COP1 {word:08x} at {pc:08x}')
                elif rs == 20 and fn == 0x20:  # cvt.s.w
                    value = sx(self.fpr[fs],32)
                    self.fpr[fd] = fp_chop(abs(value)) | (0x80000000 if value < 0 else 0)
                else:raise ValueError(f'unsupported COP1 {word:08x} at {pc:08x}')
            elif op == 0x12:  # bounded functional VU macro subset, no flags/timing
                if rs == 5:
                    if rd:self.vf[rd] = self.reg[rt]  # qmtc2
                elif rs == 1:
                    if rt:self.reg[rt] = self.vf[rd]  # qmfc2 (full 128 bits)
                elif word == 0x4a0403bd:self.q = fp_chop(abs(fp_value(self.vf[4]&0xffffffff)),root=True)
                elif word == 0x4a0003bf:pass  # vwaitq: functional result already available
                elif word == 0x4b000120:
                    self.vf[4] = (self.vf[4] & ~0xffffffff) | self.q  # vf0.x + Q
                elif rs >= 16:
                    fs,ft,fd,mask = rd,rt,sa,(word >> 21) & 15
                    left=[(self.vf[fs] >> (32*i)) & 0xffffffff for i in range(4)]
                    right=[(self.vf[ft] >> (32*i)) & 0xffffffff for i in range(4)]
                    dest=fd;out=None;accum=False
                    if fn in (0x3c,0x3d) and sa == 12:  # vmove / vmr32
                        dest=ft;out=left if fn == 0x3c else left[1:]+left[:1]
                    elif fn >= 0x3c and sa in (2,6):  # vmadda / vmula bc
                        product=[fp_signed_chop(fp_value(x)*fp_value(right[fn&3])) if mask&(8>>i) else 0 for i,x in enumerate(left)]
                        out=product if sa == 6 else [fp_signed_chop(fp_value(x)+fp_value(y)) if mask&(8>>i) else 0 for i,(x,y) in enumerate(zip(self.vacc,product))]
                        accum=True
                    elif fn == 0x3e and sa == 0x0b:  # vopmula ACC = fs.yzx * ft.zxy (M9 camera)
                        order=((1,2),(2,0),(0,1))
                        out=[fp_signed_chop(fp_value(left[i])*fp_value(right[j])) if mask&(8>>lane) else 0
                             for lane,(i,j) in enumerate(order)]+[0]
                        accum=True
                    elif fn == 0x2e:  # vopmsub fd = ACC - fs.yzx * ft.zxy (same pattern as vopmula)
                        order=((1,2),(2,0),(0,1))
                        out=[]
                        for lane,(i,j) in enumerate(order):
                            if not mask&(8>>lane):out.append(0);continue
                            product=fp_signed_chop(fp_value(left[i])*fp_value(right[j]))
                            out.append(fp_signed_chop(fp_value(self.vacc[lane])-fp_value(product)))
                        out.append(0)
                    elif fn == 0x3f and sa == 0x0b:  # vnop
                        out=[]
                    elif fn == 0x3c and sa == 14:  # vdiv Q, fs.fsf, ft.ftf (M9 camera), VU0 Chop
                        dividend,divisor=left[(word>>21)&3],right[(word>>23)&3]
                        # A zero divisor was never observed in the M9 captures; fail closed.
                        if fp_value(divisor) == 0:raise ValueError('VDIV by zero outside investigated subset')
                        self.q=fp_signed_chop(fp_value(dividend)/fp_value(divisor))
                        out=[]
                    elif fn == 0x3d and sa == 14:  # vsqrt
                        self.q=fp_chop(abs(fp_value(right[(word>>23)&3])),root=True)
                        out=[]
                    elif fn == 0x3e and sa == 14:  # vrsqrt Q, fs.fsf, ft.ftf (M9 camera)
                        # PCSX2 default VU0 rounding (Chop): square root, then division,
                        # each rounded. Zero/negative radicands stay outside the subset.
                        root=fp_chop(fp_value(right[(word>>23)&3]),root=True)
                        if root == 0:raise ValueError('VRSQRT of zero outside investigated subset')
                        self.q=fp_signed_chop(fp_value(left[(word>>21)&3])/fp_value(root))
                        out=[]
                    elif fn in tuple(range(0x10,0x18))+(0x2b,0x2f):
                        # vmaxbc/vminibc/vmax/vmini (M15 0012bc70 chain, 00155814):
                        # exact selection of one operand, no rounding or flags.
                        # Ordered by sign-magnitude value; exponent-255, denormal
                        # and mixed-sign-zero comparisons are outside the subset.
                        other=[right[fn&3]]*4 if fn < 0x18 else right
                        take_max=fn in range(0x10,0x14) or fn == 0x2b
                        out=[]
                        for lane,(x,y) in enumerate(zip(left,other)):
                            if not mask&(8>>lane):
                                out.append(0);continue
                            for v in (x,y):
                                exponent=(v>>23)&0xff
                                if exponent == 0xff or (exponent == 0 and v&0x7fffff):
                                    raise ValueError(f'VMAX/VMINI operand {v:08x} outside investigated subset')
                            if not (x|y)&0x7fffffff and x != y:
                                raise ValueError('VMAX/VMINI of +0 and -0 outside investigated subset')
                            key=lambda v:-(v&0x7fffffff) if v&0x80000000 else v
                            out.append((x if key(x)>=key(y) else y) if take_max else (x if key(x)<=key(y) else y))
                    elif fn in tuple(range(0x00,0x0c))+tuple(range(0x18,0x1c))+(0x1c,0x20,0x28,0x29,0x2a,0x2c):
                        if fn < 0x1c:other=[right[fn&3]]*4
                        elif fn in (0x1c,0x20):other=[self.q]*4  # vmulq / vaddq
                        else:other=right
                        out=[]
                        for lane,(x,y) in enumerate(zip(left,other)):
                            if not mask&(8>>lane):
                                out.append(0);continue
                            x,y=fp_value(x),fp_value(y)
                            if fn in range(4) or fn in (0x20,0x28):v=x+y
                            elif fn in range(4,8) or fn == 0x2c:v=x-y
                            else:
                                product=fp_signed_chop(x*y)
                                v=fp_value(product)
                                if fn in range(8,12) or fn == 0x29:v+=fp_value(self.vacc[lane])
                            out.append(fp_signed_chop(v))
                    else:raise ValueError(f'unsupported COP2 {word:08x} at {pc:08x}')
                    if out:
                        for lane,value in enumerate(out):
                            if mask & (8 >> lane):
                                if accum:self.vacc[lane]=value
                                elif dest:self.vf[dest]=(self.vf[dest]&~(0xffffffff<<(32*lane)))|(value<<(32*lane))
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
                self.pc, self.pending = (pc+8,None) if annul_delay else (pc+4,target)
            self.reg[0] = 0
        return sx(self.reg[2],32)
