#!/usr/bin/env python3
"""Independent M4/M7 checkpoints for the resumable worker and its DMA backend."""
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path

from hg_identify import elf32
from hg_evidence_safety import digest
from resource_execution import BUSY, ORIGINAL_ELF, ORIGINAL_ISO, TASK, TASK_ACTIVE, initialize_file_records
from run_resource_lifecycle import (
    AUDIO_RESOURCE, ConnectedRunner, N, OVERLAY, RetailDisc, write_receipt,
)
from verify_engine_runtime import get_bytes
from verify_fixup_runtime import samples


def blank_guest(disc):
    mem = N.Mem(bytes(0x2000000), bytes(0x4000))
    for seg in elf32(disc.elf)['pt_load']:
        if seg['filesz']:
            mem.write(seg['vaddr'], disc.elf[seg['offset']:seg['offset'] + seg['filesz']])
    return mem


def saved(path, bound):
    if digest(path) != bound:
        raise ValueError(f'{path.name}: checkpoint digest differs')
    return samples(path)[1]


def equal(mem, expected, address, length, name):
    actual = mem.read(address, length)
    want = get_bytes(expected, address, length)
    if actual != want:
        offset = next(i for i, (x, y) in enumerate(zip(actual, want)) if x != y)
        raise AssertionError(f'{name}: first difference at {address+offset:08x}, '
                             f'got {actual[offset]:02x}, expected {want[offset]:02x}')
    return dict(domain=name, address=f'{address:08x}', bytes=length,
                sha256=hashlib.sha256(actual).hexdigest())


def finish(run):
    for _ in range(2000):
        if not run.scheduler.thread:
            break
        run.vblanks = 1
        run.route(N.L.HG_WAIT_INTERRUPT, 0, 0, 0, 0)
        if run.faults or run.scheduler.fault:
            break
    if run.faults or run.scheduler.fault or run.scheduler.thread:
        raise AssertionError(f'worker did not complete: {run.faults}, {run.scheduler.fault}')
    if run.scheduler.load(BUSY, 1) or run.scheduler.load(TASK_ACTIVE, 2):
        raise AssertionError('worker returned while busy or active')


def invoke(run, kind, ident):
    run.route(0x130150, kind, ident, 0, 0)
    finish(run)


class ColdBootRunner(ConnectedRunner):
    def route(self, pc, a, b, c, d):
        if pc == 0x2cdef0:
            # Synthetic dismissal of the boot prompt. Card calls retain the
            # disconnected-card behavior and no persistent storage is opened.
            return 1
        if pc == 0x1219e0:
            pending, self.pending = self.pending, {}
            fn = self.lib.hg_logo_sequence_step
            fn.argtypes = [C.POINTER(N.L.Env)] + [C.c_uint32] * 5
            fn.restype = C.c_uint32
            return fn(C.byref(self.env), a, b, c, d, pending.get('t0', 0))
        if pc == AUDIO_RESOURCE and self.scheduler.load(a + 0xc) != 1:
            # The logo PACK's two-bank container uses the existing Audio
            # registration model through its public harness. Only its
            # resource bytes and endpoint cross the loader boundary.
            from hg_audio.audio_guest import AudioHarness
            size = self.scheduler.load(a)
            if not 0 < size <= 1024 * 1024:
                raise ValueError('unexpected boot audio container size')
            audio = AudioHarness()
            endpoint = audio.register_resource(self.mem.read(a, size), a)
            self.mem.write(a, audio.mem.read_blob(a, size))
            self.audio_calls.append((self.scheduler.clock, a, endpoint))
            return endpoint
        return super().route(pc, a, b, c, d)


