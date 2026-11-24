#!/usr/bin/env python3
"""Verify the AREA1 request-to-coordinator interval against two original stops."""
import argparse
import ctypes as C
import hashlib
from pathlib import Path

from hg_identify import elf32, va_to_offset
from resource_execution import BUSY, ORIGINAL_ELF, ORIGINAL_ISO, TASK, TASK_ACTIVE, RetailDisc
from run_resource_lifecycle import ConnectedRunner, N, OVERLAY, capture, write_receipt

MODE = 0x3ead30
TITLE_HANDLE = 0x3f4bbc
AREA1 = '/AREA1/A1_B00.BIN'
REGIONS = (
    ('handles', 0x3f4300, 2600), ('arena', 0x3f4d3c, 36),
    ('worker descriptor', TASK + 0x30, 0x20),
    ('loader flags', BUSY, 1), ('area selector', 0x3fa1b0, 4),
    ('mode handler and phase', MODE, 0xc),
)


class AreaEntry(ConnectedRunner):
    def __init__(self, lib, mem, disc, thread):
        super().__init__(lib, mem, {}, 0, disc, first_thread=thread)
        self.coordinator = False

    def route(self, pc, a, b, c, d):
        if pc == 0x12f850:
            self.coordinator = True
            return 0
        if pc in (0x12bf70, 0x12c3d0):
            return self.interp(pc, (a, b, c, d))
        return super().route(pc, a, b, c, d)


def verify(disc, native, lib):
    _, before = capture(native, 'area1-request')
    _, after = capture(native, 'area1-active')
    count = 0x1f9000
    offset = va_to_offset(elf32(disc.elf), 0x100000, count)
    for mem in (before, after):
        if mem.read(0x100000, count) != disc.elf[offset:offset + count]:
            raise ValueError('AREA1 stop has different resident code')
    title = disc.iso.read('/AREA0/A0_B09.BIN')
    area = disc.iso.read(AREA1)
    if (before.read(MODE + 4, 7) != b'\xc0\xd1\x12\0\5\5\2'
            or before.read(MODE + 1, 1) != b'\1'
            or before.read(TITLE_HANDLE, 4) != bytes.fromhex('00156601')
            or before.read(BUSY, 1) != b'\0'
            or before.read(OVERLAY, len(title)) != title
            or after.read(MODE + 4, 7) != b'\xc0\xd1\x12\0\5\1\1'
            or after.read(OVERLAY, len(area)) != area):
        raise ValueError('stops do not bracket the AREA1 normal worker call')

    # The first sample is at 00130150(2, c6), with area-load subphase 2
    # already stored by its caller. No guest frame is copied after this.
    thread = int.from_bytes(before.read(TASK + 0x34, 4), 'little') + 1
    run = AreaEntry(lib, N.Mem(before.ee, before.sp), disc, thread)
    lib.hg_fade_service.argtypes = [C.POINTER(N.L.Env)]
    lib.hg_fade_service.restype = None
    run.route(0x130150, 2, 0xc6, 0, 0)
    for unit in range(1200):
        run.mem.write(0x95e568, bytes(2))
        run.route(0x143240, 0, 0, 0, 0)
        if run.faults or run.scheduler.fault:
            raise ValueError(f'AREA1 execution failed {run.faults} {run.scheduler.fault}')
        if run.coordinator:
            break
        lib.hg_fade_service(C.byref(run.env))
        run.vblanks = 1
        run.route(N.L.HG_WAIT_INTERRUPT, 0, 0, 0, 0)
        run.seq += 1
    else:
        raise ValueError('AREA1 never reached the coordinator')
    if run.scheduler.load(TITLE_HANDLE) or run.scheduler.load(TASK_ACTIVE, 2) or run.scheduler.thread:
        raise ValueError('AREA1 coordinator ran with the title borrow or unfinished worker')

    domains = []
    for name, address, size in (*REGIONS, ('overlay', OVERLAY, len(area))):
        want, got = after.read(address, size), run.mem.read(address, size)
        if want != got:
            i = next(i for i in range(size) if want[i] != got[i])
            raise ValueError(f'{name}: differs at {address + i:08x}, EE {want[i]:02x}, C {got[i]:02x}')
        domains.append(dict(domain=name, address=f'{address:08x}', bytes=size,
                            sha256=hashlib.sha256(got).hexdigest()))
    return dict(schema='hg-m21-area-entry/v1', iso_sha256=ORIGINAL_ISO, elf_sha256=ORIGINAL_ELF,
                native_start_pc='00130150', native_end_pc='0012f850',
                native_ee_sha256={name: hashlib.sha256(mem.ee).hexdigest()
                                  for name, mem in (('request', before), ('active', after))},
                original_vblank_delta=int.from_bytes(after.read(0x95e300, 4), 'little') -
                int.from_bytes(before.read(0x95e300, 4), 'little'),
                driver_units=unit + 1, requests=run.scheduler.requests,
                completed=run.scheduler.finished, reads=len(run.scheduler.reads), domains=domains)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iso', required=True, type=Path)
    parser.add_argument('--native', required=True, type=Path)
    parser.add_argument('--library', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    library = N.F.build(args.library)
    with args.iso.open('rb') as stream:
        result = verify(RetailDisc(stream), args.native, library)
    write_receipt(args.output, result)
    print(f'AREA1 coordinator reached after {result["driver_units"]} units. '
          f'{len(result["domains"])} native checkpoint domains matched.')


if __name__ == '__main__':
    main()
