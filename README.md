# Hungry Ghosts — PS2 executable research

Evidence and executable reconstruction for the original Japanese **SCPS-15046**.
This continues the existing `finakechi/Hungry-Ghosts---Decomp` repository.

Start with [START_HERE.md](START_HERE.md). Detailed findings and the continuation
point are in [MASTER_HANDOFF.md](MASTER_HANDOFF.md).

M1 identifies three local disc builds, establishes an isolated Ghidra executable
map, inventories 4,556 automatic function candidates, and reconstructs four
connected disc-read functions (1,132 original bytes). They pass 32 synthetic tests
and 1,448 differential checks with mocked CDVD operations. Runtime inspection
confirms their code bytes and initialized file records; gameplay equivalence and
byte matching remain untested. No final rebuild/port track has been selected.

```sh
python -m unittest discover -s tests -v
```

Python 3.10+ and a C11 compiler are sufficient for synthetic tests. Build products
go in ignored `local/build/`. No retail data, Ghidra or emulator is needed in CI.
For owner-local analysis and verification, see [DEVELOPMENT.md](DEVELOPMENT.md).

No game, BIOS, extracted assets or patch payload is distributed here. External
research sources and reuse limits are listed in [docs/sources.md](docs/sources.md).
