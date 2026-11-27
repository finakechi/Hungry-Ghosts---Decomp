#!/usr/bin/env python3
"""Compare a cold-loaded title scene through its original actor and timeline."""
import argparse
from collections import Counter
import ctypes as C
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile

import hgscript_differential as D
from ee_subset import Machine
from hg_identify import elf32, va_to_offset
from resource_execution import BUSY, TASK, TASK_ACTIVE, ORIGINAL_ELF, ORIGINAL_ISO
from run_resource_lifecycle import capture
from verify_resource_retail import ColdBootRunner, blank_guest, boot_to_title, finish
from verify_title_hgscript import (
    N, RetailDisc, Title, TitleBus, initialize_file_records, require, write_receipt,
)

ROOT = N.ROOT
U32 = C.c_uint32
ENV = C.POINTER(N.L.Env)
MODE = 0x3ead30
SAVE_POSITION = 0x1404f0
LETTERBOX = 0x139690
LETTERBOX_DRAW = 0x139780
CINEMATIC = 0x265460
NEW_GAME_CONFIRM = 0x2cf330
AREA_COORDINATOR = 0x12f850
AREA1 = '/AREA1/A1_B00.BIN'
TITLE_HANDLE = 0x3f4bbc
AREA_DOMAINS = (
    ('mode handler and phase', MODE + 4, 7),
    ('area selector', 0x3fa1b0, 4),
    ('arena', 0x3f4d3c, 36),
    ('handle table', 0x3f4300, 2600),
    ('worker flags', BUSY, 1),
    ('worker completion kind', TASK + 0x30, 4),
    ('worker completion directory', TASK + 0x44, 4),
    ('overlay', 0xa87800, 30720),
)
POST_MENU_STATES = ((D.GP + 0x2f48, 12), (0xa6bb60 + 0x40, 0x20))
ACTOR_LISTS = {
    0x1430a0: 'hg_actor_update_main', 0x143100: 'hg_actor_update_priority',
    0x143150: 'hg_actor_update_type2', 0x1431a0: 'hg_actor_update_type2_priority',
}
SCENE_ENTRIES = (0x141470, 0x25c170, 0xa88660, 0xa88a20, 0xa88b40, 0xa886e0)
TIMELINE = 0xa6ace0
TITLE_TASK = TIMELINE + 0x50 + 0x14 * 0x30
STATES = (
    (MODE, 0x28), (0x3eaeb0, 0x2a), (0x3eb050, 4), (0x3f9772, 1),
    (0x3f9860, 0x120), (0x3f9fdf, 1), (0x4d8150, 0x3d0),
    (0x539430, 0x38), (TIMELINE, 0xe78), (D.STATE, 0x264),
    (0x70000ba0, 16),
)
# Render, sound and engine service calls whose implementations are outside this
# comparison. Camera 0012c980 and the title timeline are executed below.
EXTERNAL = {
    0x198f60, 0x13b6e0, 0x18c520, 0x182480, 0x13ce80, 0x137ac0,
    LETTERBOX, 0x167740, CINEMATIC, 0x25ca90, 0x182430, 0x13fef0,
    0x13a4c0, 0x166770, 0x166910, 0x1948b0, 0x1487a0, 0x14a0a0, 0x152aa0, 0x18c9c0, 0x18ecb0, 0x281680, 0x2f7830,
}
LIVE_REGIONS = ((0x3f4d3c, 36), (0x1661500, 120), (0x172af00, 1376))
LIVE_STOPS = (
    ('live-create', 0xa88660), ('live-dialogue-init', 0xa88c64),
    ('live-voice', 0x1564a0), ('live-glyph', 0x1cdb70),
    ('live-reset', 0xa88ce8), ('live-presentation-done', 0xa886e0),
)
SELECTED_RANGES = (
    (0x141470, 0x1414ec), (0x25c170, 0x25c20c), (0xa88660, 0xa886a0),
    (0xa886e0, 0xa886f4), (0xa88a20, 0xa88b40), (0xa88b40, 0xa88d48),
    (0x25ddd0, 0x25e6c0), (0xa8a580, 0xa8a9d0),
)
POST_MENU_RANGES = (
    (LETTERBOX, LETTERBOX_DRAW), (CINEMATIC, 0x2658b0), (0x131f40, 0x132080),
)
CONTROL_EDITS = {
    'swap-dialogue-and-actors': (
        '    call(env, 0x1430a0, 0, 0, 0);\n'
        '    call(env, 0x143100, 0, 0, 0);\n'
        '    call(env, 0x18c520, 0, 0, 0);\n'
        '    call(env, 0x143150, 0, 0, 0);\n'
        '    call(env, 0x1431a0, 0, 0, 0);\n'
        '    call(env, 0x25c170, 0, 0, 0);',
        '    call(env, 0x25c170, 0, 0, 0);\n'
        '    call(env, 0x1430a0, 0, 0, 0);\n'
        '    call(env, 0x143100, 0, 0, 0);\n'
        '    call(env, 0x18c520, 0, 0, 0);\n'
        '    call(env, 0x143150, 0, 0, 0);\n'
        '    call(env, 0x1431a0, 0, 0, 0);',
    ),
    'skip-required-fade': (
        '        call(env, 0x139040, 4, 0, 0);',
        '        call(env, 0x139010, 0, 0, 0);',
    ),
    'omit-release': (
        '            hg_title_script_release(env, &scene->dialogue);',
        '            (void)env;',
    ),
}


