# HGScript structure — measured findings

Derived from the authenticated original Japanese PACK corpus
(ISO SHA-256 `8ca1513c…994e`). All claims below are byte measurements;
interpretations beyond the raw structure are labeled as inferences.
Dialogue text is never reproduced here; private decoded outputs live in
ignored `local/corpus-scripts/`.

## Collection header

```
+0x00  u32 count          number of records
+0x04  u32 records_end    byte offset of the first byte after the records
+0x08  u32[count]         record offsets
```

Validation (measured invariant across the corpus):

- offsets strictly increase;
- `offsets[0] == 8 + 4*count` (the table end);
- `offsets[-1] < records_end <= member size`.

Members with this layout are the **collection-header family**; members
whose tail additionally parses as a text pool are HGScript dialogue
collections.

## Records

A record is a 4-byte-aligned span of **u32 word events** terminated by a
zero word, optionally followed by zero-word padding.

- Words with bit 0 set (`role: odd-word`) correlate **one-to-one** with
  string-pool entries: across the corpus, the count of odd words
  equals the count of pool entries for every fully decoded member where
  the binding is reported matched. The *sequential* order of binding
  (record order → pool order) is a **supported inference**; members whose
  counts disagree are reported `binding_status: unresolved`.
- Words with bit 0 clear (`role: control`) are preserved verbatim; their
  opcode semantics are not established. Value frequencies are committed in
  `hgscript-event-catalog.json`.
- Records that lack a zero terminator, contain nonzero data after the
  terminator, or are not 4-byte aligned are `opaque`: their bytes are
  preserved verbatim and reported separately from decoded records.

## String pool

After `records_end` (preceded by optional zero padding):

```
u32 header_word
Shift-JIS-with-ASCII bytes
NUL (0x00)
zero-run padding
```

- Strings split on the first NUL byte; a double NUL is a terminator plus
  one padding byte, and measured final entries can carry a single NUL at
  the member end. Every byte is accounted for, so rebuilds are exact.
- `header_word == 12 * byte_length` holds for pure-SJIS strings (measured;
  consistent with a 12-pixel-per-SJIS-byte width field). Mixed
  ASCII/SJIS strings do not follow the same ratio; the field is reported,
  not interpreted, for those.
- Text validity: Shift-JIS double-byte pairs with single-byte ASCII runs.
  Entries failing this check remain visible as `non-text-bytes` and are
  counted as pool anomalies.

## Observed corpus families

- **Dialogue/UI scripts** (74 members): collection header + event records
  + text pool; e.g. the save/load message file (dir 0 member 2, 44
  records, 90 strings) and per-area event tables (dir 63+ member 12/15/18,
  60 records each).
- **Collection-only members**: the same header and event-record encoding
  without a recognized text pool; tails are preserved as opaque regions.
- Reference values observed in events: even words (0x02, 0x04,
  0x0C, 0x14C, 0x28C, …) and odd words (0x03, 0x05, 0x81, 0x1C1, 0x301,
  0x681, …). The odd/even distinction is the only semantic split currently
  established; it is a correlation, not proven opcode semantics.

## Rebuilding

`encode()` rebuilds members from the decoded model:

- record offsets are recomputed from payload sizes, so controlled edits
  produce structurally valid collections;
- for unedited members the recomputed layout equals the stored layout
  (`layout_matches: true`) and the rebuild is byte-identical;
- opaque regions are copied verbatim and reported as raw preservation,
  **not** as evidence of decoding;
- the encoder refuses unparsed collections and inconsistent counts.

No retail script is modified by any tool in this milestone; controlled
edits are exercised only on synthetic fixtures.
