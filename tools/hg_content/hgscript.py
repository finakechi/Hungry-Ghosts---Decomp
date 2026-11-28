"""HGScript decoding and lossless rebuilding.

Recognized structure (derived from authenticated original-corpus bytes):

- Collection header:  u32 count, u32 records_end, then count u32 offsets.
  Offsets strictly increase, the first equals 8 + 4*count, the last is
  below records_end.
- Records: variable-length streams of u32 word events terminated by a
  zero word, optionally followed by further zero-word padding. Spans are
  4-byte aligned. Words with bit 0 set correlate one-to-one with string
  pool entries (measured on the authenticated corpus); the sequential
  word-to-pool-entry binding is recorded as a supported inference, and
  only when the per-member counts match.
- String pool: after records_end, zero-run padding, then entries of
  {u32 header_word, Shift-JIS-with-ASCII bytes, NUL terminator,
  zero-run padding}. Strings split on the first NUL (a double NUL is a
  terminator plus one padding byte; measured final entries can carry a
  single NUL at the member end). The header_word equals 12 x byte-length
  for pure-SJIS strings (a width observation, not asserted for mixed text).

Interpretation is separated from raw preservation: every byte span is kept
verbatim (including inter-entry padding), and the encoder reproduces the
original exactly when the decoder consumed the whole member. Records that
do not parse as event streams and non-text pool entries stay visible as
opaque/raw regions and never acquire invented semantics.
"""

from bisect import bisect_left
from collections import Counter
import struct

from . import bounded_u32

MAX_COUNT = 8192
POOL_TEXT_MIN_RATIO = 0.5
POOL_MIN_TEXT_ENTRIES = 1


class ScriptError(ValueError):
    """Structurally unreadable HGScript input."""


def text_bytes_valid(raw):
    """True when bytes parse as Shift-JIS with single-byte ASCII runs."""
    if not raw:
        return False
    i = 0
    while i < len(raw):
        b = raw[i]
        if b < 0x80:
            i += 1
            continue
        if b > 0xEF:
            return False
        if (0x81 <= b <= 0x9F) or (0xE0 <= b <= 0xEF):
            if i + 1 >= len(raw):
                return False
            trail = raw[i + 1]
            if not ((0x40 <= trail <= 0x7E) or (0x80 <= trail <= 0xFC)):
                return False
            i += 2
            continue
        return False
    return True


def decode_text(raw):
    return raw.decode('shift_jis', errors='replace')


class Event:
    """One u32 word event; the raw value is authoritative."""

    __slots__ = ('value', 'offset', 'role', 'bound_pool_index', 'reference')

    def __init__(self, value, offset):
        self.value = value
        self.offset = offset
        # Raw observable: bit 0 of the word. Odd words correlate
        # one-to-one with string-pool entries (measured); the string-ref
        # interpretation is a supported inference, never asserted semantics.
        self.role = 'odd-word' if value & 1 else 'even-word'
        self.bound_pool_index = None
        self.reference = None

    @property
    def opcode(self):
        return self.value & 0x3f

    @property
    def operand(self):
        return self.value >> 6

    def to_json(self):
        out = dict(value=self.value, offset=self.offset, role=self.role,
                   opcode=self.opcode, operand=self.operand)
        if self.reference is not None:
            out['runtime_reference'] = self.reference
        if self.bound_pool_index is not None:
            out['bound_pool_index'] = self.bound_pool_index
        return out


class Record:
    """Variable-length u32 event stream plus preserved padding."""

    __slots__ = ('offset', 'span', 'events', 'padding_words', 'status',
                 'raw', 'note')

    def __init__(self, offset, span):
        self.offset = offset
        self.span = span
        self.events = []
        self.padding_words = 0
        self.status = 'unparsed'
        self.raw = b''
        self.note = None

    def to_json(self):
        out = dict(offset=self.offset, span=self.span,
                   status=self.status, note=self.note,
                   padding_words=self.padding_words,
                   events=[e.to_json() for e in self.events])
        if self.status == 'opaque':
            out['raw_hex'] = self.raw.hex()
        return out


