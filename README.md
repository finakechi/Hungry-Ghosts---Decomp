# Hungry Ghosts — PS2 executable research

Evidence and executable reconstruction for the original Japanese **SCPS-15046**.
This continues the existing `finakechi/Hungry-Ghosts---Decomp` repository.

Start with [START_HERE.md](START_HERE.md). Detailed findings and the continuation
point are in [MASTER_HANDOFF.md](MASTER_HANDOFF.md).

M1 establishes verified build identities and four disc-read functions. M2 adds
11 scheduler/request/PACK/AREA functions, including a complete bootstrap loading
operation with explicit dependencies. Combined coverage is 15 functions / 3,868
original bytes, with 49 synthetic tests, instruction differential checks,
sanitizers and negative controls. PCSX2 captures substantiate actual bootstrap
loading and code/data identity. Full-game equivalence and byte matching remain
untested. No final rebuild/port track has been selected.

```sh
python -m unittest discover -s tests -v
```

Python 3.10+ and a C11 compiler are sufficient for synthetic tests. Build products
go in ignored `local/build/`. No retail data, Ghidra or emulator is needed in CI.
For owner-local analysis and verification, see [DEVELOPMENT.md](DEVELOPMENT.md).

No game, BIOS, extracted assets or patch payload is distributed here. External
research sources and reuse limits are listed in [docs/sources.md](docs/sources.md).
