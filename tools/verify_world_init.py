#!/usr/bin/env python3
"""M23 reusable state-provenance harness and authentic checkpoint inspector.

Supports two strictly separated modes:
  1. --synthetic: compiles and runs the original C synthetic world-init model
     and provenance harness (src/scene/world_init.c + existing C interfaces) on
     one shared 32 MiB EE RAM + 16 KiB scratchpad guest bus with zero retail
     inputs. Records ordered init calls, provenance classifications, sustained
     controller-driven progression, reset/reinit parity, and 10 negative
     controls.
  2. --inspect-authentic: read-only owner-local analysis against authenticated
     SCPS-15046 ELF/ISO and existing M21/M13 captures. Verifies the ELF spawn
     table (0030f3c0), 00158560 jump table (003db540), area1-request ->
     area1-active -> obs3-000 domain states, and proves that passing
     area1-active directly to C hg_player_dispatch (00158560 state-1) trips
     HG_DOMAIN_FAULT(0x30). Never mutates captures or claims synthetic == retail.
"""
import argparse
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'tests')]

from hg_identify import elf32, va_to_offset
from resource_execution import ORIGINAL_ELF, ORIGINAL_ISO

EXPECTED_INIT_ORDER = (
    0x001431f0,  # hg_resource_init
    0x00130900,  # hg_pack_general_prepare/step (0x3f player FLK5)
    0x00130900,  # hg_pack_general_prepare/step (0x102 area FLK5 + overlay + clear 0x22f)
    0x00139140,  # hg_music_fade_1
    0x0025c0f0,  # timeline/event reset
    0x00182430,  # effect service reset
    0x00141b10,  # world env / guard reset
    0x00141910,  # player/camera/actor/node pool reset
    0x00141210,  # spawn selection
    0x0013fef0,  # world environment & progress init
    0x0012bf70,  # camera struct init
    0x0013a4c0,  # area map resource bind
    0x00189aa0,  # area entity/actor spawn
    0x00156cf0,  # audio resume seam (untouched M24 boundary)
    0x001390b0,  # hg_fade_in
    0x0012f850,  # first coordinator entry
    0x00158a10,  # hg_player_initialize (state 0)
    0x001e3440,  # initial floor snap
    0x0012c790,  # hg_camera_update
    0x001430a0,  # hg_actor_update_main
    0x00143150,  # hg_actor_update_type2
)

C_SOURCES = (
    'src/scene/world_init.c',
    'src/front_end.c',
    'src/game_lifecycle.c',
    'src/resource_loader.c',
    'src/disc_read.c',
)


class HGCallRecord(C.Structure):
    _fields_ = [
        ('frame', C.c_uint32),
        ('entry_pc', C.c_uint32),
        ('a0', C.c_uint32),
        ('a1', C.c_uint32),
        ('provenance', C.c_int),
        ('stage', C.c_uint32),
    ]


class HGProvenanceRecord(C.Structure):
    _fields_ = [
        ('label', C.c_char * 48),
        ('guest_addr', C.c_uint32),
        ('size', C.c_uint32),
        ('value_u32', C.c_uint32),
        ('provenance', C.c_int),
        ('producer_pc', C.c_uint32),
        ('frame', C.c_uint32),
    ]


def build_shared_library(output: Path, compiler: str) -> C.CDLL:
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        compiler,
        '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-pedantic',
        '-ffp-contract=off', '-fPIC', '-shared', '-Wl,--no-undefined',
        '-I', str(ROOT / 'src'),
        *[str(ROOT / src) for src in C_SOURCES],
        '-lm', '-o', str(output),
    ]
    subprocess.run(cmd, cwd=ROOT, check=True, timeout=60)
    return C.CDLL(str(output))


def build_harness_binary(output: Path, compiler: str, sanitize: bool = False) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    flags = ['-std=c11', '-Wall', '-Wextra', '-Werror', '-pedantic', '-ffp-contract=off']
    if sanitize:
        flags += ['-g', '-O1', '-fno-omit-frame-pointer',
                  '-fsanitize=address,undefined', '-fno-sanitize-recover=all']
    else:
        flags += ['-O2']
    cmd = [
        compiler, *flags, '-I', str(ROOT / 'src'),
        str(ROOT / 'tests/world_init_harness.c'),
        *[str(ROOT / src) for src in C_SOURCES],
        '-lm', '-o', str(output),
    ]
    subprocess.run(cmd, cwd=ROOT, check=True, timeout=60)
    return output


