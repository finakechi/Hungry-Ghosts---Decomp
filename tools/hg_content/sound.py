"""Recognition of SShd sound containers (bounded, evidence-based only).

Observed on the authenticated corpus: members whose first u32 equals the
member length and which contain an ASCII 'SShd' chunk header followed by
0xffffffff markers. Field semantics beyond that are not asserted.
"""

import struct

SSHD_MAGIC = b'SShd'


def looks_like_sshd(data, member_length):
    """Evidence-driven recognition; returns (bool, evidence dict)."""
    evidence = dict(member_length=member_length)
    if member_length < 1024:
        return False, dict(reason='too-small', **evidence)
    if len(data) < 4:
        return False, dict(reason='unreadable', **evidence)
    first = struct.unpack_from('<I', data, 0)[0]
    evidence['first_word'] = first
    evidence['first_word_matches_length'] = first == member_length
    chunk_at = data.find(SSHD_MAGIC, 0, min(len(data), 4096))
    evidence['sshd_chunk_offset'] = chunk_at
    if chunk_at < 0:
        return False, dict(reason='no-SShd-chunk', **evidence)
    if first != member_length:
        return False, dict(reason='length-word-mismatch', **evidence)
    return True, dict(classification='recognized-partial',
                      kind='sound-sshd', **evidence)


def classify(data, member_length):
    hit, evidence = looks_like_sshd(data, member_length)
    if not hit:
        return None
    out = dict(status='recognized-partial', kind='sound-sshd',
               evidence='SShd chunk header and matching length word; '
                        'field semantics otherwise unasserted')
    out['details'] = evidence
    return out
