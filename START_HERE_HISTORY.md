# Hungry Ghosts — start here

## Verification infrastructure, Phase 3 (reviewed integration) — 2026-11-27

**Resume here.** Phase 3 reviews and integrates the verification-only
infrastructure from Phase 1/Phase 2 into `main` (base `86a5670852d47c4ead140e5c8e40a5289f773053`,
PR #21 merge) on branch `integration/verification-infrastructure-phase3`,
preserving the exact verified Phase 2 code commit
`734e328fe7c9cea90b2dd746c0ea1fff0647dbc0` (`source_sha256`
`c66bc38ffb97f13b9e2e2da13b7a4df8ea2a1b25fa97b86868fd927b551524f8`) without
folding in unpublished M19–M24 branches.

- **Verified evidence closure:** Zero files in `src/`, `tools/`, `tests/`,
  `ghidra_scripts/`, `analysis/`, `evidence/`, or `config/` changed after
  `734e328`. All five MainPC Phase 2 receipts (`m12-20261127T190644Z-2j7t7my9`
  base 3,143.14 s; `m12-20261127T195941Z-ixt34s27` serial 1,950.38 s;
  `m12-20261127T203250Z-mx0eah77` 4w 818.49 s; `m12-20261127T211502Z-cm9phbqt`
  8w 645.33 s; `m12-20261127T204736Z-49j6appf` 120-gate `current` 4w
  1,424.22 s) passed independent seal and artifact verification (`0` bad seals,
  `0` bad artifacts), with `0` semantic differences across 79/79 gates and
  83/83 JSON artifacts.
- **M19–M24 compatibility:** All eight local M19–M24 branches (`e512f2f2`,
  `04648507`, `b855eb93`, `167a7281`, `54f1f130`, `705cd20f`, `6cc3ce8d`,
  `1830f5ec`) merge with **0 code conflicts** (handoff docs only). Disposable
  trials across all four dependency families passed `compileall`, targeted
  tests, and 79/120 plans. Two nonblocking future-adoption items are mapped in
  [docs/verification-infrastructure.md](docs/verification-infrastructure.md) and
  [docs/verification/phase3-compatibility.json](docs/verification/phase3-compatibility.json):
  wrap `local/build/m19-resource.so` in `build_path()` (M19+), and declare or
  isolate the `HG_ELF` retail skip in `tests/test_m24_reference_map.py:36`
  (M24) when those milestones integrate.
- **Safe defaults & rollout:** Default remains `--jobs 1`; use `--jobs 4` for
  ordinary owner-local runs on an idle host and `--jobs 8` for `historical`
  only in an approved quiet window. Active agents finish on their pinned runner
  before adopting `main` in an isolated worktree. Rollback via a reviewed
  revert PR only.
- **Next action:** Continue local M19–M24 milestone review/integration in their
  own worktrees; adopt `main` only at each milestone's clean checkpoint with
  fresh receipts and explicit milestone gate mapping.

## Verification infrastructure, Phase 2 (MainPC validation) — 2026-11-27

**Resume here (isolated Phase 2 review; Phase 3 deferred).** On isolated branch
`verification/phase-2-mainpc` from exact PR #21 base
`86a5670852d47c4ead140e5c8e40a5289f773053`, Phase 1 handoff (`1bd0930c2753...`,
patch SHA-256 `7ffc4d87...`) was authenticated, four genuine contract/isolation
defects were repaired (Gates 19, 56, corridor `pre-dispatch-window`, and mutant
test `build_path` isolation), and verified exact optimizations (`ee_subset.py`
O(1) rational FP, zero-copy `compare_memory()`, and bounded parallel scheduling)
were validated at code commit `734e328fe7c9cea90b2dd746c0ea1fff0647dbc0`
(`source_sha256` `c66bc38ffb97...`) against authenticated retail inputs
(`SCPS_150.46` `a74248c5...`, Japan ISO `8ca1513c...`, `DATA/PACK.DAT`
`f2e04a08...`, M18 overlay `ef257df9...`, PCSX2 v2.8.2 `0c46bb6a...`, PS2DInfo
`2e4a226e...`).

- **Fresh unmodified serial baseline** (`full`, 79 gates, `86a56708`, 1w,
  `m12-20261127T190644Z-2j7t7my9`): **79/79 accepted** in **3,143.14 s** (52m 23s).
- **Fresh corrected serial** (`historical`, 79 gates, `734e328`, 1w,
  `m12-20261127T195941Z-ixt34s27`): **79/79 accepted** in **1,950.38 s** (32m 30s,
  1.61× / −38.0%).
- **Fresh optimized parallel** (`historical`, 79 gates, `734e328`):
  - **4 workers** (`m12-20261127T203250Z-mx0eah77`): **79/79 accepted** in
    **818.49 s** (13m 38s, **3.84× / −74.0%** vs. unmodified baseline).
  - **8 workers** (`m12-20261127T211502Z-cm9phbqt`): **79/79 accepted** in
    **645.33 s** (10m 45s, **4.87× / −79.5%** vs. unmodified baseline).
  - **Deep semantic equivalence:** **0 differences** across all 79 gates and 83
    JSON report/control artifacts between 1w, 4w, 8w, and the `current` historical
    subset; **0 measurement differences** vs. unmodified base across all 77
    unchanged-schema gates.
- **Fresh expanded current profile** (`current`, 120 gates, `734e328`, 4w,
  `m12-20261127T204736Z-49j6appf`): **120/120 accepted** (115 passed, 5 expected
  rejections, 0 reused, 0 retries) in **1,424.22 s** (23m 44s).
- **Cross-checks & regressions:** M16 `functions.json` / `area.json` and M18
  `connected_run` / `differential` / `negative_controls` / 17 `scenarios` exact
  match; strict C11 GCC 16.2.1 & Clang 23.1.5 pass; all 3 ASan+UBSan C harnesses
  and `HG_SANITIZE=1` Python differential suite (10/10) pass; Gate 00 passes
  465/465 with authenticated ISO (3 retail-only skips without ISO).
- **Limits:** Historical hardware/oracle boundaries remain unchanged (M18
  native-driven limits, unverified authentic Continue, 48 unresolved HGScript
  bindings, host IOP/SPU2 audio model, host compile ≠ PS2 byte matching).
- **Next action:** Owner review of isolated branch `verification/phase-2-mainpc`
  and decision on Phase 3 integration/publication (not started). See
  [docs/verification-infrastructure.md](docs/verification-infrastructure.md) and
  [docs/verification/phase2-results.json](docs/verification/phase2-results.json).

## Verification infrastructure, Phase 1 — 2026-11-27

The local Phase 1 handoff contains the Gate 01 report-contract correction,
bounded gate concurrency, and a 120-gate current-project profile preserving
all 79 historical checks. On the cloud checkpoint, 37 applicable gates were
accepted and 83 original-input gates were blocked. This is a bounded partial
milestone. MainPC original-data checks, exact contract review and benchmarks
remain outstanding. See [the handoff](docs/verification-infrastructure.md)
for source identity, cloud results and the owner reproduction checklist.

## M19–M20 local integration review — 2026-11-24

**Resume here.** Branch `integration/m19-m20-review` in
`decomp-m19-m20-integration`.
Verified source `970e7acb1b87c1b8f7397b763d655730780199fa`.
The base is `86a5670852d47c4ead140e5c8e40a5289f773053`, the locally recorded
PR #21 merge. The historical M19 and M20 sections below remain as recorded.

- **M19** is ready for the selected title publication and partial overall.
  The cold ISO-backed worker published PACK 193/007 into slot `0x22f`.
  The fixed parent header, arena and all 1,376 type-12 bytes matched the
  saved original. The following AREA1 request invalidated the handle.
- **M20** is ready after bounded repairs. The title adapter resolved the
  published child through the shared guest bus and ran record 0 from its
  original AREA0 caller slice. The comparison covered 3,861 carried frames, 10,801 glyph
  requests, voice start and reset. The standalone timed, paged-text and
  saved-title comparisons passed, and 74/74 collections rebuilt byte-exactly.
- **Verification** passed 460/460 discovered tests. The full profile accepted
  79/79 gates in `m12-20261124T181748Z-fmurluh1`.
  See the [review and reproduction commands](docs/M19-M20-review.md) and
  [connected title receipt](evidence/m19-m20-integration/title.json).
- **Private inputs** require the authenticated Japanese ISO and ELF, saved
  M18 scene data for the original checkpoint, and the pinned PS2DInfo,
  PCSX2 AppImage and historical captures for the full profile.
- Fresh native title playback, full scene scheduling and renderer/IOP/SPU2
  services remain open. **Next action.** Capture original title activation,
  the first glyph/voice requests and reset in one fresh PCSX2 session.

## M20 HGScript runtime — 2026-11-24

M20 resumed in `decomp-m20` on `research/m20-hgscript-runtime`. The actual
starting HEAD was the M17/M18/Content/Audio integration merge
`86a5670852d47c4ead140e5c8e40a5289f773053`. The uncommitted DeepSeek work
was recovered from its C draft, probe, disassembly and logs. M20 source and
verification artifacts are committed at `4a98056ec458d3f251516384c943b7350b2bda61`. The handoffs
are in the following documentation commit for later reconciliation with M19.

The original EE timed interpreter and renderer are `001ccfa0` and
`001cd9c0`, with initialization at `001ccd80`. Original instructions and
the C port independently carried state from initialization to termination
for three authenticated retail records. The comparisons matched draw and
audio requests. Two static records exercised the handle, page and text
reference paths. All 74 collections rebuilt byte-exactly. The content tools
now resolve offsets directly for the verified text, cue and metadata
reference forms. See [M20](docs/M20.md) for commands, original-build
identity, precise boundaries and the integration API.

The next integration action is to pass an authenticated collection address
from M19's loader to the timed interpreter and verify one naturally selected
record from its original caller. The EE differential uses explicit external
input and service fixtures. Fresh M20 PCSX2 playback, downstream audio and
drawing, and the alternate timed consumer remain open.

## M19 autonomous resource loading — 2026-11-23

**Resume here.** M19 is on local branch `research/m19-autonomous-loading` in
worktree `decomp-m19`. The implementation commit is
`b70c7a7b2f1a12e985b6d9d0be1d4c83e394d8ce`; the handoff follows in a separate
commit. Verified remote `main` and merged PR #21 both point to the M19 baseline
`86a5670852d47c4ead140e5c8e40a5289f773053`. The integration record below is
preserved as historical pre-merge context.

The normal worker now runs resumable PACK and AREA jobs on a shared guest bus
with authenticated ISO DMA. The M18 comparison path now has a connected
variant that carries E3 state, completes all 11 native requests, and agrees
on 57 logical reads and three completed overlay/arena/handle/task checkpoints.
The cold bootstrap-to-title run and M4/M7 resource checkpoints are verified
separately. M18 keeps its external pad, timing, scene and other subsystem
inputs. The measured overlay sequence is title → AREA1 → title → AREA1.
A real transition between distinct gameplay areas remains unverified.

See [M19](docs/M19.md), [the loader contract](analysis/resource_loading/README.md),
and [evidence](evidence/m19/results.json). The final full owner regression runs
on the frozen handoff commit; use its private `local/verification/` receipt
for the verdict and exact source fingerprint. No push, PR or merge was done.

Next, independently review the M19 implementation and receipts alongside the
parallel M20 work. M20 should use the existing request and guest publication
interfaces; `HGResourceJob` lives on the host side.

## Integration of M17, M18, Content/HGScript and Audio — 2026-11-23

**Resume here.** The four milestones are integrated on branch
`integration/m17-m18-content-audio` (worktree `decomp-integration`), based on
`origin/main` = `46cdb2189a3190b49830b0222e3a5cfcc4e0fd0b` (the M16 merge,
PR #20, re-fetched and unchanged on 2026-11-23). The branch is proposed to
`main` by pull request for owner review. **It is not merged.** The dated
milestone sections below are kept as written; their "local only / not merged"
statements describe the state when each was recorded.

| Milestone | Branch | Tip | Integrated by |
|---|---|---|---|
| M17 game lifecycle | `research/m17-game-lifecycle` | `f759bf5` | contained in M18 (ancestor) |
| M18 front end | `research/m18-front-end-lifecycle` | `08cfa09` | fast-forward of `46cdb21` |
| Content/HGScript | `research/content-corpus-hgscript` | `1b89fbb` | merge `10e7941` |
| Audio | `research/audio-engine` | `335c45d` | merge `49e1c88` |

Corrections made on the milestone branches before integration:

- `b83fb18` (Content, B1): the tests import the shared helper as a top-level
  module, so CI's `discover -s tests` loads all 71 Content tests (before: 3
  modules failed to import, 48 tests never ran).
- `1b89fbb` (Content): whitespace only; `git diff --check 46cdb21` is clean.
- `335c45d` (Audio, B2): the disassembler prints stype-0 `sync` bare, as the
  Ghidra listings do; with the M17/M18 listings the comparison had failed on
  2 words (M17 `0011df68`). (B3): the three retail-only tests run only with
  `HG_JAPAN_ISO` set (skipped otherwise, failing on a missing, unreadable or
  non-matching image), and the runner takes `--iso` instead of a sibling
  default path.

**What the combined repository provides.** Each item keeps its own evidence
level; see the milestone records for detail.

- **M17** ([docs/M17.md](docs/M17.md)): C for the lifecycle layer (24 entries,
  23 original functions). Original-instruction differential, 0 mismatches;
  the 22-frame corridor runs through it; three partial native observations.
  Boot, quit/continue and area-to-area transitions are synthetic only.
- **M18** ([docs/M18.md](docs/M18.md)): C for the front end (27 entries).
  16,273 consecutive native frames compared, 0 failing. The C carries its own
  state, but the pad buffer, vblank timing, loader cells and scene-requested
  fades (E1–E13) come from the native record, and the world simulation is
  external. It is a native-driven comparison of front-end and lifecycle
  state, not independent whole-game execution. Authentic Continue is not
  exercised.
- **Content/HGScript** ([docs/content-pipeline.md](docs/content-pipeline.md)):
  content reconstruction, not EE C. 2,541 PACK members accounted for, 74
  HGScript collections rebuilt byte-exactly. 48 keep an unresolved string
  binding, and event semantics are not claimed.
- **Audio** ([docs/audio-milestone.md](docs/audio-milestone.md)): 30 original
  EE audio functions in C behind their own guest bus. Original-instruction
  differential 407 passed / 13 excluded; M7 endpoints reproduced from the
  authenticated members. The IOP service is a host model; decoded WAV output
  does not establish SPU2 equivalence.
- **Coexistence, not runtime integration.** The four share one tree and one
  synthetic suite. The M18 main loop still treats the sound service
  `00156740` as external and does not call the Audio C; the Content tools are
  offline and not used by the runtime C.

**Coverage** (`tools/hg_coverage_inventory.py` on the integrated source,
interpreted records of M17 and M18): 138 C functions / 58,153 of 2,469,083 ELF candidate
instruction bytes (2.36%). This is identical to M18's recorded
[coverage.json](evidence/m18/coverage.json): Content and Audio add no
entries that the tool counts. The tool reads a fixed list of the M17/M18
sources and does not scan `src/audio/`, whose prototypes also carry no
`/* address */` annotations. Measured separately against the same
inventory, the 30 Audio C functions cover 28 inventory candidates (5,388
bytes; `002b4f68` and `002b53a0` are not inventory entries). Of the 30, 26
are checked by the original-instruction differential, 2 by synthetic
tests plus the M7 original-data endpoints, and 2 by synthetic tests only.
Counted together the total would be 63,541 bytes (2.57%); that figure is
not a classification produced by the tool.

**Verification on the integrated source:**

- **First integration regression** `m12-20261123T233426Z-nt7ugt5y` (on
  `49e1c88`, before the corrections below): **78/79 accepted** (73 passed,
  5 expected rejections), 0 reused. Gate 00 `synthetic-suite` was stopped
  at the runner's 180 s default (414 tests ok/skipped, none failed). The
  suite is slow, not stuck: its duration grew M16 125.9 s → M17 149.3 s →
  M18 169.6 s → integrated about 183 s.
- **Corrections during integration:**
  - `1273390`: gate 00 gets an explicit 1800 s timeout, as the other long
    gates have. Acceptance is unchanged, and an overrun still yields
    `harness-error` (negative control).
  - `daae4fd`: `verify_audio.py` looked for its default ELF under
    `tools/local/…`. It now uses the repository root, and a new synthetic
    test checks the default (it fails on the old path).
- **Synthetic suite** (`python3 -B -m unittest discover -s tests -v`, on
  `daae4fd`): 452 tests, OK, 3 skipped (the retail-only Audio tests without
  `HG_JAPAN_ISO`), 183.1 s.
- **Disassembler vs all 21 listing files:** 18,817 compared, 18,817 match.
- **Strict C:** the M18 strict build plus `src/audio/audio_engine.c` in one
  library (`-Wall -Wextra -Werror -pedantic`, `--no-undefined`) with GCC 16
  and Clang 23.1.4, 0 duplicate globals; Audio standalone with both
  compilers; lifecycle and front-end ASan+UBSan harnesses (20,000
  iterations each); CI sanitizer PASS.
- **Content, authenticated original data:** the ISO was authenticated, and
  the coverage and classification counts equal the committed summary. The
  rebuilt index equals `evidence/content/index.json` except for the private
  source path, which the committed copy omits.
- **Audio, authenticated original data:**
  - 3/3 retail tests pass with `HG_JAPAN_ISO`.
  - Original-instruction differential: 407 passed / 0 failed / 0 skipped /
    13 excluded, identical to the recorded run.
  - Runner: 96,000 samples, and the receipt is identical to the recorded
    run.
- **Final owner regression** (`tools/hg_verify.py run --profile full`): run
  on the final commit of this branch. Its run ID and result are recorded in
  the pull request description, so this file does not change after
  verification.

**Documentation corrections in this integration.** Audio: the recorded runner
output is 96,000 samples (2.0 s), not 48,000 (the 1.0 s default). M18: E10
lists 9 recoveries (11 setter calls); with E13 the receipt has 10. Content:
the committed index does publish per-member offsets, sizes, SHA-256 digests
and classifications (metadata only). README status lines and test command
updated.

**Not claimed.** No byte matching, whole-game equivalence, recompilation or
port. The project's final objective is still unselected.

**Next action.** Owner review of the pull request. After a merge, the
milestone next actions remain:

- M18: with authorization, make one memory-card save in a profile copy and
  capture an authentic Continue, or reconstruct the loader thread `00130220`.
- Content: correlate HGScript event opcodes across the 74 collections.
- Audio: label the command-ring bytes against an IOP capture, or disassemble
  the `LIBSD`/`SG2IOP` service dispatch.

## M18 original-driven front end and game-state lifecycle — 2026-11-23

**Superseded as the resume point by the integration section above.** M18 is a **bounded partial result**, committed locally only
in worktree `decomp-m18`, branch `research/m18-front-end-lifecycle`, based on
the M17 branch tip `f759bf5` (M17 is not merged). Nothing is pushed; there
is no PR or merge. Full record: [docs/M18.md](docs/M18.md),
[checks.json](evidence/m18/checks.json), [results.json](evidence/m18/results.json),
[coverage.json](evidence/m18/coverage.json).

- **Delivered:**
  - C for the front end (`src/front_end.c`, 27 entries: 25 ELF functions
    and 2 functions of the title overlay `/AREA0/A0_B09.BIN`):
    - the fade/transition family and its per-frame service 00139240;
    - the controller service 0013d970 with the libpad port state machine
      and read;
    - the title menu 00a87880 and card poll 00a87de0;
    - the memory-card scan 002cf020;
    - the new-game state 00141980;
    - the pause menu 00162750 and its cursor;
  - a connected native run: from one full capture, the C (M17 lifecycle
    plus M18 front end) runs 16,273 consecutive native frames (seq 2439 →
    18712) of one PCSX2 session, with 0 failing frames:
    - title → new game → area load → gameplay;
    - pause → resume;
    - pause → quit → teardown → title → new game → gameplay;
  - the C state is carried forward and compared every frame (434 bytes),
    plus every load request (kind and resource id);
  - 10/10 compiled defects rejected by the native run, null control clean;
  - original-instruction differential: 202,500 cases, 0 mismatches, on the
    ELF and four native full captures.
- **Not claimed:**
  - Authentic Continue: no save exists, and creating one needs owner
    authorization.
  - The loader thread (E3) as C.
  - Scene, actor and dialog code: its fade requests and the new-game
    dialog decision are recovered from native and declared.
  - The world simulation in gameplay frames: the coordinator is external.
- **Coverage:** C source covers 58,153 of 2,469,083 ELF candidate
  instruction bytes (about 2.36%), up from 49,020 (1.99%). The overlay's
  1,612 bytes are outside the inventory.

**Next action.** Owner review. Then, with authorization, make one
memory-card save in a profile copy and capture Title → Continue → gameplay
for the same runner. Or reconstruct the loader thread 00130220.

## M17 game lifecycle and connected mode reconstruction — 2026-11-23

**Superseded as the resume point by M18 above.** M17 is a **bounded partial result**, committed locally only
in worktree `decomp-m17`, branch `research/m17-game-lifecycle`, based on
`main` `46cdb21` (the M16 merge). Nothing is pushed; no PR or merge. Full
record: [docs/M17.md](docs/M17.md), [checks.json](evidence/m17/checks.json),
[results.json](evidence/m17/results.json), [coverage.json](evidence/m17/coverage.json).

- **Delivered:**
  - C for the lifecycle layer (`src/game_lifecycle.c`, 24 entries for 23
    original functions): main loop 0011d830, vblank handlers 0011df10 and
    0011df80, mode dispatcher and setters 00143240/001432c0/00143300, boot
    mode 00121810 with logo step 001219e0, lifecycle/title mode 00121be0,
    gameplay mode 0012d1c0 with its major-5 dispatch, area load 0012dd60,
    PACK selection 0012dbd0, active play 0012e700, event flags 0012fa00,
    reload, exit and teardown 0012f800;
  - original-instruction differential: 0 mismatches, 100% of reachable
    words, on the ELF plus eight retail full-RAM states;
  - the 22-frame obs3 corridor runs through the connected lifecycle layer
    (real 0012d1c0 tick, main-loop tail, C vblank handlers);
  - three native lifecycle observations beyond the corridor: M8 Start/pause
    entry, M8 pause-retained frame, M7 area-1 PACK `0xc6`;
  - a synthetic boot → title → new game → area load → play → pause → quit →
    teardown → continue → reload → play sequence from the ELF image alone.
- **Not claimed:** native equivalence of boot, quit/continue or area-to-area
  transitions (no capture exists); loader, fades, menus and audio are host
  models in the synthetic sequence.
- **Coverage:** about 2.0% of ELF candidate instruction bytes have C source
  (49,020 of 2,469,083; 1.5% before). AREA overlays, IOP modules and VU code
  are outside the inventory.
- **M16 docs:** the post-merge corrections from `decomp-m16-docs` are
  incorporated (commit `a30627e`); M16's recorded PCSX2 hash has a 65-digit
  transcription error (erratum in docs/M17.md).

**Next action.** Owner review. Then capture one native lifecycle
transition: pause → quit → title → continue → area load → first active
frame, breaking at 00143240, 0012f800, 0012dd60 and 0012f850.

## M16 connected late-frame render and RNG reconstruction — 2026-11-22

**Resume here (post-merge).** M16 reconstructs coordinator tail service
`0013ce80` (called at `0012f924`, between `00182480` and `00137890`) as the
connected C service `hg_late_frame_service` and removes external RNG draw-count
replay. It **merged into `main`** through [PR #20](https://github.com/finakechi/Hungry-Ghosts---Decomp/pull/20)
at `46cdb2189a3190b49830b0222e3a5cfcc4e0fd0b` (2026-11-23 02:14 UTC;
November 22 Eastern). The original `decomp-m16` branch
`research/m16-late-frame-render-rng` was based on M15 merge `4d016e9f`;
implementation `4ccc411`, evidence/docs `0adc83b`, and final branch head
`467d43091392da5cd8bee197e1670d22b53eb890` are historical source identities.
See [docs/M16.md](docs/M16.md).

- **Delivered:**
  - the service gate (state bytes `003fa1b0/b1/b2`) dispatches the
    AREA1-resident render body `0xa8b800` on the observed state (1,0,0)
    with `f12..f16 = 0x415f78d5, 0x461c4000, 0xc61c4000, 0x461c4000,
    0x40400000`; unobserved calling states fail closed (0x79/0x7a), all
    other states return silently;
  - the area body and its ELF callees execute **original instructions**
    through the interpreter, identity-checked against the authenticated ELF
    and AREA1/A1_B00.BIN;
  - MMI `div1`/`mflo1` interpreter support (17 deterministic unit tests;
    zero divisor and `INT_MIN / -1` fail closed);
  - the shared LCG `0011ad58` draws arise from real execution (one per
    frame, three at frame 10178); the M15 external draw replay is deleted;
  - the main-loop frame tick `[gp+2f80] += 1` is reconstructed at the end
    of `hg_player_frame_continuous` and guarded.
- **Last verification (owner-local, on the final source):**
  - 22/22 frames with per-frame same-input `camera-chain`, `model-draw`,
    `render-node-tail`, `late-frame` and `rng-continuity` checkpoints;
  - negative controls: 2 null clean, 31 defects rejected by their intended
    comparators (extra RNG draw, quotient-plus-one `div1`, omitted area
    dispatch via the extra-FP contract, unconditional scratch store, mark
    placement, missing/double frame tick, coordinator ordering, and the
    M13–M15 defect set);
  - M14 native window 22/22 (9/9), register contracts 22/22, counter guard,
    re-run on the final source;
  - 179 ELF + 451 AREA original-byte instructions; synthetic suite 324/324;
    sanitizers; historical regression `m12-20261123T004321Z-iwwr_uz2`:
    79/79 accepted (74 passed, 5 expected rejections), 0 reused. See
    [checks.json](evidence/m16/checks.json).
- **Private inputs** (ignored `local/`): ELF `a74248c5…3c61`, Japan ISO
  `8ca1513c…994e`, PS2DInfo `2e4a226`, M13 obs3 captures,
  `local/m15/r5900dis.py`. The historical regression also needs the ISO and
  PCSX2 v2.8.2 `0c46bb6a…abea` (`HG_JAPAN_ISO`, `HG_PCSX2`).
- **Limits:** observed obs3 corridor only; the five FP operands `f12..f16`
  are measured not to affect the observed AREA body output; packet bytes
  compared same-input only; remaining coordinator services and the
  light-matrix rewriter stay external seams (the bytes they produce stay
  excluded at native stops). No byte-match, equivalence, recompilation or
  port claim.
- **Post-merge checks:** GitHub Actions synthetic Python 3.10/3.14 passed
  on the merge commit ([run #45099073034](https://github.com/finakechi/Hungry-Ghosts---Decomp/actions/runs/45099073034));
  seven published source hashes match `checks.json`. In an owner-terminal
  repair, three silent-success historical logs were restored to their recorded
  empty bytes after preserving the post-run notes under ignored
  `local/m16/receipt-repair-_1z_ckwv/`; all three receipt seals and artifact
  hashes then validated. No new original-game test was run post-merge.
- **Research gap:** 58 precise scratchpad bytes remain excluded at native
  stops: 8 attributed to `0013b6e0`, and 50 to an unidentified light-matrix
  writer after `00182480`. M17 scope and the eventual project goal remain open.

**Next action.** Trace original last-writer ownership and call ordering for
those 58 bytes, identifying the light-matrix writer before scoping M17.

## M15 connected camera projection and render-node state — 2026-11-22

**Resume here (superseded by M16 above).** M15 is implemented and verified
on the obs3 corridor, and its acceptance criteria are met. The work was
implemented on branch `research/m15-camera-projection` (implementation commit
`6f592f9`) and is **merged into `main`** as `4d016e9f` via PR #19. Final C is
`cc38d26e…9017`. See [docs/M15.md](docs/M15.md).

- **Delivered:** the screen-space and render scratch (`0x500–0x53f`,
  `0x700–0x72f`, `0x7c0–0x7ff`, `0x900–0x94f`, `0xb80–0xbef`, `0xc80–0xd1f`)
  is produced after camera return, not by the `0012c790` body as M13 assumed.
  Its producers are now C on the carried bus:
  - the player model draw `0012bc70` (light setup `00129020`, ground shadow
    `00129e40`);
  - the render-node list `00143150` (M8 walker reuse) with the anchor sprite
    `0019c8a0` and the particle field `002f7830`.

  No captured values are injected and nothing is stored at camera return.
- **Last verification:**
  - 22/22 frames and 386 stage comparisons, with same-input `camera-chain`,
    `model-draw` and `render-node-tail` checkpoints every frame (0 differences);
  - 26 controls: 24 defects rejected, 2 null controls clean;
  - M14 native 22/22 (9/9 controls), contracts 22/22, counter guard;
  - packets: 743 + 1,527 + 2,574 words;
  - 298 synthetic tests and the sanitizers;
  - historical regression `m12-20261122T155330Z-veza3mwe`: 79/79 accepted, 0 reused.
- **Private inputs** (ignored `local/`): ELF `a74248c5…3c61`, PS2DInfo
  `2e4a226`, M13 obs3 captures, `local/m15/r5900dis.py`. The historical
  regression also needs the ISO and PCSX2 (`HG_JAPAN_ISO`, `HG_PCSX2`).
- **Limits:** 98 scratch bytes stay excluded at native stops, written later by
  the unreconstructed `0013ce80`/`0013b6e0` (or later main-loop code for the
  light matrices). Render-stream packet bytes are compared same-input only.
  Only the observed state, variant and model modes are reconstructed; others
  fail closed.
- **Blockers:** owner review and the commit decision.

**Next action (completed by M16).** `0013ce80` is now interpreter-executable
(MMI `div1` `70a4001a` at `00153844` was the first gap) and reconstructed.
That removes most of the remaining native-stop scratch exclusions and the
replayed external RNG draws.

## M14 continuous pre-dispatch engine services — 2026-11-21

**Resume here (publication update, 2026-11-22).** M13 merged into `main`
through [PR #16](https://github.com/finakechi/Hungry-Ghosts---Decomp/pull/16)
(`8295c622`); M14 first merged into the M13 branch through
[PR #17](https://github.com/finakechi/Hungry-Ghosts---Decomp/pull/17), then
into `main` through [PR #18](https://github.com/finakechi/Hungry-Ghosts---Decomp/pull/18)
(`535e43b691b9296912a06ba40f3450a6e71431da`). The PR #18 synthetic CI
passed. Original M14 commit `9a12391` and later receipt-resume fix `e8010b1`
are published. The acceptance records retain their pre-commit source identity;
no post-merge retail rerun is claimed.

- **Delivered:** C `00198f60` pre-dispatch service, original-instruction
  `0017f6d0`/`0017f2d0`, host-modeled GS field/bank handoff, and original
  `00148970` render setup on the carried M13 player/camera state.
- **Last original-game verification:** frames 10164–10185 (22/22), 320/320
  checkpoints, native window 22/22, 682/682 register contracts, 1,433
  original words, and 20 effective defect detections (11 continuous, 9 native).
  One continuous null control passed; two native diagnostics were unobservable.
  The final-source M12 historical regression
  `m12-20261122T003553Z-nmkuafkq` accepted 79/79 checks, 0 reused;
  the M14 synthetic suite passed 288/288. See
  [results.json](evidence/m14/results.json),
  [checks.json](evidence/m14/checks.json), and [docs/M14.md](docs/M14.md).
- **Private inputs:** Japanese ELF `a74248c5…3c61`, pinned PS2DInfo
  `2e4a226`, and M13 obs3 captures under ignored `local/`. The 79-check
  historical regression also needs the authorized ISO and PCSX2.
- **Limits:** observed corridor and modeled alternating field only;
  general GS timing, DMA submission, indirect camera screen-space writers,
  and remaining post-camera services are not fully reconstructed.
  No byte-match, full-game equivalence, recompilation, or port claim.
- **Blockers:** none for M13/M14 publication. M15 scope and the project's
  eventual technical objective remain undecided.

**Next action.** Investigate the remaining camera screen-space indirect-call
writers and their saved runtime evidence, compare their value against the
remaining post-camera services, then select one bounded M15 research objective.
Preserve historical receipts and source hashes rather than relabeling them as
post-merge measurements.

## M13 continuous player movement, collision and camera — 2026-11-21

**Publication note (2026-11-22):** M13 merged through PR #16. The
following verification and branch details record its earlier local
development state; the M13 next-action note was superseded by M14.

**Resume here.** M13 is complete for one original-game-observed continuous
sequence: 22 consecutive frames (10164–10185) run from a single authenticated
starting state — approach (6), wall contact (2), contact-turn with slides (4),
release and braking (6), stopped (4) — with position history and the same-frame
camera on every frame, and no captured-state restoration after the first frame.
Local branch `research/m13-continuous-movement` (baseline main `3b52075` =
M12 merged), HEAD `ba5b1f9`. **Historical pre-merge status; M13 later merged via PR #16.** See
[docs/M13.md](docs/M13.md).

- **Reconstructed (byte-verified against ELF `a74248c5…3c61`):** the
  coordinator `0012f850` observed prefix (the `3fa190` frame-counter increment
  with its `bgez` wrap guard, `0031bb80` edge sync, `3f9ff8` clear, guarded
  `46e118` increment) and the engine counter-advance body `001379f0`
  (`46d930`/`46e110`, −1 clamp, mega-counter path). The coordinator's
  engine-service jal targets are declared external seams; the camera-chain
  matrix helpers `00143c60`/`00143d30`/`00143b90` run original instructions
  through the owner bus in both M12 and M13 paths (regression test locks
  routing).
- **Continuity:** the original rewrites 0 player/camera struct bytes between
  frames (11 obs2 pairs); the reconstructed frame reads none of the
  inter-frame counters. Only the recorded pad input is supplied externally
  between frames; everything else carries.
- **Verification (at `ba5b1f9`):** continuous verifier 22/22 frames, 0 guarded
  failures, every stage compared (`local/m13/continuous-verify-007.json`);
  controls 7/7 (null clean, 6 rejected incl. a compiled defect); packet
  221 words passes PS2DInfo `--require-all-bytes` and the project byte/JAL
  verifier; synthetic suite 270/270; full M12 profile
  `m12-20261121T162844Z-sf2p_o3e` 79/79 accepted, 0 reused.
  [checks.json](evidence/m13/checks.json) holds commands, logs and verdicts.
- **Limits:** verified for the obs3 corridor profile only; the `0012c790`
  screen-space projection arrays and the coordinator engine services remain
  external; the player dispatcher's non-observed states fail closed.

**Next action.** Reconstruct the camera chain's screen-space projection body,
or open M14 with the coordinator's first engine service `00198f60`.

## M12 verification automation — 2026-11-19

M12 merged through [PR #15](https://github.com/finakechi/Hungry-Ghosts---Decomp/pull/15)
at `3b52075176f4a9cae1accba8a230167e0c7ef194`. Main CI passed on Python
3.10 and 3.14. The automation milestone is on
`automation/m12-verification-stage1` in `decomp-m12`.

From that worktree, run `python3 -B tools/hg_verify.py plan --profile full`
and `python3 -B tools/hg_verify.py run --profile full`. Set `HG_PCSX2`
to the approved binary for the saved M4–M7 inputs. The focused profile
retains eight gates. See the [M12 command guide](docs/M12.md#verification-automation).

MainPC accepted 79/79 historical checks in run `m12-20261120T021536Z-q6tug_5i`.
The synthetic suite passed 259 tests. The saved M12 replay matched 130
stages across 118 captures, 36 register comparisons and 11 handoffs. It
rejected 14 mutations, passed two null controls and observed the expected airborne
refusal `0x50`. The M12 instruction differential measured 551 equal and
49 expected refusals. The M11 frame cross-check matched all 36 checkpoints.

The clean run took 53m29s against the historical 47m31s, including 31.7s
outside the child commands. Resume revalidated and reused all 79 receipts
in 21.5s without executing a verifier. The missing-input run
accounted for all 79 checks and exited with an incomplete verdict.
Reports remain in ignored `local/verification/`. Final review replayed all
79 verdicts through the corrected classifier and passed 260 synthetic tests.

**Next action.** Profile check 70, the 600-case M11 instruction differential,
using the saved inputs and preserving its comparison contract.

## M12 connected grounded movement and environmental response — 2026-11-19

**Resume here.** M12 is complete for one original-game-observed connected
sequence. The player walks forward obliquely (about −45°) into the starting
corridor's +Z side wall, slides along it, turns while in contact, and releases.
Every frame the original grounded follow-up `0015fc10` lowers the player by
gravity, and its floor routine `0015e420` snaps the position to the sloped floor
and clears the vertical speed. History `+390` and the same-frame camera use the
wall- and floor-corrected position. `0015fc10` and the floor-hit path of
`0015e420` are now C (`hg_player_grounded`, `hg_player_floor`), composed with the
M11 constraint, M10 dispatcher and M9 camera. Local branch
`research/m12-grounded-movement` (baseline main `35360c9b…4c`): implementation
`95364d6`, evidence `ffa6ef3`, review fixes `28d7c80`, then the corrected
documentation/evidence commit.
**Merged through PR #15.** Private M12 Drive backup status is **unrun**.
See [docs/M12.md](docs/M12.md).

- **Evidence:** run `obs2`, 139 native stops (all PC-bound); 12 complete frames
  1209–1220: approach 2, contact 3, contact-turn 3, release 4. The 10 native
  head-push stops show dot −0.999896 … −0.999863 ≤ −0.3; every floor query returns
  `0x101` and native `v0` after the floor routine is 1; Y follows the floor slope
  each frame (38.02151 → 38.01458).
- **Independent review (2026-11-19):** four genuine findings against `ffa6ef3`:
  a hardcoded floor-return `v0`, inexact refusal-code classification in the
  differential, a comparator whose reporting reads added watched DMA accesses,
  and documentation errors. Each false pass was reproduced, then fixed in
  `28d7c80` with regressions.
- **Owner-local checks after the fixes (2026-11-19, at `28d7c80`):** 130 stage
  comparisons against 118 distinct native captures with 0 differing bytes (EE RAM
  minus the 128 KiB guest stack, untouched libpad DMA separated, full scratchpad),
  11/11 handoffs, 36/36 register cross-checks (floor-return `v0` now against the
  measured C return), 14/14 runtime controls (11 compiled defects incl.
  `floor-returns-two` rejected by register alone, 3 input counterfactuals), 2/2
  clean null controls, airborne refused with exactly 0x50; a 600-case differential
  (551 equal, 0 failed, 49 refusals each matching the original's exact no-floor
  branch) with 4/4 mutants rejected; 36/36 M11 retail checkpoints with C
  grounded/floor; M11 controls rerun 13/13 with the comparator fix. Inventory
  79/79 passed (`r20261119T213946Z`), covering 247 synthetic tests, M1–M11 regressions,
  strict C, sanitizers, PS2DInfo and the publication audit.
  [checks.json](evidence/m12/checks.json) holds the commands, logs, before/after
  results and preserved failures.
- **Model correction:** the first oblique heading showed that EE `mul.s` zero
  products keep their sign under PCSX2 (camera up vector `−0`); the shared oracle
  and its C mirror now do the same, and all M8–M11 regressions still pass.
- **Identities:** SCPS-15046 ELF `a74248c5…3c61` (CRC `91AA4B28`), ISO
  `8ca1513c…994e`, PCSX2 v2.8.2 `0c46bb6a…abea`, BIOS `c641edbd…363c4a`, save state
  `1d8798fa…808d`, freshly hashed. Private inputs in ignored `local/m12/`
  (17 GiB captures); M8–M11 inputs are still required.
- **Limits:** one floor material, one wall (head tier), `+4ad = 0`; gravity variants
  differential-only; only the floor-hit path of the 427-word `0015e420` listing is
  C; no-floor, hazard, special-surface, type-5 and spawn paths fail closed; each
  frame starts from a captured entry state. No general collision, autonomous
  inter-frame execution, all-C, byte-match or whole-game claim.
- **Publication:** on 2026-11-19 Noah chose to publish the packet with its 628
  original instruction words included, as for M1–M11. PR #15 is merged. The
  automation section above records the current continuation. Any M13 work and
  the final technical objective remain deferred.

Earlier milestone sections below retain their historical status and next-action
wording; M12 above is the current resume point.

## M11 static movement constraint and camera — 2026-11-18

**Resume here.** M11 is complete for one original-game-observed static movement
constraint: walking backward (stick Y −1.0) into the starting corridor's end wall.
In each contact frame the original `0015ebc0` foot probe hits the wall, its facing
test passes, and the push moves the proposed X back to `29.29095`. Input, speed and
Z are unchanged. History `+390` and the same-frame camera then use the corrected
position. `0015ebc0` is now C (`hg_player_static_constraint`), layered on the M10
dispatcher and M9 camera. Published through [PR #13](https://github.com/finakechi/Hungry-Ghosts---Decomp/pull/13)
(merged 2026-11-18 as `3eac8b59e22cb84f6e86b700bfdb790547fcd9bf`).
Branch head `822054dd4f824a61ba7eea246425b3a0b3db23cc` is distinct from
the merge commit; baseline was `1e0a14aaeca079d3f62d1cd5088e4ccfd15c23ae`.
**No verified M11 private Drive backup.** Delivery detail: [docs/M11.md](docs/M11.md).

- **Evidence:** 67 native stops in one continuous PCSX2 v2.8.2 run; 66 fall in
  complete frames 4278–4284: four free frames, then contact. The native `0015f654` push stop
  shows dot `−0.987 ≤ −0.3`, and 20/20 foot-query results equal the original `v0`.
  Corrections are +0.0122, +0.3095 and +0.3094; free frames change by 0. The
  bump timer `+508` arms at 0x50 and the bump sound request is reached.
- **Owner-local checks (2026-11-18):** 59 complete EE RAM/scratchpad stage
  comparisons with 0 unexplained bytes, 6/6 player/camera handoffs, CP0 Status
  invariance in 7/7 frames, and 13/13 measured negative controls (11 compiled
  defects, 2 input counterfactuals). The 600-case original-instruction
  differential passes. Inventory 72/72 passed, covering 230
  synthetic tests, M1–M10 regressions, strict C, ASan/UBSan/LeakSanitizer,
  PS2DInfo and the publication audit. 1,456 new packet words are original-byte
  verified. [checks.json](evidence/m11/checks.json) holds the commands, logs and
  preserved failures, including the first inventory's 70/72 control-target collision.
- **Independent review (2026-11-18):** DeepSeek Moby reported **READY** for
  the bounded M11 claim after fresh reruns of the 59 memory stages, 20 register
  checks, 6 handoffs, 13 M11 controls and 600-case differential. M9/M10
  negative controls also passed (14/14 and 15/15); the M10 total includes
  12 logged source edits and 3 input mutations recorded in JSON. The original
  retail capture session was reviewed, not freshly rerun.
- **Published CI:** [Synthetic tests run #44417054847](https://github.com/finakechi/Hungry-Ghosts---Decomp/actions/runs/44417054847)
  passed on the PR head. CI covers host/synthetic checks, not retail comparison.
- **New contracts:** a host-only guest-frame seam keeps an original
  uninitialised-stack read (`00145740` → `3f3a00`) identical. The sound request's
  fifth argument `t0 = 1` is supplied explicitly. CP0 Status is a varied
  input. The instruction model gains RNG/audio instructions (`multu`, `mult1`,
  `mfc0 Status`, `di/ei`, …).
- **Identities:** SCPS-15046 ELF `a74248c5…3c61` (CRC `91AA4B28`), ISO
  `8ca1513c…994e`, PCSX2 v2.8.2 AppImage `0c46bb6a…abea`, BIOS image
  `c641edbd…363c4a`, save state `1d8798fa…808d`, all freshly hashed. Private
  inputs are in ignored `local/m11/`; M8–M10 inputs are still required.
- **Limits:** hybrid C with original-instruction-modeled query, RNG, sound and
  gravity/floor leaves. One wall, foot tier, `+4ad = 0`, observed retail. Other
  tiers are covered only by the model differential. Special surfaces fail
  closed. Each frame begins from a captured entry state. No general collision,
  autonomous inter-frame execution, all-C, byte-match or whole-game claim.
- **Blockers:** none for the published M11 scope. **ONE next action:** Noah
  decides whether to make a private M11 backup. M12 and the final technical
  objective remain unselected and unauthorized.

Earlier milestone sections below retain their historical status and next-action
wording; M12 at the top is the current resume point.

## M10 forward walking and camera translation — 2026-11-16

**Resume here.** M10 is complete for one independently observed ordinary forward
walk, actual release, braking to zero and a following stationary update.
Implementation `43a2ea4ff4b82a2a5227a97d63c9389e160068ff`, original baseline `94d1ca54b8f708b872a7f03af13a3f9868fa1365`.
PR [#11](https://github.com/finakechi/Hungry-Ghosts---Decomp/pull/11) is **merged into `main`** as merge commit
`015e34cd830318493710659ff2574b7318fc582a`; `8d46bfe390565e3b51e9e6118e77de6ec8ec72cc` was the
former PR branch head, not the merge commit. **No private M10 Drive backup exists.**

- **Evidence:** 84 native stops across frames `0x2cb..0x2d6`; stick Y `+1.0`
  moves player and camera-eye X by `0.2512359619140625`. Required dispatcher
  prefix/tail, `+428` clear and full previous-position quad are reconstructed.
  The forward arithmetic and camera code already existed in M9.
- **Owner-local implementation checks (recorded 2026-11-16; not rerun for this
  post-merge status update):** 72 exact RAM/scratchpad stage comparisons, 11 complete
  player/camera structure handoffs, 12 compiled defects plus 3 input controls,
  1,200 dispatcher instruction cases, 213 synthetic tests and 59/59 regression
  checks passed. Strict C and ASan/UBSan/LeakSanitizer passed. Publication audits
  found no heuristic findings. All failures/retries and exact verification argv
  are in [docs/M10.md](docs/M10.md) and [checks.json](evidence/m10/checks.json).
- **Published CI:** GitHub Actions passed on PR #11's published head (Python
  3.10 and 3.14; push run `44198611906`, PR run `44198706302`). Those
  synthetic/host checks do not replace the owner-local original-game comparison.
- **State continuity:** each frame begins with an independently captured
  original-game dispatcher-entry state. The connected reconstruction calculates
  relevant movement, dispatcher effects and camera response. Complete resulting
  player/camera structures, including unchanged entry-state bytes, match the next
  original-game checkpoint across all 11 verified handoffs; no external structure
  writes were observed between checked boundaries. This verifies the selected
  connected operation, not autonomous reconstruction of every inter-frame effect.
- **Independent review:** READY AFTER SPECIFIC CORRECTIONS; no implementation
  defects, invalid negative controls or regressions reported. Documentation now
  qualifies state continuity and describes `camera-lags-player` as detecting
  player-X/scratchpad differences, without claiming proven camera-eye lag.
- **Original build:** SCPS-15046 ELF
  `a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61`;
  PCSX2 v2.8.2, CRC `91AA4B28`. Private inputs: ignored `local/m10/` captures,
  screenshots, profile/save state; M8/M9 snapshots and regression inputs retained.
- **Limits:** hybrid C/modeled leaves, selected dispatcher/camera branches only;
  interframe work remains external. Guest stack and untouched asynchronous libpad
  DMA are explicit exclusions. No full-frame, all-C, byte-match or port claim.
- **Blockers:** none for the selected operation. **ONE next action:** Noah
  chooses the next substantial development direction. No M11 milestone or final
  technical objective has been selected.

Earlier milestone sections below retain their historical status and next-action
wording; M10 was the resume point until M11.

## M9 player turn and camera response — 2026-11-16

**Resume here.** M9's selected connected operation is implemented and independently compared with the original Japanese game. PR [#9](https://github.com/finakechi/Hungry-Ghosts---Decomp/pull/9) is **merged into `main`** as merge commit `d249be3697bba4255006aa7ba671f53ece08d289`; `cfcefe8` was the former PR branch head, not the merge commit. Implementation `23a9ef111263a1590877faa9d9e44ada260311ac` (baseline `7c4a90b510ee23700a0147a44e2857dfeafec577`); verifier hardening `6062f0e02f2b1d091ac6f3bb98686133b62a3295`; final M9 documentation/evidence `cfcefe83276b0f7ad436492b9ff008c0346e07df`. Latest recorded owner-local verification: 59/59 checks (210 synthetic tests) at `6062f0e`. GitHub Actions separately passed on the published PR head (Python 3.10 and 3.14). Private M9 inputs stay outside Git; **no private M9 Drive backup was created**. No M10 or final technical objective is selected. Delivery detail: [docs/M9.md](docs/M9.md).

- **Operation:** holding the left stick left turns global player 003f92d0, and the
  camera follows in the same frame. The chain is 159570 → 159ad0 → 15fe00 (a1 = 1)
  → 15fe70 → 1608b0 → 160510 (rate +440, heading +44) → 12c790 → 12d130 → 12bd00 →
  look-at 12c3d0 → view matrix 70000000.
- **Evidence:** one continuous PCSX2 v2.8.2 run of 37 native stops over 8 frames:
  neutral, 5 held, release, zero. Every update, turn-entry probe and camera
  stage: **0 differing bytes in 32 MiB EE RAM and the 16 KiB scratchpad.** The
  handoffs to camera and to the next frame match. Input counterfactuals diverge, and 14
  compiled/model defects are rejected. 11 routines / 1,147 words / 4,588 bytes
  are original-byte verified. See [docs/M9.md](docs/M9.md).
- **Corrected M8 defect:** 159ad0 calls 15fe00 with a1 = 1 (delay slot 159b08); M8
  passed 0.
- **Checks (owner-local, recorded 2026-11-16):** inventory at `6062f0e` ([checks.json](evidence/m9/checks.json)): **59/59 passed** on the first attempt. That covers 210 synthetic tests, M1–M8 instruction and saved-runtime regressions, ASan/UBSan, PS2DInfo validation, the publication audit and `git diff --check`. Every control rejection is a measured divergence, never an exception. The earlier run at `8742ad0` is preserved: check 21 FAILED on an output-stem collision (not behavioural), then passed in the session rerun and in Noah's manual recheck (`m9-transition-recheck-001.json`). The private runner now prevents that collision.
- **Identities:** SCPS-15046 ELF `a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61`,
  ISO `8ca1513c8417c0a40b838ff5edf22a2360f43bce1b0458693a95a6efdafd994e`, PCSX2
  v2.8.2 AppImage `0c46bb6a88aa2782b10853a7b07cf3387ba99cbef2b966372cd2315b8571abea`
  (CRC 91AA4B28). PS2DInfo validators come from the clean local reference
  `2e4a226e`. PS2DInfo `main` is `88fa49b6` as reported by Noah; this session did
  not fetch it.
- **Private inputs** (ignored `local/m9/`): captures, screenshots, PCSX2 profile and
  save state, Ghidra exports, compiled controls, final-check logs.
- **Limits:** hybrid C with original-instruction-modeled math, look-at and strafe
  leaves. 12c790 and 12bd00 cover their observed branches only (other branches
  fail closed). One observed operation: a left turn at level 3, with no walking,
  strafing, look or pitch. Functional FP without flags or timing, under PCSX2
  defaults. Capture names release1–3 are held-stick frames.
- **Blockers:** none known. The earlier "local, unpublished" status is superseded; see the delivery addendum in [docs/M9.md](docs/M9.md).
- **ONE next action:** Noah chooses the next development direction after administrative integration. M10 has not been started.

## M8 connected gameplay result — 2026-11-16 (delivery addendum, same day)

**Superseded as the resume point by M9 above.** M8 is implemented and compared against the original game, and its
private Drive backup is project-reported complete. PR
[#7](https://github.com/finakechi/Hungry-Ghosts---Decomp/pull/7) is **merged into
`main`** as merge commit `4034189bd84df9c1d89c99b63300d9a08f58f8ef`. Its former
branch head `9fc0eb33afa712e2726a4bd26148739396c33f7f` is an ancestor of that merge,
not the merge commit. M8 implementation commit:
`0fd81f60a75163528730adcf8b0163c8b4ffd00a`. Retail inputs (original SCPS-15046
image/ELF, PS2DInfo, PCSX2 captures under ignored `local/m8/`) are owner-private and
absent from CI. **Blockers:** none known. **No M9 or final technical objective is
selected.** **ONE next action:** after the administrative documentation is on `main`,
Noah chooses the next development direction. Do not repeat the backup or start M9
before that choice.

Evidence levels (see [docs/M8.md](docs/M8.md)):

- **Original-game comparison (recorded earlier 2026-11-16):** global player
  003f92d0, 0 differing bytes over 33,423,360 EE RAM bytes per stop, across
  initializer 158a10, Square-edge update 159570, the C state at the 158e20
  transition stop, the following update and its 175190 consumer stop. No-input and
  changed-input controls pass; five compiled C defects and chop-rounded `DIV.S`
  are rejected. 35 M8 functions / 2,251 words / 9,004 bytes.
- **Tests:** 191 synthetic tests and the 52-check regression inventory
  ([connected-checks.json](evidence/m8/connected-checks.json)) passed when
  recorded. The 191 synthetic tests were re-run at the documentation-cleanup
  stage (Python 3.14, GCC, JDK 21): pass. The 52-check inventory and original-game
  captures were not re-run. PR CI (Python 3.10/3.14) succeeded at the PR #7 head before merge.
- **Private backup (project-reported complete; partly independently inspected):**
  snapshot `20261116T180057Z-544f2b16d903` (3,429 files / 2,750,856,008 bytes) at
  implementation `0fd81f6`. The project handoff reports it was uploaded through the
  configured rclone Drive connection, freshly downloaded, verified against trusted
  checksums, restored in isolation and its Git history checked. The cleanup session
  found the retained downloaded copy and restoration directory under ignored
  `local/`: archive/manifest SHA-256 match the snapshot, all 3,429 restored files
  match the manifest, and the bundle verifies as complete history with HEAD
  `0fd81f6`. It found **no local Drive transport receipt** and did not contact Drive,
  so the upload and the download's Drive origin rest on the owner/project report.
  The snapshot predates `9fc0eb3` and every later commit, including the merge.
- **Limits:** hybrid reconstruction with modeled math/engine leaves; not
  whole-game equivalence, all-C, static recompilation or byte matching. 175190 is
  reconstructed for state 0 only; one control's 64-byte camera matrix comes from a
  separate capture; FP flags, exceptional values, timing and external runtime
  effects are not modeled.

Recovery of the interrupted session repaired an invalid evidence reference
encoding, a measured `DIV.S` rounding-model error (PCSX2 default: Nearest) and an
undeclared libpad IOP-DMA buffer (now an explicit external domain). Interrupted
files are hash-preserved in `local/m8/recovery-20261116T171425Z/`.

History below: the original M8 delivery wording ("Drive pending", 2026-11-16
earlier in the day) is superseded by this addendum and retained in
[docs/M8.md](docs/M8.md) as the contemporary record.

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
push, PR, merge, remote CI or private upload is claimed. Implementation commit
`7db283852d056da0b4f8bc9609dce00954c5d135` has a fully verified additive local
snapshot: **2,091 files / 1,043,264,642 bytes**, complete isolated restore and
recovered Git fsck pass. This is same-disk recovery, with no remote upload. The
later preservation-only handoff commit is outside that immutable snapshot.

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
