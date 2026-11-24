#!/usr/bin/env python3
"""Verify cold M19 title publication and the connected M20 dialogue slice."""
import argparse
from collections import Counter
import ctypes as C
import hashlib
import json
from pathlib import Path

from hg_content.flk5 import inspect_flk5
from hg_content.hgscript import decode, encode
from hg_content.pack import Pack
from hg_identify import SECTOR, read_at
import hgscript_differential as D
from run_resource_lifecycle import N, RetailDisc, capture, write_receipt
from verify_resource_retail import (
    ColdBootRunner, blank_guest, boot_to_title, finish, initialize_file_records, invoke,
)

HANDLE_CELL = 0x003f4bbc
FADE_ACTIVE = 0x003eaeb0
SCENE = 0x01ff8000  # Isolated object fixture for the original caller's s0.
TITLE_OVERLAY = 0x00a87800
CAPTURE_HASH = '648137359e80208843ed163b1ec219f1d5f9500a51f4d8b8d34301c6525a6674'


class TitleBus(D.Bus):
    def __init__(self, memory):
        super().__init__(b'')
        self.mem = memory

    def load(self, address, size):
        self.check(address, size)
        return int.from_bytes(self.mem.read(address, size), 'little')

    def store(self, address, value, size):
        self.check(address, size)
        self.mem.write(address, (value % (1 << (8 * size))).to_bytes(size, 'little'))
        self.dirty.update(n for n in range(address, address + size)
                          if not D.SP - 0x2000 <= n < D.SP)

    def external(self, entry, a0, a1, a2, a3, f12=0, f13=0):
        result = super().external(entry, a0, a1, a2, a3, f12, f13)
        if entry in (0x1564a0, 0x156550):
            # The resident request producers update the real guest queue cells.
            # The async service that consumes them is supplied by the fixture.
            result, _ = self.original(entry, (a0, a1, a2, a3),
                                      fp=(f12, f13), unhook=entry)
        return result


class Title(C.Structure):
    _fields_ = [('parent', D.U32)]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def resource_env(callbacks):
    return N.L.Env(None, callbacks.load, callbacks.store, callbacks.call, None, None)


def bind_title(lib):
    args = [C.POINTER(N.L.Env), D.CALL_FP, C.POINTER(Title), D.U32]
    lib.hg_title_script_start.argtypes = args
    lib.hg_title_script_start.restype = C.c_int
    lib.hg_title_script_frame.argtypes = args
    lib.hg_title_script_frame.restype = C.c_int32
    lib.hg_title_script_release.argtypes = [C.POINTER(N.L.Env), C.POINTER(Title)]
    lib.hg_title_script_release.restype = None


def compare_title(lib, original, port, parent, child):
    title = Title()
    phases, counts = Counter(), Counter()
    trace = hashlib.sha256()
    request_frames = []
    for bus in (original, port):
        bus.store(FADE_ACTIVE, 0, 1)
        bus.store(SCENE + 6, 0, 1)
    # Compare the actual AREA0 overlay branch, from the fade guard to its
    # common continuation. This uses original 00138370 and 001ccd80.
    _, executed = original.original(0xa88c28, (), registers={16: SCENE}, stop=0xa88d30)
    callbacks = port.env()
    env = resource_env(callbacks)
    require(lib.hg_title_script_start(C.byref(env), callbacks.call_fp, C.byref(title), SCENE),
            'title adapter refused the completed publication')
    require(title.parent == parent and port.load(D.STATE + 0xc, 4) == child + port.load(child + 8, 4),
            'title adapter initialized from a different resource')
    require(not D.compare(original, port, 'original title caller'),
            'title activation unexpectedly called a service')

    # A ready edge follows the start request. A scheduled idle edge models
    # natural voice completion. No device, decoder or SPU2 is used.
    ready_frame = None
    for frame in range(5000):
        for bus in (original, port):
            bus.store(D.PAD_EDGE, 0, 2)
            if ready_frame is not None and frame == ready_frame:
                bus.store(D.VOICE_STATE, 3, 1)
            elif ready_frame is not None and frame == ready_frame + 199:
                bus.store(D.VOICE_STATE, 0, 1)
        _, words = original.original(0x25c1a0, (), stop=0x25c200)
        executed.update(words)
        callbacks = port.env()
        env = resource_env(callbacks)
        phase = lib.hg_title_script_frame(C.byref(env), callbacks.call_fp, C.byref(title), D.SP)
        if phase != D.sx(original.load(D.STATE, 4), 32):
            raise D.RuntimeMismatch(f'title frame {frame}: phase return differs')
        calls = D.compare(original, port, f'title frame {frame}')
        phases[phase] += 1
        for call in calls:
            counts[hex(call[0])] += 1
            if call[0] != 0x182850:
                request_frames.append([frame, *call])
            if call[0] == 0x1564a0:
                require(ready_frame is None, 'unexpected second voice request in title record 0')
                ready_frame = frame + 1
        if calls:
            trace.update(json.dumps([frame, calls], separators=(',', ':')).encode())
        if phase == -1:
            break
    else:
        raise ValueError('title sequence did not terminate')
    require(ready_frame is not None and counts['0x182850'], 'title did not exercise voice and glyph requests')

    _, words = original.original(0x1cccd0, (D.STATE, 0))
    executed.update(words)
    callbacks = port.env()
    env = resource_env(callbacks)
    lib.hg_title_script_release(C.byref(env), C.byref(title))
    release = D.compare(original, port, 'title dialogue reset')
    require(release == [(0x156550, 1)] and title.parent == 0, 'title release did not reset the borrow')
    require(lib.hg_title_script_frame(C.byref(env), callbacks.call_fp, C.byref(title), D.SP) == -3,
            'title frame accepted a released borrow')
    return dict(frames=frame + 1, phases=phases, requests=counts, audio_request_frames=request_frames,
                request_trace_sha256=trace.hexdigest(), unique_original_addresses=len(executed),
                voice_ready_frame=ready_frame, voice_idle_frame=ready_frame + 199, reset_requests=release)


