#!/usr/bin/env python3
"""M18 connected native lifecycle runner (runtime comparison).

Starts from ONE native full capture (EE RAM + scratchpad at a 00143240 stop)
and runs the reconstructed C frame by frame: hg_main_unit = mode dispatch
00143240 (M17 lifecycle C + M18 front-end C) -> main-loop tail (M18 fade
service 00139240, vblank wait with the C handlers) -> main-loop head (M18
controller service 0013d970) up to the next 00143240 call. After every unit
the C-owned state is compared with the next native stop record. C-owned state
is carried from unit to unit and never restored.

Declared external inputs (not reconstructed here; injected or modeled):
  E1 libpad/IOP: the DMA buffer 0095e340..0095e440 of stop N+1; scePadGetState
     0010dcf8 -> 6 (stable); scePadRead 0010db90 runs as original instructions
     (with DI/EI/cache kernel helpers 0010cca0/0010cce8/00104858 as no-ops).
  E2 vblank timing: the native 0095e300 delta of the frame is the number of
     vblank-start/-end handler pairs (C) delivered at the vblank wait.
  E3 loader thread (EE thread 00130220 + IOP CD reads): the loader cells
     (003eace0..003eacf0, task 003f8e00..003f8e80) of stop N. Requests
     00130150/00130170 run the existing C request functions; each request
     (task +32 kind, +44 resource id) and each native busy rise is checked
     against the native task record.
  E4 kernel: CreateThread 00103d60 returns the native task+34 of stop N+1.
  E5 the name-entry/contract/save UI 002cf330: returns 1 on the frame the
     native mode left gameplay major 1 (replayed external decision).
  E6 (removed: the title card poll 00a87de0 is C.)
  E7 memory card (no card inserted): sceMc sync 001783c0 -> -1, get-info
     001782a0 -> 0, matching the captured scan state.
  E8 analog-stick conversion 0013e5b0: its outputs (pad +1f8..+223) of stop
     N+1. libc memset/memcpy 00119ac8/00119910 are applied by their library
     semantics (their MMI bodies are outside the strict interpreter subset).
  E10 externally requested fades: only in the declared EXTERNAL_FADE mode
     contexts (callers in the scene/actor system), when the native stop N+1
     shows a fresh fade start, the C made no fade-setter call in that frame
     and the C fade block differs, the frame is re-run with the setter calls
     recovered from the native fields injected before dispatch: 00139040 for
     a fade-out; 001390b0 for a fade-in, preceded by 00139010 when it starts
     from a clear screen; 001390b0 alone when a running fade-in is re-stepped
     (new step, level advanced by it). Every injection is reported.
  E11 render frame start 00148970, pause-capture handshake only: when the
     pause fade has set 700000d4 = 1 it captures the frame (00149580) and sets
     700000d4 = 2 (0014909c..001490b4). 700000d4 stays C-compared.
  E12 title minor 1: mode +1a (presentation timer) of stop N+1; the title
     scene (00141470, actors) clears it when the presentation ends.
  E13 title scene setup 00a88660 (overlay, scene/actor construction): its
     fade-in 001390b0(step, blend, layer) is recovered from a fresh native
     fade-in at stop N+1 and run through the C setter; reported.
  The music-fade words gp+2f48..2f53 are advanced by the external sound
  service (00156740) and are taken from stop N+1; their C setters are checked
  by the original-instruction differential only.
  E9 every other callee: no memory effect, return 0, recorded. All other
     bytes of the per-frame record regions (scene, actors, render, sound) are
     taken from stop N+1.

    python3 -B tools/run_front_end_native.py --native local/m18/native --start title-menu-input-ready \
        [--frames N] [--output out.json] [--defect NAME]
"""
import argparse
import ctypes as C
import hashlib
import json
import struct
import sys
import zlib
from pathlib import Path
from hg_verify_build import build_path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import front_end_differential as F
import lifecycle_differential as L
ROOT = F.ROOT
from ee_subset import Machine