class Scene(C.Structure):
    _fields_ = [('dialogue', Title), ('call_fp', D.CALL_FP), ('scratch_top', U32)]


def build(output, source=None):
    lib = N.F.build(output, extra_sources=[source or ROOT / 'src/scene/title_scene.c'])
    lib.hg_title_scene_call.argtypes = [ENV, C.POINTER(Scene), U32, U32, C.POINTER(U32)]
    lib.hg_title_scene_call.restype = C.c_int
    lib.hg_actor_allocate.argtypes = [ENV, U32, U32, U32]
    lib.hg_actor_allocate.restype = U32
    for name in ('hg_fade_service', *ACTOR_LISTS.values()):
        getattr(lib, name).argtypes = [ENV]
        getattr(lib, name).restype = None
    return lib


class Runner(ColdBootRunner):
    def __init__(self, lib, mem, disc, port):
        super().__init__(lib, mem, {}, 0, disc)
        self.port = port
        self.scene_enabled = False
        self.outer_bus = TitleBus(mem)
        self.fp = D.CALL_FP(self.call_fp)
        self.scene = Scene(Title(), self.fp, D.SP - 0x20)
        self.events = []
        self.new_game = False
        self.confirmations = 0
        self.cold_request_count = 0
        self.area_checkpoint = None

    def call_fp(self, _user, pc, a, b, c, d, f12, f13):
        try:
            return self.presentation(pc, a, b, c, d, f12, f13)
        except Exception as exc:
            self.faults.append(f'{pc:#x}: {exc!r}')
            return 0

    def presentation(self, pc, a, b, c, d, f12=0, f13=0):
        bus = self.outer_bus
        result = D.Bus.external(bus, pc, a, b, c, d, f12, f13)
        if pc in (0x1564a0, 0x156550):
            result, words = bus.original(pc, (a, b, c, d), fp=(f12, f13),
                                         registers={29: D.SP - 0x1000}, unhook=pc)
            self.interp_pcs.update(words)
        return result

    def execute(self, pc, args=(), *, stop=None, stack_top=D.SP):
        m = Machine()
        m.mem = self.mem
        m.reg[28], m.reg[29] = D.GP, stack_top
        m.reg[4:4 + len(args)] = [D.sx(value, 32) for value in args]
        m.hooks = {at: self.intercept for at in EXTERNAL | {
            0x119ac8, 0x119910, 0x139010, 0x139040, 0x1390b0,
            0x1564a0, 0x156550, 0x182850,
            0x11b378, 0x11b100, 0x11b490, 0x11f880,
        }}
        if self.port:
            m.hooks.update({at: self.intercept for at in (*SCENE_ENTRIES, *ACTOR_LISTS)})
        m.hooks.pop(pc, None)
        try:
            m.run(pc, limit=180000, **({} if stop is None else {"stop": stop}))
        except ValueError as exc:
            raise ValueError(f'EE {m.pc:08x}: {exc}') from exc
        self.interp_pcs.update(m.executed)
        return m.reg[2] & D.MASK32

    def intercept(self, cpu):
        pc = cpu.pc
        args = tuple(reg & D.MASK32 for reg in cpu.reg[4:8])
        if pc in (0x1564a0, 0x156550, 0x182850, 0x11f880):
            return self.presentation(pc, *args, *cpu.fpr[12:14])
        if pc in (0x11b378, 0x11b100, 0x11b490):
            bus = self.outer_bus
            dest, src, _, _ = args
            if pc == 0x11b490:
                return len(bus.string(dest))
            tail = dest + len(bus.string(dest)) if pc == 0x11b100 else dest
            bus.put(tail, bus.string(src) + b'\0')
            return dest
        return self.route(pc, *args)

    def route(self, pc, a, b, c, d):
        if self.new_game:
            if pc == NEW_GAME_CONFIRM:
                self.confirmations += 1
                return 1  # Explicit accepted New Game UI response, with no card opened.
            if pc == AREA_COORDINATOR:
                self.area_checkpoint = read_area_domains(self.mem)
                return 0  # Stop at the first active call, before world simulation.
            if pc in (LETTERBOX, CINEMATIC):
                self.events.append((pc,))
                # Keep the letterbox state update and original clip clock.
                # GS packet submission starts at the excluded letterbox tail.
                stop = LETTERBOX_DRAW if pc == LETTERBOX else None
                return self.execute(pc, stop=stop, stack_top=D.SP - 0x1000)
        if not self.scene_enabled:
            return super().route(pc, a, b, c, d)
        if pc in (0x1564a0, 0x156550, 0x11f880):
            fp = self.pending.pop('f12', 0), self.pending.pop('f13', 0)
            self.pending.clear()
            return self.presentation(pc, a, b, c, d, *fp)
        if pc in EXTERNAL:
            self.events.append((pc, a) if pc == 0x13b6e0 else (pc,))
            return 0
        if pc in SCENE_ENTRIES:
            if not self.port:
                return self.execute(pc, (a,))
            result = U32()
            require(self.lib.hg_title_scene_call(C.byref(self.env), C.byref(self.scene),
                                                 pc, a, C.byref(result)), 'missing scene route')
            return result.value
        if pc in ACTOR_LISTS:
            if not self.port:
                return self.execute(pc)
            getattr(self.lib, ACTOR_LISTS[pc])(C.byref(self.env))
            return 0
        if pc == 0xa87ed0:
            # Original alpha progression after packet generation. Only the
            # alpha writes are part of the menu decision boundary here.
            return self.execute(0xa8849c if not a else 0xa885c8, stop=0xa88640)
        if self.port and pc == 0x1429b0:
            return self.lib.hg_actor_allocate(C.byref(self.env), a, b, c)
        if pc in (0x1413a0, SAVE_POSITION, 0x1429b0, 0x142e90, 0x25cd90, 0x25ce90,
                  0x25e6c0, 0x25e930, 0x25ddd0, 0x12c980, 0x12c3d0, 0x196ad0, 0x1390f0, 0x139140,
                  0x139190, 0x1391f0):
            return self.execute(pc, (a, b, c, d))
        if 0x4d8150 <= a < 0x539430 and self.scheduler.load(a + 0x1c) == pc:
            return self.execute(pc, (a,))
        return super().route(pc, a, b, c, d)


