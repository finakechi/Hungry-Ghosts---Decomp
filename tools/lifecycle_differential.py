"""M17 original-instruction versus C differential for the game lifecycle layer.

The original words of each lifecycle function run in the strict EE subset
(tools/ee_subset.py), read directly from the authenticated file-backed ELF
PT_LOAD. The C reconstruction (src/game_lifecycle.c) runs on an identical
memory image. Every callee outside the function under test is intercepted at
its entry on both sides: the interpreter hook reads the declared argument
registers (CONTRACT) and returns a scripted v0; the C bus call reports its
arguments and receives the same scripted value. Both sides produce one
ordered event stream of byte writes and calls; streams, return values and
the executed original instruction set are compared. The original routine's
own stack frame is excluded (host C does not materialize it, except through
HG_GUEST_FRAME where a callee receives a frame address).

No captured expected output is used: inputs are complete memory images
(owner-local captures or synthetic zero RAM) plus enumerated control-cell
variants; the original instructions are the reference.
"""
import ctypes as C
import hashlib
import itertools
import random
import struct
import subprocess
from pathlib import Path

from ee_subset import Machine

ROOT = Path(__file__).resolve().parents[1]
ELF_SHA256 = 'a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61'
GP = 0x3e7f70
SP = 0x1fffc00
MODE_BLOCK = 0x3ead30
RAM = 0x2000000
SCRATCH = 0x70000000
HARDWARE = (0x10000000, 0x10010000)      # EE timer registers stored by main
HG_DOMAIN_FAULT, HG_GUEST_FRAME, HG_CALL_T_REGS = 0xfffffff0, 0xfffffff1, 0xfffffff5
HG_WAIT_INTERRUPT = 0xfffffffa

A = ('a0',)
A2 = ('a0', 'a1')
A3 = ('a0', 'a1', 'a2')
A4 = ('a0', 'a1', 'a2', 'a3')
CONTRACT = {
    0x130150: A2, 0x130170: A2, 0x1390b0: A4, 0x139040: A4, 0x139010: A2,
    0x139140: A, 0x156ad0: A4 + ('t0',), 0x156d50: ('f12',) + A2, 0x12e020: A2,
    0x119ac8: A3, 0x119910: A3, 0x11b378: A2, 0x11e350: A3, 0x11fe20: A2,
    0x141330: A4, 0x166910: A2, 0x26fe40: A2, 0x196ad0: A2, 0x26ff90: A,
    0x282160: A, 0x2821f0: A, 0x270c30: A2, 0x12c3d0: A, 0x13a4c0: A,
    0x1404f0: A2, 0x156550: A, 0x1385b0: A2, 0x2cfa90: A, 0x12dbd0: A,
    0x143300: A, 0x149890: A2, 0x11da00: A3, 0x103c40: A3, 0x104aa8: A,
    0x1432c0: A, 0x104e10: A, 0x123370: A3 + ('f12', 'f13', 'f14'),
    0x1219e0: A4 + ('t0',), 0x336e90: A2,
}
FP_IN_SLOT = {0x156d50}            # f12 travels in the first bus slot
HG_CALL_FP_REGS = 0xfffffffb
GPR = {'a0': 4, 'a1': 5, 'a2': 6, 'a3': 7, 't0': 8, 't1': 9, 't2': 10}
FPR = {'f12': 12, 'f13': 13, 'f14': 14}

# (entry, end_exclusive, frame bytes, C symbol, argument count, returns)
FUNCTIONS = {
    0x143300: (0x143358, 0, 'hg_mode_set', 1, False),
    0x1432c0: (0x143300, 0, 'hg_mode_init', 1, False),
    0x143240: (0x1432b4, 0x10, 'hg_mode_dispatch', 0, False),
    0x12d1c0: (0x12d674, 0x10, 'hg_gameplay_mode', 0, False),
    0x12d680: (0x12d7cc, 0x10, 'hg_gameplay_title_step', 0, True),
    0x12d7d0: (0x12d84c, 0x10, 'hg_gameplay_enter_step', 0, True),
    0x12d850: (0x12d884, 0x10, 'hg_gameplay_reset_services', 0, False),
    0x12d9a0: (0x12dbcc, 0x10, 'hg_gameplay_major5', 0, False),
    0x12dbd0: (0x12dc5c + 4, 0, 'hg_area_pack_id', 1, True),
    0x12dc90: (0x12dd54, 0, 'hg_gameplay_entry_step', 0, True),
    0x12dd60: (0x12e01c, 0x10, 'hg_area_load_step', 0, True),
    0x12e020: (0x12e140, 0x20, 'hg_gameplay_reload_step', 2, True),
    0x12e6d0: (0x12e700, 0x10, 'hg_gameplay_event_frame', 0, False),
    0x12e700: (0x12f264, 0xa0, 'hg_gameplay_frame', 0, False),
    0x12fa00: (0x12fb30, 0, 'hg_gameplay_event_flags', 0, True),
    0x12f7d0: (0x12f7fc, 0, 'hg_gameplay_request_exit', 0, False),
    0x12f800: (0x12f84c, 0x10, 'hg_gameplay_teardown', 0, False),
    0x11d830: (0x11d938, 0x10, 'hg_main_init', 0, False),
    0x11df10: (0x11df80, 0x10, 'hg_vblank_start_handler', 0, True),
    0x11df80: (0x11dfc8, 0x10, 'hg_vblank_end_handler', 0, True),
    0x121810: (0x1219d4, 0x10, 'hg_boot_mode', 0, False),
    0x1219e0: (0x121bd4, 0x50, 'hg_logo_sequence_step', 5, True),
    0x121be0: (0x12266c, 0x10, 'hg_lifecycle_mode', 0, False),
}
MAIN_FRAME = (0x11d938, 0x11da00)        # loop body; entered at its head
STOPS = {0x11d830: 0x11d938}              # main's init falls into its loop head


