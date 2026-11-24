"""Shared helpers for hg_content synthetic tests (no retail inputs)."""

import io
import struct

SECTOR = 2048


class FakeISO:
    """Minimal stand-in for hg_identify.ISO (files dict only)."""

    def __init__(self, files):
        self.files = files


def read_at(fp, offset, size):
    fp.seek(offset)
    data = fp.read(size)
    if len(data) != size:
        raise ValueError(f'truncated range at {offset:#x}, expected {size}')
    return data


def build_pack(directories, member_blobs):
    """Build an in-memory PACK.DAT-like blob.

    directories: list of (count, extra_header_words, member_specs)
      member_specs: list of (size_bytes, flags, reserved, payload_fn) where
      payload_fn(index) returns bytes or None for zero-length members.
    member_blobs: list of bytes placed sequentially after the table.
    """
    table_size = len(directories) * SECTOR
    payload = bytearray(b''.join(member_blobs))
    blob = bytearray(table_size + len(payload))
    cursor = table_size
    for di, (count, extra, specs) in enumerate(directories):
        block = bytearray(SECTOR)
        struct.pack_into('<I', block, 0, di)
        struct.pack_into('<I', block, 4, count)
        for i, word in enumerate(extra[:7]):
            struct.pack_into('<I', block, 8 + i * 4, word)
        for mi, spec in enumerate(specs):
            if callable(spec):
                spec = spec(mi)
            size_bytes, flags, reserved, source = spec
            if source is not None:
                data = source if isinstance(source, (bytes, bytearray)) else member_blobs[source]
                blob[cursor:cursor + len(data)] = data
                offset = cursor
                cursor += len(data)
            else:
                offset = cursor
                size_bytes = 0
            struct.pack_into('<4I', block, 64 + mi * 16,
                             offset, size_bytes, flags, reserved)
        blob[di * SECTOR:(di + 1) * SECTOR] = block
    return blob


def make_fixture_member(strings=('テスト一', 'テスト二', 'テスト三'), events=None,
                        header_words=(0x30,)):
    """Build one synthetic HGScript collection member."""
    if events is None:
        events = [[0x14C, 0x1C1], [0x28C, 0x301, 2, 0x681]]
    count = len(events)
    records_blob = bytearray()
    offsets = []
    table = 8 + count * 4
    for ev in events:
        offsets.append(table + len(records_blob))
        for word in ev:
            records_blob += struct.pack('<I', word)
        records_blob += struct.pack('<I', 0)
    records_end = table + len(records_blob)
    pool = bytearray()
    for text in strings:
        raw = text.encode('shift_jis')
        width = header_words[0] if header_words else 12 * len(raw)
        pool += struct.pack('<I', width)
        pool += raw
        pool += b'\x00\x00'
    blob = bytearray()
    blob += struct.pack('<2I', count, records_end)
    for offset in offsets:
        blob += struct.pack('<I', offset)
    blob += records_blob
    blob += pool
    return bytes(blob)


def pack_with_members(members, directory_count=1):
    """Single-directory PACK with one member per entry."""
    specs = []
    blobs = []
    for payload in members:
        if payload is None:
            specs.append((0, 0, 0, None))
        else:
            blobs.append(payload)
            specs.append((len(payload), 0, 0, len(blobs) - 1))
    blob = build_pack([(len(specs), [], specs)], blobs)
    return blob, io.BytesIO(blob), FakeISO(
        {'/DATA/PACK.DAT': dict(lba=0, size_bytes=len(blob))})
