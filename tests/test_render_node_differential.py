"""Synthetic M15 00143150 / 0019c8a0 / 002f7830 original-instruction versus C differential.

The original words come from the tracked, original-byte-verified packet
analysis/render_nodes/functions.json. The memory image is synthetic: no retail
capture, ELF or expected output is used. Scratch and the packet stream start
poisoned (0xA5), so a store the C omits cannot be hidden by stale contents, and
write sets (not only values) are compared, so a same-value or unconditional
store is also visible. Callees are host stubs with input-dependent effects
(rotations, sqrt, a fade curve that overshoots both clamps, packet records);
after each stub the caller-saved GPRs, FPRs 0..19 and VU0 vf1..31 are poisoned,
so the original cannot depend on values a real callee would not preserve.
Each emission's screen-space quads are logged at the moment of the call (the
production checkpoint), not only in the final image.
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

PACKET = ROOT / 'analysis/render_nodes/functions.json'
GP = 0x3e7f70
CTX = 0x500000
BUFFER = 0x510000
SP = 0x1fffc70
STACK = (0x1fe0000, 0x2000000)
HEAD = 0x539458
NODES = 0x528430
OTHER = 0x52a000          # 0019c8a0 +b8 companion object
ANCHOR, FIELD, FOREIGN = 0x19c8a0, 0x2f7830, 0x1a6dc0
POISON = 0xa5
REG_POISON = 0xdeadbeef_a5a5a5a5_5a5a5a5a_c0dec0de
FP_POISON = 0x41a5a5a5  # finite
VF_POISON = 0x41a5a5a5_c1a5a5a5_40a5a5a5_c0a5a5a5
# Callee -> number of integer argument registers it reads (after FP_SEAMS f12..).
ARGS = {0x143c60: 2, 0x143d30: 2, 0x143b90: 2, 0x143530: 0, 0x143560: 0, 0x155350: 1,
        0x138370: 2, 0x152aa0: 2, 0x14a0a0: 4, 0x14d4f0: 4, 0x1503f0: 3, 0x11ad58: 0,
        0x196ad0: 1, FOREIGN: 1}
CALLEES = tuple(ARGS)


def bits(x): return struct.unpack('<I', struct.pack('<f', x))[0]
def flt(b): return struct.unpack('<f', struct.pack('<I', b & 0xffffffff))[0]
def f32(x): return flt(bits(x))
def sx32(v): v &= 0xffffffff; return v - (1 << 32) if v & 0x80000000 else v


def packet_words(path=PACKET):
    data = json.loads(Path(path).read_text())
    words = {}
    for row in data['functions']:
        for r in row['body_ranges']:
            start, end = int(r['start'], 16), int(r['end_exclusive'], 16)
            have = {int(i['address'], 16): bytes.fromhex(i['bytes']) for i in row['instructions']}
            if sorted(a for a in have if start <= a < end) != list(range(start, end, 4)):
                raise ValueError(f'Incomplete {row["entry"]} packet')
            words.update(have)
    return words


def bodies(path=PACKET):
    data = json.loads(Path(path).read_text())
    return {int(row['entry'], 16): {int(i['address'], 16) for i in row['instructions']}
            for row in data['functions']}


class TrackingMemory(Memory):
    def __init__(self):
        super().__init__(); self.writes = set()
    def __setitem__(self, address, value):
        self.writes.add(address); super().__setitem__(address, value)


def quad(mem, address, values):
    for i, v in enumerate(values):
        mem.store(address + 4 * i, bits(v))


# --------------------------------------------------------------------------
# Synthetic scenarios: every observed 0019c8a0 state-1 sub-state and tail
# branch, and every 002f7830 variant-0 branch (wrap both ways, cull, distance
# window, fade clamps both ways, negative pitch and facing clamp).

SCENARIOS = {
    'release-wait': dict(release=0, held=0, gate=(0, 0, 0, 0), player=1, tail=0x01, pitch=-0.4, yaw=0.7,
                         anchors=((0, 0.5, 0), (1, 3.995, OTHER), (1, 1.0, 0), (3, 0.7, 0)),
                         layers=((63.0, 63.0, -63.0), (-70.0, 66.0, 0.5), (5.0, -5.0, 64.0)),
                         previous=(15.0, 7.5, -11.0), speeds=(1.5, -2.0, 0.25)),  # delta (+5,+5,-5)
    'active-draw': dict(release=0, held=2, gate=(0, 0, 0, 0), player=1, tail=0x10, pitch=1.4, yaw=-2.1,
                        anchors=((2, 0.2, OTHER), (2, 1.39, 0), (1, 0.0, 0)),
                        layers=((-63.9, 30.0, -40.0), (12.0, -64.5, 63.0), (0.0, 0.0, 0.0)),
                        previous=(-3.0, 4.0, 0.5), speeds=(-1.0, 3.0, 0.0)),
    'suppressed': dict(release=1, held=0, gate=(0, 1, 0, 0), player=1, tail=0x00, pitch=0.3, yaw=3.0,
                       anchors=((1, 2.0, 0), (2, 0.5, OTHER), (0, 0.1, 0)),
                       layers=((-63.0, -63.0, 63.0), (-20.0, -20.0, -20.0), (64.0, -64.0, 1.0)),
                       previous=(5.0, -2.5, -1.0), speeds=(0.0, 0.0, 0.0)),  # delta (-5,-5,+5)
    'player-absent': dict(release=0, held=1, gate=(0, 0, 0, 0), player=0, tail=0x03, pitch=-1.3, yaw=0.0,
                          anchors=((2, 0.3, 0),),
                          layers=((40.0, -10.0, 55.0), (-55.0, 25.0, -30.0), (8.0, 60.0, -60.0)),
                          previous=(1.0, 1.0, 1.0), speeds=(0.5, 0.5, -0.5)),
}


def view_matrix():
    c, s = math.cos(0.6), math.sin(0.6)
    return ((c, 0.0, -s, 0.0), (0.0, 1.0, 0.0, 0.0), (s, 0.0, c, 0.0), (4.0, -2.0, 7.5, 1.0))


def build(mem, sc):
    mem.store(GP + 0x3098, CTX)
    mem.store(CTX + 0x550, BUFFER)
    mem.put(BUFFER - 0x40, bytes([POISON]) * 0x8000)
    mem.put(0x70000000, bytes([POISON]) * 0x4000)
    for r, row in enumerate(view_matrix()):
        quad(mem, 0x70000000 + 16 * r, row)
    mem.store(GP - 0x6d50, 0x12345678); mem.store(GP - 0x6d40, 0x9abcdef0)
    for r in range(4):
        quad(mem, 0x3db2b0 + 16 * r, [1.0 if r == i else 0.0 for i in range(4)])
    for k, (x, y) in enumerate(((-0.5, -0.5), (0.5, -0.5), (-0.5, 0.5), (0.5, 0.5))):
        quad(mem, 0x3c9c50 + 16 * k, (x, y, 0.0, 1.0))
    for g, v in enumerate(((1.0, 1.0, 1.0, 1.0), (0.75, 1.0, 0.5, 1.0), (1.0, 0.25, 1.0, 1.0), (0.5, 0.5, 0.9, 1.0))):
        quad(mem, 0x3c9c90 + 16 * g, v)
    for k in range(16):
        quad(mem, 0x3c9ad0 + 16 * k, (math.sin(1.7 * k + 0.3), math.cos(2.3 * k), math.sin(0.9 * k - 1.1), 0.5 + k / 32))
    quad(mem, 0x3e57c0, (128.0, 96.0, 64.0, 80.0))
    quad(mem, 0x3f9860, (10.0, 2.5, -6.0, 1.0))
    quad(mem, 0x3f9890, (0.6, 0.0, 0.8, 0.0))
    mem.store(0x3f9930, bits(sc['pitch'])); mem.store(0x3f9934, bits(sc['yaw']))
    mem.store(0x3f457c, 0x77)
    mem.store(0x3f976c, sc['release'], 1); mem.store(0x3f976d, sc['held'], 1)
    for address, v in zip((GP + 0x2f78, 0x3fa067, GP + 0x2f74, 0x3f976f), sc['gate']):
        mem.store(address, v, 1)
    mem.store(0x3f92d1, sc['player'], 1); mem.store(0x3f9753, sc['tail'], 1)
    mem.store(GP + 0x2f68, 0x5a5a, 2)
    mem.put(OTHER, bytes([POISON]) * 0x40)
    # node list: anchors..., foreign callback (rewrites its own next), field
    nodes = []
    for i, (sub, timer, other) in enumerate(sc['anchors']):
        n = NODES + 0x200 * i; nodes.append((n, ANCHOR))
        mem.put(n, bytes([POISON]) * 0x200)
        mem.store(n + 0x18, 1, 1); mem.store(n + 0x19, sub, 1)
        quad(mem, n + 0x30, (9.0, 9.0, 9.0, 9.0))
        mem.store(n + 0x60, bits(timer)); mem.store(n + 0x68, bits(0.5 + i))
        mem.store(n + 0x6c, bits(1.0 + 0.25 * i))
        mem.store(n + 0x90, 0x100 + i); mem.store(n + 0x94, 0x200 + i)
        mem.store(n + 0x108, other)
    n = NODES + 0x200 * len(nodes); nodes.append((n, FOREIGN)); mem.put(n, bytes([POISON]) * 0x40)
    n = NODES + 0x200 * len(nodes); nodes.append((n, FIELD))
    mem.put(n, bytes([POISON]) * 0x200)
    mem.store(n + 8, 0); mem.store(n + 0x18, 1, 1)
    mem.store(n + 0x40, bits(0.3)); mem.store(n + 0x44, bits(-0.8)); mem.store(n + 0x48, bits(1.9))
    quad(mem, n + 0x50, sc['previous'] + (1.0,))
    for i, layer in enumerate(sc['layers']):
        quad(mem, n + 0x60 + 16 * i, layer + (7.0,))
    for i, s in enumerate(sc['speeds']):
        mem.store(n + 0xd4 + 4 * i, bits(s))
    mem.store(n + 0xe0, bits(0.75))
    for i, (node, fn) in enumerate(nodes):
        mem.store(node + 0x1c, fn)
        mem.store(node + 0x24, nodes[i + 1][0] if i + 1 < len(nodes) else 0)
    mem.store(HEAD, nodes[0][0])
    return nodes


# --------------------------------------------------------------------------
# Shared stub effects. Inputs are (pc, fp operands, integer operands); the
# same function serves the original hook and the C driver.

class Stubs:
    def __init__(self, mem):
        self.mem = mem; self.rng = 0x0123456789abcdef; self.log = []; self.allocated = 0x52b000

    def append(self, words):
        at = self.mem.load(self.mem.load(GP + 0x3098) + 0x550)
        for i, w in enumerate(words[:4]):
            self.mem.store(at + 4 * i, w)
        self.mem.store(self.mem.load(GP + 0x3098) + 0x550, at + 0x10)

    def rotate(self, pc, angle, dst, src):
        c, s = f32(math.cos(flt(angle))), f32(math.sin(flt(angle)))
        axes = {0x143c60: (1, 2), 0x143d30: (2, 0), 0x143b90: (0, 1)}[pc]
        m = [[flt(self.mem.load(src + 16 * r + 4 * k)) for k in range(4)] for r in range(4)]
        i, j = axes
        for r in range(4):
            a, b = m[r][i], m[r][j]
            m[r][i], m[r][j] = f32(c * a - s * b), f32(s * a + c * b)
        for r in range(4):
            for k in range(4):
                self.mem.store(dst + 16 * r + 4 * k, bits(m[r][k]))

    def __call__(self, pc, fp, ints):
        if pc in (0x143c60, 0x143d30, 0x143b90):
            self.rotate(pc, fp[0], ints[0], ints[1]); return 0
        if pc == 0x143530: return bits(f32(math.sqrt(max(flt(fp[0]), 0.0))))
        if pc == 0x143560: return bits(f32(1.3 * math.sin(flt(fp[0])) - 0.15))
        if pc == 0x11ad58:
            self.rng = (self.rng * RN.LCG_MULTIPLIER + 1) & RN.MASK64
            return self.rng >> 32
        if pc == 0x138370: return 0x600000 | (ints[0] << 4) | (ints[1] >> 24)
        if pc == 0x196ad0:
            self.allocated += 0x40; self.mem.put(self.allocated, bytes(0x40)); return self.allocated
        if pc == FOREIGN:      # foreign callback: clobbers its own next and the node counter
            self.mem.store(ints[0] + 0x24, 0); self.mem.store(GP + 0x2f68, 0x40, 2); return 0
        if pc == 0x14d4f0:     # checkpoint: the four projected corners and the colour
            data = self.mem.get(ints[1], 0x40) + self.mem.get(ints[2], 0x10)
            self.log.append((pc, data, ints[3]))
            self.append([0x14d4f000, ints[3] & 0xffffffff, ints[3] >> 32, sum(data) & 0xffffffff]); return 0
        if pc == 0x1503f0:     # checkpoint: the sprite block 70000900..9 4f
            data = self.mem.get(ints[1], 0x50); self.log.append((pc, data, fp[0], ints[2]))
            self.append([0x1503f000, fp[0], ints[2], sum(data) & 0xffffffff]); return 0
        if pc in (0x155350, 0x152aa0, 0x14a0a0):
            self.append([pc << 8, *(list(fp) + list(ints))[:3]]); return 0
        raise ValueError(f'unexpected callee {pc:#x}')


def arguments(pc, fp_values, int_values):
    n_fp = FP_SEAMS.get(pc, (0, False))[0] if pc != FOREIGN else 0
    fp = tuple(v & 0xffffffff for v in fp_values[:n_fp])
    ints = tuple(v & (0xffffffffffffffff if (pc == 0x14d4f0 and i == 3) else 0xffffffff)
                 for i, v in enumerate(int_values[:ARGS[pc]]))
    return fp, ints


def original(sc):
    mem = TrackingMemory()
    nodes = build(mem, sc)
    for address, raw in packet_words().items():
        mem.put(address, raw)
    mem.writes.clear()
    stubs = Stubs(mem); calls = []
    machine = Machine(); machine.mem = mem
    machine.reg[28] = GP; machine.reg[29] = SP

    def hook(pc):
        def run(cpu):
            fp, ints = arguments(pc, [cpu.fpr[12 + i] for i in range(4)], [cpu.reg[4 + i] for i in range(4)])
            calls.append((pc, fp, ints))
            value = stubs(pc, fp, ints)
            for n in list(range(1, 16)) + [24, 25]:
                cpu.reg[n] = REG_POISON
            for n in range(20):
                cpu.fpr[n] = FP_POISON
            for n in range(1, 32):
                cpu.vf[n] = VF_POISON
            if FP_SEAMS.get(pc, (0, False))[1]:
                cpu.fpr[0] = value; return sx32(0xc0dec0de)
            return sx32(value)
        return run
    machine.hooks = {pc: hook(pc) for pc in CALLEES}
    machine.run(0x143150, limit=3000000)
    return dict(mem=mem, calls=calls, log=stubs.log, executed=set(machine.executed), nodes=nodes)


class StubDriver(Driver):
    def __init__(self, sc):
        super().__init__(TrackingMemory())
        self.nodes = build(self.mem, sc)
        for address, raw in packet_words().items():     # identical image; C never reads it
            self.mem.put(address, raw)
        self.mem.writes.clear()
        self.stubs = Stubs(self.mem); self.log = self.stubs.log
        self.faults = []; self.events = []; self.frames = []; self.depth = 0; self.wide = None; self.calls_seen = []

    def external(self, pc, a=0, b=0, c=0, d=0):
        if pc == 0xfffffff0: self.faults.append(a); return 0
        if pc == 0xfffffff6: self.events.append((a, b)); return 0
        if pc == 0xfffffff1:
            if b not in (0, 1) or a & 15 or (b == 0 and self.depth < a): raise ValueError('guest frame')
            self.depth += a if b else -a; self.frames.append((a, b)); return 0
        if pc == 0xfffffff4:
            if self.wide is not None: raise ValueError('unconsumed wide seam')
            self.wide = (b << 32) | a; return 0
        if pc not in ARGS: raise ValueError(f'unexpected call {pc:#x}')
        wide, self.wide = self.wide, None
        if (wide is not None) != (pc == 0x14d4f0): raise ValueError(f'wide contract at {pc:#x}')
        if wide is not None:
            if wide & 0xffffffff != d: raise ValueError('wide low word')
            d = wide
        n_fp = FP_SEAMS.get(pc, (0, False))[0] if pc != FOREIGN else 0
        operands = (a, b, c, d)
        fp, ints = arguments(pc, operands[:n_fp], operands[n_fp:] + (0,) * 4)
        self.calls_seen.append((pc, fp, ints))
        return self.stubs(pc, fp, ints)


def c_run(lib, sc):
    d = StubDriver(sc)
    f = lib.hg_render_node_list_fixed
    f.argtypes = [C.POINTER(ResourceEnv)]; f.restype = None
    f(C.byref(d.env))
    if d.errors: raise AssertionError(d.errors)
    return d


def differences(a, b):
    out = []
    for page in sorted(set(a.pages) | set(b.pages)):
        base = page << 12
        x, y = a.get(base, 4096), b.get(base, 4096)
        if x != y:
            out.extend(base + i for i in range(4096) if x[i] != y[i] and not STACK[0] <= base + i < STACK[1])
    return out


def outside_stack(writes):
    return {a for a in writes if not STACK[0] <= a < STACK[1]}


def mismatch(o, d):
    """Every comparison the differential makes; empty means identical."""
    rows = []
    if d.faults: rows.append(('faults', d.faults))
    diff = differences(o['mem'], d.mem)
    if diff: rows.append(('bytes', len(diff), [hex(a) for a in diff[:8]]))
    wo, wc = outside_stack(o['mem'].writes), outside_stack(d.mem.writes)
    if wo != wc: rows.append(('write-set', len(wo - wc), len(wc - wo), [hex(a) for a in sorted(wo ^ wc)[:8]]))
    if o['calls'] != d.calls_seen: rows.append(('calls', len(o['calls']), len(d.calls_seen)))
    if o['log'] != d.log: rows.append(('checkpoints', len(o['log']), len(d.log)))
    return rows


# Original instructions outside the reconstructed domain (node state 1, and
# 002f7830 variant 0). The C fails closed there with domain faults 0x70/0x72.
OUT_OF_DOMAIN = {
    0x19c8a0: ((0x19c910, 0x19c960),   # state 0: initialise and seed textures
               (0x19cd10, 0x19cd58)),  # states 2 and 3
    0x2f7830: ((0x2f7890, 0x2f78d8),   # variant 1 area-dependent table (sp+b0, read only by variants 1/2)
               (0x2f78f8, 0x2f7c00),   # states other than 1, 3, 6
               (0x2f7c20, 0x2f7c28),   # variant > 2
               (0x2f7d28, 0x2f80b8),   # variants 1 and 2
               (0x2f8a60, 0x2f8ab0)),  # states 6 and 3
}


def unreachable_padding(path=PACKET):
    """nop after the delay slot of an unconditional branch that no branch targets."""
    data = json.loads(Path(path).read_text())
    text = {int(i['address'], 16): i['text'] for row in data['functions'] for i in row['instructions']}
    targets = {int(t.rsplit(' ', 1)[1], 16) for t in text.values()
               if t.split()[0][0] in 'bj' and t.rsplit(' ', 1)[1].startswith('0x')}
    return {a for a, t in text.items()
            if t == 'nop' and a not in targets and text.get(a - 8, '').startswith(('beq zero, zero', 'j '))}


class RenderNodeDifferentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.source = (ROOT / 'src/resource_loader.c').read_text()
        cls.lib = library(output=Path(cls.tmp.name) / 'baseline.so')
        cls.originals = {name: original(sc) for name, sc in SCENARIOS.items()}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def mutant(self, name):
        path = Path(self.tmp.name) / f'{name}.c'
        path.write_text(RN.mutant_source(self.source, name))
        return library(source=path, output=Path(self.tmp.name) / f'{name}.so')

    def test_c_matches_original_instructions_on_poisoned_memory(self):
        for name, sc in SCENARIOS.items():
            with self.subTest(scenario=name):
                o = self.originals[name]
                d = c_run(self.lib, sc)
                self.assertEqual(mismatch(o, d), [])
                self.assertEqual(d.events, [(0x143150, 1), (0x143150, 0)])
                self.assertEqual(d.depth, 0)

    def test_delay_slot_next_and_counter_reload(self):
        # the foreign callback zeroes its own +24 and writes the counter: the
        # field node after it still runs, and the count continues from 0x40.
        for name, o in self.originals.items():
            with self.subTest(scenario=name):
                field_calls = [c for c in o['calls'] if c[0] == 0x14a0a0 and c[2][:2] == (5, 1)]
                self.assertEqual(len(field_calls), 1)
                self.assertEqual(o['mem'].load(GP + 0x2f68, 2), 0x42)  # 0x40 + foreign + field

    def test_branch_coverage(self):
        logs = [o['log'] for o in self.originals.values()]
        emits = sum(1 for log in logs for row in log if row[0] == 0x14d4f0)
        sprites = sum(1 for log in logs for row in log if row[0] == 0x1503f0)
        self.assertGreater(emits, 8); self.assertGreater(sprites, 2)
        # fade clamped to 1 and to 0 (colour quad equal to the full / zero colour)
        colours = {row[1][0x40:0x50] for log in logs for row in log if row[0] == 0x14d4f0}
        self.assertIn(bytes(16), colours)
        # facing clamp (pitch 1.4 > 1.2566): a zero billboard colour exists in active-draw
        self.assertTrue(any(row[1][0x40:0x50] == bytes(16) for row in self.originals['active-draw']['log']
                            if row[0] == 0x14d4f0))
        executed = set().union(*(o['executed'] for o in self.originals.values()))
        padding = unreachable_padding()
        for entry, body in bodies().items():
            with self.subTest(function=hex(entry)):
                excluded = {a for s, e in OUT_OF_DOMAIN.get(entry, ()) for a in range(s, e, 4)}
                self.assertEqual(sorted(hex(a) for a in body - executed - padding - excluded), [])
                self.assertEqual(sorted(hex(a) for a in (body & excluded) & executed), [])
                self.assertEqual(sorted(hex(a) for a in body & padding & executed), [])  # classification sound

    def test_out_of_domain_states_fail_closed(self):
        for label, node_patch, code in (('anchor-state-0', (0, 0x18, 0), 0x70),
                                         ('field-variant-1', (-1, 0x08, 1), 0x72),
                                         ('field-state-2', (-1, 0x18, 2), 0x72)):
            with self.subTest(case=label):
                d = StubDriver(SCENARIOS['release-wait'])
                index, offset, value = node_patch
                d.mem.store(d.nodes[index][0] + offset, value, 1 if offset == 0x18 else 4)
                f = self.lib.hg_render_node_list_fixed
                f.argtypes = [C.POINTER(ResourceEnv)]; f.restype = None
                f(C.byref(d.env))
                self.assertIn(code, d.faults)

    def test_m15_mutants_are_rejected(self):
        for name, defect, *_ in RN.MUTANTS:
            if name in RN.OUTSIDE_RENDER_NODES:
                continue  # hg_camera_update / hg_player_model_draw: other entries and tests
            with self.subTest(mutant=name):
                lib = self.mutant(name)
                failures = {n: mismatch(self.originals[n], c_run(lib, sc)) for n, sc in SCENARIOS.items()}
                self.assertTrue(any(failures.values()), defect)


if __name__ == '__main__':
    unittest.main()
