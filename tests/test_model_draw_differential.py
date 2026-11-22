"""Synthetic M15 0012bc70 player-model-draw original-instruction versus C differential.

The original words come from the tracked, original-byte-verified packet
analysis/model_draw/functions.json; the memory image is synthetic (no retail
capture, ELF or expected output). Scratch, the packet stream and the model are
poisoned (0xA5) and write sets are compared, so omitted, widened or extra
stores are visible. Interpreter-backed callees are host stubs with
input-dependent effects; after each stub the caller-saved GPRs, FPRs 0..19 and
VU0 vf1..31 are poisoned. The stack parameter blocks handed to 00151c40 and
0014d4f0 are logged at the call, so their contents and addresses (the guest-sp
contract of HG_GUEST_FRAME) are compared at the production checkpoint.
"""
import ctypes as C
import json
import math
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from resource_harness import Driver, Memory, ResourceEnv, ROOT, library
sys.path.insert(0, str(ROOT / 'tools'))
from ee_subset import Machine
import render_node_differential as RN
from verify_player_connected_runtime import FP_SEAMS

PACKET = ROOT / 'analysis/model_draw/functions.json'
GP = 0x3e7f70
SP = 0x1fffc70
STACK = (0x1fe0000, 0x2000000)
PLAYER = 0x3f92d0
OBJECT = PLAYER + 0x90
LIGHT = 0x52c000
HOLDER = 0x52c100      # gp+2a40 -> model matrix at +50
LIGHTSRC = 0x52c200    # gp+3080 -> ambient quad at +10
BONES = 0x52c300
CTX = 0x500000
STREAM = 5
BUFFER = 0x510000
POISON = 0xa5
REG_POISON = 0xdeadbeef_a5a5a5a5_5a5a5a5a_c0dec0de
FP_POISON = 0x41a5a5a5
VF_POISON = 0x41a5a5a5_c1a5a5a5_40a5a5a5_c0a5a5a5
ARGS = {0x125d60: 3, 0x128de0: 1, 0x126180: 0, 0x128e80: 0, 0x151c40: 1, 0x143530: 0,
        0x143c60: 2, 0x14a0a0: 4, 0x14d4f0: 4, 0x152aa0: 2}
CALLEES = tuple(ARGS) + (0x18c9c0,)


def bits(x): return struct.unpack('<I', struct.pack('<f', x))[0]
def flt(b): return struct.unpack('<f', struct.pack('<I', b & 0xffffffff))[0]
def f32(x): return flt(bits(x))
def sx32(v): v &= 0xffffffff; return v - (1 << 32) if v & 0x80000000 else v


def packet():
    data = json.loads(PACKET.read_text())
    words, bodies = {}, {}
    for row in data['functions']:
        have = {int(i['address'], 16): bytes.fromhex(i['bytes']) for i in row['instructions']}
        r = row['body_ranges'][0]
        if sorted(have) != list(range(int(r['start'], 16), int(r['end_exclusive'], 16), 4)):
            raise ValueError(f'Incomplete {row["entry"]} packet')
        words.update(have); bodies[int(row['entry'], 16)] = set(have)
    return words, bodies


class TrackingMemory(Memory):
    def __init__(self):
        super().__init__(); self.writes = set()
    def __setitem__(self, address, value):
        self.writes.add(address); super().__setitem__(address, value)


def quad(mem, address, values):
    for i, v in enumerate(values):
        mem.store(address + 4 * i, bits(v))


BASE = dict(present=1, gate=(0, 0, 0), draw_bits=0xc0000000, visible=1.0, kind=0x02000000,
            light=0x00200000, shadow=0x30000000, data=0x77, light_mode=2.0, count=0, pending=0,
            vector=(0.3, -0.8, 0.5), radius=6.0, extent=2.5, model_type=3, angle=0.6)
