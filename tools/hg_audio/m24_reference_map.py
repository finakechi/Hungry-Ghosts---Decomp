#!/usr/bin/env python3
"""M24 read-only reference dependency scan of the authenticated SCPS-15046 ELF.

Recovers, from original instruction words only, the observable relationships
between the EE audio thread, its 16-slot service scheduler, the sequence
(command) service, the channel consumer, the voice-preparation and RPC slots,
the play/cancel entry points, the two dispatch tables and the physical voice
allocation chain. It emits addresses, call sites and table words, never
instruction listings or game data. Interpretation lives in docs/M24.md.

    python3 -B tools/hg_audio/m24_reference_map.py ELF [--output JSON] [--check JSON]
"""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

ELF_SHA256 = 'a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61'
TEXT_VA, TEXT_OFFSET, TEXT_SIZE = 0x00100000, 0x800, 0x2ea980
REGISTER = 0x002a7358      # scheduler slot registration (slot, function)
TICK_RUNNER = 0x002a7450   # runs the 16 slots in order
FUNCTIONS = {
    'audio_thread': 0x001566c0, 'sleep_thread': 0x00103e80, 'rpc_check': 0x002ad118,
    'tick_runner': TICK_RUNNER, 'register_slot': REGISTER, 'unregister_slot': 0x002a7398,
    'sound_dispatch': 0x00156ad0, 'sound_descriptor': 0x00157ed0,
    'play': 0x002ad478, 'play_extra_limit': 0x002ad358, 'cancel': 0x002ad748,
    'sg2_enqueue': 0x002acf60, 'sg2_flush': 0x002ad138, 'sg2_bind': 0x002ad288,
    'channel_command': 0x002a94b8, 'channel_bytes': 0x002a95c8,
    'channel_release_check': 0x002a9d18, 'channel_system_init': 0x002aa5e0,
    'command_table_init': 0x002ae030, 'sequence_service': 0x002add90,
    'sequence_dispatch_site': 0x002ade94, 'sequence_a': 0x002ae2c0,
    'sequence_b': 0x002ae370, 'sequence_f': 0x002ae8d8,
    'channel_consumer': 0x002a96b8, 'channel_a_note': 0x002ab508,
    'instrument_lookup': 0x002b50e0, 'voice_free_search': 0x002ae9b8,
    'voice_fallback_1': 0x002afed8, 'voice_fallback_2': 0x002af1d0,
    'voice_acquire': 0x002b1098, 'voice_release_note': 0x002b2030,
    'voices_owned_by_channel': 0x002b0cd8, 'voices_matching': 0x002b0f58,
    'voice_prepare_slot': 0x002b2438, 'voice_adsr': 0x002b1668, 'voice_ssa': 0x002b1188,
}
TABLES = {'sequence_status_table': (0x003bd170, 16), 'channel_event_table': (0x003bd078, 64)}
CALLS_IN = {  # selected callees inside a function body [start, end)
    'sequence_service': (0x002add90, 0x002ae030),
    'channel_a_note_allocation': (0x002ab5f0, 0x002ab6b0),
    'audio_thread': (0x001566c0, 0x00156738),
}


def load(path):
    data = Path(path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != ELF_SHA256:
        raise SystemExit(f'{path}: SHA-256 {digest} is not the authenticated SCPS-15046 ELF')
    words = struct.unpack_from(f'<{TEXT_SIZE // 4}I', data, TEXT_OFFSET)
    return data, words


def word_at(data, va):
    return struct.unpack_from('<I', data, TEXT_OFFSET + va - TEXT_VA)[0]


def jal_target(word, va):
    return ((va + 4) & 0xf0000000) | ((word & 0x03ffffff) << 2) if word >> 26 == 3 else None


def scan(path):
    data, words = load(path)
    callers = {}
    for i, word in enumerate(words):
        target = jal_target(word, TEXT_VA + 4 * i)
        if target is not None:
            callers.setdefault(target, []).append(TEXT_VA + 4 * i)

    def h(v):
        return f'{v:08x}'

    # Scheduler registrations: jal 002a7358 with li a0,N and lui/addiu a1 nearby.
    registrations = []
    for site in callers.get(REGISTER, []):
        i = (site - TEXT_VA) // 4
        window = range(max(0, i - 10), i + 2)
        slot = hi = lo = None
        for j in window:
            w = words[j]
            op = w >> 26
            writes = ((w >> 11) & 31) if op == 0 else ((w >> 16) & 31) if op in (8, 9, 0xd, 0xf, 0x23) else None
            if writes == 4:                              # last writer of a0 decides
                slot = (w & 0xffff) if w >> 16 == 0x2404 else 0 if w == 0x0000202d else None
            if writes == 5:
                if w >> 16 == 0x3c05:                    # lui a1, imm
                    hi, lo = w & 0xffff, None
                elif op == 9 and (w >> 21) & 31 == 5:    # addiu a1, a1, imm
                    lo = w & 0xffff
                else:
                    hi = lo = None
        function = None
        if hi is not None and lo is not None and slot is not None:
            function = ((hi << 16) + (lo - 0x10000 if lo & 0x8000 else lo)) & 0xffffffff
        registrations.append({'site': h(site), 'slot': slot,
                              'function': h(function) if function is not None else None})
    registrations.sort(key=lambda r: (r['slot'] is None, r['slot'] or 0))

    tables = {}
    for name, (va, count) in TABLES.items():
        tables[name] = {'address': h(va),
                        'words': [h(word_at(data, va + 4 * k)) for k in range(count)]}

    calls_in = {}
    for name, (start, end) in CALLS_IN.items():
        found = []
        for va in range(start, end, 4):
            w = word_at(data, va)
            target = jal_target(w, va)
            if target is not None:
                found.append([h(va), h(target)])
            elif w & 0xfc1fffff == 0x0000f809:         # jalr ra, rs
                found.append([h(va), 'jalr'])
        calls_in[name] = found

    syscalls = {}
    for va in (0x00103e80, 0x00103e90, 0x00103ea0):
        li, sc = word_at(data, va), word_at(data, va + 4)
        number = li & 0xffff
        syscalls[h(va)] = {'number': number - 0x10000 if number & 0x8000 else number,
                           'syscall': sc == 0x0000000c,
                           'callers': [h(c) for c in callers.get(va, [])]}

    return {
        'format': 'hg-m24-reference-map/1',
        'elf_sha256': ELF_SHA256,
        'functions': {name: {'address': h(va),
                             'direct_callers': [h(c) for c in callers.get(va, [])][:12],
                             'direct_caller_count': len(callers.get(va, []))}
                      for name, va in FUNCTIONS.items()},
        'scheduler_registrations': registrations,
        'tables': tables,
        'calls_in': calls_in,
        'syscall_wrappers': syscalls,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('elf')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--check', type=Path, help='compare with a committed map')
    args = parser.parse_args()
    result = scan(args.elf)
    text = json.dumps(result, indent=1, sort_keys=True) + '\n'
    if args.output:
        args.output.write_text(text)
    if args.check:
        expected = json.loads(args.check.read_text())
        if expected != result:
            print('reference map differs from', args.check, file=sys.stderr)
            return 1
        print('reference map matches', args.check)
    if not args.output and not args.check:
        sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
