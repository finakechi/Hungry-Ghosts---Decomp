# Hungry Ghosts — master handoff

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


**Version 2.0 — 2026-11-14, America/New_York.** Synchronized with START_HERE.md.
The long-term objective remains executable decompilation of the original Japanese
game. No matching, static-recompilation or native-port outcome has been selected.

## Current M2 result

M2 reconstructs a complete bootstrap resource-loading control path with explicit
kernel/CDVD/fixup dependencies, plus the connected AREA read/finalization helpers.
**11 additional functions, 684 instructions / 2,736 original bytes** are now
reconstructed. Combined M1+M2 coverage is **15 functions, 967 instructions /
3,868 bytes**; no whole-game percentage or byte match is implied.

Implementation/evidence commit: `3ec079fc3df953f67462682f4a1e25565464475a` (local). A subsequent handoff-only commit records this identity; inspect HEAD for the final documentation commit.
Branch: `research/m2-resource-loader`, based on verified M1
`d67d8ef1aa06b347e101c5ce3af71b811f368351`. The initial working tree was clean.
No user changes were replaced. GitHub read access works; remote main remained
`d3cc12e7cffd4bcbdd07a742a0e2f0423042b884`, with no open PRs when checked.
No push, PR, merge or publication was performed.

### Revalidation and shared tooling

The original ISO and ELF hashes/sizes passed PS2DInfo's input verifier. M1's 283
instruction rows passed its current ELF-byte verifier, its original 32 synthetic
tests passed, and all 1,448 M1 differential checks passed again after the M2
interpreter extensions. [M2 evidence](evidence/m2/) preserves the actual results.

The original local PS2DInfo checkout and its unrelated untracked file were left
untouched. An ignored reference checkout verified remote main at
`2e4a226e00d33ab35391aacade71b55826e5f52f`, newer than the supplied
`af62918c7b2d1b0a4c435fc2176de14111c69e68`. The supplied revision is an ancestor;
tools/tests have no differences between them. M2 uses the existing shared
`verify_elf_evidence.py`, `validate_evidence.py` and `check_local_inputs.py` rather
than adding generic replacements. [Pinned hashes](evidence/m2/shared-tools.json).
M1 compatibility tools remain; no generic shared implementation was copied here.

Readelf confirms no .mdebug, an empty .symtab/.strtab, and the same Metrowerks
compiler comment. No usable retail debug symbols or matching toolchain emerged.

### Established operation and new source

Main `0011d830` registers callback `00121810` via `001432c0`, then dispatches it
through `00143240`. Callback phase 0 requests kind 0 / directory 0 via `00130150`.
The request tail-jumps to `00143360`, carrying worker entry `00130220`. This
creates/starts a normal EE kernel thread; the main-context dispatcher itself is
not the resource thread scheduler. Frame interrupt `0011df10` wakes the normal
thread while its active halfword is 1. The recovered worker's job 0 calls
`001306a0`, which reads a PACK directory, runs `001303e0`, reads its bulk range,
publishes resource handles and updates arena pointers through M1's read group.
The worker clears request-busy, then `001434f0` clears active and exits/deletes
the thread. [Byte-checked context and edges](analysis/resource_loader/context.json).

[Source](src/resource_loader.c), [API/layout](src/resource_loader.h),
[full instruction evidence](analysis/resource_loader/functions.json), and
[detailed semantics/dependencies](analysis/resource_loader/README.md) preserve:

- `001431f0` init; `00143360` thread scheduling; `001434f0` completion.
- `00130150` raw request; `00130170` ID-remapping request; `00130220` worker.
- `001303e0` special-member phases; `001306a0` complete bootstrap PACK loader.
- `0012fbc0` AREA selection; `00131810` AREA read/poll; `00131dd0` finalization.

The AREA relationship is genuine: general PACK loader `00130900` calls the AREA
helper for mode 8. The helper reads a selected AREA file via M1, then performs
FlushCache(2) and a saved-header-size zero-tail call. Its real runtime use beyond
bootstrap was not observed; its reconstruction is synthetic/differential tested.
The general PACK loader, other worker loaders, codecs, fixups and kernel/IOP
implementations remain explicit dependencies, not claimed completed source.

Original widths, signedness, tail/indirect calls, descriptor layouts, delay slots,
counter wrap, busy rejection, unchecked CreateThread failure and post-call reloads
are preserved. The extra C environment, memory bridge and resumable bootstrap
helper are host testing adaptations. They do not replace real threads, interrupts,
or guarantee concurrent/overlapping-buffer equivalence.

