#!/usr/bin/env python3
"""Audit every proposed Git object and historical tree; findings require review.

This is a heuristic publication gate, not a license or secret-detection oracle.
Bounded instruction evidence and summarized measurements require human review.
"""
import argparse
import json
import re
import subprocess

PRIVATE = re.compile(r'(^|/)(local|extracted|bios|captures?)(/|$)|\.(iso|chd|elf|irx|bin|gpr|gbf|bundle|zip|tar|gz|png|jpg)$|(^|/)(PACK|STREAM)\.DAT$', re.I)
PATTERNS = {
    'private absolute path': re.compile(r'/home/[a-zA-Z0-9_-]+/|/Users/[a-zA-Z0-9_-]+/|[A-Z]:\\Users\\'),
    'private key': re.compile(r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----'),
    'GitHub credential': re.compile(r'gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}'),
    'Google credential': re.compile(r'AIza[A-Za-z0-9_-]{30,}|1//[A-Za-z0-9_-]{50,}'),
    'assigned credential': re.compile(r'''(?i:access_token|refresh_token|client_secret)\s*["']?\s*[:=]\s*["'][^"'\s]{8,}'''),
}


def audit(repo='.', base='origin/main', head='HEAD'):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args])
    base = git('rev-parse', '--verify', base + '^{commit}').decode().strip()
    head = git('rev-parse', '--verify', head + '^{commit}').decode().strip()
    commits = git('rev-list', head, '^' + base).decode().splitlines()
    findings, objects = [], []
    for commit in commits:
        for row in git('ls-tree', '-rz', commit).split(b'\0'):
            if not row:
                continue
            meta, path = row.split(b'\t', 1)
            mode, kind, oid = meta.decode().split()
            path = path.decode()
            if kind != 'blob' or mode != '100644' and mode != '100755' or PRIVATE.search(path):
                findings.append({'commit': commit, 'path': path, 'kind': 'private/unsupported tree entry'})
    for row in git('rev-list', '--objects', head, '^' + base).decode().splitlines():
        oid, _, path = row.partition(' ')
        kind = git('cat-file', '-t', oid).decode().strip()
        data = git('cat-file', '-p', oid)
        objects.append({'oid': oid, 'type': kind, 'path': path, 'size': len(data)})
        if kind not in ('blob', 'commit'):
            continue
        try:
            text = data.decode('utf-8')
            if '\0' in text:
                raise UnicodeError()
        except UnicodeError:
            findings.append({'oid': oid, 'path': path, 'kind': 'binary/non-UTF8'})
            continue
        for label, pattern in PATTERNS.items():
            lines = [i + 1 for i, line in enumerate(text.splitlines()) if pattern.search(line)]
            if lines:
                findings.append({'oid': oid, 'path': path, 'kind': label, 'lines': lines})
    return {'schema': 'hg-publication-audit/v1', 'base': base, 'head': head,
            'commits': commits, 'objects': objects, 'findings': findings,
            'result': 'review-required' if findings else 'no-heuristic-findings',
            'limits': 'Requires content review of source, bounded instruction evidence, licensing and summarized captures. Does not prove absence of every secret.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base', default='origin/main'); p.add_argument('--head', default='HEAD')
    p.add_argument('--output', required=True)
    a = p.parse_args(); report = audit(base=a.base, head=a.head)
    with open(a.output, 'x') as f:
        json.dump(report, f, indent=2); f.write('\n')
    print(report['result'] + ': ' + str(len(report['objects'])) + ' objects; ' + str(len(report['findings'])) + ' findings')
    raise SystemExit(bool(report['findings']))


if __name__ == '__main__':
    main()
