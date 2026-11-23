# Audio Milestone Evidence (public-safe)

Recorded verification results for the audio subsystem reconstruction.
Private artifacts (authenticated member binaries, decoded WAV output,
registration receipts, disassembly dumps, differential detail) live under
`local/audio/` in the worktree and are never committed.

## Files here

- `reconstruction-functions.json` — the 30 reconstructed original EE
  functions with exact original entry/end ranges, plus the explicitly
  modeled external seams (kept separate; they are contracts, not
  reconstruction claims).

## Recorded checks

| Check | Result | Where |
|---|---|---|
| Disassembler vs 13,888 known-good listings | passed | `tests/test_hg_audio_r5900.py` |
| Synthetic engine suite (29 tests incl. negative controls) | passed | `tests/test_hg_audio_engine.py` |
| ADPCM decoder synthetic fixtures (10 tests) | passed | `tests/test_hg_audio_adpcm.py` |
| M7 authenticated-resource endpoints (3 tests) | passed | `tests/test_hg_audio_original.py` |
| Original-bytes differential (21 top-level + 5 nested functions, 20 randomized cases each) | **407 passed, 0 failed, 0 skipped, 13 excluded** | `local/audio/differential.json` |
| Connected runner end-to-end | see `local/audio/runner-receipt.json` | `tools/hg_audio/audio_runner.py` |

## Input identities (authenticated before every original-byte claim)

- Japan ISO: `8ca1513c8417c0a40b838ff5edf22a2360f43bce1b0458693a95a6efdafd994e`
- Boot ELF `SCPS_150.46`: `a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61`
- PACK dir 0x6b member 0x51: `3776b4159cacfe36e86648c459b99542e9a5ce29fc64dc8aa5111751ffc8284f`
- PACK dir 0x85 member 0x94: authenticated, extracted under `local/audio/`.

## What is NOT claimed

- No IOP module (LIBSD/SDRDRV/SG2IOP/IOPRP271) disassembly or behavior
  reconstruction. The service side is a documented host model.
- No SPU2 timing/mixing/effects verification.
- No playback-timing equivalence with the original console.