SCENARIOS = {
    'observed': {},
    'gate-set': dict(gate=(0, 1, 0)),
    'gate-gp': dict(gate=(1, 0, 0), kind=0x04000000),
    'gate-overlay': dict(gate=(0, 0, 2), kind=0x01000000, data=0),
    'no-light-no-shadow': dict(gate=(0, 1, 0), light=0, kind=0),
    'no-data': dict(data=0),
    'hidden': dict(draw_bits=0),
    'invisible': dict(visible=0.0),
    'absent': dict(present=0),
}
OUT_OF_DOMAIN = {   # case -> (patch, domain fault)
    'type-other': (dict(model_type=4), 0x75),
    'pending-2a24': (dict(pending=1), 0x75),
    'light-mode-129600': (dict(light=0x00400000), 0x76),
    'data-bit24': (dict(kind=0x01000000), 0x76),
    'light-mode-not-2': (dict(light_mode=3.0), 0x77),
    'out-of-range': (dict(radius=0.1, extent=0.1), 0x78),
    'point-lights': (dict(count=1), 0x78),
    'light-flag-bit21-clear': (dict(light=0x00100000), 0x78),
}


def build(mem, sc):
    mem.store(GP + 0x3098, CTX)
    mem.store(CTX + 0x53c + 4 * STREAM, BUFFER)
    mem.put(BUFFER - 0x40, bytes([POISON]) * 0x2000)
    mem.put(0x70000000, bytes([POISON]) * 0x4000)
    for r in range(4):
        quad(mem, 0x3db2b0 + 16 * r, [1.0 if r == i else 0.0 for i in range(4)])
    for k, (x, z) in enumerate(((-0.5, -0.5), (0.5, -0.5), (-0.5, 0.5), (0.5, 0.5))):
        quad(mem, 0x37bab0 + 16 * k, (x, 0.0, z, 1.0))
    mem.store(PLAYER + 1, sc['present'], 1)
    for address, v in zip((GP + 0x2f78, 0x3f9e3a, 0x3f9f3d), sc['gate']):
        mem.store(address, v, 1)
    mem.put(PLAYER + 0xe0, bytes([POISON]) * 0x200)
    # The model object is player+90, so its +50 type word is the player +e0 draw word.
    mem.store(PLAYER + 0xe0, sc['draw_bits'] | sc['shadow'] | sc['kind'] | sc['light'] | sc['model_type'])
    o = OBJECT
    mem.store(o + 0x10, LIGHT); mem.store(o + 0x14, sc['data']); mem.store(o + 0x0d, 2, 1)
    mem.store(o + 0x58, BONES)
    quad(mem, o + 0x40, (0.75, 0.5, 0.25, 0.4))
    mem.store(o + 0x4c, bits(sc['visible']))
    quad(mem, o + 0x190, sc['vector'] + (9.0,))
    quad(mem, o + 0x19c, (1.5, 0.5, 2.0, sc['extent']))
    mem.store(o + 0x1ac, bits(sc['light_mode']))
    mem.store(LIGHT + 0x20, bits(sc['radius']))
    c, s = math.cos(sc['angle']), math.sin(sc['angle'])
    for r, row in enumerate(((c, 0.0, -s, 0.0), (0.0, 1.0, 0.0, 0.0), (s, 0.0, c, 0.0), (1.0, -2.0, 0.5, 1.0))):
        quad(mem, HOLDER + 0x50 + 16 * r, row)
    mem.store(GP + 0x2a40, HOLDER); mem.store(GP + 0x2a50, STREAM); mem.store(GP + 0x2a24, sc['pending'])
    mem.store(GP + 0x2a14, 0x5a5a5a5a)
    mem.store(GP + 0x3080, LIGHTSRC); quad(mem, LIGHTSRC + 0x10, (0.1, 0.2, 0.3, 0.4))
    mem.store(BONES + 8, 0x52c400); quad(mem, 0x52c400 + 0x30, (3.0, 0.25, -4.0, 1.0))