def verify_synthetic(compiler: str = 'cc', sanitize: bool = False,
                     build_dir: Path | None = None) -> dict:
    base = build_dir or Path(os.environ.get('HG_VERIFY_BUILD_DIR', ROOT / 'local/build'))
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='m23-world-init-', dir=base) as tmp:
        tmp_path = Path(tmp)
        binary = build_harness_binary(tmp_path / 'world_init_harness', compiler, sanitize=sanitize)
        env = dict(os.environ)
        if sanitize:
            env['ASAN_OPTIONS'] = 'detect_leaks=1:halt_on_error=1'
            env['UBSAN_OPTIONS'] = 'halt_on_error=1'
        proc = subprocess.run([str(binary), '--json'], capture_output=True, text=True,
                              env=env, check=True, timeout=30)
        report = json.loads(proc.stdout)

        # Also inspect the ordered cold-to-active call chain & provenance ledger via ctypes.
        lib = build_shared_library(tmp_path / 'world_init.so', compiler)
        lib.hg_world_harness_create.argtypes = [C.c_void_p, C.c_void_p]
        lib.hg_world_harness_create.restype = C.c_void_p
        for fn in ('cold_boot', 'publish_resources', 'enter_world', 'teardown'):
            getattr(lib, f'hg_world_harness_{fn}').argtypes = [C.c_void_p]
            getattr(lib, f'hg_world_harness_{fn}').restype = C.c_int
        lib.hg_world_harness_call_count.argtypes = [C.c_void_p]
        lib.hg_world_harness_call_count.restype = C.c_size_t
        lib.hg_world_harness_call_at.argtypes = [C.c_void_p, C.c_size_t]
        lib.hg_world_harness_call_at.restype = C.POINTER(HGCallRecord)
        lib.hg_world_harness_provenance_count.argtypes = [C.c_void_p]
        lib.hg_world_harness_provenance_count.restype = C.c_size_t
        lib.hg_world_harness_provenance_at.argtypes = [C.c_void_p, C.c_size_t]
        lib.hg_world_harness_provenance_at.restype = C.POINTER(HGProvenanceRecord)
        lib.hg_world_provenance_name.argtypes = [C.c_int]
        lib.hg_world_provenance_name.restype = C.c_char_p
        lib.hg_world_harness_destroy.argtypes = [C.c_void_p]

        h = lib.hg_world_harness_create(None, None)
        try:
            assert lib.hg_world_harness_cold_boot(h) == 0
            assert lib.hg_world_harness_publish_resources(h) == 0
            assert lib.hg_world_harness_enter_world(h) == 0

            calls = []
            for i in range(lib.hg_world_harness_call_count(h)):
                rec = lib.hg_world_harness_call_at(h, i).contents
                calls.append({
                    'index': i,
                    'stage': rec.stage,
                    'pc': f'{rec.entry_pc:08x}',
                    'a0': f'{rec.a0:08x}',
                    'a1': f'{rec.a1:08x}',
                    'provenance': lib.hg_world_provenance_name(rec.provenance).decode(),
                })

            # Verify EXPECTED_INIT_ORDER appears as an ordered subsequence.
            pcs = [int(c['pc'], 16) for c in calls]
            cursor = 0
            for expected_pc in EXPECTED_INIT_ORDER:
                try:
                    found = pcs.index(expected_pc, cursor)
                except ValueError as exc:
                    raise AssertionError(
                        f'Missing expected init call {expected_pc:08x} after index {cursor}'
                    ) from exc
                cursor = found + 1

            provenance_entries = []
            for i in range(lib.hg_world_harness_provenance_count(h)):
                pr = lib.hg_world_harness_provenance_at(h, i).contents
                provenance_entries.append({
                    'label': pr.label.decode('ascii', errors='replace'),
                    'address': f'{pr.guest_addr:08x}',
                    'size': pr.size,
                    'value': f'{pr.value_u32:08x}',
                    'provenance': lib.hg_world_provenance_name(pr.provenance).decode(),
                    'producer_pc': f'{pr.producer_pc:08x}',
                })
        finally:
            lib.hg_world_harness_destroy(h)

    report['ordered_init_chain'] = [f'{pc:08x}' for pc in EXPECTED_INIT_ORDER]
    report['cold_entry_calls'] = calls
    report['cold_entry_provenance'] = provenance_entries
    report['compiler'] = compiler
    report['sanitized'] = sanitize
    return report


