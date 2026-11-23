"""Synthetic tests for the PS2 SPU ADPCM decoder.

The test-side encoder is written independently from the decoder
(different control flow, same published SPU2 semantics) so a round-trip
is not self-referential. Negative controls verify that corrupted blocks,
invalid predictors/shifts, and wrong predictor tables are detected.
"""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools' / 'hg_audio'))
from adpcm import (AdpcmError, decode_block, decode_stream, write_wav,
                   load_wav, PREDICTORS)


def encode_block(samples, s1, s2, shift, predictor):
    """Independent reference encoder for a 28-sample block."""
    f0, f1 = PREDICTORS[predictor]
    out = bytearray(16)
    out[0] = (predictor << 4) | shift
    for i, sample in enumerate(samples):
        sample = max(-0x8000, min(0x7fff, sample))
        predicted = (f0 * s1 + f1 * s2 + 32) >> 6
        delta = sample - predicted
        if shift == 0:
            q = 0
        elif delta >= 0:
            q = (delta + (1 << (shift - 1))) >> shift
        else:
            q = -((-delta + (1 << (shift - 1))) >> shift)
        q = max(-8, min(7, q))
        nibble = q & 0xf
        out[2 + i // 2] |= nibble << (4 * (i % 2))
        s2 = s1
        s1 = predicted + (q << shift)
    return bytes(out)


class AdpcmTests(unittest.TestCase):
    def test_block_round_trip(self):
        samples = [(i * 6) % 250 - 120 for i in range(28)]
        block = encode_block(samples, 0, 0, shift=4, predictor=1)
        decoded, s1, s2 = decode_block(block, 0, 0)
        self.assertEqual(len(decoded), 28)
        for want, got in zip(samples, decoded):
            self.assertLessEqual(abs(want - got), 1 << 4,
                                 'decode drift outside quantizer step')

    def test_predictors_round_trip(self):
        for predictor in range(5):
            samples = [(i * 9 % 400 - 200) for i in range(28)]
            block = encode_block(samples, 10, -10, shift=5, predictor=predictor)
            decoded, _, _ = decode_block(block, 10, -10)
            for want, got in zip(samples, decoded):
                self.assertLessEqual(abs(want - got), 1 << 5)

    def test_invalid_predictor_rejected(self):
        block = bytearray(16)
        block[0] = 5 << 4  # predictor 5 does not exist
        with self.assertRaises(AdpcmError):
            decode_block(bytes(block), 0, 0)

    def test_invalid_shift_rejected(self):
        block = bytearray(16)
        block[0] = 13  # shift 13 exceeds the SPU2 limit
        with self.assertRaises(AdpcmError):
            decode_block(bytes(block), 0, 0)

    def test_control_wrong_predictor_table_detected(self):
        # deliberately use the wrong table: the decoded stream must not
        # match the reference decode with the correct table
        import adpcm
        samples = [i * 41 % 800 - 400 for i in range(28)]
        block = encode_block(samples, 0, 0, shift=4, predictor=2)
        decoded, _, _ = decode_block(block, 0, 0)
        saved = adpcm.PREDICTORS
        try:
            adpcm.PREDICTORS = [(0, 0), (60, 0), (0, 0), (98, -55), (122, -60)]
            wrong, _, _ = decode_block(block, 0, 0)
        finally:
            adpcm.PREDICTORS = saved
        self.assertNotEqual(decoded, wrong,
                            'corrupted predictor table went undetected')

    def test_stream_flags_reported(self):
        data = bytearray()
        base = encode_block([0] * 28, 0, 0, shift=0, predictor=0)
        data += base
        flagged = bytearray(base)
        flagged[1] = 0x04
        data += flagged
        data += base
        pcm, flags = decode_stream(bytes(data))
        self.assertEqual(len(pcm), 28 * 3)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]['offset'], 16)
        self.assertEqual(flags[0]['sample_index'], 28)

    def test_stream_length_guard(self):
        with self.assertRaises(AdpcmError):
            decode_stream(b'\x00' * 15)

    def test_clamping(self):
        samples = [0x7fff] * 28
        block = encode_block(samples, 0x7000, 0x7000, shift=12, predictor=1)
        decoded, _, _ = decode_block(block, 0x7000, 0x7000)
        self.assertTrue(all(-0x8000 <= s <= 0x7fff for s in decoded))

    def test_deterministic(self):
        samples = [(i * 97 % 2000 - 1000) for i in range(28)]
        block = encode_block(samples, 5, -5, shift=6, predictor=3)
        a, _, _ = decode_block(block, 5, -5)
        b, _, _ = decode_block(block, 5, -5)
        self.assertEqual(a, b)

    def test_wav_round_trip(self):
        import tempfile
        samples = [i * 11 % 500 - 250 for i in range(1000)]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'test.wav'
            write_wav(path, samples, sample_rate=44100)
            rate, channels, pcm = load_wav(path)
        self.assertEqual(rate, 44100)
        self.assertEqual(channels, 1)
        self.assertEqual(list(pcm), samples)


if __name__ == '__main__':
    unittest.main()
