"""Synthetic recovery, corruption, isolation and publication-gate tests."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import hg_backup as b
from hg_publication_audit import audit


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'; self.repo.mkdir()
        self.git('init', '-q'); self.git('config', 'user.name', 'Synthetic')
        self.git('config', 'user.email', 'synthetic@example.invalid')
        (self.repo / '.gitignore').write_text('/local/\n')
        (self.repo / 'source.c').write_text('/* synthetic */\n')
        self.git('add', '.'); self.git('commit', '-qm', 'fixture')
        self.source = self.repo / 'local' / 'evidence'; self.source.mkdir(parents=True)
        (self.source / 'capture.json').write_text('{"synthetic": true}\n')
        self.config = self.repo / 'local' / 'config.json'
        self.cfg = {'repo': str(self.repo), 'sources': [{'label': 'evidence', 'path': 'local/evidence'}]}
        self.save_config()

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.repo, stderr=subprocess.DEVNULL)

    def save_config(self):
        self.config.write_text(json.dumps(self.cfg))

    def snapshot(self):
        return Path(b.create(self.config)['snapshot'])

    def test_dry_run_has_no_output_and_excludes_media(self):
        (self.source / 'game.iso').write_bytes(b'synthetic media')
        (self.source / 'bios.bin').write_bytes(b'synthetic bios')
        (self.source / 'bios-link').symlink_to(self.root / 'missing')
        result = b.create(self.config, True)
        self.assertEqual(1, result['source_files'])
        self.assertEqual(3, len(result['excluded']))
        self.assertFalse((self.repo / 'local/backups').exists())

    def test_unique_snapshots_verify_and_restore_bundle(self):
        first = self.snapshot(); trusted = b.digest(first / 'manifest.json')
        second = self.snapshot()
        self.assertNotEqual(first, second)
        self.assertEqual(trusted, b.digest(first / 'manifest.json'))
        result = b.restore(first / 'manifest.json', first / 'snapshot.tar.gz', self.root / 'restore')
        self.assertGreaterEqual(result['restored_files'], 4)
        clone = self.root / 'clone'
        subprocess.run(['git', 'clone', '-q', str(self.root / 'restore/git/history.bundle'), str(clone)], check=True)
        self.assertEqual(self.git('rev-parse', 'HEAD'), subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=clone))

    def test_changed_archive_rejected(self):
        p = self.snapshot()
        with (p / 'snapshot.tar.gz').open('ab') as f: f.write(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'Archive size/hash'):
            b.verify(p / 'manifest.json', p / 'snapshot.tar.gz')

    def rewrite_archive(self, mutate):
        p = self.snapshot(); m = json.loads((p / 'manifest.json').read_text())
        contents = {n: (p / 'data' / n).read_bytes() for n in m['files']}
        entries = [(n, v, tarfile.REGTYPE) for n, v in contents.items()]
        entries = mutate(entries)
        (p / 'snapshot.tar.gz').unlink()
        with tarfile.open(p / 'snapshot.tar.gz', 'w:gz') as tf:
            for name, value, kind in entries:
                member = tarfile.TarInfo(name); member.size = len(value); member.type = kind
                if kind == tarfile.SYMTYPE: member.linkname = '../outside'
                tf.addfile(member, io.BytesIO(value))
        m['archive'].update(size=(p / 'snapshot.tar.gz').stat().st_size, sha256=b.digest(p / 'snapshot.tar.gz'))
        (p / 'manifest.json').write_text(json.dumps(m))
        return p

    def assert_bad_archive(self, mutate, message):
        p = self.rewrite_archive(mutate)
        with self.assertRaisesRegex(ValueError, message):
            b.restore(p / 'manifest.json', p / 'snapshot.tar.gz', self.root / 'restore')
        self.assertFalse((self.root / 'restore').exists())

    def test_missing_member(self):
        self.assert_bad_archive(lambda e: e[1:], 'Missing archive')

    def test_member_hash_mismatch(self):
        def mutate(e):
            n, v, k = e[0]; e[0] = (n, b'X' * len(v), k); return e
        self.assert_bad_archive(mutate, 'Member hash')

    def test_duplicate_member(self):
        self.assert_bad_archive(lambda e: e + e[:1], 'duplicate')

    def test_traversal_member(self):
        self.assert_bad_archive(lambda e: e + [('../outside', b'X', tarfile.REGTYPE)], 'Unsafe member')

    def test_link_member(self):
        self.assert_bad_archive(lambda e: e + [('link', b'', tarfile.SYMTYPE)], 'nonregular')

    def test_existing_restore_refused(self):
        p = self.snapshot(); dest = self.root / 'restore'; dest.mkdir()
        (dest / 'untouched').write_text('original')
        with self.assertRaises(FileExistsError): b.restore(p / 'manifest.json', p / 'snapshot.tar.gz', dest)
        self.assertEqual('original', (dest / 'untouched').read_text())

    def test_source_change_during_copy_leaves_incomplete(self):
        copy = b.shutil.copyfile
        def change(src, dst):
            result = copy(src, dst)
            if Path(src) == self.source / 'capture.json': Path(src).write_text('changed')
            return result
        with patch.object(b.shutil, 'copyfile', change), self.assertRaisesRegex(ValueError, 'Source set changed'):
            b.create(self.config)
        markers = list((self.repo / 'local/backups').glob('*/.incomplete'))
        self.assertEqual(1, len(markers))

    def test_database_lock_and_open_file_rejected(self):
        (self.source / 'project.lock').touch()
        with self.assertRaisesRegex(ValueError, 'lock'): b.closed_database(self.source)
        (self.source / 'project.lock').unlink()
        with (self.source / 'capture.json').open() as f:
            with self.assertRaisesRegex(ValueError, 'Open database'): b.closed_database(self.source)
            self.assertFalse(f.closed)

    def test_missing_source_and_overlapping_output_rejected(self):
        self.cfg['sources'][0]['path'] = 'local/missing'; self.save_config()
        with self.assertRaisesRegex(ValueError, 'Missing'): b.create(self.config, True)
        self.cfg['sources'][0]['path'] = 'local'; self.save_config()
        with self.assertRaisesRegex(ValueError, 'isolated'): b.create(self.config, True)

    def test_legacy_recovery_mismatch(self):
        recovery = self.repo / 'local/recovery'; (recovery / 'logs').mkdir(parents=True)
        (recovery / 'logs/a.txt').write_text('synthetic')
        (recovery / 'manifest.json').write_text(json.dumps({'logs': {'files': 1, 'bytes': 9, 'sha256': {'a.txt': '0' * 64}}}))
        with self.assertRaisesRegex(ValueError, 'Recovery missing'): b.validate_recovery(recovery)

    def test_remote_existing_snapshot_refused_before_mutation(self):
        snapshot = self.snapshot()
        self.cfg['rclone'] = {'remote': 'synthetic', 'folder_id': 'synthetic-folder'}
        self.save_config()
        snapshot_id = json.loads((snapshot / 'manifest.json').read_text())['snapshot_id']
        responses = [json.dumps({'synthetic': {'type': 'drive'}}).encode(),
                     json.dumps([{'ID': 'existing', 'Name': snapshot_id}]).encode()]
        with patch.object(b.subprocess, 'check_output', side_effect=responses), patch.object(b.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'already exists'): b.rclone(self.config, snapshot, upload=True)
            run.assert_not_called()

    def test_non_drive_backend_refused_before_mutation(self):
        snapshot = self.snapshot()
        self.cfg['rclone'] = {'remote': 'synthetic', 'folder_id': 'synthetic-folder'}
        self.save_config()
        with patch.object(b.subprocess, 'check_output', return_value=b'{"synthetic":{"type":"local"}}'), patch.object(b.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'native Google Drive'): b.rclone(self.config, snapshot, upload=True)
            run.assert_not_called()

    def test_historical_binary_and_secret_still_detected(self):
        base = self.git('rev-parse', 'HEAD').decode().strip()
        (self.repo / 'payload.bin').write_bytes(b'\0synthetic')
        (self.repo / 'secret.txt').write_text('ghp_' + 'X' * 30)
        self.git('add', '.'); self.git('commit', '-qm', 'bad old commit')
        self.git('rm', '-q', 'payload.bin', 'secret.txt'); self.git('commit', '-qm', 'remove from tip')
        report = audit(self.repo, base)
        self.assertTrue(any(v['kind'] == 'GitHub credential' for v in report['findings']))
        self.assertTrue(any(v['kind'] == 'binary/non-UTF8' for v in report['findings']))
        self.assertEqual(2, len(report['commits']))


if __name__ == '__main__': unittest.main()
