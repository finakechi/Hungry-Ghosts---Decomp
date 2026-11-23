# Audio Subsystem Format Analysis (public-safe)

Derived exclusively from the authenticated Japanese original
(ELF SHA-256 `a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61`)
and the two M7-authenticated audio members. No commercial sample bytes,
disassembled bodies, or private paths are reproduced here; full dumps stay
under `local/audio/` in the worktree.

## SShd bank resource

- Magic `"SShd"` at resource offset 0; the bank base is at payload+0x18.
- M7 endpoint formula (one-bank inputs, verified arithmetic):
  `(input + word(input+0x10) + 0x40) & 0xffffffc0`.
- Bank descriptor: u32 count, then per-descriptor `{offset, size}` pairs;
  the worker (0x00157060) walks them by phase.
- Six slot pointer words are computed from bank offset fields and stored
  into the 8-slot table at 0x00a87440 (stride 0x2c).

## EE state layout (constant addresses)

| Region | Address | Notes |
|---|---|---|
| gate/phase/desc counters | 0x003eaa50..0x003eaa70 | gp-relative state bytes |
| per-id counters | 0x003eaa00 | gp+0x2a90 table |
| slot table / aux | 0x00a87440 / 0x00a87450 | 8 × 0x2c |
| id table | 0x003f2740 | 120-byte structs |
| channels | 0x00a6c4d8 | 32 × 0x9e4 |
| channel command queues | 0x00a6c530 | 32 × 0x9e4 (ring at +0x200) |
| voice cursor | 0x00a80158 | rotating |
| command structs | 0x00a82640 | 32 × 0x50 |
| command cursor | 0x00a83040 | rotating |
| sequence counter | 0x00a832a4 | |

## Channel struct (0x9e4 bytes)

Header words at +0x00..+0x58 (state −1, flags, 0x7f volume, 0x80 byte,
0x1000 counts, +0x2a0 sub-voice flag), then 16 sub-voices at +0x2a4,
stride 0x74. Each sub-voice records channel id, 0xff/0x40 duty fields,
0x7f gains, 0x1000 count, and the `(i ^ 9) == 0` discriminator words at
+0x28/+0x2c (established from the original loop in 0x002a9050).

## EE→IOP command ring

`hg_audio_channel_command` appends bytes at channel+0x200+index and
bumps the index word. The observed stop sequence from `hg_audio_cancel`
is `ff 41 f7`. Play posting goes through the 0x50-byte command structs
and the sequence counter.

## DMA transfer

The worker stores `{src, dest, size}` in a scratch struct and calls
`sceSifSetDma`, then polls `sceSifDmaStat`; the IOP alloc address is
kept at gp+0x2afc. The host model mirrors these observable fields only.

## PS2 SPU ADPCM payload

16-byte blocks: header byte `predictor<<4 | shift`, flags byte, 28
4-bit samples. Flags: 0x01 loop-start region end, 0x02 loop start, 0x04
sample start. Loop-point byte offsets are reported by the decoder, not
interpreted into SPU2 voice behavior.

## Disassembly method

`tools/hg_audio/r5900.py` is an independent R5900 decoder validated
against 13,888 Ghidra-derived instruction listings from the repository's
M1–M7 analysis (COP2/VU macro words are excluded and reported as raw
words; the audio subsystem uses none). All audio conclusions were checked
instruction-by-instruction against this decoder, including branch/delay
slots and sign extensions.