GP = 0x3e7f70
REGIONS = [('gp', 0x3eac00, 0x500), ('pad', 0x95e300, 0x2c0), ('game', 0x3fa080, 0x180),
           ('play', 0x3f9e00, 0x180), ('misc', 0x46d880, 0x100), ('misc2', 0x46e100, 0x40),
           ('task', 0x3f8e00, 0x80), ('arena', 0xa87800, 0x100), ('sp', 0x70000000, 0xc00)]
C_OWNED = [  # (name, start, end_exclusive)
    ('mode block', 0x3ead30, 0x3ead58), ('fade block', GP + 0x2f40, GP + 0x2f48),
    ('counters', GP + 0x2f7c, GP + 0x2f88), ('pad copies', GP + 0x2f90, GP + 0x2fcc),
    ('pad port/buttons', 0x95e440, 0x95e538), ('pad buttons', 0x95e564, 0x95e580),
    ('vblank', 0x95e300, 0x95e304), ('vblank flag', 0x95e308, 0x95e30c),
    ('scratch c4', 0x700000c4, 0x700000c8), ('scratch cc', 0x700000cc, 0x700000d0),
    ('scratch d4', 0x700000d4, 0x700000d8), ('scratch f8', 0x700000f8, 0x70000100),
    ('save flag', 0x70000410, 0x70000414), ('area triple', 0x3fa1b0, 0x3fa1b4),
    ('3fa1c4', 0x3fa1c4, 0x3fa1c6),
]
LOADER = [(0x3eace0, 0x3eacf0), (0x3f8e00, 0x3f8e80)]
DMA = (0x95e340, 0x95e440)
ANALOG = (0x95e538, 0x95e564)
INTERP = {0x10db90}
LIBC = {0x119ac8: 'memset', 0x119910: 'memcpy'}
KERNEL_HOOKS = {0x10cca0: 1, 0x10cce8: 0, 0x104858: 0}
FADE_SETTERS = {0x138fd0, 0x139010, 0x139040, 0x1390b0}
EXTERNAL_FADE = {(0x121be0, 1, 3),   # (handler, major, minor): title gate cutscene (scene/actors)
                 (0x121be0, 1, 1),   # title presentation scene 00141470 (re-steps the fade-in)
                 (0x12d1c0, 1, 1)}   # name-entry/contract/save UI 002cf330 (E5)
SEAMS = (L.HG_CALL_T_REGS, F.HG_CALL_T3_REG, L.HG_CALL_FP_REGS)


class Mem:
    def __init__(self, ee, sp):
        self.ee, self.sp = bytearray(ee), bytearray(sp)

    def _where(self, a, n):
        if 0 <= a and a + n <= 0x2000000:
            return self.ee, a
        if 0x70000000 <= a and a + n <= 0x70004000:
            return self.sp, a - 0x70000000
        if 0x10000000 <= a < 0x10010000:
            return None, 0
        raise ValueError(f'unmapped {a:#x}+{n}')

    def read(self, a, n):
        buf, o = self._where(a, n)
        return bytes(n) if buf is None else bytes(buf[o:o + n])

    def write(self, a, data):
        buf, o = self._where(a, len(data))
        if buf is not None:
            buf[o:o + len(data)] = data

    def __getitem__(self, a):
        return self.read(a, 1)[0]

    def update(self, pairs):
        for a, x in pairs:
            self.write(a, bytes([x]))


def load_frames(native):
    out = {}
    with open(Path(native) / 'frames.jsonl') as fp:
        for line in fp:
            r = json.loads(line)
            if r.get('pc') == '00143240':
                out[r['seq']] = {name: bytes.fromhex(r[name]) for name, _, _ in REGIONS}
    return out


def rec_read(rec, a, n):
    for name, base, size in REGIONS:
        if base <= a and a + n <= base + size:
            return rec[name][a - base:a - base + n]
    return None


def in_owned(a):
    return any(s <= a < e for _, s, e in C_OWNED)


