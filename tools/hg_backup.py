#!/usr/bin/env python3
"""Private, additive recovery snapshots. No credentials, sync, deletion or scheduler."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import uuid

SCHEMA = 'hg-private-backup/v1'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        before = os.fstat(f.fileno())
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
        after = os.fstat(f.fileno())
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('File changed while hashing: ' + str(path))
    return h.hexdigest()


def write_json(path, value):
    with Path(path).open('x', encoding='utf-8') as f:
        json.dump(value, f, indent=2, sort_keys=True)
        f.write('\n')


def safe_name(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or '..' in p.parts or '\\' in name or str(p) != name:
        raise ValueError('Unsafe member name: ' + name)
    return name


def closed_database(root):
    """Fail closed on locks or owner processes with database files open."""
    root = Path(root).resolve()
    if any(root.rglob('*.lock')) or any(root.rglob('*.lock~')):
        raise ValueError('Ghidra lock found: close the project normally')
    unreadable = []
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            argv = (proc / 'cmdline').read_bytes().split(b'\0')
            if any(b'ghidra.Ghidra' in a or b'ghidra.app.util.headless.AnalyzeHeadless' in a for a in argv):
                raise ValueError('Ghidra is running; close it before database backup')
            for fd in (proc / 'fd').iterdir():
                try:
                    target = fd.resolve()
                except OSError:
                    continue
                if target == root or root in target.parents:
                    raise ValueError('Open database file: ' + str(fd))
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError as e:
            # Desktop privilege-separated processes may deny fd inspection.
            # Require readable command identity; never ignore an unknown JVM.
            try:
                argv = (proc / 'cmdline').read_bytes().split(b'\0')
                identity = (proc / 'comm').read_text().strip()
            except OSError:
                raise ValueError('Cannot inspect process identity for database safety') from e
            if not argv or any(b'java' in a.lower() or b'ghidra' in a.lower() for a in argv):
                raise ValueError('Cannot inspect possible Ghidra process') from e
            unreadable.append({'pid': int(proc.name), 'command': identity})
    return {'locks': 0, 'open_database_files': 0, 'unreadable_non_java_processes': unreadable,
            'limit': 'Closed-project checks plus stable pre/post hashes; not an application transaction or filesystem freeze.'}


def blocked(path):
    name = path.name.lower()
    return (path.suffix.lower() in {'.iso', '.chd', '.elf', '.irx', '.bin'}
            or name in {'pack.dat', 'stream.dat', '.env', 'rclone.conf'}
            or name.startswith('scps_') or 'bios' in [s.lower() for s in path.parts]
            or 'extracted' in [s.lower() for s in path.parts])


def inventory(root, exclude=True):
    """Never follow links or include media/extraction payloads."""
    root = Path(root)
    if root.is_symlink() or not root.exists():
        raise ValueError('Missing or linked source: ' + str(root))
    found, excluded = {}, []
    paths = [root] if root.is_file() else sorted(root.rglob('*'))
    for p in paths:
        name = p.name if root.is_file() else p.relative_to(root).as_posix()
        safe_name(name)
        if p.is_symlink():
            excluded.append({'path': name, 'reason': 'symlink; target never read'})
        elif p.is_dir():
            continue
        elif not stat.S_ISREG(p.stat().st_mode):
            raise ValueError('Unsupported special file: ' + str(p))
        elif exclude and blocked(p):
            excluded.append({'path': name, 'reason': 'media, BIOS, extracted input or credential config'})
        else:
            found[name] = {'size': p.stat().st_size, 'sha256': digest(p)}
    if not found:
        raise ValueError('Source contains no eligible regular files: ' + str(root))
    return found, excluded


def validate_recovery(root):
    """Validate the legacy M3 recovery manifest, bundle and all declared groups."""
    root = Path(root)
    record = json.loads((root / 'manifest.json').read_text())
    total = 0
    for group, entry in record.items():
        safe_name(group)
        source = root / group
        actual, excluded = inventory(source, exclude=False)
        # Legacy manifests intentionally omit BIOS symlinks; never dereference them.
        expected = entry['sha256']
        if {n: v['sha256'] for n, v in actual.items()} != expected:
            raise ValueError('Recovery missing, extra or mismatched file: ' + group)
        if len(actual) != entry['files'] or sum(v['size'] for v in actual.values()) != entry['bytes']:
            raise ValueError('Recovery count/size mismatch: ' + group)
        total += len(actual)
    with tempfile.TemporaryDirectory(prefix='hg-bundle-check-') as directory:
        subprocess.run(['git', 'init', '--bare', '-q', directory], check=True, capture_output=True)
        subprocess.run(['git', '-C', directory, 'bundle', 'verify', str((root / 'history.bundle').resolve())], check=True, capture_output=True)
    if not (root / 'source.tar').is_file():
        raise ValueError('Recovery source archive missing')
    return {'groups': len(record), 'files': total, 'bundle_verified': True,
            'legacy_limit': 'Historical manifest hashes grouped files, not bundle/source.tar; current snapshot hashes both.'}


def resolve_config(config_path):
    cfg = json.loads(Path(config_path).read_text())
    repo = Path(cfg.get('repo', '.')).resolve()
    if not (repo / '.git').exists():
        raise ValueError('Expected a Git repository')
    sources = []
    for entry in cfg['sources']:
        label = entry['label']
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', label) or label == 'git':
            raise ValueError('Invalid/reserved source label')
        path = Path(entry['path'])
        path = path if path.is_absolute() else repo / path
        if path.is_symlink():
            raise ValueError('Linked source root')
        path = path.resolve()
        if repo not in path.parents or not path.is_relative_to(repo / 'local'):
            raise ValueError('Private sources must be beneath repository local/')
        sources.append((label, path, entry.get('kind', 'files')))
    if len({s[0] for s in sources}) != len(sources):
        raise ValueError('Duplicate source labels')
    return cfg, repo, sources


def create(config_path, dry_run=False):
    cfg, repo, sources = resolve_config(config_path)
    dest = (repo / cfg.get('snapshot_root', 'local/backups')).resolve()
    if not dest.is_relative_to(repo / 'local') or any(dest == p or p in dest.parents or dest in p.parents for _, p, _ in sources):
        raise ValueError('Snapshot output must be isolated beneath local/')
    checks, inventories, exclusions = {}, {}, []
    for label, path, kind in sources:
        if kind == 'ghidra':
            checks[label] = closed_database(path)
        if kind == 'recovery':
            checks[label] = validate_recovery(path)
        inventories[label], skipped = inventory(path)
        exclusions.extend(dict(source=label, **v) for v in skipped)
    media = []
    for name in cfg.get('inventory_only', []):
        path = (repo / name).resolve()
        if not path.exists():
            raise ValueError('Missing inventory-only path: ' + name)
        for f in ([path] if path.is_file() else sorted(path.rglob('*'))):
            if f.is_symlink():
                media.append({'path': str(f), 'reason': 'inventory-only symlink'})
            elif f.is_file():
                media.append({'path': str(f), 'size': f.stat().st_size, 'sha256': digest(f), 'reason': 'inventory only; never uploaded'})
    result = {'source_files': sum(map(len, inventories.values())),
              'source_bytes': sum(v['size'] for rows in inventories.values() for v in rows.values()),
              'excluded': exclusions, 'inventory_only': media, 'recovery_checks': checks}
    if dry_run:
        return dict(result, dry_run=True)
    snapshot_id = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:12]
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o700)
    target = dest / snapshot_id
    target.mkdir(mode=0o700)
    (target / '.incomplete').touch(exist_ok=False)
    data = target / 'data'
    data.mkdir(mode=0o700)
    files = {}
    for label, source, kind in sources:
        for name, entry in inventories[label].items():
            p = data / label / name
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source if source.is_file() else source / name, p)
            os.chmod(p, 0o600)
            if p.stat().st_size != entry['size'] or digest(p) != entry['sha256']:
                raise ValueError('Source/copy mismatch: ' + label + '/' + name)
            files[label + '/' + name] = entry
        if inventory(source)[0] != inventories[label]:
            raise ValueError('Source set changed during copy: ' + label)
        if kind == 'ghidra':
            closed_database(source)
    git_dir = data / 'git'
    git_dir.mkdir()
    bundle = git_dir / 'history.bundle'
    subprocess.run(['git', 'bundle', 'create', str(bundle), '--all'], cwd=repo, check=True, capture_output=True)
    subprocess.run(['git', 'bundle', 'verify', str(bundle)], cwd=repo, check=True, capture_output=True)
    files['git/history.bundle'] = {'size': bundle.stat().st_size, 'sha256': digest(bundle)}
    # Include dirty/new public files as well as committed history.
    names = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=repo).decode().split('\0')
    for name in filter(None, names):
        safe_name(name)
        source = repo / name
        if source.is_symlink() or not source.is_file() or blocked(Path(name)) or name.startswith('local/'):
            raise ValueError('Unexpected public working-tree file: ' + name)
        before = digest(source)
        p = git_dir / 'working-tree' / name
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, p)
        if digest(p) != before or digest(source) != before:
            raise ValueError('Working tree changed during backup')
        files['git/working-tree/' + name] = {'size': p.stat().st_size, 'sha256': before}
    archive = target / 'snapshot.tar.gz'
    with tarfile.open(archive, 'x:gz', dereference=False) as tf:
        for name in sorted(files):
            tf.add(data / name, arcname=name, recursive=False)
    manifest = dict(result, schema=SCHEMA, snapshot_id=snapshot_id, files=files,
                    file_count=len(files), total_bytes=sum(v['size'] for v in files.values()),
                    head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo).decode().strip(),
                    refs=subprocess.check_output(['git', 'show-ref'], cwd=repo).decode().splitlines(),
                    status=subprocess.check_output(['git', 'status', '--porcelain'], cwd=repo).decode().splitlines(),
                    archive={'name': archive.name, 'size': archive.stat().st_size, 'sha256': digest(archive)})
    write_json(target / 'manifest.json', manifest)
    verify(target / 'manifest.json', archive)
    (target / '.incomplete').unlink()
    return {'snapshot': str(target), 'file_count': len(files), 'total_bytes': manifest['total_bytes'], 'archive': manifest['archive']}


def load_manifest(path):
    m = json.loads(Path(path).read_text())
    if m.get('schema') != SCHEMA or not m.get('files'):
        raise ValueError('Unsupported/empty backup manifest')
    for name, row in m['files'].items():
        safe_name(name)
        if not isinstance(row['size'], int) or row['size'] < 0 or not re.fullmatch('[a-f0-9]{64}', row['sha256']):
            raise ValueError('Invalid manifest file record')
    if m['file_count'] != len(m['files']) or m['total_bytes'] != sum(v['size'] for v in m['files'].values()):
        raise ValueError('Manifest totals mismatch')
    return m


def verify(manifest_path, archive):
    m = load_manifest(manifest_path)
    archive = Path(archive)
    if archive.stat().st_size != m['archive']['size'] or digest(archive) != m['archive']['sha256']:
        raise ValueError('Archive size/hash mismatch')
    seen = set()
    with tarfile.open(archive, 'r:gz') as tf:
        for member in tf:
            name = safe_name(member.name)
            if not member.isfile() or name not in m['files'] or name in seen:
                raise ValueError('Unexpected, duplicate or nonregular archive member: ' + name)
            row = m['files'][name]
            if member.size != row['size']:
                raise ValueError('Member size mismatch: ' + name)
            h = hashlib.sha256()
            with tf.extractfile(member) as f:
                for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
                    h.update(block)
            if h.hexdigest() != row['sha256']:
                raise ValueError('Member hash mismatch: ' + name)
            seen.add(name)
    if seen != set(m['files']):
        raise ValueError('Missing archive members: ' + str(sorted(set(m['files']) - seen)))
    return {'result': 'passed', 'snapshot_id': m['snapshot_id'], 'files_verified': len(seen),
            'bytes_verified': m['total_bytes'], 'manifest_sha256': digest(manifest_path),
            'archive_sha256': m['archive']['sha256']}


def restore(manifest_path, archive, destination, members=None):
    result = verify(manifest_path, archive)
    m = load_manifest(manifest_path)
    selected = set(members or m['files'])
    if not selected or not selected <= set(m['files']):
        raise ValueError('Requested restore member missing')
    destination = Path(destination)
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    with tarfile.open(archive, 'r:gz') as tf:
        for member in tf:
            if member.name not in selected:
                continue
            p = destination / safe_name(member.name)
            p.parent.mkdir(parents=True, exist_ok=True)
            with tf.extractfile(member) as src, p.open('xb') as out:
                shutil.copyfileobj(src, out)
            os.chmod(p, 0o600)
            if digest(p) != m['files'][member.name]['sha256']:
                raise ValueError('Restored member mismatch: ' + member.name)
    return dict(result, restored_files=len(selected), destination=str(destination))


def rclone(config_path, snapshot, upload=False):
    """Optional native Drive transport. Always download and verify all bytes."""
    cfg, repo, _ = resolve_config(config_path)
    transport = cfg['rclone']
    remote = transport['remote']
    folder = transport['folder_id']
    if not re.fullmatch(r'[\w-]+', remote) or not re.fullmatch(r'[\w-]+', folder):
        raise ValueError('Invalid rclone remote/folder ID')
    # Read type without printing credentials. Only Google Drive is acceptable.
    dump = json.loads(subprocess.check_output(['rclone', 'config', 'dump']))
    if dump.get(remote, {}).get('type') != 'drive':
        raise ValueError('A native Google Drive rclone remote is required')
    base = [ 'rclone', '--drive-root-folder-id', folder ]
    snapshot = Path(snapshot)
    m = load_manifest(snapshot / 'manifest.json')
    verify(snapshot / 'manifest.json', snapshot / 'snapshot.tar.gz')
    if (snapshot / '.incomplete').exists():
        raise ValueError('Incomplete snapshot')
    snapshot_id = m['snapshot_id']
    if not re.fullmatch(r'[A-Za-z0-9_-]+', snapshot_id):
        raise ValueError('Unsafe snapshot ID')
    root = remote + ':'
    previous = json.loads(subprocess.check_output(base + ['lsjson', root, '--dirs-only']))
    if upload:
        if any(v['Name'] == snapshot_id for v in previous):
            raise ValueError('Remote snapshot already exists; verify it or create a new snapshot')
        subprocess.run(base + ['mkdir', root + snapshot_id], check=True)
        for name in ('snapshot.tar.gz', 'manifest.json'):
            subprocess.run(base + ['copyto', str(snapshot / name), root + snapshot_id + '/' + name, '--immutable'], check=True)
    listing = json.loads(subprocess.check_output(base + ['lsjson', root + snapshot_id]))
    if len(listing) != 2 or {v['Name'] for v in listing} != {'manifest.json', 'snapshot.tar.gz'}:
        raise ValueError('Remote file set mismatch (including possible duplicate names)')
    # Named fresh directory retained for owner inspection. No automatic cleanup.
    download = Path(tempfile.mkdtemp(prefix='download-', dir=snapshot.parent))
    for name in ('manifest.json', 'snapshot.tar.gz'):
        subprocess.run(base + ['copyto', root + snapshot_id + '/' + name, str(download / name), '--immutable'], check=True)
    if digest(download / 'manifest.json') != digest(snapshot / 'manifest.json'):
        raise ValueError('Remote manifest differs from trusted local manifest')
    report = verify(download / 'manifest.json', download / 'snapshot.tar.gz')
    current = json.loads(subprocess.check_output(base + ['lsjson', root, '--dirs-only']))
    if not {(v['ID'], v['Name']) for v in previous} <= {(v['ID'], v['Name']) for v in current}:
        raise ValueError('Earlier remote directory missing or renamed')
    report.update(download_directory=str(download), backend='native Google Drive',
                  previous_directories_preserved=len(previous), remote_listing=listing)
    write_json(download / 'remote-verification.json', report)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    c = sub.add_parser('create'); c.add_argument('--config', required=True); c.add_argument('--dry-run', action='store_true')
    for name in ('verify', 'restore'):
        c = sub.add_parser(name); c.add_argument('--manifest', required=True); c.add_argument('--archive', required=True)
        if name == 'restore':
            c.add_argument('--destination', required=True); c.add_argument('--member', action='append')
    for name in ('upload', 'verify-remote'):
        c = sub.add_parser(name); c.add_argument('--config', required=True); c.add_argument('--snapshot', required=True)
    a = p.parse_args()
    try:
        if a.command == 'create': result = create(a.config, a.dry_run)
        elif a.command == 'verify': result = verify(a.manifest, a.archive)
        elif a.command == 'restore': result = restore(a.manifest, a.archive, a.destination, a.member)
        else: result = rclone(a.config, a.snapshot, a.command == 'upload')
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError, tarfile.TarError) as e:
        p.exit(1, 'FAIL: ' + str(e) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
