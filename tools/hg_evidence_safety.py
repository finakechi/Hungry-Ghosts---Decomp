"""Hungry Ghosts evidence selection, freshness and review gates (no retail bytes)."""
import hashlib
import json
import subprocess
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as fp:
        for block in iter(lambda: fp.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def select_export(root, entry):
    root = Path(root)
    direct = root / f'{entry:08x}.json'
    candidates = [direct] if direct.is_file() else sorted(root.glob(f'*/{entry:08x}.json'))
    if len(candidates) != 1:
        raise ValueError(f'Export {entry:08x}: expected one snapshot, found {len(candidates)}; pass a specific --exports directory')
    path = candidates[0]
    if (path.parent / '.incomplete').exists() or (path.parent / '.partial').exists():
        raise ValueError('Incomplete export: choose a completed snapshot')
    marker = path.parent / 'COMPLETE.json'
    if marker.exists():
        manifest = json.loads(marker.read_text())
        if path.name not in manifest['files'] or any(not (path.parent / n).is_file() or digest(path.parent / n) != h for n,h in manifest['files'].items()):
            raise ValueError('Export digest mismatch')
    return path


def source_identity(root):
    root = Path(root)
    names = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=root).decode().split('\0')
    files = {n: digest(root / n) if (root / n).is_file() else None for n in sorted(set(names))
             if n.startswith(('src/', 'tools/', 'tests/', 'ghidra_scripts/'))}
    return {'revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root).decode().strip(),
            'files': files, 'dirty_files': subprocess.check_output(['git','status','--porcelain','--untracked-files=all'],cwd=root).decode().splitlines()}


def receipt(root, inputs, level, command, limitations, runtime=None):
    allowed = {'original-bytes', 'structure', 'synthetic', 'differential', 'saved-runtime'}
    if level not in allowed:
        raise ValueError('Unsupported evidence level; fresh runtime requires a collector-bound session')
    if level == 'saved-runtime':
        if not runtime or not {'raw', 'post', 'profile', 'emulator', 'image'} <= inputs.keys():
            raise ValueError('Runtime proof requires capture, post, profile, emulator and image bindings')
        metas = [runtime.get(k) for k in ('before', 'after', 'post_before', 'post_after')]
        if not metas[0] or any(m != metas[0] for m in metas):
            raise ValueError('Runtime identity mismatch')
    return {'schema': 'hg-verification-binding/v1', 'level': level, 'result': 'passed',
            'source': source_identity(root), 'inputs': {k: digest(v) for k, v in inputs.items()},
            'python_version': __import__('platform').python_version(), 'command': command, 'limitations': limitations, 'runtime': runtime}


def check_receipt(record, root, inputs):
    if record.get('result') != 'passed' or record.get('schema') != 'hg-verification-binding/v1':
        raise ValueError('Missing successful bound verification')
    if record['source']['files'] != source_identity(root)['files']:
        raise ValueError('Stale verification: relevant source changed')
    if record['inputs'] != {k: digest(v) for k, v in inputs.items()}:
        raise ValueError('Stale verification: input digest mismatch')
    if record['level'] == 'saved-runtime' and not record.get('runtime'):
        raise ValueError('Missing runtime context')
    return record['level']


def reconcile(old, rows, decisions):
    """Explicit decisions contain reason and replacement entries (empty for removal)."""
    indexed = {r['entry']: r for r in rows}
    if len(indexed) != len(rows):
        raise ValueError('Duplicate inventory entries')
    history = []
    for previous in old:
        if previous.get('status') == 'auto-discovered-unreviewed':
            continue
        entry = previous['entry']; current = indexed.get(entry)
        changed = current is None or any(previous.get(k) != current.get(k) for k in ('body_ranges', 'name', 'calls'))
        if changed:
            decision = decisions.get(entry)
            if not decision or not decision.get('reason') or 'replacements' not in decision:
                raise ValueError('Reviewed entry requires reconciliation: ' + entry)
            if any(e not in indexed for e in decision['replacements']):
                raise ValueError('Reconciliation replacement missing: ' + entry)
            history.append({'previous': previous, 'decision': decision})
    return history