def elf_image(path):
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != ELF_SHA256:
        raise ValueError('ELF is not the authenticated Japanese SCPS_150.46')
    ph, phes, phn = struct.unpack_from('<I', data, 0x1c)[0], *struct.unpack_from('<HH', data, 0x2a)
    for i in range(phn):
        kind, off, va, _, filesz, _ = struct.unpack_from('<IIIIII', data, ph + i * phes)
        if kind == 1 and va == 0x100000:
            return data[off:off + filesz]
    raise ValueError('resident PT_LOAD not found')


class Bus:
    """Copy-on-write view of a 32 MiB RAM + scratchpad image, recorded
    hardware stores, and the ordered byte-write/call event log."""
    def __init__(self, ram, scratch, frame=None):
        self.ram = ram
        self.base_scratch = scratch
        self.overlay = {}
        self.events = []
        self.frame = frame or (0, 0)

    def _excluded(self, address):
        return self.frame[0] <= address < self.frame[1]

    def _byte(self, address):
        v = self.overlay.get(address)
        if v is not None:
            return v
        if 0 <= address < RAM:
            return self.ram[address]
        if SCRATCH <= address < SCRATCH + 0x4000:
            return self.base_scratch[address - SCRATCH]
        if HARDWARE[0] <= address < HARDWARE[1]:
            return 0
        raise ValueError(f'unmapped read {address:#x}')

    def peek(self, address, size):
        if not (0 <= address <= RAM - size or SCRATCH <= address <= SCRATCH + 0x4000 - size
                or HARDWARE[0] <= address <= HARDWARE[1] - size):
            raise ValueError(f'unmapped read {address:#x}+{size}')
        return bytes(self._byte(address + i) for i in range(size))

    def get(self, address, size):
        return self.peek(address, size)

    def put(self, address, data, log=True):
        if not (0 <= address <= RAM - len(data) or SCRATCH <= address <= SCRATCH + 0x4000 - len(data)
                or HARDWARE[0] <= address <= HARDWARE[1] - len(data)):
            raise ValueError(f'unmapped store {address:#x}+{len(data)}')
        for i, x in enumerate(data):
            self.overlay[address + i] = x
            if log and not self._excluded(address + i):
                self.events.append(('w', address + i, x))

    # Machine interface (byte granularity)
    def __getitem__(self, address):
        return self.get(address, 1)[0]

    def __setitem__(self, address, value):
        self.put(address, bytes([value]))

    def update(self, pairs):
        for address, value in pairs:
            self[address] = value

    def load(self, address, width=4):
        return int.from_bytes(self.get(address, width), 'little')

    def store(self, address, value, width=4):
        self.put(address, (value & ((1 << (width * 8)) - 1)).to_bytes(width, 'little'))


class Script:
    """Scripted callee returns and optional identical side effects."""
    def __init__(self, returns=None, effects=None):
        self.returns = returns or {}
        self.effects = effects or {}
        self.counts = {}

    def __call__(self, pc, bus):
        n = self.counts.get(pc, 0)
        self.counts[pc] = n + 1
        if pc in self.effects:
            self.effects[pc](bus, n)
        value = self.returns.get(pc, 0)
        if isinstance(value, (list, tuple)):
            value = value[n] if n < len(value) else value[-1]
        return value & 0xffffffff


