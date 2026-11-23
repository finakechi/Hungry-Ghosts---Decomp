# Audio Milestone — EE Audio Engine and IOP Sound-System Reconstruction

## Status

Connected audio subsystem reconstructed and verified against original
instruction bytes and authenticated resources. The full supported lifecycle
executes offline:

**Authenticated resource → bank registration → sound selection → play
command → modeled IOP service → deterministic audio output → stop → release**

## Git / source identity

- Worktree: `decomp-audio`
- Branch: `research/audio-engine`
- Baseline: `46cdb2189a3190b49830b0222e3a5cfcc4e0fd0b` (verified upstream M16 merge)
- Audio work is committed locally only; nothing pushed, no PR/merge.
- Original inputs (private, never committed):
  - ISO: `8ca1513c8417c0a40b838ff5edf22a2360f43bce1b0458693a95a6efdafd994e`
  - Boot ELF (`SCPS_150.46`): `a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61`
  - Audio members: PACK dir `0x6b` member `0x51` (323,088 B,
    `3776b4159cacfe36e86648c459b99542e9a5ce29fc64dc8aa5111751ffc8284f`)
    and PACK dir `0x85` member `0x94` (46,992 B).

## Reconstructed original EE functions (C, src/audio/)

| Entry | Symbol | Role |
|---|---|---|
| 0x00156a50 | hg_audio_register_entry | registration state gate |
| 0x00157060 | hg_audio_register_worker | descriptor phase machine (jump table) |
| 0x00156f20 | hg_audio_transfer_setup | SIF DMA setup/poll |
| 0x00156fc0 | hg_audio_align16 | alloc + 16-byte alignment |
| 0x002b4db0 | hg_audio_slot_find | free 8-slot search |
| 0x002b4df0 | hg_audio_bank_register | SShd bank → slot tables |
| 0x002b4f68 | hg_audio_bank_index | bank table lookup |
| 0x002b4f88 | hg_audio_slot_active | slot state test |
| 0x002b4fc8 | hg_audio_slot_state | slot state/aux read |
| 0x002b5008 | hg_audio_table_lookup4 | bank+0x3c table walk |
| 0x002b50e0 | hg_audio_table_lookup6 | bank+0x44 table walk |
| 0x002b5178 | hg_audio_table_lookup1 | bank+0x30 table walk |
| 0x002b5228 | hg_audio_null8 | `x ? x+8 : 0` |
| 0x002b5240 | hg_audio_slot_range18 | slot+0x18/0x1c read |
| 0x002b5278 | hg_audio_slot_range20 | slot+0x20..0x28 read |
| 0x002b52c0 | hg_audio_slot_swap | aux word exchange |
| 0x002b52f0 | hg_audio_slot_get | aux word read |
| 0x002b5318 | hg_audio_bank_register_wrapper | ints-off registration wrapper |
| 0x002b53a0 | hg_audio_slot_bank | slot → bank word |
| 0x002b54e8 | hg_audio_slot_clear | zero a slot |
| 0x002b55a8 | hg_audio_slots_init | zero all 8 slots |
| 0x002a99b8 | hg_audio_open_channel_a | voice allocation + prepare |
| 0x002a9b68 | hg_audio_open_channel_b | voice allocation + prepare B |
| 0x002a9050 | hg_audio_channel_init_a | 0x9e4-byte channel struct init |
| 0x002a9180 | hg_audio_channel_init_b | channel struct init B |
| 0x002ad478 | hg_audio_play | post play command |
| 0x002ad748 | hg_audio_cancel | stop/cancel commands |
| 0x002a94b8 | hg_audio_channel_command | queue IOP command byte |
| 0x002ae998 | hg_audio_next_sequence | global sequence counter |
| 0x002adcf0 | hg_audio_bank_bind | bind bank to IOP service |

**Explicit external seams** (host-modeled contracts, not reconstructed):
EE kernel syscalls (FlushCache 0x64, sceSifSetDma 0x77, sceSifDmaStat
0x76), EI/DI interrupt control, SIF allocator (0x0010af40), SIF0_MSCOM
(0x0010b0a8), audio tick (0x002a74d0), time helpers, play variant tail
(0x002ad358), flag/stream service (0x002adab0).

## How the registration contract was recovered

M7 established the endpoint formula `(input + word(input+0x10) + 0x40) &
0xffffffc0` for one-bank inputs and six pointer updates. The original
worker `0x00157060` is a **phase machine**: phases 0–6 walk the bank
descriptor (count at descriptor+0, cursor at gp+0x2aec), phases 7–9 post
the DMA transfer of the resolved payload and call the bank bind. The full
phase jump table was read from the ELF. The two observed M7 resources map
to one- and two-descriptor cases; both are exercised by the harness.

Bank structure (established from the original bytes):

- Resource = `"SShd"` magic + region + bank base at payload+0x18.
- Bank descriptor: count word, then per-descriptor offset/size pairs.
- Six slot pointers computed from bank offset fields; slot tables live at
  0x00a87440 (8 slots, 0x2c stride).
- The DMA'd payload is PS2 SPU ADPCM (16-byte blocks: predictor/shift
  header, flags byte, 28 4-bit samples).

## Sound command path (reconstructed)

- `hg_audio_open_channel_a/b` allocate a 0x9e4-byte channel from a pool of
  32, initialize 16 sub-voices (0x74 stride), and build a 0x50-byte
  command struct at 0x00a82640.
- `hg_audio_play` resolves the bank table (play-path lookup via the
  bank+0x30 table), posts the play command and sequence counter.
- `hg_audio_channel_command` appends bytes to the 512-byte command queue
  at channel+0x200; the queue is the EE→IOP channel ring.
