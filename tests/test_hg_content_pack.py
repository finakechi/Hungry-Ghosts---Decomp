"""Synthetic tests for PACK archive processing (no retail inputs)."""

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))

from hg_content.pack import Pack, PackError  # noqa: E402

from .hg_content_util import build_pack, pack_with_members  # noqa: E402


def open_pack(blob):
    import io
    from .hg_content_util import FakeISO, read_at
    fp = io.BytesIO(blob)
    iso = FakeISO({'/DATA/PACK.DAT': dict(lba=0, size_bytes=len(blob))})
    return Pack(fp, iso, iso.files['/DATA/PACK.DAT'], read_at)


class TestPackParsing(unittest.TestCase):
    def test_valid_single_directory(self):
        blob, fp, iso = pack_with_members([b'ABCD', b'FLK5', None, b'x' * 100])
        from .hg_content_util import read_at
        pack = Pack(fp, iso, iso.files['/DATA/PACK.DAT'], read_at)
        counts = pack.member_counts()
        self.assertEqual(counts, dict(directory_count=1, member_count=4,
                                      nonzero_member_count=3,
                                      table_size_bytes=2048))
        members = list(pack.all_members())
        self.assertEqual([m.size_bytes for m in members], [4, 4, 0, 100])
        self.assertEqual([m.offset for m in members], [2048, 2052, 2056, 2056])
        self.assertEqual(pack.read_member(members[0]), b'ABCD')
        self.assertEqual(pack.read_member(members[2]), b'')
        self.assertEqual(pack.read_member(members[3], limit=5), b'xxxxx')
        self.assertEqual(pack.read_member_head(members[1]), b'FLK5')

    def test_member_exceeding_pack_rejected(self):
        blob = bytearray(build_pack([(1, [], [(100, 0, 0, None)])], [b'']))
        struct.pack_into('<I', blob, 64 + 0, 2048)       # offset
        struct.pack_into('<I', blob, 64 + 4, 100)        # length beyond pack
        with self.assertRaises(PackError):
            open_pack(bytes(blob))

    def test_member_overlapping_directory_rejected(self):
        blob, _fp, _iso = pack_with_members([b'abcd'])
        struct.pack_into('<I', blob, 64 + 0, 4)          # inside the table
        with self.assertRaises(PackError):
            open_pack(bytes(blob))

    def test_truncated_directory_rejected(self):
        blob = bytearray(build_pack([(1, [], [(4, 0, 0, None)])], [b'']))
        with self.assertRaises(ValueError):
            open_pack(blob[:-100])

    def test_invalid_ident_rejected(self):
        blob = bytearray(build_pack([(1, [], [(4, 0, 0, None)])], [b'']))
        struct.pack_into('<I', blob, 0, 99)
        with self.assertRaises(PackError):
            open_pack(bytes(blob))

    def test_absurd_count_rejected(self):
        blob = bytearray(build_pack([(1, [], [(4, 0, 0, None)])], [b'']))
        struct.pack_into('<I', blob, 4, 200)
        with self.assertRaises(PackError):
            open_pack(bytes(blob))

    def test_extent_overflow_rejected(self):
        # offset + length wraps 32 bits; must be caught before any read.
        blob = bytearray(build_pack([(1, [], [(4, 0, 0, None)])], [b'']))
        struct.pack_into('<I', blob, 64 + 0, 0xFFFFF000)
        struct.pack_into('<I', blob, 64 + 4, 0x00002000)
        with self.assertRaises(PackError):
            open_pack(bytes(blob))

    def test_deterministic_inventory(self):
        blob, fp, iso = pack_with_members([b'FLK5', b'other', None])
        from .hg_content_util import read_at
        results = []
        for _ in range(2):
            pack = Pack(fp, iso, iso.files['/DATA/PACK.DAT'], read_at)
            results.append(json.dumps(
                [m.record() for m in pack.all_members()], sort_keys=True))
            fp.seek(0)
        self.assertEqual(results[0], results[1])

    def test_member_range_checks(self):
        blob, fp, iso = pack_with_members([b'0123456789'])
        from .hg_content_util import read_at
        pack = Pack(fp, iso, iso.files['/DATA/PACK.DAT'], read_at)
        member = next(pack.all_members())
        self.assertEqual(pack.read_member_range(member, 2, 4), b'2345')
        with self.assertRaises(ValueError):
            pack.read_member_range(member, 8, 4)
        with self.assertRaises(ValueError):
            pack.read_member_range(member, -1, 4)
        with self.assertRaises(ValueError):
            pack.read_member(member, limit=-1)

    def test_multi_directory(self):
        specs = [(1, [], [(40, 0, 0, 0)]),
                 (2, [], [(8, 0, 0, 1), (4, 0, 0, 2)])]
        blob = build_pack(specs, [b'a' * 40, b'bbbbbbbb', b'cccc'])
        pack = open_pack(blob)
        counts = pack.member_counts()
        self.assertEqual(counts['directory_count'], 2)
        self.assertEqual(counts['member_count'], 3)
        members = list(pack.all_members())
        self.assertEqual([(m.directory, m.index, m.offset) for m in members],
                         [(0, 0, 4096), (1, 0, 4136), (1, 1, 4144)])


if __name__ == '__main__':
    unittest.main()