def _load_m21_capture(native_dir: Path, name: str):
    full = native_dir / 'full'
    meta = json.loads((full / f'{name}.json').read_text())
    ee = zlib.decompress((full / f'{name}.ee.z').read_bytes())
    sp = zlib.decompress((full / f'{name}.sp.z').read_bytes())
    if hashlib.sha256(ee).hexdigest() != meta['ee_sha256']:
        raise ValueError(f'{name}: EE RAM digest mismatch')
    if hashlib.sha256(sp).hexdigest() != meta['sp_sha256']:
        raise ValueError(f'{name}: scratchpad digest mismatch')
    if meta['metadata']['serial'] != 'SCPS-15046' or meta['metadata']['uuid'].lower() != '91aa4b28':
        raise ValueError(f'{name}: wrong retail identity')
    return meta, ee, sp


def _r32(buf: bytes, addr: int) -> int:
    return struct.unpack_from('<I', buf, addr)[0]


def _rf32(buf: bytes, addr: int) -> float:
    return struct.unpack_from('<f', buf, addr)[0]


def _summarize_authentic_state(ee: bytes, sp: bytes) -> dict:
    p = 0x3f92d0
    cam = 0x3f9860
    mode = 0x3ead30
    actors = {}
    for label, head in (('main', 0x539448), ('prio', 0x539450),
                        ('type2', 0x539458), ('type2_prio', 0x539460)):
        cur = _r32(ee, head)
        items = []
        while cur and len(items) < 32:
            items.append({
                'addr': f'{cur:08x}',
                'callback': f'{_r32(ee, cur + 0x1c):08x}',
                'kind': _r32(ee, cur + 4),
                'phase': [ee[cur + 0x18], ee[cur + 0x19]],
            })
            cur = _r32(ee, cur + 0x24)
        actors[label] = items
    return {
        'mode': {
            'handler': f'{_r32(ee, mode + 4):08x}',
            'major': ee[mode + 8], 'minor': ee[mode + 9],
            'sub': ee[mode + 10], 'step': ee[mode + 11],
            'area': ee[mode + 1],
            'triple': list(ee[0x3fa1b0:0x3fa1b4]),
        },
        'player': {
            'active_00': ee[p], 'flag_01': ee[p + 1],
            'phase_18': ee[p + 0x18], 'subphase_19': ee[p + 0x19],
            'handle_idx_2a': f'{struct.unpack_from("<H", ee, p + 0x2a)[0]:#x}',
            'pos': [round(_rf32(ee, p + 0x30 + i * 4), 4) for i in range(4)],
            'rot': [round(_rf32(ee, p + 0x40 + i * 4), 4) for i in range(4)],
            'eye_380': [round(_rf32(ee, p + 0x380 + i * 4), 4) for i in range(4)],
            'hist_390': [round(_rf32(ee, p + 0x390 + i * 4), 4) for i in range(3)],
            'obj_model_ptr': f'{_r32(ee, p + 0x90 + 0x10):08x}',
            'obj_node_table': f'{_r32(ee, p + 0x90 + 0x58):08x}',
            'grounded_2c3': ee[p + 0x2c3],
            'effect_4e4': f'{_r32(ee, p + 0x4e4):08x}',
        },
        'camera': {
            'eye': [round(_rf32(ee, cam + i * 4), 4) for i in range(4)],
            'target': [round(_rf32(ee, cam + 0x10 + i * 4), 4) for i in range(3)],
            'pitch_yaw': [round(_rf32(ee, cam + 0xd0), 4), round(_rf32(ee, cam + 0xd4), 4)],
            'flag_140': ee[cam + 0x140],
        },
        'handles': {
            'player_0x3f': f'{_r32(ee, 0x3f4300 + 0x3f * 4):08x}',
            'area_0x96': f'{_r32(ee, 0x3f4300 + 0x96 * 4):08x}',
            'title_0x22f': f'{_r32(ee, 0x3f4300 + 0x22f * 4):08x}',
        },
        'pools': {
            'node_stack_top': f'{_r32(ee, 0x4d8140):08x}',
            'node_free_count': _r32(ee, 0x4d8144),
            'actor_free_counts': [_r32(ee, 0x53943c), _r32(ee, 0x539440), _r32(ee, 0x539444)],
        },
        'actors': actors,
        'fade': list(ee[0x3eaeb0:0x3eaeb8]),
        'guards': {'3eaee8': ee[0x3eaee8], '3eaed4': ee[0x3eaed4]},
        'counters': {
            '3fa190': _r32(ee, 0x3fa190), '3eaef0': _r32(ee, 0x3eaef0),
            '3eaef4': _r32(ee, 0x3eaef4), '46e118': _r32(ee, 0x46e118),
            '46d930': _r32(ee, 0x46d930),
        },
        'scratch': {
            '700000c0': f'{_r32(sp, 0xc0):08x}',
            '700000c4': _r32(sp, 0xc4),
            '700000cc': _r32(sp, 0xcc),
            '70000040': [round(_rf32(sp, 0x40 + i * 4), 4) for i in range(3)],
            '700000e0': [round(_rf32(sp, 0xe0 + i * 4), 4) for i in range(3)],
        },
    }


