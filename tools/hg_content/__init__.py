"""Hungry Ghosts content toolchain.

Archive, nested-resource and HGScript processing for the original Japanese
game corpus. Independent implementation derived from original-disc bytes;
no external implementation code is incorporated.

Modules:
- pack:      PACK.DAT directory/member enumeration and classification.
- flk5:      structural inspection of FLK5 containers.
- hgscript:  HGScript (event-table) decoding, encoding, round-trip.
- sound:     recognition of SShd sound containers.
- index:     searchable content index.
- corpus:    whole-corpus verification driver.

Classification values (deterministic, explicit):

- "parsed-validated":   structure recognized and all fields validated.
- "recognized-partial": structure recognized but partially understood.
- "unknown":            no supported structure recognized.
- "malformed":          supported signature present with invalid fields.
- "unprocessed":        requires inputs that were unavailable.

A recognized signature is never treated as proof of full decoding.
"""

VERSION = '1.0.0'

# PACK directory/member layout (measured on the authenticated original).
DIRECTORY_STRIDE = 2048
MEMBER_RECORD_STRIDE = 16
MEMBER_TABLE_OFFSET = 64
MAX_DIRECTORIES = 4096
MAX_MEMBERS_PER_DIRECTORY = (DIRECTORY_STRIDE - MEMBER_TABLE_OFFSET) // MEMBER_RECORD_STRIDE

FLK5_MAGIC = b'FLK5'
FLK5_MAGIC_WORD = 0x354B4C46  # little-endian 'FLK5'


def bounded_u32(offset, size, context):
    """Reject negative/absurd ranges before any arithmetic."""
    if offset < 0 or size < 0:
        raise ValueError(f'{context}: negative range {offset:#x}+{size:#x}')
    if offset > 0xFFFFFFFF or size > 0xFFFFFFFF:
        raise ValueError(f'{context}: range exceeds 32-bit space {offset:#x}+{size:#x}')


def json_dump(obj, path):
    """Deterministic JSON output (sorted keys, fixed separators, trailing LF)."""
    import json
    text = json.dumps(obj, sort_keys=True, indent=2) + '\n'
    with open(path, 'x') as fp:
        fp.write(text)
