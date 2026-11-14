# Research sources and reuse boundaries

Observed 2026-11-14. Source-inspected claims below are separate from original-byte
measurements in evidence/. No external project source was copied into the public
implementation. Locally retained reference files are ignored. No patch payload,
translation, font, executable or asset from either reference is distributed.

| Source | Pinned revision | Use and licensing observation |
|---|---|---|
| [HungryGhostsFanTranslation/hgtools](https://github.com/HungryGhostsFanTranslation/hgtools/tree/ef12c410b29276aa9e3170517a292feb3dae8b86) | `ef12c410b29276aa9e3170517a292feb3dae8b86` | Inspected pack.py and hgpack.ksy. No LICENSE file in the complete inspected tree. Used as format leads, not copied implementation. |
| [MordOfSwoonlight/HungryGhostsPS2English](https://github.com/MordOfSwoonlight/HungryGhostsPS2English/tree/f65691249b2978c12ef750f93565bab21248b683) | `f65691249b2978c12ef750f93565bab21248b683` | Inspected patch README and original-build pnach control leads. No LICENSE file in the complete inspected tree. Main still resolved to this revision when checked. No payload or source reused. |
| [PS2DInfo](https://github.com/finakechi/PS2DInfo/tree/ab295d765728700f107321c5771b62783bd2e83c) | `ab295d765728700f107321c5771b62783bd2e83c` | Local source inspected; its existing validator was run as an external command. Validator SHA-256: `66fb9db3fae3675ca55232306363d6db8914f6aa28353b379caa52cc5be4e505`. No shared tool was copied/forked here. |
| [Ghidra Emotion Engine Reloaded](https://github.com/chaoticgd/ghidra-emotionengine-reloaded) | Installed changelog 2.1.38 / Ghidra compatibility 12.1.4 | Installed source archive, ELF loader and language definition inspected. Bundled LICENSE is Apache 2.0. Used as installed tooling; jar hash in toolchain record. |
| [PCSX2 debugger documentation](https://pcsx2.net/docs/advanced/debugger/) | Read 2026-11-14 | Explains EE/IOP debugger views and breakpoints; no debugger call trace is claimed for this milestone. |
| [PCSX2 PINE.cpp](https://github.com/PCSX2/pcsx2/blob/v2.8.2/pcsx2/PINE.cpp) | Tag `v2.8.2` | Inspected message framing and read/identity opcodes; GPL-3.0+ source. Client independently implements the published wire protocol and no mutation operation. |

The hgtools Python parser uses 273 directory slots; its Kaitai template uses 271.
The independent retail scanner establishes the count for this original image.
HGScript decoding in hgtools is a data-format tool, not EE executable decompilation.

The translation README reports original SHA-1
`e765a2db962bea182f47e768c7ef67876947cbb6`, MD5
`251e818a9b8d8c3d9a32024446bf83dc`, CRC32 `9d898274`, Standard SHA-1
`5992a8ad15050b16bd6268d35b3a6b36c9829d91` and Modern SHA-1
`698241df995f137f4d813bb646d20970a38950ce`. Those values were compared with the
owner's local media, not substituted for measuring it. A historical hgtools
SHA-256 lead also agrees with the measured original image.

The historical November 13 research handoff supplied useful leads and explicitly
had no retail-byte or executed-test evidence. It is context; current evidence
records and test results supersede its corresponding unknowns. No license for
this project's own work has been selected by the user in this milestone.
