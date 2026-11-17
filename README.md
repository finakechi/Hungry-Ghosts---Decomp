# Hungry Ghosts — PS2 executable research

Evidence and executable reconstruction for the original Japanese **SCPS-15046**.
This continues the existing `finakechi/Hungry-Ghosts---Decomp` repository.

Start with [START_HERE.md](START_HERE.md). Detailed findings and the continuation
point are in [MASTER_HANDOFF.md](MASTER_HANDOFF.md).

M1–M6 connect disc reads, PACK/AREA loading, real resource processing and a
selected AREA1 actor callback. M6 independently verifies all 13 marked startup
PACKc6 members (1,184,000 transformed member bytes and a complete 1,228,800-byte
postprocessing buffer), publication, worker completion and AREA1 reuse. Coverage
is 34 functions / 10,992 original instruction bytes, with 126 synthetic tests.
[M6](docs/M6.md) records exact evidence levels, commands and remaining engine,
upload and numerical boundaries. Whole-game equivalence, compiler byte matching
and a final rebuild/port target remain unclaimed.

```sh
python -m unittest discover -s tests -v
```

The full synthetic suite requires **Python 3.10+, a C11 compiler, and JDK 21
with `javac`** for the Java export transaction test. Build products go in ignored
`local/build/`. No retail data, Ghidra or emulator is needed in CI. See the
[current quick start](DEVELOPMENT.md#current-quick-start--synthetic-checks-only)
for prerequisite checks, and the later sections for owner-local verification.

No game, BIOS, extracted assets or patch payload is distributed here. External
research sources and reuse limits are listed in [docs/sources.md](docs/sources.md).

M8 (merged via PR #7) adds a connected player-gameplay operation compared
against the original game; the figures above are the M6 summary. See
[START_HERE.md](START_HERE.md) and [docs/M8.md](docs/M8.md) for current status.
M9 (merged via PR #9) adds a held left-stick turn and the camera response that follows it,
compared frame by frame with the original game; see [docs/M9.md](docs/M9.md).

M3 adds evidence-safety gates and connected resource rebasing; see [M3](docs/M3.md).

Private recovery tooling and verified GitHub integration are documented in
[docs/INTEGRATION.md](docs/INTEGRATION.md) and [docs/BACKUP.md](docs/BACKUP.md).
