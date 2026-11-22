"""M14 original-instruction versus C differential helpers for 00198f60.

The original 00198f60 words run in the strict EE subset (tools/ee_subset.py);
the C hg_pre_dispatch_service runs on an identical memory image. Callees are
either host stubs (synthetic tests) or interpreted original instructions
(retail checks). Results compare every byte except the original routine's own
0x20-byte guest frame, which host C does not materialize.

The bridged-callee register contract is recorded in CONTRACT. It lists the
registers the original caller assigns for each call site in 00198f60
(0x198f74..0x199420). Sufficiency of the contract on retail entry states is
checked by tools/verify_predispatch_contracts.py, not assumed here.
"""
import json
from pathlib import Path

from ee_subset import Machine

ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / 'analysis/pre_dispatch/functions.json'
SERVICE = 0x198f60
SERVICE_END = 0x199464
GP = 0x3e7f70
FRAME = 0x20  # sq s0,0(sp) and sd ra,0x10(sp) after addiu sp,sp,-0x20
NATIVE_SP = 0x1fffc70  # 0012f8a8 sp; reproduces the native stack exactly (contracts tool)
GPR = {'v0': 2, 'v1': 3, 'a0': 4, 'a1': 5, 'a2': 6, 'a3': 7,
       't0': 8, 't1': 9, 't2': 10, 't3': 11}
ABI4 = ('a0', 'a1', 'a2', 'a3')
CONTRACT = {
    0x155350: ('f12', 'a0'),            # 198f74 mtc1 f12; 198f80 delay-slot a0
    0x153020: ABI4,                     # 198f98..198fc0
    0x152c50: ABI4,                     # 198fc8..198fd8 (a3 = lh ctx+5c0)
    0x14a0a0: ABI4,                     # 199088/90, 199214/1c; 199414..24
    0x14f060: ABI4 + ('t0', 't1', 't2'),  # 199268..1992c0
    0x152e90: ABI4,                     # 199380..199410
}
CALLEES = tuple(CONTRACT)


def packet_words(path=PACKET):
    """Original 00198f60 words from the original-byte-verified packet."""
    data = json.loads(Path(path).read_text())
    row = next(f for f in data['functions'] if int(f['entry'], 16) == SERVICE)
    words = {int(i['address'], 16): bytes.fromhex(i['bytes']) for i in row['instructions']}
    if sorted(words) != list(range(SERVICE, SERVICE_END, 4)):
        raise ValueError('Incomplete 00198f60 packet')
    return words


def install_service(mem, words):
    for address, raw in words.items():
        mem.put(address, raw)


def contract_values(cpu, pc):
    """Caller-assigned registers at a callee entry, as declared in CONTRACT."""
    row = {}
    for name in CONTRACT[pc]:
        value = cpu.fpr[12] if name == 'f12' else cpu.reg[GPR[name]]
        row[name] = value & 0xffffffff
    return row


def c_contract(pc, a, b, c, d, extra):
    """The same view of a C bridge call: FP seams place operand 0 in f12."""
    if pc == 0x155350:
        row = dict(f12=a, a0=b)
    else:
        row = dict(a0=a, a1=b, a2=c, a3=d)
    if extra is not None:
        row.update(t0=extra[8], t1=extra[9], t2=extra[10])
    undeclared = sorted(k for k, v in row.items() if k not in CONTRACT[pc] and v)
    if undeclared:
        raise ValueError(f'C passes undeclared registers {undeclared} to {pc:#x}')
    return {name: row.get(name, 0) & 0xffffffff for name in CONTRACT[pc]}


def run_original(mem, sp=NATIVE_SP, hooks=None, trace=None, limit=400000):
    machine = Machine()
    machine.mem = mem
    machine.reg[28] = GP
    machine.reg[29] = sp
    machine.hooks = dict(hooks or {})
    machine.trace = trace
    machine.run(SERVICE, limit=limit)
    return machine


def direct_call_recorder(calls):
    """Trace observer: records CONTRACT registers at callees called from 00198f60."""
    def observe(cpu):
        if cpu.pc in CONTRACT and SERVICE <= (cpu.reg[31] & 0xffffffff) < SERVICE_END:
            calls.append((cpu.pc, contract_values(cpu, cpu.pc)))
    return observe


