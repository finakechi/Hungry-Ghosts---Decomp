# M21–M22 local integration review

M21 (scene coordination) and M22 (SG2IOP audio service) are locally integrated
into one verified development baseline on branch `integration/m21-m22-review`
in worktree `decomp-m21-m22-integration`. Both milestones coexist in one strict
C11 library and unified test suite with their ancestry and evidence preserved,
one lifecycle safety defect repaired, and the scene-to-audio interface boundary
established by a repeatable synthetic integration experiment.

## Git and workspace identities

| Item | Verified revision |
| --- | --- |
| Local `main` (untouched) | `4d016e9f898c7c0fd857eeb6c34cfcc5d7686b68` |
| `origin/main` (untouched) | `86a5670852d47c4ead140e5c8e40a5289f773053` |
| Common integration baseline (`integration/m19-m20-review`) | `b855eb93fa797eded31c1849f2bb69d68470609a` |
| M21 (`research/m21-scene-coordination`) | `167a7281a980538d19cceddc4cb2228362e1ef18` |
| M21 merge commit | `33353a0f419663b5331792a71d5e4b2810d60a1f` |
| M22 (`research/m22-audio-service`) | `54f1f130c17bb000d8f3c8bbca91d139460637f8` |
| M22 merge commit | `e26b7baded5bc9eebf287b1618799eaeda908159` |
| Verified integrated source commit | `5e7f10776492e36cacd463d507ba9089f0ccab0a` |

Only `START_HERE.md` and `MASTER_HANDOFF.md` conflicted during the merge of M21
and M22; all C sources, headers, tools, tests, and evidence merged cleanly.
Original worktrees (`decomp`, `decomp-m19-m20-integration`, `decomp-m21`,
`decomp-m22`) remain at their exact starting commits with clean tracked trees.
No push, publish, PR, or merge into `main` was performed.

Reference build: Japanese PS2 SCPS-15046, PCSX2 CRC `91AA4B28`, ISO SHA-256
`8ca1513c8417c0a40b838ff5edf22a2360f43bce1b0458693a95a6efdafd994e`, boot ELF
SHA-256 `a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61`.

## Review of M21 and M22

- **M21 (scene coordination):** Reviewed `src/scene/title_scene.{c,h}`,
  `src/hgscript/title.{c,h}`, `tools/ee_subset.py` (`lwu` zero-extension of low
  64-bit lane), `tools/front_end_differential.py`, `tools/verify_title_scene.py`,
  `tools/verify_scene_area_entry.py`, and `tests/title_scene.c`. All six
  committed M21/M22 retail receipts were freshly regenerated on `5e7f1077` and
  matched the committed evidence JSON files exactly:
  - Cold boot through AREA1 coordinator in 1,174 post-bootstrap units with all
    8 selected domains matching fresh PCSX2 (`docs/m21/title-to-area1.json`).
  - Title menu + 3 negative controls (`evidence/m21/title-menu.json`, 198
    frames) and neutral dialogue completion (`evidence/m21/title-neutral.json`,
    3,991 frames, 10,801 glyph requests).
  - Independent AREA1 request-to-coordinator comparison
    (`evidence/m21/area1-entry.json`, 183 units, 7 domains) and separate ferry
    reload comparisons (`docs/m21/boat-reload.json`, 7 domains on both pairs).
  - Preserved limitation: cold gameplay spawn, actor initialization, and camera
    initialization after first AREA1 coordinator entry remain incomplete.
- **M22 (audio service):** Reviewed `src/audio/audio_iop.{c,h}`,
  `src/audio/audio_engine.{c,h}`, `tools/hg_audio/connected.py`,
  `tools/hg_audio/verify_m22.py`, and `tests/test_hg_audio_connected.py`. Fresh
  rerun on `5e7f1077` matched `evidence/m22/retail-check.json` exactly:
  - 4 SShd uploads across 3 PACK members (`6b51`, `8594`, `6d90`) totaling
    804,336 bytes matched saved SPU2 RAM byte-for-byte with 4/4 staging
    allocations released.
  - 201 ordered SPU2 register writes across fresh cursor down (38), cursor up
    (57), and confirmation (106) matched original SG2IOP + LIBSD instructions,
    and the host model rendered 16,000 stereo PCM frames.
  - Preserved limitations: automatic EE note sequencing, autonomous physical
    voice allocation, streaming voice playback, full original IOP behavior, and
    retail PCM equivalence remain unresolved.
