"""Synthetic FLK5 inspection tests and negative controls."""

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))

from hg_content.flk5 import FLK5, inspect_flk5  # noqa: E402


def flk5_blob(entries, count=None, size_word=0x1100, reserved=0, pad=0):
    words = count if count is not None else len(entries)
    blob = bytearray(b'FLK5')
    blob += struct.pack('<3I', words, size_word, reserved)
    for entry in entries:
        blob += struct.pack('<I', entry)
    blob += b'\x00' * pad
    return bytes(blob)


class TestFLK5(unittest.TestCase):
    def test_valid_container(self):
        data = flk5_blob([0x09000020, 0x010002A0], pad=0x2A0)
        result = inspect_flk5(data, len(data))
        self.assertEqual(result['status'], 'parsed-validated')
        self.assertEqual(result['header']['count'], 2)
        self.assertEqual([e['type'] for e in result['entries']], [9, 1])
        self.assertEqual([e['encoded_offset'] for e in result['entries']],
                         [0x20, 0x2A0])

    def test_entry_out_of_bounds_is_malformed(self):
        data = flk5_blob([0x0600FFFF])
        result = inspect_flk5(data, len(data))
        self.assertEqual(result['status'], 'malformed')
        self.assertTrue(any(e['kind'] == 'entry-out-of-bounds'
                            for e in result.get('errors', [])))

    def test_count_exceeding_member_is_malformed(self):
        data = flk5_blob([], count=1000)
        result = inspect_flk5(data, len(data))
        self.assertEqual(result['status'], 'malformed')

    def test_unknown_entry_type_is_recognized_partial(self):
        data = flk5_blob([0x05000020, 0x09000030], pad=0x30)
        result = inspect_flk5(data, len(data))
        self.assertEqual(result['status'], 'recognized-partial')
        self.assertIn('unobserved entry types [5]', result['evidence'])

    def test_truncated_header_is_malformed(self):
        result = inspect_flk5(b'FLK5\x01\x00', 8)
        self.assertEqual(result['status'], 'malformed')

    def test_non_flk5_is_malformed(self):
        result = inspect_flk5(b'NOT5' + b'\x00' * 32, 36)
        self.assertEqual(result['status'], 'malformed')

    def test_header_fields_exact(self):
        data = flk5_blob([0x06000020], size_word=0x9A00, reserved=0xDEADBEEF)
        result = inspect_flk5(data, len(data))
        self.assertEqual(result['header']['size_word'], 0x9A00)
        self.assertEqual(result['header']['reserved_word'], 0xDEADBEEF)

    def test_raw_entry_word_preserved(self):
        data = flk5_blob([0x12345678])
        result = inspect_flk5(data, len(data))
        self.assertEqual(result['entries'][0]['raw'], 0x12345678)
        self.assertEqual(result['entries'][0]['type'], 0x12)
        self.assertEqual(result['entries'][0]['encoded_offset'], 0x345678)

    def test_flk5_class(self):
        data = flk5_blob([0x09000020])
        container = FLK5(data, len(data))
        self.assertEqual(container.count, 1)
        self.assertEqual(container.size_word, 0x1100)
        with self.assertRaises(ValueError):
            FLK5(b'xxxx' + b'\x00' * 12, 16)


if __name__ == '__main__':
    unittest.main()
