#!/usr/bin/env python3
"""M17: C lifecycle paths versus native M7/M8 runtime observations.

Conditions (private captures under local/m7, local/m8; all are partial,
sequential running reads of selected regions, not atomic snapshots):

  pause-entry     --entry frame: M8 pad-read-entry (mode 0012d1c0, (5,1,1,0),
                  pad 0) -> pause-transition ((5,1,2,0), pad 0x0800 Start).
                  Not frame-adjacent (gameplay tick +0x24), so only the C
                  active-play frame 0012e700 runs once, with the post-pause
                  captured pad word as the input; the coordinator 0012f850 is
                  an external and only the lifecycle-owned pause edge is
                  compared.
  pause-retained  --entry mode: pause-transition -> pause-retained, frame
                  adjacent (tick +1). The full C gameplay mode 0012d1c0 runs
                  one frame (major-5 dispatch, pause sub state, the gp+2f80
                  tick and the gp+2f7c accumulator); the pause menu 00162750 is
                  an external returning 0 (still open).
  area-pack       --entry pack: M7 c6-entry, the loader entry of the area-1
                  PACK request. C 0012dbd0(1) on the captured triple and area
                  tables must return the directory in the captured worker
                  request word 003f8e44.

The fade request 00139040 executes its original ELF instructions in the
interpreter; other callees are recorded. Every byte the C path writes and
every mode-block byte is compared with the later capture. A read outside the
captured regions fails the check.

    python3 -B tools/verify_lifecycle_native.py --elf "$HG_ELF" --entry frame \
        --before local/m8/captures/pad-read-entry.json \
        --after local/m8/captures/pause-transition.json --negative-controls --output <fresh>.json
"""
import argparse
import ctypes as C
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lifecycle_differential import (CALL, Env, LOAD, STORE, HG_CALL_FP_REGS, HG_CALL_T_REGS,
                                    HG_DOMAIN_FAULT, Script, build, elf_image, run_original)

MODE = 0x3ead30
GP = 0x3e7f70
PAD = GP + 0x2f94
FADE_SETTER = (0x139040, 0x1390a8)
# Cells compared whether or not the C path writes them (a missing write must
# be visible): frame -> fade request bytes; mode -> also the frame tick and
# accumulator (the pause-retained pair is frame-adjacent). In the mode pair
# the fade bytes are advanced by the per-frame fade service outside the mode
# handler (gp+2f44 0 -> 8, 700000d4 1 -> 2), so they are not lifecycle-owned
# there and are not guarded.
GUARDED = {'frame': [*range(0x3eaeb0, 0x3eaeb8), *range(0x700000d4, 0x700000d8)],
           'mode': [*range(0x3eaeec, 0x3eaef4), *range(0x700000c4, 0x700000c8)],
           'pack': []}
ROOT = Path(__file__).resolve().parents[1]
PAUSE = """        set8(e,0xa,2);set8(e,0xb,0);set8(e,0xc,0);w16(e,blk(e)+0x1c,0);
        call(e,0x1404f0,0,0,0,0);
        call(e,0x156d50,0x3ca3d70au,0,2,0);
        call(e,0x156ce0,0,0,0,0);
        sound(e,0x56);
        call(e,0x139040,8,0,1,0);"""
START = "if((pad&0x800u) || r8(e,0x95e44a)!=1)return 1;"
CONTROLS = {'frame': (
    ('pause-wrong-sub', PAUSE, PAUSE.replace("set8(e,0xa,2)", "set8(e,0xa,3)")),
    ('pause-fade-mode-0', PAUSE, PAUSE.replace("call(e,0x139040,8,0,1,0)", "call(e,0x139040,8,0,0,0)")),
    ('pause-fade-time-6', PAUSE, PAUSE.replace("call(e,0x139040,8,0,1,0)", "call(e,0x139040,6,0,1,0)")),
    ('start-bit-ignored', START, "if(r8(e,0x95e44a)!=1)return 1;"),
), 'mode': (
    ('tick-skipped-in-pause', '    if(!le)w32(e,G(0x2f7c),0);\n    w32(e,G(0x2f80),r32(e,G(0x2f80))+1u);',
     '    if(!le)w32(e,G(0x2f7c),0);'),
    ('accumulator-step-two', 'hg_ee_fp(0,r32(e,G(0x2f7c)),0x3f800000u,&fault)',
     'hg_ee_fp(0,r32(e,G(0x2f7c)),0x40000000u,&fault)'),
), 'pack': (
    ('pack-retained-offset', 'return retained?v+0x93u:', 'return retained?v+0x94u:'),
    ('pack-base-from-stride-table', 'base=r32(e,G(-0x7e00)+area)', 'base=r32(e,G(-0x7df0)+area)'),
)}