class Stubs:
    def __init__(self, mem, sc):
        self.mem = mem; self.sc = sc; self.log = []

    def append(self, words):
        slot = self.mem.load(GP + 0x3098) + 0x53c + 4 * STREAM
        at = self.mem.load(slot)
        for i, w in enumerate(words[:4]):
            self.mem.store(at + 4 * i, w & 0xffffffff)
        self.mem.store(slot, at + 0x10)

    def __call__(self, pc, fp, ints):
        if pc == 0x128de0:
            self.mem.store(GP + 0x2a30, OBJECT); self.log.append((pc, fp, ints)); return 0
        if pc == 0x143530: return bits(f32(math.sqrt(max(flt(fp[0]), 0.0))))
        if pc == 0x143c60:
            angle = flt(fp[0]); c, s = f32(math.cos(angle)), f32(math.sin(angle))
            m = [[flt(self.mem.load(ints[1] + 16 * r + 4 * k)) for k in range(4)] for r in range(4)]
            for r in range(4):
                m[r][1], m[r][2] = f32(c * m[r][1] - s * m[r][2]), f32(s * m[r][1] + c * m[r][2])
            for r in range(4):
                for k in range(4):
                    self.mem.store(ints[0] + 16 * r + 4 * k, bits(m[r][k]))
            return 0
        if pc == 0x151c40:     # checkpoint: the 00129020 stack parameter block
            self.log.append((pc, ints[0], self.mem.get(ints[0], 0x1c))); return self.sc['count']
        if pc == 0x14d4f0:     # checkpoint: four shadow corners (stack) and the colour
            data = self.mem.get(ints[1], 0x40) + self.mem.get(ints[2], 0x10)
            self.log.append((pc, ints, data)); self.append([0x14d4f000, ints[3], ints[3] >> 32, sum(data)]); return 0
        if pc in (0x14a0a0, 0x152aa0, 0x125d60):
            self.log.append((pc, ints)); self.append([pc << 8, *ints[:3]])
            if pc == 0x125d60: self.mem.store(ints[1], ints[0])
            return 0
        if pc in (0x126180, 0x128e80):
            self.log.append((pc,)); self.mem.store(0x52c500 + (pc & 0xf0), pc); return 0
        raise ValueError(f'unexpected callee {pc:#x}')


def arguments(pc, fp_values, int_values):
    n_fp = FP_SEAMS.get(pc, (0, False))[0]
    fp = tuple(v & 0xffffffff for v in fp_values[:n_fp])
    ints = tuple(v & (0xffffffffffffffff if (pc == 0x14d4f0 and i == 3) else 0xffffffff)
                 for i, v in enumerate(int_values[:ARGS[pc]]))
    return fp, ints


def original(sc):
    mem = TrackingMemory(); build(mem, sc)
    words, _ = packet()
    for address, raw in words.items(): mem.put(address, raw)
    mem.writes.clear()
    stubs = Stubs(mem, sc); calls = []
    machine = Machine(); machine.mem = mem; machine.reg[28] = GP; machine.reg[29] = SP

    def hook(pc):
        def run(cpu):
            fp, ints = arguments(pc, [cpu.fpr[12 + i] for i in range(4)], [cpu.reg[4 + i] for i in range(4)])
            calls.append((pc, fp, ints))
            value = stubs(pc, fp, ints)
            for n in list(range(1, 16)) + [24, 25]: cpu.reg[n] = REG_POISON
            for n in range(20): cpu.fpr[n] = FP_POISON
            for n in range(1, 32): cpu.vf[n] = VF_POISON
            if FP_SEAMS.get(pc, (0, False))[1]:
                cpu.fpr[0] = value; return sx32(0xc0dec0de)
            return sx32(value)
        return run
    machine.hooks = {pc: hook(pc) for pc in ARGS}
    machine.run(RN.MODEL, limit=400000)
    return dict(mem=mem, calls=calls, log=stubs.log, executed=set(machine.executed))


