#!/usr/bin/env python3
"""Verify the M21 AREA1 ferry reload (PACK 0x69) against paired original stops.

Promoted unchanged in behaviour from the M21 owner-local probe so the runner
can register it. The C run starts at the authenticated request stop with the
previous AREA1 resources and selector present, carries the PACK 0x69 job,
answers the no-save prompt (002cfa90) once, delivers one vblank per unit and
stops at the first coordinator call (0012f850). The seven loader/scene
domains are then compared with the later original stop.
"""
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path

import verify_scene_area_entry as base
from hg_identify import elf32, va_to_offset
from resource_execution import BUSY, ORIGINAL_ELF, ORIGINAL_ISO, TASK, TASK_ACTIVE

NO_SAVE_PROMPT = 0x2cfa90
SELECTOR = 0x3fa1b0
RESIDENT = (0x100000, 0x1f9000)


def require(condition, message):
    if not condition:
        raise ValueError(message)


class FerryEntry(base.AreaEntry):
    def __init__(self, lib, mem, disc, thread):
        super().__init__(lib, mem, disc, thread)
        self.no_save_responses = 0

    def route(self, pc, a, b, c, d):
        if pc == NO_SAVE_PROMPT:
            self.no_save_responses += 1
            return 1
        return super().route(pc, a, b, c, d)


def verify(disc, native, lib, start_name, end_name, begin_job, unit_limit=1200):
    _, before = base.capture(native, start_name)
    _, after = base.capture(native, end_name)
    start, length = RESIDENT
    offset = va_to_offset(elf32(disc.elf), start, length)
    overlay = disc.iso.read(base.AREA1)
    for name, mem in ((start_name, before), (end_name, after)):
        require(mem.read(start, length) == disc.elf[offset:offset + length],
                f'{name}: resident ELF image differs')
        require(mem.read(base.OVERLAY, len(overlay)) == overlay, f'{name}: AREA1 overlay differs')
    first = int.from_bytes(before.read(TASK + 0x34, 4), 'little') + 1
    require(after.read(base.MODE + 4, 7) == bytes.fromhex('c0d11200050101'), 'Wrong final mode bytes')
    run = FerryEntry(lib, base.N.Mem(before.ee, before.sp), disc, first)
    run.lib.hg_fade_service.argtypes = [C.POINTER(base.N.L.Env)]
    run.lib.hg_fade_service.restype = None
    if begin_job:
        # Original 0012e074 stores subphase 1 in 00130150's call delay slot.
        require(before.read(base.MODE + 4, 7) == bytes.fromhex('c0d11200050601'), 'Wrong request mode bytes')
        require(not run.scheduler.load(BUSY, 1), 'Loader busy at the request stop')
        run.route(0x130150, 1, 0x69, 0, 0)
    else:
        require(before.read(base.MODE + 4, 7) == bytes.fromhex('c0d11200050602'), 'Wrong resume mode bytes')
    for unit in range(unit_limit):
        run.mem.write(0x95e568, bytes(2))
        run.route(0x143240, 0, 0, 0, 0)
        require(not (run.faults or run.scheduler.fault), f'{run.faults} {run.scheduler.fault}')
        if run.coordinator:
            break
        run.lib.hg_fade_service(C.byref(run.env))
        run.vblanks = 1
        run.route(base.N.L.HG_WAIT_INTERRUPT, 0, 0, 0, 0)
        run.seq += 1
    else:
        raise ValueError('coordinator not reached')
    require(run.no_save_responses == 1, 'No-save prompt was not answered exactly once')
    require(not run.scheduler.load(BUSY, 1) and not run.scheduler.load(TASK_ACTIVE, 2),
            'Coordinator ran with an unfinished worker')
    expected = [(1, 0x69)] if begin_job else []
    require([(r['kind'], r['directory']) for r in run.scheduler.requests] == expected, 'Unexpected loader requests')
    require(len(run.scheduler.finished) == len(expected), 'Unexpected completed jobs')
    require(run.mem.read(SELECTOR, 4) == bytes.fromhex('01000100'), 'Ferry selector differs')
    domains = {}
    for name, at, size in (*base.REGIONS, ('overlay', base.OVERLAY, len(overlay))):
        original, computed = after.read(at, size), run.mem.read(at, size)
        differences = [f'{at + i:08x}' for i, (want, have) in enumerate(zip(original, computed)) if want != have]
        domains[name] = dict(match=not differences, diff=differences, sha256=hashlib.sha256(computed).hexdigest())
    return dict(native_start=start_name, native_end=end_name, units=unit + 1,
                native_vblanks=int.from_bytes(after.read(0x95e300, 4), 'little') -
                int.from_bytes(before.read(0x95e300, 4), 'little'),
                overlay_ee_sha256=hashlib.sha256(overlay).hexdigest(),
                native_ee_sha256={k: hashlib.sha256(m.ee).hexdigest() for k, m in (('before', before), ('after', after))},
                operations=run.scheduler.requests, reads=run.scheduler.reads,
                completed=run.scheduler.finished, confirmations=run.no_save_responses, domains=domains)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iso', required=True, type=Path)
    parser.add_argument('--native', required=True, type=Path, help='M21 native directory containing full/')
    parser.add_argument('--library', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--begin-job', action='store_true')
    args = parser.parse_args()
    library = base.N.F.build(args.library)
    with args.iso.open('rb') as stream:
        result = verify(base.RetailDisc(stream), args.native, library, args.start, args.end, args.begin_job)
    result = dict(schema='hg-m21-ferry-reload/v1', iso_sha256=ORIGINAL_ISO, elf_sha256=ORIGINAL_ELF, **result)
    base.write_receipt(args.output, result)
    matched = sum(row['match'] for row in result['domains'].values())
    print(f'Ferry reload reached the coordinator after {result["units"]} units; '
          f'{matched}/{len(result["domains"])} domains matched.')
    return 0 if matched == len(result['domains']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
