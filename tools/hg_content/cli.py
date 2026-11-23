#!/usr/bin/env python3
"""Hungry Ghosts content toolchain CLI.

Commands:
  corpus    [IMAGE | --pack-file FILE] --output O [--expect-sha256 H]
            [--scripts-dir D]
  inventory [IMAGE | --pack-file FILE] --output O
  extract   [IMAGE | --pack-file FILE] --dir D --member M --out-dir DIR
            [--name N]
  flk5      [IMAGE | --pack-file FILE] --dir D --member M [--output O]
  script decode  [IMAGE | --pack-file FILE] --dir D --member M [--no-text]
                 [--output O]
  script rebuild DECODED.json --output BIN [--expect-sha256 H]
  script verify  [IMAGE | --pack-file FILE] --dir D --member M --output R
  index build CORPUS.json --output DB
  index query DB --kind K [--dir D] [--member M] [--status S]
                 [--entry-type T] [--binding-status B] [--roundtrip R]

All reads are bounded; all outputs are exclusively created. Dialogue text is
only written to explicitly requested private outputs (script decode --output,
corpus --scripts-dir); index and inventory documents never contain text.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hg_identify  # noqa: E402,F401  (registered in sys.modules for lookups)

from hg_content import VERSION, json_dump  # noqa: E402
from hg_content.corpus import (AuthenticationError, open_source,  # noqa: E402
                               run_corpus)
from hg_content.flk5 import inspect_flk5  # noqa: E402
from hg_content.hgscript import (ScriptError, decode, encode,  # noqa: E402
                                 looks_like_hgscript,
                                 stored_layout_matches)
from hg_content.index import QUERY_KINDS, build_index, query  # noqa: E402
from hg_content.pack import Pack  # noqa: E402

SAFE_NAME = re.compile(r'^[A-Za-z0-9._-]+$')


def _iso_module():
    return sys.modules['hg_identify']


def _open_pack(image=None, pack_file=None):
    if image is None and pack_file is None:
        raise SystemExit('one of IMAGE or --pack-file is required')
    iso_module = _iso_module()
    if image is not None:
        path = Path(image)
        return path, Pack.open(path, iso_module)
    path = Path(pack_file)
    fp, iso, record, read_at = open_source(pack_path=path,
                                           iso_module=iso_module)
    return path, Pack(fp, iso, record, read_at)


def _find_member(pack, directory, member):
    for m in pack.all_members():
        if m.directory == directory and m.index == member:
            return m
    raise SystemExit(f'member d{directory} m{member} not found')


def _add_source_args(parser, image_help=None):
    parser.add_argument('image', nargs='?', help=image_help)
    parser.add_argument('--pack-file', dest='pack_file',
                        help='raw PACK.DAT file instead of an ISO image')


def cmd_corpus(args):
    output = Path(args.output)
    if output.exists():
        raise SystemExit(f'output exists: {output}')
    if args.image is None and args.pack_file is None:
        raise SystemExit('one of IMAGE or --pack-file is required')
    scripts_dir = Path(args.scripts_dir) if args.scripts_dir else None
    if scripts_dir is not None:
        if scripts_dir.exists() and (scripts_dir.is_symlink()
                                     or not scripts_dir.is_dir()):
            raise SystemExit('--scripts-dir must be a real directory')
        scripts_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = run_corpus(image_path=Path(args.image) if args.image else None,
                            pack_path=Path(args.pack_file)
                            if args.pack_file else None,
                            iso_module=_iso_module(),
                            expected_sha256=args.expect_sha256,
                            script_outputs=scripts_dir)
    except AuthenticationError as error:
        raise SystemExit(f'authentication failed: {error}')
    json_dump(result, output)
    print(json.dumps({k: v for k, v in result.items()
                      if k not in ('members', 'scripts', 'failures')},
                     indent=2))


def cmd_inventory(args):
    output = Path(args.output)
    if output.exists():
        raise SystemExit(f'output exists: {output}')
    path, pack = _open_pack(args.image, args.pack_file)
    with pack:
        result = dict(schema='hg-content-inventory/v1',
                      tool_version=VERSION,
                      pack=pack.member_counts(),
                      directories=[])
        for directory in pack.directories:
            entry = dict(index=directory['index'], count=directory['count'],
                         header_words_2_to_8=directory['header_words_2_to_8'],
                         members=[])
            for member in directory['members']:
                item = member.record()
                if member.size_bytes:
                    head = pack.read_member_head(member, 4)
                    item['magic_hex'] = head.hex()
                    if head == b'FLK5':
                        item['classification'] = inspect_flk5(
                            pack.read_member(member, limit=4096),
                            member.size_bytes)
                    else:
                        item['classification'] = dict(
                            status='unknown', kind='binary',
                            evidence='not classified by light inventory')
                else:
                    item['magic_hex'] = None
                    item['classification'] = dict(
                        status='unknown', kind='empty',
                        evidence='zero-length member record')
                entry['members'].append(item)
            result['directories'].append(entry)
    json_dump(result, output)
    print(json.dumps(result['pack']))


def cmd_extract(args):
    out_dir = Path(args.out_dir)
    if out_dir.exists() and (out_dir.is_symlink() or not out_dir.is_dir()):
        raise SystemExit('--out-dir must be a real, non-symlink directory')
    out_dir.mkdir(parents=True, exist_ok=True)
    name = args.name if args.name is not None else f'd{args.dir:03d}_m{args.member:03d}.bin'
    if not SAFE_NAME.match(name) or '/' in name or '\\' in name or name in ('.', '..'):
        raise SystemExit('--name must be a plain file name')
    path, pack = _open_pack(args.image, args.pack_file)
    with pack:
        member = _find_member(pack, args.dir, args.member)
        data = pack.read_member(member)
        target = out_dir / name
        with target.open('xb') as fp:
            fp.write(data)
        sha = hashlib.sha256(data).hexdigest()
        print(json.dumps(dict(dir=member.directory, member=member.index,
                              offset=member.offset, size_bytes=member.size_bytes,
                              written=str(target), sha256=sha)))


def cmd_flk5(args):
    path, pack = _open_pack(args.image, args.pack_file)
    with pack:
        member = _find_member(pack, args.dir, args.member)
        data = pack.read_member(member, limit=4096)
        if data[:4] != b'FLK5':
            raise SystemExit('member is not FLK5')
        result = inspect_flk5(data, member.size_bytes)
        result['dir'] = member.directory
        result['member'] = member.index
        result['offset'] = member.offset
        result['size_bytes'] = member.size_bytes
    if args.output:
        json_dump(result, Path(args.output))
    print(json.dumps(dict(status=result['status'], kind=result['kind'],
                          evidence=result['evidence'],
                          count=result['header']['count'],
                          entry_types=sorted({e['type'] for e in result['entries']}),
                          entries=result['entries']), indent=2))


def cmd_script_decode(args):
    path, pack = _open_pack(args.image, args.pack_file)
    with pack:
        member = _find_member(pack, args.dir, args.member)
        data = pack.read_member(member)
        try:
            collection = decode(data, include_text=not args.no_text)
        except ScriptError as error:
            raise SystemExit(f'member is not a recognized HGScript collection: {error}')
        result = dict(dir=member.directory, member=member.index,
                      offset=member.offset, size_bytes=member.size_bytes,
                      decoded=collection.to_json(include_text=not args.no_text))
        rebuilt = encode(collection)
        result['roundtrip'] = rebuilt == data
    if args.output:
        json_dump(result, Path(args.output))
    lines = []
    for i, record in enumerate(collection.records):
        events = ' '.join(f'{e.value:#x}' for e in record.events)
        lines.append(f'record[{i}] @{record.offset:#x}: [{events}] '
                     f'pad={record.padding_words} status={record.status}')
    lines.append(f'pool: {len(collection.pool)} entries; '
                 f'binding={collection.binding_status}; status={collection.status}')
    if not args.no_text:
        for entry in collection.pool:
            if entry.encoding_status == 'text-valid':
                lines.append(f'  @{entry.offset:#x} ({entry.header_word:#x}): {entry.text}')
    print('\n'.join(lines))
    if args.output:
        print(f'decoded JSON written to {args.output}')


def cmd_script_rebuild(args):
    with Path(args.decoded_json).open() as fp:
        doc = json.load(fp)
    inner = doc.get('decoded') if 'decoded' in doc else doc
    if inner.get('schema') != 'hg-hgscript/v1':
        raise SystemExit('input is not an hg-hgscript/v1 decoded document')
    collection = _collection_from_json(inner)
    rebuilt = encode(collection)
    if args.expect_sha256:
        measured = hashlib.sha256(rebuilt).hexdigest()
        if measured != args.expect_sha256:
            raise SystemExit(f'rebuild SHA-256 {measured} != expected {args.expect_sha256}')
    output = Path(args.output)
    if output.exists():
        raise SystemExit(f'output exists: {output}')
    with output.open('xb') as fp:
        fp.write(rebuilt)
    print(f'wrote {len(rebuilt)} bytes to {output}')


def _collection_from_json(doc):
    from hg_content.hgscript import (Collection, Event, Record, StringEntry)
    collection = Collection()
    collection.count = doc['count']
    collection.records_end = doc['records_end']
    collection.offsets = list(doc['offsets'])
    for r in doc['records']:
        record = Record(r['offset'], r['span'])
        record.events = [Event(e['value'], e['offset']) for e in r['events']]
        record.padding_words = r['padding_words']
        record.status = r['status']
        record.note = r['note']
        record.raw = bytes.fromhex(r.get('raw_hex', ''))
        collection.records.append(record)
    collection.pool_lead_pad = bytes(doc.get('pool_lead_pad_length', 0))
    collection.pool_tail_pad = bytes(doc.get('pool_tail_pad_length', 0))
    entries = []
    for e in doc['pool']:
        entry = StringEntry(e['offset'], e['header_word'],
                            bytes.fromhex(e['text_bytes_hex']),
                            bytes.fromhex(e['pad_hex']),
                            terminated=e.get('terminated', True))
        entries.append(entry)
    collection.pool = entries
    collection.binding_status = doc.get('binding_status', 'none')
    collection.status = doc['status']
    collection.note = doc.get('note')
    return collection


def cmd_script_verify(args):
    path, pack = _open_pack(args.image, args.pack_file)
    with pack:
        member = _find_member(pack, args.dir, args.member)
        data = pack.read_member(member)
        try:
            collection = decode(data, include_text=False)
        except ScriptError as error:
            raise SystemExit(f'not a recognized HGScript collection: {error}')
        rebuilt = encode(collection)
        result = dict(schema='hg-hgscript-verify/v1',
                      dir=member.directory, member=member.index,
                      offset=member.offset, size_bytes=member.size_bytes,
                      status=collection.status,
                      byte_exact=(rebuilt == data),
                      layout_matches=stored_layout_matches(collection),
                      opaque_records=sum(1 for r in collection.records
                                         if r.status == 'opaque'),
                      binding_status=collection.binding_status,
                      sha256=hashlib.sha256(data).hexdigest())
    json_dump(result, Path(args.output))
    print(json.dumps(result, indent=2))


def cmd_index_build(args):
    with Path(args.corpus_json).open() as fp:
        corpus = json.load(fp)
    doc = build_index(corpus)
    json_dump(doc, Path(args.output))
    print(json.dumps(dict(members=len(doc['members']),
                          scripts=len(doc['scripts']),
                          flk5_members=len(doc['flk5_members']),
                          sound_members=len(doc['sound_members']),
                          children=len(doc['children']))))


def cmd_index_query(args):
    with Path(args.db).open() as fp:
        doc = json.load(fp)
    filters = dict(dir=args.dir, member=args.member, status=args.status,
                   entry_type=args.entry_type,
                   binding_status=args.binding_status,
                   roundtrip=args.roundtrip)
    filters = {k: v for k, v in filters.items() if v is not None}
    rows = query(doc, args.kind, **filters)
    print(json.dumps(rows, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(prog='hg_content',
                                     description='Hungry Ghosts content toolchain')
    parser.add_argument('--version', action='version', version=VERSION)
    sub = parser.add_subparsers(dest='command', required=True)

    p = sub.add_parser('corpus', help='full corpus verification pass')
    _add_source_args(p)
    p.add_argument('--output', required=True)
    p.add_argument('--expect-sha256')
    p.add_argument('--scripts-dir')
    p.set_defaults(func=cmd_corpus)

    p = sub.add_parser('inventory', help='light deterministic member inventory')
    _add_source_args(p)
    p.add_argument('--output', required=True)
    p.set_defaults(func=cmd_inventory)

    p = sub.add_parser('extract', help='safe extraction of one member')
    _add_source_args(p)
    p.add_argument('--dir', type=int, required=True)
    p.add_argument('--member', type=int, required=True)
    p.add_argument('--out-dir', required=True)
    p.add_argument('--name')
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser('flk5', help='inspect one FLK5 container')
    _add_source_args(p)
    p.add_argument('--dir', type=int, required=True)
    p.add_argument('--member', type=int, required=True)
    p.add_argument('--output')
    p.set_defaults(func=cmd_flk5)

    p = sub.add_parser('script', help='HGScript decode/rebuild/verify')
    s = p.add_subparsers(dest='script_command', required=True)
    d = s.add_parser('decode', help='decode one script (readable output)')
    _add_source_args(d)
    d.add_argument('--dir', type=int, required=True)
    d.add_argument('--member', type=int, required=True)
    d.add_argument('--no-text', action='store_true',
                   help='omit dialogue text from output')
    d.add_argument('--output')
    d.set_defaults(func=cmd_script_decode)
    r = s.add_parser('rebuild', help='rebuild bytes from a decoded document')
    r.add_argument('decoded_json')
    r.add_argument('--output', required=True)
    r.add_argument('--expect-sha256')
    r.set_defaults(func=cmd_script_rebuild)
    v = s.add_parser('verify', help='decode-encode-compare receipt for one script')
    _add_source_args(v)
    v.add_argument('--dir', type=int, required=True)
    v.add_argument('--member', type=int, required=True)
    v.add_argument('--output', required=True)
    v.set_defaults(func=cmd_script_verify)

    p = sub.add_parser('index', help='content index build/query')
    s = p.add_subparsers(dest='index_command', required=True)
    b = s.add_parser('build')
    b.add_argument('corpus_json')
    b.add_argument('--output', required=True)
    b.set_defaults(func=cmd_index_build)
    q = s.add_parser('query')
    q.add_argument('db')
    q.add_argument('--kind', required=True, choices=QUERY_KINDS)
    q.add_argument('--dir', type=int)
    q.add_argument('--member', type=int)
    q.add_argument('--status')
    q.add_argument('--entry-type', type=int)
    q.add_argument('--binding-status')
    q.add_argument('--roundtrip')
    q.set_defaults(func=cmd_index_query)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == '__main__':
    main()
