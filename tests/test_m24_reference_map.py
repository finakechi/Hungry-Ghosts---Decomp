"""M24 read-only reference dependency map (tools/hg_audio/m24_reference_map.py)."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'hg_audio'))
import m24_reference_map  # noqa: E402

ELF_ENV = os.environ.get('HG_ELF', '')
EVIDENCE = ROOT / 'evidence' / 'm24' / 'reference-map.json'


class ReferenceMapSafetyTests(unittest.TestCase):
    def test_rejects_unauthenticated_input(self):
        with tempfile.NamedTemporaryFile() as fake:
            fake.write(b'\0' * 4096)
            fake.flush()
            with self.assertRaises(SystemExit):
                m24_reference_map.scan(fake.name)

    def test_committed_map_is_addresses_only(self):
        data = json.loads(EVIDENCE.read_text())
        self.assertEqual(data['elf_sha256'], m24_reference_map.ELF_SHA256)
        slots = {r['slot']: r['function'] for r in data['scheduler_registrations']
                 if r['function']}
        self.assertEqual(slots, {0: '002b3d70', 1: '002add90', 2: '002a96b8',
                                 3: '002b2438', 4: '002ad138'})
        table = data['tables']['sequence_status_table']['words']
        self.assertEqual((table[10], table[11], table[15]), ('002ae2c0', '002ae370', '002ae8d8'))


@unittest.skipUnless(ELF_ENV, 'retail-only: set HG_ELF to the authenticated SCPS_150.46')
class ReferenceMapRetailTests(unittest.TestCase):
    def test_fresh_scan_matches_committed_map(self):
        self.assertEqual(m24_reference_map.scan(ELF_ENV), json.loads(EVIDENCE.read_text()))


if __name__ == '__main__':
    unittest.main()
