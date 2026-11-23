#!/usr/bin/env python3
"""Source-backed decompilation coverage inventory (M17).

Classifies every candidate function of analysis/function-inventory.json:

  c-reconstructed   C source with no fail-closed path (no HG_DOMAIN_FAULT
                    reachable from its C body, directly or through static
                    helpers). This is NOT proof of completeness: earlier
                    milestones also bound behavior through bus seams; M17
                    completeness is established separately by the lifecycle
                    differential (100% statically reachable original words);
  c-fail-closed     C source whose unverified paths fail closed;
  instruction-backed original instructions executed by the host interpreter
                    in a verified run (--interpreted records), not C;
  external          a callee of reconstructed C reached only through a bus
                    seam (declared boundary or host model), not C/interpreted;
  analyzed          analyzed or byte-checked in the inventory, no source;
  unknown           automatic candidate only.

C entries come from the original-address annotations `/* 00xxxxxx */` on
prototypes in src/*.h; a C function is classified only if its body is found
in src/*.c. Byte counts use the inventory body ranges (instruction bytes).
Runtime-loaded AREA code is reported separately (not in the ELF inventory).

    python3 -B tools/hg_coverage_inventory.py [--baseline REF] \
        [--interpreted local/m17/out/interpreted.json] --output evidence/m17/coverage.json
"""
import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTO = re.compile(r'^\s*(?:void|uint32_t|uint64_t|int)\s+(hg_\w+)\s*\([^;]*\);\s*/\*\s*([0-9a-f]{8})\b', re.M)
CALL = re.compile(r'call\(\s*\w+\s*,\s*0x([0-9a-f]{5,8})u?\s*,')
EXTERNAL = re.compile(r'HG_EXTERNAL_CALL\s*,\s*0x([0-9a-f]{5,8})')


def git_text(ref, path):
    try:
        return subprocess.run(['git', 'show', f'{ref}:{path}'], cwd=ROOT, check=True,
                              capture_output=True, text=True).stdout
    except subprocess.CalledProcessError:
        return ''


def sources(ref=None):
    names = ('src/resource_loader.h', 'src/game_lifecycle.h', 'src/disc_read.h',
             'src/resource_loader.c', 'src/game_lifecycle.c', 'src/disc_read.c')
    if ref:
        return {n: git_text(ref, n) for n in names}
    return {n: (ROOT / n).read_text() if (ROOT / n).exists() else '' for n in names}


def c_body(text, name):
    m = re.search(r'^(?:static\s+)?(?:void|uint32_t|uint64_t|int)\s+' + name + r'\s*\([^)]*\)\s*\{', text, re.M)
    if not m:
        return None
    depth, i = 0, m.end() - 1
    while i < len(text):
        depth += {'{': 1, '}': -1}.get(text[i], 0)
        if depth == 0:
            return text[m.start():i + 1]
        i += 1
    return None


STATIC = re.compile(r'^static\s+(?:inline\s+)?(?:void|uint32_t|uint64_t|int)\s+(\w+)\s*\(', re.M)


def faulting(bodies):
    """Static helpers from which a domain fault is reachable (transitive)."""
    helpers = {n: c_body(bodies, n) or '' for n in set(STATIC.findall(bodies))}
    bad = {n for n, b in helpers.items() if 'HG_DOMAIN_FAULT' in b or n == 'domain_fault'}
    changed = True
    while changed:
        changed = False
        for n, b in helpers.items():
            if n not in bad and any(re.search(r'\b' + h + r'\s*\(', b) for h in bad):
                bad.add(n)
                changed = True
    return bad


def classify(src, interpreted=frozenset()):
    headers = ''.join(v for k, v in src.items() if k.endswith('.h'))
    bodies = ''.join(v for k, v in src.items() if k.endswith('.c'))
    bad = faulting(bodies)
    c_entries = {}
    for name, address in PROTO.findall(headers):
        body = c_body(bodies, name)
        if body is None:
            continue
        bounded = 'HG_DOMAIN_FAULT' in body or any(re.search(r'\b' + h + r'\s*\(', body) for h in bad)
        entry = int(address, 16)
        row = c_entries.setdefault(entry, dict(names=[], bounded=False))
        row['names'].append(name)
        row['bounded'] |= bounded
    callees = {int(x, 16) for x in CALL.findall(bodies)} | {int(x, 16) for x in EXTERNAL.findall(bodies)}
    callees = {c for c in callees if c < 0xfffffff0}
    return c_entries, callees


