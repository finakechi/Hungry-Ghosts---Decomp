# Content pipeline — PACK corpus, FLK5, and HGScript toolchain

**Branch:** `research/content-corpus-hgscript` · **Worktree:** `decomp-content`
· **Baseline:** `origin/main` = `46cdb2189a3190b49830b0222e3a5cfcc4e0fd0b`
(the verified upstream M16 merge). Nothing is pushed or merged.

This milestone delivers an offline, deterministic toolchain for the original
Japanese game's PACK archive corpus: complete member enumeration, structural
FLK5 inspection, HGScript (event-table) decoding with byte-exact rebuilding,
and a searchable content index. It is independent of M17 (executable
reconstruction); no file owned by M17 was modified.

All implementation is derived from authenticated original-corpus bytes.
The pinned hgtools reference was inspected in earlier milestones (see
[docs/sources.md](sources.md)); it was not re-inspected or copied during
this milestone, and no external implementation or dependency is
incorporated.

## Supported formats and tools

| Tool (under `tools/hg_content/`) | Purpose |
|---|---|
| `pack.py` | PACK.DAT directory/member enumeration, bounds validation, member classification |
| `flk5.py` | FLK5 container inspection: header fields, entry types/offsets, bounds, nested-FLK5 children |
| `hgscript.py` | HGScript collection decode/encode: u32-event records, string pool, sequential binding inference, byte-exact rebuild |
| `sound.py` | Evidence-driven recognition of SShd sound containers |
| `corpus.py` | Whole-corpus verification driver (authentication, classification, decode, rebuild, coverage) |
| `index.py` | Searchable content index: members, FLK5, scripts, events, strings, sound, parent/child, unresolved, round-trip |
| `cli.py` | Command-line interface for all operations |

Reused, unmodified: `tools/hg_identify.py` (ISO9660 reader, bounded reads,
hashes). `src/resource_loader.c` was inspected read-only for FLK5 semantics
and was not changed.

### HGScript structure (measured on the authenticated corpus)

- **Collection header:** `u32 count`, `u32 records_end`, then `count` u32
  offsets. Offsets strictly increase; the first equals `8 + 4*count`; the
  last is below `records_end`.
- **Records:** variable-length streams of **u32 word events** terminated by
  a zero word, optionally followed by zero-word padding. Words with bit 0
  set correlate one-to-one with string-pool entries (measured); the
  sequential word-to-pool-entry binding is recorded as a **supported
  inference**, and only when per-member counts match.
- **String pool:** after `records_end`, zero-run padding, then entries of
  `{u32 header_word, Shift-JIS-with-ASCII bytes, NUL, zero-run padding}`.
  For pure-SJIS strings the header word equals `12 × byte length` (a width
  field; not asserted for mixed encodings).
- **Interpretation vs. raw:** every byte span is preserved verbatim.
  Records that do not parse as event streams and non-text pool entries
  remain visible as opaque/raw regions; they never acquire invented
  semantics, and a rebuild that only copies them is reported as
  raw-preserved, not as evidence of decoding.

## Reproduction commands

```sh
# 0. Synthetic suite (no retail inputs required)
python3 -B -m unittest tests.test_hg_content_pack \
                        tests.test_hg_content_flk5 \
                        tests.test_hg_content_hgscript \
                        tests.test_hg_content_index \
                        tests.test_hg_content_cli -v

# 1. Full corpus verification pass (authenticated; retail-only, private outputs)
python3 -B tools/hg_content/cli.py corpus "$HG_JAPAN_ISO" \
    --expect-sha256 8ca1513c8417c0a40b838ff5edf22a2360f43bce1b0458693a95a6efdafd994e \
    --scripts-dir local/corpus-scripts \
    --output local/corpus/corpus.json

# A raw, independently authenticated PACK.DAT is equally supported:
python3 -B tools/hg_content/cli.py corpus --pack-file "$PACK_DAT" \
    --expect-sha256 "$PACK_SHA256" --output local/corpus/pack-corpus.json

# 2. Light inventory (no full-image authentication)
python3 -B tools/hg_content/cli.py inventory "$HG_JAPAN_ISO" \
    --output local/corpus/inventory.json

# 3. Extraction (safe: fixed names, exclusive create, no symlink targets)
python3 -B tools/hg_content/cli.py extract "$HG_JAPAN_ISO" \
    --dir 0 --member 2 --out-dir local/corpus/extracted

# 4. Script inspection (dialogue text only in the requested private output)
python3 -B tools/hg_content/cli.py script decode "$HG_JAPAN_ISO" \
    --dir 0 --member 2 --no-text --output local/corpus/d000_m002.json

# 5. Rebuilding (byte-exact for unedited decoded documents; controlled
#    edits are allowed, see the synthetic edit tests)
python3 -B tools/hg_content/cli.py script rebuild local/corpus/d000_m002.json \
    --output local/corpus/d000_m002.bin \
    --expect-sha256 <original-member-sha256>

# 6. Single-script verification receipt
python3 -B tools/hg_content/cli.py script verify "$HG_JAPAN_ISO" \
    --dir 0 --member 2 --output local/corpus/verify-d000_m002.json

# 7. Index build and queries
python3 -B tools/hg_content/cli.py index build local/corpus/corpus.json \
    --output local/corpus/index.json
python3 -B tools/hg_content/cli.py index query local/corpus/index.json --kind stats
python3 -B tools/hg_content/cli.py index query local/corpus/index.json --kind flk5 --entry-type 9
python3 -B tools/hg_content/cli.py index query local/corpus/index.json --kind scripts
python3 -B tools/hg_content/cli.py index query local/corpus/index.json --kind unresolved
python3 -B tools/hg_content/cli.py index query local/corpus/index.json --kind roundtrip
python3 -B tools/hg_content/cli.py index query local/corpus/index.json --kind children
```