def boot_to_title(run, disc):
    """Drive real boot and title dispatch through the first menu entry."""
    for _ in range(1500):
        mode = run.mem.read(0x3ead34, 6)
        if mode == b'\xe0\x1b\x12\0\1\0':
            break
        run.route(0x143240, 0, 0, 0, 0)
        run.route(0x139240, 0, 0, 0, 0)
        run.vblanks = 1
        run.route(N.L.HG_WAIT_INTERRUPT, 0, 0, 0, 0)
        run.seq += 1
        if run.faults or run.scheduler.fault:
            raise AssertionError((run.faults, run.scheduler.fault))
    else:
        raise AssertionError(f'cold lifecycle did not reach the title menu: mode {mode.hex()}, '
                             f'fade {run.mem.read(0x3eaeb0, 8).hex()}, requests {run.requests}')
    title = disc.iso.read('/AREA0/A0_B09.BIN')
    if run.mem.read(OVERLAY, len(title)) != title:
        raise AssertionError('cold title overlay differs from original disc')
    expected = [(0, 0), (1, 0x6a), (0, 0x6d), (1, 0x54), (1, 1), (1, 4),
                (1, 0x50), (1, 7), (2, 0xc0)]
    actual = [(r['kind'], r['directory']) for r in run.scheduler.requests]
    if actual != expected or len(run.scheduler.finished) != len(expected):
        raise AssertionError(f'cold lifecycle request order differs: {actual}')
    return dict(requests=actual, frames=run.seq, completed=len(run.scheduler.finished),
                overlay_bytes=len(title), overlay_sha256=hashlib.sha256(title).hexdigest())


def verify_bootstrap(lib, disc, folder):
    pins = json.loads((N.ROOT / 'evidence/m4/runtime-comparison.json').read_text())['binding']['inputs']
    outputs = {name: saved(folder / f'{name}.json', pins[f'{name}.json'])
               for name in ('post-fixup', 'post-bootstrap', 'exit-thread-stepped')}
    mem = blank_guest(disc)
    area_paths = [f'/AREA0/A0_B{i:02}.BIN' for i in range(10)] + ['/AREA1/A1_B00.BIN']
    initialize_file_records(mem, disc, area_paths)
    run = ColdBootRunner(lib, mem, {}, 0, disc)
    # Request comes from original boot mode 00121810 on its initial phase.
    lib.hg_boot_mode.argtypes = [C.POINTER(N.L.Env)]
    lib.hg_boot_mode.restype = None
    mem.write(0x700000c0, (0x3ead30).to_bytes(4, 'little'))
    mem.write(0x3ead30, b'\1\0\0\0\x10\x18\x12\0')
    lib.hg_boot_mode(C.byref(run.env))
    finish(run)
    checks = [
        equal(mem, outputs['post-fixup'], 0xb00000, 4352, 'M4 fixed bootstrap member'),
        equal(mem, outputs['post-bootstrap'], 0x3f4300, 20, 'M4 five bootstrap handles'),
        equal(mem, outputs['post-bootstrap'], 0x3f4d3c, 36, 'M4 bootstrap arena'),
        equal(mem, outputs['exit-thread-stepped'], BUSY, 1, 'M4 completion busy'),
        equal(mem, outputs['exit-thread-stepped'], TASK_ACTIVE, 2, 'M4 completion active'),
    ]
    if [(r['kind'], r['directory']) for r in run.scheduler.requests] != [(0, 0)]:
        raise AssertionError('boot mode did not issue directory 0')
    expected = [(disc.pack['lba'], 1, 0x3f3b00), (disc.pack['lba'] + 273, 170, 0xb00000)]
    actual = [(r['lba'], r['sectors'], r['destination']) for r in run.scheduler.reads]
    if expected != actual:
        raise AssertionError(f'bootstrap transfer trace differs: {actual}')
    bootstrap = dict(checks=checks, reads=run.scheduler.reads.copy(), worker=run.scheduler.finished[0])
    return bootstrap, boot_to_title(run, disc)


