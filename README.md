# Hungry Ghosts — PS2 executable research

Evidence and executable reconstruction for the original Japanese **SCPS-15046**.
This continues the existing `finakechi/Hungry-Ghosts---Decomp` repository.

Start with [START_HERE.md](START_HERE.md). Detailed findings and the continuation
point are in [MASTER_HANDOFF.md](MASTER_HANDOFF.md).

M1 establishes verified build identities and four disc-read functions. M2 adds
11 scheduler/request/PACK/AREA functions, including a complete bootstrap loading
operation with explicit dependencies. M3 adds a connected rebasing dependency, bringing coverage to 16 functions /
4,080 original bytes. The current suite has 81 synthetic tests, instruction differential checks,
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

M3 adds evidence-safety gates and connected resource rebasing; see [M3](docs/M3.md).
Synthetic tests now also require JDK 21 for the Java export transaction tests.

Private recovery tooling and verified GitHub integration are documented in
[docs/INTEGRATION.md](docs/INTEGRATION.md) and [docs/BACKUP.md](docs/BACKUP.md).
