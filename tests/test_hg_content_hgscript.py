"""Synthetic HGScript decode/encode tests and negative controls."""

import json
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))

from hg_content.hgscript import (Collection, Event, Record, ScriptError,  # noqa: E402
                                 StringEntry, decode, encode,
                                 looks_like_hgscript, parse_pool,
                                 parse_record, stored_layout_matches)

from .hg_content_util import make_fixture_member  # noqa: E402


def refs(collection):
    return sum(1 for r in collection.records for e in r.events
               if e.role == 'odd-word')


class TestDecode(unittest.TestCase):
    def test_fixture_round_trip(self):
        data = make_fixture_member()
        hit, evidence = looks_like_hgscript(data)
        self.assertTrue(hit, evidence)
        collection = decode(data)
        self.assertEqual(collection.status, 'parsed-validated')
        self.assertEqual(len(collection.records), 2)
        self.assertEqual(refs(collection), 3)
        self.assertEqual(len(collection.pool), 3)
        self.assertEqual(collection.binding_status,
                         'supported-inference-count-matched')
        self.assertEqual(encode(collection), data)
        self.assertTrue(stored_layout_matches(collection))

    def test_header_fields_preserved(self):
        data = make_fixture_member()
        collection = decode(data)
        self.assertEqual(collection.count, 2)
        self.assertEqual(collection.offsets, [16, 28])
        self.assertEqual(collection.records_end, 48)

    def test_event_values_and_order_preserved(self):
        events = [[0x28C, 0x301, 2, 0x681], [0x14C, 0x1C1, 0x0C, 0x81]]
        data = make_fixture_member(events=events, strings=('a', 'b', 'c', 'd'))
        collection = decode(data)
        self.assertEqual([[e.value for e in r.events] for r in collection.records],
                         events)
        self.assertEqual([[e.role for e in r.events] for r in collection.records],
                         [['even-word', 'odd-word', 'even-word', 'odd-word'],
                          ['even-word', 'odd-word', 'even-word', 'odd-word']])
        self.assertEqual([e.bound_pool_index for e in collection.records[0].events],
                         [None, 0, None, 1])
        self.assertEqual([e.bound_pool_index for e in collection.records[1].events],
                         [None, 2, None, 3])

    def test_unknown_event_values_preserved(self):
        # Events that match no known catalog must survive verbatim.
        data = make_fixture_member(events=[[0xAB12, 0xCD34, 0xEF56]])
        collection = decode(data)
        self.assertEqual([e.value for e in collection.records[0].events],
                         [0xAB12, 0xCD34, 0xEF56])
        self.assertEqual(encode(collection), data)

    def test_truncated_member_rejected(self):
        data = make_fixture_member()
        with self.assertRaises(ScriptError):
            decode(data[:20])

    def test_negative_count_rejected(self):
        data = make_fixture_member()
        bad = bytearray(data)
        struct.pack_into('<I', bad, 0, 0)
        with self.assertRaises(ScriptError):
            decode(bytes(bad))

    def test_implausible_count_rejected(self):
        data = make_fixture_member()
        bad = bytearray(data)
        struct.pack_into('<I', bad, 0, 0xFFFFFF)
        with self.assertRaises(ScriptError):
            decode(bytes(bad))

    def test_records_end_outside_member_rejected(self):
        data = make_fixture_member()
        bad = bytearray(data)
        struct.pack_into('<I', bad, 4, len(data) + 4)
        with self.assertRaises(ScriptError):
            decode(bytes(bad))

    def test_decreasing_offsets_rejected(self):
        data = make_fixture_member()
        bad = bytearray(data)
        struct.pack_into('<I', bad, 12, 0xFFFFFF)
        with self.assertRaises(ScriptError):
            decode(bytes(bad))

    def test_first_offset_not_table_size_rejected(self):
        data = make_fixture_member()
        bad = bytearray(data)
        struct.pack_into('<I', bad, 8, 12)
        with self.assertRaises(ScriptError):
            decode(bytes(bad))

    def test_last_record_empty_rejected(self):
        data = make_fixture_member()
        bad = bytearray(data)
        struct.pack_into('<I', bad, 12, 48)  # last offset == records_end
        with self.assertRaises(ScriptError):
            decode(bytes(bad))


