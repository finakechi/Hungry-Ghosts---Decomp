# Hungry Ghosts — start here

## M7 resource preparation — 2026-11-15

**The selected downstream resource branch is implemented and independently
verified.** The fixed worker now resolves `130020 -> 12fe90` and their indexed /
context directory loaders. Four original routines add **204 words / 816 bytes**:
M1–M7 total **38 functions / 2,952 words / 11,808 instruction bytes**.

Two fresh original-image PCSX2 boots verify directory loads **6b, 4c and 85**,
**2,134,016 newly read resource bytes**, all 2,600 handle bytes, arena/task/ready
state and retention of c6/AREA1 through worker completion. Audio remains external
under a separately verified resource-memory/endpoint contract; no expected output
is replayed. Later AREA1 state-0 dispatch is observed, with actor construction
and activation still explicit boundaries. Upload was not reached.

**136 synthetic tests**, **960 new instruction cases + 4 complete workers**,
all **204 new instruction addresses**, M1–M6 regressions, strict C, ASan/UBSan,
input/evidence checks and two compiled behavioral negative controls pass.
[docs/M7.md](docs/M7.md) starts with Resume here and records exact boundaries,
commands, evidence classes, limitations and local delivery identities. No M7
push, PR, merge, remote CI or private upload is claimed.