- **Guest memory map compatibility:** Audited every fixed guest address across
  `src/front_end.c`, `src/game_lifecycle.c`, `src/resource_loader.c`,
  `src/hgscript/`, `src/scene/title_scene.c`, and `src/audio/`. No guest RAM
  range overlaps exist; both subsystems operate cleanly on one shared 32 MiB EE
  RAM + 16 KiB scratchpad address space.
- **Integration defect fixed (`src/audio/audio_iop.c`):** Unlike
  `hg_audio_iop_*` (which guarded `!iop`), the EE bridge entry points
  (`hg_audio_ee_service_init`, `hg_audio_ee_post`, `hg_audio_ee_flush`,
  `hg_audio_ee_bank_call`, `hg_audio_ee_sample_address`,
  `hg_audio_ee_stage_voice`, `hg_audio_ee_stage_play_voice`,
  `hg_audio_ee_stage_key`) dereferenced `service`, `service->bus`, or
  `service->iop` without NULL checks. A zero-initialized (unbound) or torn-down
  `HGAudioEeService` crashed instead of failing closed. Added explicit NULL
  guards so all unbound or post-teardown calls fail closed (`false` /
  `UINT32_MAX`), verified by negative controls under GCC and Clang ASan/UBSan.

## Scene-to-audio interface verification

### Confirmed signatures and prerequisites

Verified directly from `src/audio/audio_engine.h`, `src/audio/audio_iop.h`,
`src/resource_loader.h`, `src/front_end.h`, and `src/scene/title_scene.h`:

- `HGAudioIop *hg_audio_iop_create(HGSpu2Io io);` /
  `void hg_audio_iop_destroy(HGAudioIop *iop);` — requires `io.read_iop` for
  bank uploads (`HG_SG2_UPLOAD`); `write16`, `voice_transfer`, and `read16` are
  optional observers/overrides.
- `void hg_audio_ee_service_init(HGAudioEeService *service, HGAudioBus *bus, HGAudioIop *iop);` —
  binds the shared EE guest bus and IOP instance and zeros `HG_SG2_EE_CURSOR`
  (`0x00a80180`). Caller owns EE/IOP guest memory, `HG_AUDIO_CTX_SAVE`
  (`0x003e0720`) initial SPU2 destinations, free channel markers (`-1` at
  `0x00a6c4d8 + ch*0x9e4`), and serialized stepping.
- `uint32_t hg_audio_register_entry(HGAudioBus *bus, uint32_t resource);` —
  reconstructed `00156a50`; invoked directly by `hg_pack_special_step`
  (`src/resource_loader.c:244`). Requires bus routing for SIF alloc/DMA/free
  (`0010af40`, `00104320`, `00104300`, `0010b0a8`) and SG2 bank hooks
  (`002a7560`, `002a7590`, `002b5428`) plus `hg_audio_ee_flush` /
  `hg_audio_iop_step_dma` stepping until it returns the non-zero endpoint.
- `uint32_t hg_audio_ee_bank_call(HGAudioEeService *service, uint32_t target, uint32_t arg1, uint32_t arg2, uint32_t arg3);` —
  handles `HG_SG2_UPLOAD_START` (`002a7560`), `HG_SG2_UPLOAD_POLL` (`002a7590`),
  and `HG_SG2_BANK_BIND` (`002b5428`). Returns `UINT32_MAX` on rejection, `1`
  while pending, `0` on completion.
- `uint32_t hg_audio_play(HGAudioBus *bus, uint32_t slot, uint32_t param1, uint32_t param2, uint32_t param3, uint32_t t0);` —
  reconstructed `002ad478`; validates active slot, resolves cue entry via
  `hg_audio_table_lookup4(slot, param1, param2)`, allocates an EE command slot
  (`00a82640 + idx*0x50`, `HG_CMD_STATE = 1`) and EE channel, and returns
  `play_index` (`0..31`) or negative error code (`-1..-4`).