class TestOpaqueRecords(unittest.TestCase):
    def test_record_without_terminator_stays_opaque_and_round_trips(self):
        # Record 0 terminates normally; record 1 has no terminator.
        records = bytearray()
        for word in [0x0C, 0x81, 0]:
            records += struct.pack('<I', word)
        for word in [0x14C, 0x1C1, 0x28C]:
            records += struct.pack('<I', word)
        blob2 = bytearray()
        blob2 += struct.pack('<2I', 2, 16 + len(records))
        blob2 += struct.pack('<2I', 16, 28)
        blob2 += records
        blob2 += struct.pack('<I', 0x30) + 'はい'.encode('shift_jis') + b'\x00\x00'
        collection2 = decode(bytes(blob2))
        self.assertEqual(collection2.records[1].status, 'opaque')
        self.assertEqual(collection2.status, 'recognized-partial')
        self.assertEqual(encode(collection2), bytes(blob2))

    def test_nonzero_after_terminator_is_opaque(self):
        records = bytearray()
        records += struct.pack('<I', 0x0C)
        records += struct.pack('<I', 0)      # terminator
        records += struct.pack('<I', 0x77)   # nonzero padding -> opaque
        blob = bytearray()
        blob += struct.pack('<2I', 1, 16)
        blob += struct.pack('<I', 12)
        blob += records
        blob += b'\x00' * 8
        collection = decode(bytes(blob))
        self.assertEqual(collection.records[0].status, 'opaque')
        self.assertEqual(collection.status, 'recognized-partial')
        self.assertEqual(encode(collection), bytes(blob))

    def test_odd_span_is_opaque(self):
        data = make_fixture_member()
        bad = bytearray(data)
        struct.pack_into('<I', bad, 4, 15)  # records_end odd -> last span odd
        with self.assertRaises(ScriptError):
            decode(bytes(bad))


class TestPool(unittest.TestCase):
    def test_pool_split_and_text(self):
        data = make_fixture_member(strings=('テスト一', 'テスト二'))
        collection = decode(data)
        self.assertEqual([e.text for e in collection.pool],
                         ['テスト一', 'テスト二'])
        self.assertEqual(collection.pool[0].header_word, 0x30)
        self.assertTrue(all(e.terminated for e in collection.pool))
        self.assertEqual(encode(collection), data)

    def test_ascii_mixed_text_valid(self):
        raw = 'ＰＳ２？'.encode('shift_jis')
        pool = bytearray(struct.pack('<I', 0x48) + raw + b'\x00\x00')
        entries, anomalies, lead, tail = parse_pool(bytes(pool), 0)
        self.assertEqual(anomalies, 0)
        self.assertEqual(entries[0].text, 'ＰＳ２？')

    def test_non_text_pool_entry_counted_but_preserved(self):
        # bytes that are not valid SJIS-with-ASCII must still round-trip
        pool = bytearray(struct.pack('<I', 0x10) + b'\xff\xfe\xfd\xfc' + b'\x00\x00')
        entries, anomalies, _lead, _tail = parse_pool(bytes(pool), 0)
        self.assertEqual(anomalies, 1)
        collection = Collection()
        collection.count = 1
        collection.records_end = 16
        collection.offsets = [12]
        record = Record(12, 4)
        record.status = 'opaque'
        record.raw = b'\x00\x00\x00\x00'
        collection.records = [record]
        collection.pool = entries
        collection.status = 'recognized-partial'
        rebuilt = encode(collection)
        self.assertEqual(rebuilt[16:], bytes(pool))

    def test_unterminated_final_entry_round_trips(self):
        data = make_fixture_member()
        cut = data.rstrip(b'\x00')  # remove every trailing NUL/pad byte
        collection = decode(cut)
        self.assertFalse(collection.pool[-1].terminated)
        self.assertEqual(encode(collection), cut)

    def test_leading_and_trailing_pool_padding_preserved(self):
        data = make_fixture_member()
        padded = data + b'\x00\x00\x00\x00'
        collection = decode(padded)
        # Single-NUL split: the second NUL of the fixture terminator and the
        # four appended zeros all land in the last entry's padding.
        self.assertEqual(collection.pool[-1].pad_bytes, b'\x00' * 5)
        self.assertEqual(collection.pool_tail_pad, b'')
        self.assertEqual(encode(collection), padded)