- `hg_audio_cancel` sends the observed stop sequence `ff 41 f7` through
  the ring and releases the command slot.

## IOP boundary

The EE writes a transfer descriptor `{src, dest, size}` to scratch and
calls `sceSifSetDma`; completion is polled with `sceSifDmaStat`. The IOP
side is a **host service model** (tools/hg_audio/audio_guest.py): it
consumes the DMA'd payload, records the alloc'd IOP address, and consumes
queued channel commands. Responses (MSCOM byte, alloc address) are
deterministic and documented, but this is NOT a claim of reconstructed
IOP code. Roles of `LIBSD.IRX`, `SDRDRV.IRX`, `SG2IOP.IRX`,
`IOPRP271.IMG` are **not established** and remain an external boundary.

## Audio decoding / output

Independent PS2 SPU ADPCM (VAG-family) decoder (tools/hg_audio/adpcm.py):
4-bit samples, predictor/history per block, loop flags recorded (0x01
loop-start region end, 0x02 loop start, 0x04 sample start). The runner
renders a supported sample (member 6b51) to `local/audio/out.wav`
(48 kHz, 16-bit mono, 48,000 samples in the recorded run) with
deterministic output and a stage-by-stage receipt
(`local/audio/runner-receipt.json`). No original sample data is
committed.

## Verification

- **Original-bytes differential**: `tools/hg_audio/verify_audio.py`
  interprets the original function bytes with the shared EE-subset
  interpreter and compares complete memory effects against the
  reconstructed C for randomized inputs (21 top-level functions × 20
  cases, plus 5 nested callees verified transitively). Final run:
  **407 passed, 0 failed, 0 skipped, 13 excluded** — the excluded cases
  are inputs on which the original hardware itself would raise an
  Address Error (unaligned table walk), so no host comparison exists.
  See `evidence/audio/` for recorded results.
- **Disassembler ground truth**: `tools/hg_audio/r5900.py` was validated
  against 13,888 known-good Ghidra-derived instructions from the
  repository's M1–M7 listings (tests/test_hg_audio_r5900.py).
- **M7 endpoints**: both observed resources reproduce the M7-reported
  successful endpoint and slot effects (tests/test_hg_audio_original.py).
- **Synthetic suites**: 43 test methods across bank registration,
  malformed inputs, repeated registration, channel/command ring, play/
  cancel, determinism, ADPCM fixtures, disassembler checks (including
  `sync` stype formatting), plus the 3 retail-only M7 endpoint tests,
  which skip without `HG_JAPAN_ISO` (46 methods in all).
- **Negative controls** (tests/test_hg_audio_engine.py
  `NegativeControlTests`): corrupted bank offset, wrong slot selection,
  missing stop behavior, wrong endpoint — each must be detected by the
  intended validator.

## Reproduce

```sh
# build (from decomp-audio)
cc -shared -fPIC -O2 -Wall -Wextra -o local/audio/libhg_audio.so src/audio/audio_engine.c
# synthetic suites (public CI form; no retail inputs). Without HG_JAPAN_ISO
# the three retail-only tests in test_hg_audio_original.py are skipped.
python3 -B -m unittest discover -s tests -p 'test_hg_audio_*.py' -v
# retail-only M7 endpoint checks: the ISO path must be given explicitly (no
# default path). A missing, unreadable or non-matching image fails; the full
# SHA-256 is computed once per invocation and reused while the file is unchanged.
HG_JAPAN_ISO=/path/to/authenticated.iso \
    python3 -B -m unittest discover -s tests -p 'test_hg_audio_original.py' -v
# original-bytes differential (HG_ELF, default local/extracted/japan/SCPS_150.46)
PYTHONPATH=tools:tools/hg_audio python3 tools/hg_audio/verify_audio.py \
    --cases 20 --output local/audio/differential.json
# connected runner (needs the authenticated ISO: --iso or HG_JAPAN_ISO)
PYTHONPATH=tools:tools/hg_audio python3 tools/hg_audio/audio_runner.py \
    --iso "$HG_JAPAN_ISO" \
    --member 6b51 --out local/audio/out.wav --receipt local/audio/runner-receipt.json
```

## Limitations

- IOP module code (LIBSD/SDRDRV/SG2IOP) not disassembled; the service is
  a documented host model. No claim of complete IOP or SPU2 emulation.
- Playback timing, mixing, effects and hardware voice behavior are not
  reconstructed or verified.
- Command **semantics** (what each queued byte means to the IOP service)
  are only established where the original EE code itself consumes or
  tests them (allocation, busy checks, stop release). Byte meanings on
  the IOP side of the ring are an open boundary.
- Sample-rate and channel parameters come from the runner's table
  specification; the original table layout beyond the play path is not
  fully decoded.
- Only the two M7-verified bank families are supported; other members
  remain unclassified until evidence exists.

## Resume-here (for future sessions)

- Worktree `decomp-audio`, branch `research/audio-engine`, HEAD `8ece614`
  (all audio work committed locally; nothing pushed).
- Last passing connected operation: full runner end-to-end for member
  6b51 (registration → 96,000-sample 48 kHz WAV → stop/release, state
  slot released). Differential: 407 passed / 0 failed / 0 skipped /
  13 excluded. Regression: 43/43.
- Required private inputs: authenticated ISO (8ca1513c…) or the two
  member binaries under `local/audio/`; set `HG_JAPAN_ISO` (required for
  the retail tests and the runner; there is no default ISO path) and
  `HG_ELF` or populate its repo-relative default path.
- Next action: label the observed command-ring bytes (ff 41 f7 and
  play commands) against an authenticated IOP capture, or disassemble
  `LIBSD.IRX`/`SG2IOP.IRX` service dispatch to extend the host model
  with original-backed IOP behavior.