class StringEntry:
    """One string-pool entry with verbatim raw preservation."""

    __slots__ = ('offset', 'header_word', 'text_bytes', 'pad_bytes',
                 'terminated', 'encoding_status')

    def __init__(self, offset, header_word, text_bytes, pad_bytes,
                 terminated=True):
        self.offset = offset
        self.header_word = header_word
        self.text_bytes = text_bytes
        self.pad_bytes = pad_bytes
        self.terminated = terminated
        self.encoding_status = ('text-valid' if text_bytes_valid(text_bytes)
                                else 'non-text-bytes')

    @property
    def text(self):
        return decode_text(self.text_bytes)

    def to_json(self, include_text=True):
        out = dict(offset=self.offset, header_word=self.header_word,
                   byte_length=len(self.text_bytes),
                   pad_length=len(self.pad_bytes),
                   terminated=self.terminated,
                   encoding_status=self.encoding_status)
        out['text_bytes_hex'] = self.text_bytes.hex()
        out['pad_hex'] = self.pad_bytes.hex()
        if include_text and self.encoding_status == 'text-valid':
            out['text'] = self.text
        return out


class Collection:
    """Decoded collection header, records, and pool."""

    def __init__(self):
        self.count = 0
        self.records_end = 0
        self.offsets = []
        self.records = []
        self.pool_lead_pad = b''
        self.pool = []
        self.pool_tail_pad = b''
        self.binding_status = 'none'
        self.runtime = {}
        self.status = 'unparsed'
        self.note = None

    def to_json(self, include_text=True):
        return dict(
            schema='hg-hgscript/v1',
            count=self.count,
            records_end=self.records_end,
            offsets=self.offsets,
            records=[r.to_json() for r in self.records],
            pool_lead_pad_length=len(self.pool_lead_pad),
            pool=[e.to_json(include_text) for e in self.pool],
            pool_tail_pad_length=len(self.pool_tail_pad),
            binding_status=self.binding_status,
            runtime=self.runtime,
            status=self.status,
            note=self.note)


def decode_header(data):
    """Validate the collection header; returns (count, records_end, offsets)."""
    if len(data) < 12:
        raise ScriptError('member too small for collection header')
    count = struct.unpack_from('<I', data, 0)[0]
    if count == 0 or count > MAX_COUNT:
        raise ScriptError(f'implausible record count {count}')
    table_size = 8 + count * 4
    if table_size > len(data):
        raise ScriptError('offset table exceeds member')
    records_end = struct.unpack_from('<I', data, 4)[0]
    if records_end < table_size or records_end > len(data):
        raise ScriptError(f'records_end {records_end:#x} outside member')
    offsets = struct.unpack_from(f'<{count}I', data, 8)
    if offsets[0] != table_size:
        raise ScriptError(f'first offset {offsets[0]:#x} != table size {table_size:#x}')
    prev = offsets[0]
    for o in offsets[1:]:
        if o <= prev or o > records_end:
            raise ScriptError('offsets not strictly increasing within records region')
        prev = o
    if offsets[-1] >= records_end:
        raise ScriptError('last record empty')
    return count, records_end, offsets


def parse_record(data, start, end):
    record = Record(start, end - start)
    record.raw = data[start:end]
    if record.span % 4:
        record.status = 'opaque'
        record.note = 'span not 4-byte aligned'
        return record
    words = struct.unpack_from(f'<{record.span // 4}I', data, start)
    cursor = 0
    terminated = False
    while cursor < len(words):
        word = words[cursor]
        if word == 0:
            terminated = True
            cursor += 1
            break
        record.events.append(Event(word, start + cursor * 4))
        cursor += 1
    if not terminated:
        record.status = 'opaque'
        record.note = 'no zero terminator'
        record.events = []
        return record
    if any(words[cursor:]):
        record.status = 'opaque'
        record.note = 'nonzero data after terminator'
        record.events = []
        return record
    record.padding_words = len(words) - cursor
    record.status = 'parsed'
    return record


def parse_pool(data, start):
    """Greedy deterministic pool split; every byte is accounted for.

    Returns (entries, anomalies, lead_pad, tail_pad). Boundaries are pure
    byte positions: zero runs are padding, the first non-zero byte after a
    zero run starts the next entry header. An entry without a 00 00
    terminator consumes the remainder of the member verbatim.
    """
    entries = []
    anomalies = 0
    pos = start
    tail_pad = b''
    pad_start = start
    while pos < len(data) and data[pos] == 0:
        pos += 1
    lead_pad = data[pad_start:pos]
    while pos < len(data):
        if pos + 4 > len(data):
            anomalies += 1
            tail_pad = data[pos:]
            break
        header_word = struct.unpack_from('<I', data, pos)[0]
        header_pos = pos
        pos += 4
        # Strings are NUL-terminated; measured final entries may carry a
        # single NUL at the member end. A double NUL is a terminator plus
        # one byte of zero padding. Split on the first NUL; the zero run
        # after it is padding. Byte accounting is identical either way.
        text_end = data.find(b'\x00', pos)
        if text_end < 0:
            entries.append(StringEntry(header_pos, header_word,
                                       data[pos:], b'', terminated=False))
            anomalies += 1
            pos = len(data)
            tail_pad = b''
            break
        text_bytes = data[pos:text_end]
        pos = text_end + 1
        pad_start = pos
        while pos < len(data) and data[pos] == 0:
            pos += 1
        entry = StringEntry(header_pos, header_word, text_bytes,
                            data[pad_start:pos], terminated=True)
        if not text_bytes_valid(text_bytes):
            anomalies += 1
        entries.append(entry)
        if pos >= len(data):
            tail_pad = b''
            break
    return entries, anomalies, lead_pad, tail_pad


