#!/usr/bin/env python3
"""Run authenticated SCPS-15046 HGScript instructions against the C runtime."""

import argparse
from collections import Counter
import ctypes as C
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile

from ee_subset import Machine, sx
import hg_identify
from hg_content.corpus import authenticate, open_source
from hg_content.pack import Pack

ROOT = Path(__file__).resolve().parents[1]
BUILD = json.loads((ROOT / 'evidence/builds/japan.json').read_text())
SP = 0x01fffc00
GP = 0x003e7f70
STATE = 0x00a6bcc0
COLLECTION = 0x01200000
PAD_EDGE = 0x0095e568
VOICE_STATE = 0x003f2fa0
MASK32 = 0xffffffff


class RuntimeMismatch(ValueError):
    """A measured EE/C difference, distinct from a verifier or callback fault."""

U32 = C.c_uint32
LOAD = C.CFUNCTYPE(U32, C.c_void_p, U32, U32)
STORE = C.CFUNCTYPE(None, C.c_void_p, U32, U32, U32)
CALL = C.CFUNCTYPE(U32, C.c_void_p, *([U32] * 5))
CALL_FP = C.CFUNCTYPE(U32, C.c_void_p, *([U32] * 7))


class Env(C.Structure):
    _fields_ = [('user', C.c_void_p), ('load', LOAD), ('store', STORE),
                ('call', CALL), ('call_fp', CALL_FP)]


def elf_image(path):
    data = path.read_bytes()
    manifest = BUILD['elf']
    if hashlib.sha256(data).hexdigest() != manifest['sha256']:
        raise ValueError('ELF identity differs from the Japanese SCPS-15046 manifest')
    elf = hg_identify.elf32(data)
    if elf['entry'] != manifest['entry'] or elf['pt_load'] != manifest['pt_load']:
        raise ValueError('ELF load mapping differs from the Japanese manifest')
    segment, = (p for p in elf['pt_load'] if p['vaddr'] == 0x100000 and p['filesz'])
    start = hg_identify.va_to_offset(elf, 0x100000, segment['filesz'])
    return data[start:start + segment['filesz']]


def verify_original_ranges(image):
    source = ROOT / 'analysis/hgscript_runtime/functions.json'
    packet = json.loads(source.read_text())
    if packet['elf_sha256'] != BUILD['elf']['sha256']:
        raise ValueError('instruction inventory names a different original build')
    ranges = [span for entry in packet['functions'] for span in entry['ranges']]
    ranges.extend(packet['other_ranges'])
    for span in ranges:
        low = int(span['start'], 16) - 0x100000
        high = int(span['end_exclusive'], 16) - 0x100000
        if not 0 <= low < high <= len(image):
            raise ValueError(f'outside the authenticated resident segment: {span}')
        if hashlib.sha256(image[low:high]).hexdigest() != span['sha256']:
            raise ValueError(f'original bytes differ at {span["start"]}')
    return dict(functions=len(packet['functions']), ranges=len(ranges),
                bytes=sum(int(span['end_exclusive'], 16) - int(span['start'], 16)
                          for span in ranges))