- `bool hg_audio_ee_stage_play_voice(HGAudioEeService *service, uint32_t play_index, uint32_t voice, uint32_t sample_offset, HGSg2VoiceParams params);` —
  requires active play command (`HG_CMD_STATE == 1`), active bound bank slot
  (`HG_SLOT_AUXC != 0`), and uploaded 16-byte-aligned SPU2 span; stages
  `ADSR`, `START_ADDRESS`, `GAIN`, and `PITCH` packets into the EE ring.
- `void hg_audio_iop_render(HGAudioIop *iop, int16_t *stereo, size_t frames);` —
  advances the host SPU2 ADPCM model at 48 kHz stereo.

### Integration approach comparison

| Candidate approach | Functional value (40%) | Feasibility (30%) | Integration benefit (20%) | Low regression risk (10%) | Weighted |
| --- | ---: | ---: | ---: | ---: | ---: |
| **A (Selected): Shared-RAM C harness exercising real `hg_pack_special_step`, `hg_title_menu`, and `hg_title_scene_call`/`hg_script_timed_*` against M22's documented interface with explicit boundary separation and negative controls** | 5 | 5 | 5 | 5 | **5.0** |
| B: Reconstruct `00156ad0`/`00157ed0` in production `src/` and wire `front_end.c` directly into `HGAudioEeService` | 4 | 3 | 4 | 2 | 3.5 |
| C: Shim HGScript's `001564a0` dialogue voice call into `hg_audio_play` via a synthetic fake bank | 1 | 4 | 1 | 3 | 2.1 |

Option A was selected because it tests every real existing C producer on one
shared guest bus without inventing unreconstructed EE logic or misrepresenting
streaming voice (`001564a0`) as an SShd sample-bank request.

### Observed boundary across existing scene producers

Implemented in `tests/scene_audio_interface.c` and discovered by
`tests/test_scene_audio_interface.py` using 100% synthetic resources:

1. **Loader bank registration (`src/resource_loader.c:244` -> `00156a50`):**
   **Fully connected end-to-end.** `hg_pack_special_step` (backed by real
   `disc_read.c` `HGReadState`/`HGReadEnv`) emits `00156a50(0x01000000)`,
   routed directly to `hg_audio_register_entry` + `hg_audio_ee_bank_call` +
   `hg_audio_ee_flush` + `hg_audio_iop_step_dma`. M22 uploads the 32-byte
   synthetic ADPCM payload to SPU2 `0x5010`, binds `HG_SLOT_AUXC`, publishes
   slot `0` at `HG_AUDIO_ID_TABLE` (`0x003f2740`), frees the IOP staging
   allocation (`allocs == frees == 1`), and returns the exact endpoint
   `0x01000140` to `hg_pack_special_step`.
