# PACK loading and AREA actor use: M5 instruction evidence

The [instruction packet](functions.json) extends the existing resource-loader
packet with five resident original functions. These are distinct from the established
AREA index/read/finalization functions in `analysis/resource_loader/`.

| Entry | Reconstructed function | Original words |
|---|---|---:|
| `00130900` | `hg_pack_general` | 699 |
| `00131420` | `hg_pack_alternative` | 246 |
| `0012fb30` | `hg_resource_clear_dynamic_handles` | 22 |
| `0012fb90` | `hg_resource_clear_selected_handles` | 11 |
| `0018c220` | `hg_actor_data_init` | 20 |

Resident total: **998 words / 3,992 instruction bytes**. The general-loader body spans
`00130900` through `00131418`; its packet preserves Ghidra's disjoint body ranges
and excludes unreferenced padding. The original Japanese ELF identity is
`a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61`.

## Connected operation

The general loader chooses its destination and allocation mode from the PACK
directory ID. IDs `102`–`110` select mode 8. On this branch the loader calls both
handle-clear helpers, saves arena pointer `003f4d50` to task `+48`, and writes
mode 8 to task `+3e` before entering its polling state machine.

`0012fb30` clears resource-handle indices `96`–`28d` in groups of eight, then
clears index `28e` in the return delay slot. It leaves index `95` and `28f`
untouched. `0012fb90` independently clears addresses `003f45f8`, `003f4c84`,
`003f4c88`, `003f4c8c`, and `003f460c`. Those stores are retained even where the
preceding wider invalidation already cleared them. Neither helper calls a memory
allocator or establishes resource release.

Mode 8 advances main phase 0 directly into the AREA helper in the same iteration.
The existing `hg_area_read_step` selects an AREA record, reads its whole file into
`*(003dff04)`, performs the established cache/zero-tail finalization, and requires
a later phase-2 call before reporting completion. General loading then reads the
requested PACK directory and its bulk range through M1, uses the existing special
member processor, publishes member handles, invokes the explicit fixup boundary
for the header's marked prefix, and updates the mode-8 arena:

```
003f4d54 = (task.destination + header.bulk_length + 7ff) & fffff800
003f4d5c = 003f4d54 + 50000
```

The existing M4 worker adapter resolves `00130900` and `00131420` to these implementations; the
old M2 dependency-isolated worker interface remains available for regressions.
The host-only `hg_pack_general_prepare` and `hg_pack_general_step` expose the
original preparation and individual polling iterations for bounded tests. They
are not additional original function entries. Direct `hg_pack_general` retains
SleepThread between pending iterations.

## Alternative loader used by the startup worker

`hg_pack_alternative` at `00131420` selects the AREA read only when task kind
`+32` is 2. In phase 2, kind 2 clears the dynamic handle range and takes its PACK
destination from `003f4d4c`; other kinds clear the five selected handles and take
`003f4d50`. It then uses the same directory, special-member and bulk read path.

Its completed arena ends are **unrounded**. Kind 2 writes
`003f4d50 = destination + bulk_length`. Other kinds write that raw end to
`003f4d54` and normally set `003f4d5c = end + 50000`. State bytes `(0,2,1)` at
`003fa1b0..b2` select the exceptional reserve: `003f4d58 = end + 50000` and
`003f4d5c = 003f4d58 + 200000`. Empty-directory branches use the destination
alone instead of adding bulk length. An empty non-kind-2 directory sets completion
byte `003eace0`; kind 3 also sets that byte when the bulk read poll completes.
These flag timings and the distinction from general-loader sector alignment are
preserved. No allocator/free interpretation is attached to arena stores.

## Selected AREA1 callback

A separate [raw-file packet](area1-functions.json) records the **94 words /
376 instruction bytes** at `00a8a070` in `/AREA1/A1_B00.BIN`. Its input is the
original file at LBA `1402368`, length `30720`, SHA-256
`8ec765c77cb432b8dbe29c3ef8daa99d23ff0dcfdca939636ffc7b8b78cfa788`.
The measured load destination is `00a87800`; instruction file offset is therefore
`address - 00a87800`. This raw packet deliberately uses no fabricated ELF header
or PT_LOAD mapping. The combined new resident/raw evidence totals **1,092 words /
4,368 bytes across six functions**.