class Bus:
    """Independent guest memory for each side with sparse writes over PT_LOAD."""

    def __init__(self, image):
        self.image = image
        self.memory = {}
        self.dirty = set()
        self.calls = []
        self.errors = []
        self.reads = 0

    @staticmethod
    def check(address, size):
        end = address + size
        if not (0 <= address < end <= 0x02000000 or
                0x70000000 <= address < end <= 0x70004000):
            raise ValueError(f'unmapped guest access {address:#x}+{size}')

    def load(self, address, size):
        self.check(address, size)
        start = address - 0x100000
        base = self.image[start:start + size] if 0 <= start < len(self.image) else b''
        return int.from_bytes(bytes(self.memory.get(address + n, base[n] if n < len(base) else 0)
                                    for n in range(size)), 'little')

    def store(self, address, value, size):
        self.check(address, size)
        raw = (value & ((1 << (size * 8)) - 1)).to_bytes(size, 'little')
        for n, byte in enumerate(raw, address):
            self.memory[n] = byte
            if not SP - 0x2000 <= n < SP:
                self.dirty.add(n)

    def put(self, address, data):
        for n, byte in enumerate(data, address):
            self.store(n, byte, 1)

    def string(self, address):
        data = bytearray()
        while len(data) < 4096:
            byte = self.load(address + len(data), 1)
            if byte == 0:
                return bytes(data)
            data.append(byte)
        raise ValueError('unterminated guest text')

    def external(self, entry, a0, a1, a2, a3, f12=0, f13=0):
        if entry == 0x1564a0:
            self.calls.append((entry, a0, a1, a2, a3, f12, f13))
        elif entry == 0x11f880:
            self.calls.append((entry, a0, a1))
        elif entry == 0x156550:
            self.calls.append((entry, a0))
        elif entry == 0x182850:
            text_hash = hashlib.sha256(self.string(a0)).hexdigest()
            # a3 is not live on entry, as the original function immediately overwrites it.
            style = (self.load(a1, 4), self.load(a1 + 13, 1), self.load(a1 + 16, 16))
            self.calls.append((entry, a0, a1, a2, f12, f13, style, text_hash))
            return 24
        else:
            raise ValueError(f'unhandled external call at {entry:#x}')
        return 0

    def original(self, entry, args, *, fp=(0, 0), t0=0, registers=None,
                 stop=0x00fff000, unhook=None):
        m = Machine()
        m.reg[28], m.reg[29] = GP, SP
        m.reg[4:4 + len(args)] = [sx(value, 32) & ((1 << 64) - 1) for value in args]
        m.reg[8] = t0
        for register, value in (registers or {}).items():
            m.reg[register] = value
        m.fpr[12], m.fpr[13] = fp
        m.load = self.load
        m.store = self.store
        for target in (0x1564a0, 0x156550, 0x182850, 0x11f880):
            if target == unhook:
                continue
            m.hooks[target] = lambda machine, target=target: self.external(
                target, *(value & MASK32 for value in machine.reg[4:8]),
                *machine.fpr[12:14])
        # These optimized libc leaves use MMI outside the supplied EE subset.
        def copy(machine, append=False):
            dest, src = (reg & MASK32 for reg in machine.reg[4:6])
            tail = dest + len(self.string(dest)) if append else dest
            self.put(tail, self.string(src) + b'\0')
            return dest

        m.hooks[0x11b378] = copy
        m.hooks[0x11b100] = lambda machine: copy(machine, append=True)
        m.hooks[0x11b490] = lambda machine: len(self.string(machine.reg[4] & MASK32))
        m.run(entry, stop=stop)
        return m.reg[2] & MASK32, m.executed

    def env(self):
        def protected(fn, default):
            def callback(_, *args):
                if self.errors:
                    return default
                try:
                    return fn(*args)
                except (ValueError, OverflowError) as exc:
                    self.errors.append(str(exc))
                    return default
            return callback

        def read(address, size):
            self.reads += 1
            if self.reads > 100000:
                raise ValueError('C read budget exceeded')
            return self.load(address, size)

        self.reads = 0
        return Env(None, LOAD(protected(read, 0)),
                   STORE(protected(lambda address, size, value:
                                   self.store(address, value, size), None)),
                   CALL(protected(self.external, 0)),
                   CALL_FP(protected(self.external, 0)))


def library(directory, source=None):
    output = directory / 'hgscript.so'
    subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                    '-pedantic', '-ffp-contract=off', '-fPIC', '-shared', '-Wl,--no-undefined',
                    '-I', str(ROOT / 'src/hgscript'),
                    str(ROOT / 'src/hgscript/hgscript.c'),
                    str(source or ROOT / 'src/hgscript/timed.c'), '-o', str(output)],
                   check=True)
    lib = C.CDLL(str(output))
    signatures = {
        'timed_init': (5, None), 'timed_prelude': (1, None), 'timed_tick': (1, U32), 'timed_draw': (2, None),
        'walk': (4, U32), 'draw_handle': (6, None), 'page_count': (2, U32),
        'line_count': (2, U32), 'handle_entry': (2, U32), 'advance': (2, None),
        'format_number': (3, None),
    }
    for name, (count, result) in signatures.items():
        fn = getattr(lib, f'hg_script_{name}')
        fn.argtypes = [C.POINTER(Env)] + [U32] * count
        fn.restype = result
    for name in ('hg_text_width', 'hg_text_width_bytes'):
        fn = getattr(lib, name)
        fn.argtypes = [C.POINTER(Env), U32]
        fn.restype = U32
    for name in ('hg_text_classify', 'hg_text_width_ascii', 'hg_text_width_ascii_alt'):
        fn = getattr(lib, name)
        fn.argtypes = [U32]
        fn.restype = U32
    return lib


