#!/usr/bin/env python3
"""Run M18 lifecycle with carried M19 resource state and authenticated ISO DMA."""
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import struct
import zlib

from hg_identify import elf32, va_to_offset
import run_front_end_native as N
from resource_execution import BUSY, ORIGINAL_ELF, ORIGINAL_ISO, RetailDisc, ResourceScheduler, TASK, TASK_ACTIVE
from verify_engine_runtime import audio_resource_contract

OVERLAY = 0xa87800
RESOURCE_RANGES = (
    (0x3eace0, 0x3ead30), (0x3eaf88, 0x3eaf8c), (0x3f3a30, 0x3f4300),
    (0x3f4300, 0x3f4d60), (0x3f6d80, 0x3f6dd0), (TASK, TASK + 0x80),
    (0x3f9fe0, 0x3f9fe4), (0x3f9fef, 0x3f9ff0), (0x46e10c, 0x46e10d),
    (0x46e130, 0x46e138), (OVERLAY, OVERLAY + 0x100),
)
KERNEL = {0x103d60, 0x103d80, 0x103da0, 0x104e10}
AUDIO_RESOURCE = 0x156a50
SCENE_RESET = 0x141b10
SCENE_RESET_END = 0x141c98
INJECT = {
    name: tuple(i for i in range(size) if not N.in_owned(base + i) and
                not any(lo <= base + i < hi for lo, hi in RESOURCE_RANGES))
    for name, base, size in N.REGIONS
}


def capture(native, name):
    base = native / 'full'
    rec = json.loads((base / (name + '.json')).read_text())
    meta = rec['metadata']
    if (meta != rec['metadata_after'] or meta['version'] != 'PCSX2 v2.8.2' or
            meta['serial'] != 'SCPS-15046' or meta['uuid'].lower() != '91aa4b28' or meta['status'] != 1):
        raise ValueError(f'{name}: capture is not a paused original-build snapshot')
    buffers = []
    for region, size in (('ee', 0x2000000), ('sp', 0x4000)):
        data = zlib.decompress((base / (name + '.' + region + '.z')).read_bytes())
        if len(data) != size or hashlib.sha256(data).hexdigest() != rec[region + '_sha256']:
            raise ValueError(f'{name}: {region} snapshot hash differs')
        buffers.append(data)
    return rec['seq'], N.Mem(*buffers)


def record_value(rec, address, width):
    return int.from_bytes(N.rec_read(rec, address, width), 'little')


def compare_operations(run, start, end):
    """Compare sampled request-to-exit order and logical DMA arguments.

    Start/finish clocks and physical chunk division are deliberately left out
    of this domain. The C trace is collected during execution independently.
    """
    original = []
    old = run.frames[start]
    current = None
    for seq in range(start + 1, end + 1):
        row = run.frames[seq]
        busy = record_value(row, BUSY, 1)
        if busy and not record_value(old, BUSY, 1):
            current = dict(first=seq, thread=record_value(row, TASK + 0x34, 4),
                           request=(record_value(row, TASK + 0x32, 2), record_value(row, TASK + 0x44, 4)),
                           setups=[], last=None)
            original.append(current)
        if current and busy:
            args = N.rec_read(row, 0x3eacf8, 16)
            if args != N.rec_read(old, 0x3eacf8, 16):
                current['setups'].append(tuple(struct.unpack('<4I', args)))
        elif current:
            current['last'] = seq
            current['finished'] = (record_value(row, TASK + 0x32, 2), record_value(row, TASK + 0x44, 4))
            current['active'] = record_value(row, TASK_ACTIVE, 2)
            current = None
        old = row
    model = run.scheduler.requests
    finished = {job['thread']: (job['kind'], job['directory']) for job in run.scheduler.finished}
    matched = []
    for observed, request in zip(original, model):
        thread = request['thread']
        order = (request['kind'], request['directory']) == observed['request']
        identity = thread == observed['thread']
        ended = ((finished.get(thread) == observed.get('finished') and observed.get('active') == 0)
                 if observed['last'] is not None else None)
        setups = run.setups.get(thread, [])
        same = setups == observed['setups'] if observed['last'] is not None else None
        matched.append(dict(original=observed, request_matches=order, thread_matches=identity, completion_matches=ended,
                            setup_matches=same, model_thread=thread, model_setups=setups))
    same_count = len(original) == len(model)
    checked = [row for row in matched if row['original']['last'] is not None]
    passed = same_count and all(row['request_matches'] and row['thread_matches'] for row in matched) and all(
        row['completion_matches'] and row['setup_matches'] for row in checked)
    return dict(passed=passed, sampled_requests=len(original), model_requests=len(model),
                compared_completions=len(checked), operations=matched)


