#!/usr/bin/env python3
"""M17 connected lifecycle sequence runner (state-machine level, synthetic).

Starts from the authenticated ELF image (no captured RAM), runs the C main
init (0011d830) and then main-loop frames (hg_main_frame) through the
reconstructed lifecycle layer: boot mode 00121810 -> lifecycle mode 00121be0
(title, new game) -> gameplay mode 0012d1c0 -> area load 0012dd60 -> gameplay
entry -> active play 0012e700 -> pause -> quit -> teardown 0012f800 ->
lifecycle mode -> continue -> area reload -> active play.

Every lifecycle callee is routed to C. All other callees are host models,
declared below and recorded; they are NOT reconstructions:
  loader       00130150/00130170 requests are recorded; the loader busy flag
               [gp+2d7c] stays 0 (instant completion). Real loading is the
               M2-M7 C worker, verified separately.
  fades        00139040 (fade out) writes FADE_STATE [gp+2f40] = 3 and
               001390b0 (fade in) writes 1, as the original setters do; the
               model completes them at the next frame (3 -> 2 black, 1 -> 0
               clear). 00139010/00138fd0 set 2/0 immediately, as in the ELF.
  menus/movies 002cdef0, 001620d0, 00a87880, 002cf020, 002cf5c0, 00162750,
               00122bd0, 00336e90, 002cf330 return scripted choices.
  coordinator  0012f850 is recorded only (gameplay frames themselves are the
               M13-M16 corridor, verified separately against original runs).
  input        the Start button is pressed through [gp+2f94] on one frame.
The recorded transcript (mode states, resource requests, mode handlers) is
the output; it is a synthetic integration check, not retail equivalence.

    python3 -B tools/run_lifecycle_sequence.py [--elf "$HG_ELF"] [--output transcript.json]
"""
import argparse
import ctypes as C
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lifecycle_differential import (CALL, Env, LOAD, STORE, Bus, HG_CALL_FP_REGS, HG_CALL_T_REGS,
                                    HG_DOMAIN_FAULT, HG_WAIT_INTERRUPT, build, elf_image, packet_image)

GP = 0x3e7f70
M = 0x3ead30
LOADER_BUSY, FADE, PAD, PADMASK = GP + 0x2d7c, GP + 0x2f40, GP + 0x2f94, GP + 0x2f54
ROUTE = {  # lifecycle entries -> (C symbol, argument count, returns)
    0x143300: ('hg_mode_set', 1, False), 0x1432c0: ('hg_mode_init', 1, False),
    0x143240: ('hg_mode_dispatch', 0, False), 0x12d1c0: ('hg_gameplay_mode', 0, False),
    0x12d9a0: ('hg_gameplay_major5', 0, False), 0x12dc90: ('hg_gameplay_entry_step', 0, True),
    0x12dd60: ('hg_area_load_step', 0, True), 0x12dbd0: ('hg_area_pack_id', 1, True),
    0x12d7d0: ('hg_gameplay_enter_step', 0, True), 0x12d850: ('hg_gameplay_reset_services', 0, False),
    0x12d680: ('hg_gameplay_title_step', 0, True), 0x12e020: ('hg_gameplay_reload_step', 2, True),
    0x12e700: ('hg_gameplay_frame', 0, False), 0x12e6d0: ('hg_gameplay_event_frame', 0, False),
    0x12fa00: ('hg_gameplay_event_flags', 0, True), 0x12f7d0: ('hg_gameplay_request_exit', 0, False),
    0x12f800: ('hg_gameplay_teardown', 0, False), 0x11df10: ('hg_vblank_start_handler', 0, True),
    0x11df80: ('hg_vblank_end_handler', 0, True), 0x121810: ('hg_boot_mode', 0, False),
    0x121be0: ('hg_lifecycle_mode', 0, False),
}
MENU = {0x2cdef0: 1, 0x1620d0: 1, 0x2cf020: 1, 0x2cf5c0: 1, 0x122bd0: 1, 0x336e90: 1, 0x2cf330: 1}