def compare(a, b, label):
    if b.errors:
        raise ValueError(f'{label}: C callback {b.errors[0]}')
    for address in sorted(a.dirty | b.dirty):
        expected, actual = a.load(address, 1), b.load(address, 1)
        if expected != actual:
            raise RuntimeMismatch(f'{label}: byte at {address:#x}: EE {expected:02x}, C {actual:02x}')
    if a.calls != b.calls:
        for index, (expected, actual) in enumerate(zip(a.calls, b.calls)):
            if expected != actual:
                raise RuntimeMismatch(f'{label}: external request {index} differs '
                                      f'(EE {expected!r}, C {actual!r})')
        raise RuntimeMismatch(f'{label}: request count differs '
                              f'(EE {len(a.calls)}, C {len(b.calls)})')
    a.dirty.clear()
    b.dirty.clear()
    calls = a.calls[:]
    a.calls.clear()
    b.calls.clear()
    return calls


def timed_case(lib, image, data, record, *, voice, press=()):
    a, b = Bus(image), Bus(image)
    for bus in (a, b):
        bus.put(COLLECTION, data)
        for offset in range(0, 16, 4):
            bus.store(0x0039b3e0 + offset, 0x43000000, 4)
        bus.dirty.clear()
    x, y = 0, 0x42a00000
    _, words = a.original(0x1ccd80, (STATE, COLLECTION, record), fp=(x, y))
    b_env = b.env()
    lib.hg_script_timed_init(C.byref(b_env), STATE, COLLECTION, record, x, y)
    compare(a, b, 'initialization')
    for bus in (a, b):
        bus.store(STATE, 0, 4)

    phases = Counter()
    audio_requests = []
    draw_request_count = 0
    draw_digest = hashlib.sha256()
    steps = []
    for frame in range(5000):
        # Identical external inputs for both buses. The ready edge follows the
        # fixture schedule; it is never copied from either implementation.
        if voice:
            latch = {1: 3, 4: 0}.get(frame)
            if latch is not None:
                for bus in (a, b):
                    bus.store(VOICE_STATE, latch, 1)
        for bus in (a, b):
            bus.store(PAD_EDGE, 0x20 if frame in press else 0, 2)
        phase, executed = a.original(0x1ccfa0, (STATE,))
        words.update(executed)
        b_env = b.env()
        actual = lib.hg_script_timed_tick(C.byref(b_env), STATE)
        calls = compare(a, b, f'tick {frame}')
        for call in calls:
            audio_requests.append([frame, *call])
        _, executed = a.original(0x1cd9c0, (STATE,))
        words.update(executed)
        b_env = b.env()
        lib.hg_script_timed_draw(C.byref(b_env), STATE, SP)
        rendered = compare(a, b, f'draw {frame}')
        if rendered:
            draw_request_count += len(rendered)
            draw_digest.update(json.dumps([frame, rendered], separators=(',', ':')).encode())
        if phase != actual:
            raise RuntimeMismatch(f'tick {frame}: phase return differs')
        phases[sx(phase, 32)] += 1
        signed_phase = sx(phase, 32)
        if calls or not steps or signed_phase != steps[-1][1]:
            steps.append([frame, signed_phase, a.load(STATE + 0xc, 4) - COLLECTION])
        if phase == MASK32:
            return dict(frames=frame + 1, phases=dict(phases), transitions=steps,
                        audio_requests=audio_requests, draw_requests=draw_request_count,
                        draw_trace_sha256=draw_digest.hexdigest(), executed=words)
    raise ValueError('sequence failed to reach terminal state')