def inventory():
    data = json.loads((ROOT / 'analysis/function-inventory.json').read_text())
    rows = {}
    for f in data['functions']:
        size = sum(int(r['end_exclusive'], 16) - int(r['start'], 16) for r in f['body_ranges'])
        rows[int(f['entry'], 16)] = dict(size=size, status=f['status'], ranges=f['body_ranges'])
    return rows


def summarize(src, interpreted_pcs):
    inv = inventory()
    c_entries, callees = classify(src)
    interpreted_entries = set()
    starts = sorted(inv)
    import bisect
    for pc in interpreted_pcs:
        i = bisect.bisect_right(starts, pc) - 1
        if i >= 0:
            e = starts[i]
            if any(int(r['start'], 16) <= pc < int(r['end_exclusive'], 16) for r in inv[e]['ranges']):
                interpreted_entries.add(e)
    classes = {}
    for entry, row in inv.items():
        if entry in c_entries:
            k = 'c-fail-closed' if c_entries[entry]['bounded'] else 'c-reconstructed'
        elif entry in interpreted_entries:
            k = 'instruction-backed'
        elif entry in callees:
            k = 'external'
        elif row['status'] != 'auto-discovered-unreviewed':
            k = 'analyzed'
        else:
            k = 'unknown'
        classes.setdefault(k, []).append(entry)
    out = {}
    for k in ('c-reconstructed', 'c-fail-closed', 'instruction-backed', 'external', 'analyzed', 'unknown'):
        entries = sorted(classes.get(k, []))
        out[k] = dict(functions=len(entries), body_bytes=sum(inv[e]['size'] for e in entries))
    elf_pcs = [pc for pc in interpreted_pcs if 0x100000 <= pc < 0x3ea980]
    area_pcs = [pc for pc in interpreted_pcs if 0xa87800 <= pc < 0x2000000]
    c_not_in_inventory = sorted(e for e in c_entries if e not in inv)
    return dict(classes=out,
                c_entries={f'{e:08x}': dict(names=v['names'], bounded=v['bounded'],
                                            in_inventory=e in inv) for e, v in sorted(c_entries.items())},
                c_entries_outside_inventory=[f'{e:08x}' for e in c_not_in_inventory],
                instruction_backed_entries=[f'{e:08x}' for e in sorted(interpreted_entries - set(c_entries))],
                interpreted_instruction_bytes=dict(elf=4 * len(elf_pcs), area_overlay=4 * len(area_pcs)),
                inventory_functions=len(inv),
                inventory_body_bytes=sum(r['size'] for r in inv.values()))


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--baseline', default='46cdb2189a3190b49830b0222e3a5cfcc4e0fd0b')
    p.add_argument('--interpreted', type=Path, action='append', default=[],
                   help='JSON list (or {"pcs": [...]}) of original instruction addresses executed '
                        'by the interpreter in verified runs')
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    pcs = set()
    for path in a.interpreted:
        data = json.loads(path.read_text())
        pcs.update(int(x, 16) if isinstance(x, str) else x for x in (data['pcs'] if isinstance(data, dict) else data))
    after = summarize(sources(), pcs)
    before = summarize(sources(a.baseline), pcs)
    new = sorted(set(after['c_entries']) - set(before['c_entries']))
    result = dict(schema='hg-coverage-inventory/v1', baseline=a.baseline,
                  note='Inventory candidates are automatic Ghidra discoveries, not verified function totals. '
                       'C classes require C source; instruction-backed is interpreter execution of original '
                       'words in verified runs and is never counted as C. Byte counts are inventory body '
                       'bytes of whole candidates; c-bounded bodies are only partly reconstructed.',
                  before=before['classes'], after=after['classes'],
                  new_c_entries={e: after['c_entries'][e] for e in new},
                  c_entries=after['c_entries'],
                  c_entries_outside_inventory=after['c_entries_outside_inventory'],
                  instruction_backed_entries=after['instruction_backed_entries'],
                  interpreted_instruction_bytes=after['interpreted_instruction_bytes'],
                  inventory_functions=after['inventory_functions'],
                  inventory_body_bytes=after['inventory_body_bytes'])
    text = json.dumps(result, indent=1) + '\n'
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(text)
    for k in after['classes']:
        b, c = before['classes'][k], after['classes'][k]
        print(f"{k:<20} before {b['functions']:5d} fn {b['body_bytes']:8d} B   after {c['functions']:5d} fn "
              f"{c['body_bytes']:8d} B")
    print('new C entries:', len(new))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