class Runner:
    def __init__(self, lib, code):
        ram = bytearray(0x2000000)
        ram[0x100000:0x100000 + len(code)] = code
        self.bus = Bus(bytes(ram), bytes(0x4000))
        self.lib = lib
        self.pending_t0 = 0
        self.fade_pending = False
        self.requests, self.transcript, self.faults, self.models = [], [], [], {}
        self.frame = 0
        self.press_start = False
        self.menu_queue = {0x162750: [3], 0xa87880: [1, 1]}
        self.title_choice = [0, 1]          # blk+0x11 before 00a87880: new game, then continue
        self.env = Env(None, LOAD(self._load), STORE(self._store), CALL(self._call), None, None)
        for name, n, returns in ROUTE.values():
            fn = getattr(lib, name)
            fn.argtypes = [C.POINTER(Env)] + [C.c_uint32] * n
            fn.restype = C.c_uint32 if returns else None
        lib.hg_logo_sequence_step.argtypes = [C.POINTER(Env)] + [C.c_uint32] * 5
        lib.hg_logo_sequence_step.restype = C.c_uint32
        for name in ('hg_main_init', 'hg_main_frame'):
            getattr(lib, name).argtypes = [C.POINTER(Env)]
            getattr(lib, name).restype = None

    def _load(self, _, address, width):
        try:
            return self.bus.load(address, width)
        except Exception as exc:
            self.faults.append(repr(exc))
            return 0

    def _store(self, _, address, width, value):
        try:
            self.bus.store(address, value, width)
        except Exception as exc:
            self.faults.append(repr(exc))

    def model(self, pc):
        self.models[f'{pc:08x}'] = self.models.get(f'{pc:08x}', 0) + 1

    def _call(self, _, pc, a, b, c, d):
        try:
            return self.route(pc, a, b, c, d)
        except Exception as exc:
            self.faults.append(f'{pc:#x}: {exc!r}')
            return 0

    def route(self, pc, a, b, c, d):
        if pc == HG_CALL_T_REGS:
            self.pending_t0 = a
            return 0
        if pc == HG_CALL_FP_REGS:
            return 0
        if pc == HG_DOMAIN_FAULT:
            self.faults.append(f'domain fault {a:#x}')
            return 0
        if pc == HG_WAIT_INTERRUPT:
            self.lib.hg_vblank_start_handler(C.byref(self.env))
            self.lib.hg_vblank_end_handler(C.byref(self.env))
            return 0
        if pc in ROUTE:
            name, n, returns = ROUTE[pc]
            result = getattr(self.lib, name)(C.byref(self.env), *(a, b, c, d)[:n])
            return result if returns else 0
        if pc == 0x1219e0:
            t0, self.pending_t0 = self.pending_t0, 0
            return self.lib.hg_logo_sequence_step(C.byref(self.env), a, b, c, d, t0)
        if pc in (0x130150, 0x130170):
            self.requests.append(dict(frame=self.frame, entry=f'{pc:08x}', kind=a, directory=f'{b:#x}',
                                      mode=self.state()))
            self.model(pc)
            return 0
        if pc in (0x1390b0, 0x139040, 0x139010, 0x138fd0):
            state = {0x139040: 3, 0x1390b0: 1, 0x139010: 2, 0x138fd0: 0}[pc]
            self.bus.store(FADE, state, 1)
            self.fade_pending = state in (1, 3)
            self.model(pc)
            return 0
        if pc == 0xa87880:
            self.bus.store(M + 0x11, self.title_choice.pop(0) if self.title_choice else 1, 1)
        if pc in self.menu_queue and self.menu_queue[pc]:
            self.model(pc)
            return self.menu_queue[pc].pop(0)
        if pc in MENU:
            self.model(pc)
            return MENU[pc]
        if pc == 0x11dfd0:
            n = self.models.get('0011dfd0', 0)
            self.model(pc)
            return 0 if n == 0 else 1
        self.model(pc)
        return 0

    def state(self):
        raw = self.bus.peek(M, 0x14)
        return dict(handler=f'{struct.unpack_from("<I", raw, 4)[0]:08x}', major=raw[8], minor=raw[9],
                    sub=raw[10], step=raw[11], area=raw[1])

    def run(self, frames):
        self.lib.hg_main_init(C.byref(self.env))
        last = None
        for self.frame in range(frames):
            if self.fade_pending:
                self.bus.store(FADE, {3: 2, 1: 0}.get(self.bus.load(FADE, 1), self.bus.load(FADE, 1)), 1)
                self.fade_pending = False
            st = self.state()
            pad = 0
            if st['handler'] == '0012d1c0' and st['major'] == 5 and st['minor'] == 1 and st['sub'] == 1:
                active = sum(1 for t in self.transcript if t['state'] == st)
                if active >= 3 and self.models.get('00162750', 0) == 0 and not self.press_start:
                    pad, self.press_start = 0x800, True
                    self.bus.store(0x46d90c, 1, 4)        # event flags enabled (gameplay running)
                    self.bus.store(0x95e44a, 1, 1)        # pad connected
            self.bus.store(PAD, pad, 2)
            self.lib.hg_main_frame(C.byref(self.env))
            st = self.state()
            key = (st['handler'], st['major'], st['minor'], st['sub'], st['step'])
            if key != last:
                self.transcript.append(dict(frame=self.frame, state=st))
                last = key
            else:
                self.transcript.append(dict(frame=self.frame, state=st, repeat=True))
        return dict(states=[t for t in self.transcript if not t.get('repeat')], requests=self.requests,
                    models=self.models, faults=self.faults, frames=frames,
                    counters=dict(frame_tick=self.bus.load(GP + 0x2f80), main_loop=self.bus.load(GP + 0x2f84),
                                  vblank=self.bus.load(0x95e300)))


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--elf', type=Path, help='authenticated ELF (default: published packet image only)')
    p.add_argument('--frames', type=int, default=120)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    code = elf_image(a.elf) if a.elf else packet_image()
    result = Runner(build(), code).run(a.frames)
    text = json.dumps(result, indent=1)
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(text + '\n')
    for t in result['states']:
        s = t['state']
        print(f"frame {t['frame']:4d} mode {s['handler']} ({s['major']},{s['minor']},{s['sub']},{s['step']}) area {s['area']}")
    for r in result['requests']:
        print('request', r['frame'], r['entry'], r['kind'], r['directory'])
    print('faults', result['faults'][:5], 'counters', result['counters'])
    return 1 if result['faults'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