def verify(disc, native, lib):
    mem = blank_guest(disc)
    initialize_file_records(mem, disc, [f'/AREA0/A0_B{i:02}.BIN' for i in range(10)] + ['/AREA1/A1_B00.BIN'])
    run = ColdBootRunner(lib, mem, {}, 0, disc)
    mem.write(0x700000c0, (0x3ead30).to_bytes(4, 'little'))
    mem.write(0x3ead30, b'\1\0\0\0\x10\x18\x12\0')
    lib.hg_boot_mode.argtypes = [C.POINTER(N.L.Env)]
    lib.hg_boot_mode.restype = None
    require(run.scheduler.load(HANDLE_CELL) == 0, 'title handle was already present before boot')
    lib.hg_boot_mode(C.byref(run.env))
    finish(run)
    boot = boot_to_title(run, disc)
    require(all(not row['destination'] <= SCENE + 6 < row['destination'] + row['sectors'] * SECTOR
                for row in run.scheduler.reads), 'isolated title scene cell overlaps loaded data')
    finished = run.scheduler.finished[-1]
    require((finished['kind'], finished['directory']) == (3, 193), 'worker did not complete PACK 193')

    pack = Pack(disc.stream, disc.iso, disc.pack, read_at)
    member = next(row for row in pack.all_members() if (row.directory, row.index) == (193, 7))
    data = pack.read_member(member)
    entries = inspect_flk5(data, len(data))['entries']
    offset = entries[18]['encoded_offset']
    end = entries[19]['encoded_offset']
    extracted = data[offset:end]
    require(entries[18]['type'] == 12 and encode(decode(extracted)) == extracted,
            'extracted child is not a losslessly decoded type-12 collection')

    # Use the saved original only as a publication checkpoint, never as
    # input to either side of the connected runtime comparison.
    seq, saved = capture(native, 'title-menu-idle')
    require(seq == 913 and hashlib.sha256(saved.ee).hexdigest() == CAPTURE_HASH,
            'saved title checkpoint identity differs')
    parent = run.scheduler.load(HANDLE_CELL)
    child = parent + offset
    require(parent == 0x1661500 and child == 0x172af00, 'published parent or child placement differs')
    header_size = 16 + 4 * len(entries)
    require(mem.read(parent, header_size) == saved.read(parent, header_size)
            and mem.read(parent + 8, 4) == (0x1e00).to_bytes(4, 'little'),
            'fixed title parent header differs')
    require(mem.read(child, len(extracted)) == saved.read(child, len(extracted)) == extracted,
            'published child differs from original disc or saved RAM')
    require(mem.read(0x3f4d3c, 36) == saved.read(0x3f4d3c, 36), 'title arena differs')
    require(mem.read(TITLE_OVERLAY, boot['overlay_bytes']) == saved.read(TITLE_OVERLAY, boot['overlay_bytes']),
            'title overlay differs from the saved original')

    original = TitleBus(N.Mem(mem.ee, mem.sp))
    port = TitleBus(mem)
    require(original.original(0x138370, (parent, 0x0c000000))[0] == child,
            'original type-12 lookup resolved a different child')
    runtime = compare_title(lib, original, port, parent, child)
    require(mem.read(child, len(extracted)) == extracted, 'script modified its borrowed resource')
    # The gameplay entry supplies the AREA1 selector before submitting c6.
    mem.write(0x3fa1b0, b'\1\0\0')
    mem.write(0x700000c4, b'\2\0\0\0')
    invoke(run, 2, 0xc6)
    require(run.scheduler.load(HANDLE_CELL) == 0, 'next AREA request retained the title handle')
    area = disc.iso.read('/AREA1/A1_B00.BIN')
    require(mem.read(TITLE_OVERLAY, len(area)) == area, 'requested AREA1 overlay differs from disc')
    return dict(schema='hg-title-script-integration/v1', boot=boot, publication=finished,
                original_capture_seq=seq, original_capture_ee_sha256=CAPTURE_HASH,
                handle=0x22f, parent=parent, parent_header_bytes=header_size,
                child=child, child_size=len(extracted), child_offset=offset,
                child_sha256=hashlib.sha256(extracted).hexdigest(),
                child_rebuild_exact=True, runtime=runtime,
                cleanup={'handle_cleared': True, 'area1_overlay_matches': True})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iso', required=True, type=Path)
    parser.add_argument('--native', required=True, type=Path)
    parser.add_argument('--library', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    lib = N.F.build(args.library)
    bind_title(lib)
    with args.iso.open('rb') as stream:
        disc = RetailDisc(stream)
        result = verify(disc, args.native, lib)
    write_receipt(args.output, result)
    print(f'Connected title: {result["runtime"]["frames"]} frames, {dict(result["runtime"]["requests"])}')


if __name__ == '__main__':
    main()
