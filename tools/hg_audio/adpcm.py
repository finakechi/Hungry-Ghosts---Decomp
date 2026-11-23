#!/usr/bin/env python3
"""Independent PS2 SPU ADPCM (VAG-family) decoder.

The bank payload transferred to the IOP is 16-byte-block 4-bit ADPCM:
per block: 1 header byte (predictor<<4 | shift), 1 flags byte, then 28
signed 4-bit nibbles. Decoding follows the public SPU2 register model:
sample = clamp16(((nibble << shift) + (f0 * s1 + f1 * s2 + 32)) >> 6)
with per-predictor f0/f1 tables. No third-party code.

Flags: 0x01 loop-start region end, 0x02 loop start, 0x04 sample start;
exact loop semantics beyond the raw bit decoding are reported, not
asserted.
"""
import struct

PREDICTORS = [
    (0, 0), (60, 0), (115, -52), (98, -55), (122, -60),
]


class AdpcmError(ValueError):
    pass


def decode_block(block, s1, s2):
    """Decode one 16-byte block. Returns (samples, next_s1, next_s2)."""
    if len(block) != 16:
        raise AdpcmError('block must be 16 bytes')
    header = block[0]
    shift = header & 0xf
    predictor = header >> 4
    if predictor > 4:
        raise AdpcmError(f'invalid predictor {predictor}')
    if shift > 12:
        raise AdpcmError(f'invalid shift {shift}')
    f0, f1 = PREDICTORS[predictor]
    samples = []
    for i in range(2, 16):
        byte = block[i]
        for nibble in (byte & 0xf, byte >> 4):
            value = (nibble << shift) if nibble < 8 else ((nibble - 16) << shift)
            sample = value + ((f0 * s1 + f1 * s2 + 32) >> 6)
            sample = max(-0x8000, min(0x7fff, sample))
            samples.append(sample)
            s2 = s1
            s1 = sample
    return samples, s1, s2


def decode_stream(data, start=0, end=None, initial_s1=0, initial_s2=0):
    """Decode a VAG-family stream. Returns (pcm list, loop_points dict).

    loop_points reports the byte offset and sample index of observed
    0x01/0x02/0x04 flag blocks; semantics are preserved raw.
    """
    if end is None:
        end = len(data)
    if (end - start) % 16:
        raise AdpcmError('stream length not a multiple of 16')
    pcm = []
    s1, s2 = initial_s1, initial_s2
    flags = []
    offset = start
    while offset < end:
        block = data[offset:offset + 16]
        if block[1] in (0x01, 0x02, 0x04):
            flags.append({'offset': offset, 'sample_index': len(pcm),
                          'flags': block[1]})
        samples, s1, s2 = decode_block(block, s1, s2)
        pcm.extend(samples)
        offset += 16
    return pcm, flags


def write_wav(path, pcm, sample_rate=48000):
    """Write 16-bit mono PCM as WAV (private output only)."""
    import wave
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(struct.pack('<%dh' % len(pcm), *pcm))


def load_wav(path):
    import wave
    with wave.open(str(path), 'rb') as w:
        return (w.getframerate(), w.getnchannels(),
                struct.unpack('<%dh' % (w.getnframes() * w.getnchannels()),
                              w.readframes(w.getnframes())))