def leaf_cases(lib, image):
    original, port = Bus(image), Bus(image)
    executed = set()
    results = Counter()

    def check(entry, symbol, register_args, c_args, *, takes_env=True, fp=(0, 0),
              t0=0, compare_return=True):
        expected, words = original.original(entry, register_args, fp=fp, t0=t0)
        executed.update(words)
        env = port.env()
        call = getattr(lib, symbol)
        actual = call(C.byref(env), *c_args) if takes_env else call(*c_args)
        label = f'{entry:#x} {symbol} args={c_args}'
        compare(original, port, label)
        if compare_return and expected != actual:
            raise ValueError(f'{label}: EE returned {expected:#x}, C returned {actual:#x}')
        results[f'{entry:08x}'] += 1

    for entry, symbol in ((0x182490, 'hg_text_classify'),
                          (0x14b330, 'hg_text_width_ascii'),
                          (0x14b690, 'hg_text_width_ascii_alt')):
        for value in range(256):
            check(entry, symbol, (value,), (value,), takes_env=False)
    for text in (b'', b'Width', bytes.fromhex('82a041e080'), b'space and punctuation?'):
        for bus in (original, port):
            bus.put(COLLECTION, text + b'\0')
        for entry, symbol in ((0x182540, 'hg_text_width'),
                              (0x1824e0, 'hg_text_width_bytes')):
            check(entry, symbol, (COLLECTION,), (COLLECTION,))
    for mode in (0, 1, 2):
        for value in (0, 1, 20, -37, 10003, -2147483648, 2147483647):
            args = (COLLECTION + 0x200, value, mode)
            check(0x182e90, 'hg_script_format_number', args, args, compare_return=False)
    for pages in (0, 1, 2, 4):
        for bus in (original, port):
            bus.store(COLLECTION + 0x100, COLLECTION + 0x400, 4)
            bus.put(COLLECTION + 0x400, struct.pack('<6I', 1, 2, 5, 3, 5, 0))
        args = (COLLECTION + 0x100, pages)
        check(0x182ae0, 'hg_script_advance', args, args, compare_return=False)
    for leading in ((0x4abc << 6) | 11, 1):
        for bus in (original, port):
            bus.store(STATE + 0xc, COLLECTION + 0x400, 4)
            bus.store(COLLECTION + 0x400, leading, 4)
        check(0x25cf60, 'hg_script_timed_prelude', (), (STATE,), compare_return=False)
    return dict(results), executed


def static_case(lib, image, data, record_index):
    original, port = Bus(image), Bus(image)
    handle = 3
    executed = set()
    width_checks = 0
    draw_requests = 0
    requests = hashlib.sha256()
    for bus in (original, port):
        bus.put(COLLECTION, data)
        bus.store(0x003f4300 + handle * 4, COLLECTION, 4)
        bus.store(0x003eaed0, 0x20, 2)
        bus.dirty.clear()
    env = port.env()
    record = COLLECTION + original.load(COLLECTION + 8 + 4 * record_index, 4)
    pool = COLLECTION + original.load(COLLECTION + 4, 4)
    expected, words = original.original(0x183a30, (handle, record_index))
    executed.update(words)
    if expected != lib.hg_script_handle_entry(C.byref(env), handle, record_index):
        raise ValueError('record header metadata address differs')
    pages, words = original.original(0x182a60, (handle, record_index))
    executed.update(words)
    if pages != lib.hg_script_page_count(C.byref(env), handle, record_index):
        raise ValueError('page count differs')
    for mode in (0, 1, 0x80, 0x81):
        expected, words = original.original(0x182b50, (record, pool, 0, 0), t0=mode)
        executed.update(words)
        if expected != lib.hg_script_walk(C.byref(env), record, pool, mode, SP):
            raise ValueError(f'measured width differs for mode {mode:#x}')
        compare(original, port, 'measure')
        width_checks += 1
    for page in range(pages + 1):
        expected, words = original.original(0x183910, (handle, record_index, page))
        executed.update(words)
        if expected != lib.hg_script_line_count(C.byref(env), record, page):
            raise ValueError('line count differs')
        for options in (0, 0x20000, 0x40000, 0xc0000):
            flags = page | options
            x, y = 0x43440000, 0x42a00000
            _, words = original.original(0x1836b0, (handle, record_index, flags), fp=(x, y))
            executed.update(words)
            lib.hg_script_draw_handle(C.byref(env), handle, record_index, flags, x, y, SP)
            draws = compare(original, port, f'static page {page} flags {flags:#x}')
            draw_requests += len(draws)
            requests.update(json.dumps([flags, draws], separators=(',', ':')).encode())
    return dict(record=record_index, pages=pages + 1, width_checks=width_checks,
                draw_requests=draw_requests, draw_trace_sha256=requests.hexdigest()), executed