class Runner:
    def __init__(self, lib, mem, frames, start_seq):
        self.lib, self.mem, self.frames, self.seq = lib, mem, frames, start_seq
        self.pending, self.calls, self.requests, self.faults, self.interp_counts = {}, {}, [], [], {}
        self.interp_pcs = set()
        self.vblanks = 1
        self.setter_calls = 0
        self.injections = []
        self.env = L.Env(None, L.LOAD(self._load), L.STORE(self._store), L.CALL(self._call), None, None)
        lib.hg_main_unit.argtypes = [C.POINTER(L.Env)]
        lib.hg_main_unit.restype = None
        for name in ('hg_front_end_call', 'hg_lifecycle_call'):
            fn = getattr(lib, name)
            fn.argtypes = [C.POINTER(L.Env)] + [C.c_uint32] * 5 + [C.POINTER(C.c_uint32)]
            fn.restype = C.c_int
        for name in ('hg_vblank_start_handler', 'hg_vblank_end_handler'):
            getattr(lib, name).argtypes = [C.POINTER(L.Env)]
            getattr(lib, name).restype = C.c_uint32
        lib.hg_pad_pressure.argtypes = [C.POINTER(L.Env)] + [C.c_uint32] * 3
        lib.hg_pad_pressure.restype = C.c_uint32
        for name in ('hg_resource_request_raw', 'hg_resource_request'):
            getattr(lib, name).argtypes = [C.POINTER(L.Env), C.c_uint32, C.c_uint32]
            getattr(lib, name).restype = None
        lib.hg_fade_in.argtypes = [C.POINTER(L.Env)] + [C.c_uint32] * 4
        lib.hg_fade_in.restype = None
        self.code = None

    def _load(self, _, a, n):
        try:
            return int.from_bytes(self.mem.read(a, n), 'little')
        except Exception as exc:
            self.faults.append(f'load {exc}')
            return 0

    def _store(self, _, a, n, v):
        try:
            self.mem.write(a, (v & ((1 << (8 * n)) - 1)).to_bytes(n, 'little'))
        except Exception as exc:
            self.faults.append(f'store {exc}')

    def note(self, pc):
        self.calls[pc] = self.calls.get(pc, 0) + 1

    def interp(self, pc, args, hooks=None):
        m = Machine()
        m.mem = self.mem
        m.reg[28] = GP
        m.reg[29] = 0x1ffe000
        for i, v in enumerate(args):
            m.reg[4 + i] = v - (1 << 32) if v & 0x80000000 else v
        m.hooks = {h: (lambda v: (lambda cpu: v))(v) for h, v in KERNEL_HOOKS.items()}
        if hooks:
            m.hooks.update(hooks)
        m.run(pc, limit=5_000_000)
        self.interp_counts[pc] = self.interp_counts.get(pc, 0) + 1
        self.interp_pcs |= set(m.executed)
        return m.reg[2] & 0xffffffff

    def _call(self, _, pc, a, b, c, d):
        try:
            return self.route(pc, a, b, c, d)
        except Exception as exc:
            self.faults.append(f'{pc:#x}: {exc!r}')
            return 0

    def route(self, pc, a, b, c, d):
        if pc == L.HG_CALL_T_REGS:
            self.pending.update(t0=a, t1=b, t2=c); return 0
        if pc == F.HG_CALL_T3_REG:
            self.pending.update(t3=a); return 0
        if pc == L.HG_CALL_FP_REGS:
            self.pending.update(f12=a, f13=b, f14=c); return 0
        if pc == L.HG_GUEST_FRAME:
            return (0x1fff000 - a) & 0xffffffff if b else 0
        if pc == L.HG_DOMAIN_FAULT:
            self.faults.append(f'domain fault {a:#x} at seq {self.seq}'); return 0
        if pc == L.HG_WAIT_INTERRUPT:
            for _ in range(self.vblanks):
                self.lib.hg_vblank_start_handler(C.byref(self.env))
                self.lib.hg_vblank_end_handler(C.byref(self.env))
            self.vblanks = 0
            return 0
        pend, self.pending = self.pending, {}
        if pc == 0x13e8f0:
            return self.lib.hg_pad_pressure(C.byref(self.env), a, pend.get('f12', 0), pend.get('f13', 0))
        r = C.c_uint32()
        if pc in FADE_SETTERS:
            self.setter_calls += 1
        if self.lib.hg_front_end_call(C.byref(self.env), pc, a, b, c, d, C.byref(r)):
            return r.value
        if self.lib.hg_lifecycle_call(C.byref(self.env), pc, a, b, c, d, C.byref(r)):
            return r.value
        if pc in (0x130150, 0x130170):
            self.requests.append(dict(seq=self.seq, entry=f'{pc:08x}', kind=a, directory=b))
            fn = 'hg_resource_request_raw' if pc == 0x130150 else 'hg_resource_request'
            getattr(self.lib, fn)(C.byref(self.env), a, b)
            return 0
        if pc in INTERP:
            return self.interp(pc, (a, b, c, d))
        if pc in LIBC:                     # declared libc semantics (memset/memcpy)
            self.note(pc)
            if pc == 0x119ac8:
                self.mem.write(a, bytes([b & 0xff]) * c)
            else:
                self.mem.write(a, self.mem.read(b, c))
            return a
        self.note(pc)
        nxt = self.frames.get(self.seq + 1)
        if pc == 0x10dcf8:
            return 6
        if pc == 0x148970 and self.mem.read(0x700000d4, 4) == b'\x01\0\0\0':
            self.mem.write(0x700000d4, (2).to_bytes(4, 'little'))     # E11
            self.note(0x149580)
        if pc == 0x103d60:
            return int.from_bytes(rec_read(nxt, 0x3f8e34, 4), 'little') if nxt else 0
        if pc == 0x2cf330:
            cur = self.mem.read(0x3ead38, 1)[0]
            return 1 if nxt and cur == 1 and rec_read(nxt, 0x3ead38, 1)[0] == 5 else 0
        if pc == 0x1783c0:
            return 0xffffffff
        if pc == 0xa88660 and nxt:         # E13: the scene setup's fade-in, from stop N+1
            f1 = rec_read(nxt, GP + 0x2f40, 8)
            level, step = struct.unpack_from('<hh', f1, 4)
            if f1[0] == 1 and f1[3] == 2 and level == 0xff - step:
                self.lib.hg_fade_in(C.byref(self.env), step & 0xffff, f1[2], f1[1], 0)
                self.injections.append(dict(seq=self.seq + 1, model='E13',
                                            calls=[['001390b0', [step & 0xffff, f1[2], f1[1], 0]]]))
        return 0

    def inject(self, cur, nxt):
        # E9 (and E1, E8): every non-C-owned byte of the record regions from stop N+1 ...
        for name, base, size in REGIONS:
            data = nxt[name]
            cbuf = bytearray(self.mem.read(base, size))
            for i in range(size):
                if not in_owned(base + i):
                    cbuf[i] = data[i]
            self.mem.write(base, bytes(cbuf))
        # ... except the loader cells, which are those of stop N (E3).
        for s, e in LOADER:
            self.mem.write(s, rec_read(cur, s, e - s))
        # E12: in title minor 1 the scene ends the presentation by clearing mode +1a.
        if mode_of(cur)[:3] == (0x121be0, 1, 1):
            self.mem.write(0x3ead4a, rec_read(nxt, 0x3ead4a, 2))

    def compare(self, nxt):
        diffs = []
        for name, s, e in C_OWNED:
            want = rec_read(nxt, s, e - s)
            got = self.mem.read(s, e - s)
            if want != got:
                diffs.append(dict(domain=name, address=f'{s:#x}', native=want.hex(), c=got.hex()))
        return diffs

    def external_fade(self, cur, nxt):
        """Setter call recoverable from a fresh native fade start in a declared context."""
        if mode_of(cur)[:3] not in EXTERNAL_FADE:
            return None
        f0, f1 = rec_read(cur, GP + 0x2f40, 8), rec_read(nxt, GP + 0x2f40, 8)
        level, step = struct.unpack_from('<hh', f1, 4)
        if f1[0] == 3 and f1[3] in (3, 4, 5, 7) and level == step and f0[0] != 3:
            mode = {3: 0, 4: 1, 5: 2, 7: 3}[f1[3]]
            return [(0x139040, (step & 0xffff, f1[2], mode, f1[1]))]
        if f1[0] == 1 and f1[3] == 2 and level == 0xff - step and f0[0] != 1:
            fade_in = (0x1390b0, (step & 0xffff, f1[2], f1[1], 0))
            if struct.unpack_from('<h', f0, 4)[0] != 0xff:    # from a clear screen: black, then fade in
                return [(0x139010, (f1[2], f1[1], 0, 0)), fade_in]
            return [fade_in]
        l0, s0 = struct.unpack_from('<hh', f0, 4)
        if f0[0] == 1 and f1[0] == 1 and f1[3] == 2 and step != s0 and level == max(l0 - step, 0):
            return [(0x1390b0, (step & 0xffff, f1[2], f1[1], 0))]   # re-stepped fade-in
        return None

    def unit(self):
        cur, nxt = self.frames[self.seq], self.frames[self.seq + 1]
        self.inject(cur, nxt)
        v0 = int.from_bytes(rec_read(cur, 0x95e300, 4), 'little')
        v1 = int.from_bytes(rec_read(nxt, 0x95e300, 4), 'little')
        self.vblanks = (v1 - v0) & 0xffffffff
        nreq = len(self.requests)
        snap = (bytes(self.mem.ee), bytes(self.mem.sp), len(self.requests), self.vblanks)
        self.setter_calls = 0
        self.lib.hg_main_unit(C.byref(self.env))
        diffs = self.compare(nxt)
        setter = self.external_fade(cur, nxt) if diffs and not self.setter_calls else None
        if setter and any(d['domain'] == 'fade block' for d in diffs):
            self.mem.ee[:], self.mem.sp[:] = snap[0], snap[1]
            del self.requests[snap[2]:]
            self.vblanks = snap[3]
            r = C.c_uint32()
            for pc, args in setter:
                self.lib.hg_front_end_call(C.byref(self.env), pc, *args, C.byref(r))
            self.injections.append(dict(seq=self.seq + 1, calls=[[f'{pc:08x}', list(args)] for pc, args in setter]))
            self.lib.hg_main_unit(C.byref(self.env))
            diffs = self.compare(nxt)
        # Loader request checks against the native task record of stop N+1.
        busy0, busy1 = rec_read(cur, 0x3eacec, 1)[0], rec_read(nxt, 0x3eacec, 1)[0]
        new = self.requests[nreq:]
        kind1 = int.from_bytes(rec_read(nxt, 0x3f8e32, 2), 'little')
        if new:
            q = new[-1]
            if kind1 != (q['kind'] & 0xffff):
                diffs.append(dict(domain='loader request kind', native=kind1, c=q['kind']))
            id1 = int.from_bytes(rec_read(nxt, 0x3f8e44, 4), 'little')
            idc = int.from_bytes(self.mem.read(0x3f8e44, 4), 'little')
            if id1 != idc:
                diffs.append(dict(domain='loader request resource id', native=id1, c=idc))
        elif busy0 == 0 and busy1 == 1:
            diffs.append(dict(domain='loader request missing', native='busy 0->1', c='no request'))
        self.seq += 1
        return diffs


