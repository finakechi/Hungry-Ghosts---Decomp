"""Searchable game-content index.

Connects the archive parser and the HGScript decoder through stable
resource identifiers (dir, member, PACK offset, SHA-256). Relationships
are labeled verified / supported-inference / unresolved. No dialogue text
is stored in index documents; string content stays in private outputs.
"""

from . import json_dump

QUERY_KINDS = ('members', 'flk5', 'scripts', 'events', 'strings', 'sound',
               'children', 'unresolved', 'stats', 'roundtrip')


def build_index(corpus_result):
    """Build a compact, queryable index document from a corpus run."""
    members = []
    scripts = []
    flk5 = []
    sound = []
    children = []
    for row in corpus_result['members']:
        classification = row['classification']
        item = dict(dir=row['dir'], member=row['index'],
                    offset=row['offset'], size_bytes=row['size_bytes'],
                    flags=row['flags'], reserved=row['reserved'],
                    sha256=row['sha256'],
                    magic_hex=row.get('magic_hex'),
                    status=classification['status'],
                    kind=classification['kind'])
        members.append(item)
        if classification['kind'] == 'flk5':
            flk5.append(dict(
                dir=row['dir'], member=row['index'], offset=row['offset'],
                sha256=row['sha256'], status=classification['status'],
                entry_types=sorted(
                    {e['type'] for e in classification.get('entries', [])})))
            for child in classification.get('details', {}).get(
                    'nested_flk5_children', []):
                children.append(dict(parent_dir=row['dir'],
                                     parent_member=row['index'],
                                     parent_offset=row['offset'],
                                     entry_index=child['entry_index'],
                                     child_offset=child['target_offset'],
                                     relationship=child['relationship']))
        if classification['kind'] == 'hgscript':
            scripts.append(next(s for s in corpus_result['scripts']
                                if s['dir'] == row['dir']
                                and s['member'] == row['index']))
        if classification['kind'] == 'sound-sshd':
            sound.append(dict(dir=row['dir'], member=row['index'],
                              offset=row['offset'],
                              size_bytes=row['size_bytes'],
                              sha256=row['sha256']))
    doc = dict(
        schema='hg-content-index/v1',
        source=corpus_result['source'],
        pack=corpus_result['pack'],
        classification_counts=corpus_result['classification_counts'],
        flk5_entry_type_counts=corpus_result['flk5_entry_type_counts'],
        nested_flk5_children_count=corpus_result['nested_flk5_children'],
        coverage=corpus_result['coverage'],
        members=members,
        flk5_members=flk5,
        scripts=scripts,
        sound_members=sound,
        children=children)
    return doc


def query(index, kind, **filters):
    """Answer a fixed set of queries; returns a JSON-safe list."""
    if kind == 'members':
        rows = index['members']
        for key in ('dir', 'member', 'status', 'kind'):
            if key in filters and filters[key] is not None:
                rows = [r for r in rows if r[key] == filters[key]]
        return rows
    if kind == 'flk5':
        rows = index['flk5_members']
        if filters.get('entry_type') is not None:
            rows = [r for r in rows
                    if filters['entry_type'] in r['entry_types']]
        if filters.get('status') is not None:
            rows = [r for r in rows if r['status'] == filters['status']]
        return rows
    if kind == 'scripts':
        rows = index['scripts']
        if filters.get('status') is not None:
            rows = [r for r in rows if r['status'] == filters['status']]
        if filters.get('binding_status') is not None:
            rows = [r for r in rows
                    if r['binding_status'] == filters['binding_status']]
        if filters.get('roundtrip') is not None:
            rows = [r for r in rows if r['roundtrip'] == filters['roundtrip']]
        return rows
    if kind == 'events':
        # Per-script event counts by role.
        rows = []
        for script in index['scripts']:
            rows.append(dict(dir=script['dir'], member=script['member'],
                             odd_word_events=script['odd_word_events'],
                             control_events=script['event_count']
                             - script['odd_word_events'],
                             total_events=script['event_count']))
            if 'runtime' in script:
                rows[-1]['opcodes'] = script['runtime']['opcodes']
        return rows
    if kind == 'strings':
        # String metadata only (counts/lengths/hash, never dialogue text).
        rows = []
        for script in index['scripts']:
            rows.append(dict(dir=script['dir'], member=script['member'],
                             pool_entries=script['pool_entries'],
                             pool_text_entries=script['pool_text_entries'],
                             binding_status=script['binding_status'],
                             sha256=script['sha256']))
            if 'runtime' in script:
                rows[-1]['runtime_references'] = script['runtime']['references']
        return rows
    if kind == 'sound':
        return index['sound_members']
    if kind == 'children':
        return index['children']
    if kind == 'unresolved':
        return [s for s in index['scripts']
                if s['binding_status'] == 'unresolved'
                or s['opaque_records'] > 0
                or s['roundtrip'] != 'decoded']
    if kind == 'roundtrip':
        return [dict(dir=s['dir'], member=s['member'], sha256=s['sha256'],
                     roundtrip=s['roundtrip'],
                     raw_preserved=s['roundtrip_raw_preserved'],
                     layout_matches=s['layout_matches'])
                for s in index['scripts']]
    if kind == 'stats':
        return [index['classification_counts'], index['coverage'],
                index['flk5_entry_type_counts']]
    raise ValueError(f'unknown query kind {kind!r}; available: {QUERY_KINDS}')
