# Verification infrastructure (Phases 1–3)

Phase 1 implemented the Gate 01 contract fix, bounded scheduler, and 120-gate
current profile in the cloud from `86a5670852d47c4ead140e5c8e40a5289f773053`
(PR #21 merge). Phase 2 authenticated the Phase 1 handoff on MainPC, fixed four
genuine contract/isolation defects exposed by original-data receipts, applied
measured exact-behavior optimizations, verified all 79 historical gates and all
120 current gates against authenticated original inputs, and proved exact
serial/parallel semantic equivalence. Phase 3 reviewed the Phase 2 diff and
receipt seals, assessed M19–M24 compatibility in isolated disposable worktrees,
and prepared this verification-only integration into `main` without importing
unrelated milestone branches.

## Phase 3 integration, compatibility, and rollout — 2026-11-27

- **Scope:** Verification infrastructure only (`integration/verification-infrastructure-phase3`).
  Preserves the exact Phase 2 verified code revision
  `734e328fe7c9cea90b2dd746c0ea1fff0647dbc0` (`source_sha256`
  `c66bc38ffb97f13b9e2e2da13b7a4df8ea2a1b25fa97b86868fd927b551524f8`) on top of
  remote `main` (`86a5670852d47c4ead140e5c8e40a5289f773053`) with docs-only
  Phase 2/Phase 3 records layered on top; prior private receipts are never
  resealed.
- **Receipt & code review:** Independently re-verified all 5 Phase 2 private
  receipts (`bad_seals=0`, `bad_artifacts=0` across all 436 check receipts),
  Python 3.10 AST/stdlib compatibility across all 27 changed Python files
  (0 issues), and privacy/leak boundaries. Machine-readable compatibility
  records are in [phase3-compatibility.json](verification/phase3-compatibility.json).

### Safe defaults, configuration, and verified CLI usage

- **Worker defaults & limits:** `--jobs 1` remains the serial default reference.
  Use `--jobs 4` explicitly for ordinary owner-local runs on an idle 8-core
  host (`historical` ~13.6 min, peak child RSS 3.38 GiB; `current` ~23.7 min,
  peak child RSS 8.07 GiB). Use `--jobs 8` for `historical` only in an approved
  quiet window (~10.8 min); do not default to 8 workers and do not infer
  8-worker `current` RAM safety from single-child RSS alone.
- **Isolation & invalidation:** Keep each checkout's `local/verification/` and
  attempt build directories separate; never share writable `local/`, build,
  cache, or receipt folders across worktrees. Any change to tracked/untracked
  source under `SOURCE_DIRS`, pinned tools, or authenticated inputs invalidates
  receipt reuse automatically. Never resume a baseline receipt into changed
  source; start a fresh `run`.
- **Example private config (`$CONFIG`, kept outside tracked files; placeholders only):**

```json
{
  "elf": "/path/to/local/extracted/japan/SCPS_150.46",
  "image": "/path/to/Hungry Ghosts (Japan).iso",
  "emulator": "/path/to/pcsx2-v2.8.2.AppImage",
  "ps2dinfo": "/path/to/PS2DInfo",
  "captures": "/path/to/local/m12/captures",
  "ordinary-source": "/path/to/local/m9/m8-a1-zero.c"
}
```

Set `HG_CAPTURE_DIR` (or add `"m13-captures"` to `$CONFIG` on the updated runner)
for `current` corridor checks. Verified CLI commands:

```sh
# Plan without executing (verify zero missing/invalid inputs first)
python3 -B tools/hg_verify.py plan --profile focused --config "$CONFIG"
python3 -B tools/hg_verify.py plan --profile historical --config "$CONFIG"
python3 -B tools/hg_verify.py plan --profile current --config "$CONFIG"

# Focused (8 gates), historical/full (79 gates), comprehensive current (120 gates)
python3 -B tools/hg_verify.py run --profile focused --jobs 4 --config "$CONFIG"
python3 -B tools/hg_verify.py run --profile historical --jobs 1 --config "$CONFIG"
python3 -B tools/hg_verify.py run --profile historical --jobs 4 --config "$CONFIG"
python3 -B tools/hg_verify.py run --profile historical --jobs 8 --config "$CONFIG"   # quiet window only
python3 -B tools/hg_verify.py run --profile current --jobs 4 --config "$CONFIG"

# Resume an interrupted run in the same worktree, or print its summary
python3 -B tools/hg_verify.py resume --run "$RUN_ID" --jobs 4
python3 -B tools/hg_verify.py summarize --run "$RUN_ID"

# Public CI synthetic suite (no retail inputs; 462 pass + 3 retail-only skips)
python3 -B -m unittest discover -s tests -v
```

### M19–M24 compatibility and migration matrix

All eight unpublished local M19–M24 branches branch from `86a56708`. Disposable
isolated trial merges (committed tips only; no original worktree or branch
touched) showed **0 code conflicts** across all eight branches; only handoff
docs (`MASTER_HANDOFF.md`, `START_HERE.md`, and `README.md` on M21–M24)
conflict. The 120-gate `current` profile covers M1–M18/Content/Audio only; it
does **not** claim coverage of M19–M24, which must register their own explicit
gates, controls, and receipts when they integrate.

| Branch (Pinned Tip) | Worktree State | Code Conflicts | Doc Conflicts | Isolated Trial Result | Adoption / Migration Action |
|---|---|---:|---|---|---|
| `research/m19-autonomous-loading` (`e512f2f2`) | Clean | 0 | `MASTER_HANDOFF.md`, `START_HERE.md` | Inherited via `m19-m20` trial | Wrap `local/build/m19-resource.so` in `build_path()` before parallel runner use. |
| `research/m20-hgscript-runtime` (`04648507`) | **Dirty** (11 untracked probe files; uncommitted unknown) | 0 | `MASTER_HANDOFF.md`, `START_HERE.md` | Inherited via `m19-m20` trial (committed tip only) | Archive/clean untracked probes before adopting; do not auto-rebase. |
| `integration/m19-m20-review` (`b855eb93`) | Clean | 0 | `MASTER_HANDOFF.md`, `START_HERE.md` | **Pass:** `compileall` OK, 473 discovered, 47/47 targeted tests OK, 79/120 plans OK | Wrap `local/build/m19-resource.so` in `build_path()`; map 6 new M19/M20 verifiers explicitly on M19–M20 integration. |
| `research/m21-scene-coordination` (`167a7281`) | Clean | 0 | `MASTER_HANDOFF.md`, `START_HERE.md` | Inherited via `m21-m22` trial | `tools/ee_subset.py` (`lwu` `0x27`) & `tests/test_ee_subset.py` auto-merge cleanly with Phase 2 FP code. |
| `research/m22-audio-service` (`54f1f130`) | Clean | 0 | `MASTER_HANDOFF.md`, `START_HERE.md` | Inherited via `m21-m22` trial | `tools/hg_audio/audio_guest.py` auto-merges cleanly with `build_path()`. |
| `integration/m21-m22-review` (`705cd20f`) | Clean | 0 | `MASTER_HANDOFF.md`, `README.md`, `START_HERE.md` | **Pass:** `compileall` OK, 483 discovered, 49/49 targeted tests OK, 79/120 plans OK | Resolve handoff docs; map 3 new M21/M22 verifiers explicitly on M21–M22 integration. |
| `research/m23-gameplay-initialization` (`6cc3ce8d`) | Clean | 0 | `MASTER_HANDOFF.md`, `README.md`, `START_HERE.md` | **Pass:** `compileall` OK, 488 discovered, 46/46 targeted tests OK, 79/120 plans OK | Resolve handoff docs; map `tools/verify_world_init.py` explicitly when M23 integrates. |
| `research/m24-audio-sequencing` (`1830f5ec`) | Clean | 0 | `MASTER_HANDOFF.md`, `README.md`, `START_HERE.md` | **Pass:** `compileall` OK, 491 discovered, 49 passed / 1 retail skip, 79/120 plans OK | **Note:** `tests/test_m24_reference_map.py:36` skips 1 test when `HG_ELF` is unset. Gate 00 fails closed on unlisted skips, so M24 integration must explicitly update the discovery skip contract or isolate that retail test. |

### Safe rollout and rollback

- **Rollout for active coding agents:** Existing agents on M19–M24 do **not**
  need to migrate mid-task. Finish active work on the pinned runner, then adopt
  `main` in a separate isolated worktree after a clean checkpoint, resolve
  handoff-doc conflicts, re-run synthetic/targeted checks, and generate fresh
  receipts (old receipts from `86a56708` will not reuse across changed source).
- **Rollback:** If any post-merge regression is discovered, roll back via a
  reviewed `git revert -m 1 <merge-commit>` PR against `main`, preserving
  history and all private/sanitized receipts. Never use force-push, history
  rewrites, or automatic rollback.

## Phase 2 MainPC validation — 2026-11-27

Verified code revision: `734e328fe7c9cea90b2dd746c0ea1fff0647dbc0`
(`source_sha256` `c66bc38ffb97...`, clean working tree on isolated branch
`verification/phase-2-mainpc`). Subsequent documentation and sanitized-evidence
commits record these results without resealing historical receipts. Machine-readable
sanitized results are in [phase2-results.json](verification/phase2-results.json).

### Benchmark results (MainPC, Ryzen 7 5800X 8C/16T, 31 GiB RAM, NVMe, quiet window)

All runs executed with zero reused receipts and zero retries against identical
authenticated retail inputs (`SCPS_150.46` `a74248c5...`, Japan ISO `8ca1513c...`,
`DATA/PACK.DAT` `f2e04a08...`, M18 overlay `ef257df9...`, pinned PCSX2 v2.8.2
`0c46bb6a...`, pinned PS2DInfo `2e4a226e...`).

| Run | Profile / Revision | Workers (Peak) | Accepted / Total | Wall Time | Child Time | vs. Unmodified Base | vs. Corrected Serial | Peak Child RSS |
|---|---|---|---|---|---|---|---|---|
| Unmodified baseline (`m12-20261127T190644Z-2j7t7my9`) | `full` (79) on `86a56708` | 1 (1) | 79/79 (74 pass, 5 exp-rej) | 3,143.14 s (52m 23s) | 3,119.86 s | 1.00× (0.0%) | — | 3.50 GiB |
| Corrected serial (`m12-20261127T195941Z-ixt34s27`) | `historical` (79) on `734e328` | 1 (1) | 79/79 (74 pass, 5 exp-rej) | 1,950.38 s (32m 30s) | 1,925.20 s | **1.61× (−38.0%)** | 1.00× | 3.76 GiB |
| Optimized parallel-4 (`m12-20261127T203250Z-mx0eah77`) | `historical` (79) on `734e328` | 4 (4) | 79/79 (74 pass, 5 exp-rej) | **818.49 s (13m 38s)** | 2,105.12 s | **3.84× (−74.0%)** | **2.38× (−58.0%)** | 3.38 GiB |
| Optimized parallel-8 (`m12-20261127T211502Z-cm9phbqt`) | `historical` (79) on `734e328` | 8 (8) | 79/79 (74 pass, 5 exp-rej) | **645.33 s (10m 45s)** | 2,431.96 s | **4.87× (−79.5%)** | **3.02× (−66.9%)** | 2.85 GiB |
| Expanded current (`m12-20261127T204736Z-49j6appf`) | `current` (120) on `734e328` | 4 (4) | **120/120** (115 pass, 5 exp-rej) | **1,424.22 s (23m 44s)** | 4,706.20 s | — (expanded workload) | — | 8.07 GiB |

Representative synthetic scheduler scaling (`python3 -B docs/verification/benchmark.py`,
16 gates × 25,000 R5900 cases × 3 repeats): medians `8.721 s` (1w) / `4.704 s`
(2w, 1.85×) / `2.465 s` (4w, 3.54×) / `1.510 s` (8w, 5.78×), identical semantic
receipt SHA-256 `6b26c5c5...` across all 12 runs.

### Phase 2 defects fixed and optimizations verified

1. **Gate 19 (`connected-player-saved-runtime`) false rejection fixed:** Phase 1's
   anchor in `tools/hg_verify_legacy_contracts.py` raised `KeyError: 'compared_bytes'`
   on genuine case 6 (`connected-gameplay-sequence`). Hardened contract now validates
   cases 0–5 plus all 6 nested stages and `gameplay_checks` of case 6.
2. **Gate 56 (`turn-camera-saved-runtime`) false rejection fixed:** Phase 1's anchor
   and stage loop raised `KeyError: 'entry_sha256'` / `'compared_bytes'` on genuine
   probe-only stage 14 (`release5-update`). Now validates stages 0–13 and the
   `release5-update` probe fields explicitly.
3. **M16/M17 corridor contract false rejection fixed:** `tools/hg_verify_current.py`
   raised `KeyError: 'guarded_ram_count'` on genuine `pre-dispatch-window` stages.
   Now verifies `pre-dispatch-window` oracle, stream, and difference fields.
4. **Attempt isolation and thread safety fixed:** Routed mutant build directories in
   `tests/test_front_end_differential.py` and `tests/test_lifecycle_differential.py`
   through `build_path()`; locked `Inputs.tool()` and canonicalized `Inputs.file()`
   cache keys in `tools/hg_verify.py`.
5. **Exact O(1) rational FP and zero-copy comparison:** Replaced the 31-step
   `Fraction` binary search in `tools/ee_subset.py` (`fp_value`, `fp_chop`,
   `fp_signed_nearest`) with exact integer bit-length and `math.isqrt` arithmetic
   (90.7× faster in micro-benchmark, 100% bit-exact across 15,019 cases, all 3
   `MODEL_DEFECTS` anchors preserved) and added a `memoryview` fast path in
   `tools/verify_player_connected_runtime.py:compare_memory()`.
6. **Bounded concurrency for isolated verifiers:** Enabled audited attempt-isolated
   verifiers in `PARALLEL_RESULTS` with heavy-first dispatch when `--jobs > 1`
   while retaining inventory order for `--jobs 1` and explicit `exclusive`
   serialization. (Future tuning note: propagating heavy-first priority to
   transitive `after` prerequisites like `constraint-shared-bytes` can eliminate
   a ~331 s start delay observed on `constraint-differential` at 4 workers.)

### Semantic equivalence, cross-checks, and regressions

- **Serial vs. parallel equivalence:** Deep comparison of all 79 historical gates
  and all 83 JSON report/control artifacts produced **0 differences** between
  1-worker serial (`m12-20261127T195941Z-ixt34s27`), 4-worker parallel
  (`m12-20261127T203250Z-mx0eah77`), 8-worker parallel
  (`m12-20261127T211502Z-cm9phbqt`), and the 79-gate historical subset of the
  120-gate current run (`m12-20261127T204736Z-49j6appf`).
- **Base vs. candidate equivalence:** All 77 unchanged-schema historical gates
  (81 JSON artifacts) produced **0 measurement differences** vs. unmodified base
  `86a56708` (differences were strictly metadata digests such as `ee_subset.py`
  SHA-256 and `mutated_model_sha256`).
- **Gate 00 & Gate 01:** Gate 00 ran all **465/465** tests with authenticated
  `HG_JAPAN_ISO` (0 skipped) and preserves the 3 retail-only skips when
  `HG_JAPAN_ISO` is unset (public CI). Gate 01 false acceptance was reproduced on
  base (`missing_measurements` and `empty_cases` returned `passed`) and rejected on
  candidate (`harness-error`), while genuine ELF execution passed (4×350 + 12/48).
- **M16/M18 cross-checks:** `tools/hg_m16_evidence.py` matched tracked
  `analysis/late_frame/functions.json` and `analysis/late_frame/area.json`
  exactly. `tools/hg_m18_checks.py` matched tracked `evidence/m18/results.json`
  on `connected_run` (16,273 frames), `differential` (27 entries / 202,500 cases),
  `negative_controls` (11), and all 17 `scenarios`.
- **Sanitizers, strict C, and runner controls:** Strict C11 builds (`-Wall -Wextra
  -Werror -pedantic -Wl,--no-undefined`) passed with both GCC 16.2.1 and Clang
  23.1.5; all 3 ASan+UBSan C harnesses passed; `HG_SANITIZE=1` Python differential
  tests passed (10/10 in 34.6 s); competing writer lock (`exit 2`), `SIGTERM`
  interrupt (`exit 143`), `summarize`, and `resume` receipt reuse (5/5 reused) all
  passed.

### Phase 2 reproducible commands

```sh
# Focused diagnostic run (8 gates)
python3 -B tools/hg_verify.py run --profile focused --jobs 4 --config "$CONFIG"

# Historical serial reference (79 gates) and optimized parallel (79 gates)
python3 -B tools/hg_verify.py run --profile historical --jobs 1 --config "$CONFIG"
python3 -B tools/hg_verify.py run --profile historical --jobs 4 --config "$CONFIG"
python3 -B tools/hg_verify.py run --profile historical --jobs 8 --config "$CONFIG"

# Comprehensive current profile (120 gates)
python3 -B tools/hg_verify.py run --profile current --jobs 4 --config "$CONFIG"

# Public CI synthetic suite (no retail inputs)
python3 -B -m unittest discover -s tests -v
```

---

## Phase 1 record (preserved)

Cloud implementation and synthetic validation on a local branch from
`86a5670852d47c4ead140e5c8e40a5289f773053` (PR #21 merge). Original
verification and performance characterization remained owner-local during Phase 1.

## Contract and coverage matrix

| Gates or scope | Required evidence | Phase 1 limits |
|---|---|---|
| 00 and focused unittest gates | Discovery reports list each discovered test and its outcome. Gate 00 requires at least the integrated 452 cases and accepts only the three named public Audio skips without an authenticated image. Focused unittest gates require a completed nonempty suite and disallow skips. | Current adds a separate owner Audio test gate. Its missing ISO is a blocker and all three original tests must run when the image is supplied. |
| 01, disc-read instructions | Exact ELF hash, v2 schema, seed, four function entries, 350 invocations per function, 12 ordered lifecycle IDs and 1–25 completed ticks per sequence. The aggregate counts must agree. | The legacy classifier accepted missing measurements and an empty case list. Afterward both return harness errors. Invented valid counts show classifier compatibility only. No original instructions were run. CDVD seams remain deterministic mocks. |
| 02–23 and 55–70 historical reports and controls | Stable count and case identifiers anchored to tracked records where available. Selected runtime reports check stage and handoff identity, memory, scratchpad, refusal and control measurements. Compiled early controls must produce source and library artifacts. | Older heterogeneous reports retain some producer-owned assertions. Exact intended divergence and every omitted-field combination have not been independently proved for every legacy report. |
| Historical 24–54 and packet gates | Strict C11 ASan/UBSan build and measured assertions, pinned structural tools, ELF hashes and expected function/instruction counts, and Git hygiene. | A structural packet verdict only covers the schema. Host C builds do not establish PS2 byte matching. |
| 75–78 grounded replay, differential, M11 replay, and A1-zero control | Observation stages, register and control identities, exact no-floor refusal, matching counts, artifacts and typed A1-zero trace. | Runtime reports remain bounded by authenticated captures and the checked stage/range contracts. |
| M13–M16 | `m16-corridor` exercises the 22 M13 frames and adds the M14 service/render handoff, M15 camera-chain/model-draw/render-node tail, and M16 late service and RNG. `m14-native-window`, `m14-register-contracts` and `m14-counter-guard` retain the separate original model and control comparisons. Packet structure and selected ELF words/JAL targets have separate gates. | Uses the M13 observation manifest and independent captures. Final-frame RNG lacks the next-entry measurement, with the guarded state still checked. The light-matrix exclusion and modeled/external effects remain as documented in [M16](M16.md). Full-coverage equivalence to the separately dated M13–M15 runs requires owner review. |
| M17 | `lifecycle-differential` requires all configured bases and cases, per-function reachable instruction coverage, plus separate controls. `lifecycle-corridor` drives all 22 frames through lifecycle. The two pause observations and area-PACK observation have separate native gates and control receipts. `lifecycle-sequence-synthetic` executes the packet-backed 1,500-frame host sequence. | Partial saved observations cover the documented cells and edges. See [M17](M17.md) for the captured ranges and externally supplied values. |
| M18 | Separate ELF/overlay byte gate, 27 differential entries on five bases, and 16,273 consecutive native-driven frames with transitions, 11 requests, external fades and compiled/null controls. | Native-driven execution covers the front-end and lifecycle state. World simulation stays external. Authentic Continue remains unverified. Preserve the 17 separate scenario assertions in [M18](M18.md) when reviewing the fresh connected receipt. |
| Content | `content-corpus` requires the authenticated ISO, 2,541 distinct members and 74 byte-exact HGScript round trips, with 48 unresolved string bindings. The public suite covers PACK, FLK5, script decode/rebuild, and index behavior on invented inputs. | Round-trip equality does not establish event semantics. PACK-only and selected extract/index commands remain in the [content pipeline guide](content-pipeline.md). |
| Audio | Original ELF differential accounts for all 420 cases, with 407 matches and 13 declared unaligned exclusions. Connected member `6b51` requires registration through stop/release and its WAV artifact. The separately gated owner suite requires the authenticated ISO. | The connected check uses a host IOP service model and offline ADPCM decoding. Retail IOP/SPU2 equivalence remains unverified. See [Audio](audio-milestone.md). |

The historical profile retains 19 original-byte, 16 differential, 11
saved-runtime, 10 synthetic, 16 structure, 4 local-integrity and 3
differential-control gates. `full` remains an alias for the 79 historical IDs.
The 8-gate `focused` profile is retained, `current` includes 120 gates, and
`synthetic` selects the 37 cloud-applicable gates including pinned packet
structure and hygiene checks. The current profile keeps missing original-data
gates in its total. The selected evidence levels are separate from
reconstruction, hardware and runtime coverage.

## Scheduling and evidence

The coordinator publishes rows in inventory order. `requires` waits on and
requires an accepted predecessor. `after` waits on completion only. The
worker choices are 1, 2, 4 and 8, with 1 the default reference. Packet
validation, read-only ELF comparisons, compileall, Git audits and the
isolated sanitizer build/run can overlap. Other gates, including full unittest
discovery, native replay and retail C differential, are exclusive. Each
checkout and each run have a writer lock. Child processes receive isolated
temporary/build paths and the held lock descriptors. Foreground descendants
share the process group, which is cleaned up on timeout or interruption.
Legacy direct commands run outside this lock. Keep them out of the same
checkout while the runner is active.

Each attempt records bound source, tools, input digests, command, measurements
and sealed artifacts. The coordinator rechecks accepted artifacts and inputs
before aggregation. Resume can reuse a latest accepted receipt only if the
seal, required artifact set and fingerprint match. An edited source or input
invalidates affected work. The progress ETA extrapolates observed throughput
and is noisy across different gate types. Large native gates are serialized
because their RAM, capture reads, legacy build outputs and disk use still
need measurement on MainPC. There are no per-process hard memory or disk
quotas. A killed runner or a detached descendant may require operator cleanup
before retry. No new build reuse or interpreter optimization was inferred
from synthetic timing.

## Cloud results and historical provenance

See [sanitized results](verification/phase1-results.json) for commands,
source identity, outcomes, and timings. The Phase 1 baseline used Python
3.12.14, Debian GCC 14.2.0, Temurin JDK 21.0.13+9, a 4-CPU cgroup quota,
32 GiB memory limit and approximately 60 GiB free disk. The first baseline
discovered 452 tests and failed one Java test because `javac` was absent.
After loading the checksum-verified JDK, 449 passed and the three Audio
original tests skipped. The pinned PS2DInfo revision
`af62918c7b2d1b0a4c435fc2176de14111c69e68` matched four tracked tool hashes
and passed its 74 synthetic tests. No shared source was copied or changed.

The tracked [M12 report](M12.md#verification-automation) reports 53m29s
wall time and 31.7s outside verifier processes for an earlier MainPC run.
The supplied PR #21 observation reports the separate 79/79 run
`m12-20261124T005712Z-hezf4rz1` at
`948871ec3bf4a6b681445e3b283720ea7c9c52da`, 74 passes, 5 expected
rejections, zero reuse/retry, approximately 53.5 minutes, and 452 tests in
203.7 seconds. The PR head and merge were corroborated by Git refs. The PR
body and Actions status were inaccessible through the API. These reported
timings are historical and have no role in the cloud benchmark.

The scheduler benchmark uses 16 fresh receipt-producing attempts per run,
each checking 25,000 independently calculated signed-word and branch
delay-slot outcomes. The benchmark report compares fingerprints, exit
status, measurements and artifact digests across 1, 2, 4 and 8 workers.
The timings exclude setup and durable per-gate summaries. The small
independent jobs scale to the 4-CPU quota. They do not estimate retail
runner performance. Reproduce from the repository root with
`python3 -B docs/verification/benchmark.py`.

The separately measured current-profile serial run accepted 37 cloud gates
and disclosed 83 missing-input blockers. The four-worker synthetic selection
accepted the same 37 gates. All 37 common receipts matched after normalizing
timing fields and attempt paths, including the nested temporary mutant
directories. Artifact contents were retained except for those normalizations.
Use `python3 -B docs/verification/compare-cloud-receipts.py SERIAL_RUN PARALLEL_RUN`
to reproduce this check on the local cloud receipts. These profiles execute
different selections, so their session wall times are not a speed comparison.

## Phase 2 owner checklist

1. Verify the transferred source patch SHA-256 from its companion manifest.
   Apply it only inside a fresh MainPC worktree at the exact base. Compare
   the staged tree hash to the manifest and inspect the change before a
   local commit. Use separate fresh output folders throughout.

   ```sh
   BASE=86a5670852d47c4ead140e5c8e40a5289f773053
   # Set PATCH to the transferred patch and NEW to a new owner-local path.
   sha256sum "$PATCH"                       # compare with companion manifest
   git worktree add -b verification/phase-1-review "$NEW" "$BASE"
   cd "$NEW"
   git diff --exit-code
   git apply --check --index "$PATCH"
   git apply --index "$PATCH"
   git diff --cached --check
   git write-tree                          # compare with companion manifest
   ```

2. Bind only owner-local originals. `HG_ELF` and `HG_JAPAN_ISO` use
   [local-inputs.json](../evidence/local-inputs.json). `HG_PS2DINFO` must
   satisfy [the four tool pins](../evidence/m2/shared-tools.json).
   `HG_PCSX2` is bound by the M4–M7 observations. Configure `captures`
   and `ordinary-source` in a private runner config where necessary.
   Rehydrate the M2–M6 Ghidra exports and M4–M12 captures, screenshots and
   profiles from authorized owner backups. The plan prints the specific
   gate arguments. Keep originals and existing evidence read-only.

   For current coverage, `HG_CAPTURE_DIR` (or `m13-captures` in runner
   config) points to the M13 captures with sibling `stops`, as specified in
   [continuous-observation.json](../evidence/m13/continuous-observation.json).
   M17 uses the eight full capture IDs listed in `lifecycle-differential`
   and three capture pairs with hashes in
   [M17 results](../evidence/m17/results.json). M18 requires
   `local/m18/ovl/A0_B09.BIN` with the published overlay hash,
   `local/m18/native/frames.jsonl` bound to
   [M18 checks](../evidence/m18/checks.json), and the four full snapshot
   stems for differential plus the native/control start snapshots listed
   in [M18 results](../evidence/m18/results.json). The full snapshot
   sidecars bind decompressed EE and scratchpad bytes. The current
   corridor uses the Japan image for the AREA1 identity as well as the ELF.
   Content and connected Audio use the same authenticated Japan ISO.
   No retail data belongs in the patch or cloud system.

3. Run a separate original serial baseline on a clean worktree of the
   exact base. Run corrected serial, parallel historical, then current on
   the review worktree, with identical manifests and pinned toolchain. Use the base-compatible config keys plus HG_CAPTURE_DIR for the added corridor.
   Scan plans for all remaining missing/invalid inputs before allocating
   long runs. Review per-attempt evidence and preserve private receipts.

   ```sh
   # Exact-base worktree, original runner
   python3 -B tools/hg_verify.py plan --profile full --config "$CONFIG"
   python3 -B tools/hg_verify.py run --profile full --config "$CONFIG"

   # Patched review worktree
   python3 -B tools/hg_verify.py plan --profile historical --config "$CONFIG"
   python3 -B tools/hg_verify.py run --profile historical --jobs 1 --config "$CONFIG"
   python3 -B tools/hg_verify.py run --profile historical --jobs 4 --config "$CONFIG"
   python3 -B tools/hg_verify.py plan --profile current --config "$CONFIG"
   python3 -B tools/hg_verify.py run --profile current --jobs 1 --config "$CONFIG"
   python3 -B tools/hg_verify.py run --profile current --jobs 4 --config "$CONFIG"
   # On interruption, keep the same worktree and run ID.
   python3 -B tools/hg_verify.py resume --run "$RUN" --jobs 2
   python3 -B tools/hg_verify.py summarize --run "$RUN"
   ```

4. Compare semantic receipts before reporting speedups. Review legacy
   anchors against the new owner-produced reports, validate intended
   negative-control reasons, and run the documented M16 AREA packet and
   M18 scenario cross-checks. Direct generation/aggregation should target
   fresh ignored paths. Compare packet output to the preserved tracked
   record. Investigate differences without relaxing expected values or
   rewriting historical evidence. Collect serial/parallel wall time,
   peak RAM and disk, and repeat with 2 or 8 workers only within the
   observed resource budget. Phase 3 remains deferred.

The independent M16 AREA packet and M18 boundary checks can write to an
ignored owner review directory. In the commands below, `OUT` is a fresh
directory and `M18_RECEIPTS` contains the new front-end differential,
connected and control reports copied as `front-end-diff.json`,
`connected-run.json` and `controls.json`. Include the other M18 inputs
listed by the aggregator for any additional checks it reports.

```sh
python3 -B tools/hg_m16_evidence.py --elf "$HG_ELF" --ps2dinfo "$HG_PS2DINFO" \
  --image "$HG_JAPAN_ISO" --output "$OUT/late-frame-functions.json" \
  --area-output "$OUT/late-frame-area.json"
python3 -B tools/hg_m18_checks.py --elf "$HG_ELF" --overlay local/m18/ovl/A0_B09.BIN \
  --native local/m18/native --out-dir "$M18_RECEIPTS" --output-dir "$OUT/m18-review"
```