def mode_of(rec):
    m = rec_read(rec, 0x3ead30, 0x14)
    return (struct.unpack_from('<I', m, 4)[0], m[8], m[9], m[10], m[11])


# Negative controls: (name, source, anchor, replacement, start capture). Each
# mutated copy must be rejected (a failing unit) by the connected run.
CONTROLS = (
    ('null-control', 'front_end.c', None, None, 's3-quit-selected'),     # unmodified copy: must pass
    ('title-new-game-routes-to-area-select', 'game_lifecycle.c',
     'if(choice==0) {w32(e,FLAG_CC,0);w8(e,m+8,2);}', 'if(choice==0) {w32(e,FLAG_CC,0);w8(e,m+8,4);}',
     'title-menu-input-ready'),
    ('title-cursor-no-continue-skip', 'front_end.c',
     'if(!r32(e,SAVE_FLAG) && r8(e,blk(e)+0x11)==1)w8(e,blk(e)+0x11,2);', '',
     'title-menu-input-ready'),
    ('fade-in-rate-off-by-one', 'front_end.c',
     'w16(e,FADE+4,s16(r16(e,FADE+4))-s16(r16(e,FADE+6)));',
     'w16(e,FADE+4,s16(r16(e,FADE+4))-s16(r16(e,FADE+6))-1);', 'title-menu-input-ready'),
    ('pause-quit-fade-step-6', 'front_end.c',
     'call(e,0x139040,8,0,0,0);\n                sound(e,0x52);', 'call(e,0x139040,6,0,0,0);\n                sound(e,0x52);',
     's3-before-pause'),
    ('pause-quit-wrong-next-state', 'front_end.c',
     'w8(e,blk(e)+0xc,2);w8(e,blk(e)+0x14,4);w16(e,blk(e)+0x22,1);',
     'w8(e,blk(e)+0xc,2);w8(e,blk(e)+0x14,5);w16(e,blk(e)+0x22,1);', 's3-before-pause'),
    ('pause-resume-wrong-next-state', 'front_end.c',
     'pause_fade(e,0x11);\n                sound(e,0x52);', 'pause_fade(e,0x12);\n                sound(e,0x52);',
     's2-before-pause'),
    ('teardown-session-flag-dropped', 'game_lifecycle.c',
     '    call(e,0x156e60,0,0,0,0);\n    w32(e,0x700000f8,1);', '    call(e,0x156e60,0,0,0,0);', 's3-before-pause'),
    ('title-return-fade-in-step-7', 'game_lifecycle.c',
     'call(e,0x26ff90,4,0,0,0);\n                call(e,0x1390b0,6,0,0,0);',
     'call(e,0x26ff90,4,0,0,0);\n                call(e,0x1390b0,7,0,0,0);', 's3-quit-selected'),
    ('title-return-resource-kind', 'game_lifecycle.c',
     'call(e,0x130150,1,0x50,0,0);', 'call(e,0x130150,1,0x51,0,0);', 's3-quit-selected'),
    ('new-game-carried-area-corrupt', 'game_lifecycle.c',
     'w8(e,0x3fa1b1,9);', 'w8(e,0x3fa1b1,8);', 's3-quit-selected'),
)


