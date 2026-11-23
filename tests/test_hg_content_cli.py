"""CLI/corpus integration tests on synthetic images (no retail inputs)."""

import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))

from .hg_content_util import make_fixture_member, pack_with_members  # noqa: E402

CLI = Path(__file__).resolve().parent.parent / 'tools' / 'hg_content' / 'cli.py'


def run_cli(*args, cwd):
    return subprocess.run([sys.executable, '-B', str(CLI), *args],
                          cwd=cwd, capture_output=True, text=True)


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        script = make_fixture_member()
        blob, fp, iso = pack_with_members(
            [b'FLK5' + b'\x00' * 64, script, b'junk', None])
        self.image = self.dir / 'synth.iso'
        self.image.write_bytes(blob)
        self.sha256 = __import__('hashlib').sha256(blob).hexdigest()

    def tearDown(self):
        self.tmp.cleanup()

    def test_inventory_deterministic(self):
        out1, out2 = self.dir / 'inv1.json', self.dir / 'inv2.json'
        r1 = run_cli('inventory', '--pack-file', str(self.image),
                     '--output', str(out1), cwd=self.dir)
        r2 = run_cli('inventory', '--pack-file', str(self.image),
                     '--output', str(out2), cwd=self.dir)
        self.assertEqual(r1.returncode, 0, r1.stderr)
        self.assertEqual(out1.read_bytes(), out2.read_bytes())

    def test_inventory_output_exclusive(self):
        out = self.dir / 'inv.json'
        run_cli('inventory', '--pack-file', str(self.image), '--output', str(out),
                cwd=self.dir)
        r = run_cli('inventory', '--pack-file', str(self.image), '--output',
                    str(out), cwd=self.dir)
        self.assertNotEqual(r.returncode, 0)

    def test_extract_and_hash(self):
        out_dir = self.dir / 'out'
        r = run_cli('extract', '--pack-file', str(self.image), '--dir', '0',
                    '--member', '2', '--out-dir', str(out_dir), cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = (out_dir / 'd000_m002.bin').read_bytes()
        self.assertEqual(data, b'junk')
        import hashlib
        self.assertIn(hashlib.sha256(data).hexdigest(), r.stdout)

    def test_extract_path_traversal_rejected(self):
        out_dir = self.dir / 'out'
        for name in ('../evil.bin', 'a/b.bin', 'a\\b.bin', '..', '.', ''):
            r = run_cli('extract', '--pack-file', str(self.image), '--dir', '0',
                        '--member', '2', '--out-dir', str(out_dir),
                        '--name', name, cwd=self.dir)
            self.assertNotEqual(r.returncode, 0, name)
        self.assertFalse((self.dir / 'evil.bin').exists())

    def test_extract_symlink_outdir_rejected(self):
        real = self.dir / 'real'
        real.mkdir()
        link = self.dir / 'link'
        link.symlink_to(real)
        r = run_cli('extract', '--pack-file', str(self.image), '--dir', '0',
                    '--member', '2', '--out-dir', str(link), cwd=self.dir)
        self.assertNotEqual(r.returncode, 0)

    def test_extract_overwrite_refused(self):
        out_dir = self.dir / 'out'
        run_cli('extract', '--pack-file', str(self.image), '--dir', '0',
                '--member', '2', '--out-dir', str(out_dir), cwd=self.dir)
        r = run_cli('extract', '--pack-file', str(self.image), '--dir', '0',
                    '--member', '2', '--out-dir', str(out_dir), cwd=self.dir)
        self.assertNotEqual(r.returncode, 0)

    def test_script_decode_verify_rebuild_cycle(self):
        decoded = self.dir / 'dec.json'
        r = run_cli('script', 'decode', '--pack-file', str(self.image),
                    '--dir', '0', '--member', '1', '--no-text',
                    '--output', str(decoded), cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(decoded.read_text())
        self.assertEqual(doc['decoded']['status'], 'parsed-validated')
        self.assertTrue(doc['roundtrip'])
        # rebuild
        bin_out = self.dir / 'rebuilt.bin'
        r = run_cli('script', 'rebuild', str(decoded), '--output',
                    str(bin_out), '--expect-sha256',
                    __import__('hashlib').sha256(
                        make_fixture_member()).hexdigest(), cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(bin_out.read_bytes(), make_fixture_member())
        # verify
        rec = self.dir / 'verify.json'
        r = run_cli('script', 'verify', '--pack-file', str(self.image),
                    '--dir', '0', '--member', '1', '--output', str(rec),
                    cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        receipt = json.loads(rec.read_text())
        self.assertTrue(receipt['byte_exact'])

    def test_script_rebuild_wrong_hash_rejected(self):
        decoded = self.dir / 'dec.json'
        run_cli('script', 'decode', '--pack-file', str(self.image),
                '--dir', '0', '--member', '1', '--no-text',
                '--output', str(decoded), cwd=self.dir)
        r = run_cli('script', 'rebuild', str(decoded), '--output',
                    str(self.dir / 'x.bin'), '--expect-sha256', '0' * 64,
                    cwd=self.dir)
        self.assertNotEqual(r.returncode, 0)

    def test_corpus_authenticated_and_deterministic(self):
        out1, out2 = self.dir / 'corpus1.json', self.dir / 'corpus2.json'
        for out in (out1, out2):
            r = run_cli('corpus', '--pack-file', str(self.image),
                        '--output', str(out), '--expect-sha256', self.sha256,
                        cwd=self.dir)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(out1.read_bytes(), out2.read_bytes())
        doc = json.loads(out1.read_text())
        self.assertTrue(doc['source']['authenticated'])
        self.assertEqual(doc['pack']['member_count'], 4)
        self.assertEqual(doc['classification_counts']['unknown'], 2)
        self.assertEqual(doc['coverage']['hgscript_candidates'], 1)
        self.assertEqual(doc['coverage']['hgscript_roundtrip_decoded'], 1)

    def test_corpus_authentication_failure_detected(self):
        r = run_cli('corpus', '--pack-file', str(self.image), '--output',
                    str(self.dir / 'c.json'), '--expect-sha256', 'f' * 64,
                    cwd=self.dir)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('authentication failed', r.stderr + r.stdout)
        self.assertFalse((self.dir / 'c.json').exists())

    def test_corpus_reports_corrupted_script(self):
        # Corrupt a script record (no terminator): must round-trip as raw,
        # and be reported separately from fully-decoded members.
        script = bytearray(make_fixture_member())
        struct.pack_into('<I', script, 24, 0x1234)  # replace terminator
        blob, fp, iso = pack_with_members([bytes(script)])
        image = self.dir / 'corrupt.iso'
        image.write_bytes(blob)
        out = self.dir / 'corpus3.json'
        r = run_cli('corpus', '--pack-file', str(image), '--output', str(out),
                    cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(out.read_text())
        self.assertEqual(doc['coverage']['hgscript_roundtrip_decoded'], 0)
        self.assertEqual(doc['coverage']['hgscript_roundtrip_raw_preserved'], 1)
        self.assertEqual(doc['scripts'][0]['opaque_records'], 1)

    def test_corpus_failure_accounting(self):
        # A member whose extent overflows must be recorded as malformed,
        # never silently skipped (bounded by a directory-size pack).
        blob, fp, iso = pack_with_members([b'abcd'])
        bad = bytearray(blob)
        struct.pack_into('<I', bad, 64 + 4, 0x10000000)
        image = self.dir / 'bad.iso'
        image.write_bytes(bytes(bad))
        out = self.dir / 'corpus4.json'
        r = run_cli('corpus', '--pack-file', str(image), '--output', str(out),
                    cwd=self.dir)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('exceeds PACK', r.stderr + r.stdout)

    def test_index_build_and_query_cycle(self):
        corpus = self.dir / 'corpus.json'
        run_cli('corpus', '--pack-file', str(self.image), '--output',
                str(corpus), '--expect-sha256', self.sha256, cwd=self.dir)
        db = self.dir / 'index.json'
        r = run_cli('index', 'build', str(corpus), '--output', str(db),
                    cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(db.read_text())
        self.assertNotIn('"text"', json.dumps(doc))
        r = run_cli('index', 'query', str(db), '--kind', 'scripts', cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(json.loads(r.stdout)), 1)
        r = run_cli('index', 'query', str(db), '--kind', 'roundtrip',
                    cwd=self.dir)
        rows = json.loads(r.stdout)
        self.assertEqual(rows[0]['roundtrip'], 'decoded')

    def test_script_decode_rejects_non_script(self):
        r = run_cli('script', 'decode', '--pack-file', str(self.image),
                    '--dir', '0', '--member', '2', '--no-text', cwd=self.dir)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('not a recognized HGScript', r.stdout + r.stderr)


if __name__ == '__main__':
    unittest.main()