`hg_area1_actor` dispatches on actor byte `+18`. State 0 sets actor identification
fields, initializes actor data at `actor+2d0` using resident `0018c220`, performs
explicit engine calls, writes the original float bit patterns without arithmetic,
and increments the state byte after reloading it. The six defined arguments to
`0018a330` are preserved through the narrow `HGArea1Call6` callback, including
`t0/t1 = 0`; the existing `HGResourceEnv` layout is unchanged.

State 1 clears 12 bytes at `003eb058`, publishes the actor and its `+2bc` data
pointer there, and calls resident `0018c280(1)`. State 2 advances the actor to 3;
state 3 calls `00142e90` with the actor pointer preserved in `a0`. Other byte
states return without these actions. The resident `hg_actor_data_init` clears
48 data bytes, writes three `3f800000` bit patterns and publishes `actor+2bc`.

The runtime entry selected for reconstruction is `00a8a070`. Earlier candidate
entries `00a87880`, `00a87940`, and `00a87a60` are not part of this source or packet.
Engine registration/rendering, actor construction/dispatch, and broader AREA
variants remain explicit boundaries. State-0 model coverage must not be described
as a state-0 retail breakpoint observation.

## Instruction comparison scope

`tools/verify_area_lifecycle.py` reuses the M1–M4 memory bus, strict EE integer
interpreter, ELF mapper, source compiler, and comparison procedure. It checks:

- Every directory ID `0`–`111`, signed boundary IDs, all tested variant and
  scratchpad mode values against the original preparation instructions.
- Complete mode-8 calls for all fifteen IDs `102`–`110`, nine AREA table choices,
  CDVD busy/retry states, and sector-aligned/nonaligned file lengths.
- Representative complete non-AREA calls, all main state bytes including unusual
  values, and mode-dependent alignment and 32-bit-wrap calculations.
- Complete alternative calls with kinds `0`, `1`, `2`, `3`, and `ffff`, normal
  and exceptional reserve states, retries, empty directories, and raw-end wrap.
- Exact handle invalidation with randomized contents and neighboring sentinels.
- Member-ID preservation when a published handle aliases its descriptor's ID.

Comparisons include non-stack RAM and scratchpad, ordered defined external
arguments, and original callee-saved registers. Preparation-only cases stop at
`00131080` and therefore do not establish complete integration of every PACK
allocation mode. Kernel scheduling, CDVD responses and asset fixups are explicit
deterministic mocks. Unsupported state values are exercised with an explicitly
modeled state change at SleepThread so the bounded test can terminate; no game
validation or recovery behavior is invented.

The loader-only run contains **1,641 comparisons**, executing **978/978 loader
instruction addresses**. The combined runner additionally compares 128 selected
AREA callback invocations and 128 resident actor-data initializations, checking
all 94 raw callback words and all 20 resident-helper words. Raw byte validation
authenticates the full original image and selected ISO file before comparing
each instruction at its measured file offset. Two disposable-source negative controls passed by detecting memory
mismatches: changing the mode-8 reserve from `50000` to `60000` differs at
`003f4d5e`; rounding the alternative loader's raw end differs at `003f4d54`.
Neither control is credited for a compilation failure.

These checks establish original-byte and bounded model evidence. The milestone's
retail execution/activation and subsequent lifecycle findings are documented
separately and must not be inferred from a successful memory copy or this
differential result. Mocked engine operations establish no rendering equivalence.

## Retail transition and delivery state

[The M5 report](../../docs/M5.md) and [runtime comparison](../../evidence/m5/runtime-comparison.json)
record the measured A0_B09 -> A1_B00 replacement at a87800, cache preparation,
actual a8a070 state-1 publication, later PACK69 completion with AREA1 retained,
and callback reuse. The new runtime verifier compares original AREA bytes and
loader/control/publication state separately from explicit PACK/engine boundaries.
The selected retail entry uses alternative kind2, rather than general mode8.
Private Drive transport remains a delivery blocker; no completed remote M5
publication is claimed.
