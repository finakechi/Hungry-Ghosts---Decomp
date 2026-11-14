# Hungry Ghosts — start here

**Version 2.0 — 2026-11-14, America/New_York.** Detailed status:
[MASTER_HANDOFF.md](MASTER_HANDOFF.md). Read AGENTS.md first.

M2 reconstructs a complete bootstrap PACK request/worker/read/handle-publication
operation and the connected AREA selection/read/finalization helpers, retaining
explicit kernel/CDVD/fixup dependencies. **11 new functions / 2,736 bytes** join
M1's four disc-read functions: **15 functions / 3,868 bytes / 967 instructions**.
The final matching/reconstruction/recompilation/port outcome remains undecided.

The original ELF SHA-256 is
`a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61`.
Entry is `0x00100008`; PCSX2 CRC is `91AA4B28`. Separate original/Standard/Modern
build manifests and comparative changes are under evidence/. Original ISO hashes
and both patched ISO SHA-1 values match the pinned translation reference.

- [M2 C](src/resource_loader.c), [full M2 instruction evidence](analysis/resource_loader/functions.json),
  and [execution path / semantics](analysis/resource_loader/README.md).
- [M1 C](src/disc_read.c) and [M1 instruction evidence](analysis/disc_read/functions.json).
- [Function inventory](analysis/function-inventory.json) and [Ghidra map](analysis/ghidra-map.json).
- **49 synthetic tests**, **1,448 M1 differential checks**, **970 M2 randomized
  invocations + 12 end-to-end sequences**, sanitizers and **9 negative controls** pass.
- [PCSX2 activity](evidence/m2/runtime-loading.json): 2,147 samples show directory 0
  and bulk reads, loading phases, completion, five handles and arena pointers.
  [All 967 instruction words](evidence/m2/runtime-code.json) match runtime memory.
  These are sampled states/data, not a PC call trace or full-game equivalence.
- PACK contains **273 directories**, 2,541 outer members, 2,255 FLK5 signatures.
  The 271-versus-273 discrepancy is resolved for the measured Japanese image.

The existing repository was cloned as `decomp/` beneath the original-media
directory. Local branch: `research/m2-resource-loader`, based on M1 `d67d8ef`.
M2 implementation/evidence commit: `3ec079fc3df953f67462682f4a1e25565464475a` (local); the final handoff commit follows it.
Remote main remained `d3cc12e`; no open PRs, push, merge or publication.

Ignored `local/ghidra/HungryGhosts_JP_M1.gpr` preserves the initial import;
`HungryGhosts_JP_M1_Work.gpr` has M1's RAM correction and analyst labels.
Separate `HungryGhosts_JP_M2.gpr` has the new worker/wake discovery and M2 labels;
the memory blocks are unchanged. Inventory has **4,558 automatic candidates**,
including 15 reconstructed/model-tested and 12 partially analyzed entries.
Ghidra 12.1.4 / EE Reloaded 2.1.38 / `r5900:LE:32:default` were used. Original
media, BIOS, extracted files, private logs and captures remain outside Git.

```sh
python -m unittest discover -s tests -v
```

[DEVELOPMENT.md](DEVELOPMENT.md) gives exact commands. PS2DInfo remote main was
verified at `2e4a226e`, newer than the supplied `af62918c`; its tools are unchanged
between those revisions and are used directly. The older unrelated checkout was
not changed.

**Shutdown safety:** an unrelated Kate window reports unsaved changes. It was
preserved. The final safety recheck still reports modified=true: **shutdown was
not attempted and MainPC was left running**. M2 completion does not override this. Final gate/result receipt:
ignored `local/m2/final-report.json`.

Next: reconstruct real resource fixups and general PACK loader paths, observe an
AREA transition, and add PC-correlated runtime traces. No .mdebug or usable retail
symbols were found. Read the master's verification limits before changing status.