def frame_excluded(sp):
    return range(sp - FRAME, sp)


# ---------------------------------------------------------------------------
# Retail window oracle and write-set comparison (owner-local captures only).

WINDOW = (0x12f8a8, 0x158560)        # caller window: 00198f60, 0017f6d0, 0017f2d0
STACK = (0x1fe0000, 0x2000000)
CURSORS = (0x544, 0x548, 0x54c, 0x550)  # ctx packet cursors advanced in the window
CHUNK = 0x10000


def window_oracle(counter_capture, elfdata=None, segments=(), *, trace_writes=False):
    """Original instructions 0012f8a8 -> 00158560 on a copy of a native
    post-counter capture at sp = NATIVE_SP. Returns the final memory, the exact
    write set and the instruction count; optionally checks ELF identity."""
    from verify_player_connected_runtime import CapturedMemory
    mem = CapturedMemory(bytes(counter_capture.raw), bytes(counter_capture.scratch))
    machine = Machine()
    machine.mem = mem
    machine.reg[28] = GP
    machine.reg[29] = NATIVE_SP
    writers = {}
    if trace_writes:
        store = mem.put

        def traced_store(address, data):
            store(address, data)
            for byte in range(address, address + len(data)):
                writers[byte] = (machine.steps, machine.pc)

        mem.put = traced_store
    machine.run(WINDOW[0], WINDOW[1], limit=400000)
    mismatches = []
    if elfdata is not None:
        for pc in machine.executed:
            offsets = [off + pc - start for start, end, off in segments if start <= pc and pc + 4 <= end]
            if len(offsets) != 1 or counter_capture.raw[pc:pc + 4] != elfdata[offsets[0]:offsets[0] + 4]:
                mismatches.append(pc)
    return dict(memory=mem, writes=set(mem.writes), steps=machine.steps, writers=writers,
                executed=len(machine.executed), identity_mismatches=sorted(mismatches))


def changed(before, after, limit=STACK[0], start=0):
    out = set()
    for base in range(start, limit, CHUNK):
        x, y = before.raw[base:base + CHUNK], after.raw[base:base + CHUNK]
        if x != y:
            out.update(base + i for i in range(len(x)) if x[i] != y[i])
    return out


def _streams(before, after, ctx):
    return {k: (before.peek_word(ctx + k), after.peek_word(ctx + k)) for k in CURSORS}


def _split(writes, streams, cursor_bytes, dma):
    absolute, scratch, stream = set(), set(), {k: set() for k in CURSORS}
    for a in writes:
        if a >= 0x70000000:
            scratch.add(a - 0x70000000); continue
        if STACK[0] <= a < STACK[1] or a in cursor_bytes or any(s <= a < e for s, e in dma):
            continue
        for k, (start, end) in streams.items():
            if start <= a < end:
                stream[k].add(a - start); break
        else:
            absolute.add(a)
    return absolute, scratch, stream


def _sample(values, n=16, base=0):
    return [hex(base + v) for v in sorted(values)[:n]]