CASES = ((73, 6, 1, False, ()), (63, 18, 5, True, ()),
         (73, 6, 2, True, (8, 9, 30)))


DEFECTS = {
    'pool-offset-one-word-late': ('uint32_t text = pool + operand * 4 + 4;',
                                  'uint32_t text = pool + operand * 4 + 8;'),
    'wait-does-not-yield': ('set_word(e, s + PHASE, 1);\n                    yield = 1;',
                            'set_word(e, s + PHASE, 1);\n                    yield = 0;'),
    'audio-ignores-ready-edge': ('load(e, VOICE_STATE, 1) != 3',
                                 'load(e, VOICE_STATE, 1) == 3'),
    'wrong-audio-request-channel': ('0x001564a0, 1, voice, 1, 0',
                                     '0x001564a0, 2, voice, 1, 0'),
}


def check_defects(image, data, scratch):
    source_dir = ROOT / 'src/hgscript'
    source = (source_dir / 'timed.c').read_text()
    results = {}
    for name, (before, after) in DEFECTS.items():
        if source.count(before) != 1:
            raise ValueError(f'cannot apply defect control {name}')
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            folder = Path(directory)
            mutant = folder / 'timed.c'
            mutant.write_text(source.replace(before, after))
            lib = library(folder, mutant)
            try:
                timed_case(lib, image, data, 5, voice=True)
            except RuntimeMismatch as exc:
                results[name] = str(exc)
            else:
                raise ValueError(f'failed to reject {name}')
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--elf', required=True, type=Path)
    parser.add_argument('--iso', required=True, type=Path)
    parser.add_argument('--scratch', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    image = elf_image(args.elf)
    original_bytes = verify_original_ranges(image)
    args.scratch.mkdir(parents=True, exist_ok=True)
    fp, iso, pack_record, read_at = open_source(image_path=args.iso, iso_module=hg_identify)
    with fp, tempfile.TemporaryDirectory(dir=args.scratch) as directory:
        authenticate(fp, BUILD['image']['sha256'])
        pack = Pack(fp, iso, pack_record, read_at)
        lib = library(Path(directory))
        results = []
        leaves, executed = leaf_cases(lib, image)
        members = {(m.directory, m.index): m for m in pack.all_members()}

        resource = pack.read_member(members[(1, 50)])
        static = []
        for record in (2, 157):
            row, addresses = static_case(lib, image, resource, record)
            executed.update(addresses)
            static.append(dict(directory=1, member=50,
                               sha256=hashlib.sha256(resource).hexdigest(), **row))
        for directory_number, member_index, record, voice, press in CASES:
            member = members[(directory_number, member_index)]
            data = pack.read_member(member)
            row = timed_case(lib, image, data, record, voice=voice, press=press)
            executed.update(row.pop('executed'))
            results.append(dict(directory=directory_number, member=member_index,
                                record=record, input_edge_frames=list(press), sha256=hashlib.sha256(data).hexdigest(), **row))
        defects = check_defects(image, pack.read_member(members[(63, 18)]), args.scratch)
    receipt = dict(schema='hg-m20-differential/v1', elf_sha256=BUILD['elf']['sha256'],
                   iso_sha256=BUILD['image']['sha256'], original_bytes=original_bytes,
                   unique_original_instruction_addresses=len(executed),
                   fixture_voice_latch={'ready_frame': 1, 'idle_frame': 4},
                   fixture_renderer_return=24, leaves=leaves, static_sequences=static,
                   sequences=results, negative_controls=defects)
    if args.output:
        with args.output.open('x') as output:
            json.dump(receipt, output, indent=2)
            output.write('\n')
    else:
        print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
