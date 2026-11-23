"""M18 original-instruction versus C differential for the front-end services.

Same method as tools/lifecycle_differential.py (M17): the original words of
each function run in the strict EE subset (tools/ee_subset.py) from the
authenticated ELF PT_LOAD, or, for 00a87880, from the authenticated title
overlay /AREA0/A0_B09.BIN placed at its link address 00a87800; the C
(src/front_end.c) runs on an identical memory image. Every callee is
intercepted at its entry on both sides and compared by its declared argument
registers (CONTRACT); both sides receive the same scripted return. The ordered
byte-write/call streams, return values and executed original words are
compared. Only each routine's register-save area is excluded; buffers that
callees receive in the guest frame are compared.

Additions to the M17 harness: the t3 host seam (HG_CALL_T3_REG), f12/f13
contracts for 001564a0, 0013e5b0 and 0013e8f0, and f0 returns for 0013e8f0.
"""
import ctypes as C
import hashlib
import os
import struct
import subprocess
from pathlib import Path

import lifecycle_differential as L
from ee_subset import Machine

ROOT = L.ROOT
SP = L.SP
OVERLAY_SHA256 = 'ef257df984b273e0482c1b6575c1f4a1898e7f4a33861b8127001cc5a769e935'
OVERLAY_BASE = 0xa87800
HG_CALL_T3_REG = 0xfffffffc
A, A2, A3, A4 = L.A, L.A2, L.A3, L.A4
FP2 = ('f12', 'f13')
CONTRACT = dict(L.CONTRACT)
CONTRACT.update({
    0x138fd0: A2, 0x1390f0: A, 0x139190: A, 0x1391f0: A,
    0x14a0a0: A4, 0x14f370: A4 + ('t0', 't1', 't2', 't3'),
    0x10dcf8: A2, 0x10e0d8: A4, 0x10e210: A4, 0x10de10: A2, 0x10de98: A4, 0x10e388: A3,
    0x10e5c8: A2, 0x10e628: A2, 0x10db90: A3, 0x13dc30: A, 0x13e090: A2, 0x13e550: A,
    0x13e5b0: A2 + FP2, 0x13e8f0: A + FP2, 0x13e500: A2, 0x13d910: A, 0x13dad0: A,
    0x1783c0: A, 0x1782a0: A4 + ('t0',), 0x178270: A4 + ('t0', 't1'), 0x11af00: A4 + ('t0',),
    0x1413a0: (), 0x141470: (), 0x1564a0: A4 + FP2, 0xa88660: A, 0xa87de0: A, 0xa87ed0: A,
    0x163350: A, 0x1635b0: A, 0x161cf0: A2, 0x166770: (), 0x147f10: (), 0x166620: A,
})
GPR = dict(L.GPR, t3=11)
FPR = L.FPR
F0_RETURN = {0x13e8f0}

# entry: (end_exclusive, save-area bytes excluded at the frame base, frame bytes, symbol, nargs, returns)
FUNCTIONS = {
    0x138fa0: (0x138fc4, 0, 0, 'hg_fade_reset', 0, False),
    0x138fd0: (0x139008, 0, 0, 'hg_fade_clear', 2, False),
    0x139010: (0x139034, 0, 0, 'hg_fade_black', 2, False),
    0x139040: (0x1390a8, 0, 0, 'hg_fade_out', 4, False),
    0x1390b0: (0x1390f0, 0, 0, 'hg_fade_in', 4, False),
    0x1390f0: (0x139140, 0, 0, 'hg_music_fade_2', 1, False),
    0x139140: (0x139188, 0, 0, 'hg_music_fade_1', 1, False),
    0x139190: (0x1391e8, 0, 0, 'hg_music_fade_6', 1, False),
    0x1391f0: (0x139238, 0, 0, 'hg_music_fade_5', 1, False),
    0x139240: (0x13968c, 0x30, 0x90, 'hg_fade_service', 0, False),
    0x13d970: (0x13da7c, 0x10, 0x10, 'hg_pad_service', 0, False),
    0x13dc30: (0x13e088, 0x40, 0x40, 'hg_pad_port', 1, False),
    0x13e090: (0x13e4f8, 0x60, 0x80, 'hg_pad_read', 2, True),
    0x13e500: (0x13e550, 0, 0, 'hg_pad_stick_dpad', 2, True),
    0x13e550: (0x13e5ac, 0, 0, 'hg_pad_digital_pressure', 1, False),
    0x13e8f0: (0x13e980, 0, 0, 'hg_pad_pressure', 3, True),
    0x13d910: (0x13d970, 0, 0, 'hg_pad_clear_buttons', 1, False),
    0x13dad0: (0x13dae4, 0, 0, 'hg_pad_clear_port', 1, False),
    0x147f10: (0x147f58, 0x10, 0x10, 'hg_menu_clear', 0, False),
    0x2cf020: (0x2cf324, 0x20, 0x120, 'hg_title_card_scan', 0, True),
    0xa87880: (0xa87de0, 0x20, 0x20, 'hg_title_menu', 0, True),
    0x162750: (0x16334c, 0x30, 0x30, 'hg_pause_menu', 0, True),
    0x161cf0: (0x161dc0, 0x20, 0x20, 'hg_menu_cursor', 2, False),
}


