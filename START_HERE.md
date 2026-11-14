# Hungry Ghosts — start here

**Version 1.0 — 2026-11-14, America/New_York.** Detailed status:
[MASTER_HANDOFF.md](MASTER_HANDOFF.md). Read AGENTS.md first.

M1 establishes original Japanese **SCPS-15046** identity, a usable isolated Ghidra
boot-executable map, **4,556 automatic function candidates**, and **four connected
disc-read reconstructions** covering **1,132 original bytes / 283 instructions**.
The final matching/reconstruction/recompilation/port outcome remains undecided.

The original ELF SHA-256 is
`a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61`.
Entry is `0x00100008`; PCSX2 CRC is `91AA4B28`. Separate original/Standard/Modern
build manifests and comparative changes are under evidence/. Original ISO hashes
and both patched ISO SHA-1 values match the pinned translation reference.

- [Reconstructed C](src/disc_read.c) and [complete instruction evidence](analysis/disc_read/functions.json).
- [Function inventory](analysis/function-inventory.json) and [Ghidra map](analysis/ghidra-map.json).
- **32 synthetic tests** and **1,448 mocked-CDVD instruction differential checks** pass.
  The synthetic suite also passes from a clean export of the staged files.
- [PCSX2 observation](evidence/runtime-boot.json) verifies original code bytes and
  initialized PACK/AREA records; gameplay semantics were not runtime verified.
- PACK contains **273 directories**, 2,541 outer members, 2,255 FLK5 signatures.
  The 271-versus-273 discrepancy is resolved for the measured Japanese image.

The existing repository was cloned as `decomp/` beneath the original-media
directory. Local branch: `research/m1-executable-foundation`; baseline main:
`d3cc12e7cffd4bcbdd07a742a0e2f0423042b884`. No publication was performed.

Ignored `local/ghidra/HungryGhosts_JP_M1.gpr` preserves the initial import;
`HungryGhosts_JP_M1_Work.gpr` has the RAM correction and saved analyst labels.
Ghidra 12.1.4 / EE Reloaded 2.1.38 / `r5900:LE:32:default` were used. Original
media, BIOS, extracted files, private logs and captures remain outside Git.

```sh
python -m unittest discover -s tests -v
```

[DEVELOPMENT.md](DEVELOPMENT.md) gives reproducible local retail/Ghidra/runtime
commands. Next: connect scheduler and PACK/AREA loaders to the reconstructed read
group; recover the MWo3 overlay layout and CDVD dependencies; capture runtime call
transitions. Read the master's evidence limits before changing verification labels.
