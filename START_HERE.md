# Hungry Ghosts — start here

## M21–M22 local integration review — 2026-11-26

**Resume here.** Branch `integration/m21-m22-review` in
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