### Runtime evidence of actual loading

Isolated PCSX2 2.8.2 fast-booted the hash-verified original ISO, optional patches
disabled, no gameplay input/save state, memory cards disabled. Read-only PINE
collected **2,147 samples** at requested 120 Hz. Active job 0 was observed from
2.315 to 2.676 seconds of sampling; completion first appeared at 2.684 seconds.
The captured request ranges were directory `[offset 0, length 2048]` into
`003f3b00` and bulk `[559104, 346880]` into `00b00000`, repeatedly observed 26 and
18 times respectively. Loader phases 1–5, special phases 3/7/b, busy and active
completion transitions were observed.

The loaded 2 KiB directory header equals original PACK directory 0. All five
resource handles equal the original members' calculated addresses. Arena end
`00b55000` and derived arena pointers match the reconstructed calculation.
[Activity/data verification](evidence/m2/runtime-loading.json). All **967** M1+M2
instruction words also match runtime memory through the shared byte verifier:
[code-presence check](evidence/m2/runtime-code.json).

The first FLK5 header retains magic/count but its +08 word changes from 1100 to
3200. This is observed post-read transformation; the actual fixup semantics are
unreconstructed. The verifier deliberately records this difference instead of
claiming the entire transformed payload equals the disc. Sequential samples are
not atomic or a PC/breakpoint call trace; they do substantiate actual resource
activity beyond M1's code-presence observations. No AREA gameplay, all-error-path,
asset-decoding or full-game runtime equivalence is claimed. The owned emulator
was stopped cleanly; project headless Ghidra operations finished.

### Final verification results

Exact commands and reproducible setup are in [DEVELOPMENT.md](DEVELOPMENT.md).
Current result records are under [evidence/m2](evidence/m2/).

| Check | Actual outcome |
|---|---|
| `python -m unittest discover -s tests -v` | 49 passed, 0 failed/skipped |
| Shared `verify_elf_evidence.py --require-all-bytes` on M1 / M2 packets | 283 / 684 instruction rows verified, 0 unknown |
| Shared `validate_evidence.py` on both packets | Both structurally valid |
| `python tools/verify_disc_read.py ...` | 1,448 passed |
| `python tools/verify_resource_loader.py ...` | 970 randomized invocations + 12 end-to-end sequences passed |
| ASan/UBSan synthetic C integration executable | Passed, no sanitizer error |
| `python tools/hg_m2_negative_controls.py ...` | All 9 deliberate defects detected |
| `python tools/verify_loading_runtime.py ...` | Observed activity, data/handles and arena pointers passed |
| `python tools/hg_loading_code_check.py ...` | 15 runtime bodies / 967 words passed |

Negative controls mutate disposable copies only: one evidence byte row, six C
defects, a runtime handle and removal of observed activity. They must produce
specific mismatches, not arbitrary compile failures. [Results](evidence/m2/negative-controls.json).
The restricted instruction runner is independently implemented and rejects
unsupported instructions. External operations remain deterministic mocks; the
new 970 cases include nonzero directory IDs, error/busy states and rounding/wrap
boundaries. Remote CI was not run or claimed. Original images/BIOS/databases were
not patched; proprietary inputs/raw captures/private paths remain out of Git.

### Ghidra and inventory continuity

M1 baseline/working projects are preserved. `local/ghidra/HungryGhosts_JP_M2.gpr`
is a separate copy. Only two missing entries were explicitly discovered:
worker `00130220` and wake handler `0011df10`. Analyst labels for 11 reconstructed
functions and four globals were saved and re-exported in M2. The memory-block map
is unchanged. Inventory now has **4,558 candidates**: 15 reconstructed/model-tested,
12 partially analyzed/byte-checked and 4,531 unreviewed. Known extension mirror/
rom0 limitations from M1 still apply. No whole-game completeness claim follows.

### Acceptance and shutdown decision

All eight M2 acceptance criteria are supported within the explicitly bounded
bootstrap operation and AREA reconstruction: M1 revalidation, connected path,
source integration, passing regressions/differentials, byte checks/negative
controls, substantive runtime observations, reproduction tools and synchronized
handoffs. There is no unresolved technical blocker to this milestone.

