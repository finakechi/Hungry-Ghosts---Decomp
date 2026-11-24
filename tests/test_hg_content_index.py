"""Synthetic content-index tests: queries, determinism, no-text guarantees."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))

from hg_content.index import build_index, query  # noqa: E402


def fake_corpus():
    members = []
    scripts = []
    for row in (
        (0, 0, b'FLK5', 'parsed-validated', 'flk5', [6, 9], None),
        (0, 1, b'x', 'unknown', 'binary', [], None),
        (0, 2, b'\x00', 'parsed-validated', 'hgscript', [], 'ok'),
        (0, 3, b'x', 'recognized-partial', 'hgscript', [], 'unresolved'),
        (1, 0, b'x', 'recognized-partial', 'sound-sshd', [], None),
    ):
        d, m, magic, status, kind, types, binding = row
        classification = dict(status=status, kind=kind,
                              evidence='synthetic')
        if kind == 'flk5':
            classification['entries'] = [dict(type=t, raw=0, index=i,
                                              encoded_offset=0x20,
                                              status='in-bounds')
                                         for i, t in enumerate(types)]
            classification['details'] = dict(nested_flk5_children=[
                dict(entry_index=0, target_offset=0x20,
                     relationship='verified')])
        members.append(dict(dir=d, index=m, offset=100 * m,
                            size_bytes=10, flags=0, reserved=0,
                            sha256='a' * 64, magic_hex=magic.hex(),
                            classification=classification))
        if kind == 'hgscript':
            scripts.append(dict(dir=d, member=m, offset=100 * m,
                                size_bytes=10, status=status,
                                record_count=2, records_end=0x40,
                                event_count=5, odd_word_events=2,
                                pool_entries=2, pool_text_entries=2,
                                binding_status=binding or 'supported-inference-count-matched',
                                roundtrip='decoded',
                                roundtrip_raw_preserved=False,
                                opaque_records=0, layout_matches=True,
                                sha256='b' * 64))
    return dict(schema='hg-content-corpus/v1',
                source=dict(path='synthetic', sha256='c' * 64,
                            authenticated=True),
                pack=dict(directory_count=2, member_count=5,
                          nonzero_member_count=5, table_size_bytes=4096),
                classification_counts=dict(
                    **{'parsed-validated': 2, 'unknown': 1,
                       'recognized-partial': 2}),
                flk5_entry_type_counts={6: 1, 9: 1},
                nested_flk5_children=1,
                sound_members=[dict(dir=1, member=0, offset=100,
                                    size_bytes=10, sha256='d' * 64)],
                scripts=scripts,
                failures=[],
                members=members,
                coverage=dict(members_enumerated=5,
                              members_structurally_classified=4,
                              hgscript_candidates=2,
                              hgscript_decoded=2,
                              hgscript_roundtrip_decoded=2,
                              hgscript_roundtrip_raw_preserved=0,
                              hgscript_binding_count_matched=1,
                              hgscript_binding_unresolved=1,
                              unknown_members=1, malformed_members=0,
                              failed_members=0))


class TestIndex(unittest.TestCase):
    def setUp(self):
        self.doc = build_index(fake_corpus())

    def test_no_dialogue_text_in_index(self):
        text = json.dumps(self.doc)
        self.assertNotIn('"text"', text)
        for member in self.doc['members']:
            self.assertNotIn('text', member)
        for script in self.doc['scripts']:
            self.assertNotIn('text', script)

    def test_member_queries(self):
        rows = query(self.doc, 'members', status='unknown')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['kind'], 'binary')
        rows = query(self.doc, 'members', dir=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['kind'], 'sound-sshd')

    def test_flk5_query(self):
        rows = query(self.doc, 'flk5', entry_type=9)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['entry_types'], [6, 9])
        rows = query(self.doc, 'flk5', entry_type=3)
        self.assertEqual(rows, [])

    def test_script_queries(self):
        rows = query(self.doc, 'scripts', binding_status='unresolved')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['member'], 3)
        rows = query(self.doc, 'scripts', roundtrip='decoded')
        self.assertEqual(len(rows), 2)

    def test_event_and_string_metadata_queries(self):
        rows = query(self.doc, 'events')
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['total_events'], 5)
        rows = query(self.doc, 'strings')
        self.assertEqual([r['pool_entries'] for r in rows], [2, 2])
        self.assertTrue(all('sha256' in r for r in rows))

    def test_sound_query(self):
        rows = query(self.doc, 'sound')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['dir'], 1)

    def test_children_query(self):
        rows = query(self.doc, 'children')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['relationship'], 'verified')

    def test_unresolved_query(self):
        rows = query(self.doc, 'unresolved')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['binding_status'], 'unresolved')

    def test_stats_query(self):
        rows = query(self.doc, 'stats')
        self.assertEqual(len(rows), 3)

    def test_unknown_query_kind_rejected(self):
        with self.assertRaises(ValueError):
            query(self.doc, 'nonsense')

    def test_deterministic_build(self):
        again = build_index(fake_corpus())
        self.assertEqual(json.dumps(self.doc, sort_keys=True),
                         json.dumps(again, sort_keys=True))


if __name__ == '__main__':
    unittest.main()
