# Hungry Ghosts — start here

## M24 audio sequencing and physical voice management — 2026-11-27

**Resume here for audio.** Local branch `research/m24-audio-sequencing` in
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
