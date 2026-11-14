# Hungry Ghosts — start here

## M4 current result — 2026-11-14

**M4 complete for the selected bootstrap FLK5 resource.** The combined
`hg_resource_worker_fixed` operation connects M2 loading/publication, new fixup
`00137ed0`, M3 rebasing and reconstructed address helper `001389d0`. Eight new
functions add 395 original words / 1,580 bytes: total 24 functions / 1,415 words /
5,660 bytes. Both loader fixup calls now retain the original member ID in a2.

Actual PCSX2 EE breakpoints observed `137ed0 → 1385b0 → 1389d0 → 130834`, then
bootstrap publication and worker completion. The selected directory-0 member's
entire 4,352-byte output, return, five handles and arena match reconstructed C.
**99 synthetic tests**, 1,227 new instruction comparisons (393/395 addresses;
two guarded unreachable instructions), M1–M3 regressions, ASan/UBSan, byte and
safety checks pass. [M4 report](docs/M4.md) separates each evidence level and
provides exact commands; [runtime comparison](evidence/m4/runtime-comparison.json)
binds private captures and original image identity.

Geometry/material `1386e0` and upload `152ae0` remain explicit boundaries; neither
is called by the selected retail member. Other variants have model evidence,
not retail runtime equivalence. No whole-game/byte-match/native-port claim.
Starting branch was clean `research/m4-resource-fixup` at merged `main` commit
`7e54e15`; older PR/branch/status claims below are historical and superseded here.
No M4 push, PR or merge is authorized or performed. Original/private research is
preserved; existing private backup destination is unchanged.

Next: AREA overlay activation/lifetime through an observed real transition,
including general PACK or type-1 fixups only where that path requires them.

## GitHub and private recovery integration — 2026-11-14

M1–M3 history and source have been revalidated for authorized GitHub publication.
The integration adds additive private backup tooling, 17 synthetic recovery/audit
tests and [repeat/restore procedures](docs/BACKUP.md). The local suite has
81 passing tests; M1/M2/M3 model and original-byte checks pass again.
[Integration report](docs/INTEGRATION.md) records scope and verification limits;
the report records actual publication/recovery results as they are observed.
The first private Drive copy passed full downloaded verification and isolated
restoration: 815 files / 359,144,294 bytes. Its Git bundle clones and passes fsck.
Latest private transport/verification receipts are in `local/integration/`.
Implementation `033e79c` is pushed on `research/m3-evidence-safety`;
[PR #1](https://github.com/finakechi/Hungry-Ghosts---Decomp/pull/1) targets `main`
and is open/unmerged. Its initial push and PR CI passed on Python 3.10/3.14;
current head results are visible in PR checks and private final receipts.
Historical milestone sections below retain their original no-publication status.
No merge, shutdown, final project-track choice or backup schedule is authorized.

## Current M3 update — 2026-11-14

Evidence safeguards and a connected rebasing dependency are implemented on local
`research/m3-evidence-safety`. [M3 report](docs/M3.md) gives recovery scope, exact
checks and limits. 13 safeguard tests passed before retail work; final synthetic
suite has 64 tests. `hg_resource_rebase` at `001385b0` adds 53 original instructions
/ 212 bytes: combined reconstructed coverage is **16 functions / 1,020 words /
4,080 bytes**. 256 new bounded differentials pass. Fresh PCSX2 verifies the new
body's code presence only; full fixup and AREA execution remain unresolved.
Same-disk hash-verified recovery snapshot and Git bundle are under ignored
`local/recovery/20261114T171347Z`; off-disk recovery remains unknown. Original
media and original Ghidra databases were preserved. No push, PR or merge.

The M2 sections below are retained as historical milestone evidence; their
counts, branch and next-step summaries are superseded by this update.


**Version 2.0 — 2026-11-14, America/New_York.** Detailed status:
[MASTER_HANDOFF.md](MASTER_HANDOFF.md). Read AGENTS.md first.

M2 reconstructs a complete bootstrap PACK request/worker/read/handle-publication
operation and the connected AREA selection/read/finalization helpers, retaining
explicit kernel/CDVD/fixup dependencies. **11 new functions / 2,736 bytes** join
M1's four disc-read functions: **15 functions / 3,868 bytes / 967 instructions**.
The final matching/reconstruction/recompilation/port outcome remains undecided.

The original ELF SHA-256 is
`a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61`.
Entry is `0x00100008`; PCSX2 CRC is `91AA4B28`. Separate original/Standard/Modern
build manifests and comparative changes are under evidence/. Original ISO hashes
and both patched ISO SHA-1 values match the pinned translation reference.

- [M2 C](src/resource_loader.c), [full M2 instruction evidence](analysis/resource_loader/functions.json),
  and [execution path / semantics](analysis/resource_loader/README.md).
- [M1 C](src/disc_read.c) and [M1 instruction evidence](analysis/disc_read/functions.json).
- [Function inventory](analysis/function-inventory.json) and [Ghidra map](analysis/ghidra-map.json).
- **49 synthetic tests**, **1,448 M1 differential checks**, **970 M2 randomized
  invocations + 12 end-to-end sequences**, sanitizers and **9 negative controls** pass.
- [PCSX2 activity](evidence/m2/runtime-loading.json): 2,147 samples show directory 0
  and bulk reads, loading phases, completion, five handles and arena pointers.
  [All 967 instruction words](evidence/m2/runtime-code.json) match runtime memory.
  These are sampled states/data, not a PC call trace or full-game equivalence.
- PACK contains **273 directories**, 2,541 outer members, 2,255 FLK5 signatures.
  The 271-versus-273 discrepancy is resolved for the measured Japanese image.

The existing repository was cloned as `decomp/` beneath the original-media
directory. Local branch: `research/m2-resource-loader`, based on M1 `d67d8ef`.
M2 implementation/evidence commit: `3ec079fc3df953f67462682f4a1e25565464475a` (local); the final handoff commit follows it.
Remote main remained `d3cc12e`; no open PRs, push, merge or publication.

Ignored `local/ghidra/HungryGhosts_JP_M1.gpr` preserves the initial import;
`HungryGhosts_JP_M1_Work.gpr` has M1's RAM correction and analyst labels.
Separate `HungryGhosts_JP_M2.gpr` has the new worker/wake discovery and M2 labels;
the memory blocks are unchanged. Inventory has **4,558 automatic candidates**,
including 15 reconstructed/model-tested and 12 partially analyzed entries.
Ghidra 12.1.4 / EE Reloaded 2.1.38 / `r5900:LE:32:default` were used. Original
media, BIOS, extracted files, private logs and captures remain outside Git.

```sh
python -m unittest discover -s tests -v
```

[DEVELOPMENT.md](DEVELOPMENT.md) gives exact commands. PS2DInfo remote main was
verified at `2e4a226e`, newer than the supplied `af62918c`; its tools are unchanged
between those revisions and are used directly. The older unrelated checkout was
not changed.

**Shutdown safety:** an unrelated Kate window reports unsaved changes. It was
preserved. The final safety recheck still reports modified=true: **shutdown was
not attempted and MainPC was left running**. M2 completion does not override this. Final gate/result receipt:
ignored `local/m2/final-report.json`.

Next: reconstruct real resource fixups and general PACK loader paths, observe an
AREA transition, and add PC-correlated runtime traces. No .mdebug or usable retail
symbols were found. Read the master's verification limits before changing status.