def boot(lib, disc):
    mem = blank_guest(disc)
    initialize_file_records(mem, disc, [f'/AREA0/A0_B{i:02}.BIN' for i in range(10)] +
                            ['/AREA1/A1_B00.BIN'])
    run = Runner(lib, mem, disc, True)
    mem.write(0x700000c0, MODE.to_bytes(4, 'little'))
    mem.write(MODE, b'\1\0\0\0\x10\x18\x12\0')
    run.route(0x121810, 0, 0, 0, 0)
    finish(run)
    return run, boot_to_title(run, disc)


def check_frame(original, port, frame):
    require(not original.faults and not port.faults,
            f'frame {frame}: guest callback error {original.faults} {port.faults}')
    if original.events != port.events:
        raise D.RuntimeMismatch(
            f'frame {frame}: scheduled services differ: {original.events!r} / {port.events!r}')
    original.events.clear()
    port.events.clear()
    for at, size in compared_states(port.new_game):
        want, have = original.mem.read(at, size), port.mem.read(at, size)
        if want != have:
            i = next(i for i in range(size) if want[i] != have[i])
            raise D.RuntimeMismatch(f'frame {frame}: {at+i:08x} got {have[i]:02x}, expected {want[i]:02x}')
    return D.compare(original.outer_bus, port.outer_bus, f'frame {frame}')