class StubDriver(Driver):
    def __init__(self, sc):
        super().__init__(TrackingMemory())
        build(self.mem, sc)
        words, _ = packet()
        for address, raw in words.items(): self.mem.put(address, raw)   # identical image; C never reads it
        self.mem.writes.clear()
        self.stubs = Stubs(self.mem, sc); self.log = self.stubs.log
        self.faults = []; self.events = []; self.depth = 0; self.wide = None; self.calls_seen = []

    def external(self, pc, a=0, b=0, c=0, d=0):
        if pc == 0xfffffff0: self.faults.append(a); return 0
        if pc == 0xfffffff6: self.events.append((a, b)); return 0
        if pc == 0xfffffff1:   # guest sp of the entered frame, as the original's prologue
            if b not in (0, 1) or a & 15 or (b == 0 and self.depth < a): raise ValueError('guest frame')
            self.depth += a if b else -a
            return (SP - self.depth) & 0xffffffff if b else 0
        if pc == 0xfffffff4:
            if self.wide is not None: raise ValueError('unconsumed wide seam')
            self.wide = (b << 32) | a; return 0
        if pc not in ARGS: raise ValueError(f'unexpected call {pc:#x}')
        wide, self.wide = self.wide, None
        if (wide is not None) != (pc == 0x14d4f0): raise ValueError(f'wide contract at {pc:#x}')
        if wide is not None:
            if wide & 0xffffffff != d: raise ValueError('wide low word')
            d = wide
        n_fp = FP_SEAMS.get(pc, (0, False))[0]
        operands = (a, b, c, d)
        fp, ints = arguments(pc, operands[:n_fp], operands[n_fp:] + (0,) * 4)
        self.calls_seen.append((pc, fp, ints))
        return self.stubs(pc, fp, ints)


def c_run(lib, sc):
    d = StubDriver(sc)
    f = lib.hg_player_model_draw
    f.argtypes = [C.POINTER(ResourceEnv)]; f.restype = None
    f(C.byref(d.env))
    if d.errors: raise AssertionError(d.errors)
    return d


def outside_stack(writes): return {a for a in writes if not STACK[0] <= a < STACK[1]}


def mismatch(o, d):
    rows = []
    if d.faults: rows.append(('faults', d.faults))
    a, b = o['mem'], d.mem
    diff = []
    for page in sorted(set(a.pages) | set(b.pages)):
        base = page << 12
        x, y = a.get(base, 4096), b.get(base, 4096)
        if x != y:
            diff.extend(base + i for i in range(4096) if x[i] != y[i] and not STACK[0] <= base + i < STACK[1])
    if diff: rows.append(('bytes', len(diff), [hex(v) for v in diff[:8]]))
    wo, wc = outside_stack(a.writes), outside_stack(b.writes)
    if wo != wc: rows.append(('write-set', len(wo - wc), len(wc - wo), [hex(v) for v in sorted(wo ^ wc)[:8]]))
    if o['calls'] != d.calls_seen: rows.append(('calls', len(o['calls']), len(d.calls_seen)))
    if o['log'] != d.log: rows.append(('checkpoints', len(o['log']), len(d.log)))
    return rows


# Original instructions outside the reconstructed domain; the C fails closed
# there (0x74..0x78). Branch-delay padding is classified separately.
OUT_OF_DOMAIN_RANGES = {
    0x123ee0: ((0x123f1c, 0x123f38),),                     # a1 == 1: 0018c9c0 visibility test
    0x124010: ((0x1240a4, 0x124620), (0x124650, 0x125d10),  # model types other than 3
               (0x125d1c, 0x125d30)),                       # gp+2a24 != 0 tail
    0x125f30: ((0x125f70, 0x125f8c),                        # light flag 00100000: its 00129020 path (bit 21 clear) is out of domain
               (0x125fb0, 0x125fc8),                        # light mode 00400000: 00129600
               (0x125ff0, 0x126008),                        # shadow 10000000 (00129840): unreachable from 0012bc70
               (0x126034, 0x126098)),                       # data with +50 bit 24
    0x129020: ((0x1290a8, 0x1290b0), (0x1290cc, 0x129190),  # model type 40/20/e/d/2; +1ac == 1 (after delay slots)
               (0x1291a8, 0x129208), (0x129220, 0x129238), # +1ac >= 4; < 4 but != 2
               (0x129240, 0x129248),
               (0x1293e8, 0x1293f8),                        # |v| >= range
               (0x1294a0, 0x129590),                        # +50 bit 21 clear; +1ac == 0 (129500)
               (0x1295a0, 0x1295d0)),                       # point-light loop (00151c40 count > 0)
}