The user authorized shutdown only if no known unsaved work would be lost.
Preflight found a pre-existing unrelated Kate window reporting
`windowModified=true`, with Save All enabled. It was neither saved nor closed.
The final safety recheck still reported unsaved changes. **Shutdown was not attempted; MainPC was left running.** The physical host was
verified as MainPC/CachyOS, PID 1 systemd, no detected virtualization; logind says
poweroff is permitted. No forced shutdown, privilege escalation or attempt to
bypass the unsaved-work condition is authorized. A final gate/result receipt is
kept in ignored `local/m2/final-report.json`; do not infer shutdown from M2 success.

### Next substantial milestone

Recover real fixup `00137ed0` and its data transformations, then extend the
reconstruction through general PACK loader `00130900` / `00131420` and observe
an actual AREA transition. Add breakpoint or equivalent PC-correlated traces to
strengthen the current sampled-state evidence. Kernel scheduling, IOP/CDVD,
remaining codecs and overlay activation remain research dependencies. Continue
executable work without choosing the final project track on the user's behalf.

## Historical M1 record

The following sections preserve M1's original findings and continuation point;
the current M2 results above supersede their progress/next-action summaries.

## M1 outcome and repository state

The designated directory initially contained three ISOs and a 4 MiB BIOS, with no
Git checkout, instructions, code, tests or Hungry Ghosts Ghidra database found.
The existing remote repository was cloned into `decomp/`; baseline main and
origin/main were `d3cc12e7cffd4bcbdd07a742a0e2f0423042b884` (README only).
Work is on local branch `research/m1-executable-foundation`. GitHub authenticated
metadata access succeeded. No push, PR, merge or publication was performed.

The historical `Hungry_Ghosts_PS2_Decomp_Research_Handoff_2026-11-13.md` was read
as research context. Its unverified disc/build claims are now superseded by the
measured records below. No historical implementation was assumed to exist.
PS2DInfo was inspected at local revision `ab295d765728700f107321c5771b62783bd2e83c`;
its instructions, handoffs, evidence contract and validator informed this work.
Its unrelated untracked file was left alone. This does not establish its current
remote state. No changes were made to other projects or their Ghidra databases.

## Verified input identity

All three images are readable ISO9660/UDF images of **4,389,568,512 bytes**. The
BIOS is present/readable, 4,194,304 bytes; no BIOS material is tracked. Full image
hashes and hashes/LBAs/sizes for all 26 ISO files are in [build records](evidence/builds/).
Original media were opened read-only; executable copies are under ignored local/.

| Build | ELF SHA-256 | Calculated ELF word-XOR CRC |
|---|---|---|
| Japanese original | `a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61` | `91AA4B28` |
| Standard English v1.0 | `c12bd48ea7bd3b4199b29db0fb2b0f4b5e064a408fbcc23669d4ca14ecac72d9` | `B3C0E286` |
| Modern Controls v1.0 | `4bc0d365858c3b62f0f97edafc8cd2dc24afd2d96d0d2d3b1fdeb9e50c36414d` | `798E3212` |

The original image SHA-256 is
`8ca1513c8417c0a40b838ff5edf22a2360f43bce1b0458693a95a6efdafd994e`.
Its SHA-1, MD5 and CRC32 also match the pinned translation author's reported
original. Both patched-image SHA-1 values match that author's separate editions.
See [source attribution](docs/sources.md) and [measured comparison](evidence/build-comparison.json).
Only `/SCPS_150.46` and `/DATA/PACK.DAT` differ among ISO logical files. Relative to
Japanese, Standard changes 881 ELF bytes and Modern changes 1,466; Standard versus
Modern changes 585 bytes. Equal addresses are not assumed to imply equal code.

SYSTEM.CNF identifies `BOOT2 = cdrom0:\SCPS_150.46;1`, VER 1.02, VMODE NTSC.
Serial **SCPS-15046** is derived from that actual boot filename. Each boot ELF is
3,062,752 bytes, ELF32 little endian, MIPS flags `0x20924000`, entry `0x00100008`.
Readelf and the independent parser agree. The ELF comment reports
`MW MIPS C Compiler (2.4.1.01)`; this is a fingerprint, not a recovered build chain.

## Executable map, components and Ghidra

The [build manifest](evidence/builds/japan.json) retains all 16 PT_LOAD records,
including overlapping empty segments. PT_LOAD[0] maps file `[0x800,0x2eb180)` to
EE `[0x00100000,0x003ea980)`, with memory through `0x00a87800`.
PT_LOAD[1..13] share `0x00a87800`, have zero file sizes and differing memory sizes
whose union ends at `0x00ae5000`. There is a zero-size segment at `0x00ae5000` and
a 16 KiB scratchpad segment at `0x70000000`. Do not flatten these into 16 separate
resident code bodies.

