# Selected PACKc6 finalization evidence

Four original resident functions add 241 words / 964 bytes. The packet preserves
actual discontinuous body ranges and original instruction bytes from read-only
Ghidra exports. Shared PS2DInfo verifies their file-backed ELF mapping; live paused
memory independently matches all 241 words. Raw resource payloads are not ELF code.

See [M6](../../docs/M6.md) for the selected operation, per-member classifications,
exact commands, evidence provenance and numerical/engine boundaries. The existing
`verify_resource_fixup.py --geometry-exports ... --image ...` runs 565 new bounded
comparisons, including every original-disc marked member with real dependencies.
`verify_area_runtime.py --pack-runtime ... --pack-profile ...` compares calculated
outputs against the independent original-game checkpoints.

All 13 outputs, 16 geometry calls and 12 texture entries are checked. Upload is
not reached. Finite-normal geometry arithmetic is verified; exceptional FP flags,
VU timing and unrelated engine behavior are excluded. No private asset, raw
capture, Ghidra database, media, BIOS or emulator binary is published.