def compared_states(new_game):
    return STATES + POST_MENU_STATES if new_game else STATES


def read_area_domains(mem):
    return {name: mem.read(at, size) for name, at, size in AREA_DOMAINS}


def compare_area(original, port, disc):
    require(original.area_checkpoint == port.area_checkpoint,
            'first AREA1 coordinator checkpoint differs between carried guests')
    data = port.area_checkpoint
    overlay = disc.iso.read(AREA1)
    require(data['overlay'] == overlay, 'AREA1 overlay differs from disc')
    followups = port.scheduler.requests[port.cold_request_count:]
    expected = [(1, 9), (1, 0x6c), (1, 0x51), (2, 0xc6)]
    for runner in (original, port):
        requests = runner.scheduler.requests[runner.cold_request_count:]
        require([(job['kind'], job['directory']) for job in requests] == expected,
                f'gameplay resource order differs: {requests}')
        require(not runner.scheduler.thread and not runner.scheduler.transfer and
                not runner.scheduler.load(TITLE_HANDLE) and not runner.scheduler.load(BUSY, 1) and
                not runner.scheduler.load(TASK_ACTIVE, 2), 'AREA1 retained title or an unfinished worker')
        require(runner.confirmations == 1, 'expected one accepted New Game service response')
        require(len(runner.scheduler.finished) == len(runner.scheduler.requests),
                'AREA1 route lost a resource completion')
        worker = runner.scheduler.finished[-1]
        require((worker['kind'], worker['directory']) == (3, 0x85),
                'unexpected final worker state for AREA1')
    worker = port.scheduler.finished[-1]
    return dict(path=AREA1, requests=followups, worker=worker,
                area_reads=worker['last_read'] - followups[-1]['first_read'],
                coordinator=f'{AREA_COORDINATOR:08x}', confirmations=port.confirmations,
                domains={name: hashlib.sha256(raw).hexdigest() for name, raw in data.items()})


def compare_native_area(area, directory):
    _, native = capture(directory, 'area1-active')
    observed = read_area_domains(native)
    require(observed['mode handler and phase'] == bytes.fromhex('c0d11200050101'),
            'native stop is outside the first AREA1 coordinator phase')
    domains = {name: hashlib.sha256(raw).hexdigest() for name, raw in observed.items()}
    differences = {name: sha for name, sha in domains.items() if area['domains'][name] != sha}
    require(differences.keys() <= {'arena', 'handle table'},
            f'AREA1 native mode or worker checkpoint differs: {differences}')
    return dict(ee_sha256=hashlib.sha256(native.ee).hexdigest(),
                match=[name for name in domains if name not in differences], differ=differences)