def _probe_dispatch_guard_on_area1_active(ee: bytes, sp: bytes, compiler: str) -> dict:
    """Confirm that passing authentic area1-active directly to existing C
    hg_player_dispatch (00158560 state-1) trips HG_DOMAIN_FAULT(0x30)."""
    from resource_harness import Driver, ResourceEnv, library as build_res_lib

    class _Mem:
        def __init__(self, raw, scratch):
            self.raw = bytearray(raw)
            self.scratch = bytearray(scratch)
        def load(self, a, w):
            buf, off = (self.scratch, a - 0x70000000) if a >= 0x70000000 else (self.raw, a)
            return int.from_bytes(buf[off:off + w], 'little')
        def store(self, a, v, w):
            buf, off = (self.scratch, a - 0x70000000) if a >= 0x70000000 else (self.raw, a)
            buf[off:off + w] = (v & ((1 << (8 * w)) - 1)).to_bytes(w, 'little')

    class _GuardDriver(Driver):
        def __init__(self, mem):
            super().__init__(mem)
            self.domain_faults = []
        def external(self, pc, a=0, b=0, c=0, d=0):
            if pc == 0xfffffff0:
                self.domain_faults.append(a)
                return 0
            return 0

    mem = _Mem(ee, sp)
    drv = _GuardDriver(mem)
    lib = build_res_lib()
    lib.hg_player_dispatch.argtypes = [C.POINTER(ResourceEnv)]
    lib.hg_player_dispatch.restype = None
    lib.hg_player_dispatch(C.byref(drv.env))
    if drv.domain_faults != [0x30]:
        raise AssertionError(f'Expected HG_DOMAIN_FAULT(0x30) on area1-active, got {drv.domain_faults}')
    return {
        'entry': '00158560',
        'input_player_phase_18': ee[0x3f92d0 + 0x18],
        'domain_faults': [f'{code:#x}' for code in drv.domain_faults],
        'boundary_confirmed': True,
    }