def bind_strings(collection):
    """Bind odd-word events to pool entries sequentially.

    Only performed when the member-wide counts match exactly; otherwise
    reported as unresolved. Binding is a supported inference and is
    labeled as such.
    """
    ref_count = sum(1 for r in collection.records for e in r.events
                    if e.role == 'odd-word')
    if ref_count == 0 and not collection.pool:
        collection.binding_status = 'empty'
        return
    if ref_count != len(collection.pool):
        collection.binding_status = 'unresolved'
        return
    cursor = 0
    for record in collection.records:
        for event in record.events:
            if event.role == 'odd-word':
                event.bound_pool_index = cursor
                cursor += 1
    collection.binding_status = 'supported-inference-count-matched'


def annotate_runtime(collection, data):
    """Apply the proven EE address rules to raw bytes, independent of pool splitting."""
    counts = Counter()
    references = Counter()
    schedule_ends = None
    for record in collection.records:
        for index, event in enumerate(record.events):
            opcode = event.opcode
            counts[opcode] += 1
            offset = collection.records_end + event.operand * 4
            if opcode == 1:
                reference = dict(kind='draw-text', offset=offset, text_offset=offset + 4)
                end = data.find(b'\0', offset + 4) if offset + 4 < len(data) else -1
                if end != -1:
                    reference.update(width=struct.unpack_from('<I', data, offset)[0],
                                     byte_length=end - offset - 4,
                                     status='resolved')
            elif opcode == 7:
                reference = dict(kind='timed-cue', offset=offset)
                if offset + 8 <= len(data):
                    voice, parameter = struct.unpack_from('<II', data, offset)
                    reference.update(voice=voice, parameter=parameter)
                    if parameter == 0xffffffff:
                        reference.update(schedule_count=0, status='resolved')
                    else:
                        if schedule_ends is None:
                            schedule_ends = [
                                cursor for cursor in range(collection.records_end, len(data) - 3, 4)
                                if struct.unpack_from('<I', data, cursor)[0] == 0xffffffff
                            ]
                        end_index = bisect_left(schedule_ends, offset + 8)
                        if end_index < len(schedule_ends):
                            reference.update(
                                schedule_offset=offset + 8,
                                schedule_count=(schedule_ends[end_index] - offset - 8) // 4,
                                status='resolved')
            elif opcode == 12 and index == 0:
                # 001839f0 returns the block address. One verified caller reads
                # its leading i16. The rest of the metadata stays opaque.
                reference = dict(kind='leading-metadata', offset=offset)
                if offset + 2 <= len(data):
                    reference.update(leading_i16=struct.unpack_from('<h', data, offset)[0],
                                     status='resolved')
            else:
                continue
            reference.setdefault('status', 'unresolved')
            event.reference = reference
            references[(reference['kind'], reference['status'])] += 1
    collection.runtime = dict(
        opcodes={str(opcode): count for opcode, count in sorted(counts.items())},
        references={kind: {status: references[(kind, status)]
                           for status in ('resolved', 'unresolved')}
                    for kind in sorted({kind for kind, _ in references})})


