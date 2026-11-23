#!/usr/bin/env python3
"""M15 original-instruction oracles for the coordinator tail render services.

00143150 (called at 0012f900) walks the render-node list at 00539458 and calls
each node's +1c callback. In the observed corridor frames the list holds four
nodes; 0019c8a0 and 002f7830 produce the camera-relative screen-space scratch
(70000780..7ff, 70000900..94f, 70000b80..bef). The C reconstruction is
src/resource_loader.c hg_render_node_list.

This module provides:
  * run_original: original instructions for one entry point on a copy of a
    memory image, with the exact write set, executed-instruction identity
    against the authenticated ELF and the number of 0011ad58 RNG calls;
  * compare_same_input: C output vs the original on the identical input
    (absolute RAM, scratchpad and write sets; only the guest stack excluded);
  * lcg_distance: steps of the 0011ad58/00114010 LCG between two states.

The original is the oracle; owner captures remain comparison inputs. No
emulator, hardware timing or byte matching is implied.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools')]
from ee_subset import Machine
import predispatch_differential as PD

TAIL = 0x143150            # coordinator call site 0012f900
CAMERA = 0x12c790          # coordinator call site 0012f8cc
MODEL = 0x12bc70           # coordinator call site 0012f8f0 (player model draw)
LATE = 0x13ce80            # coordinator call site 0012f924 (M16 late-frame render)
PARTICLE_TAG = 0x20064405dd413200  # 002f7830 -> 0014d4f0 a3
SHADOW_TAG = 0x20072a2599423880    # 00129e40 -> 0014d4f0 a3
RNG = 0x11ad58             # random_next: state at [*(0037b208)+0xa8], 64-bit LCG
RNG_POINTER = 0x37b208
LCG_MULTIPLIER = 0x5851f42d4c957f2d
MASK64 = (1 << 64) - 1
STACK = PD.STACK
SAMPLE = 16


def rng_state_address(memory):
    return memory.peek_word(RNG_POINTER) + 0xa8


def rng_state(memory):
    address = rng_state_address(memory)
    return int.from_bytes(memory.peek(address, 8), 'little')


def lcg_distance(start, end, limit=256):
    """Number of 0011ad58 steps (state = state * M + 1) from start to end."""
    state = start
    for steps in range(limit + 1):
        if state == end:
            return steps
        state = (state * LCG_MULTIPLIER + 1) & MASK64
    raise ValueError('RNG states are not within the bounded LCG distance')


def copy_memory(memory):
    from verify_player_connected_runtime import CapturedMemory
    copy = CapturedMemory(bytes(memory.raw), bytes(memory.scratch))
    copy.scratch_known = set(getattr(memory, 'scratch_known', range(0x4000)))
    return copy


def run_original(memory, entry=TAIL, elfdata=None, segments=(), *, a0=0, sp=PD.NATIVE_SP,
                 limit=4000000, area=None):
    """Original instructions from entry (or each entry of a tuple, in order)
    to its return on a copy of memory.

    memcpy 00119910 and memset 00119ac8 are applied as byte copies/fills (as
    the M8+ instruction driver does): their MMI quadword loops are outside the
    interpreter subset and their effect is exactly the copied/filled bytes.
    area=(bytes, base) adds the authenticated AREA1 file identity check for
    executed addresses outside the ELF PT_LOAD (M16 0013ce80's area body)."""
    mem = copy_memory(memory)
    machine = Machine()
    machine.mem = mem
    machine.reg[28] = PD.GP
    machine.reg[29] = sp
    machine.reg[4] = a0

    def memset(cpu):
        a, b, c = (cpu.reg[i] & 0xffffffff for i in (4, 5, 6))
        mem.put(a, bytes([b & 255]) * c)
        return a

    def memcpy(cpu):
        a, b, c = (cpu.reg[i] & 0xffffffff for i in (4, 5, 6))
        mem.put(a, mem.get(b, c))
        return a

    machine.hooks = {0x119ac8: memset, 0x119910: memcpy}
    calls = [0]

    def observe(cpu):
        if cpu.pc == RNG:
            calls[0] += 1

    machine.trace = observe
    steps = 0
    for start in (entry if isinstance(entry, tuple) else (entry,)):
        machine.reg[28] = PD.GP; machine.reg[29] = sp; machine.reg[4] = a0
        machine.run(start, limit=limit)
        steps += machine.steps
    mismatches = []
    if elfdata is not None:
        area_bytes, area_base = area if area is not None else (b'', 0)
        for pc in machine.executed:
            offsets = [off + pc - start for start, end, off in segments if start <= pc and pc + 4 <= end]
            if len(offsets) == 1:
                if memory.raw[pc:pc + 4] != elfdata[offsets[0]:offsets[0] + 4]:
                    mismatches.append(pc)
            elif area_base <= pc < area_base + len(area_bytes):
                if memory.raw[pc:pc + 4] != area_bytes[pc - area_base:pc - area_base + 4]:
                    mismatches.append(pc)
            else:
                mismatches.append(pc)
    return dict(memory=mem, writes=set(mem.writes), steps=steps, rng_calls=calls[0],
                executed=len(machine.executed), identity_mismatches=sorted(mismatches))


def _outside_stack(address):
    return not STACK[0] <= address < STACK[1]


def _byte(memory, address):
    if address >= 0x70000000:
        return memory.scratch[address - 0x70000000]
    return memory.raw[address]


def compare_same_input(after, writes, oracle, label):
    """Reconstruction vs original on the same input: every byte either side
    writes (outside the guest stack) must be written by both and carry equal
    values; all other RAM and the full scratchpad must also be equal."""
    expected = oracle['memory']
    c_writes = {a for a in writes if _outside_stack(a)}
    o_writes = {a for a in oracle['writes'] if _outside_stack(a)}
    missing = sorted(o_writes - c_writes)
    extra = sorted(c_writes - o_writes)
    values = sorted(a for a in c_writes | o_writes if _byte(after, a) != _byte(expected, a))
    ram = sorted(PD.changed(after, expected, STACK[0]))
    scratch = [0x70000000 + i for i in range(0x4000) if after.scratch[i] != expected.scratch[i]]
    failed = bool(missing or extra or values or ram or scratch)

    def sample(rows):
        return [dict(address=hex(a), reconstructed=hex(_byte(after, a)), original=hex(_byte(expected, a)))
                for a in rows[:SAMPLE]]

    return dict(result='failed' if failed else 'passed', service=label,
                compared_writes=len(c_writes | o_writes), oracle_steps=oracle['steps'],
                oracle_rng_calls=oracle['rng_calls'],
                missing_writes=len(missing), missing_samples=[hex(a) for a in missing[:SAMPLE]],
                extra_writes=len(extra), extra_samples=[hex(a) for a in extra[:SAMPLE]],
                value_differences=len(values), value_samples=sample(values),
                ram_differences=len(ram), ram_samples=sample(ram),
                scratch_differences=len(scratch), scratch_samples=sample(scratch),
                identity_mismatches=[hex(a) for a in oracle['identity_mismatches'][:SAMPLE]])


# M15 compiled defects: projection arithmetic, missing write, changed order and
# unconditional store, each a one-site source mutation of src/resource_loader.c:
# (name, defect, original text, mutated text, retail target frame).
MUTANTS = (
    ('anchor-offset-sign', 'anchor projection arithmetic (local y -1.5 -> +1.5)',
     'w32(e,0x70000b80,0);w32(e,0x70000b84,0xbfc00000u);',
     'w32(e,0x70000b80,0);w32(e,0x70000b84,0x3fc00000u);', 10165),
    ('billboard-translation-sign', 'billboard arithmetic (row 3 += position -> -= position)',
     'for(int i=0;i<3;i++)t[i]=vadd(e,t[i],p[i]);',
     'for(int i=0;i<3;i++)t[i]=vsub(e,t[i],p[i]);', 10165),
    ('missing-anchor-row', 'missing write (70000940 local-to-world row)',
     '    copy_quad(e,0x70000940,node+0x30);                  /* local-to-world row 3 */\n', '', 10165),
    ('anchor-texture-order', 'changed ordering (second texture word before first sprite)',
     '    w32(e,0x7000090c,r32(e,s3+0x40));\n', '    w32(e,0x7000090c,r32(e,s3+0x44));\n', 10165),
    ('quad-emission-order', 'changed ordering (horizontal quad before billboard)',
     'particle_quad(e,pitch,r32(e,0x3f9934),1);     /* camera-facing billboard */',
     'particle_quad(e,0x3fc90fdbu,0,0);', 10165),
    ('unconditional-anchor-draw', 'unconditional store (anchor sprite drawn in sub-state 1)',
     '    if(sub==1) {                                        /* 0019c9c8: wait for release */\n',
     '    if(sub==1)anchor_draw(e,node);\n    if(sub==1) {\n', 10173),
    ('camera-return-projection-store', 'unconditional projection store at camera return',
     '    w8(e,camera+0x140,0);',
     '    w32(e,0x70000940,0x4325cccdu);w32(e,0x70000944,0x43340000u);\n'
     '    w32(e,0x70000948,0x43f78000u);w32(e,0x7000094c,0x3f800000u);\n    w8(e,camera+0x140,0);', 10165),
    ('shadow-scale-sign', 'shadow projection arithmetic (00129e40 x scale 4.2 -> -4.2)',
     'w32(e,0x70000514,0x40866666u);w32(e,0x70000500,0x40866666u);',
     'w32(e,0x70000514,0x40866666u);w32(e,0x70000500,0xc0866666u);', 10165),
    ('light-unnormalized', 'light arithmetic (70000710 = v instead of v * rsqrt(v.v))',
     '    for(int i=0;i<3;i++)w32(e,0x70000710+4*i,vmul(e,v[i],q));\n',
     '    for(int i=0;i<3;i++)w32(e,0x70000710+4*i,vmul(e,v[i],0x3f800000u|(q&0)));\n', 10165),
    ('missing-light-w', 'missing write (7000071c light w lane)',
     '    w32(e,0x7000071c,w);\n', '    (void)w;\n', 10165),
    ('light-column-order', 'changed ordering (light matrix column copied before 70000710 is normalized)',
     '    for(int i=0;i<3;i++)w32(e,0x70000710+4*i,vmul(e,v[i],q));\n    w32(e,0x7000071c,w);\n'
     '    w32(e,lights+0x08,r32(e,0x70000710));w32(e,lights+0x18,r32(e,0x70000714));\n'
     '    w32(e,lights+0x28,r32(e,0x70000718));w32(e,lights+0x38,0);\n',
     '    w32(e,lights+0x08,r32(e,0x70000710));w32(e,lights+0x18,r32(e,0x70000714));\n'
     '    w32(e,lights+0x28,r32(e,0x70000718));w32(e,lights+0x38,0);\n'
     '    for(int i=0;i<3;i++)w32(e,0x70000710+4*i,vmul(e,v[i],q));\n    w32(e,0x7000071c,w);\n', 10165),
)


# Mutants of code outside the 00143150 entry: the camera-return store (retail
# control only) and the 0012bc70 model draw (tests/test_model_draw_differential.py).
OUTSIDE_RENDER_NODES = ('camera-return-projection-store', 'shadow-scale-sign', 'light-unnormalized',
                        'missing-light-w', 'light-column-order')
MODEL_DRAW_MUTANTS = OUTSIDE_RENDER_NODES[1:]


def mutant_source(source, name):
    for row in MUTANTS:
        if row[0] == name:
            if source.count(row[2]) != 1:
                raise AssertionError(f'{name} target not unique')
            return source.replace(row[2], row[3])
    raise KeyError(name)
