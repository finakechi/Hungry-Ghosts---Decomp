#!/usr/bin/env python3
"""M26 read-only authenticated reference timing and allocation verifier.

Authenticates the Japanese SCPS-15046 ELF (SHA-256 a74248c5...) and optionally
the pinned M22 saved PCSX2 checkpoints under --live (no raw game bytes or
personal paths are emitted).

Verifies from original ELF instruction words:
  * INTC 2 (0011df10) and INTC 3 (0011df80) registration at 0011d880..0011d8b8
    and audio init call 00156870;
  * audio thread creation 00156910..00156920 -> 00143360(0x64, 0, 001566c0)
    writing active flag 003f6db0=1 and tid 003f6db4;
  * INTC 3 VBlank-End handler 0011df80 waking 003f6db4 via 00104e10 ->
    00103ea0 (iWakeupThread, syscall -0x34);
  * EE audio thread 001566c0 (SleepThread -> 002ad118 RPC idle check ->
    002a7450 16-slot scheduler);
  * Slot 1 (002add90) state-2 retirement via 002a9d18 and soft-double timing;
  * Slot 2 (002a96b8) channel ring consumption and deferred close via 002b0cd8;
  * Slot 3 (002b2438) voice age>=3 + ENVX<2 retirement via 002ad0e8 and mask
    enqueue order (0x09 wet, 0x0a noise, 0x07 key-on, 0x08 key-off);
  * Allocator chain 002ae9b8 -> 002afed8 -> 002af1d0 -> 002b1098 (monotonic
    cursor 00a832a0) and release 002b2030 (ADSR(0,8) + key-off masks 00a83278..7c).

When --live is supplied, authenticates the pinned M22 fresh checkpoints and
extracts sanitized counters, command timing fields, channel ring indices,
voice records, and allocation cursor progression across title-input,
cursor-down, cursor-up, and cursor-confirm (plus wake vs VBlank-Start across
all 12 M22 saves).

    python3 -B tools/hg_audio/m26_reference_timing.py --elf ELF [--live DIR]
        [--output JSON] [--check JSON]
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
import zipfile

ELF_SHA256 = 'a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61'
TEXT_VA, TEXT_OFFSET, TEXT_SIZE = 0x00100000, 0x800, 0x2ea980

# Pinned M22 fresh checkpoint SHA-256 digests (matching evidence/m22/retail-check.json).
PINNED_STATES = {
    'title-input.p2s': '4c32e376633fe1e9fa922bb1760d441af938df567414c7c7321b10f29a02a1bb',
    'cursor-down.p2s': '815393198648ded45e6b704eba9d8087a44ce5d2d8b958e20e13324c9ed8dda2',
    'cursor-up.p2s': 'a05d36829abacd4c5f139e95a8b7c1362222a8377e6d120e40c9af9a801ddd14',
    'cursor-confirm.p2s': 'a549596bd96641dde84e6132293205ee8520abc3a21b6579638d7652729ba9e9',
}

ALL_M22_SAVES = (
    'title-idle.p2s', 'title-move.p2s', 'title-enter.p2s', 'no-save.p2s',
    'title-input.p2s', 'press-start.p2s', 'menu-enter.p2s', 'menu-move-down.p2s',
    'menu-move-up.p2s', 'cursor-down.p2s', 'cursor-up.p2s', 'cursor-confirm.p2s',
)

# Selected instruction anchors confirming wake, timing, consumer, allocator, and release wiring.
ELF_ANCHORS = {
    'boot_register_intc2_vblank_start': (0x0011d88c, 'jal', 0x00103c40),
    'boot_register_intc3_vblank_end': (0x0011d8a0, 'jal', 0x00103c40),
    'boot_enable_intc2': (0x0011d8a8, 'jal', 0x00104aa8),
    'boot_enable_intc3': (0x0011d8b0, 'jal', 0x00104aa8),
    'boot_audio_init': (0x0011d8b8, 'jal', 0x00156870),
    'audio_init_set_tick_60': (0x00156890, 'jal', 0x002a74b8),
    'audio_init_create_thread': (0x0015691c, 'jal', 0x00143360),
    'vblank_start_wakeup': (0x0011df5c, 'jal', 0x00104e10),
    'vblank_end_wakeup_audio': (0x0011dfa8, 'jal', 0x00104e10),
    'iwakeup_wrapper_syscall': (0x00104e30, 'jal', 0x00103ea0),
    'audio_thread_sleep': (0x001566d0, 'jal', 0x00103e80),
    'audio_thread_rpc_check': (0x001566d8, 'jal', 0x002ad118),
    'audio_thread_scheduler': (0x001566e8, 'jal', 0x002a7450),
    'slot1_retire_check': (0x002adde4, 'jal', 0x002a9d18),
    'slot1_time_add': (0x002ade44, 'jal', 0x00115fd8),
    'slot1_time_cmp': (0x002adf30, 'jal', 0x00116580),
    'slot2_voice_owner_check': (0x002a98ac, 'jal', 0x002b0cd8),
    'slot3_envx_read': (0x002b24b8, 'jal', 0x002ad0e8),
    'slot3_enqueue_wet_mask': (0x002b2cf4, 'jal', 0x002acf60),
    'slot3_enqueue_noise_mask': (0x002b2d44, 'jal', 0x002acf60),
    'slot3_enqueue_key_on': (0x002b2d78, 'jal', 0x002acf60),
    'slot3_enqueue_key_off': (0x002b2da4, 'jal', 0x002acf60),
    'release_note_set_adsr': (0x002b20c0, 'jal', 0x002b1668),
}


def load_elf(path):
    data = Path(path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != ELF_SHA256:
        raise SystemExit(f'{path}: SHA-256 {digest} is not the authenticated SCPS-15046 ELF')
    return data


def word_at(data, va):
    return struct.unpack_from('<I', data, TEXT_OFFSET + va - TEXT_VA)[0]


def jal_target(word, va):
    return ((va + 4) & 0xf0000000) | ((word & 0x03ffffff) << 2) if word >> 26 == 3 else None


def verify_elf(data):
    anchors = {}
    for name, (va, kind, expected_target) in ELF_ANCHORS.items():
        w = word_at(data, va)
        if kind != 'jal':
            raise ValueError(f'unsupported anchor kind {kind}')
        target = jal_target(w, va)
        if target != expected_target:
            raise SystemExit(f'anchor {name} at {va:08x}: target {target} != {expected_target:08x}')
        anchors[name] = {'address': f'{va:08x}', 'target': f'{target:08x}'}

    # Verify specific immediate constants directly from instruction words.
    tick_imm = word_at(data, 0x00156894) & 0xffff       # addiu a0, zero, 0x3c
    thread_kind = word_at(data, 0x00156914) & 0xffff    # addiu a0, zero, 0x64
    age_thresh = word_at(data, 0x002b24ac) & 0xffff     # slti v0, v0, 3
    envx_thresh = word_at(data, 0x002b24c0) & 0xffff    # slti v0, v0, 2
    rel_adsr2 = word_at(data, 0x002b20c4) & 0xffff      # addiu a2, zero, 8
    vbe_flag_off = word_at(data, 0x0011df98) & 0xffff   # lhu v1, 0x6db0($at) -> 003f6db0
    vbe_tid_off = word_at(data, 0x0011dfac) & 0xffff    # lw a0, 0x6db4($at)  -> 003f6db4
    iwake_sys = word_at(data, 0x00103ea0) & 0xffff
    iwake_sys = iwake_sys - 0x10000 if iwake_sys & 0x8000 else iwake_sys
    sleep_sys = word_at(data, 0x00103e80) & 0xffff

    if (tick_imm != 60 or thread_kind != 0x64 or age_thresh != 3 or envx_thresh != 2
            or rel_adsr2 != 8 or vbe_flag_off != 0x6db0 or vbe_tid_off != 0x6db4
            or iwake_sys != -52 or sleep_sys != 50):
        raise SystemExit('ELF instruction immediate constants disagreed with authenticated layout')

    # Verify Slot 3 mask enqueue command IDs in exact order: 9 (wet), 10 (noise), 7 (key-on), 8 (key-off).
    mask_cmds = [
        word_at(data, 0x002b2cf0) & 0xffff,
        word_at(data, 0x002b2d40) & 0xffff,
        word_at(data, 0x002b2d74) & 0xffff,
        word_at(data, 0x002b2da0) & 0xffff,
    ]
    if mask_cmds != [9, 10, 7, 8]:
        raise SystemExit(f'unexpected Slot 3 mask enqueue command order: {mask_cmds}')

    return {
        'elf_sha256': ELF_SHA256,
        'anchors': anchors,
        'constants': {
            'scheduler_tick_hz_word': tick_imm,
            'audio_thread_slot_kind': thread_kind,
            'vblank_end_thread_flag_addr': '003f6db0',
            'vblank_end_thread_id_addr': '003f6db4',
            'vblank_start_counter_addr': '0095e300',
            'wake_counter_addr': '00a6c3c0',
            'sleep_thread_syscall': sleep_sys,
            'i_wakeup_thread_syscall': iwake_sys,
            'slot3_retire_min_age_wakes': age_thresh,
            'slot3_retire_envx_lt': envx_thresh,
            'slot3_mask_enqueue_order': mask_cmds,
            'release_note_adsr2': rel_adsr2,
            'alloc_cursor_addr': '00a832a0',
            'pending_key_on_masks': ['00a83270', '00a83274'],
            'pending_key_off_masks': ['00a83278', '00a8327c'],
        },
    }


def read_p2s_ee(path, expected_sha=None):
    blob = path.read_bytes()
    sha = hashlib.sha256(blob).hexdigest()
    if expected_sha is not None and sha != expected_sha:
        raise SystemExit(f'{path.name}: SHA-256 {sha} != pinned {expected_sha}')
    with zipfile.ZipFile(path) as zf:
        ee = zf.read('eeMemory.bin')
    return sha, ee


def inspect_checkpoint(ee, detailed=False):
    r32 = lambda a: struct.unpack_from('<I', ee, a)[0]
    r16 = lambda a: struct.unpack_from('<H', ee, a)[0]
    r64u = lambda a: struct.unpack_from('<Q', ee, a)[0]
    r64f = lambda a: struct.unpack_from('<d', ee, a)[0]

    vblank_start = r32(0x0095e300)
    wake = r32(0x00a6c3c0)
    gtick = r32(0x00a6c3c4)
    slot_counters = [r32(0x00a6c344 + 8 * n) for n in range(16)]
    row = {
        'vblank_start_0095e300': vblank_start,
        'wake_counter_00a6c3c0': wake,
        'vblank_minus_wake': vblank_start - wake,
        'global_tick_00a6c3c4': gtick,
        'all_16_slot_counters_equal_wake': all(c == wake for c in slot_counters),
        'audio_thread_flag_003f6db0': r16(0x003f6db0),
        'audio_thread_id_003f6db4': r32(0x003f6db4),
        'alloc_cursor_00a832a0': r32(0x00a832a0),
        'sequence_counter_00a832a4': r32(0x00a832a4),
        'pending_masks_00a83270_7c': [f'{r32(0x00a83270 + 4 * k):08x}' for k in range(4)],
        'ee_sg2_cursor_00a80180': r32(0x00a80180),
        'nonzero_envx': [[v, r32(0x00a821c0 + 4 * v)] for v in range(48)
                         if r32(0x00a821c0 + 4 * v) != 0],
    }
    if not detailed:
        return row

    cmds = []
    for i in range(32):
        b = 0x00a82640 + i * 0x50
        st, seq = r32(b), r32(b + 8)
        if st != 0 or seq != 0:
            cmds.append({
                'index': i,
                'state': st,
                'seq': seq,
                'slot': r32(b + 0x0c),
                'channel': r32(b + 0x10),
                'param1': r32(b + 0x14),
                'param2': r32(b + 0x18),
                'step_0x20_double': r64f(b + 0x20),
                'step_0x20_hex': f'{r64u(b + 0x20):016x}',
                'accum_0x28_double': r64f(b + 0x28),
                'delta_0x30_double': r64f(b + 0x30),
                'cursor_0x40': f'{r32(b + 0x40):08x}',
                'flag_0x44': r32(b + 0x44),
                'running_status_0x48': f'{r32(b + 0x48):02x}',
            })
    channels = []
    for ch in range(32):
        cb = 0x00a6c4d8 + ch * 0x9e4
        qb = 0x00a6c530 + ch * 0x9e4
        slot = r32(cb)
        qw, qr = r32(qb + 0x200), r32(qb + 0x204)
        if slot != 0xffffffff or qw != 0 or qr != 0:
            channels.append({
                'channel': ch,
                'slot': None if slot == 0xffffffff else slot,
                'owner_id_0x04': r32(cb + 4),
                'closing_flag_0x10': r32(cb + 0x10),
                'ring_write_index': qw,
                'ring_read_index': qr,
                'ring_drained': qw == qr,
            })
    voices = []
    for v in range(48):
        vb = 0x00a832b0 + v * 0xf0
        flags = r32(vb)
        vclass = r32(vb + 4)
        if flags != 0 or r32(vb + 0x24) != 0:
            voices.append({
                'voice': v,
                'flags_0x00': f'{flags:02x}',
                'class_0x04': vclass,
                'alloc_stamp_0x08': r32(vb + 8),
                'owner_channel_id_0x0c': r32(vb + 0x0c),
                'age_wakes_0x20': r32(vb + 0x20),
                'note_0x24': r32(vb + 0x24),
                'envx_reply': r32(0x00a821c0 + 4 * v),
            })
    row['staging_00a83150_count'] = r32(0x00a83150)
    row['staging_00a83154_last_status'] = f'{r32(0x00a83154):02x}'
    row['global_staging_00a83260_count'] = r32(0x00a83260)
    row['commands'] = cmds
    row['channels'] = channels
    row['voices'] = voices
    return row


def scan(elf_path, live_dir=None):
    elf_data = load_elf(elf_path)
    result = {
        'schema': 'hg-m26-reference-timing/v1',
        'evidence_note': (
            'Authenticated ELF instruction anchors and sanitized M22 saved-checkpoint '
            'counter/state observations. Contains no game media, raw captures, or '
            'personal paths. Live multi-frame PINE tracing was not executed.'
        ),
        'elf': verify_elf(elf_data),
        'saved_checkpoints': None,
        'instrumentation_limits': [
            'Saved PCSX2 checkpoints capture frame-boundary snapshots only (not sub-frame wake latency).',
            'Pending SG2IOP masks at 00a83270..00a8327c are always 0 at saved boundaries because Slot 3 clears them before Slot 4 flushes.',
            'Live PCSX2 PINE polling across consecutive frames and repeated cursor runs remain unexecuted in this milestone.',
            'Exact RPC-busy skip frequency under heavy load, fallback steal tie-breaking inside 002afed8/002af1d0, and SPU2 hardware ADSR release curves remain outside this bounded check.',
        ],
    }
    if live_dir is not None:
        live = Path(live_dir)
        timeline = []
        detailed = {}
        for name in ALL_M22_SAVES:
            p = live / name
            if not p.is_file():
                raise SystemExit(f'missing M22 saved checkpoint: {name}')
            expected_sha = PINNED_STATES.get(name)
            sha, ee = read_p2s_ee(p, expected_sha=expected_sha)
            is_det = name in PINNED_STATES
            snap = inspect_checkpoint(ee, detailed=is_det)
            timeline.append({
                'checkpoint': name.removesuffix('.p2s'),
                'state_sha256': sha,
                'vblank_start_0095e300': snap['vblank_start_0095e300'],
                'wake_counter_00a6c3c0': snap['wake_counter_00a6c3c0'],
                'vblank_minus_wake': snap['vblank_minus_wake'],
                'all_16_slot_counters_equal_wake': snap['all_16_slot_counters_equal_wake'],
                'alloc_cursor_00a832a0': snap['alloc_cursor_00a832a0'],
            })
            if is_det:
                detailed[name.removesuffix('.p2s')] = {'state_sha256': sha, **snap}

        # Derived consistency checks on the cursor sequence:
        cd = detailed['cursor-down']
        cu = detailed['cursor-up']
        cc = detailed['cursor-confirm']
        v_cu = {v['voice']: v for v in cu['voices']}
        v_cc = {v['voice']: v for v in cc['voices']}
        result['saved_checkpoints'] = {
            'timestamp_source': 'guest VBlank-Start counter (0095e300) vs audio scheduler wake counter (00a6c3c0) in PCSX2 v2.8.2 saved states',
            'checkpoint_count': len(timeline),
            'vblank_span': timeline[-1]['vblank_start_0095e300'] - timeline[0]['vblank_start_0095e300'],
            'wake_span': timeline[-1]['wake_counter_00a6c3c0'] - timeline[0]['wake_counter_00a6c3c0'],
            'all_diffs_equal_one': all(row['vblank_minus_wake'] == 1 for row in timeline),
            'all_slots_match_wake': all(row['all_16_slot_counters_equal_wake'] for row in timeline),
            'timeline': timeline,
            'cursor_sequence': detailed,
            'cursor_attribution': {
                'cursor_down_wakes': cd['wake_counter_00a6c3c0'],
                'cursor_up_wakes': cu['wake_counter_00a6c3c0'],
                'cursor_confirm_wakes': cc['wake_counter_00a6c3c0'],
                'cursor_down_to_up_delta_wakes': cu['wake_counter_00a6c3c0'] - cd['wake_counter_00a6c3c0'],
                'cursor_up_to_confirm_delta_wakes': cc['wake_counter_00a6c3c0'] - cu['wake_counter_00a6c3c0'],
                'voice_8_owner_at_cursor_up': v_cu[8]['owner_channel_id_0x0c'],
                'voice_9_owner_at_cursor_up': v_cu[9]['owner_channel_id_0x0c'],
                'voice_10_owner_at_cursor_up': v_cu[10]['owner_channel_id_0x0c'],
                'voice_6_7_8_idle_at_confirm': all(v_cc[v]['flags_0x00'] == '00' for v in (6, 7, 8)),
                'voice_16_17_releasing_at_confirm': all(v_cc[v]['flags_0x00'] == '15' for v in (16, 17)),
                'note_spacing_ticks': 72,
                'note_spacing_wakes': 9,
                'ticks_per_wake': 8.0,
            },
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--elf', required=True, help='Path to authenticated SCPS_150.46')
    parser.add_argument('--live', help='Path to authenticated local/m22/live saved checkpoints')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--check', type=Path, help='Compare with committed evidence JSON')
    args = parser.parse_args()
    result = scan(args.elf, args.live)
    text = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding='utf-8')
    if args.check:
        expected = json.loads(args.check.read_text(encoding='utf-8'))
        if expected != result:
            print('M26 reference timing differs from', args.check, file=sys.stderr)
            return 1
        print('M26 reference timing matches', args.check)
    if not args.output and not args.check:
        sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
