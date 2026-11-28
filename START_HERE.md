# Hungry Ghosts — start here

## M26 audio timing and voice management — 2026-11-27

**Resume here.** Branch `research/m26-audio-timing` in `decomp-m26`, based on
pinned `origin/main` `7191ca7402b5c8f0d5a58dbd38c62d099470c667` (PR #23 merge;
leaves local `main`, M25, and world/gameplay code untouched). Working tree is
**uncommitted** pending user approval for commit/push/PR. Full detail:
[M26](docs/M26.md).

- **Authenticated reference timing research**
  ([evidence/m26/reference-timing.json](evidence/m26/reference-timing.json),
  `tools/hg_audio/m26_reference_timing.py`):
  - **Wake source confirmed from `SCPS-15046` ELF bytes (`a74248c5…`):** boot
    `0011d880..0011d8b8` registers VBlank-End handler `0011df80` (INTC 3), which
    wakes audio thread `001566c0` via `00104e10` → `00103ea0` (`iWakeupThread`,
    syscall `-52`) when `003f6db0 == 1`.
  - **12 M22 PCSX2 v2.8.2 checkpoints:** across 5,618 VBlanks (`title-idle`
    through `cursor-confirm`), `VBlankStart(0095e300) - AudioWake(00a6c3c0) == 1`
    and all 16 slot counters `00a6c344 + 8n == 00a6c3c0` identically.
  - **Cursor attribution & retirement:** at `cursor-up`, voice 8 (`owner=0`) is
    `cursor-down`'s 3rd note while voices 9–10 (`owner=1`) are `cursor-up`;
    Slot 3 (`002b2438`) retires voices at `age >= 3 && ENVX < 2` and enqueues
    masks in order `0x09, 0x0a, 0x07, 0x08`, followed by Slot 2 channel and
    Slot 1 command retirement.
  - **Limits:** saved frame-boundary snapshots only; live multi-frame PINE
    capture was not run.
- **Independent timing & voice model** (`src/audio/audio_sequencer.{h,c}`):
  additive opt-in `HG_SEQ_TIMING_CHRONO` mode (`HGSeqTimingConfig`,
  `HGSeqClock`, `HGSeqDecisionTiming`, 9 new APIs; 289 combined exported
  functions, 0 duplicates). Distinguishes elapsed, request, due, service/flush,
  and render time; supports 0-tick/unit/coarse/irregular schedules, rotating
  cursor or lowest-free allocation, steal/refusal, polyphonic/retrigger repeat,
  pending/active cancel retirement, deferred exclusive release holds, bank
  replacement, and reset/rebind. Legacy M22/M24/world-audio receipts stay
  byte-identical.
- **Verification (2 new gates; profiles: `focused` 8, `historical`/`full` 79,
  `synthetic` 42, `current` 140):**
  - Connected driver (`tests/audio_timing_lifecycle.c`,
    [evidence/m26/timing-lifecycle.json](evidence/m26/timing-lifecycle.json)):
    all 7 scenario families, 3-schedule logical parity (`unit-tick`,
    `coarse-2`, `irregular-zero`), Cycle 2 repeat parity, 10/10 negative
    controls, byte-identical across GCC/Clang + ASan/UBSan/LSan.
  - `synthetic` (`m12-20261128T021136Z-o9n0yove`, `--jobs 1`): **42/42 accepted**
    (40 passed, 2 expected rejections, 0 reused, 0 bad seals, 353.8 s).
  - `current` (`m12-20261128T021844Z-end66r4x`, `--jobs 1`, `local/m26/config.json`):
    **140/140 accepted** (135 passed, 5 expected rejections, 0 reused, 0 bad
    seals, Gate 00 510/510 with 0 skips, 5,079.5 s).
  - Coverage/results: [m26-coverage.json](docs/verification/m26-coverage.json),
    [m26-results.json](docs/verification/m26-results.json).
  - Private inputs needed for full `current`: `HG_JAPAN_ISO`, `HG_ELF`,
    `local/m22/{retail,live}`, M18/M21/M4–M11 saved states, and pinned PCSX2
    v2.8.2 (`{emulator}` in `local/m26/config.json`).