def run_original(code, entry, end, bus, script, args=(), sp=SP, start=None, stop=None):
    machine = Machine()
    machine.mem = bus
    machine.reg[28] = GP
    machine.reg[29] = sp
    machine.cop0_status = 0      # only the handlers' trailing `ei` touches it
    for i, value in enumerate(args):
        machine.reg[4 + i] = value
    body = range(entry, end)

    def hook(pc):
        def fire(cpu):
            contract = CONTRACT.get(pc, ())
            row = {}
            for name in contract:
                row[name] = (cpu.fpr[FPR[name]] if name in FPR else cpu.reg[GPR[name]]) & 0xffffffff
            bus.events.append(('c', pc, tuple(sorted(row.items()))))
            return script(pc, bus)
        return fire

    callees = set()
    for pc in range(entry, end, 4):
        word = struct.unpack_from('<I', code, pc - 0x100000)[0]
        if word >> 26 == 3:
            callees.add(((pc + 4) & 0xf0000000) | ((word & 0x3ffffff) << 2))
    callees |= set(script.returns) | set(script.effects) | {0x12d1c0, 0x121be0, 0x121810}
    callees.discard(entry)
    machine.hooks = {pc: hook(pc) for pc in callees}
    # Code is served from the authenticated ELF bytes, never from captured RAM.
    for pc in range(entry, end, 4):
        bus.put(pc, code[pc - 0x100000:pc - 0x100000 + 4], log=False)
    if stop is not None:
        machine.run(start, stop, limit=200000)
    else:
        machine.run(entry, limit=200000)
    return machine


LOAD = C.CFUNCTYPE(C.c_uint32, C.c_void_p, C.c_uint32, C.c_uint32)
STORE = C.CFUNCTYPE(None, C.c_void_p, C.c_uint32, C.c_uint32, C.c_uint32)
CALL = C.CFUNCTYPE(C.c_uint32, C.c_void_p, *([C.c_uint32] * 5))


class Env(C.Structure):
    _fields_ = [('user', C.c_void_p), ('load', LOAD), ('store', STORE), ('call', CALL),
                ('read_state', C.c_void_p), ('read_env', C.c_void_p)]


def build(output=None, source=None, sanitize=False):
    output = Path(output or ROOT / 'local/build/m17-lifecycle.so')
    output.parent.mkdir(parents=True, exist_ok=True)
    args = ['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-pedantic', '-fPIC', '-shared',
            '-I' + str(ROOT / 'src'), str(source or ROOT / 'src/game_lifecycle.c'),
            str(ROOT / 'src/resource_loader.c'), str(ROOT / 'src/disc_read.c'), '-o', str(output)]
    if sanitize:
        args[1:1] = ['-fsanitize=undefined', '-fno-sanitize-recover=all']
    subprocess.run(args, check=True)
    lib = C.CDLL(str(output))
    for entry, (_, _, name, n, returns) in FUNCTIONS.items():
        fn = getattr(lib, name)
        fn.argtypes = [C.POINTER(Env)] + [C.c_uint32] * n
        fn.restype = C.c_uint32 if returns else None
    lib.hg_main_frame.argtypes = [C.POINTER(Env)]
    lib.hg_main_frame.restype = None
    return lib


def run_c(lib, name, bus, script, args=(), sp=SP, frame=0):
    errors = []
    pending = {}

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
            if pc == HG_CALL_T_REGS:
                pending.update(t0=a, t1=b, t2=c)
                return 0
            if pc == HG_CALL_FP_REGS:
                pending.update(f12=a, f13=b, f14=c)
                return 0
            if pc == HG_GUEST_FRAME:
                return (sp - a) & 0xffffffff if b else 0
            if pc == HG_DOMAIN_FAULT:
                errors.append(f'domain fault {a:#x}')
                return 0
            if pc == HG_WAIT_INTERRUPT:
                bus.events.append(('c', pc, ()))
                return script(pc, bus)
            contract = CONTRACT.get(pc, ())
            slots = dict(zip(('f12',) + A3 if pc in FP_IN_SLOT else A4, (a, b, c, d)))
            undeclared = [k for k, v in slots.items() if v and k not in contract]
            if undeclared:
                errors.append(f'C passes undeclared {undeclared} to {pc:#x}')
            row = {}
            for name in contract:
                if name in ('t0', 't1', 't2') or (name in FPR and pc not in FP_IN_SLOT):
                    if name not in pending:
                        errors.append(f'missing {name} for {pc:#x}')
                    row[name] = pending.get(name, 0) & 0xffffffff
                else:
                    row[name] = slots.get(name, 0) & 0xffffffff
            pending.clear()
            bus.events.append(('c', pc, tuple(sorted(row.items()))))
            return script(pc, bus)
        except Exception as exc:
            errors.append(repr(exc))
            return 0

    env = Env(None, LOAD(load), STORE(store), CALL(call), None, None)
    result = getattr(lib, name)(C.byref(env), *args)
    return result, errors