def compare_window(before, after, writes, native_before, native_after, oracle_writes, dma=()):
    """Window outputs of a reconstructed run versus the original's.

    before/after: reconstructed memory at coordinator-counter and pre-dispatch;
    writes: addresses the reconstruction stored in between. native_*: the
    captured post-counter and dispatch-entry stops; oracle_writes: the exact
    original-instruction write set for the same native window. The packet
    streams of the four ctx cursors are compared relative to each cursor's
    start, so a reconstruction whose cursor began elsewhere is still checked
    offset by offset. Guest stack (host frames differ) and libpad DMA buffers
    are the only exclusions. Every other window write, either side, must be
    in both write sets with the native dispatch-entry value."""
    ctx_r, ctx_n = after.peek_word(GP + 0x3098), native_after.peek_word(GP + 0x3098)
    sr, sn = _streams(before, after, ctx_r), _streams(native_before, native_after, ctx_n)
    cursor_bytes = set(range(ctx_r + 0x544, ctx_r + 0x554)) | set(range(ctx_n + 0x544, ctx_n + 0x554))
    rec_abs, rec_scr, rec_str = _split(writes, sr, cursor_bytes, dma)
    ora_abs, ora_scr, ora_str = _split(oracle_writes, sn, cursor_bytes, dma)
    native = changed(native_before, native_after)
    native = {a for a in native if not any(s <= a < e for s, e in dma)}
    native_ok = {a for a in native if a in ora_abs or a in cursor_bytes or
                 any(s <= a < e and a - s in ora_str[k] for k, (s, e) in sn.items())}
    abs_all = rec_abs | ora_abs
    abs_diff = {a for a in abs_all if after.raw[a] != native_after.raw[a]}
    scr_all = rec_scr | ora_scr
    scr_diff = {o for o in scr_all if after.scratch[o] != native_after.scratch[o]}
    streams = []
    for k in CURSORS:
        (ri, ro), (ni, no) = sr[k], sn[k]
        offsets = rec_str[k] | ora_str[k]
        diff = {o for o in offsets if ri + o >= 0x2000000 or ni + o >= 0x2000000 or
                after.raw[ri + o] != native_after.raw[ni + o]}
        row = dict(cursor=hex(k), reconstructed=[hex(ri), hex(ro)], native=[hex(ni), hex(no)],
                   delta_matches=ro - ri == no - ni, written_offsets=len(ora_str[k]),
                   missing_offsets=len(ora_str[k] - rec_str[k]), unexpected_offsets=len(rec_str[k] - ora_str[k]),
                   value_differences=len(diff), first_value_differences=_sample(diff),
                   first_missing=_sample(ora_str[k] - rec_str[k]), first_unexpected=_sample(rec_str[k] - ora_str[k]))
        row['result'] = 'passed' if (row['delta_matches'] and not diff and
                                     not row['missing_offsets'] and not row['unexpected_offsets']) else 'failed'
        streams.append(row)
    native_changed_scratch = {i for i, (x, y) in enumerate(zip(native_before.scratch, native_after.scratch))
                              if x != y}
    unmodeled_native_scratch = native_changed_scratch - ora_scr
    result = dict(
        ctx_matches=ctx_r == ctx_n,
        absolute_outputs=len(abs_all), absolute_value_differences=len(abs_diff),
        first_absolute_differences=_sample(abs_diff),
        absolute_missing=_sample(ora_abs - rec_abs), absolute_missing_count=len(ora_abs - rec_abs),
        absolute_unexpected=_sample(rec_abs - ora_abs), absolute_unexpected_count=len(rec_abs - ora_abs),
        scratch_outputs=len(scr_all), scratch_value_differences=len(scr_diff),
        first_scratch_differences=_sample(scr_diff, base=0x70000000),
        scratch_missing_count=len(ora_scr - rec_scr), scratch_unexpected_count=len(rec_scr - ora_scr),
        first_scratch_missing=_sample(ora_scr - rec_scr, base=0x70000000),
        first_scratch_unexpected=_sample(rec_scr - ora_scr, base=0x70000000),
        native_scratch_changed_outside_oracle=len(unmodeled_native_scratch),
        first_unmodeled_native_scratch=_sample(unmodeled_native_scratch, base=0x70000000),
        native_changed=len(native), native_changed_outside_oracle=len(native - native_ok),
        streams=streams)
    result['result'] = 'passed' if (
        result['ctx_matches'] and not abs_diff and not scr_diff and
        not result['absolute_missing_count'] and not result['absolute_unexpected_count'] and
        not result['scratch_missing_count'] and not result['scratch_unexpected_count'] and
        not result['native_changed_outside_oracle'] and not unmodeled_native_scratch and
        all(row['result'] == 'passed' for row in streams)) else 'failed'
    return result