def simulate(lib, disc, *, skip=None, until_reset=False, new_game=False,
             advance_interval=None, progress=False):
    port, publication = boot(lib, disc)
    port.cold_request_count = len(publication['requests'])
    first_thread = port.scheduler.finished[-1]['thread'] + 1
    original = Runner(lib, N.Mem(port.mem.ee, port.mem.sp), disc, False)
    original.scheduler.next_thread = first_thread
    render_context = 0x9e64a0
    identity = struct.pack('<16f', *(float(i % 5 == 0) for i in range(16)))
    for run in (original, port):
        run.scene_enabled = True
        run.new_game = new_game
        run.mem.write(0x3eb008, render_context.to_bytes(4, 'little'))
        # GS/VU output is a host boundary. Initialize its projection buffers
        # with an affine identity so original packet writers have a finite W.
        for offset in (0x150, 0x210, 0x310):
            run.mem.write(render_context + offset, identity)
    trace, events = Counter(), {}
    audio_requests, first_glyph = [], None
    publication['title_slot'] = port.scheduler.load(0x3f4bbc)
    publication['domains'] = [
        dict(address=f'{at:08x}', bytes=size, sha256=hashlib.sha256(port.mem.read(at, size)).hexdigest())
        for at, size in LIVE_REGIONS
    ]
    request_digest = hashlib.sha256()
    overlay = disc.iso.read('/AREA0/A0_B09.BIN')
    voice_edges = {}
    for frame in range(12500):
        mode = port.mem.read(MODE + 4, 8)
        if progress and frame % 128 == 0:
            print(f'frame {frame}: mode {mode.hex()} timeline '
                  f'{port.mem.read(TIMELINE, 16).hex()} events {events}', file=sys.stderr, flush=True)
        edge = 0x20 if frame == skip else 0
        if advance_interval and skip is not None and frame > skip and (frame - skip) % advance_interval == 0:
            edge = 0x20
        if new_game and mode[:6] == b'\xe0\x1b\x12\0\1\2' and frame % 16 == 0:
            edge = 0x20
        borrowed = bool(port.scene.dialogue.parent)
        for run in (original, port):
            # Host GS submission boundary. Give the original packet writers
            # private cursors, reset at the start of each driver unit.
            for channel in range(6):
                cursor = 0x1e00000 + channel * 0x10000
                run.mem.write(render_context + 0x53c + channel * 4, cursor.to_bytes(4, 'little'))
            run.mem.write(D.PAD_EDGE, edge.to_bytes(2, 'little'))
            run.mem.write(0x95e564, edge.to_bytes(2, 'little'))
            if frame in voice_edges:
                run.mem.write(D.VOICE_STATE, bytes([voice_edges[frame]]))
            run.route(0x143240, 0, 0, 0, 0)
            run.lib.hg_fade_service(C.byref(run.env))
            if new_game:
                run.vblanks = 1
                run.route(N.L.HG_WAIT_INTERRUPT, 0, 0, 0, 0)
                run.seq += 1
        calls = check_frame(original, port, frame)
        if port.scene.dialogue.parent and 'activation' not in events:
            events['activation'] = frame
        if calls:
            request_digest.update(json.dumps([frame, calls], separators=(',', ':')).encode())
        for call in calls:
            trace[hex(call[0])] += 1
            if call[0] in (0x1564a0, 0x156550):
                audio_requests.append([frame, *call])
            if call[0] == 0x1564a0 and call[1] == 1:
                voice_edges = {frame + 1: 3, frame + 200: 0}
                if call[2] == 0x240:
                    events.setdefault('voice_start', frame)
            if call[0] == 0x156550 and call[1] == 1:
                voice_edges = {frame + 1: 0}
            if call[0] == 0x182850 and 'first_glyph' not in events:
                events['first_glyph'] = frame
                first_glyph = dict(flags=call[3], text_sha256=call[-1])
        if port.area_checkpoint is not None:
            events['area1_ready'] = frame
            break
        if borrowed and not port.scene.dialogue.parent:
            events.setdefault('dialogue_reset', frame)
            if until_reset:
                break
        if port.mem.read(MODE + 4, 6) == b'\xe0\x1b\x12\0\1\2':
            events.setdefault('menu', frame)
            if not new_game:
                break
    else:
        raise ValueError(f'title sequence did not reach the requested endpoint, '
                         f'events {events}, mode {mode.hex()}, '
                         f'timeline {port.mem.read(TIMELINE, 16).hex()}')

    require(not port.scene.dialogue.parent, 'title endpoint retained the dialogue borrow')
    require({'activation', 'voice_start', 'first_glyph', 'dialogue_reset'} <= events.keys(),
            'title scene missed the selected dialogue')
    area = None
    if new_game:
        area = compare_area(original, port, disc)
        overlay = disc.iso.read(AREA1)
    else:
        require(port.mem.read(0x3f4bbc, 4) == bytes.fromhex('00156601'),
                'title release changed the worker-owned handle')
    require(all(run.mem.read(0xa87800, len(overlay)) == overlay for run in (original, port)),
            'published overlay differs from disc')
    return dict(publication=publication, frames=frame + 1, events=events, skip_frame=skip, area=area,
                requests=dict(trace), audio_requests=audio_requests, first_glyph=first_glyph,
                request_sha256=request_digest.hexdigest(),
                original_pcs=len(original.interp_pcs),
                shared_instruction_pcs=len(port.interp_pcs), compared_bytes=sum(size for _, size in compared_states(new_game)),
                advance_interval=advance_interval)