Thirteen AREA*.BIN files contain `MWo3` headers with a common `0x00a87800` value.
The main file lookup table contains **22 slots referring to those 13 files**, with
repeated AREA2 filenames. These are overlay candidates; executable payload layout
and the full overlay lifecycle remain to be analyzed. `0x00131810` reads an AREA
file through the reconstructed read group and calls `0x00131dd0`, which reads
header +0x14, calls FlushCache(2), then calls a memset-like helper with arguments
`(buffer + file_size, 0, header_word_14)`.
This supports a load/zero-tail interpretation, not a complete overlay specification.

IOP components are IOPRP271.IMG plus LIBSD, MCMAN, MCSERV, PADMAN, SDRDRV, SG2IOP,
and SIO2MAN IRX files. Their hashes and extents are recorded. `0x0011db30` loops on
IOP reboot/sync-style calls and loads seven module paths. Module internals/imports
are not reconstructed. PACK.DAT and STREAM.DAT are the principal asset/stream
containers. VU microprogram identities and their upload paths remain unknown;
the extension's VU memory regions are not evidence that microprograms were recovered.

Ghidra **12.1.4**, Emotion Engine Reloaded **2.1.38**, language
`r5900:LE:32:default`, compiler spec `default` were actually used. Java, VS Code,
compiler and extension hashes are in [toolchain.json](evidence/toolchain.json).
The baseline import and working database are both in ignored `local/ghidra/`:

- `HungryGhosts_JP_M1.gpr`: preserved first import, 4,520 automatic candidates.
- `HungryGhosts_JP_M1_Work.gpr`: RAM correction plus analyst labels, 4,556 candidates.

The initial ELF import omitted uninitialized PT_LOAD tails. The empty PROGBITS
sections appear related, but the exact loader cause was not isolated.
HGMap.java restores `[0x003ea980,0x00a87800)`
as uninitialized BSS, the overlay arena through `0x00ae5000`, and remaining EE RAM
through `0x02000000` for analysis. The last region is physical RAM, not asserted
ELF BSS. Original databases in another game's project were never opened for writes.
HGAnnotate.java adds seven function names and five globals to the working copy;
a subsequent read-only export confirmed the saved labels.

The extension reports a rom0 mapping conflict and generates suspect mirror/BIOS
ranges. They remain visible as tool output, explicitly **not** validated hardware
maps. All byte claims in this milestone use the first original PT_LOAD in EE ram.
No BIOS import, peripheral-map repair or destructive import was performed.
[Map](analysis/ghidra-map.json), [inventory](analysis/function-inventory.json), and
[reproduction commands](DEVELOPMENT.md) preserve the useful executable map.
The inventory records a pre-annotation snapshot plus explicit analyst names for
the four reconstructed entries and analyzed status for eight context entries;
it is not a count of confirmed original functions.

## Connected executable findings

The byte-backed call path is entry `0x00100008` → main loop `0x0011d830` → disc
lookup `0x0012fbf0`. That lookup searches PACK.DAT, stores its LBA/size at
`0x003ead20`, then fills 22 AREA file records at `0x003f3a30`.
Main loop calls `0x00143240` (scheduler lead); PACK loader `0x00130900` invokes
read setup/poll and reads directory `id << 11` into `0x003f3b00`.
Scheduler-to-loader ownership is an open edge; no runtime call trace was captured.
[Caller context](analysis/caller-context.json) retains byte-checked ranges, hashes
and direct-call addresses for eight partially analyzed context functions.

| Original entry | Analyst-assigned reconstruction | Body bytes | Status |
|---|---|---:|---|
| `0x001319c0` | hg_read_setup | 28 | Original-byte checked; reconstructed; model differential tested |
| `0x00131bf0` | hg_read_start | 428 | Same |
| `0x001319e0` | hg_read_continue | 492 | Same |
| `0x001318f0` | hg_read_poll | 184 | Same |

[C reconstruction](src/disc_read.c), [layout/callback contract](src/disc_read.h),
[full 283-instruction evidence](analysis/disc_read/functions.json), and
[semantic notes](analysis/disc_read/README.md) include addresses, ranges, original
memory-order bytes, assembly, incoming references, interpretations and limits.
These are understandable reconstructed functions, not exported pseudocode.
The host callback/context parameters are test seams; they are not original ABI
parameters. Original poll/setup use a singleton at `0x003eacf0`; start/continue
accept its address. External operations at `0x0010f520`, `0x0010fd28`,
`0x0010ff30`, `0x00110268` are still unreconstructed dependencies.

