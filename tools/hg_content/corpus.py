"""Whole-corpus verification driver.

One deterministic pass over PACK.DAT:
- authenticate the image when an expected SHA-256 is supplied;
- enumerate and classify every member;
- inspect FLK5 containers (header, entries, nested-FLK5 children);
- recognize SShd sound containers;
- detect, decode, rebuild and byte-compare HGScript candidates;
- report aggregate coverage with explicit statuses.

A raw copy of an unparsed region is preservation, not evidence of
successful decoding: round-trip results are reported separately for
fully-decoded members versus members with opaque (raw-preserved) regions.
"""

import hashlib
import struct

from . import (FLK5_MAGIC, bounded_u32, json_dump)
from .flk5 import inspect_flk5
from .hgscript import ScriptError, decode, encode, stored_layout_matches
from .pack import PACK_PATH, Pack
from .sound import classify as classify_sshd

MAX_SCRIPT_MEMBER = 2 * 1024 * 1024  # decode bound (corpus scripts are far smaller)
MAX_SCRIPT_COUNT = 8192


class AuthenticationError(ValueError):
    """Image hash does not match the expected source identity."""


class _FakeISO:
    """Files-dict stand-in used for raw PACK.DAT sources."""

    def __init__(self, files):
        self.files = files


def open_source(image_path=None, pack_path=None, iso_module=None):
    """Open either an ISO image or a raw PACK.DAT file.

    Returns (fp, iso_like, pack_record, read_at).
    """
    if image_path is not None:
        fp = image_path.open('rb')
        try:
            iso = iso_module.ISO(fp)
            return fp, iso, iso.files[PACK_PATH], iso_module.read_at
        except Exception:
            fp.close()
            raise
    fp = pack_path.open('rb')
    fp.seek(0, 2)
    size = fp.tell()
    record = dict(lba=0, size_bytes=size, path_on_disc='<raw PACK.DAT>')
    return fp, _FakeISO({PACK_PATH: record}), record, iso_module.read_at


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def hash_file_chunks(fp, offset, size):
    """Streamed SHA-256 over an extent (4 MiB chunks)."""
    digest = hashlib.sha256()
    fp.seek(offset)
    remaining = size
    while remaining:
        block = fp.read(min(4 * 1024 * 1024, remaining))
        if not block:
            raise ValueError('truncated extent during hashing')
        digest.update(block)
        remaining -= len(block)
    return digest.hexdigest()


def authenticate(fp, expected):
    """Full-image authentication; returns measured SHA-256."""
    fp.seek(0, 2)
    size = fp.tell()
    measured = hash_file_chunks(fp, 0, size)
    if expected is not None and measured != expected:
        raise AuthenticationError(
            f'image SHA-256 {measured} != expected {expected}')
    return measured