def case(code, lib, entry, ram, scratch, cells, script_factory, args=()):
    """Run one input on both sides; return (equal, detail, executed pcs)."""
    end, frame, name, _, returns = FUNCTIONS[entry]
    images = []
    for _ in range(2):
        bus = Bus(ram, scratch, frame=(SP - frame, SP))
        for address, width, value in cells:
            bus.put(address, (value & ((1 << (8 * width)) - 1)).to_bytes(width, 'little'), log=False)
        images.append(bus)
    original, cbus = images
    try:
        stop = STOPS.get(entry)
        machine = run_original(code, entry, end, original, script_factory(), args=args,
                               start=entry if stop else None, stop=stop)
    except Exception as exc:
        return False, f'original failed: {exc!r}', set()
    result, errors = run_c(lib, name, cbus, script_factory(), args=args)
    if errors:
        return False, f'C errors {errors[:3]}', machine.executed
    if original.events != cbus.events:
        n = next((i for i, (x, y) in enumerate(zip(original.events, cbus.events)) if x != y),
                 min(len(original.events), len(cbus.events)))
        return False, dict(first_difference=n, original=original.events[max(0, n - 2):n + 3],
                           c=cbus.events[max(0, n - 2):n + 3],
                           lengths=(len(original.events), len(cbus.events))), machine.executed
    if returns and (machine.reg[2] & 0xffffffff) != result:
        return False, f'return {machine.reg[2] & 0xffffffff:#x} != {result:#x}', machine.executed
    return True, len(original.events), machine.executed


def main_frame_case(code, lib, ram, scratch, cells, script_factory):
    """One iteration of the main loop 0011d938..0011d9fc on both sides.

    The original loop never returns; it is run from the instruction after
    its head (the head's `lui at, 0x0096` is applied first, identically)
    until the back branch reaches the head again."""
    entry, end = MAIN_FRAME
    images = []
    for _ in range(2):
        bus = Bus(ram, scratch, frame=(SP - 0x10, SP))
        for address, width, value in cells:
            bus.put(address, (value & ((1 << (8 * width)) - 1)).to_bytes(width, 'little'), log=False)
        images.append(bus)
    original, cbus = images
    machine = Machine()
    machine.mem = original
    machine.reg[28] = GP
    machine.reg[29] = SP - 0x10
    script = script_factory()
    callees = set()
    for pc in range(entry, end, 4):
        word = struct.unpack_from('<I', code, pc - 0x100000)[0]
        if word >> 26 == 3:
            callees.add(((pc + 4) & 0xf0000000) | ((word & 0x3ffffff) << 2))

    def hook(pc):
        def fire(cpu):
            row = {n: cpu.reg[GPR[n]] & 0xffffffff for n in CONTRACT.get(pc, ())}
            original.events.append(('c', pc, tuple(sorted(row.items()))))
            return script(pc, original)
        return fire
    machine.hooks = {pc: hook(pc) for pc in callees}
    for pc in range(entry, end, 4):
        original.put(pc, code[pc - 0x100000:pc - 0x100000 + 4], log=False)
    if machine.load(entry, 4) != 0x3c010096:
        raise ValueError('unexpected main-loop head')
    machine.setreg(1, 0x00960000)
    machine.executed.add(entry)
    machine.run(entry + 4, stop=entry, limit=200000)
    result, errors = run_c(lib, 'hg_main_frame', cbus, script_factory(), sp=SP - 0x10)
    if errors:
        return False, f'C errors {errors[:3]}', machine.executed
    if original.events != cbus.events:
        n = next((i for i, (x, y) in enumerate(zip(original.events, cbus.events)) if x != y),
                 min(len(original.events), len(cbus.events)))
        return False, dict(first_difference=n, original=original.events[max(0, n - 2):n + 3],
                           c=cbus.events[max(0, n - 2):n + 3]), machine.executed
    return True, len(original.events), machine.executed


def packet_image(root=ROOT):
    """Resident-segment image built only from the published original-byte
    packet and jump tables (synthetic CI; no ELF). Unlisted words are zero."""
    import json
    packet = json.loads((root / 'analysis/game_lifecycle/functions.json').read_text())
    tables = json.loads((root / 'analysis/game_lifecycle/tables.json').read_text())
    if packet['program']['sha256'] != ELF_SHA256 or tables['elf_sha256'] != ELF_SHA256:
        raise ValueError('packet is not for the authenticated Japanese ELF')
    image = bytearray(0x2ea980)
    for function in packet['functions']:
        for row in function['instructions']:
            address = int(row['address'], 16)
            image[address - 0x100000:address - 0x100000 + 4] = bytes.fromhex(row['bytes'])
    for table in tables['tables']:
        base = int(table['address'], 16)
        for i, word in enumerate(table['words']):
            struct.pack_into('<I', image, base - 0x100000 + 4 * i, int(word, 16))
    return bytes(image)
