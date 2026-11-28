# M19–M20 local integration review

M20 is **ready after bounded repairs** for the authenticated HGScript paths
described here. The connected title path uses M19's verified normal worker
publication. It starts from blank guest RAM and the authenticated resident ELF
and disc. The saved PCSX2 state is used to check publication and in a separate
saved-state continuation comparison.

## Git and workspace

| Item | Verified revision |
| --- | --- |
| Local `main` | `4d016e9f898c7c0fd857eeb6c34cfcc5d7686b68`, predates PR #21 |
| Base, locally stored `origin/main` | `86a5670852d47c4ead140e5c8e40a5289f773053`, PR #21 merge |
| M20 `research/m20-hgscript-runtime` | `0464850716038d0ae989411482ef2eab574330c6` |
| M20 merge | `c606f1c7deb9a75262d5247a55d600e701017a2c` |
| M19 `research/m19-autonomous-loading` | `e512f2f280ab14786416fa30d3b1c83877ff43c3` |
| M19 merge | `e9252acca9b66d09e24d0e87d2f5a242b7a83226` |
| Verified integration source | `970e7acb1b87c1b8f7397b763d655730780199fa` |

Branch `integration/m19-m20-review`, worktree `decomp-m19-m20-integration`.
The base is the exact assigned merge commit. The local `main` and
remote-tracking refs were left at their inspected revisions. M20's commits
`4a98056`, `2c1e0ec` and `0464850` are ancestors through a normal merge.
M19's implementation `b70c7a7` and handoff `e512f2f` were merged after the
read-only cold publication probe. Both historical handoffs were preserved.
M19's tracked tree and index stayed clean. M20 retained its 11 untracked
research files with unchanged SHA-256 values. The first M19 probe imported
the original branch's audio harness, which rebuilt its ignored
`local/audio/libhg_audio.so` cache. The rebuilt SHA-256
`bbe4e622618ab4cf1b837af7242890c41c8cb9a28bf6a8ab204f8d9a24a01c81`
matches the pre-existing sibling audio builds. No pre-probe hash of that
cache was taken. Subsequent builds wrote into the integration worktree.
No fetch, push, PR operation, or merge into `main` was performed.

## M19 assessment

**Partial overall. Ready for the selected normal-worker publication.** The
lifecycle's raw and adjusted submissions feed the normal descriptor at
`003f8e00`. `ResourceScheduler` starts one `HGResourceJob`, advances its CDVD
transfer from the authenticated ISO and resumes the job on normal vblank wake.
The disc context, busy flag, descriptor, arenas and handle table carry forward.
Kind 2 selects the AREA and its alternate PACK. For the title request
`(2, 192)`, the worker advances to `(3, 193)`. Bulk-ready can rise before
handle writes and fixups finish. The script starts after both busy and task
active clear. The M19 comparison against M18 supplies one pre-operation
guest snapshot and native scene, input and timing seams. The cold path to
title needs the ELF, ISO and host seams only.

The nine-request cold run reached the first title mode after 837 driver units.
It transferred the directory-193 bulk extent, published member 007 in slot
`0x22f` at `01661500`, then completed the worker. Original `00138370` with
selector `0x0c000000` returned the first type-12 child at `0172af00`. Its entry
18 starts `0xc9a00` bytes into the member. Entry 19 bounds its 1,376-byte
extent. The fixed 120-byte parent header, including the replacement of word
`+8` by `0x1e00`, and all 36 arena bytes matched sequence 913. The child equals
both the extracted original and the saved RAM bytes. The worker applied the parent fixup while leaving the type-12 collection byte-exact. The subsequent AREA1 request cleared the dynamic handle
and loaded its original overlay. Handle invalidation and arena reuse are the
observed lifetime effects.

The latest M19-only full receipt `m12-20261124T045553Z-dbucwn1n` was verified at
`e512f2f`. It accepted all 79 gates. A fresh run on the integration source
reproduced the 36 jobs, 765 sleep/return comparisons and four rejected
controls, five M4 and 12 M7 original checkpoints, and M19's 11 ordered requests,
11 completions, 57 logical setups and three full checkpoint matches. Its
16,273-unit M18-driven comparison had 15,591 frame-indexed lifecycle matches
and 697 raw loader matches under the host DMA clock.

