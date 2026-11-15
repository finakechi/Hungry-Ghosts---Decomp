# Hungry Ghosts — development and reproduction

**Current local baseline:** M7 on `research/m7-engine-preparation`, based on
verified GitHub main `280a1a296a877cc14709a6adca0505569f1a57c7` (2026-11-15).
[The M7 report](docs/M7.md) owns the selected engine-preparation boundary,
original-data commands and Resume here handoff. M7 is local and unpublished;
recheck actual HEAD, tool versions and private inputs before reproducing.

## Current quick start — synthetic checks only

**Requirements:** Python 3.10+, a C11 compiler (`cc`), and **JDK 21 with `javac`**
for the Java export transaction test. A Java runtime without `javac` is
insufficient. No ISO, BIOS, Ghidra, PCSX2, or PS2DInfo checkout is required for
this synthetic suite. From the **Hungry Ghosts repository root**:

```sh
git status --short --branch
git rev-parse HEAD
python3 --version
cc --version
java -version
javac -version
python3 -m unittest discover -s tests -v
git diff --check
```

Expected at the M7 local milestone: **136 tests** and a passing full suite when
those prerequisites are installed. [GitHub CI](https://github.com/finakechi/Hungry-Ghosts---Decomp/actions/runs/43675180294)
passed its two M6-head jobs (Python 3.10/3.14 with JDK 21 and C sanitizers).
That remote result does not prove a new local run passed. If a prerequisite is
missing, record the affected check as **blocked/unrun**, not passed; do not
install or reconfigure system software automatically.

## Original media and runtime checks — owner-local only

For original-game checks, establish the actual approved paths **before running**
the [M7 verification commands](docs/M7.md#commands-and-checks). The current routines
use `HG_ELF` (unmodified Japanese boot ELF), `HG_JAPAN_ISO` (original image),
`HG_PS2DINFO` (pinned shared-tools checkout), and, for emulator checks,
`HG_PCSX2`. Ghidra work additionally uses `HG_GHIDRA`; comparative build checks
use `HG_STANDARD_ISO` and `HG_MODERN_ISO`; an isolated emulator profile also
requires the user's `HG_BIOS`. Check each needed input hash separately; use
`evidence/local-inputs.json`, the game build manifests, and the shared
`tools/check_local_inputs.py` as applicable. Never assume any local paths from
historical reports.

Use **fresh, exclusive destinations under ignored `local/`** and preserve the
original media and existing Ghidra databases. The M6 report distinguishes
synthetic tests, original-code bytes, saved/retail runtime observations,
negative controls, and sanitizer checks. Without approved local inputs, those
owner-local tests are **skipped**, not a failed attempt or a simulated pass.
Private captures and recovered profiles are not included in the public clone.

## Older milestone procedures (preserved for reproduction)

The following M1–M5 command recipes and observations are dated milestone
material, **not** a claim about current MainPC paths, CI, backup status or the
next operation. For current work start with the sections above and `docs/M7.md`.

Run commands from the repository root in a POSIX shell. Originals stay read-only;
all extraction, databases, profiles, logs and build products go in ignored local/.
HGExport now reserves a fresh destination and publishes a completion manifest.
Legacy exports lack that guarantee; Python builders still use exclusive output files.
Use fresh output names when repeating a check. Do not overwrite existing databases.

## Synthetic checks

```sh
python -m unittest discover -s tests -v
git diff --check
```

The original M1 implementation used Python 3.10+ and `cc` with C11, and compiled
`src/disc_read.c` to `local/build/disc_read.so`. Its historical synthetic suite
did not yet include the later Java tests. The GitHub workflow was not remotely
run **during M1**; subsequent M6-head CI passed as stated above.

## Original and comparative inputs

Set `HG_JAPAN_ISO`, `HG_STANDARD_ISO`, `HG_MODERN_ISO` to the three owner-local
images; `HG_BIOS` to the absolute path of the owner-local BIOS; `HG_GHIDRA` to the
Ghidra installation directory; `HG_PCSX2` to the PCSX2 executable; and
`HG_PS2DINFO` to the shared repository. No actual private absolute path belongs in
tracked config or documentation. The original M1 checkout reported media just
above its repository; **do not assume this remains true** on MainPC or in a new
cloud checkout.

```sh
mkdir -p local/recheck local/logs local/ghidra
python tools/hg_identify.py "$HG_JAPAN_ISO" --build japan --output local/recheck/japan.json --extract-boot local/recheck/japan
python tools/hg_identify.py "$HG_STANDARD_ISO" --build english-standard --output local/recheck/english-standard.json --extract-boot local/recheck/english-standard
python tools/hg_identify.py "$HG_MODERN_ISO" --build english-modern --output local/recheck/english-modern.json --extract-boot local/recheck/english-modern
python tools/hg_pack_scan.py "$HG_JAPAN_ISO" --output local/recheck/pack.json
python tools/hg_overlay_scan.py "$HG_JAPAN_ISO" --output local/recheck/area.json
python tools/hg_compare_builds.py --japan local/recheck/japan/SCPS_150.46 --english-standard local/recheck/english-standard/SCPS_150.46 --english-modern local/recheck/english-modern/SCPS_150.46 --output local/recheck/comparison.json
```

The comparison defaults to the pinned evidence/builds manifests and verifies the
supplied ELF hashes. An unexpected revision must get its own manifest and explicit
analysis, not a forced comparison. ISO parsing deliberately rejects unsupported
multi-extent/interleaved files, malformed records, out-of-image extents and cycles.
The PACK scanner checks outer directory/member bounds, not full nested formats.

## Original-byte and model checks

```sh
python tools/verify_elf_evidence.py --elf local/recheck/japan/SCPS_150.46
python "$HG_PS2DINFO/tools/validate_evidence.py" analysis/disc_read/functions.json
python tools/verify_disc_read.py --elf local/recheck/japan/SCPS_150.46 --output local/recheck/differential.json
```

The first verifies actual ELF bytes/JAL targets. The second validates the shared
evidence format. The third compares four original functions against compiled C,
using the deterministic seed 0x15046 and mocked CDVD calls. These are different
checks. Neither the format validator nor compilation establishes equivalence.
The shared validator revision/hash used for M1 are pinned in docs/sources.md.

## Ghidra import and preserved working copy

The installed combination actually tested was Ghidra 12.1.4, EE Reloaded 2.1.38,
OpenJDK 21, language `r5900:LE:32:default`, compiler spec `default`.
Use new project names in reproduction; never add `-overwrite` to these commands.

```sh
"$HG_GHIDRA/support/analyzeHeadless" local/ghidra HG_Reproduce_Base -import local/recheck/japan/SCPS_150.46 -processor r5900:LE:32:default -cspec default -analysisTimeoutPerFile 600 -scriptPath ghidra_scripts -postScript HGExport.java local/recheck/exports/base 0x00100008 -log local/logs/reproduce-base.log -scriptlog local/logs/reproduce-base-script.log
```

Close the database, then copy it before the map correction:

```sh
python - <<'PY'
from pathlib import Path
import shutil
p=Path('local/ghidra')
if (p/'HG_Reproduce_Work.gpr').exists():
    raise SystemExit('Working project already exists; choose a new name')
shutil.copy2(p/'HG_Reproduce_Base.gpr',p/'HG_Reproduce_Work.gpr')
shutil.copytree(p/'HG_Reproduce_Base.rep',p/'HG_Reproduce_Work.rep')
PY
"$HG_GHIDRA/support/analyzeHeadless" local/ghidra HG_Reproduce_Work -process SCPS_150.46 -scriptPath ghidra_scripts -preScript HGMap.java -postScript HGExport.java local/recheck/exports/work 0x0011d830 0x0012fbf0 0x00130900 0x00131810 0x00131dd0 0x0011db30 0x0010e6d8 -log local/logs/reproduce-work.log -scriptlog local/logs/reproduce-work-script.log
"$HG_GHIDRA/support/analyzeHeadless" local/ghidra HG_Reproduce_Work -process SCPS_150.46 -readOnly -noanalysis -scriptPath ghidra_scripts -postScript HGExport.java local/recheck/exports/read_state 0x001318f0 0x001319c0 0x001319e0 0x00131bf0 -log local/logs/reproduce-read.log -scriptlog local/logs/reproduce-read-script.log
python tools/hg_context.py --elf local/recheck/japan/SCPS_150.46 --export-root local/recheck/exports --output local/recheck/context.json
python tools/hg_evidence.py --elf local/recheck/japan/SCPS_150.46 --export local/recheck/exports/read_state --context local/recheck/context.json --output local/recheck/functions.json --inventory-output local/recheck/inventory.json --map-output local/recheck/map.json
"$HG_GHIDRA/support/analyzeHeadless" local/ghidra HG_Reproduce_Work -process SCPS_150.46 -noanalysis -scriptPath ghidra_scripts -postScript HGAnnotate.java -log local/logs/reproduce-names.log -scriptlog local/logs/reproduce-names-script.log
```

HGMap and HGAnnotate guard the original ELF hash. Apply HGMap only once; existing
blocks cause refusal. Only the working copy is changed. HGExport itself does not
mutate the program. Preserve its original JSON, analysis logs and warnings in local/.
Original pseudocode is not promoted to verified source. The known extension rom0
conflict and suspect mirrors must not be used as verified hardware mappings.

## Isolated PCSX2 boot and read-only capture

PCSX2's `-datapath` adds a **PCSX2/** child. A minimal profile placed directly in
the supplied parent will be ignored and may open the setup wizard. M1 used the
existing PCSX2 2.8.2 AppImage; `-help` and `-version` use one leading hyphen.

Prepare a new ignored profile. The BIOS link is read-only input to the emulator;
memory cards are disabled and all generated state remains inside this profile.

```sh
mkdir -p local/runtime/repro/PCSX2/inis local/runtime/repro/PCSX2/bios
ln -s "$HG_BIOS" local/runtime/repro/PCSX2/bios/ps2-0150a-20001228.bin
cat > local/runtime/repro/PCSX2/inis/PCSX2.ini <<'INI'
[UI]
SettingsVersion = 1
SetupWizardIncomplete = false
StartPaused = false
PauseOnFocusLoss = false
StartFullscreen = false
ConfirmShutdown = false
[EmuCore]
EnablePatches = false
EnableCheats = false
EnableWideScreenPatches = false
EnableNoInterlacingPatches = false
EnablePINE = true
PINESlot = 28046
SaveStateOnShutdown = false
EnableDiscordPresence = false
[Filenames]
BIOS = ps2-0150a-20001228.bin
[EmuCore/GS]
Renderer = 12
AspectRatio = 4:3
[SPU2/Output]
OutputMuted = true
[MemoryCards]
Slot1_Enable = false
Slot2_Enable = false
INI
"$HG_PCSX2" -datapath "$PWD/local/runtime/repro" -logfile "$PWD/local/logs/reproduce-runtime.log" -batch -fastboot -- "$HG_JAPAN_ISO"
```

Let fast boot run without gameplay input. In another shell, set `HG_PINE_SOCKET`
to the runtime directory's `pcsx2.sock.28046`. Do not connect to another session's
socket or enable the same slot concurrently. After file lookup initializes:

```sh
python tools/hg_pine_read.py --socket "$HG_PINE_SOCKET" --output local/recheck/runtime.json --range 0x1318f0 184 --range 0x1319c0 28 --range 0x1319e0 492 --range 0x131bf0 428 --range 0x3eace0 72 --range 0x3f3a30 176
python tools/hg_runtime_verify.py --capture local/recheck/runtime.json --elf local/recheck/japan/SCPS_150.46 --manifest evidence/builds/japan.json --emulator "$HG_PCSX2" --profile local/runtime/repro/PCSX2/inis/PCSX2.ini --output local/recheck/runtime-verification.json
```

Only read/identity/status protocol operations are used. Running reads are
sequential, not atomic. The verifier checks the four instruction ranges and
initialized PACK/AREA LBA/size data, not gameplay behavior. Stop this isolated
emulator normally after the capture; do not terminate unrelated emulator sessions.

For actual behavioral validation, use the official debugger and reproducible
breakpoint/input sequences with recorded registers/memory around the four entries.
That is the next verification level, not an achieved part of M1.

## M2: connected resource-loading checks

Use the original inputs and environment variables from the earlier sections.
The actual shared-tool revision used is
`2e4a226e00d33ab35391aacade71b55826e5f52f`. It is newer than the supplied
af62918c revision; tools/tests are unchanged between them. The older unrelated
local PS2DInfo checkout was not modified. An ignored reference checkout supplies
the existing tools. If absent, clone it into local/references/PS2DInfo, inspect its
status, and select that revision; never reset an existing user checkout.

The exact final test commands used, with private paths represented by variables:

```sh
python "$HG_PS2DINFO/tools/check_local_inputs.py" --manifest evidence/local-inputs.json
python "$HG_PS2DINFO/tools/verify_elf_evidence.py" --require-all-bytes "$HG_ELF" analysis/disc_read/functions.json
python "$HG_PS2DINFO/tools/verify_elf_evidence.py" --require-all-bytes "$HG_ELF" analysis/resource_loader/functions.json
python "$HG_PS2DINFO/tools/validate_evidence.py" analysis/disc_read/functions.json
python "$HG_PS2DINFO/tools/validate_evidence.py" analysis/resource_loader/functions.json
python -m unittest discover -s tests -v
python tools/verify_disc_read.py --elf "$HG_ELF" --output local/m2/recheck-m1.json
python tools/verify_resource_loader.py --elf "$HG_ELF" --ps2dinfo "$HG_PS2DINFO" --output local/m2/recheck-resource.json
cc -std=c11 -Wall -Wextra -Werror -pedantic -O1 -g -fsanitize=address,undefined -fno-sanitize-recover=all -Isrc tests/resource_sanitizer.c src/resource_loader.c src/disc_read.c -o local/m2/resource_sanitizer
ASAN_OPTIONS=detect_leaks=1:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 local/m2/resource_sanitizer
python -m compileall -q tools tests
git diff --check
```

`HG_ELF` and `HG_JAPAN_ISO` must be exported for the shared input checker (or placed
in its ignored literal config). No extra Python packages are needed. Synthetic
tests require neither shared repository nor retail files; the two retail
differential commands are separate. M2 adds the observed integer instructions to
the strict M1 interpreter and directly uses the shared ELF mapping parser.
All kernel/CDVD/fixup operations in differential tests are mocks. The harness
compares defined external arguments, descriptors, ordinary RAM and scratchpad,
excluding original stack scratch bytes and undefined return registers.

To reproduce Ghidra discovery, first close the M1 working database and copy its
.gpr and .rep into a **new** M2 project name (the copy procedure above applies).
Do not replace either preserved M1 project. Then use the separate copy:

```sh
"$HG_GHIDRA/support/analyzeHeadless" local/ghidra HungryGhosts_JP_M2_Repro -process SCPS_150.46 -noanalysis -scriptPath ghidra_scripts -preScript HGDiscoverM2.java -postScript HGExport.java local/m2/repro/exports/original 0x001431f0 0x00143360 0x001434f0 0x00130150 0x00130170 0x00130220 0x001303e0 0x001306a0 0x0012fbc0 0x00131810 0x00131dd0 0x00121810 0x00143240 0x001432c0 0x0011df10 0x0011df80 0x00130900 0x00119ac8 -log local/m2/repro/ghidra.log -scriptlog local/m2/repro/ghidra-script.log
python tools/hg_m2_evidence.py --elf "$HG_ELF" --ps2dinfo "$HG_PS2DINFO" --exports local/m2/repro/exports --output local/m2/repro/functions.json
python tools/hg_m2_context.py --elf "$HG_ELF" --ps2dinfo "$HG_PS2DINFO" --exports local/m2/repro/exports --output local/m2/repro/context.json
```

Create the output/log directories first. HGDiscoverM2 only discovers the two
missing original entries. A later HGAnnotateM2 run on this copy assigns analyst
names; it does not claim the C host environment is the original ABI. Main memory
blocks are unchanged from M1. The current raw Ghidra exports stay in ignored local/.

For runtime activity, create a fresh profile from the M1 isolated INI, with all
optional patches still false and PINESlot changed to **28047**. As above,
`-datapath` appends PCSX2/. M2 used local/m2/runtime/PCSX2/inis/PCSX2.ini and an
ignored BIOS link. Start the sampler in one shell before launching the emulator
in another shell. It waits up to 30 seconds for original serial/CRC identity:

```sh
python tools/hg_loading_capture.py --socket "$HG_PINE_SOCKET" --output local/m2/repro/boot-loading-raw.json --seconds 18 --hz 120
```

```sh
"$HG_PCSX2" -datapath "$PWD/local/m2/runtime" -logfile "$PWD/local/m2/repro/pcsx2.log" -batch -fastboot -- "$HG_JAPAN_ISO"
```

Do not supply gameplay input or a save state. After boot sampling has completed:

```sh
python tools/hg_pine_read.py --socket "$HG_PINE_SOCKET" --output local/m2/repro/post-loading-raw.json --range 0x3f3b00 2048 --range 0x3f4300 32 --range 0xb00000 16 --range 0x3f4d3c 36 --range 0x3f8e00 80 --range 0x3eace0 72
python tools/verify_loading_runtime.py --raw local/m2/repro/boot-loading-raw.json --post local/m2/repro/post-loading-raw.json --image "$HG_JAPAN_ISO" --profile local/m2/runtime/PCSX2/inis/PCSX2.ini --output local/m2/repro/runtime-verification.json
python tools/hg_loading_code_check.py --socket "$HG_PINE_SOCKET" --elf "$HG_ELF" --ps2dinfo "$HG_PS2DINFO" --record analysis/disc_read/functions.json --record analysis/resource_loader/functions.json --private-output local/m2/repro/runtime-code-raw.json --output local/m2/repro/runtime-code-check.json
```

Raw captures remain local. The runtime verifier checks actual loading phases,
directory/bulk requests, original directory bytes, calculated handles and arena
pointers. It records the transformed FLK5 +08 field rather than claiming it is
unchanged. It rejects missing observations; rerun a fresh capture if the relevant
operation was missed. Sampling is sequential and is not a breakpoint/PC trace.
Stop only the emulator instance that this session created after its captures.

Negative controls run offline on disposable copies:

```sh
python tools/hg_m2_negative_controls.py --elf "$HG_ELF" --ps2dinfo "$HG_PS2DINFO" --raw local/m2/repro/boot-loading-raw.json --post local/m2/repro/post-loading-raw.json --image "$HG_JAPAN_ISO" --profile local/m2/runtime/PCSX2/inis/PCSX2.ini --output local/m2/repro/negative-controls.json
```

The expected result is nine detected defects. A compile error or unexpected
exception does not count as detecting a C behavioral defect. Positive source,
retail media, BIOS and databases are never mutated by these controls.

## M3 safety and reconstruction

See [docs/M3.md](docs/M3.md) for current commands and limits. Select one explicit
export snapshot; ambiguous multi-snapshot roots now fail. Runtime reanalysis CLI
additionally requires `--emulator` and authenticates the full original image.
`hg_check_binding.py --record RECORD --input LABEL PATH ...` checks current source
and all labeled inputs against a bound receipt. JDK 21 is required by safety tests.

## GitHub integration and independent private recovery

[Backup procedures](docs/BACKUP.md) cover configuration, dry-run, source validation,
additive private Drive upload, full downloaded verification and isolated restore.
`tools/hg_publication_audit.py` audits unpublished historical objects before push.
The existing synthetic CI discovers `tests/test_backup.py` without Drive credentials
or proprietary fixtures. Private IDs/authentication stay beneath ignored `local/`.
See [integration verification](docs/INTEGRATION.md) for actual results and limits.

## M4: combined FLK5 processing

[docs/M4.md](docs/M4.md) records exact original-byte, differential, synthetic,
sanitizer and private runtime commands plus the repeatable EE breakpoint sequence.
Use `hg_resource_worker_fixed(env, frame)` to resolve fixup/helper boundaries
through the existing bus; reserve aligned guest scratch as specified in the API.
The old worker retains M2's isolated external seam for regressions. New packets
are under `analysis/resource_fixup/` and results under `evidence/m4/`.

## M5: AREA1 loading, execution and retention

[docs/M5.md](docs/M5.md) gives the recovered stopping point, exact original-byte,
differential, runtime reanalysis and sanitizer commands. The M5 runner keeps the
resident ELF packet separate from its raw AREA1 instruction packet. Runtime
reanalysis uses the recovered isolated profile, 19 manually transcribed debugger
pairs and the resumed retention/reuse captures; it never infers PCs from PINE.
The Drive transport gate was unresolved **during the original M5 session**;
later independent delivery/recovery and merged PR #3 are recorded in
[START_HERE.md](START_HERE.md)
and [docs/M5.md](docs/M5.md). Preserve historical receipts; do not carry
that old blocker into current status.

## M6: real selected PACK processing

[docs/M6.md](docs/M6.md) gives the complete verified commands and capture sequence.
Extend the existing fixup runner with `--geometry-exports local/m6/geometry-export`
and `--image "$HG_JAPAN_ISO"`; its M4 mock-based cases stay separate from 565 new
M6 real-processing comparisons. Extend the existing AREA runtime runner with
`--pack-runtime local/m6/captures2 --pack-profile local/m6/capture2-profile.ini`.
That private observation set validates all 13 actual fixups, complete payload,
publication and fresh AREA1 reuse. Runtime and original-data checks are local
only; synthetic CI uses neither media nor captures. Snapshot/review delivery is
recorded separately in the PR; preserve all prior snapshots.

## M7: downstream engine directory preparation

The existing fixed worker now resolves retained/context/indexed preparation. Use
`hg_resource_prepare_engine_fixed(env, task, frame)` only for the measured pair
after the kind-3 alternative loader and phase reset; it shares the worker's
resolver and guest scratch contract. Audio/IOP remains an explicit callback under
the separately checked resource-memory contract. See [M7](docs/M7.md) for the
precise boundary; actor construction is not implied.

```sh
python -B tools/verify_engine_preparation.py --elf "$HG_ELF" --ps2dinfo "$HG_PS2DINFO" --output local/m7/repro-instructions.json
python -B tools/verify_engine_runtime.py --elf "$HG_ELF" --image "$HG_JAPAN_ISO" --runtime local/m7 --ps2dinfo "$HG_PS2DINFO" --emulator "$HG_PCSX2" --profile local/m7/capture-profile.ini --contract-profile local/m7/contract-profile.ini --output local/m7/repro-runtime.json
```

The runtime command needs the private M7 observation bundle and both frozen
profiles. A public-only checkout must report that gate SKIPPED when unavailable.
Use fresh outputs on repetition. Synthetic CI needs none of those inputs.
