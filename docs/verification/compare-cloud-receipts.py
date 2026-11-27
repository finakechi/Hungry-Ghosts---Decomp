"""Compare accepted cloud receipts from two fresh runs in the same checkout."""
import hashlib
import json
from pathlib import Path
import re
import sys

root = Path('local/verification')
ignore = {'attempt', 'started_utc', 'ended_utc', 'seconds', 'child_seconds', 'receipt_sha256'}
def normalize(value, mapping):
    if isinstance(value, str):
        for path, label in mapping.items():
            value = value.replace(path, label)
        return value
    if isinstance(value, list):
        return [normalize(v, mapping) for v in value]
    if isinstance(value, dict):
        return {normalize(k, mapping): normalize(v, mapping) for k, v in value.items()}
    return value

def load(run):
    folder = (root / run).resolve()
    summary=json.loads((folder / 'summary.json').read_text())
    rows = [r for r in summary['checks'] if r['verdict'] == r['expected']]
    mapping = {str(folder / r['attempt']): f"<attempt:{r['id']}>" for r in rows}
    result = {}
    for row in rows:
        entry = {k: v for k, v in row.items() if k not in ignore}
        hashes = {}
        for name, seal in entry['artifacts'].items():
            data = (folder / row['attempt'] / name).read_bytes()
            if hashlib.sha256(data).hexdigest() != seal:
                raise ValueError(f'Artifact changed: {row["id"]}/{name}')
            if name.endswith(('.log', '.json', '.txt')):
                text = normalize(data.decode('utf-8'), mapping)
                text = re.sub(r'(Ran \d+ tests? in )\d+(?:\.\d+)?s', r'\1<seconds>', text)
                seal = hashlib.sha256(text.encode()).hexdigest()
            # These are mkdtemp paths within the unittest attempt, one per named
            # mutant. Preserve both source and build bytes and normalize only
            # the random directory component.
            stable = re.sub(r'^late-frame-mutant-[a-z0-9_]{8}/', '<mutant-attempt>/', name)
            if stable in hashes:
                raise ValueError(f'Duplicate normalized artifact {stable}')
            hashes[stable] = seal
        result[row['id']] = normalize({**entry, 'artifacts': hashes}, mapping)
    return result

a, b = (load(item) for item in sys.argv[1:])
div = {}
for id in a.keys() & b.keys():
    if a[id] == b[id]:
        continue
    parts = {key: True for key in a[id].keys() | b[id].keys() if a[id].get(key) != b[id].get(key)}
    if 'artifacts' in parts:
        parts['artifacts'] = sorted(name for name in a[id]['artifacts'].keys() | b[id]['artifacts'].keys()
                                    if a[id]['artifacts'].get(name) != b[id]['artifacts'].get(name))
    div[id] = parts
print(json.dumps(dict(serial=len(a),parallel=len(b),matched=len(a.keys() & b.keys())-len(div),
                      differences=div,serial_only=sorted(a.keys()-b.keys()),
                      parallel_only=sorted(b.keys()-a.keys())), indent=2))