def code_image(elf, overlay):
    """ELF resident image from 0x100000 with the overlay at its link address."""
    code = bytearray(L.elf_image(elf))
    data = Path(overlay).read_bytes()
    if hashlib.sha256(data).hexdigest() != OVERLAY_SHA256:
        raise ValueError('overlay is not the authenticated /AREA0/A0_B09.BIN')
    off = OVERLAY_BASE - 0x100000
    code.extend(bytes(off + len(data) - len(code)))
    code[off:off + len(data)] = data
    return bytes(code)


def build(output=None, sanitize=False):
    sanitize = sanitize or os.environ.get('HG_SANITIZE') == '1'
    output = Path(output or ROOT / ('local/build/m18-front-end-ubsan.so' if sanitize
                                    else 'local/build/m18-front-end.so'))
    output.parent.mkdir(parents=True, exist_ok=True)
    args = ['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-pedantic', '-fPIC', '-shared',
            '-I' + str(ROOT / 'src'), str(ROOT / 'src/front_end.c'), str(ROOT / 'src/game_lifecycle.c'),
            str(ROOT / 'src/resource_loader.c'), str(ROOT / 'src/disc_read.c'), '-o', str(output)]
    if sanitize:
        args[1:1] = ['-fsanitize=undefined', '-fno-sanitize-recover=all']
    subprocess.run(args, check=True)
    lib = C.CDLL(str(output))
    for entry, (_, _, _, name, n, returns) in FUNCTIONS.items():
        fn = getattr(lib, name)
        fn.argtypes = [C.POINTER(L.Env)] + [C.c_uint32] * n
        fn.restype = C.c_uint32 if returns else None
    return lib


def run_original(code, entry, bus, script, args=(), fpr=None):
    end, _, frame, _, _, _ = FUNCTIONS[entry]
    machine = Machine()
    machine.mem = bus
    machine.reg[28] = L.GP
    machine.reg[29] = SP
    for i, value in enumerate(args):
        machine.reg[4 + i] = value
    for k, v in (fpr or {}).items():
        machine.fpr[k] = v

    def hook(pc):
        def fire(cpu):
            row = {}
            for name in CONTRACT.get(pc, ()):
                row[name] = (cpu.fpr[FPR[name]] if name in FPR else cpu.reg[GPR[name]]) & 0xffffffff
            bus.events.append(('c', pc, tuple(sorted(row.items()))))
            value = script(pc, bus)
            if pc in F0_RETURN:
                cpu.fpr[0] = value
            # A callee's 32-bit result is sign-extended in v0, as lw/addiu leave it.
            return value - (1 << 32) if value & 0x80000000 else value
        return fire

    callees = set()
    for pc in range(entry, end, 4):
        word = struct.unpack_from('<I', code, pc - 0x100000)[0]
        if word >> 26 == 3:
            callees.add(((pc + 4) & 0xf0000000) | ((word & 0x3ffffff) << 2))
    callees |= set(script.returns) | set(script.effects)
    callees.discard(entry)
    machine.hooks = {pc: hook(pc) for pc in callees}
    for pc in range(entry, end, 4):
        bus.put(pc, code[pc - 0x100000:pc - 0x100000 + 4], log=False)
    machine.run(entry, limit=200000)
    return machine