The priority worker, CD/audio arbitration, exact IOP timing, original Continue
and a transition between distinct gameplay areas remain open. The surrounding
title scene and actor dispatch remain external. The connected check invokes the authenticated title dialogue branch and supplies explicit scene and timing inputs.

## M20 findings and repairs

- **Guest display buffer overwrite.** `src/hgscript/timed.c` appended text and
  line-break tokens through a carried tail without checking the 512-byte
  display boundary. A long or crowded page could write into the adjacent
  cursor and state cells. The host boundary now reserves the end marker,
  checks space before writing a token and returns
  `HG_SCRIPT_DISPLAY_OVERFLOW`. Rendering is suppressed for that fault.
  The original instruction comparisons still match within the exercised
  release paths. A synthetic overlong token verifies that the tail and a
  sentinel beyond the state remain intact.
- **Negative-control verifier fault handling.** The imported verifier treated
  any `ValueError` prefixed with “tick” as evidence of rejecting a mutant.
  Callback errors used that same prefix. The revised harness distinguishes
  measured EE/C byte, phase and request divergences from execution failures.
  It reports the first differing byte or request and authenticates the actual
  ELF program headers against the build manifest.
- **Title caller identity.** Authenticated resident fragment `00214c68` uses
  handle `0x38`. The title uses the AREA0 overlay slice
  `00a88c28..00a88c80`, whose `00a88c44` call selects handle `0x22f` and
  child type 12. The init call at `00a88c64` uses record 0 and position
  `(0, -50)`. The C adapter implements that slice, the dialogue portion of
  `0025c170`, and the timed reset `001cccd0`. It borrows the child from
  `HGResourceEnv` and checks handle revocation without republishing it.

The interrupted DeepSeek logs show unmapped guest reads, exceptions escaping
ctypes callbacks (including an undefined diagnostic `sys`), write differences
and an unscripted `00182850` draw. The recovered comparison formerly depended
on a queued oracle of expected external returns. The merged verifier uses
separate guest memories, actual nested EE instructions, and declared renderer,
pad, audio and libc fixtures. The current seals bind 34 functions, 92 ranges
and 12,208 bytes. Independently checked 2,382 recovered listing words
(2,144 unique addresses) and another 3,603 word observations in resume listings
(3,493 unique addresses in that group), all equal to the authenticated ELF.
The earlier 1,724-word count describes the owner's older listing selection.

### Independently reproduced evidence

| Domain | Observed result |
| --- | --- |
| Authenticated retail command and frame interpreter | Three fully carried records took 226, 482 and 32 frames. Timed bodies `001ccd80`, `001ccfa0`, `001cd9c0`, plus 803 helper cases. The five retail cases and helpers executed 1,681 original addresses. |
| Static records | PACK 001/050 records 2 and 157, one and two pages respectively, with eight width comparisons and 48 / 84 ordered draw calls. |
| Supplemental authored inputs | 14 original-instruction comparisons for nested text, numeric and record substitutions, timed slot writes and actor cleanup. |
| Saved original title at sequence 913 | 11,880 resident function bytes matched. Neutral pad and fixture voice idle at frame 200. Compared 382 carried frames and 1,104 ordered glyph requests. |
| Corpus | All 2,541 members enumerated, 2,402 structurally classified, 139 unknown and 74/74 collections rebuilt byte-exactly. Runtime offset metadata resolved 10,633 text, 1,326 voice/cue and 2,664 leading metadata references. The legacy binding count remains 26 matched and 48 unresolved. |
| Connected cold title | Original and C executed the title activation and frame slice from independent copies of the cold-loaded guest. 3,861 carried frames, 10,801 ordered glyph requests, one voice-start request, one voice-stop request during reset, and 877 original addresses in the compared routines. The ordered request digest is `db88f73ce9764729da108f7c5f9f2f649d92ddb03c64b612b0bc8eda1e14e6d7`. |
| Release | Reset guest state and requested voice stop through the resident EE queue leaf. The next authentic AREA1 request invalidated slot `0x22f` and replaced the overlay. |

The corpus also contained 4,066 parsed records and zero opaque records. The
longest single referenced text was 54 bytes. The 12,024 referenced scheduled
cue words all targeted slot 0 and used actions 0–2. These are corpus counts
and give no additional runtime comparisons.

