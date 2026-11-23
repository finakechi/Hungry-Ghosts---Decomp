"""Structural inspection of FLK5 containers.

Field knowledge is derived from the original executable's loader behavior
(documented in M4/M6) and from original-disc measurements. Entries are
4-byte words: type = byte 3, encoded offset = low 24 bits. The loader's
material-chain walk (tag & 0xf0000000 == 0x60000000 terminal, otherwise
advance (tag & 0xffff) * 16 + 16) is mirrored only as a bounded inspection
aid; no fixup semantics are re-implemented here.
"""

import struct

from . import FLK5_MAGIC, FLK5_MAGIC_WORD, bounded_u32

HEADER_SIZE = 16
ENTRY_STRIDE = 4
CHAIN_MAX_STEPS = 512
# Entry types observed in the authenticated original corpus.
OBSERVED_ENTRY_TYPES = (1, 6, 7, 9, 10, 12, 13, 14, 15, 16)
# Types the reconstructed executable fixup is known to traverse/ignore (M6).
LOADER_KNOWN_TYPES = (3, 4, 6, 10, 12)


class FLK5:
    """One FLK5 container inspection result."""

    def __init__(self, data, member_length):
        if len(data) < HEADER_SIZE:
            raise ValueError('FLK5 header truncated')
        magic = data[:4]
        if magic != FLK5_MAGIC:
            raise ValueError('not FLK5')
        count, size_word, reserved = struct.unpack_from('<3I', data, 4)
        self.count = count
        self.size_word = size_word
        self.reserved_word = reserved
        self.member_length = member_length
        self.entries = []
        self.errors = []
        if count > (member_length - HEADER_SIZE) // ENTRY_STRIDE:
            self.errors.append(dict(kind='entry-table-exceeds-member', count=count))
        read = min(count, (len(data) - HEADER_SIZE) // ENTRY_STRIDE)
        for i in range(read):
            raw = struct.unpack_from('<I', data, HEADER_SIZE + i * ENTRY_STRIDE)[0]
            entry = dict(index=i, raw=raw, type=raw >> 24,
                         encoded_offset=raw & 0xFFFFFF,
                         target=(raw & 0xFFFFFF) if (raw & 0xFFFFFF) <= member_length - 4 else None,
                         status='unread')
            if entry['target'] is None:
                entry['status'] = 'out-of-bounds'
                self.errors.append(dict(kind='entry-out-of-bounds', index=i, raw=raw))
            else:
                entry['status'] = 'in-bounds'
            self.entries.append(entry)

    def walk_chain(self, data, target):
        """Bounded mirror of the loader's material-chain walk (inspection only)."""
        path = []
        cursor = target
        terminal = None
        for step in range(CHAIN_MAX_STEPS):
            if cursor < 0 or cursor + 4 > self.member_length or cursor + 4 > len(data):
                return dict(path=path, terminal=None,
                            error='chain-leaves-member',
                            steps=step)
            tag = struct.unpack_from('<I', data, cursor)[0]
            path.append(dict(offset=cursor, tag=tag))
            if (tag & 0xF0000000) == 0x60000000:
                terminal = cursor
                break
            cursor += (tag & 0xFFFF) * 16 + 16
        else:
            return dict(path=path, terminal=None,
                        error='chain-step-limit', steps=CHAIN_MAX_STEPS)
        return dict(path=path, terminal=terminal, steps=len(path))

    def inspect(self, data):
        """Return a deterministic JSON-safe inspection record."""
        out = dict(magic='FLK5', count=self.count, size_word=self.size_word,
                   reserved_word=self.reserved_word, entries=[])
        for entry in self.entries:
            item = dict(entry)
            item.pop('target', None)
            item['target'] = entry['encoded_offset']
            item['status'] = entry['status']
            out['entries'].append(item)
        if self.errors:
            out['errors'] = self.errors
        return out

    def classify(self):
        """Deterministic status for the whole container."""
        if self.errors:
            return dict(status='malformed',
                        kind='flk5',
                        evidence='FLK5 header present but entries/bounds invalid: '
                                 + '; '.join(e['kind'] for e in self.errors))
        types = sorted({e['type'] for e in self.entries})
        unknown = [t for t in types if t not in OBSERVED_ENTRY_TYPES]
        if unknown:
            return dict(status='recognized-partial', kind='flk5',
                        evidence=f'FLK5 entries valid; unobserved entry types {unknown}')
        return dict(status='parsed-validated', kind='flk5',
                    evidence='FLK5 header and entry bounds validated')


def is_flk5(data):
    return data[:4] == FLK5_MAGIC


def inspect_flk5(data, member_length):
    """Classify and inspect a FLK5 member; never raises for bad inputs."""
    try:
        container = FLK5(data, member_length)
    except ValueError as error:
        return dict(status='malformed', kind='flk5',
                    evidence=f'FLK5 signature but header unreadable: {error}')
    result = container.classify()
    if container.errors:
        result['errors'] = container.errors
    result['header'] = dict(count=container.count,
                            size_word=container.size_word,
                            reserved_word=container.reserved_word)
    result['entries'] = []
    for entry in container.entries:
        item = dict(index=entry['index'], raw=entry['raw'], type=entry['type'],
                    encoded_offset=entry['encoded_offset'], status=entry['status'])
        result['entries'].append(item)
    return result