def run_c(lib, name, bus, script, args=()):
    errors, pending, frames = [], {}, []

    def load(_, address, width):
        try:
            return bus.load(address, width)
        except Exception as exc:
            errors.append(repr(exc))
            return 0

    def store(_, address, width, value):
        try:
            bus.store(address, value, width)
        except Exception as exc:
            errors.append(repr(exc))

    def call(_, pc, a, b, c, d):
        try:
            if pc == L.HG_CALL_T_REGS:
                pending.update(t0=a, t1=b, t2=c)
                return 0
            if pc == HG_CALL_T3_REG:
                pending.update(t3=a)
                return 0
            if pc == L.HG_CALL_FP_REGS:
                pending.update(f12=a, f13=b, f14=c)
                return 0
            if pc == L.HG_GUEST_FRAME:
                return (SP - a) & 0xffffffff if b else 0
            if pc == L.HG_DOMAIN_FAULT:
                errors.append(f'domain fault {a:#x}')
                return 0
            contract = CONTRACT.get(pc, ())
            slots = dict(zip(('f12',) + A3 if pc in L.FP_IN_SLOT else A4, (a, b, c, d)))
            undeclared = [k for k, v in slots.items() if v and k not in contract]
            if undeclared:
                errors.append(f'C passes undeclared {undeclared} to {pc:#x}')
            row = {}
            for nm in contract:
                if nm in ('t0', 't1', 't2', 't3') or (nm in FPR and pc not in L.FP_IN_SLOT):
                    if nm not in pending:
                        errors.append(f'missing {nm} for {pc:#x}')
                    row[nm] = pending.get(nm, 0) & 0xffffffff
                else:
                    row[nm] = slots.get(nm, 0) & 0xffffffff
            pending.clear()
            bus.events.append(('c', pc, tuple(sorted(row.items()))))
            return script(pc, bus)
        except Exception as exc:
            errors.append(repr(exc))
            return 0

    env = L.Env(None, L.LOAD(load), L.STORE(store), L.CALL(call), None, None)
    result = getattr(lib, name)(C.byref(env), *args)
    return result, errors


def case(code, lib, entry, ram, scratch, cells, script_factory, args=(), fpr=None):
    """One input on both sides: (equal, detail, executed original pcs)."""
    end, save, frame, name, _, returns = FUNCTIONS[entry]
    base = SP - frame
    images = []
    for _ in range(2):
        bus = L.Bus(ram, scratch, frame=(base, base + save) if save else (base, base))
        for address, width, value in cells:
            bus.put(address, (value & ((1 << (8 * width)) - 1)).to_bytes(width, 'little'), log=False)
        images.append(bus)
    original, cbus = images
    try:
        machine = run_original(code, entry, original, script_factory(), args=args, fpr=fpr)
    except Exception as exc:
        return False, f'original failed: {exc!r}', set()
    cargs = tuple(args) + tuple((fpr or {}).get(k, 0) for k in (12, 13) if entry in F0_RETURN)
    result, errors = run_c(lib, name, cbus, script_factory(), args=cargs)
    if errors:
        return False, f'C errors {errors[:3]}', machine.executed
    if original.events != cbus.events:
        n = next((i for i, (x, y) in enumerate(zip(original.events, cbus.events)) if x != y),
                 min(len(original.events), len(cbus.events)))
        return False, dict(first_difference=n, original=original.events[max(0, n - 2):n + 3],
                           c=cbus.events[max(0, n - 2):n + 3],
                           lengths=(len(original.events), len(cbus.events))), machine.executed
    if returns:
        want = machine.fpr[0] if entry in F0_RETURN else machine.reg[2]
        if (want & 0xffffffff) != result:
            return False, f'return {want & 0xffffffff:#x} != {result:#x}', machine.executed
    return True, len(original.events), machine.executed