The selected timed runtime consumes authenticated collections and
initialized guest state. The direct standalone static APIs assume a valid
handle, record, substitution slot and mapped string. The title adapter checks
busy/active, fixed FLK5 type and span, record-zero header, fade gate, aligned
scratch, and borrow revocation. Callers serialize this singleton with the
loader, supply the scene object and release before replacing its resources.
Draw callbacks are synchronous because their style and text pointers can be
transient. The comparison checks scratch-backed callback fields at the call,
and checks the final bytes of the union of guest writes outside the CPU stack
after each comparison boundary.

The connected run executes the original queue writers `001564a0` and
`00156550` against both guests. Voice-ready and voice-idle are external
inputs at frames 1 and 200. The glyph service returns 24 and records incoming
arguments, style and text digests. The scene object and fade gate are explicit
fixture inputs. The independent saved continuation starts in countdown phase 1.
Fresh PCSX2 playback, actual glyph drawing, IOP/SPU2 playback, the alternate
timed consumer, and natural title scheduling through the entire scene are
unrun. These bounds limit the evidence to the verified loader and script slices.

### Measured negative controls

| Mutant | Divergence |
| --- | --- |
| Text offset one word late | Tick 1, display byte at `00a6bce9` differed. |
| Wait does not yield | Tick 1, wait cell at `00a6bcc4` differed. |
| Reversed audio-ready predicate | Tick 0, voice-status byte at `003f2fa0` differed. |
| Wrong audio request channel | Tick 0, first request sent channel 2 instead of channel 1. |

The synthetic title smoke also verifies rejection of missing or unfinished
publication, an earlier wrong type-12 selection, the wrong child span, a
pending fade and misaligned scratch. Revocation suppresses stale callbacks.

## Verification

Inputs are owner-local. Use SCPS-15046 ISO SHA-256
`8ca1513c8417c0a40b838ff5edf22a2360f43bce1b0458693a95a6efdafd994e` and
ELF SHA-256 `a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61`.
The resident file-backed PT_LOAD begins at VA `00100000`, file offset
`800`, with entry `00100008` and build CRC `91AA4B28`. Capture sequence
913 had EE RAM SHA-256
`648137359e80208843ed163b1ec219f1d5f9500a51f4d8b8d34301c6525a6674`.
Full verification needs the pinned PS2DInfo checkout
`2e4a226e00d33ab35391aacade71b55826e5f52f`, the PCSX2 v2.8.2 AppImage
SHA-256 `0c46bb6a88aa2782b10853a7b07cf3387ba99cbef2b966372cd2315b8571abea`,
and the historical private artifacts under `local/m2` through `local/m12`.
The front-end regression and title check need the M18 `native` capture
directory. The M17 corridor check also needs `local/m13/captures`. Existing
private inputs are reused through local links. GCC 16.2.1, Clang 23.1.5,
Python 3.14.10 and javac 21.0.13.1 ran on CachyOS x86-64.

From the integration worktree, choose a fresh ignored output directory. The
private run here used `local/m19-m20-review/`.

```sh
review=local/m19-m20-review
elf=local/extracted/japan/SCPS_150.46
iso='../Hungry Ghosts (Japan).iso'
native=../decomp-m18/local/m18/native
mkdir -p "$review"

python3 -B tools/hg_identify.py "$iso" --build japan --output "$review/original-identity.json"
python3 -B tools/verify_title_hgscript.py --iso "$iso" --native "$native" \
  --library "$review/title-final.so" --output "$review/title-final.json"
python3 -B tools/hgscript_differential.py --elf "$elf" --iso "$iso" \
  --scratch "$review" --output "$review/differential.json"
python3 -B tools/hg_content/cli.py corpus "$iso" \
  --expect-sha256 8ca1513c8417c0a40b838ff5edf22a2360f43bce1b0458693a95a6efdafd994e \
  --output "$review/corpus.json"
python3 -B -m unittest discover -s tests -p 'test_hg_content_*.py'
python3 -B -m unittest discover -s tests -p 'test_hgscript_runtime.py' -v
python3 -B tools/verify_resource_job.py --elf "$elf" --ps2dinfo local/references/PS2DInfo \
  --negative-controls --output "$review/m19-job.json"
python3 -B tools/verify_resource_retail.py --iso "$iso" --m4 local/m4/runtime \
  --m7 local/m7/captures --output "$review/m19-retail.json"
python3 -B tools/run_resource_lifecycle.py --iso "$iso" --native "$native" \
  --library "$review/lifecycle.so" --output "$review/m19-connected.json"
python3 -B tools/run_front_end_native.py --native "$native" --start title-menu-input-ready \
  --library "$review/front-end.so" --output "$review/m18-regression.json"
python3 -B tools/verify_player_continuous.py --elf "$elf" --ps2dinfo local/references/PS2DInfo \
  --capture-dir local/m13/captures --area-image "$iso" --lifecycle --output "$review/m17-corridor.json"
```

