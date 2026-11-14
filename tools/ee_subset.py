"""Strict integer R5900 subset for the four disc-read functions, not an emulator.

Supports only observed operations; unknown opcodes and unmapped reads fail.
Models word sign extension, low 64-bit arithmetic, 128-bit lq/sq, and delay slots.
External CDVD calls must be supplied by the caller; no PS2 hardware is emulated.
"""
MASK64 = (1 << 64) - 1


def sx(value, bits):
    value &= (1 << bits) - 1
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


class Machine:
    def __init__(self):
        self.mem = {}
        self.reg = [0] * 32
        self.pc = 0
        self.pending = None
        self.hooks = {}
        self.steps = 0

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
                elif fn == 8: target = a
                elif fn == 0x21: wordreg(rd,a+b)
                elif fn == 0x23: wordreg(rd,a-b)
                elif fn == 0x25: self.setreg(rd,a|b)
                elif fn == 0x2d: self.setreg(rd,a+b)
                else: raise ValueError(f'unsupported SPECIAL {word:08x} at {pc:08x}')
            elif op == 1 and rt == 1:
                target = pc + 4 + imm*4 if sx(a,64) >= 0 else pc + 8
            elif op == 3:
                target = ((pc+4) & 0xf0000000) | ((word & 0x3ffffff) << 2)
                self.setreg(31,pc+8)
            elif op in (4,5):
                take = a == b if op == 4 else a != b
                target = pc+4+imm*4 if take else pc+8
            elif op == 9: wordreg(rt,a+imm)
            elif op == 11: self.setreg(rt, int(a < (imm & MASK64)))
            elif op == 15: wordreg(rt,(word & 0xffff)<<16)
            elif op in (0x20,0x24,0x23,0x37,0x1e):
                address = (a+imm) & 0xffffffff
                size = {0x20:1,0x24:1,0x23:4,0x37:8,0x1e:16}[op]
                v = self.load(address,size)
                if op == 0x1e: self.reg[rt] = v if rt else 0
                else: self.setreg(rt,sx(v,size*8) if op in (0x20,0x23) else v)
            elif op in (0x28,0x2b,0x3f,0x1f):
                size = {0x28:1,0x2b:4,0x3f:8,0x1f:16}[op]
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
