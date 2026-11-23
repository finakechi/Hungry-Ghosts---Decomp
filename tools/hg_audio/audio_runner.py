#!/usr/bin/env python3
"""Connected audio runner: the full supported sound lifecycle.

    Authenticated original resource -> bank registration -> sound
    selection -> play command -> IOP service response -> offline audio
    output -> stop -> release.

Each stage uses state produced by the preceding stage; no captured
intermediate state or expected-output tables are injected. The IOP
service is a clearly labeled host model. Outputs (decoded WAV and the
registration receipt) stay under local/audio and are never committed.
"""
import argparse
import hashlib
import json
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hg_identify import ISO, read_at  # noqa: E402
from hg_audio.audio_guest import (AudioHarness, SLOT_TABLE, ID_TABLE,  # noqa: E402
                                  COMMAND_BASE, CHANNEL_QUEUE)
from hg_audio.adpcm import decode_stream, write_wav  # noqa: E402

ISO_SHA256 = '8ca1513c8417c0a40b838ff5edf22a2360f43bce1b0458693a95a6efdafd994e'
KNOWN = {
    '6b51': {'dir': 0x6b, 'flags': 0x51, 'load': 0x01009000,
             'endpoint': 0x0100a740, 'table1_index': 1, 'sample_rate': 48000},
    '8594': {'dir': 0x85, 'flags': 0x94, 'load': 0x00f78000,
             'endpoint': 0x00f78880, 'table1_index': 1, 'sample_rate': 48000},
}


def authenticate(fp):
    h = hashlib.sha256()
    for chunk in iter(lambda: fp.read(4 * 1024 * 1024), b''):
        h.update(chunk)
    if h.hexdigest() != ISO_SHA256:
        raise SystemExit('ISO identity mismatch; refusing to run')
    fp.seek(0)


def fetch_member(fp, iso, directory, flags):
    rec = iso.files['/DATA/PACK.DAT']
    base = rec['lba'] * 2048
    block = read_at(fp, base + directory * 2048, 2048)
    ident, count = struct.unpack_from('<2I', block)
    for item in range(count):
        offset, length, fl, reserved = struct.unpack_from('<4I', block, 64 + item * 16)
        if fl == flags:
            return read_at(fp, base + offset, length)
    raise SystemExit('member not found')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--member', choices=sorted(KNOWN), default='6b51',
                        help='authenticated audio member to exercise')
    parser.add_argument('--out', type=Path, default=Path('local/audio/out.wav'),
                        help='private WAV output path')
    parser.add_argument('--receipt', type=Path,
                        default=Path('local/audio/runner-receipt.json'))
    parser.add_argument('--max-seconds', type=float, default=1.0,
                        help='seconds of decoded audio to render')
    parser.add_argument('--iso', type=Path,
                        default=Path(os.environ['HG_JAPAN_ISO'])
                        if os.environ.get('HG_JAPAN_ISO') else None,
                        help='authenticated Japan ISO (default: $HG_JAPAN_ISO)')
    args = parser.parse_args()
    if args.iso is None:
        parser.error('no original image: pass --iso or set HG_JAPAN_ISO')
    if not args.iso.is_file():
        parser.error(f'original image not found: {args.iso}')
    spec = KNOWN[args.member]

    receipt = {'member': args.member, 'stages': []}
    with open(args.iso, 'rb') as fp:
        authenticate(fp)
        iso = ISO(fp)
        blob = fetch_member(fp, iso, spec['dir'], spec['flags'])
    member_sha = hashlib.sha256(blob).hexdigest()

    # Stage 1: registration through the reconstructed EE functions
    harness = AudioHarness()
    endpoint = harness.register_resource(blob, spec['load'])
    assert endpoint == spec['endpoint'], f'endpoint {endpoint:#x} != recorded {spec["endpoint"]:#x}'
    bank_id = struct.unpack_from('<I', blob, 0x28)[0]
    entry = harness.mem.r32(ID_TABLE + bank_id * 0x78)
    bank_file = struct.unpack_from('<I', blob, 0x24)[0]
    receipt['stages'].append({'stage': 'registration',
                              'member_sha256': member_sha,
                              'endpoint': hex(endpoint), 'slot': entry,
                              'bank': hex(spec['load'] + bank_file)})

    # Stage 2+3: sound selection and play-command construction through
    # the reconstructed play path (2ad478 -> table4 lookup -> 2a9b68)
    command = harness.play(entry, spec['table1_index'], 0, 0, 0)
    sample = harness.mem.r32(COMMAND_BASE + command * 0x50 + 0x40)
    receipt['stages'].append({'stage': 'selection',
                              'play_index': spec['table1_index'],
                              'resolved_sample': hex(sample),
                              'command_index': command})
    assert command != 0xffffffff, 'play command not posted'
    assert sample != 0, 'selection did not resolve a sample'

    # Stage 4: IOP service response (host model)
    channel = harness.mem.r32(COMMAND_BASE + command * 0x50 + 0x10)
    assert channel != 0xffffffff, 'no channel opened'
    harness.model.iop_audio.queue_command(channel, 0x01)  # modeled "play"
    harness.channel_command(channel, 0x80)                # modeled "note on"
    receipt['stages'].append({'stage': 'service',
                              'channel': channel,
                              'iop_payloads': len(harness.model.iop_audio.payloads),
                              'mscom': harness.model.mscom})

    # Stage 5: offline audio output from the DMA'd payload
    payload = harness.model.iop_audio.payloads[0]['blob']
    pcm, flags = decode_stream(payload)
    samples = int(min(len(pcm), args.max_seconds * spec['sample_rate']))
    write_wav(args.out, pcm[:samples], spec['sample_rate'])
    receipt['stages'].append({'stage': 'output',
                              'wav': str(args.out),
                              'samples_rendered': samples,
                              'adpcm_flags': flags[:8],
                              'payload_sha256': harness.model.iop_audio.payloads[0]['sha256']})

    # Stage 6: stop and release through the reconstructed cancel path
    stop_result = harness.cancel(command)
    ring = CHANNEL_QUEUE + channel * 0x9e4
    ring_index = harness.mem.r32(ring + 0x200)
    stop_bytes = [harness.mem.r8(ring + ring_index - 3 + i) for i in range(3)]
    harness._bind('hg_audio_slot_clear', ['s'])(harness._bus_p, entry)
    receipt['stages'].append({'stage': 'stop_release',
                              'cancel_result': stop_result,
                              'stop_command_bytes': stop_bytes,
                              'slot_state_after': harness.mem.r32(
                                  SLOT_TABLE + entry * 0x2c)})
    assert stop_bytes == [0xff, 0x41, 0xf7], 'stop command sequence missing'

    args.receipt.write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({'result': 'accepted-connected-run', 'receipt': str(args.receipt)},
                     indent=2))


if __name__ == '__main__':
    main()