class ConnectedRunner(N.Runner):
    def __init__(self, lib, memory, frames, seq, disc, first_thread=1):
        super().__init__(lib, memory, frames, seq)
        self.scheduler = ResourceScheduler(lib, self.env, memory, disc.read, first_thread=first_thread,
                                           latency=lambda _lba, count: 1 + count // 48)
        self.lib.hg_resource_rebase.argtypes = [C.POINTER(N.L.Env), C.c_uint32, C.c_uint32]
        self.lib.hg_resource_rebase.restype = None
        self.lib.hg_resource_map_address.argtypes = [C.POINTER(N.L.Env), C.c_uint32, C.c_uint32]
        self.lib.hg_resource_map_address.restype = C.c_uint32
        self.slice = 'dispatch'
        self.audio_calls = []
        self.setups = {}
        self.requests_at = {}
        self.releases = []
        length = SCENE_RESET_END - SCENE_RESET
        offset = va_to_offset(elf32(disc.elf), SCENE_RESET, length)
        if memory.read(SCENE_RESET, length) != disc.elf[offset:offset + length]:
            raise ValueError('guest scene reset differs from authenticated original text')

    def _store(self, user, addr, width, value):
        super()._store(user, addr, width, value)
        if addr == 0x3ead04:
            thread = self.scheduler.thread
            setup = tuple(self.scheduler.load(at) for at in range(0x3eacf8, 0x3ead08, 4))
            history = self.setups.setdefault(thread, [])
            if not history or history[-1] != setup:
                history.append(setup)

    def route(self, pc, a, b, c, d):
        if pc in KERNEL:
            self.note(pc)
            if pc == 0x104e10 and self.slice == 'priority':
                return 0  # Existing priority worker is outside the normal loader model.
            if pc == 0x103d80:
                self.requests_at[a] = self.seq + 1
            return self.scheduler.service(pc, a, b)
        if pc == N.L.HG_WAIT_INTERRUPT:
            self.slice = 'interrupt'
            for _ in range(self.vblanks):
                self.slice = 'interrupt'
                self.scheduler.tick()
                self.lib.hg_vblank_start_handler(C.byref(self.env))
                self.slice = 'priority'
                self.lib.hg_vblank_end_handler(C.byref(self.env))
            self.vblanks = 0
            self.slice = 'dispatch'
            return 0
        if pc == AUDIO_RESOURCE:
            self.note(pc)
            size = self.scheduler.load(a)
            if not 0 < size <= 1024 * 1024:
                raise ValueError('unexpected sound container size')
            changed, end = audio_resource_contract(self.mem.read(a, size), a)
            self.mem.write(a, changed)
            self.audio_calls.append((self.scheduler.clock, a, end))
            return end
        if pc == 0x1385b0:
            self.lib.hg_resource_rebase(C.byref(self.env), a, b)
            return 0
        if pc == 0x1389d0:
            return self.lib.hg_resource_map_address(C.byref(self.env), a, 0x1ffd000)
        if pc == SCENE_RESET:
            old = self.scheduler.load(0x3f9fef, 1)

            def memset(cpu):
                addr, value, size = (cpu.reg[i] & 0xffffffff for i in range(4, 7))
                return self.route(0x119ac8, addr, value, size, 0)

            result = self.interp(pc, (), {0x119ac8: memset, 0x11ad58: lambda _: 0})
            self.releases.append(dict(seq=self.seq, retained_before=old,
                                      retained_after=self.scheduler.load(0x3f9fef, 1)))
            return result
        return super().route(pc, a, b, c, d)

    def inject(self, cur, nxt):
        for name, base, size in N.REGIONS:
            data = nxt[name]
            current = bytearray(self.mem.read(base, size))
            for i in INJECT[name]:
                current[i] = data[i]
            self.mem.write(base, current)
        if N.mode_of(cur)[:3] == (0x121be0, 1, 1):
            self.mem.write(0x3ead4a, N.rec_read(nxt, 0x3ead4a, 2))

    def unit(self):
        cur, nxt = self.frames[self.seq], self.frames[self.seq + 1]
        self.inject(cur, nxt)
        self.vblanks = (record_value(nxt, 0x95e300, 4) - record_value(cur, 0x95e300, 4)) & 0xffffffff
        if self.vblanks > 8:
            raise ValueError(f'unexpected vblank delta {self.vblanks}')
        # In these three source contexts the M18 runner models scene-owned
        # fade setters. Deliver the recovered input once, before dispatch.
        # A connected worker can run during dispatch, so never retry a unit.
        mode = N.mode_of(cur)
        actual = self.mem.read(0x3ead34, 7)
        setter = self.external_fade(cur, nxt)
        if setter and actual == N.rec_read(cur, 0x3ead34, 7) and mode[:3] in N.EXTERNAL_FADE:
            r = C.c_uint32()
            for pc, args in setter:
                self.lib.hg_front_end_call(C.byref(self.env), pc, *args, C.byref(r))
            self.injections.append(dict(seq=self.seq + 1,
                                        calls=[[f'{pc:08x}', list(args)] for pc, args in setter]))
        self.lib.hg_main_unit(C.byref(self.env))
        self.seq += 1
        return self.compare(nxt)

    def compare_task(self, nxt):
        differences = []
        for name, addr, size in (
            ('loader flags', 0x3eace0, 16), ('read context', 0x3eacf0, 44),
            ('task', TASK + 0x30, 0x20), ('overlay prefix', OVERLAY, 0x100),
        ):
            got = self.mem.read(addr, size)
            expected = N.rec_read(nxt, addr, size)
            if got != expected:
                differences.append(dict(domain=name, address=hex(addr), native=expected.hex(), c=got.hex()))
        return differences


def write_receipt(output, result):
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x') as fp:
            json.dump(result, fp, indent=2)
            fp.write('\n')


CHECKPOINTS = (
    (13061, 's1-first-active', '/AREA1/A1_B00.BIN'),
    (14125, 's3-title-menu-ready', '/AREA0/A0_B09.BIN'),
    (18622, 's3-first-active-again', '/AREA1/A1_B00.BIN'),
)


def compare_checkpoint(memory, native, disc, name, path):
    seq, expected = capture(native, name)
    overlay = disc.iso.read(path)
    if overlay != expected.read(OVERLAY, len(overlay)):
        raise ValueError(f'{name}: captured AREA overlay differs from the ISO')
    rows = []
    for domain, start, length in (
        ('overlay', OVERLAY, len(overlay)), ('arena', 0x3f4d3c, 36),
        ('handles', 0x3f4300, 2600), ('completed task', TASK + 0x30, 0x20),
    ):
        want, got = expected.read(start, length), memory.read(start, length)
        mismatch = [f'{start + i:08x}' for i, (a, b) in enumerate(zip(want, got)) if a != b]
        rows.append(dict(domain=domain, address=f'{start:08x}', bytes=length,
                         differing_bytes=len(mismatch), first_differences=mismatch[:16],
                         original_sha256=hashlib.sha256(want).hexdigest(),
                         model_sha256=hashlib.sha256(got).hexdigest()))
    return dict(capture=name, seq=seq, area=path, domains=rows)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--native', type=Path, required=True)
    p.add_argument('--iso', type=Path, required=True)
    p.add_argument('--start', default='title-menu-input-ready')
    p.add_argument('--frames', type=int)
    p.add_argument('--stop-on-diff', type=int, default=0)
    p.add_argument('--output', type=Path)
    p.add_argument('--library', type=Path, default=N.ROOT / 'local/m19/connected.so')
    a = p.parse_args()
    with a.iso.open('rb') as stream:
        disc = RetailDisc(stream)
        frames_path = a.native / 'frames.jsonl'
        frames_digest = hashlib.sha256(frames_path.read_bytes()).hexdigest()
        pinned = json.loads((N.ROOT / 'evidence/m18/checks.json').read_text())['identities']
        if frames_digest != pinned['native_stop_records_sha256']:
            raise ValueError('M18 stop record digest differs')
        frames = N.load_frames(a.native)
        start_seq, mem = capture(a.native, a.start)
        if (start_seq not in frames or mem.read(0x3eac00, 0x500) != frames[start_seq]['gp'] or
                mem.read(BUSY, 1) != b'\0' or mem.read(TASK_ACTIVE, 2) != b'\0\0'):
            raise ValueError('start must be a captured idle loader frame')
        first_thread = int.from_bytes(mem.read(TASK + 0x34, 4), 'little') + 1
        run = ConnectedRunner(N.F.build(a.library), mem, frames, start_seq, disc, first_thread)
        failures, transitions, checkpoints = [], [], []
        wanted = {seq: (name, path) for seq, name, path in CHECKPOINTS}
        units, task_matches, lifecycle_matches = 0, 0, 0
        prev = N.mode_of(frames[start_seq])[:3]
        for seq in range(start_seq, min(start_seq + a.frames, max(frames)) if a.frames else max(frames)):
            c_diffs = run.unit()
            task_diffs = run.compare_task(frames[seq + 1])
            units += 1
            lifecycle_matches += not c_diffs
            task_matches += not task_diffs
            if (c_diffs or task_diffs) and len(failures) < max(12, a.stop_on_diff):
                failures.append(dict(seq=seq + 1, lifecycle=c_diffs, resource=task_diffs))
            mode = N.mode_of({'gp': bytes(run.mem.read(0x3eac00, 0x500))})[:3]
            if mode != prev:
                transitions.append((seq + 1, mode))
                prev = mode
            if seq + 1 in wanted:
                checkpoint = compare_checkpoint(mem, a.native, disc, *wanted[seq + 1])
                if checkpoint['seq'] != seq + 1:
                    raise ValueError('M18 checkpoint is from the wrong frame')
                checkpoints.append(checkpoint)
            if run.faults or run.scheduler.fault or (a.stop_on_diff and len(failures) >= a.stop_on_diff):
                break
        operations = compare_operations(run, start_seq, run.seq)
        checkpoint_matches = all(not domain['differing_bytes']
                                 for cp in checkpoints for domain in cp['domains'])
        passed = not (run.faults or run.scheduler.fault or not operations['passed'] or
                      not checkpoint_matches or a.stop_on_diff and failures)
        result = dict(schema='hg-m19-connected-lifecycle/v1', start=a.start, first=start_seq, last=run.seq,
                      iso_sha256=ORIGINAL_ISO, elf_sha256=ORIGINAL_ELF, native_sha256=frames_digest,
                      optical_clock='1 + sectors // 48 vblank ticks', units=units, lifecycle_matches=lifecycle_matches,
                      task_matches=task_matches, transitions=transitions, differences=failures,
                      requests=run.scheduler.requests, finished=run.scheduler.finished, setups=run.setups,
                      requests_at=run.requests_at, scene_resets=run.releases,
                      disc_reads=run.scheduler.reads, resource_audio_calls=run.audio_calls,
                      original_operations=operations, checkpoints=checkpoints, result='passed' if passed else 'failed',
                      interpreted={f'{pc:08x}': n for pc, n in sorted(run.interp_counts.items())},
                      external_fades=run.injections, faults=run.faults, disc_fault=run.scheduler.fault)
        write_receipt(a.output, result)
        print(f'{start_seq}..{run.seq}: {lifecycle_matches}/{units} lifecycle and {task_matches}/{units} loader matches')
        print(f'{len(run.scheduler.requests)} requests, {len(run.scheduler.finished)} completed, '
              f'{len(run.scheduler.reads)} reads')
        print(f'Original request/completion/setup comparison: {"passed" if operations["passed"] else "failed"}')
        print(f'Original full checkpoints: {len(checkpoints)} checked, {"passed" if checkpoint_matches else "failed"}')
        for fault in run.faults[:5]:
            print(f'fault: {fault}')
        if run.scheduler.fault:
            print(f'disc fault: {run.scheduler.fault}')
        return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