- **Not claimed:** retail equivalence, retail sequencer/allocator
  reconstruction, live multi-frame PINE timing, streaming dialogue
  (`001564a0`/`00156550`), full IOP emulation, or PCM equivalence.
- **Next single action:** review and approve committing `research/m26-audio-timing`
  (or request optional live PCSX2 PINE multi-frame capture before commit).

## M23–M24 integration review — 2026-11-27

**Historical baseline (merged into `origin/main` at `7191ca7402b5c8f0d5a58dbd38c62d099470c667`).** Branch `integration/m23-m24-review` in
`decomp-m23-m24-integration`, based on remote `main`
`09b5832119d5ec97c85c05c6ab1eddd6c20be8ad` (PR #22). It merges
`research/m23-gameplay-initialization` (`6cc3ce8d`) and
`research/m24-audio-sequencing` (`1830f5ec`), which carry the accepted
M19–M20 (`b855eb93`) and M21–M22 (`705cd20f`) histories. Tested commit
`77a8dee39813a8ec8e8ade6952279f4891559f00`. Full detail:
[M23–M24 review](docs/M23-M24-review.md).

- **Verification migration:** named `HG_ELF` skip contract (Gate 00
  authenticates the ELF before exporting it; skip allowed only when unbound),
  explicit M19 `build_path()`, and 18 new `current` gates with contracts in
  `tools/hg_verify_milestones.py` (14 authenticated M19–M24, 4 synthetic).
  Profiles: `focused` 8, `historical`/`full` 79, `current` **138**,
  `synthetic` **41**. Coverage map:
  [docs/verification/m23-m24-coverage.json](docs/verification/m23-m24-coverage.json).
- **Connected lifecycle:** `tests/world_audio_lifecycle.c` runs the M23
  world harness and the M22/M24 audio path on one guest EE RAM. Synthetic
  events from carried world state drive `hg_audio_play`; the M24 model makes
  every note/voice decision. Two cycles (143 frames, 17 requests, 19
  key-on/off, 1 steal, 101,600 non-zero PCM frames each) with clean teardown,
  re-entry, bit-identical standalone-M23 parity and 10 detected controls.
- **Results:** `current` 138/138 accepted (`m12-20261128T000859Z-4aeadr8x`, 4 workers, 2,280.6 s; Gate 00 505/505, 0 skips); `synthetic` 41/41 (`m12-20261128T004904Z-tqj5dqx3`, 1 worker, 320.0 s; 501 + 4 named skips); 0 bad seals.
- **Not claimed:** retail equivalence, retail sequencing/timing/allocation,
  authentic initialization past `0012f850`, IOP/PCM equivalence.
- **Next (not started):** authentic AREA1 state-0 world setup (world track)
  and retail sequencer timing/allocation observation (audio track); see the
  review's recommendations.

## M24 audio sequencing and physical voice management — 2026-11-27

**Historical (integrated by the M23–M24 review).** Local branch `research/m24-audio-sequencing` in
`decomp-m24`. It is based on the accepted integration HEAD
`705cd20fcc28d17b3fac374dfad34c990f76ddc6` and is independent of `main` and
M23. The implementation checkpoint is `263b246`.

- **Reference map (read-only, authenticated ELF):**
  [evidence/m24/reference-map.json](evidence/m24/reference-map.json).
  - The EE audio thread `001566c0` sleeps, checks the RPC, then runs 16
    scheduler slots (`002a7450`). Slot 1 is the sequence service `002add90`,
    slot 2 the channel consumer `002a96b8`, slot 3 voice preparation
    `002b2438` and slot 4 the SG2IOP RPC `002ad138`.
  - The streams are SMF-like: running status and variable-length deltas,
    dispatched through `003bd170` and `003bd078`.
  - Voice allocation runs `002ae9b8`, then `002afed8`, then `002af1d0`, then
    `002b1098`.
  - What wakes the thread and how often is unknown.
- **Synthetic model** (`src/audio/audio_sequencer.{c,h}`, original HSQ1
  format). It adopts `hg_audio_play` commands and owns all timing, note,
  allocation, stealing, release, cancel, bank-loss and reset decisions. It
  stages through M22 only.
- **Verification:**
  - 9 connected scenarios and 5 negative controls pass, with an
    independent SPU2/ENVX/ownership/timeline checker.
  - GCC, Clang and ASan/UBSan receipts are identical
    ([evidence/m24/sequencer.json](evidence/m24/sequencer.json)).
  - The title-menu cursor request now plays automatically in
    `tests/scene_audio_interface.c`.
  - 478/478 discovered tests pass with retail inputs.
  - The M22 retail receipt reproduced identically, and the legacy EE
    differential passed 407/0.
  - The full profile accepted **79/79** on `a1df5fc` (74 passed, 5
    expected rejections, 0 reused): `m12-20261127T164636Z-_7x8pqlh`.
- **Not claimed:** retail sequencer or allocator reconstruction, automatic
  retail sequencing, or PCM equivalence.
- **Next:** record `00a6c3c0`, command `+0x20..+0x48`, the channel ring and
  the voice records at `00a832b0`/`00a832a0` across the two M22 cursor
  checkpoints. Then reconstruct `002add90` and `002ab508` against them.

## M23 cold gameplay initialization and world-state continuity — 2026-11-27

**Historical (integrated by the M23–M24 review).** Branch `research/m23-gameplay-initialization` in
`decomp-m23`, branched from the shared baseline `integration/m21-m22-review`
(`705cd20fcc28d17b3fac374dfad34c990f76ddc6`, tested source
`5e7f10776492e36cacd463d507ba9089f0ccab0a`). Verified M23 implementation commit:
`b0464cd5bc54c95e33146b8d28dd8c7e9d5c5222`. Leaves audio (`src/audio/`,
`tools/hg_audio/` — M24), shared baseline C sources, `decomp-m21-m22-integration`,
local `main`, and `origin/main` untouched.

- **Authenticated cold-to-corridor dependency map:** Traced Japanese `SCPS-15046`
  (`a74248c5…`) and owner captures (`area1-request`, `area1-active`, `obs3-000`).
  Last supported retail cold-entry boundary remains `0012f850` (`area1-active`);
  earliest missing prerequisites are (1) `0012e700` sub-0 setup callees
  (`00141b10 -> 00141910 -> 00141210 -> 0013fef0 -> 0012bf70 -> 0013a4c0 ->
  00189aa0`) and (2) `00158560` state-0 init (`001585c0 -> 00158a10 + 001e3440 +
  phase 0->1`), confirmed by live `HG_DOMAIN_FAULT(0x30)` on `area1-active`
  (`evidence/m23/authentic-boundary.json`).
- **Reusable provenance harness & original synthetic lifecycle:** Added
  `src/scene/world_init.{h,c}`, `tests/world_init_harness.c`,
  `tools/verify_world_init.py`, and `tests/test_world_init.py` on the existing
  `HGResourceEnv` 32 MiB EE RAM + 16 KiB scratchpad bus. Proves two full cycles
  of an independently authored synthetic courtyard (`spawn=(12,10,20)`) through
  resource publish, stale title handle clear, world entry, 63-frame fade, 60
  consecutive controller updates (walk, turn, 26 wall contacts, release), clean
  teardown (`2048/2048` free nodes, `0` actors), and Cycle 2 bit-exact parity,
  plus 10/10 targeted negative controls (`evidence/m23/synthetic-lifecycle.json`).
  Synthetic execution is explicitly distinct from retail equivalence.
- **Verification on `b0464cd5`:** Strict combined GCC 16.2.1 & Clang 23.1.5
  builds (all 11 `src/**/*.c`), 4/4 GCC/Clang ASan+UBSan+LSan checks,
  **475/475** discovered tests with `HG_JAPAN_ISO` (0 skipped), M21 cold-entry
  and M17 22-frame corridor regressions, and **79/79** full-profile gates fresh
  (0 reused) in `m12-20261127T160149Z-aq4rtrbd`.
- [M23 initialization map, harness, and verification](docs/M23.md).
  [Master handoff](MASTER_HANDOFF.md).

## M21–M22 local integration review — 2026-11-26

**Accepted integration baseline** (M24 builds on it). Branch `integration/m21-m22-review` in
`decomp-m21-m22-integration`, based on `integration/m19-m20-review`
(`b855eb93fa797eded31c1849f2bb69d68470609a`).
Verified integrated source commit: `5e7f10776492e36cacd463d507ba9089f0ccab0a`.
Merges M21 (`research/m21-scene-coordination` at `167a7281`) and M22
(`research/m22-audio-service` at `54f1f130`) locally with ancestry and evidence
preserved. Original worktrees, local `main`, and `origin/main` are unchanged.

- **M21 preserved & re-verified:** Cold boot through AREA1 coordinator in 1,174
  units (8 domains), title menu + 3 negative controls (198 frames), neutral
  dialogue (3,991 frames, 10,801 glyphs), AREA1 entry (7 domains), and ferry
  reload (7 domains). Cold gameplay spawn, actor and camera initialization
  remain open.
- **M22 preserved & re-verified:** EE/IOP SG2IOP transport (`11223344`), 4
  uploads (804,336 bytes) matching SPU2 RAM, 201 ordered SPU2 register writes
  across 3 fresh menu checkpoints, 16,000 modeled stereo PCM frames, 407/407
  legacy EE audio differential. Fixed unbound/post-teardown NULL safety in
  `src/audio/audio_iop.c`. Automatic note sequencing, physical voice allocation,
  streaming voice (`001564a0`/`00156550`), full IOP behavior, and PCM
  equivalence remain open.
- **Scene-to-audio boundary verified** (`tests/scene_audio_interface.c`,
  `tests/test_scene_audio_interface.py`): On one shared guest bus,
  `hg_pack_special_step` (`00156a50`) registers and uploads a synthetic SShd
  bank end-to-end through M22; `hg_title_menu` (`00156ad0`, ID 4) reaches
  `hg_audio_play` via the documented `00157ed0` descriptor and stops at the
  missing EE sequencer/voice allocator before progressing through M22's
  resolved-note adapter (`hg_audio_ee_stage_play_voice` -> `hg_audio_iop_render`
  -> `hg_audio_cancel` + key-off -> cleanup); HGScript dialogue (`001564a0` /
  `00156550`) stops at the separate streaming-voice boundary (`gp+0x2ab0` ->
  `00156570`, outside M22). All targeted negative controls pass.
- **Verification on `5e7f1077`:** Strict combined GCC/Clang builds, 10/10
  GCC/Clang ASan+UBSan+LSan C harnesses + 6/6 M22 Clang ASan+UBSan checks,
  **470/470** discovered tests with `HG_JAPAN_ISO` (0 skipped; 3 retail-only
  Audio tests skip when `HG_JAPAN_ISO` is unset), all M17–M22 owner-local
  regressions, and **79/79** full-profile gates in
  `m12-20261126T170926Z-prt6dzmf`.
- [M21–M22 integration review and reproduction](docs/M21-M22-review.md).
  [M21](docs/M21.md). [M22](docs/M22.md).
  [Master handoff](MASTER_HANDOFF.md).
  [Archived start-here entries](START_HERE_HISTORY.md).

**Next two independent milestones:** (1) carry cold AREA1 entry through world,
actor, and camera initialization into the first controller-driven frame; (2)
reconstruct `00156ad0`/`00157ed0`, the EE note sequencer (`002ade94..002ae2c0`,
`002a96b8`, `002ab508`), and the physical voice allocator to replace the
external resolved-note seam.