Startup Ghidra pseudocode treats parts of the zero register as undefined in its
quadword clear; the actual `sq zero` instruction must govern interpretation.
The read reconstruction explicitly checks word sign extension, addiu/addu wrap,
branch delay slots, signed offset truncation, unsigned whole-file rounding,
128-bit saved registers, read retries and chunk counters. There is no new
concurrency or input validation silently added to the recovered behavior.

The published control-patch site `0x0010e6d8` has original prologue word
`0x27bdffb0`, retained by Standard and replaced by Modern's `0x3c190096`.
The original routine is an RPC-style controller wrapper. The hook address alone
does not identify the player's movement or camera implementation.

## PACK discrepancy resolved against bytes

[Measured directory scan](evidence/pack-directory.json) finds **273** sequential
records, each 2,048 bytes. The first member starts at **559,104 = 273 × 2,048**;
records 271 and 272 have valid IDs and bounded member extents. The following
sector starts with FLK5 data, not directory 273. Total: **2,541 outer members**,
of which **2,255** begin with FLK5. This does not count nested files or prove all
FLK5 semantics. The scan derives the table boundary, validates every member
extent and never unpacks or repacks assets. The executable loader's directory
dispatch includes ID `0x110` (272), independently corroborating the final slot.
The pinned hgtools Kaitai template's count 271 omits two observed directories;
its Python parser's count 273 agrees with this original image.

## Executed verification and precise limits

- **32 synthetic tests passed** on Python 3.14.9 / GCC 16.2.1. The C11 source
  compiles with `-Wall -Wextra -Werror -pedantic`. See [test log](evidence/synthetic-tests.txt).
  CI configuration uses synthetic inputs only; remote CI was not run or claimed.
  The same 32 tests passed from an export of the staged files with no retail
  inputs, Ghidra databases, emulator profile or third-party Python packages.
- **1,448 local differential checks passed**: 1,400 deterministic randomized
  function invocations and 48 transitions across 12 full request lifecycles.
  [Result](evidence/disc-read-differential.json). The strict independent integer
  instruction runner reads the original ELF; CDVD calls are mocks. Unknown
  instructions fail. This is bounded model equivalence, not a hardware oracle.
- **283 instruction rows / 1,132 bytes match the original ELF**, including
  independently decoded JAL targets. PS2DInfo's pinned validator also accepts the
  evidence structure; that validator does not verify bytes or semantics.
- **PCSX2 2.8.2 runtime observation passed** in an isolated profile, fast boot,
  no input/save state, memory cards disabled, cheats and optional patches disabled.
  Read-only PINE reports running SCPS-15046 / 91aa4b28. All four function bodies
  match the ELF; initialized PACK LBA/size and all 22 AREA records match ISO9660.
  [Runtime record](evidence/runtime-boot.json). Sequential reads are not atomic.
  This verifies code presence and initialization data, not function execution
  coverage or reconstructed behavior in gameplay. Private raw captures/logs and
  the full original decompiler output stay in local/.

The isolated emulator was stopped cleanly after the captures. Its profile and
generated NVRAM remain in ignored local/, separate from the original BIOS.

Original media were not patched. No proprietary binaries/assets, translations,
BIOS or personal absolute paths are intended for the local commit. No original
compiler build, byte match, native game, gameplay runtime trace, rendering/audio
equivalence or full decompilation is claimed.

## Next substantial milestone

Continue the verified original boot executable and saved working database.
Prioritize `0x00143240` / `0x00143360` task ownership, PACK loader `0x00130900`,
member processor `0x001303e0`, AREA read path `0x00131810` and zero-tail helper
`0x00131dd0`. Recover the overlay header/load contract and inspect the original
CDVD dependencies. These connect the reconstructed read group to actual resource
and executable overlay loading. Reproduce the same state transitions at debugger
breakpoints before upgrading the status to runtime behaviorally verified.

Then choose connected gameplay/input/camera targets using verified callers and
build comparisons. Assets/scripts support executable investigation; HGScript
decoding is not EE decompilation. Do not select the eventual project outcome on
the user's behalf. There is no essential missing input blocking continued work;
full overlay layouts, dependencies and runtime call traces remain research work.