def decode(data, include_text=True):
    """Decode a whole member. Never invents bytes; opaque spans stay raw."""
    count, records_end, offsets = decode_header(data)
    collection = Collection()
    collection.count = count
    collection.records_end = records_end
    collection.offsets = list(offsets)
    opaque = 0
    for i, start in enumerate(offsets):
        end = offsets[i + 1] if i + 1 < count else records_end
        record = parse_record(data, start, end)
        if record.status == 'opaque':
            opaque += 1
        collection.records.append(record)
    entries, anomalies, lead_pad, tail_pad = parse_pool(data, records_end)
    collection.pool_lead_pad = lead_pad
    collection.pool = entries
    collection.pool_tail_pad = tail_pad
    text_entries = sum(1 for e in entries
                       if e.encoding_status == 'text-valid' and e.text_bytes)
    pool_like = bool(entries) and (
        text_entries >= POOL_MIN_TEXT_ENTRIES and
        (text_entries / len(entries)) >= POOL_TEXT_MIN_RATIO)
    bind_strings(collection)
    annotate_runtime(collection, data)
    if opaque:
        collection.status = 'recognized-partial'
        collection.note = f'{opaque} opaque record(s); ' \
                          f'tail {"pool-parsed" if pool_like else "opaque"}'
    elif pool_like and anomalies == 0 and \
            collection.binding_status in ('supported-inference-count-matched', 'empty'):
        collection.status = 'parsed-validated'
        collection.note = 'header, records, and string pool validated'
    else:
        collection.status = 'recognized-partial'
        if pool_like:
            collection.note = ('records parsed; pool partially understood '
                               f'({anomalies} non-text/odd entry(s))')
        else:
            collection.note = ('records parsed; tail region is not a recognized '
                               'string pool (collection-only)')
    if anomalies:
        collection.note = f'{collection.note}; {anomalies} pool anomaly(s)'
    return collection


def stored_layout_matches(collection):
    """True when the stored offsets/end equal the sizes of the payloads.

    Used by the verifier: a decode of unedited bytes always matches;
    an edited collection reports False (its rebuild intentionally moves
    later records).
    """
    table_size = 8 + collection.count * 4
    position = table_size
    computed = []
    for record in collection.records:
        computed.append(position)
        if record.status == 'opaque':
            position += len(record.raw)
        else:
            position += 4 * (len(record.events) + 1 + record.padding_words)
    return computed == list(collection.offsets) and position == collection.records_end


def encode(collection):
    """Rebuild bytes from a decoded collection.

    Offsets are recomputed from record payload sizes (so controlled edits
    produce structurally valid output); `stored_layout_matches` reports
    whether that recomputation reproduces the original layout.
    """
    if collection.status == 'unparsed':
        raise ScriptError('refusing to encode an unparsed collection')
    count = collection.count
    if count == 0 or count > MAX_COUNT:
        raise ScriptError(f'implausible record count {count}')
    if count != len(collection.records):
        raise ScriptError('record count disagrees with decoded records')
    table_size = 8 + count * 4
    records_blob = bytearray()
    offsets = []
    for record in collection.records:
        offsets.append(table_size + len(records_blob))
        if record.status == 'opaque':
            records_blob += record.raw
        elif record.status == 'parsed':
            words = [e.value for e in record.events]
            words.append(0)
            words.extend([0] * record.padding_words)
            for word in words:
                records_blob += struct.pack('<I', word & 0xFFFFFFFF)
        else:
            raise ScriptError(f'record {record.offset:#x} is {record.status}')
    records_end = table_size + len(records_blob)
    out = bytearray()
    out += struct.pack('<2I', count, records_end)
    for offset in offsets:
        out += struct.pack('<I', offset)
    out += records_blob
    out += collection.pool_lead_pad
    for entry in collection.pool:
        out += struct.pack('<I', entry.header_word & 0xFFFFFFFF)
        out += entry.text_bytes
        if entry.terminated:
            out += b'\x00'
        out += entry.pad_bytes
    out += collection.pool_tail_pad
    return bytes(out)


def looks_like_hgscript(data):
    """Structural detection: header tiling + event streams + text pool."""
    try:
        count, records_end, offsets = decode_header(data)
    except ScriptError as error:
        return False, dict(reason=f'header: {error}')
    opaque = 0
    for i, start in enumerate(offsets):
        end = offsets[i + 1] if i + 1 < count else records_end
        if parse_record(data, start, end).status == 'opaque':
            opaque += 1
    entries, anomalies, _lead, _tail = parse_pool(data, records_end)
    text = sum(1 for e in entries if e.encoding_status == 'text-valid'
               and e.text_bytes)
    pool_like = bool(entries) and text >= POOL_MIN_TEXT_ENTRIES and (
        text / len(entries)) >= POOL_TEXT_MIN_RATIO
    evidence = dict(count=count, records_end=records_end, opaque_records=opaque,
                    pool_entries=len(entries), pool_text_entries=text,
                    pool_anomalies=anomalies)
    if opaque == 0 and pool_like:
        return True, evidence
    return False, dict(reason='partial structure', **evidence)
