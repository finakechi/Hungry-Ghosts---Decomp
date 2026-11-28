# Hungry Ghosts — start here

## SCPS-15046 research baseline (post-realignment M25/M26 reconciliation) — 2026-11-28

**Resume here.** The active scope is reverse engineering and decompilation
research for the original Japanese **SCPS-15046** build. Existing independent
models (including M25's world-session fixtures in `src/scene/world_init.{c,h}`
and M26's HSQ1 timing fixtures in `src/audio/audio_sequencer.{c,h}`) are
retained strictly as legacy verification infrastructure and contribute zero
recovered original instruction bytes. The separate
[Hungry Ghosts PC](https://github.com/finakechi/Hungry-Ghosts---PC) repository
holds a future-port research index (`main` `16d48310`, PR #1);
[PS2DInfo](https://github.com/finakechi/PS2DInfo) (`2e4a226e`) owns shared PS2
tooling.

- **Accepted source identities:**
  - Japanese `SCPS-15046` ISO `8ca1513c…`, boot ELF `a74248c5…` (CRC `91AA4B28`, entry `0x00100008`).
  - Decomp PR #25 (`research/scps-15046-realignment` `ea330a5e`, merged on `main` at `32cf2bfb`).
  - Decomp PR #24 (`research/m25-world-initialization` original `969e913c`, reconciled `205ce2fd`, merged on `main` at `4951c8aa`).
  - Standalone M26 (`research/m26-audio-timing` `d0230a7e` on `7191ca74`), integrated here onto `4951c8aa` (branch `integration/m26-audio-timing`). The earlier local snapshot `integration/m25-m26-review` (`1be28330`, containing uncommitted-tree snapshot `5750236a`) is preserved locally, not pushed, and not used as the integration source.
- **Verified original-game evidence retained:**
  - [Coverage inventory v2](evidence/realignment/coverage.json) and [realignment report](docs/SCPS-15046-realignment.md): 178 / 4,525 source-mapped resident EE entries, 93,452 / 2,469,784 candidate bytes checked (+4 outside), AREA0 `5/6` (2,504 B), AREA1 `1/2` (2,180 B), IOP 80 checked instruction bytes across SG2IOP/LIBSD/SDRDRV, VU unmeasured; recovered-original-instruction byte coverage remains unmeasured.
  - [Collision replay](docs/retail-collision-snap.md) ([receipt](evidence/realignment/collision.json)) and [M25 live/authentic evidence](docs/M25.md) (`evidence/m25/{live-continuity,authentic-dependencies,provenance-ledger}.json`): same-run AREA1 PCSX2 stops (`0012f850` tick 0 → `001585c8` → `001585e0` → `0012f8d4` → ticks 1–3 → walk ticks 114–200), 5,333-step / 824-address original-instruction replay of `001e3440` with 243/243 matching non-stack writes and 3 perturbed no-hit controls.
  - [Audio sequencing research](docs/retail-audio-sequencing.md) and [M26 reference timing](evidence/m26/reference-timing.json) ([M26](docs/M26.md)): 23 ELF instruction anchors confirming VBlank-End INTC 3 (`0011df80`) → `iWakeupThread` (`00103ea0`, syscall `-52`) → `001566c0`, 1:1 VBlank-to-wake cadence across 12 M22 saves (5,618 VBlanks), cursor voice attribution (`voice 8` = `owner 0` at `cursor-up`), and Slot 3 retirement (`age >= 3 && ENVX < 2`, mask order `0x09, 0x0a, 0x07, 0x08`).
- **Verification registry (`focused` 8, `historical`/`full` 79, `current` 143, `synthetic` 43):** All 3 M25 gates and 2 M26 gates are registered in `config/current-verification.json` with contracts in `tools/hg_verify_milestones.py` / `tools/hg_verify_m25.py` and tests in `tests/test_hg_verify.py`. Public/unbound discovery preserves the 4 named retail skips. Historical branch receipts remain attributed to their actual source commits (`969e913c` / `4075bec7…` for M25; `d0230a7e` / `feeafd55…` for M26).
- **Current limits (not claimed):** No retail equivalence, C recovery of `001e3440`/collision meshes, retail audio sequencer/allocator reconstruction, streaming dialogue (`001564a0`/`00156550`), `00156cf0` resume, full IOP/VU census, or hardware ADSR/PCM equivalence. M25 unobserved stops (`001586c8`, `0012f8cc`, ticks 4–113, neutral release, wall contact, retail teardown) and audio fallback contention (`002afed8`/`002af1d0`, RPC-busy rate, sub-frame latency) remain open.
- **Next single highest-value original research action:** Analyze **collision mesh candidate selection around `00145740` and the second query `00268ad0`** (inside `00144820` called by `001e3440`). Current evidence favors this over `002afed8`/`002af1d0` because `001e3440` is the earliest unsupported retail dependency blocking AREA1 cold-entry continuity (and shares `00144820` with M12 grounded movement), and we already have an authenticated same-run capture pair (`001585c8` → `001585e0`) plus a deterministic 5,333-step original-instruction replay ready for offline analysis without a new capture, whereas `002afed8`/`002af1d0` have zero contention observations in existing M22 saves.

## M26 audio timing and voice management — 2026-11-28

**Integrated onto realigned `main` (original reference timing retained; HSQ1 timing model preserved as legacy verification infrastructure).** Standalone commit `d0230a7e` on branch `research/m26-audio-timing` (`decomp-m26`, base `7191ca74`), merged into `integration/m26-audio-timing` on `4951c8aa`. Full detail: [M26](docs/M26.md).

- **Authenticated reference timing research**
  ([evidence/m26/reference-timing.json](evidence/m26/reference-timing.json),
  `tools/hg_audio/m26_reference_timing.py`): 23 ELF anchors confirming VBlank-End
  INTC 3 (`0011df80`) → `iWakeupThread` (`00103ea0`, syscall `-52`) → `001566c0`;
  12 M22 checkpoints showing `VBlankStart(0095e300) - AudioWake(00a6c3c0) == 1`
  and all 16 slot counters matching `00a6c3c0`; cursor voice attribution and
  Slot 3 retirement order (`0x09, 0x0a, 0x07, 0x08`). Saved frame-boundary
  snapshots only; live multi-frame PINE tracing was not run.
- **Independent timing & voice model (legacy verification fixture)**
  (`src/audio/audio_sequencer.{h,c}`): additive opt-in `HG_SEQ_TIMING_CHRONO`
  mode (9 new APIs; legacy M22/M24/world-audio receipts unchanged); 7 scenario
  families, 17 schedule runs × 2 cycles, 10/10 negative controls
  ([evidence/m26/timing-lifecycle.json](evidence/m26/timing-lifecycle.json)).
- **Historical standalone M26 branch receipts (`d0230a7e`, `source_sha256` `feeafd55…`):**
  `synthetic` `m12-20261128T021136Z-o9n0yove` (42/42 accepted, 353.8 s);
  `current` `m12-20261128T021844Z-end66r4x` (140/140 accepted, Gate 00 510/510, 0 skips, 5,079.5 s).
  Coverage/results: [m26-coverage.json](docs/verification/m26-coverage.json),
  [m26-results.json](docs/verification/m26-results.json).
- **Not claimed:** retail equivalence, retail sequencer/allocator reconstruction,
  live multi-frame PINE timing, streaming dialogue (`001564a0`/`00156550`),
  full IOP emulation, or PCM equivalence.

## M25 world initialization and state continuity — 2026-11-28

**Merged into realigned `main` at `4951c8aa` (PR #24; original evidence retained; synthetic world-session fixtures preserved as legacy verification infrastructure).** Branch `research/m25-world-initialization` (original `969e913c` on `7191ca74`, reconciled with PR #25 `32cf2bfb` at `205ce2fd`). Detail: [M25](docs/M25.md).

- **Synthetic (legacy verification fixture).** Two original scenes run four interleaved cycles of boot →
  publish → setup → 134 updates → teardown → re-entry. Parity holds, 14/14
  controls are detected, and 36/36 invalid inputs are rejected.
- **Live (one original PCSX2 run).** Covers tick 0, `00158a10` return, `001e3440`
  return (Y 38.0455322265625), first camera follow, ticks 1–3, and walk ticks 114–200.
  Tick 0 equals M21 `area1-active`; `00158a10` return equals M8 and C reproduces it;
  original instructions from tick 0 reproduce the post-camera stop in all 11 domains.
- **Historical M25 branch receipts (`969e913c`, `source_sha256` `4075bec7…`):**
  `current` `m12-20261128T141916Z-kghnfr1n` (141/141 accepted, Gate 00 516/516);
  `synthetic` `m12-20261128T141423Z-cphcz36j` (42/42); 0 bad seals.
- **Still unobserved or unreconstructed:** `00158470` return register, ticks 4–113,
  neutral release, wall contact, retail teardown, C for `001e3440`.

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