def original_ranges(disc, new_game=False):
    overlay = disc.iso.read('/AREA0/A0_B09.BIN')
    mapping = elf32(disc.elf)
    rows = []
    for start, end in (*SELECTED_RANGES, *(POST_MENU_RANGES if new_game else ())):
        size = end - start
        if start >= 0xa87800:
            image = overlay
            offset = start - 0xa87800
            file = '/AREA0/A0_B09.BIN'
        else:
            image = disc.elf
            offset = va_to_offset(mapping, start, size)
            file = '/SCPS_150.46'
        raw = image[offset:offset + size]
        require(len(raw) == size, 'selected address range exceeds authenticated file')
        rows.append(dict(start=f'{start:08x}', end=f'{end:08x}', file=file,
                         offset=offset, size=size, sha256=hashlib.sha256(raw).hexdigest()))
    return rows


def compare_live(directory, runtime, disc):
    rows = {}
    raw = {}
    for name, pc in LIVE_STOPS:
        source = directory / f'{name}.json'
        data = source.read_bytes()
        capture = json.loads(data)
        meta = capture['metadata']
        require((meta['version'], meta['serial'], meta['uuid'].lower(), meta['status']) ==
                ('PCSX2 v2.8.2', 'SCPS-15046', '91aa4b28', 1), f'{name}: wrong emulator or status')
        regions = {int(at, 16): bytes.fromhex(value) for at, value in capture['regions'].items()}
        raw[name] = regions
        rows[name] = dict(pc=f'{pc:08x}', vblank=int.from_bytes(regions[0x95e300], 'little'),
                          sample_sha256=hashlib.sha256(data).hexdigest(),
                          debugger_sha256=hashlib.sha256(source.with_suffix('.png').read_bytes()).hexdigest())
        require(len(regions[0x3eace0]) == 40 and regions[0x3eace0][12] == 0 and
                regions[0x3f8e20][16:20] == b'\0\0\3\0' and
                regions[0x3f8e20][36:40] == b'\xc1\0\0\0',
                f'{name}: title worker is unfinished or has a different descriptor')
        require(regions[0x3f4bbc] == runtime['publication']['title_slot'].to_bytes(4, 'little'),
                f'{name}: title publication differs')
        for domain, (at, size) in zip(runtime['publication']['domains'], LIVE_REGIONS):
            sample = regions[at]
            require(len(sample) == size and hashlib.sha256(sample).hexdigest() == domain['sha256'],
                    f'{name}: published region {at:#x} differs')

    create = raw['live-create']
    activate = raw['live-dialogue-init']
    voice = raw['live-voice']
    glyph = raw['live-glyph']
    reset = raw['live-reset']
    end = raw['live-presentation-done']
    require(create[0x3eaeb0][0] == 1 and activate[0x3eaeb0][0] == 0 and
            activate[TITLE_TASK][5:7] == b'\1\0' and voice[TITLE_TASK][5:7] == b'\1\1' and
            glyph[TITLE_TASK][5:7] == b'\1\1' and reset[TITLE_TASK][5:7] == b'\2\0' and
            reset[0x3eaeb0][0] == 2 and end[MODE][0x1a:0x1c] == b'\1\0' and
            int.from_bytes(end[TITLE_TASK][8:12], 'little') == 0xa886e0,
            'native actor, fade or teardown checkpoint differs')
    native_activation = rows['live-dialogue-init']['vblank'] - rows['live-create']['vblank']
    same_frame_voice = rows['live-dialogue-init']['vblank'] == rows['live-voice']['vblank']
    require(native_activation == runtime['events']['activation'] and same_frame_voice and
            runtime['events']['voice_start'] == runtime['events']['activation'],
            'activation/voice ordering differs')
    # s0 at the original first draw JAL is STATE+0x38. Its delay slot sets
    # a0 to the bytes following this display token header.
    display = glyph[D.STATE]
    token = 0x38
    start = token + 2 + display[token + 1]
    length = display.index(0, start) - start
    text_sha256 = hashlib.sha256(display[start:start + length]).hexdigest()
    require(runtime['first_glyph']['text_sha256'] == text_sha256, 'first glyph request differs')
    identity = json.loads((directory / 'live-identity.json').read_text())
    count = identity['resident_prefix_bytes']
    require(count >= 0x1ddb74 - 0x100000 and
            identity['runtime_prefix_sha256'] == hashlib.sha256(disc.elf[0x800:0x800 + count]).hexdigest() and
            identity['title_mapped_prefix_sha256'] ==
            hashlib.sha256(disc.iso.read('/AREA0/A0_B09.BIN')[:0x2d40]).hexdigest(),
            'native code image identity differs')
    reset_after_glyph = rows['live-reset']['vblank'] - rows['live-glyph']['vblank']
    if runtime['skip_frame'] is None:
        events = runtime['events']
        require(events['dialogue_reset'] - events['first_glyph'] == reset_after_glyph,
                'the neutral dialogue reset interval after the first glyph differs')
    return dict(stops=rows, resident_prefix_bytes=count, activation_vblanks=native_activation,
                glyph_vblanks_after_activation=rows['live-glyph']['vblank'] - rows['live-voice']['vblank'],
                glyph_text_sha256=text_sha256, reset_vblanks_after_glyph=reset_after_glyph,
                publication_matches=True)