def window_difference_writers(comparison, actual, expected, writers):
    """Localize sampled differing/missing bytes to the last original store."""
    samples = {}
    for domain, keys in (
        ('absolute', ('first_absolute_differences', 'absolute_missing', 'absolute_unexpected')),
        ('scratch', ('first_scratch_differences', 'first_scratch_missing', 'first_scratch_unexpected')),
    ):
        for key in keys:
            for value in comparison[key]:
                address = int(value, 16)
                samples[domain, address, address] = None
    for stream in comparison['streams']:
        actual_start = int(stream['reconstructed'][0], 16)
        expected_start = int(stream['native'][0], 16)
        for key in ('first_value_differences', 'first_missing', 'first_unexpected'):
            for value in stream[key]:
                offset = int(value, 16)
                samples[stream['cursor'], actual_start + offset, expected_start + offset] = value

    def key(sample):
        _, actual_address, expected_address = sample
        return writers.get(expected_address, (float('inf'), 0))[0], expected_address, actual_address

    def read_byte(memory, address):
        try:
            return memory.peek(address, 1).hex()
        except ValueError:
            return None

    return [dict(domain=domain, stream_offset=samples[domain, a, b],
                 actual_address=hex(a), expected_address=hex(b),
                 actual=read_byte(actual, a), expected=read_byte(expected, b),
                 original_writer=hex(writers[b][1]) if b in writers else None,
                 original_step=writers[b][0] if b in writers else None)
            for domain, a, b in sorted(samples, key=key)[:16]]


# ---------------------------------------------------------------------------
# Compiled 00198f60 defects shared by the synthetic test and retail controls.

QUAD_PRE_FIX = '''static void sprite_quad_packet(HGResourceEnv *e,uint32_t ctx,uint64_t xy,uint64_t last) {
    uint32_t at=r32(e,ctx+0x550);
    w8(e,at+3,0x10);w32(e,at+4,0);w16(e,at,3);
    w32(e,ctx+0x550,at+0x40);
    w32(e,at+0x1c,0x50000002);
    for(uint32_t q=0x20;q<=0x38;q+=8) {
        uint64_t v=q==0x20?0x1000000000008001ull:q==0x28?14:q==0x30?xy:last;
        sprite_sd(e,at+q,v);w32(e,at+q+8,0);w32(e,at+q+12,0); /* 128-bit */
    }
}'''
SQ_ZERO = '    for(uint32_t i=0;i<16;i+=4)w32(e,at+0x10+i,0);   /* sq $zero,0x10 */\n'
NEGATIVE_WRAP = '        if(flt(e,phase,0))phase=fadd(e,phase,r32(e,s+8)); /* add.s f0,f1,f0 */\n'
PHASE_STEP = '        uint32_t phase=fadd(e,r32(e,s+0xc),step);\n'
WRAP_TEST = '        if(!fle(e,phase,r32(e,s+8)))w32(e,s+0xc,fsub(e,phase,r32(e,s+8)));\n'
T_REGS = '        call(e,HG_CALL_T_REGS,0x70000ba0,ctx+0x5b0,s+0x18,0);\n'
FINAL_CALL = '    call(e,0x152e90,5,0x381880,packet,ctx);\n'


def quad_function(source):
    start = source.index('static void sprite_quad_packet(')
    return source[start:source.index('\n}\n', start) + 2]


def mutants(source):
    """name -> (kind, original text, replacement); each original occurs once."""
    rows = {
        'store-width-pre-fix': ('store', quad_function(source), QUAD_PRE_FIX),
        'missing-sq-zero': ('store', SQ_ZERO, ''),
        'negative-wrap-skipped': ('branch', NEGATIVE_WRAP, ''),
        'phase-step-sign': ('arithmetic', PHASE_STEP, PHASE_STEP.replace('fadd', 'fsub')),
        'wrap-test-inverted': ('branch', WRAP_TEST, WRAP_TEST.replace('if(!fle', 'if(fle')),
        't1-omitted': ('contract', T_REGS, T_REGS.replace('ctx+0x5b0', '0')),
        '152e90-pre-fix-arguments': ('contract', FINAL_CALL, FINAL_CALL.replace('packet,ctx', '0x4a,14')),
    }
    for name, (_, old, _) in rows.items():
        if source.count(old) != 1:
            raise ValueError(f'mutant target not unique: {name}')
    return rows


def mutant_source(source, name):
    _, old, new = mutants(source)[name]
    return source.replace(old, new)