def inspect_authentic(elf_path: Path, native_dir: Path, m13_dir: Path | None = None,
                      iso_path: Path | None = None, compiler: str = 'cc') -> dict:
    elf = elf_path.read_bytes()
    elf_sha = hashlib.sha256(elf).hexdigest()
    if elf_sha != ORIGINAL_ELF:
        raise ValueError(f'Unexpected ELF SHA-256: {elf_sha}')

    iso_sha = None
    if iso_path is not None:
        h = hashlib.sha256()
        with iso_path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b''):
                h.update(chunk)
        iso_sha = h.hexdigest()
        if iso_sha != ORIGINAL_ISO:
            raise ValueError(f'Unexpected ISO SHA-256: {iso_sha}')

    segs = elf32(elf)

    def elf_r32(va: int) -> int:
        return struct.unpack_from('<I', elf, va_to_offset(segs, va, 4))[0]

    def elf_rf32(va: int) -> float:
        return struct.unpack_from('<f', elf, va_to_offset(segs, va, 4))[0]

    # Authenticate 0030f3c0(1, 0, 0, 0) spawn table indirection from ELF.
    gp = 0x3e7f70
    t0_addr = gp - 0x1fa0 + 1 * 4
    t1_addr = elf_r32(t0_addr)
    t2_addr = elf_r32(t1_addr)
    spawn_addr = elf_r32(t2_addr)
    spawn_vals = [round(elf_rf32(spawn_addr + i * 4), 6) for i in range(4)]
    spawn_raw = [f'{elf_r32(spawn_addr + i * 4):08x}' for i in range(4)]

    # Authenticate 00158560 jump table at 003db540.
    jt = {f'state_{i}': f'{elf_r32(0x3db540 + i * 4):08x}' for i in range(8)}

    req_meta, req_ee, req_sp = _load_m21_capture(native_dir, 'area1-request')
    act_meta, act_ee, act_sp = _load_m21_capture(native_dir, 'area1-active')

    checkpoints = {
        'area1_request': {
            'ee_sha256': req_meta['ee_sha256'],
            'sp_sha256': req_meta['sp_sha256'],
            'state': _summarize_authentic_state(req_ee, req_sp),
        },
        'area1_active': {
            'ee_sha256': act_meta['ee_sha256'],
            'sp_sha256': act_meta['sp_sha256'],
            'state': _summarize_authentic_state(act_ee, act_sp),
        },
    }

    if m13_dir is not None and (m13_dir / 'obs3-000.json').exists():
        from verify_player_grounded_runtime import Captures
        caps = Captures(m13_dir)
        obs = caps.load('obs3-000')
        obs_ee, obs_sp = bytes(obs.raw), bytes(obs.scratch)
        checkpoints['obs3_000'] = {
            'ee_sha256': hashlib.sha256(obs_ee).hexdigest(),
            'sp_sha256': hashlib.sha256(obs_sp).hexdigest(),
            'state': _summarize_authentic_state(obs_ee, obs_sp),
        }

    guard_probe = _probe_dispatch_guard_on_area1_active(act_ee, act_sp, compiler)

    return {
        'schema': 'hg-m23-authentic-boundary/v1',
        'synthetic_only': False,
        'reference_build': 'SCPS-15046',
        'pcsx2_crc': '91AA4B28',
        'elf_sha256': elf_sha,
        'iso_sha256': iso_sha,
        'elf_spawn_lookup_1_0_0_0': {
            'routine': '0030f3c0',
            'chain': [f'{t0_addr:08x}', f'{t1_addr:08x}', f'{t2_addr:08x}', f'{spawn_addr:08x}'],
            'values': spawn_vals,
            'raw_words': spawn_raw,
        },
        'elf_player_dispatch_jump_table_003db540': jt,
        'last_supported_retail_boundary': {
            'cold_entry_stop': '0012f850 (first AREA1 coordinator call, 8 matched domains in M21)',
            'earliest_missing_sub0_init': '00141b10 -> 00141910 -> 00141210 -> 0013fef0 -> 0012bf70 -> 0013a4c0 -> 00189aa0 (called by C 0012e700 sub 0, externalized in M21)',
            'earliest_missing_frame1_init': '00158560 state-0 branch (001585c0 -> 00158a10 + 001e3440 + phase 0->1)',
            'guard_probe': guard_probe,
        },
        'checkpoints': checkpoints,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--synthetic', action='store_true',
                        help='Run pure synthetic C provenance harness (no retail files)')
    parser.add_argument('--inspect-authentic', action='store_true',
                        help='Inspect authenticated SCPS-15046 ELF and M21/M13 captures (read-only)')
    parser.add_argument('--compiler', default=os.environ.get('CC', 'cc'))
    parser.add_argument('--sanitize', action='store_true')
    parser.add_argument('--elf', type=Path)
    parser.add_argument('--iso', type=Path)
    parser.add_argument('--native', type=Path, help='M21 native capture directory (containing full/)')
    parser.add_argument('--m13-captures', type=Path, help='M13 captures directory (obs3-000.json)')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()

    if not args.synthetic and not args.inspect_authentic:
        args.synthetic = True
    if args.synthetic and args.inspect_authentic:
        parser.error('Run --synthetic and --inspect-authentic separately to keep evidence distinct')

    if args.synthetic:
        result = verify_synthetic(compiler=args.compiler, sanitize=args.sanitize)
    else:
        if not args.elf or not args.native:
            parser.error('--inspect-authentic requires --elf and --native')
        result = inspect_authentic(args.elf, args.native, m13_dir=args.m13_captures,
                                   iso_path=args.iso, compiler=args.compiler)

    text = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        print(text, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