class TestBinding(unittest.TestCase):
    def test_count_mismatch_is_unresolved(self):
        data = make_fixture_member(strings=('a', 'b', 'c', 'd'))
        collection = decode(data)
        self.assertEqual(refs(collection), 3)
        self.assertEqual(len(collection.pool), 4)
        self.assertEqual(collection.binding_status, 'unresolved')
        self.assertTrue(all(e.bound_pool_index is None
                            for r in collection.records for e in r.events))

    def test_empty_script(self):
        blob = bytearray()
        blob += struct.pack('<2I', 1, 16)
        blob += struct.pack('<I', 12)
        blob += struct.pack('<I', 0)
        collection = decode(bytes(blob))
        self.assertEqual(collection.binding_status, 'empty')
        self.assertEqual(collection.status, 'recognized-partial')


class TestControlledEdits(unittest.TestCase):
    def test_string_edit_produces_valid_script(self):
        data = make_fixture_member()
        collection = decode(data)
        collection.pool[0].text_bytes = '修正済み'.encode('shift_jis')
        collection.pool[0].header_word = 12 * len(collection.pool[0].text_bytes)
        edited = encode(collection)
        self.assertNotEqual(edited, data)
        redecoded = decode(edited)
        self.assertEqual(redecoded.pool[0].text, '修正済み')
        self.assertEqual(encode(redecoded), edited)
        # Record region unchanged: only pool bytes differ.
        self.assertEqual(edited[:collection.records_end],
                         data[:collection.records_end])

    def test_event_edit_recomputes_offsets(self):
        data = make_fixture_member()
        collection = decode(data)
        collection.records[0].events.append(Event(0xFFFF, 0))
        edited = encode(collection)
        self.assertNotEqual(edited, data)
        self.assertFalse(stored_layout_matches(collection))
        redecoded = decode(edited)
        self.assertEqual(len(redecoded.records[0].events), 3)
        self.assertEqual(encode(redecoded), edited)
        self.assertTrue(stored_layout_matches(redecoded))

    def test_corrupted_decoder_output_diverges(self):
        # Deliberate corruption: wrong padding word count must change bytes.
        data = make_fixture_member()
        collection = decode(data)
        collection.records[0].padding_words += 1
        edited = encode(collection)
        self.assertNotEqual(edited, data)
        # And the corrupted rebuild must still be structurally readable.
        self.assertEqual(decode(edited).status, 'parsed-validated')

    def test_refusal_on_unparsed(self):
        collection = Collection()
        with self.assertRaises(ScriptError):
            encode(collection)

    def test_refusal_on_record_count_mismatch(self):
        data = make_fixture_member()
        collection = decode(data)
        collection.records = collection.records[:1]
        with self.assertRaises(ScriptError):
            encode(collection)


class TestDetection(unittest.TestCase):
    def test_negative_controls(self):
        # Random bytes must not be recognized.
        hit, _ = looks_like_hgscript(b'\x01\x02\x03\x04' * 100)
        self.assertFalse(hit)
        hit, _ = looks_like_hgscript(b'FLK5' + b'\x00' * 100)
        self.assertFalse(hit)
        hit, _ = looks_like_hgscript(b'')
        self.assertFalse(hit)
        # Valid header but no pool: collection-only, not a script.
        blob = bytearray(struct.pack('<2I', 1, 16) + struct.pack('<I', 12)
                         + struct.pack('<I', 0) + b'\x01\x02\x03\x04')
        hit, evidence = looks_like_hgscript(bytes(blob))
        self.assertFalse(hit)


if __name__ == '__main__':
    unittest.main()