2. **Title menu SFX (`src/front_end.c:570` -> `00156ad0` -> `hg_audio_play` -> M22 adapter):**
   - **Scene-produced values:** `hg_title_menu` (with D-pad Down edge `0x4000`
     and `VOLUME = 1.0f`) calls `hg_title_scene_call(00141470)`, advances cursor
     `0 -> 1`, and emits `HG_CALL_T_REGS(t0=1)` followed by
     `00156ad0(a0=4, a1=0x1000, a2=0x7f, a3=0x40)`.
   - **Exact ELF gap #1 (`00156ad0`/`00157ed0` glue):** Confirmed against the
     authenticated SCPS-15046 ELF (`00156ad0..00156ca8`, `00157ed0..00157f14`):
     sound ID `4` maps via table `00388200`/`00387610` to descriptor
     `[bank_group=0, bank_idx=0, param1=1, param2=3, t0=0, flag5=0]`, loads
     `slot = lw(003f2740 + group*0x78 + idx*4)`, and calls `jal 002ad478`
     (`hg_audio_play`). Supplying this documented descriptor delivers the scene
     request to `hg_audio_play(&bus, slot, 1, 3, 0, 0)`, which allocates
     `play_index = 0` (`HG_CMD_STATE = 1`, `HG_CMD_SLOT = 0`, resolved
     `HG_CMD_SAMPLE != 0`, channel `< 32`).
   - **Last automatic boundary / Exact missing dependency #2 (EE sequencer &
     physical voice allocator):** Flushing `HGAudioEeService` immediately after
     `hg_audio_play` transmits 0 packets, emits 0 SPU2 writes, reports
     `reply.envelope[*] == 0`, and renders 32 all-zero stereo frames. The
     unreconstructed EE sequence consumer (`002ade94..002ae2c0`, `002a96b8`,
     `002ab508`) and physical voice allocator are required to translate an
     active EE play command into SG2IOP voice packets.
   - **Externally supplied resolved-note inputs:** Passing explicit external
     `(voice=6, sample_offset=0, HGSg2VoiceParams{note=60, root=60, scale=0x116b, left=0x2000, right=0x2000, adsr1=0x8080, adsr2=0x808a, fine=0, bend=64, bend_scale=1})`
     to `hg_audio_ee_stage_play_voice` + `hg_audio_ee_stage_key(6, true)` +
     `hg_audio_ee_flush` executes SG2IOP RPC `11223344`, emits 11 ordered SPU2
     register writes (`ADSR`, `SSA 0x5010 -> word 0x2808`, `GAIN`, `PITCH`,
     `KEY_ON 1<<6`), reports readiness `reply.envelope[6] == 0x7fff`, and
     renders 64 deterministic non-zero stereo frames `(3, 3)` from the host
     SPU2 ADPCM model.
   - **Stop/reset & cleanup:** `hg_audio_cancel(&bus, 0)` marks `HG_CMD_STATE = 2`,
     clears `HG_CMD_SLOT = -1`, appends `ff 41 f7` to the EE channel queue, and
     causes `hg_audio_ee_stage_play_voice` to reject `play_index = 0`, while
     confirming that `hg_audio_cancel` alone does not emit SG2IOP `KEY_OFF`
     (missing EE channel ring consumer). Explicit `hg_audio_ee_stage_key(6, false)`
     + `hg_audio_ee_flush` emits `KEY_OFF`, drops `reply.envelope[6]` to `0`,
     and silences `hg_audio_iop_render`. `hg_audio_iop_destroy` plus clearing
     `service.iop = NULL` causes subsequent EE service calls to fail closed.
