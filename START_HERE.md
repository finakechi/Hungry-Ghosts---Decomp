# Hungry Ghosts — start here

## M21 scene coordination — 2026-11-24

**Resume at M21** on local branch `research/m21-scene-coordination` in
`decomp-m21`, based on `b855eb93fa797eded31c1849f2bb69d68470609a`.

- The carried C path joins M19 resource publication and M20 timed dialogue to
  the original title actors, then reaches the first AREA1 coordinator in
  1,174 post-boot units. Eight AREA1 checkpoint domains match fresh PCSX2.
  The separate ferry reload matches seven domains. The owner run accepted
  all 79 gates.
- Fresh original runs also verify title reset, controlled AREA1 movement,
  pause/resume, and return to title. The C world setup after AREA1 entry is open.
- [M21 results and reproduction](docs/M21.md).
  [Master handoff](MASTER_HANDOFF.md).
  [Archived start-here entries](START_HERE_HISTORY.md).

Next, carry the AREA1 world initialization into the first controller-driven
frame on the same guest bus. Use the private fresh captures under `local/m21/`
and the existing M22 audio interface.

## M22 SG2IOP audio service and playback — 2026-11-24

**Resume here for audio.** Local branch `research/m22-audio-service` in
`decomp-m22`, implementation
`a4ee75ab4317d6230d9a442fc7fd3f10ae47caf7`. M21 and M22 share
`b855eb93fa797eded31c1849f2bb69d68470609a` on
`integration/m19-m20-review`. Japanese retail identity is SCPS-15046,
PCSX2 CRC `91AA4B28`, ELF SHA-256
`a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61`.

The EE registration and play-request paths now reach SG2IOP RPC
`11223344`. Four uploads across three PACK members matched 804,336 bytes
of saved SPU2 RAM. Fresh cursor down, cursor up and confirmation supplied
92 supported voice records. The original SG2IOP and LIBSD instructions
agreed with 201 ordered register writes and the model rendered 16,000
stereo frames. See [M22](docs/M22.md) and the [retail receipt](evidence/m22/retail-check.json).

Strict GCC/Clang, six ASan/UBSan connected checks, all 54 focused audio
tests, the 467-test project suite and the 407-case legacy EE comparison
passed. The owner profile `m12-20261124T235355Z-yg8tjftd` accepted
79/79 gates on the implementation checkpoint, with zero reused results. The private inputs are the Japanese disc and
ELF, extracted IRX files, the M18 saved PCSX2 state, and four fresh M22
states in `local/m22/live/`. The full profile uses the historical inputs
and pinned PS2DInfo listed in the integration handoff.

The M21 contract accepts resolved notes and sample offsets through
`src/audio/audio_iop.h`. The host approximates envelopes and PCM timing.
Streaming and the automatic EE note-to-voice scheduler remain open.
Reconstruct the EE cursor note dispatch at `002ade94` through handler
`002ae2c0`, then compare the calculated RPC packets with both fresh
cursor checkpoints.