```sh
for compiler in gcc clang; do
  "$compiler" -std=c11 -O2 -Wall -Wextra -Werror -pedantic -ffp-contract=off \
    -fPIC -shared -Wl,--no-undefined src/hgscript/hgscript.c src/hgscript/timed.c \
    src/hgscript/title.c src/resource_loader.c src/disc_read.c src/front_end.c \
    src/game_lifecycle.c -o "$review/strict-$compiler.so"
  "$compiler" -std=c11 -g -O1 -Wall -Wextra -Werror -pedantic -ffp-contract=off \
    -fno-omit-frame-pointer -fsanitize=address,undefined -fno-sanitize-recover=all \
    -I src tests/hgscript_title.c src/hgscript/hgscript.c src/hgscript/timed.c \
    src/hgscript/title.c src/resource_loader.c src/disc_read.c -o "$review/title-$compiler-san"
  ASAN_OPTIONS=detect_leaks=1:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 \
    "$review/title-$compiler-san"
done
gcc -std=c11 -g -O1 -Wall -Wextra -Werror -pedantic -ffp-contract=off \
  -fno-omit-frame-pointer -fsanitize=address,undefined -fno-sanitize-recover=all \
  -I src/hgscript tests/hgscript_runtime.c src/hgscript/hgscript.c src/hgscript/timed.c \
  -o "$review/standalone-san"
ASAN_OPTIONS=detect_leaks=1:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 "$review/standalone-san"
gcc -std=c11 -g -O1 -Wall -Wextra -Werror -pedantic -fno-omit-frame-pointer \
  -fsanitize=address,undefined -fno-sanitize-recover=all -I src \
  tests/resource_sanitizer.c src/resource_loader.c src/disc_read.c -o "$review/resource-san"
ASAN_OPTIONS=detect_leaks=1:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 "$review/resource-san"

export HG_JAPAN_ISO="$PWD/$iso"
python3 -B -m unittest discover -s tests -v
python3 -B tools/hg_verify.py run --profile full
git diff --check 86a5670 HEAD
```

The full runner uses its checked-in inventory and default local inputs. The
separate unittest invocation uses the ISO to exercise the retail-only audio tests.
Private M20 owner scripts `observed-title.py` and `edges.py` were read from
`decomp-m20/local/m20-resume/` and executed via `PYTHONPATH=tools python3 -B`
with their input and output paths mapped into this worktree. The earlier M19
and M20 full receipts were SHA-256 checked. The selected tests and strict
GCC/Clang builds passed. Both compilers passed the connected smoke under
ASan/UBSan, and GCC passed the standalone HGScript and resource sanitizer
drivers. The first lookup probe called an isolated C function without
routing its three helper callbacks. The recheck ran the original EE lookup.
The ELF mapping check then needed to select the resident segment from 16
PT_LOAD entries. The first smoke fixtures also needed the original glyph
flag and the AREA1 selector bytes. The successful rechecks used the
corrected inputs.

**Final committed-source verification.** The separate discovery command passed 460/460 tests in 197.086 seconds with zero failures or skips. M18 passed 16,273/16,273 frames in its original external-loader configuration. The M17 lifecycle corridor passed 22/22 frames. The repository full profile accepted **79/79 gates** (74 passed and 5 expected rejections). Run `m12-20261124T181748Z-fmurluh1` took 3132.28 seconds with 0 reuses. All expected outcomes were reproduced. The receipt at `local/verification/m12-20261124T181748Z-fmurluh1/summary.json` has SHA-256 `206d3e244c264d8f371aa2cb8c41200529035bccd253dc69a7c453a16f5ab045`. It binds source digest `452259437eea70fd20141bd1b1b156d338385f3bea1561e37e5f5c0748961952` to `970e7acb1b87c1b8f7397b763d655730780199fa`.

**Next action.** Observe the original title overlay's activation, the first
renderer/voice requests and the reset in one fresh PCSX2 capture. Compare the
surrounding title scene scheduling with the verified script slice.
