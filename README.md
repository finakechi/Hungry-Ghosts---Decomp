# Hungry Ghosts — PS2 executable research

Evidence and executable reconstruction for the original Japanese **SCPS-15046**.
This continues the existing `finakechi/Hungry-Ghosts---Decomp` repository.

Start with [START_HERE.md](START_HERE.md). Detailed findings and the continuation
point are in [MASTER_HANDOFF.md](MASTER_HANDOFF.md).

M1–M4 establish disc reads, resource scheduling/loading and selected FLK5 fixup.
M5 reconstructs the connected AREA/PACK loaders and one AREA1 actor callback: the
original game replaces a previous AREA, executes/publishes the loaded callback,
and retains/reuses it across a later resource request. Coverage is 30 functions /
10,028 original instruction bytes, with 120 synthetic tests and bounded original
instruction/runtime comparisons. [M5](docs/M5.md) describes explicit engine/fixup
boundaries and the pending private-backup delivery gate. Whole-game equivalence
and compiler byte matching remain untested; no final rebuild/port track is selected.

```sh
python -m unittest discover -s tests -v
```

Python 3.10+ and a C11 compiler are sufficient for synthetic tests. Build products
go in ignored `local/build/`. No retail data, Ghidra or emulator is needed in CI.
For owner-local analysis and verification, see [DEVELOPMENT.md](DEVELOPMENT.md).

No game, BIOS, extracted assets or patch payload is distributed here. External
research sources and reuse limits are listed in [docs/sources.md](docs/sources.md).

M3 adds evidence-safety gates and connected resource rebasing; see [M3](docs/M3.md).
Synthetic tests now also require JDK 21 for the Java export transaction tests.

Private recovery tooling and verified GitHub integration are documented in
[docs/INTEGRATION.md](docs/INTEGRATION.md) and [docs/BACKUP.md](docs/BACKUP.md).
