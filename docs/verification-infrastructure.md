# Verification infrastructure, Phase 1

Cloud implementation and synthetic validation on a local branch from
`86a5670852d47c4ead140e5c8e40a5289f773053` (PR #21 merge). Original
verification and performance characterization remain owner-local. This
handoff records bounded Phase 1 progress. The complete milestone remains open.

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