**Phase 1 merge addendum — 2026-11-15:** HG documentation PR
[#5](https://github.com/finakechi/Hungry-Ghosts---Decomp/pull/5) and PS2DInfo PR
[#8](https://github.com/finakechi/PS2DInfo/pull/8) are verified merged at
`280a1a296a877cc14709a6adca0505569f1a57c7` and
`88fa49b64817fb98e59182c9cef7819b7a7d8746`. The original review-proposal wording
below is preserved as historical state. This session has javac 21 and a passing
full local suite; it does not revise the earlier environment failure report.

ONE next action: trace the observed AREA1 state-0 six-argument `18a330` setup
before defining the next actor implementation boundary. Publication requires
Noah's separate explicit approval; no final port/rebuild strategy is selected.

## Phase 1 documentation update — 2026-11-15 (review proposal)

The current starting commands are now at the top of
[DEVELOPMENT.md](DEVELOPMENT.md): **Python 3.10+, C11, and JDK 21 with `javac`**
for the full 126-test synthetic suite. The older M1–M5 procedures are still
available there as historical reproduction steps. No M6 implementation or
original-game evidence changed. The new guide is **proposed for review, not
merged into `main`**. Local tests found one missing-`javac` environment error; the
separately verified M6-head GitHub CI was successful. See the
[Phase 1 master note](MASTER_HANDOFF.md#phase-1-development-guide-refresh--2026-11-15-review-proposal)
for the actual checks and next approval boundary.

## M6 selected PACK processing — 2026-11-14

**All 13 marked PACKc6 members now use real reconstructed processing.**
Four new functions add **241 words / 964 bytes**, bringing M1–M6 to **34 functions /
2,748 words / 10,992 original bytes**. The existing HGResourceEnv/bus/worker
composition resolves geometry/material preparation, descriptor addressing and
finite-normal radius calculation; linked texture rebasing and publication reuse
M1–M5. Upload is not reached by this operation.

Fresh original-game debugger observations independently match **1,184,000 bytes
across all 13 transformed members**, the complete **1,228,800-byte** bulk read at
loader return, 2,600 handle-table bytes and 36 arena bytes. All 13 handles and the
1,227,648-byte logical buffer survive worker completion; the sector-read tail is
legitimately reused by the next allocation. Fresh AREA1 state-1 publication and
reuse across a later kind-1/directory-69 request also pass. No captured output is
used to calculate the selected transformation.

**126 synthetic tests**, **565 new instruction comparisons**, all 241 new
instruction addresses, M1–M5 regressions, strict C, ASan/UBSan, original-byte and
evidence checks pass. Three compiled M6 defects and altered instruction evidence
are detected. [M6 report](docs/M6.md) gives exact commands, member classifications,
hashes, provenance (including the separately corroborated first-call arguments),
engine/FP boundaries and the continuation point. Private recovery and review
receipts are recorded in the milestone PR, avoiding a source/backup reference cycle.
Later engine loaders, exceptional FP/VU flags/timing, general rendering and
whole-game equivalence remain outside this result. The old M5 mock-based PACK
comparison stays explicitly labeled as a historical/regression measurement.


## M5 delivery addendum — 2026-11-14 (America/New_York)

The delivery-blocked statements below describe the original session and are
retained as contemporary evidence. Later delivery is verified:
[PR #3](https://github.com/finakechi/Hungry-Ghosts---Decomp/pull/3) merged as
`071dedb19879b3cd2625e08456e689adaab57e7b`, containing final M5 commit
`a8e6e312a2c1695de0fff1930fad386efb08c5ee`. A fresh origin fetch confirms the
merge is the current main HEAD. All four M5 CI jobs passed (Python 3.10/3.14,
push and pull request; workflow runs 43667224422 and 43667301683).

Independent private snapshot `20261115T022539Z-b6831f1d96c0` was uploaded,
downloaded and restored, as recorded in the PR delivery receipt. This session
reverified the retained downloaded copy: **1,323 files / 404,840,629 bytes**,
then performed another exclusive isolated restore, cloned its complete-history
Git bundle and passed `git fsck --full`; the final M5 commit is present.
Archive SHA-256:
`1c7fefd694c8d4d4778336734e36c1255f1c7833c06becd20adee40688957b98`;
manifest SHA-256:
`c13be9d0170f38f57951abbb004e53a71aa8445a4bb3e5c3006714c9cd1fec56`.
The transport receipt is historical; this session's recheck used the retained
download and does not claim another fresh Drive download.


## M5 current result — 2026-11-14

**Selected AREA1 transition verified locally; private Drive delivery is blocked.**
The interrupted work was recovered on `research/m5-area-lifecycle`, based on merged
M4/main `28a5873`, with all partial source, tests, exports and captures preserved.
The still-paused original-game emulator was resumed at general loader `130900`;
the milestone was not restarted. [M5 report](docs/M5.md) records the stopping point,
exact commands, evidence classes and continuation gates.

Request kind 2 / directory c6 selects AREA index 10 and reads A1_B00.BIN into
`a87800`, replacing bytes independently verified as the original A0_B09.BIN.
The entire **30,720-byte** AREA1 file matches original disc bytes at finalization,
FlushCache(2), callback execution and later reuse. Callback `a8a070`, actor state 1,
publishes actor/data pointers `4d8150/4d8420`. A later kind-1/directory-69 request
completes while retaining AREA1, then its callback executes and publishes again.
This is observed retention/reuse; no free or unload is inferred.

Six original functions add **1,092 words / 4,368 bytes** (five resident bodies and
one raw AREA callback), bringing M1–M5 to **30 functions / 2,507 words / 10,028
bytes**. All 1,897 new instruction comparisons pass and execute all recorded new
addresses. **120 synthetic tests**, M1–M4 differentials, original-byte/structure
checks, ASan/UBSan and compiled negative controls pass.
[Runtime comparison](evidence/m5/runtime-comparison.json) binds 19 debugger pairs
and separates recovered captures from fresh resumed observations.

PACKc6's 13 marked fixups remain mocked for its loader-state comparison; their
payload transformations, actor construction/dispatch, resident18c280, rendering,
and retail state-0 initialization are not claimed reconstructed/runtime-equivalent.
The later menu-consumed PACK has three reported downstream word changes with no
assigned call semantics. These limits do not change the measured AREA1 bytes.
There is no whole-game, compiler-byte-match or final-port claim.

The API session lacks a Drive connector, its browser host is unavailable, and
rclone/configuration are absent. Local preservation and restore results are in
[evidence/m5/preservation.json](evidence/m5/preservation.json). No independent M5
Drive backup, push or PR is claimed. Finish the private upload/download/restore
gate and publication audit before the authorized normal branch push and PR to
main. Leave the PR open; do not merge. Current M4 history is already merged via
PR #2; older no-publication claims below are historical.

Implementation commit: `2609da9751bbcdb83a68f05b714ce3c95df2525d` (local).
An additive local snapshot passed full member verification, isolated restore,
recovered Git clone and fsck: **1,323 files / 404,833,472 bytes**, including **328
private M5 files**. Restored HEAD equals that implementation commit. Original
media/BIOS/extraction inventory matches the previous M4 snapshot. This is a
same-disk recovery result, **not** an independently downloaded Drive backup. The
later preservation-only handoff commit is outside this immutable snapshot.
The implementation publication audit found zero heuristic findings. No push/PR
or M5 remote CI was attempted while the Drive gate remains blocked.

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

Implementation commit: `36ebbd8b5e846f968c357e0e4011ac922b92fb29` (local).
The existing private Drive backup passed fresh download verification, full isolated
restore, recovered Git clone and fsck: **947 files / 365,128,227 bytes**, including
90 M4 files. Both earlier snapshots and the original configuration/media/research
were preserved. [Preservation receipt](evidence/m4/preservation.json) records hashes
and limits. This immutable backup covers the implementation commit; the later
preservation-only handoff commit is outside it. No push, PR or merge occurred.

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