def _flk5_children(pack, member, sample):
    """Verified nested-FLK5 children: entry targets that begin with FLK5.

    `sample` supplies the entry table; each target head is read with a
    bounded per-target read, so children beyond the sample are found too.
    """
    if sample is None or len(sample) < 16:
        return []
    try:
        count = struct.unpack_from('<I', sample, 4)[0]
    except struct.error:
        return []
    children = []
    read = min(count, (len(sample) - 16) // 4, 256)
    for i in range(read):
        raw = struct.unpack_from('<I', sample, 16 + i * 4)[0]
        target = raw & 0xFFFFFF
        if 0 <= target <= member.size_bytes - 4:
            try:
                head = pack.read_member_range(member, target, 4)
            except ValueError:
                continue
            if head == FLK5_MAGIC:
                children.append(dict(entry_index=i, target_offset=target,
                                     relationship='verified'))
    return children


def run_corpus(image_path=None, pack_path=None, iso_module=None,
               expected_sha256=None, script_outputs=None, progress=None):
    """Run the full corpus pass over an ISO image or a raw PACK.DAT file.

    `script_outputs` (optional dir) receives private per-script decoded
    JSON including dialogue text.
    """
    result = dict(schema='hg-content-corpus/v1')
    source_label = str(image_path) if image_path is not None else str(pack_path)
    fp, iso, pack_record, read_at_fn = open_source(
        image_path=image_path, pack_path=pack_path, iso_module=iso_module)
    with fp:
        source_sha256 = authenticate(fp, expected_sha256)
        result['source'] = dict(path=source_label, sha256=source_sha256,
                                authenticated=expected_sha256 is not None,
                                kind='iso-image' if image_path is not None
                                else 'raw-pack')
        pack = Pack(fp, iso, pack_record, read_at_fn)
        result['pack'] = pack.member_counts()
        member_rows = []
        status_counts = {}
        flk5_types = {}
        scripts = []
        sound_members = []
        nested_children = 0
        failures = []
        for member in pack.all_members():
            row = member.record()
            row['dir'] = member.directory
            try:
                if member.size_bytes == 0:
                    classification = dict(status='unknown', kind='empty',
                                          evidence='zero-length member record')
                else:
                    head = pack.read_member_head(member, 4)
                    row['magic_hex'] = head.hex() if head else None
                    classification = None
                    first_word = None
                    script = None
                    full = None
                    if head == FLK5_MAGIC:
                        sample = pack.read_member(member, limit=4096)
                        classification = inspect_flk5(sample, member.size_bytes)
                        for entry in classification.get('entries', []):
                            flk5_types[entry['type']] = flk5_types.get(entry['type'], 0) + 1
                        children = _flk5_children(pack, member, sample)
                        nested_children += len(children)
                        if children:
                            classification.setdefault('details', {})['nested_flk5_children'] = children
                    elif member.size_bytes >= 12:
                        first_word = struct.unpack_from('<I', head, 0)[0]
                        if 1 <= first_word <= MAX_SCRIPT_COUNT:
                            probe = pack.read_member(member, limit=12)
                            count, records_end = struct.unpack_from('<2I', probe, 0)
                            try:
                                if (count == first_word
                                        and 8 + count * 4 <= records_end
                                        and records_end <= MAX_SCRIPT_MEMBER
                                        and records_end <= member.size_bytes):
                                    full = pack.read_member(member,
                                                            limit=MAX_SCRIPT_MEMBER)
                                    # Opaque records stay visible: any member
                                    # with a valid collection header is decoded
                                    # and reported, with round-trip status
                                    # separating fully-decoded from raw-preserved.
                                    collection = decode(full, include_text=False)
                                    rebuilt = encode(collection)
                                    roundtrip = (rebuilt == full)
                                    refs = sum(1 for r in collection.records
                                               for e in r.events
                                               if e.role == 'odd-word')
                                    opaque = sum(1 for r in collection.records
                                                 if r.status == 'opaque')
                                    script = dict(
                                        dir=member.directory, member=member.index,
                                        offset=member.offset,
                                        size_bytes=member.size_bytes,
                                        status=collection.status,
                                        record_count=len(collection.records),
                                        records_end=collection.records_end,
                                        event_count=sum(len(r.events)
                                                        for r in collection.records),
                                        odd_word_events=refs,
                                        pool_entries=len(collection.pool),
                                        pool_text_entries=sum(
                                            1 for e in collection.pool
                                            if e.encoding_status == 'text-valid'
                                            and e.text_bytes),
                                        binding_status=collection.binding_status,
                                        roundtrip=('decoded' if roundtrip
                                                    else 'failed'),
                                        roundtrip_raw_preserved=roundtrip and opaque > 0,
                                        opaque_records=opaque,
                                        layout_matches=stored_layout_matches(collection))
                                    if script_outputs is not None and roundtrip:
                                        private = collection.to_json(include_text=True)
                                        name = f'd{member.directory:03d}_m{member.index:03d}.json'
                                        json_dump(private, script_outputs / name)
                                    classification = dict(
                                        status=collection.status, kind='hgscript',
                                        evidence=f'HGScript collection: {collection.note}')
                            except ScriptError:
                                classification = None
                    if classification is None:
                        sshd = classify_sshd(full or pack.read_member(member, limit=4096),
                                             member.size_bytes)
                        if sshd is not None:
                            classification = sshd
                            sound_members.append(dict(
                                dir=member.directory, member=member.index,
                                offset=member.offset, size_bytes=member.size_bytes))
                        else:
                            classification = dict(status='unknown', kind='binary',
                                                  evidence='no supported structure recognized')
                row['classification'] = classification
                row['sha256'] = hash_file_chunks(
                    fp, pack.absolute_offset(member), member.size_bytes)
                status = classification['status']
                status_counts[status] = status_counts.get(status, 0) + 1
                if script is not None:
                    script['sha256'] = row['sha256']
                    scripts.append(script)
            except Exception as error:  # record, never hide, never abort the pass
                failures.append(dict(dir=member.directory, member=member.index,
                                     offset=member.offset, error=repr(error)))
                row['classification'] = dict(status='malformed', kind='processing-error',
                                             evidence=f'uncaught processing error: {error}')
                status_counts['malformed'] = status_counts.get('malformed', 0) + 1
            member_rows.append(row)
        result['classification_counts'] = status_counts
        result['flk5_entry_type_counts'] = dict(sorted(flk5_types.items()))
        result['nested_flk5_children'] = nested_children
        result['sound_members'] = sound_members
        result['scripts'] = scripts
        result['failures'] = failures
        result['members'] = member_rows
        result['coverage'] = dict(
            members_enumerated=len(member_rows),
            members_structurally_classified=sum(
                1 for m in member_rows
                if m['classification']['status'] in
                ('parsed-validated', 'recognized-partial')),
            hgscript_candidates=len(scripts),
            hgscript_decoded=sum(1 for s in scripts
                                 if s['status'] in
                                 ('parsed-validated', 'recognized-partial')),
            hgscript_roundtrip_decoded=sum(
                1 for s in scripts if s['roundtrip'] == 'decoded'
                and not s['roundtrip_raw_preserved']),
            hgscript_roundtrip_raw_preserved=sum(
                1 for s in scripts if s['roundtrip'] == 'decoded'
                and s['roundtrip_raw_preserved']),
            hgscript_binding_count_matched=sum(
                1 for s in scripts
                if s['binding_status'] == 'supported-inference-count-matched'),
            hgscript_binding_unresolved=sum(
                1 for s in scripts if s['binding_status'] == 'unresolved'),
            unknown_members=status_counts.get('unknown', 0),
            malformed_members=status_counts.get('malformed', 0),
            failed_members=len(failures))
    return result