def negative_controls(entry, argv_base):
    import subprocess
    source = (ROOT / 'src/game_lifecycle.c').read_text()
    folder = ROOT / 'local/m17/controls'
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, old, new in CONTROLS[entry]:
        if source.count(old) != 1:
            raise SystemExit(f'{name}: anchor not unique')
        else:
            text = source.replace(old, new)
        path = folder / f'native-{name}.c'
        path.write_text(text)
        r = subprocess.run([sys.executable, '-B', __file__, *argv_base, '--source', str(path),
                            '--build', str(folder / f'native-{name}.so')], capture_output=True, text=True)
        rows.append(dict(control=name, source_sha256=hashlib.sha256(text.encode()).hexdigest(),
                         rejected=r.returncode == 1, returncode=r.returncode))
    return rows


class Regions:
    def __init__(self, path):
        data = json.loads(Path(path).read_text())
        self.sha256 = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        self.timestamp = data.get('timestamp_utc')
        self.segments = []
        for s in data['samples']:
            raw = bytes.fromhex(s['bytes'])
            if hashlib.sha256(raw).hexdigest() != s['sha256']:
                raise ValueError(f'sample digest mismatch in {path}')
            self.segments.append((int(s['address'], 16), raw))

    def byte(self, address):
        for base, raw in self.segments:
            if base <= address < base + len(raw):
                return raw[address - base]
        return None

    def read(self, address, n):
        out = [self.byte(address + i) for i in range(n)]
        return None if None in out else bytes(out)


