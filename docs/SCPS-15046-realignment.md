# SCPS-15046 research realignment

**2026-11-28.** The Decomp changes are based on
`7191ca7402b5c8f0d5a58dbd38c62d099470c667` (PR #23 on `main`).
The separate private PC repository starts at
`cf2ab914a431cbbab9e77c22f7cfaf4959566704`. Both are local review
changesets. Decomp owns original-game research, PC holds a future-facing
reference index, and PS2DInfo owns reusable PS2 methods.

## Identity and provenance

| Input | Verified identity |
| --- | --- |
| Japanese ISO | `8ca1513c8417c0a40b838ff5edf22a2360f43bce1b0458693a95a6efdafd994e` |
| Japanese boot ELF | `a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61`, serial SCPS-15046, version 1.02 NTSC, entry `00100008`, CRC `91AA4B28` |
| PS2DInfo tools | `2e4a226e00d33ab35391aacade71b55826e5f52f`, exported locally from the existing checkout for reproducible reads |
| M25 stop pair | PR head `969e913cf9520868ed1d575f108aafc1ab016a72`. M25 receipt SHA-256 `815855b6d1d13f34cfd17c2da0b05aa0343f2699874c9facf6e69e5fc76e0521` |
| Captured environment | Existing PCSX2 v2.8.2 package `0c46bb6a88aa2782b10853a7b07cf3387ba99cbef2b966372cd2315b8571abea` and BIOS `c641edbd8fcc1ef363639710795a670850b4549efa0de7dbadabc2b6eb363c4a` match the capture references |
| Audio saves, modules and verifier | [M22 module and save identities](../evidence/m22/retail-check.json) and [M26 working-file provenance](retail-audio-sequencing.md) |

PS2DInfo `check_local_inputs.py` ran against `evidence/local-inputs.json`
with an existing owner-local config. Decomp's `hg_identify.py --build japan`
reproduced the build manifest apart from the observation date. Research here
uses authenticated bytes and existing snapshots. A new Ghidra export, live
PCSX2 capture, and hardware comparison were outside the checks run.

## Coverage results

The [v2 inventory](../evidence/realignment/coverage.json) deduplicates
resident EE and each overlay by original image hash and guest address.
IOP uses module hash and module-relative address. Source mappings indicate
bounded work. Separate flags mark reviewed entries, checked original bytes,
selected runtime tests, interpreter execution, partial recovery and external
dependencies. An entry may have several flags. The runtime flags are a
selected, source-cited subset of repository evidence.

| Domain | Source-mapped entries / candidate entries | Original bytes checked / candidate bytes |
| --- | ---: | ---: |
| Resident EE | 178 / 4,525 | 93,452 / 2,469,784, plus 4 checked bytes outside candidate bodies |
| AREA0 title overlay | 5 / 6 | 2,504 / 2,504 within documented functions |
| AREA1 overlay | 1 / 2 | 2,180 / 2,180 within documented functions |
| IOP | Full census unavailable | 80 selected instruction bytes across SG2IOP (48), LIBSD (20) and SDRDRV (12). Their executable sections contain 17,840, 18,528 and 4,384 bytes respectively. |
| VU | No authenticated microprogram census | Unmeasured |

The EE denominator is the union of aligned, file-backed automatic and
documented candidate bodies. Of 4,558 historical automatic candidates,
35 entries or body ranges are unbacked or unaligned and excluded. The reviewed
packets can add candidates independently. Later source mappings cover audio
EE, HGScript and scene routines. For M21 the title timeline payload is data and
excluded. For M22 an SG2IOP data-table anchor adds 4 checked data bytes and
contributes zero to the IOP instruction count. Overlays without authenticated
packets, other IOP modules, BSS and VU microprograms remain outside these
denominators. EE COP2 macro instructions count as EE instructions.

The collision replay authenticates 3,296 distinct executed bytes, already
included in the checked-byte tally where they overlap. Checked bytes and
candidate totals measure evidence and scope. Recovered-original-instruction
byte coverage is unmeasured. Independent HSQ1, host IOP and world-model
behavior supply zero original instruction bytes, irrespective of exported
symbols, body sizes or model test results. Earlier v1 reports remain
reproducible, with their historical classification semantics.

```sh
python3 -B tools/hg_coverage_inventory.py --retail-elf "$HG_ELF" \
  --retail-iso "$HG_JAPAN_ISO" --output "$OUT/coverage.json"
cmp evidence/realignment/coverage.json "$OUT/coverage.json"
python3 -B -m unittest discover -s tests -p test_hg_retail_coverage.py -v
```

## Validation on the realignment worktree

- `python3 -B -m unittest discover -s tests -v` with the authenticated ISO
  and ELF bound passed **508/508** with zero skips (Python 3.14.10,
  GCC 16.2.1, JDK 21.0.13.1). The three added coverage controls check address
  deduplication, overlay identity and independent-model exclusion.
- The v1 inventory matched the base script's result. The v2 inventory and
  collision replay reproduced their saved reports byte-for-byte. An independent
  interval calculation matched EE totals. Independent ISO extraction and
  read-back matched both overlays. A separate ELF section check matched all
  three IOP counts.
- Wrong ELF identity and a changed M25 receipt were rejected as expected.
  All three perturbed collision queries produced the expected no-hit result.
  The M22, M24, M25 live-continuity and M26 reference-timing receipts reproduced.
- Both worktree publication scans returned zero heuristic findings. Portable
  Markdown links and pinned Decomp file targets were checked. The two changesets
  contain text and research tooling, with no engine changes.

## PR #24 and M26 integration

**Hold PR #24 for a scope decision.** On 2026-11-28 GitHub showed the
[PR](https://github.com/finakechi/Hungry-Ghosts---Decomp/pull/24) open and
mergeable at `969e913` against `7191ca7`, with four successful Python
3.10/3.14 synthetic check runs. The original-game evidence is worth retaining.
The same PR also introduces an opt-in world-session API and approximately
1,000 lines of independent world-fixture implementation. Noah should
explicitly accept that legacy fixture expansion as part of this PR or request
an evidence-focused revision before deciding to merge.
[Head CI](https://github.com/finakechi/Hungry-Ghosts---Decomp/actions/runs/46157004374)

The PR's two commits are `7e0a686bc68251e1e693c79605bf9988323ec8f7`
and `969e913cf9520868ed1d575f108aafc1ab016a72`. The final 42-gate synthetic
and 141-gate current summaries matched their published summary hashes.
Recomputing the source fingerprint at the PR head produced
`4075bec787265f04be2a93d53b5d081de3b743c8a42177c5bccc0049196e196a`, matching
both summaries. The current receipt recorded 136 passes and 5 expected
rejections. The present review reran the 10 world-session tests and
reproduced the live-continuity and authentic-dependency receipts byte-for-byte.
The sanitized world-session check reported a matching receipt with no findings.
A full PR profile rerun was outside this pass. The object audit scanned 51 objects with zero heuristic findings.
See [M25's historical verification record](https://github.com/finakechi/Hungry-Ghosts---Decomp/blob/969e913cf9520868ed1d575f108aafc1ab016a72/docs/verification/m25-results.json).

M26's branch `research/m26-audio-timing` still points to `7191ca7` and
contains uncommitted files. The clean local `integration/m25-m26-review`
at `1be283304aa72b8d3d96b0670ce934295296923e` includes the M26 working
snapshot commit `5750236a259d5f7a317247169abdcba40440d0a1`. Reconciliation
against a committed M26 source is blocked. Preserve both worktrees.
The owner needs to provide or authorize a reviewed standalone M26 source
commit and approve an isolated integration worktree. After comparing against
that commit, check the shared registry, tests, handoffs and receipts before
any separately approved push.

## Publication plan

The Decomp review branch is `research/scps-15046-realignment`, targeting
Decomp `main`. Its scope is documentation, coverage tooling, the collision
verifier and evidence summaries. The PC review branch is
`docs/research-boundary`, targeting PC `main`. Its scope is the README,
agent guidance and a commit-pinned index. The audit found no distinct
port design to move. The index contains newly written summaries and links.
Original research, code, models, tests and receipts stay in Decomp. No
third-party code, original media or private captures are transferred.

Both local branches have separate pending approvals for commit, push and PR
creation. Recheck both remote base refs before any approved action, verify the
resulting head after a push, and confirm PR base/head and available CI after
creation. PR #24 and the snapshot integration branch have their own decisions.