3. **Title scene / HGScript dialogue voice (`src/scene/title_scene.c` -> `src/hgscript/timed.c:390,60,181` -> `001564a0`/`00156550`):**
   - **Scene-produced values:** `hg_title_scene_call(00a88b40)` +
     `hg_title_scene_call(0025c170)` emits `001564a0(1, 0x240, 1, 0, 1.0f, 0.02f)`,
     waits on guest byte `VOICE_STATE` (`003f2fa0 == 3`) before drawing glyphs,
     and emits `00156550(1, 0, 0, 0)` on fade-gated release.
   - **Exact missing dependency #3 (Streaming voice subsystem):** Confirmed from
     authenticated SCPS-15046 ELF (`001564a0..00156660`), `001564a0` and
     `00156550` write to the separate streaming-voice request buffer at
     `gp+0x2ab0` consumed by `00156570 -> 001561f0 / 002a8850 / 002a8f18`
     (outside M22's SShd sample-bank scope). The experiment verifies that
     `001564a0` leaves M22's SShd command queue and SG2IOP ring untouched and
     does not fake success readiness.

### Targeted negative controls verified

1. **Disconnected bank-call routing:** Bypassing `hg_audio_ee_bank_call` leaves
   `HG_SLOT_AUXC == 0` and SPU2 un-uploaded -> `hg_audio_ee_stage_play_voice`
   returns `false`, 0 SPU2 writes, silent PCM.
2. **Missing DMA step:** Polling before `hg_audio_iop_step_dma` keeps
   `HG_SG2_UPLOAD_POLL == 1`, parks `hg_pack_special_step`, and causes
   `hg_audio_ee_stage_play_voice` to return `false`.
3. **Cleared bank slot:** `hg_audio_slot_clear` invalidates active play staging
   (`hg_audio_ee_stage_play_voice` returns `false`).
4. **Unbound / torn-down `HGAudioEeService`:** Zero-initialized or post-destroy
   service fails closed (`false` / `UINT32_MAX`) across all EE bridge APIs.
5. **Pre-flush render & unready HGScript voice & revoked title handle:**
   Rendering before `hg_audio_ee_flush` yields silence; `VOICE_STATE != 3`
   blocks HGScript glyph draws; clearing handle `0x22f` suppresses stale
   `00156550` voice stop on release.

## Verification results on `5e7f10776492e36cacd463d507ba9089f0ccab0a`

Host environment: CachyOS x86-64, GCC 16.2.1, Clang 23.1.5, Python 3.14.10,
javac 21.0.13.1. Generated artifacts remain in ignored `local/m21-m22-review/`
and `local/verification/`.

| Check | Status | Details (fresh on `5e7f1077`, distinguished from historical) |
| --- | --- | --- |
| Combined strict GCC & Clang shared library (`-std=c11 -O2 -Wall -Wextra -Werror -pedantic -ffp-contract=off -Wl,--no-undefined` across all 10 `src/**/*.c`, `-lm`) | **PASS** | 0 errors, 0 warnings, 249 exported symbols, 0 duplicate globals. |
| GCC & Clang ASan+UBSan+LSan (`tests/scene_audio_interface.c`, `tests/title_scene.c`, `tests/hgscript_title.c`, `tests/hgscript_runtime.c`, `tests/resource_sanitizer.c`) | **PASS** | 10/10 runs clean with `detect_leaks=1:halt_on_error=1`. |
| M22 Clang ASan+UBSan (`tests/test_hg_audio_connected.py`) | **PASS** | 6/6 tests clean (`detect_leaks=0` for CPython host). |
| `HG_JAPAN_ISO=... python3 -B -m unittest discover -s tests -v` | **PASS** | **470/470** passed, 0 failed, 0 skipped (199.912s). |
| `python3 -B -m unittest discover -s tests -v` (inside `hg_verify.py` gate 00, with `HG_JAPAN_ISO` configured by the runner) | **PASS** | **470/470** passed, 0 failed, 0 skipped (226.587s); without `HG_JAPAN_ISO`, the 3 retail-only Audio tests skip cleanly. |
| M21 owner-local retail verifiers (`verify_title_scene.py` menu+controls, neutral, cold-to-AREA1; `verify_scene_area_entry.py`; ferry reload pairs) | **PASS** | All 5 regenerated receipts matched committed M21 receipts (`True`). |
| M22 owner-local retail verifiers (`verify_m22.py`, `verify_audio.py --cases 20`, `audio_runner.py --member 6b51`) | **PASS** | `retail-check.json` matched committed M22 receipt (`True`); EE differential 407 passed / 0 failed / 0 skipped / 13 excluded; legacy runner accepted-connected-run (96,000 samples). |
| M17–M20 connected owner-local regressions (`run_front_end_native.py`, `run_resource_lifecycle.py`, `verify_title_hgscript.py`, `verify_player_continuous.py --lifecycle`) | **PASS** | M18 16,273/16,273 frames (0 faults); M19 11/11 requests, 3/3 full checkpoints; M19–M20 3,861 frames, 10,801 glyphs; M17 22/22 corridor frames (0 failures). |
| Full historical owner profile (`tools/hg_verify.py run --profile full`, run `m12-20261126T170926Z-prt6dzmf`) | **PASS (79/79)** | **74 passed, 5 expected rejections, 0 failures, 0 skips, 0 unrun.** Initial sandboxed session accepted 76/79 (3 M4–M7 gates reported `missing-input` because `local/references/pcsx2.AppImage` symlinks outside the workspace). Resumed outside the sandbox on the exact same clean commit `5e7f1077`: all 79/79 gates accepted (63 reused from the earlier unsandboxed session on `5e7f1077`, 16 executed in the final resumed session). Receipt `local/verification/m12-20261126T170926Z-prt6dzmf/summary.json` SHA-256 `3c8c55c000c7ab09e29fa471f509060e64778f34854cb33b4eb8fbc4015f59c4`, `source_sha256` `4be3bce2bb1382ec3766bbc4330fea53747e70ef11bc0c30cedb748216ee0f0a`, `working_files` empty. |

Historical receipts preserved unchanged: M21 full profile
`m12-20261124T215334Z-f57j4vwj` (at `8e04bd9e`, 79/79) and M22 full profile
`m12-20261124T235355Z-yg8tjftd` (at `a4ee75ab`, 79/79).

## Reproduction commands

From `decomp-m21-m22-integration`:

```bash
iso='../Hungry Ghosts (Japan).iso'
elf='local/extracted/japan/SCPS_150.46'
out='local/m21-m22-review'
mkdir -p "$out"

# Strict combined builds and scene-to-audio ASan/UBSan/LSan
for c in gcc clang; do
  "$c" -std=c11 -O2 -Wall -Wextra -Werror -pedantic -ffp-contract=off -fPIC -shared \
    -Wl,--no-undefined src/disc_read.c src/resource_loader.c src/game_lifecycle.c \
    src/front_end.c src/hgscript/hgscript.c src/hgscript/timed.c src/hgscript/title.c \
    src/scene/title_scene.c src/audio/audio_engine.c src/audio/audio_iop.c -lm \
    -o "$out/combined-$c.so"
  "$c" -std=c11 -g -O1 -Wall -Wextra -Werror -pedantic -ffp-contract=off \
    -fno-omit-frame-pointer -fsanitize=address,undefined -fno-sanitize-recover=all \
    -I src tests/scene_audio_interface.c src/scene/title_scene.c src/hgscript/title.c \
    src/hgscript/hgscript.c src/hgscript/timed.c src/front_end.c src/game_lifecycle.c \
    src/resource_loader.c src/disc_read.c src/audio/audio_engine.c src/audio/audio_iop.c \
    -lm -o "$out/scene-audio-$c-san"
  ASAN_OPTIONS=detect_leaks=1:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 \
    "$out/scene-audio-$c-san"
done

# Synthetic + retail-enabled unittest discovery
python3 -B -m unittest discover -s tests -p test_scene_audio_interface.py -v
HG_JAPAN_ISO="$PWD/$iso" python3 -B -m unittest discover -s tests -v

# Owner-local M21, M22, and 79-gate full profile
python3 -B tools/verify_title_scene.py --iso "$iso" --library "$out/m21-title.so" \
  --output "$out/m21-title-menu.json" --live ../decomp-m21/local/m21 --skip 100 \
  --advance-interval 32 --negative-controls
python3 -B tools/verify_title_scene.py --iso "$iso" --library "$out/m21-neutral.so" \
  --output "$out/m21-title-neutral.json" --live ../decomp-m21/local/m21 --until-reset
python3 -B tools/verify_title_scene.py --iso "$iso" --library "$out/m21-area.so" \
  --output "$out/m21-title-area1.json" --live ../decomp-m21/local/m21 --skip 100 \
  --advance-interval 32 --new-game --area-native ../decomp-m21/local/m21/native
python3 -B tools/verify_scene_area_entry.py --iso "$iso" --native ../decomp-m21/local/m21/native \
  --library "$out/m21-area-entry.so" --output "$out/m21-area1-entry.json"
python3 -B tools/hg_audio/verify_m22.py --iso "$iso" --retail ../decomp-m22/local/m22/retail \
  --state "../decomp-m18/local/m18/runtime/PCSX2/sstates/SCPS-15046 (91AA4B28).01.p2s" \
  --native ../decomp-m22/local/m22/live --library "$out/m22-retail.so" \
  --output "$out/m22-retail-check.json" --wav "$out/m22-cursor-model.wav"
python3 -B tools/hg_verify.py run --profile full
```

## Recommended next two independent milestones

1. **M23 — Cold AREA1 world spawn and first controller-driven frame (scene/gameplay track):**
   Carry cold AREA1 entry (`0012f850`) through world/actor/camera
   initialization (`00158560` state-0 dispatch, spawn `(48.5, 38.0, 265.0)` at
   `003f9300`, camera block, and actor lists) into the first controller-driven
   gameplay frame on the shared guest bus, validated against fresh M21
   coordinator-entry captures. Independent of audio sequencing.
2. **M24 — EE audio note sequencer, voice allocator, and `00156ad0` bridge (audio track):**
   Reconstruct the EE sound-request dispatcher (`00156ad0`/`00157ed0`),
   sequence consumer (`002ade94..002ae2c0`, `002a96b8`, `002ab508`), and
   physical voice allocator so `hg_title_menu` cursor/confirm SFX automatically
   compute and emit the SG2IOP packets verified against M22's fresh cursor
   checkpoints (removing the external resolved-note seam). Independent of AREA1
   world initialization.