def unreachable_padding(path=PACKET):
    data = json.loads(Path(path).read_text())
    text = {int(i['address'], 16): i['text'] for row in data['functions'] for i in row['instructions']}
    targets = {int(t.rsplit(' ', 1)[1], 16) for t in text.values()
               if t.split()[0][0] in 'bj' and t.rsplit(' ', 1)[1].startswith('0x')}
    return {a for a, t in text.items()
            if t == 'nop' and a not in targets and text.get(a - 8, '').startswith(('beq zero, zero', 'j '))}


class ModelDrawDifferentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.source = (ROOT / 'src/resource_loader.c').read_text()
        cls.lib = library(output=Path(cls.tmp.name) / 'baseline.so')
        cls.scenarios = {name: dict(BASE, **patch) for name, patch in SCENARIOS.items()}
        cls.originals = {name: original(sc) for name, sc in cls.scenarios.items()}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_c_matches_original_instructions_on_poisoned_memory(self):
        for name, sc in self.scenarios.items():
            with self.subTest(scenario=name):
                d = c_run(self.lib, sc)
                self.assertEqual(mismatch(self.originals[name], d), [])
                self.assertEqual(d.events, [(RN.MODEL, 1), (RN.MODEL, 0)])
                self.assertEqual(d.depth, 0)

    def test_observed_outputs_and_stack_contracts(self):
        o = self.originals['observed']
        block = next(row for row in o['log'] if row[0] == 0x151c40)
        self.assertEqual(block[1], SP - 0x220 + 0x70)   # 00129020 sp+70 under 0012bc70..00125f30 frames
        shadow = next(row for row in o['log'] if row[0] == 0x14d4f0)
        self.assertEqual(shadow[1][1], SP - 0x200 + 0x30)
        self.assertEqual(shadow[1][3], RN.SHADOW_TAG)
        self.assertEqual(shadow[2][0x40:], struct.pack('<4f', 2.0, 2.0, 2.0, 124.0))
        light = [flt(o['mem'].load(0x70000710 + 4 * i)) for i in range(3)]
        self.assertAlmostEqual(sum(v * v for v in light), 1.0, places=5)
        self.assertEqual(o['mem'].load(PLAYER + 0xe0) & 0x30000000, 0x30000000)
        self.assertEqual(self.originals['gate-set']['mem'].load(PLAYER + 0xe0) & 0x30000000, 0)

    def test_branch_coverage(self):
        executed = set().union(*(o['executed'] for o in self.originals.values()))
        padding = unreachable_padding()
        _, bodies = packet()
        for entry, body in bodies.items():
            with self.subTest(function=hex(entry)):
                excluded = {a for s, e in OUT_OF_DOMAIN_RANGES.get(entry, ()) for a in range(s, e, 4)}
                self.assertEqual(sorted(hex(a) for a in body - executed - padding - excluded), [])
                self.assertEqual(sorted(hex(a) for a in body & excluded & executed), [])
                self.assertEqual(sorted(hex(a) for a in body & padding & executed), [])

    def test_out_of_domain_paths_fail_closed(self):
        for name, (patch, code) in OUT_OF_DOMAIN.items():
            with self.subTest(case=name):
                d = StubDriver(dict(BASE, **patch))
                f = self.lib.hg_player_model_draw
                f.argtypes = [C.POINTER(ResourceEnv)]; f.restype = None
                f(C.byref(d.env))
                self.assertIn(code, d.faults)
                self.assertEqual(d.depth, 0)

    def test_model_draw_mutants_are_rejected(self):
        for name, defect, *_ in RN.MUTANTS:
            if name not in RN.MODEL_DRAW_MUTANTS:
                continue
            with self.subTest(mutant=name):
                path = Path(self.tmp.name) / f'{name}.c'
                path.write_text(RN.mutant_source(self.source, name))
                lib = library(source=path, output=Path(self.tmp.name) / f'{name}.so')
                self.assertTrue(mismatch(self.originals['observed'], c_run(lib, self.scenarios['observed'])), defect)


if __name__ == '__main__':
    unittest.main()