def verify_area(lib, disc, folder):
    rows = json.loads((N.ROOT / 'evidence/m7/observations.json').read_text())['observations']
    observed = {r['capture_id']: r['capture_sha256'] for r in rows}
    before = saved(folder / 'c6-entry.json', observed['c6-entry'])
    output = saved(folder / 'engine-output.json', observed['engine-output'])
    pins = json.loads((N.ROOT / 'evidence/m7/runtime-comparison.json').read_text())['binding']['inputs']
    end = saved(folder / 'stop28.json', pins['captures/stop28.json'])
    mem = blank_guest(disc)
    for address, data in before.items():
        if address >= 0x3dff00:  # Resident code is supplied only by the authenticated ELF.
            mem.write(address, data)
    if mem.read(TASK + 0x30, 4) != b'\1\0\2\0' or mem.read(TASK + 0x44, 4) != b'\xc6\0\0\0':
        raise ValueError('Unexpected M7 kind-2 worker-entry state')
    tid = int.from_bytes(mem.read(TASK + 0x34, 4), 'little')
    mem.write(TASK_ACTIVE, bytes(2))
    mem.write(BUSY, bytes(1))
    run = ConnectedRunner(lib, mem, {}, 0, disc, tid)
    invoke(run, 2, 0xc6)
    checks = [
        equal(mem, end, OVERLAY, 30720, 'M7 complete AREA1 overlay'),
        equal(mem, end, 0x3f4300, 2600, 'M7 handle table'),
        equal(mem, end, 0x3f4d3c, 36, 'M7 arena publication'),
        equal(mem, end, TASK, 80, 'M7 completed task descriptor'),
        equal(mem, end, 0x3f3b00, 2048, 'M7 final PACK header'),
        equal(mem, end, BUSY, 1, 'M7 completion busy'),
        equal(mem, end, 0x3eace0, 1, 'M7 bulk-ready publication'),
        equal(mem, end, 0x46e10c, 1, 'M7 retained-ready publication'),
    ]
    # Compare the entire surviving C7 and downstream read ranges; C6 content
    # may have been overwritten by later operations in this same request.
    for label, at, size in (('PACKc7', 0x1270000, 4784128),
                            ('PACK6b audio', 0x1009000, 6144),
                            ('PACK4c', 0x100a800, 1016 * 2048),
                            ('PACK85', 0xf78000, 23 * 2048)):
        checks.append(equal(mem, output, at, size, f'M7 {label} final bytes'))
    header_lbas = [r['lba'] - disc.pack['lba'] for r in run.scheduler.reads if r['destination'] == 0x3f3b00]
    if header_lbas != [0xc6, 5, 0xc7, 0x6b, 0x4c, 0x85]:
        raise AssertionError(f'M7 directory sequence differs: {header_lbas}')
    before_reuse = len(run.scheduler.reads)
    invoke(run, 8, 0)
    if len(run.scheduler.reads) != before_reuse:
        raise AssertionError('cached retained preparation issued another read')
    if run.scheduler.load(0x3eace0, 1) != 0:
        raise AssertionError('retained request did not clear the original bulk-ready flag')
    for check in checks:
        if check['domain'] in ('M7 completed task descriptor', 'M7 bulk-ready publication'):
            continue
        at = int(check['address'], 16)
        if digest_bytes(mem.read(at, check['bytes'])) != check['sha256']:
            raise AssertionError(f'retained request changed {check["domain"]}')
    return dict(checks=checks, reads=run.scheduler.reads, workers=run.scheduler.finished,
                directories=header_lbas, retention_reads=0)


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--iso', type=Path, required=True)
    p.add_argument('--m4', type=Path, required=True)
    p.add_argument('--m7', type=Path, required=True, help='M7 captures directory')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--library', type=Path, help='existing strict host build, for defect controls')
    a = p.parse_args()
    lib = C.CDLL(str(a.library)) if a.library else N.F.build(a.output.with_suffix('.so'))
    with a.iso.open('rb') as stream:
        disc = RetailDisc(stream)
        bootstrap, title = verify_bootstrap(lib, disc, a.m4)
        result = dict(schema='hg-m19-retail-checkpoints/v1', iso_sha256=ORIGINAL_ISO,
                      elf_sha256=ORIGINAL_ELF, bootstrap=bootstrap, cold_title=title,
                      area=verify_area(lib, disc, a.m7), result='passed')
    write_receipt(a.output, result)
    print(f'PASS: {len(result["bootstrap"]["checks"])} M4 and {len(result["area"]["checks"])} M7 checkpoint comparisons')


if __name__ == '__main__':
    main()