## Corpus results (public-safe aggregate)

Measured with the commands above against the authenticated original
Japanese ISO (`8ca1513c…994e`). Per-member hashes, offsets and
classifications are in the private corpus receipt
(`local/corpus/corpus.json`); the public-safe aggregate is committed as
`evidence/content/corpus-summary.json`. Dialogue text exists only in
ignored `local/corpus-scripts/`.

| Measure | Count |
|---|---|
| PACK directories | 273 |
| Members enumerated | 2,541 (2,537 nonzero, 4 zero-length) |
| FLK5 members (parsed-validated) | 2,255 |
| Nested FLK5 children (verified entry targets) | 0 |
| SShd sound containers recognized | 73 |
| HGScript candidates (valid collection header) | 74 |
| HGScript fully validated (records + pool + binding) | 26 |
| HGScript recognized-partial (unresolved binding) | 48 |
| HGScript round-trip byte-exact (all 74) | 74 |
| HGScript round-trip raw-preserved (opaque regions) | 0 |
| Binding count-matched / unresolved | 26 / 48 |
| Unknown members (binary / empty) | 139 (135 / 4) |
| Malformed / failed members | 0 / 0 |

All 2,541 members are accounted for (2255 + 26 + 48 + 73 + 139).

## Verification evidence and limits

- The image is authenticated by a fresh full SHA-256 read compared with
  the expected value supplied on the command line; the expected value is
  never used as proof by itself.
- Every claimed lossless rebuild is compared byte-for-byte with its
  original member bytes during the corpus pass; the receipt records
  per-member SHA-256 and the round-trip verdict.
- A raw copy of an unparsed region is **not** counted as successful
  decoding: members with opaque records are reported under
  `roundtrip_raw_preserved` separately from fully decoded members.
- Synthetic tests (71) cover malformed archives, truncation, overlap and
  integer overflow, nested container errors, unknown event preservation,
  record ordering/padding, exact unchanged rebuilding, controlled edits,
  corrupted decoder output, deterministic indexes, path traversal, symlink
  escapes and unsafe overwrite; each negative control verifies the error is
  actually detected.
- **Not claimed:** opcode-level semantics of HGScript events (the event
  word roles are a supported inference), FLK5 payload semantics beyond the
  loader-documented fields, sound-format internals, or any retail-script
  editing. The original ISO and PACK.DAT are never modified.

## Unknown formats and remaining limitations

- FLK5 entry types 7, 13, 14, 15, 16 are observed in the corpus but have
  no established semantics; members containing them are classified
  `recognized-partial`.
- Area-table collections (shared collection header, no recognized string
  pool) decode as `recognized-partial` with opaque tails preserved.
- String-pool entries whose bytes are not Shift-JIS-with-ASCII remain
  visible as raw bytes.
- Members without a recognized structure are `unknown` and fully
  accounted for in the inventory with offsets and hashes.

## Next action

One high-value continuation: correlate the event-word opcodes across the
74 decoded script collections (frequency/variance of the catalog) to
label the known text/control event set, then validate the sequential
string binding against a second, structurally independent line of
evidence (e.g., fixed-up in-memory pointers observed through the
existing runtime capture tooling).
