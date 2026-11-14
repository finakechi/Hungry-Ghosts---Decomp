# M4 FLK5 fixup evidence

Reference: original Japanese SCPS_150.46, SHA-256
`a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61`.
[Original instruction packet](functions.json): eight functions, **395 words /
1,580 bytes**. All bytes verified against file-backed ELF segments with pinned
PS2DInfo. Ghidra's discontinuous body ranges exclude unreachable alignment gaps.

| Entry | Reconstruction | Words | Exclusive end (outer span) |
|---|---|---:|---|
| 00137ed0 | hg_resource_fixup | 289 | 00138370 |
| 001389d0 | hg_resource_map_address | 43 | 00138a7c |
| 00138af0 | hg_flk5_entry | 15 | 00138b2c |
| 00138b30 | hg_flk5_type | 15 | 00138b6c |
| 00138b70 | hg_flk5_count | 8 | 00138b90 |
| 00138b90 | hg_flk5_size | 8 | 00138bb0 |
| 00138560 | hg_flk5_disable | 14 | 00138598 |
| 001385a0 | hg_resource_base_delta | 3 | 001385ac |

Fixup takes **resource, selected texture index, member ID**. Both M2 call sites
retain the descriptor ID in a2; its omission from the previous C boundary is fixed.
ID 3d/7d/92 rebases a raw chain at 3f00/3b00/3600 and calls external 152ae0.
Other IDs enumerate FLK5's tagged 24-bit offsets. Type 1 entries enter a 64-word
model list; type 9 enters a 16-word texture list, except ID 999999 skips textures
and guards repeated processing with resource+0c's byte. IDs 7c/96/22f select bases
3b00/1e00/1e00; default is 3200. ID 7c first replaces texture descriptors with
ff000000 and subsequently calls external 152ae0 on the selected texture.

The selected texture's old +08 determines base delta. Model records whose first
word is not ffffffff go to external 1386e0; their +30 linked offset is reloaded
after that call, and a nonzero link gets another call. Every collected texture
uses M3's linked rebase at the selected absolute base. Normal FLK5 returns its
old end rounded to 128 bytes and writes delta to header+08; ID 7c returns the
first texture rounded to 128 bytes. No collected textures means return zero.

Helper 1389d0 copies a 32-byte table from 3e0260, computes signed division and
remainders by 32/8/4, and returns a signed-byte permutation plus group offset.
It is **not allocation**. M3's caller aligns end to 32 bytes and discards this
return. Negative helper inputs can address bytes before the table on its stack.

The C API's explicit guest scratch frame preserves original stack local arrays,
including overflow into adjacent arrays, without native array overflows. This is
an adapter, not an original fifth parameter or full ABI implementation. Scratch
must be aligned and separate from live data; arbitrary malformed accesses into
omitted register-save slots are outside equivalence. Word inputs use canonical
sign-extended 32-bit ABI values in the instruction model. The existing bus owns
fault handling; native C does not invent format validation or termination guards.

The combined `hg_resource_worker_fixed(env, frame)` forwards the existing bus,
read state and external operations, resolving fixup and address permutation in C.
The old `hg_resource_worker` seam remains for isolated M2 dependency tests.
Neither composition entry is counted as a new original function.

[Detailed report and commands](../../docs/M4.md),
[model results](../../evidence/m4/differential.json), and
[independent runtime comparison](../../evidence/m4/runtime-comparison.json).
Geometry/material processing at 1386e0 and upload at 152ae0 remain explicit,
argument-tested external boundaries; the selected retail path calls neither.
