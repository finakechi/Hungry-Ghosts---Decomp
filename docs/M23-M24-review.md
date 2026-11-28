# M23–M24 integration review — M19–M24 on the verification runner

This review integrates the preserved M19–M24 histories onto `main` after
PR #22 (the Phase 1–3 verification infrastructure), registers explicit
verification for every milestone, and adds one connected original synthetic
world/audio lifecycle. It does not start M25/M26, choose a final project
objective, or claim retail equivalence.

## Git and workspace identities

| Item | Value |
| --- | --- |
| Branch / worktree | `integration/m23-m24-review` in `decomp-m23-m24-integration` |
| Base (remote `main`, PR #22 merge) | `09b5832119d5ec97c85c05c6ab1eddd6c20be8ad` |
| M23 tip merged | `research/m23-gameplay-initialization` `6cc3ce8d71fc98b070349ce9dc822886fce1186f` |
| M24 tip merged | `research/m24-audio-sequencing` `1830f5ecbf94230da42049e5b300171f47154785` |
| Inherited | `integration/m21-m22-review` `705cd20f…` ⊃ `integration/m19-m20-review` `b855eb93…` (both ancestors of M23 and of M24; neither was in `main`) |
| Merge commits | `71cd01d` (M23 with M19–M22 history), `216b126` (M24) |
| Implementation commits / tested commit | `ade2ece`, `77a8dee` / `77a8dee39813a8ec8e8ade6952279f4891559f00` |
| Newly reachable commits | 26 (normal merges; no rewritten or duplicated history) |
| Reference build | SCPS-15046, ELF `a74248c5…3c61`, ISO `8ca1513c…994e`, PS2DInfo `2e4a226e…`, PCSX2 v2.8.2 `0c46bb6a…` |
| Host | CachyOS x86-64, GCC 16.2.1, Clang 23.1.5, Python 3.14.10 |

Code merged without conflicts. Only `DEVELOPMENT.md`, `MASTER_HANDOFF.md`,
`README.md` and `START_HERE.md` conflicted. `START_HERE.md` keeps the
condensed M21+ layout; `main`'s dated Phase 1–3 sections moved verbatim to
the top of `START_HERE_HISTORY.md` (the older history `main` still carried
was already there verbatim). Five files combine both sides through clean
auto-merges, all additive: `tools/ee_subset.py` (M21 `lwu` beside the Phase 2
exact rational FP), `tests/test_ee_subset.py`, `tools/front_end_differential.py`,
`tools/run_front_end_native.py` and `tools/hg_audio/audio_guest.py`. PR #22's
Gate 01/19/56 and corridor contract fixes, exact FP and zero-copy compare,
thread-safe input resolution, attempt isolation, scheduling and receipt
sealing/invalidation are unchanged.

## Migration fixes

1. **M19 build path.** `tests/test_resource_execution.py` now routes
   `local/build/m19-resource.so` through `build_path()` explicitly (the
   `resource_harness.library()` helper already redirected `local/` outputs
   under the runner; the Phase 3 leak came from direct runs). Verified: with
   `HG_VERIFY_BUILD_DIR` set the library lands in the attempt directory.
2. **Writable-output audit.** Every new verifier writes only beneath its
   `--output`, `--library` or `--scratch` argument (runner artifacts) or
   `HG_VERIFY_BUILD_DIR`. Retail gates remain exclusive. Only four synthetic
   C gates (`m23-world-init-synthetic`, `m24-sequencer-synthetic`,
   `m20-m22-c-sanitizers`, `world-audio-lifecycle`) are parallel: all four
   ran concurrently with one attempt build directory and wrote nothing else
   in the worktree.
3. **Named `HG_ELF` discovery skip.** Gate 00 previously exported `HG_ELF`
   unconditionally, so the M24 retail test ran against whatever path was
   configured (in a public run, a nonexistent one) without the runner
   authenticating it. Now Gate 00 binds `{elf}` when present (size and
   SHA-256 from `evidence/local-inputs.json`), discovery gates export
   `HG_ELF` only after that authentication, and `RETAIL_ELF_TESTS` names the
   single allowed skip, valid only when the ELF is unbound. An authenticated
   ELF makes the test mandatory; full discovery must still list every named
   retail test; a wrong ELF blocks the gate before execution. Other
   verifiers keep their historical environment. The retail check also has
   its own authenticated gate, `m24-reference-map`.
4. **Ferry reload.** The M21 ferry reload existed only as an owner-local
   probe. It is now `tools/verify_scene_ferry_reload.py` with unchanged logic
   (named options; explicit checks instead of `assert`). The resume-pair
   receipt `evidence/m21/ferry-resume.json` is byte-identical to the private
   M21 receipt apart from the added schema/identity fields.

## Gate registration and coverage

`config/current-verification.json` adds 18 gates; contracts live in
`tools/hg_verify_milestones.py`. Each contract compares measured fields
with tracked evidence (exact equality where the milestone produced exact
receipts), re-derives summary values from raw reports, and requires every
declared control to be detected. A machine-readable map is in
[verification/m23-m24-coverage.json](verification/m23-m24-coverage.json).

| Scope | Gate | Evidence | Acceptance (abridged) |
| --- | --- | --- | --- |
| M19 loading/publication/worker | `m19-resource-job` | differential | equals `evidence/m19/results.json` differential; every control rejected |
| M19 | `m19-resource-retail` | saved-runtime | cold title and 5 M4 + 12 M7 checkpoint domains equal evidence |
| M19 lifecycle | `m19-connected-title` | saved-runtime | 16,273 units, 11 requests, 57 setups, 164 transfers re-derived; 3 M18 checkpoints, 0 differing bytes |
| M20 timed dialogue/paged text | `m20-hgscript-differential` | differential | equals evidence; 4 controls diverge at the recorded tick |
| M20 connected execution | `m20-title-hgscript` | saved-runtime | equals `evidence/m19-m20-integration/title.json` (3,861 frames, 10,801 glyphs) |
| M21 scene coordination | `m21-title-menu`, `m21-title-neutral` | saved-runtime | equal `evidence/m21/title-*.json`; menu controls diverge |
| M21 cold entry | `m21-cold-new-game`, `m21-area1-entry` | saved-runtime | 1,174 units to AREA1 (`docs/m21/title-to-area1.json`); 183 units, 7 domains |
| M21 ferry reload | `m21-ferry-reload`, `m21-ferry-resume` | saved-runtime | 18 units/3,825 vblanks and 34 units/49 vblanks; 7/7 domains |
| M22 service/uploads/ordered output | `m22-retail-service` | saved-runtime + original bytes | equals `evidence/m22/retail-check.json`; 3 instruction controls |
| M22 | existing `audio-instructions`, `audio-connected` | — | reused, not duplicated |
| M20–M22 C | `m20-m22-c-sanitizers` | synthetic | 4 drivers clean under ASan+UBSan+LSan; overflow canary detected |
| M23 authentic boundary | `m23-authentic-boundary` | original bytes + saved-runtime | equals `evidence/m23/authentic-boundary.json` |
| M23 synthetic lifecycle | `m23-world-init-synthetic` | synthetic | equals `evidence/m23/synthetic-lifecycle.json` (sanitized); 10 controls |
| M24 reference map | `m24-reference-map` (+ Gate 00 named skip) | original bytes | equals `evidence/m24/reference-map.json` |
| M24 sequencer/voices | `m24-sequencer-synthetic` | synthetic | byte-identical `evidence/m24/sequencer.json`; 9 scenarios, 5 controls |
| Integration | `world-audio-lifecycle` | synthetic | independent re-check (below); byte-identical receipt |

Reused rather than duplicated: Gate 00 runs every M19–M24 Python test;
Gates 24/25 cover `tests/resource_sanitizer.c` including M19's additions;
`m16-corridor`/`lifecycle-corridor` (M17 corridor), `connected-native-lifecycle`
(M18 16,273 frames) and the existing Audio gates.

| Profile | Before | After |
| --- | ---: | ---: |
| `focused` | 8 | 8 (unchanged) |
| `historical` / `full` | 79 | 79 (IDs and contracts unchanged) |
| `current` | 120 | **138** (+14 authenticated, +4 synthetic) |
| `synthetic` | 37 | **41** (+4) |

## Connected original synthetic world/audio lifecycle

`tests/world_audio_lifecycle.c` (driver) and `tools/verify_world_audio.py`
(verifier). No new engine, sequencer or resource manager.

**Architecture.** One process, one guest EE RAM: the M23 harness owns the
32 MiB EE RAM, and a new additive accessor, `hg_world_harness_guest_ram()`,
lets the M22/M24 audio bus read and write that same memory. The world side is
unchanged M23 code (`world_init.c` with `front_end.c`, `game_lifecycle.c`,
`resource_loader.c`, `disc_read.c`). The audio side is the existing
`hg_audio_register_entry`/`hg_audio_play`/`hg_audio_cancel`/`hg_audio_slot_clear`,
`hg_audio_ee_flush`, `hg_audio_iop_step_dma`, `hg_audio_iop_render` and the
M24 `HGSequencer`.

**Service order per world frame** (external harness schedule: 1 sequencer
tick and 800 rendered frames at 48 kHz per 60 Hz world frame; this is not
evidence of retail EE thread timing or service order):
world step → audio-memory integrity check → derive synthetic events →
`hg_audio_play` → `hg_seq_update(1)` → `hg_audio_ee_flush` →
`hg_audio_iop_step_dma` when busy → independent checker → render.

**Cycle.** Cold boot; M23 resource publication and world entry (actor pools,
player, camera); IOP model creation, slot initialization and registration of
an original synthetic bank (SIF allocation, DMA, the three bank calls, flush
and DMA steps); sequencer init (cycle 1) or rebind (cycle 2). Then the M23
controller script: fade-in with a mid-fade forward probe, 4 neutral, 14
forward, 12 turn+walk, 20 wall-constrained, 14 release, then 16 drain
frames. Teardown cancels the ambient request, drains to natural completion,
resets the sequencer, releases the bank slot, destroys the IOP model and
tears down the world. A second cycle repeats everything.

**Synthetic events** (explicit original rule over carried world snapshots,
not a retail callback): AMBIENT at world entry (two held loops until
cancelled), CHIME when the fade completes (two overlapping timed notes),
STEP after every 1.0 unit of planar travel, BUMP when the per-cycle
wall-contact count rises (8-frame refractory). The M24 model makes every
note, timing and voice decision from the HSQ1 cues; the harness never
supplies resolved notes or voice numbers. The pool is 3 voices, so the
chime/step overlap causes a priority steal.

**Measured per cycle** (both cycles identical): 143 world frames and audio
updates; 17 requests (1 ambient, 1 chime, 11 steps, 4 bumps), all adopted;
110 decisions; 19 key-ons and 19 key-offs; 1 steal; 16 natural completions
and 1 cancellation; 0 lost, refused or rejected; 115 ring records and 287
SPU2 writes; 101,600 non-zero PCM frames, 56 audible updates while the
player moves; 1 staging allocation and 1 free.

**Integrity and cleanup checks.**
- Standalone parity: a world-only run in the same process follows the same
  script; every frame's position, rotation, camera eye/target, speed, actor
  and node counts and wall contacts are bit-identical, and a 64-bit digest of
  all world-owned EE (everything outside the 10 declared audio ranges) is
  equal at 8 checkpoints in both cycles. The standalone run never writes the
  audio ranges.
- M23 parity: connected positions equal `evidence/m23/synthetic-lifecycle.json`
  (walk `(12, 10, 24.094994)`, turn `(13.379306, 10, 28.010624)` at yaw
  0.7125, wall `(18.059095, 10, 28.059095)` with 26 contacts, release
  `(18.05908, 10, 28.05908)`, camera eye/target as recorded).
- Memory ownership: every audio-bus write falls inside the declared audio
  ranges (0 stray writes); world updates never change them.
- Checker: SPU2 key-on/key-off registers against decisions, start addresses
  against authored tones, no unowned sounding voice, owned looping voices
  sounding, ownership from decisions only, checker mask equal to the model's.
  The expected timeline comes from the harness's own tick schedule and the
  authored cue lengths (chime 12, step 4, bump 6 ticks), not from the model.
- End of each cycle: 0 active requests, owned voices, busy commands, open
  channels and sounding envelopes; bank slot unbound; allocations equal frees;
  0 actors, 2,048 free nodes, 0 dynamic handles, 0 unknown calls.
- Determinism: cycle 2 equals cycle 1 (cycle-relative decision digest, PCM,
  SPU2 stream, world trajectory) and a second full run equals the first.
- The M23 wall-contact diagnostic counts across cycles; the event rule and
  checkpoints use per-cycle deltas.

**Controls** (each completes and is detected by its intended check; the
full failure sets are in the receipt):

| Control | Fault | Intended check |
| --- | --- | --- |
| `nc-missing-world-init` | M23 state-0 player init omitted | world-init |
| `nc-missing-bank-registration` | bank never registered | audio-init |
| `nc-missing-sequence-ticks` | frames 80–82 advance 0 ticks | timeline |
| `nc-flush-before-sequence-update` | flush before the sequencer update | key-on |
| `nc-early-bank-release` | bank slot cleared at frame 100 while sounding | teardown-order |
| `nc-stale-audio-teardown` | no cancel/drain/reset before release | audio-end-state |
| `nc-stale-world-teardown` | M23 pool reset skipped | world-reentry |
| `nc-foreign-voice-release` | unowned key-off of voice 8 | key-off |
| `nc-audio-writes-world-memory` | audio-bus write into the player block | memory-ownership |
| `nc-world-writes-audio-memory` | world-side write into the channel table | audio-memory-integrity |

GCC, Clang and both ASan+UBSan+LSan builds produce byte-identical receipts
([evidence/m23-m24/world-audio-lifecycle.json](../evidence/m23-m24/world-audio-lifecycle.json)).

**Provenance of significant values.**

| Class | Values |
| --- | --- |
| Fixture (original synthetic) | M23 scene config and controller script; bank container, 5 tones, 4 HSQ1 cues and ADPCM blocks (`bank_fnv1a64` in the receipt) |
| Existing C | M23 world publication/entry/step/teardown state (C-produced per M23); M22 registration, play/cancel commands, slot clear, flush packets and DMA |
| Synthetic world model | M23 pools, actors, collision walls, camera model (the M23 ledger: 5 fixture, 18 C-produced, 20 synthetic, 0 unknown) |
| Sequencer (M24 model) | every adoption, timing, allocation, steal, release, completion and cancel decision |
| Host model (M22 IOP) | SPU2 register writes, ENVX replies, PCM |
| External harness | 1 tick and 800 samples per world frame, update order, immediate SIF DMA completion, global tick counter, the synthetic event rule |

## Results

Tested commit `77a8dee39813a8ec8e8ade6952279f4891559f00` (clean tree;
runner source digest `3b698e927c94f94122f97e6a0177e8c76b04ae07e5aa46aad26ffb588ea8183b`,
inventory `a09ae982…`, PS2DInfo `2e4a226e…`). The commit after it changes
only documentation (no file under the runner's fingerprinted source
directories). Sanitized record:
[verification/m23-m24-results.json](verification/m23-m24-results.json).

| Check | Run / command | Workers | Result | Duration |
| --- | --- | ---: | --- | ---: |
| Strict combined builds, GCC 16.2.1 and Clang 23.1.5 (`-Wall -Wextra -Werror -pedantic`, `--no-undefined`) | 12 sources | — | 280 exported functions, 0 duplicates, both compilers | — |
| Connected lifecycle, GCC/Clang, plain and ASan+UBSan+LSan | `tools/verify_world_audio.py` | — | 4 byte-identical receipts equal to the tracked evidence | — |
| Expanded `current` acceptance | `m12-20261128T000859Z-4aeadr8x` | 4 | **138/138 accepted** (133 passed, 5 expected rejections), 0 reused, 0 retries, 0 interventions; all 79 historical gates passed (`full_verdict` passed); 0 bad seals, 0 bad artifacts | 2,280.6 s session, 6,390.1 s child |
| Gate 00 inside that run (authenticated ISO and ELF) | `synthetic-suite` | — | 505/505 passed, 0 skipped, no named retail test left unrun | — |
| `synthetic` profile (public inputs only) | `m12-20261128T004904Z-tqj5dqx3` | 1 | **41/41 accepted** (39 passed, 2 expected rejections); Gate 00 501 passed + exactly the 4 named retail skips; 0 bad seals | 320.0 s |

The `current` summary (`summary.json`) has SHA-256
`310f44ea114461781b24c9ee80a0f6707e11c6348bc4c8510d930eec93fb0950`. The 5
expected rejections are the historical negative-control gates
(`ordinary-a1-zero-control`, `connected-player-negative-controls`,
`actor-setup-negative-controls`, `actor-lists-negative-controls`,
`player-transition-negative-controls`).

**Child seconds and measurements for the new gates** (run `m12-20261128T000859Z-4aeadr8x`):

| Gate | s | Measured |
| --- | ---: | --- |
| `m19-resource-job` | 14.0 | 36 jobs, 4 controls rejected |
| `m19-resource-retail` | 8.2 | 837 cold-title frames, 17 checkpoint domains |
| `m19-connected-title` | 29.2 | 16,273 units, 11 requests, 3 checkpoints |
| `m20-hgscript-differential` | 6.7 | 3 sequences, 4 controls |
| `m20-title-hgscript` | 16.2 | 3,861 frames, 10,801 glyph requests |
| `m21-title-menu` | 37.9 | 198 frames, 3 requests |
| `m21-title-neutral` | 137.3 | 3,991 frames, 3 requests |
| `m21-cold-new-game` | 276.8 | 1,174 frames, 3 requests |
| `m21-area1-entry` | 8.0 | 183 units, 7 domains |
| `m21-ferry-reload` | 6.9 | 18 units, 3,825 native vblanks, 7 domains |
| `m21-ferry-resume` | 6.9 | 34 units, 49 native vblanks, 7 domains |
| `m22-retail-service` | 5.6 | 7 instruction sequences, 3 controls |
| `m23-authentic-boundary` | 5.7 | 3 checkpoints |
| `m24-reference-map` | 0.4 | 37 functions, 6 scheduler registrations |
| `m23-world-init-synthetic` | 9.4 | 10 controls, cycle-2 parity |
| `m24-sequencer-synthetic` | 3.0 | 9 scenarios, 5 controls |
| `m20-m22-c-sanitizers` | 9.8 | 4 drivers clean, canary detected |
| `world-audio-lifecycle` | 8.8 | 2 cycles × 143 frames, 17 events, 19 key-ons, 1 steal, 101,600 non-zero PCM frames; 10 controls |

**Earlier attempts (kept, not resealed).** An earlier pass of both profiles
ran on implementation commit `ade2ece`. In it, `synthetic`
(`m12-20261127T233042Z-1n5zu0lq`, 4 workers, 208.9 s) accepted 41/41.
`current` (`m12-20261127T233429Z-eu9ds18y`, 4 workers, 2,017.8 s) accepted
135/138: three gates ended as harness errors because their contracts
expected the wrong report shapes:

- `m19-resource-job`: `jobs` is a count, not a list.
- `m20-hgscript-differential`: control messages read `EE …, C …`, not "differ".
- `m20-title-hgscript`: the verifier does not emit the review-added
  `source_revision`.

The measurements themselves matched evidence. `77a8dee` corrected only those
contracts, and a regression test with each real report shape was added.
The acceptance run above is a fresh run, not a resume: all 138 gates were
executed again.

**Scheduling.** The host was not dedicated, so the 4-worker `current` run
used the safe idle-host setting rather than 8. No separate `historical` run
was made because `current` contains all 79 historical gates with unchanged
contracts. The final `synthetic` run used the single-worker default
because the host was busy with interactive use at the time.

## Limitations

- Authentic cold entry still stops at the first AREA1 coordinator call
  (`0012f850`); continuing on `area1-active` hits `HG_DOMAIN_FAULT(0x30)` at
  `00158560`. The connected lifecycle is an original synthetic model.
- Retail sequencing (`002add90`, `002ab508`), the consumer `002a96b8`,
  scheduler wake period/timing, the allocator chain and release timing,
  streaming dialogue (`001564a0`/`00156550`), complete IOP behaviour and PCM
  equivalence remain open. The connected schedule and service order are
  supplied by the harness.
- `00156cf0` gameplay audio resume is not called; it remains an
  `HG_PROV_UNKNOWN` seam in the M23 ledger.
- Prior milestone receipts establish only their own pinned sources; this
  review's receipts establish only the tested commit recorded under Results.

## Remaining authentic dependencies (read-only reconciliation)

- **Coordinator/player state-0:** `00158560` state-0 branch `001585c0`
  (`00158a10`, `001e3440`, `00158470`, phase 0→1); `PLAYER+0x18 == 0` on
  `area1-active`.
- **Actor/bone pools:** `00141b10`, `00141910` (`00141f60`/`00141ce0`).
- **Spawn/camera:** `00141210` (`00141330`, spawn table `0030f3c0`), camera
  `0012c790`, bone evaluator `0013a240`/`0015e1d0`.
- **Collision/overlay services:** `0013a4c0` (`001454a0` registration), mesh
  queries `001e3440`/`001e3850`/`001e3c40`, AREA1 overlay callbacks
  (`00a8a070`, `00a88de0`, `00a87ef0`, `00a89ab0`, `00a88970`), type-2 render
  callbacks (`0019c8a0`, `001a6dc0`, `002a20b0`, `002f7830`); also
  `0013fef0`, `0012bf70`, `00189aa0`, `0025c0f0`, `00182430`.
- **Audio timing/consumer/allocation:** wake source/period (`00a6c3c0`, slot
  counters `00a6c344 + 8n`), consumer `002a96b8`, sequencer `002add90`, Ax
  handler `002ab508`, allocator `002ae9b8`→`002afed8`→`002af1d0`→`002b1098`,
  release `002b2030`.
- **Gameplay resume:** `00156cf0` inside `0012e700` sub 0.
- **Separate streaming:** dialogue streaming `001564a0`/`00156550`.

## Recommended next independent milestones (not started)

1. **World track — authentic AREA1 state-0 player and world setup.**
   Reconstruct the `0012e700` sub-0 callees and the `00158560` state-0 branch
   so authentic `area1-active` continues past the `0x30` fault into the first
   controller-driven frame, compared against `obs3-000`. Prerequisites: the
   existing `area1-active`/`obs3-000` captures plus fresh PINE stops at the
   `00158a10` return and the first state-1 frame; retail collision queries
   for the first movement.
2. **Audio track — retail sequencer timing and allocation observation.**
   Record the wake counter `00a6c3c0`, slot counters, command
   `+0x20..+0x48`, the channel ring and the voice records/masks at
   `00a832b0`/`00a832a0`/`00a83270..7c` across the two M22 cursor
   checkpoints, then reconstruct `002add90`/`002ab508` against them.
   Prerequisites: a PCSX2 v2.8.2 session on the pinned build and the M22
   checkpoints. Independent of the world track.

## Reproduction

From the integration worktree, with `$CONFIG` a private runner config
(placeholders in [verification-infrastructure.md](verification-infrastructure.md))
that also binds `m13-captures`, and the M18/M21/M22 private captures under
`local/m18`, `local/m21` and `local/m22`:

```sh
python3 -B -m unittest discover -s tests -v
python3 -B tools/hg_verify.py plan --profile current --config "$CONFIG"
python3 -B tools/hg_verify.py run --profile synthetic --jobs 4 --config "$CONFIG"
python3 -B tools/hg_verify.py run --profile current --jobs 4 --config "$CONFIG"
python3 -B tools/hg_verify.py summarize --run "$RUN_ID"
python3 -B tools/verify_world_audio.py --compiler gcc --sanitize --output "$OUT/world-audio.json"
```

Strict combined builds (12 sources, 280 exported functions, 0 duplicates):

```sh
for c in gcc clang; do
  "$c" -std=c11 -O2 -Wall -Wextra -Werror -pedantic -ffp-contract=off -fPIC -shared \
    -Wl,--no-undefined $(git ls-files 'src/*.c' 'src/**/*.c' | sort -u) -lm -o "$OUT/combined-$c.so"
done
```
