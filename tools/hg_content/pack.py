"""PACK.DAT archive processing: enumeration, bounds checks, classification.

Reuses the existing independent ISO9660 reader (hg_identify) and its
bounded `read_at` / chunked `hashes`. All directory/member fields are
preserved verbatim; classification is derived evidence, never a rewrite.
"""

import struct

from . import (DIRECTORY_STRIDE, FLK5_MAGIC, MAX_DIRECTORIES,
               MAX_MEMBERS_PER_DIRECTORY, MEMBER_RECORD_STRIDE,
               MEMBER_TABLE_OFFSET, bounded_u32)

PACK_PATH = '/DATA/PACK.DAT'


class PackError(ValueError):
    """Malformed or out-of-bounds PACK structure."""


class Member:
    """One PACK member record plus derived evidence."""

    __slots__ = ('directory', 'index', 'offset', 'size_bytes', 'flags',
                 'reserved', 'head', 'classification')

    def __init__(self, directory, index, offset, size_bytes, flags, reserved):
        self.directory = directory
        self.index = index
        self.offset = offset
        self.size_bytes = size_bytes
        self.flags = flags
        self.reserved = reserved
        self.head = None          # member prefix (None until read)
        self.classification = None

    def record(self):
        return dict(index=self.index, offset=self.offset,
                    size_bytes=self.size_bytes, flags=self.flags,
                    reserved=self.reserved)

    def to_json(self):
        out = self.record()
        out['magic_hex'] = self.head.hex() if self.head is not None else None
        out['classification'] = self.classification
        return out


class Pack:
    """Validated read-only view of PACK.DAT inside an ISO image."""

    def __init__(self, fp, iso, pack_record, read_at):
        self._fp = fp
        self._iso = iso
        self._rec = pack_record
        self._read_at = read_at
        self._base = pack_record['lba'] * 2048
        self._size = pack_record['size_bytes']
        self.directories = []
        self._parse()

    @classmethod
    def open(cls, image_path, iso_module):
        fp = image_path.open('rb')
        try:
            iso = iso_module.ISO(fp)
            rec = iso.files[PACK_PATH]
            return cls(fp, iso, rec, iso_module.read_at)
        except Exception:
            fp.close()
            raise

    def close(self):
        self._fp.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _parse(self):
        if self._size < DIRECTORY_STRIDE:
            raise PackError('PACK smaller than one directory block')
        index = 0
        earliest = self._size
        while index * DIRECTORY_STRIDE < earliest:
            if index >= MAX_DIRECTORIES:
                raise PackError('directory count exceeds scan bound')
            if (index + 1) * DIRECTORY_STRIDE > self._size:
                raise PackError('directory table exceeds PACK')
            block = self._read_at(self._fp, self._base + index * DIRECTORY_STRIDE,
                                  DIRECTORY_STRIDE)
            ident, count = struct.unpack_from('<2I', block)
            if ident != index or count > MAX_MEMBERS_PER_DIRECTORY:
                raise PackError(f'directory {index}: invalid ident/count {ident}/{count}')
            header_words = list(struct.unpack_from('<7I', block, 8))
            members = []
            for item in range(count):
                offset, length, flags, reserved = struct.unpack_from(
                    '<4I', block, MEMBER_TABLE_OFFSET + item * MEMBER_RECORD_STRIDE)
                bounded_u32(offset, length, f'directory {index} member {item}')
                if offset + length > self._size:
                    raise PackError(
                        f'directory {index} member {item}: extent {offset:#x}+{length:#x}'
                        f' exceeds PACK size {self._size:#x}')
                if length and offset < (index + 1) * DIRECTORY_STRIDE:
                    raise PackError(
                        f'directory {index} member {item}: extent overlaps directory table')
                if length:
                    earliest = min(earliest, offset)
                members.append(Member(index, item, offset, length, flags, reserved))
            self.directories.append(dict(index=index, count=count,
                                         header_words_2_to_8=header_words,
                                         members=members))
            index += 1
        if earliest != index * DIRECTORY_STRIDE:
            raise PackError(f'directory/member boundary not exact: {earliest:#x} '
                            f'vs {index * DIRECTORY_STRIDE:#x}')

    def read_member_head(self, member, size=4):
        """Bounded read of a member prefix."""
        if member.size_bytes == 0:
            return b''
        if member.head is None or len(member.head) < max(size, 4):
            member.head = self._read_at(
                self._fp, self._base + member.offset,
                min(member.size_bytes, max(size, 4)))
        return member.head[:min(size, len(member.head))]

    def read_member(self, member, limit=None):
        """Bounded read of member bytes (optionally capped)."""
        if limit is not None:
            if limit < 0:
                raise ValueError('negative member read limit')
            size = min(member.size_bytes, limit)
        else:
            size = member.size_bytes
        if size == 0:
            return b''
        return self._read_at(self._fp, self._base + member.offset, size)

    def absolute_offset(self, member, offset=0):
        """Absolute PACK offset of a member-relative position."""
        bounded_u32(member.offset, offset, 'absolute member offset')
        if offset < 0 or offset > member.size_bytes:
            raise ValueError(f'member-relative offset {offset:#x} out of bounds')
        return self._base + member.offset + offset

    def read_member_range(self, member, offset, size):
        """Bounded read inside a member."""
        bounded_u32(offset, size, 'member range')
        if size < 0 or offset < 0 or offset + size > member.size_bytes:
            raise ValueError('member range out of bounds')
        if size == 0:
            return b''
        return self._read_at(self._fp, self._base + member.offset + offset, size)

    def all_members(self):
        for directory in self.directories:
            for member in directory['members']:
                yield member

    def member_counts(self):
        return dict(directory_count=len(self.directories),
                    member_count=sum(d['count'] for d in self.directories),
                    nonzero_member_count=sum(
                        1 for m in self.all_members() if m.size_bytes),
                    table_size_bytes=len(self.directories) * DIRECTORY_STRIDE)