class PartialBus:
    """Byte bus over captured regions; code words come from the ELF."""
    def __init__(self, regions, code):
        self.regions, self.code = regions, code
        self.overlay, self.uncaptured, self.events = {}, set(), []

    def _byte(self, address):
        if address in self.overlay:
            return self.overlay[address]
        if 0x100000 <= address < 0x100000 + len(self.code) and address < 0x3d0000:
            return self.code[address - 0x100000]
        v = self.regions.byte(address)
        if v is None:
            self.uncaptured.add(address)
            return 0
        return v

    def get(self, address, size):
        return bytes(self._byte(address + i) for i in range(size))

    peek = get

    def put(self, address, data, log=True):
        for i, x in enumerate(data):
            self.overlay[address + i] = x
            if log:
                self.events.append(('w', address + i, x))

    def __getitem__(self, address):
        return self._byte(address)

    def __setitem__(self, address, value):
        self.put(address, bytes([value]))

    def update(self, pairs):
        for address, value in pairs:
            self[address] = value

    def load(self, address, width=4):
        return int.from_bytes(self.get(address, width), 'little')

    def store(self, address, value, width=4):
        self.put(address, (value & ((1 << (width * 8)) - 1)).to_bytes(width, 'little'))


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--elf', type=Path, required=True)
    p.add_argument('--entry', choices=('frame', 'mode', 'pack'), required=True)
    p.add_argument('--before', type=Path, required=True)
    p.add_argument('--after', type=Path, required=True)
    p.add_argument('--expect', type=lambda x: int(x, 16), default=0x3f8e44,
                   help='pack: native request word address (hex)')
    p.add_argument('--source', type=Path, help='alternate src/game_lifecycle.c (negative controls)')
    p.add_argument('--build', type=Path, help=argparse.SUPPRESS)
    p.add_argument('--negative-controls', action='store_true')
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    if a.output and a.output.exists():
        raise FileExistsError(a.output)
    code = elf_image(a.elf)
    before, after = Regions(a.before), Regions(a.after)
    lib = build(output=a.build, source=a.source)
    bus = PartialBus(before, code)
    pad = after.read(PAD, 2)
    if a.entry != 'pack':
        bus.put(PAD, pad, log=False)      # the native input of the transition
    calls, pending = [], {}
    for name, restype in (('hg_gameplay_event_flags', C.c_uint32), ('hg_gameplay_frame', None),
                          ('hg_gameplay_major5', None), ('hg_gameplay_mode', None)):
        getattr(lib, name).argtypes = [C.POINTER(Env)]
        getattr(lib, name).restype = restype
    lib.hg_area_pack_id.argtypes = [C.POINTER(Env), C.c_uint32]
    lib.hg_area_pack_id.restype = C.c_uint32
    routed = {0x12fa00: lib.hg_gameplay_event_flags}
    if a.entry == 'mode':
        routed.update({0x12d9a0: lib.hg_gameplay_major5, 0x12e700: lib.hg_gameplay_frame})
    errors = []

    def load(_, address, width):
        return bus.load(address, width)

    def store(_, address, width, value):
        bus.store(address, value, width)

    def call(_, pc, a0, a1, a2, a3):
        try:
            if pc in (HG_CALL_T_REGS, HG_CALL_FP_REGS):
                pending[pc] = (a0, a1, a2)
                return 0
            if pc == HG_DOMAIN_FAULT:
                errors.append(f'domain fault {a0:#x}')
                return 0
            if pc in routed:
                calls.append(dict(pc=f'{pc:08x}', kind='C'))
                return routed[pc](C.byref(env)) or 0
            row = dict(pc=f'{pc:08x}', args=[f'{x:#x}' for x in (a0, a1, a2, a3)])
            if pc == FADE_SETTER[0]:
                run_original(code, FADE_SETTER[0], FADE_SETTER[1], bus, Script(), args=(a0, a1, a2, a3))
                row['kind'] = 'original instructions'
            else:
                row['kind'] = 'external (recorded)'
            for seam in list(pending):
                row['seam' if seam == HG_CALL_T_REGS else 'fp'] = [f'{x:#x}' for x in pending.pop(seam)]
            calls.append(row)
            return 0
        except Exception as exc:        # noqa: BLE001 - reported as a failure
            errors.append(f'{pc:#x}: {exc!r}')
            return 0

    env = Env(None, LOAD(load), STORE(store), CALL(call), None, None)
    pre = before.read(MODE, 0x28)
    native_value = c_value = None
    if a.entry == 'frame':
        lib.hg_gameplay_frame(C.byref(env))
    elif a.entry == 'mode':
        lib.hg_gameplay_mode(C.byref(env))
    else:
        c_value = lib.hg_area_pack_id(C.byref(env), 1)
        native_value = int.from_bytes(after.read(a.expect, 4), 'little')
        if c_value != native_value:
            errors.append(f'pack directory C {c_value:#x} != native request {native_value:#x}')
        for address in range(0x3e0170, 0x3e01a8):   # captured tables equal the ELF data words
            if before.byte(address) is None or before.byte(address) != code[address - 0x100000]:
                errors.append(f'table byte {address:08x} not captured or differs from the ELF')
    written = sorted({e[1] for e in bus.events})
    compared = sorted(set(written) | set(range(MODE, MODE + 0x28)) | set(GUARDED[a.entry]))
    rows, mismatches, unobserved = [], [], []
    for address in compared:
        c = bus._byte(address)
        n = after.byte(address)
        if n is None:
            unobserved.append(f'{address:08x}')
            continue
        rows.append((address, c, n))
        if c != n:
            mismatches.append(dict(address=f'{address:08x}', c=c, native=n, before=before.byte(address)))
    changed_by_c = [f'{x:08x}' for x in written if before.byte(x) != bus._byte(x)]
    result = dict(schema='hg-m17-lifecycle-native/v1', entry=a.entry,
                  pack=dict(c=c_value, native=native_value, address=f'{a.expect:08x}') if a.entry == 'pack' else None,
                  before=dict(sha256=before.sha256, timestamp_utc=before.timestamp,
                              mode=pre.hex(), state=list(pre[8:12])),
                  after=dict(sha256=after.sha256, timestamp_utc=after.timestamp,
                             mode=after.read(MODE, 0x28).hex(), state=list(after.read(MODE, 0x28)[8:12])),
                  input_pad=pad.hex() if pad else None, elf_sha256=hashlib.sha256(a.elf.read_bytes()).hexdigest(),
                  calls=calls, errors=errors,
                  uncaptured_reads=[f'{x:08x}' for x in sorted(bus.uncaptured)],
                  c_written_bytes=len(written), c_changed_bytes=changed_by_c,
                  compared_bytes=len(rows), mismatches=mismatches, unobserved_written=unobserved,
                  limitations=['Partial, sequential running reads of selected regions; not atomic.',
                               'Externals (coordinator 0012f850, pause menu 00162750, sound, render) are '
                               'recorded, not compared; only lifecycle-owned bytes are compared.'])
    result['passed'] = (not errors and not bus.uncaptured and not mismatches
                        and (bool(changed_by_c) or a.entry == 'pack'))
    if a.negative_controls:
        result['negative_controls'] = negative_controls(a.entry, [
            '--elf', str(a.elf), '--entry', a.entry, '--before', str(a.before), '--after', str(a.after),
            '--expect', f'{a.expect:x}'])
        result['passed'] &= all(r['rejected'] for r in result['negative_controls'])
    text = json.dumps(result, indent=1) + '\n'
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.open('x').write(text)
    print(json.dumps({k: result[k] for k in ('entry', 'pack', 'errors', 'uncaptured_reads', 'c_changed_bytes',
                                             'compared_bytes', 'mismatches', 'unobserved_written', 'passed')}
                     | {k: result[k] for k in ('negative_controls',) if k in result},
                     indent=1))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