def check_controls(disc, folder):
    source = (ROOT / 'src/scene/title_scene.c').read_text()
    results = {}
    for name, (anchor, replacement) in CONTROL_EDITS.items():
        require(source.count(anchor) == 1, f'ambiguous control {name}')
        with tempfile.TemporaryDirectory(prefix='scene-control-', dir=folder) as scratch:
            path = Path(scratch)
            mutant = path / 'title_scene.c'
            mutant.write_text(source.replace(anchor, replacement))
            # The source is built with the original include directory on the path.
            header = path / 'title_scene.h'
            header.write_text((ROOT / 'src/scene/title_scene.h').read_text().replace(
                '../hgscript/title.h', 'hgscript/title.h'))
            lib = build(path / 'test.so', mutant)
            try:
                simulate(lib, disc, skip=100, until_reset=True)
            except D.RuntimeMismatch as exc:
                results[name] = str(exc)
            else:
                raise ValueError(f'undetected scene defect {name}')
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iso', type=Path, required=True)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--skip', type=int)
    parser.add_argument('--advance-interval', type=int)
    parser.add_argument('--progress', action='store_true')
    endpoint = parser.add_mutually_exclusive_group()
    endpoint.add_argument('--until-reset', action='store_true')
    endpoint.add_argument('--new-game', action='store_true')
    parser.add_argument('--live', type=Path)
    parser.add_argument('--area-native', type=Path)
    parser.add_argument('--negative-controls', action='store_true')
    args = parser.parse_args()
    if args.advance_interval is not None and (args.skip is None or args.advance_interval < 1):
        parser.error('--advance-interval requires --skip and a positive interval')
    if args.area_native and not args.new_game:
        parser.error('--area-native requires --new-game')
    lib = build(args.library)
    with args.iso.open('rb') as stream:
        disc = RetailDisc(stream)
        result = dict(schema='hg-m21-title-scene/v1', iso_sha256=ORIGINAL_ISO, elf_sha256=ORIGINAL_ELF,
                      ranges=original_ranges(disc, args.new_game))
        result.update(simulate(lib, disc, skip=args.skip, until_reset=args.until_reset,
                               new_game=args.new_game, advance_interval=args.advance_interval,
                               progress=args.progress))
        if args.area_native:
            result['area']['native'] = compare_native_area(result['area'], args.area_native)
        if args.live:
            result['live'] = compare_live(args.live, result, disc)
        if args.negative_controls:
            result['controls'] = check_controls(disc, args.library.parent)
    write_receipt(args.output, result)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