def negative_controls(native):
    import subprocess
    folder = build_path(ROOT / 'local/m18/controls')
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, src, old, new, start in CONTROLS:
        text = (ROOT / 'src' / src).read_text()
        if old is not None and text.count(old) != 1:
            raise SystemExit(f'{name}: anchor not unique')
        path = folder / f'{name}.c'
        path.write_text(text if old is None else text.replace(old, new))
        opt = '--front-end-source' if src == 'front_end.c' else '--lifecycle-source'
        r = subprocess.run([sys.executable, '-B', __file__, '--native', str(native), '--start', start,
                            opt, str(path), '--library', str(folder / f'{name}.so'), '--stop-on-diff', '1'],
                           capture_output=True, text=True)
        first = next((ln.strip() for ln in r.stdout.splitlines() if ln.startswith('  FAIL')), None)
        if r.returncode not in (0, 1):
            raise SystemExit(f'{name}: runner error\n{r.stderr[-2000:]}')
        rows.append(dict(control=name, source=src, start=start, defect=old is not None,
                         rejected=r.returncode == 1,
                         returncode=r.returncode, source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                         first_failure=first and first[:300]))
        print(f"  control {name}: {'rejected' if r.returncode == 1 else 'passed'} {first and first[:160]}")
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--native', default='local/m18/native')
    p.add_argument('--start', default='title-menu-input-ready')
    p.add_argument('--frames', type=int, default=0)
    p.add_argument('--stop-on-diff', type=int, default=20)
    p.add_argument('--library')
    p.add_argument('--output', type=Path)
    p.add_argument('--front-end-source', type=Path, help='alternate src/front_end.c (negative controls)')
    p.add_argument('--lifecycle-source', type=Path, help='alternate src/game_lifecycle.c (negative controls)')
    p.add_argument('--negative-controls', action='store_true')
    a = p.parse_args()
    native = Path(a.native)
    if a.negative_controls:
        rows = negative_controls(native)
        out = dict(schema='hg-m18-native-lifecycle-controls/v1', controls=rows,
                   passed=all(r['rejected'] == r['defect'] for r in rows))
        if a.output:
            a.output.parent.mkdir(parents=True, exist_ok=True)
            with a.output.open('x') as fp:
                json.dump(out, fp, indent=1)
                fp.write('\n')
        return 0 if out['passed'] else 1
    rec = json.loads((native / 'full' / f'{a.start}.json').read_text())
    ee = zlib.decompress((native / 'full' / f'{a.start}.ee.z').read_bytes())
    sp = zlib.decompress((native / 'full' / f'{a.start}.sp.z').read_bytes())
    assert hashlib.sha256(ee).hexdigest() == rec['ee_sha256'] and hashlib.sha256(sp).hexdigest() == rec['sp_sha256']
    lib = F.build(a.library, front_end=a.front_end_source, lifecycle=a.lifecycle_source)
    frames = load_frames(native)
    start = rec['seq']
    assert start in frames and rec_read(frames[start], 0x3eac00, 0x500) == ee[0x3eac00:0x3ead00 + 0x400], 'start mismatch'
    last = max(frames)
    end = min(last, start + a.frames) if a.frames else last
    run = Runner(lib, Mem(ee, sp), frames, start)
    transitions, failures, checked, prev = [], [], 0, mode_of(frames[start])
    while run.seq < end and run.seq + 1 in frames:
        seq = run.seq
        diffs = run.unit()
        checked += 1
        m = mode_of(frames[seq + 1])
        if m[:3] != prev[:3]:
            transitions.append(dict(seq=seq + 1, mode=f'{m[0]:08x} ({m[1]},{m[2]},{m[3]},{m[4]})'))
        prev = m
        if diffs:
            failures.append(dict(seq=seq + 1, mode=f'{m[0]:08x} ({m[1]},{m[2]},{m[3]},{m[4]})', diffs=diffs))
            if len(failures) >= a.stop_on_diff:
                break
    out = dict(schema='hg-m18-native-lifecycle/v1', start=a.start, start_seq=start, end_seq=run.seq,
               units=checked, failing_units=len(failures), failures=failures[:a.stop_on_diff],
               transitions=transitions, external_fades=run.injections, requests=run.requests, faults=run.faults[:20],
               externals={f'{k:08x}': v for k, v in sorted(run.calls.items())},
               interpreted={f'{k:08x}': v for k, v in sorted(run.interp_counts.items())},
               pcs=[f'{x:08x}' for x in sorted(run.interp_pcs)])
    print(f"units {checked} (seq {start}..{run.seq}) failing {len(failures)} faults {len(run.faults)}")
    for t in transitions[:80]:
        print('  transition', t['seq'], t['mode'])
    for i in run.injections:
        print('  E10 external fade', i)
    for f_ in failures[:6]:
        print('  FAIL', f_['seq'], f_['mode'], json.dumps(f_['diffs'])[:400])
    for f_ in run.faults[:5]:
        print('  fault', f_)
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        with a.output.open('x') as fp:
            json.dump(out, fp, indent=1)
            fp.write('\n')
    return 1 if failures or run.faults else 0


if __name__ == '__main__':
    raise SystemExit(main())
