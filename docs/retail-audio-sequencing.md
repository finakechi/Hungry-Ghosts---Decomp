# Retail EE audio sequencing and voice allocation

**2026-11-28 · Japanese SCPS-15046.** The [M24 dependency scan](../evidence/m24/reference-map.json)
and [M22 transport comparison](../evidence/m22/retail-check.json) reproduced
byte-for-byte. The M26 timing verifier reproduced its local receipt from 23
ELF anchors and 12 M22 PCSX2 v2.8.2 saves. The M26 branch still points at
`7191ca7402b5c8f0d5a58dbd38c62d099470c667`. Its verifier and timing receipt
are working files, with SHA-256 IDs
`2420a591afe6dcf3a737dbc6d8ddc59e1924c986802cea64f805f1a8d8af7832`
and `149217648f474b009d5322f7d3a8f9eb2e445ee8014ebdfed92b455bba77058a`,
respectively. The verifier independently pins the four detailed saves.
The remaining eight save digests match the existing local M26 receipt.
The M22 verifier separately pins and compares the four detailed saves and
its earlier reference save.

## Original instruction paths

| Boundary | Verified wiring and state |
| --- | --- |
| VBlank-End to scheduler | Boot code registers `0011df80` at INTC 3 and `0011df10` at INTC 2. Audio init `00156870` sets the scheduler divisor/rate field at `00a6c3c4` to 60 and binds `001566c0` with thread priority argument `0x64`. The INTC 3 handler checks `003f6db0 == 1` and forwards the thread ID from `003f6db4` through `00104e10` to the interrupt-context wake syscall. The audio thread sleeps, checks RPC state through `002ad118` and runs `002a7450` on the idle path. |
| Slot 1, `002add90` | Command state 1 updates the soft-double accumulator +0x28 using step +0x20. Processing compares it with the next threshold +0x30, maintains cursor +0x40 and running status +0x48, uses the status table at `003bd170`, and advances the threshold from variable-length deltas. The producer sends channel bytes through `002a95c8`. State 2 clears to state 0 when `002a9d18` returns zero. |
| Slot 2, `002a96b8` | Walks channels with stride `0x9e4`. The ring starts at channel +0x58, its producer and consumer indices are at +0x258/+0x25c, and the latter wraps with mask `0x1ff`. The consumer stages bytes at +0x260, dispatches by status/event length through the tables beginning at `003bd078`, and handles the `0xf7` marker separately. An empty ring alone leaves the channel allocated. Closing +0x10 and zero voices from `002b0cd8` permit the slot at +0x00 to become −1 and the closing flag to clear. |
| Note handler, `002ab508` | Staged velocity byte zero takes the tail path to `002b1f20`. The nonzero path records note, velocity and bank index, resolves the instrument through `002b50e0`, and selects a 16-byte tone by subtracting the instrument's low note. Channel mode 1, a missing lookup or a note below the low bound can exit before allocation. A tone field also gates an earlier release through `002b2030`. The remainder configures the selected voice, calculates its sample address through `002b1188`, and tail-dispatches key-on preparation to `002b2178`. |
| Allocation and ownership | The note handler calls `002ae9b8` first, then `002afed8` on −1, then `002af1d0` on a second −1. The free search selects a 24- or 48-voice span according to class and starts from a cursor-derived position. `002b1098` increments `00a832a0`, stamps its previous value in the selected `0xf0`-byte record at `00a832b0` and writes active flag, class and ownership fields. Note preparation passes class 2 to the claim call. The callsite has no further −1 guard after the last fallback. Establishing its return invariant, fallback ordering and ties requires separate work. |
| Release, retirement and RPC | `002b2030` programs release ADSR through `002b1668(v,0,8)`, sets a pending key-off bit and writes −1 to the voice's +0x1c. Slot 3 `002b2438` ages voices at +0x20 and tests age ≥3 with the `002ad0e8` ENVX reply <2 before retirement. It enqueues wet, noise, key-on, then key-off using opcodes `09, 0a, 07, 08`. Slot 4 `002ad138` services SG2IOP RPC. Channel and command retirement are distinct checks on later scheduler passes. |

## Saved runtime measurements

- The 12 saved boundaries span 5,618 guest VBlanks and the same delta in
  scheduler counter `00a6c3c0`. VBlank-Start `0095e300` is one ahead at
  each sampled boundary. All 16 slot counters agree with the scheduler
  counter at those boundaries. This supports one scheduler pass per
  VBlank-End while RPC is idle on the sampled route. IRQ-to-thread latency
  and intermediate wake outcomes remain unmeasured.
- Cursor-down, cursor-up and confirm occur at scheduler counts 6694,
  6728 and 6762. In the cursor-up save, voice 8 still belongs to the
  cursor-down command (owner 0, note 82). Voices 9 and 10 have owner 1
  and notes 84 and 83. In confirm, voices 6–8 are idle and 16–17 are
  releasing with nonzero ENVX. The allocation cursor at title-input,
  down, up and confirm is 6, 8, 11, 18. Other cue classes and contention
  are outside those samples.
- The cursor-down command's step is 8.0 and next delta is 72.0 logical
  ticks. Ages 15 and 6 for voices 6 and 7 are consistent with nine
  scheduler passes between adjacent notes. Treat that spacing as a
  bounded inference for the selected cue. The saved pending masks are
  zero at the sampled boundaries. Exact intra-frame service order
  depends on the instruction path and a future trace.
- Sub-frame timing, RPC-busy behavior under load, fallback contention,
  streaming dialogue and hardware ADSR curves remain open. The M24 HSQ1
  harness's 1-tick/800-frame convention and host PCM outputs characterize
  its independent model.

## Reproduction

From Decomp run the M24 map with `"$HG_ELF"` and
`--check evidence/m24/reference-map.json`. Use the M22 commands with the
pinned inputs in [M22 reproduction](M22.md#reproduction-and-verification).
From the M26 worktree, after checking the two working file hashes above:

```sh
python3 -B tools/hg_audio/m26_reference_timing.py --elf "$HG_ELF" \
  --live "$M22_SAVES" --check evidence/m26/reference-timing.json
```

Use the existing [R5900 decoder](../tools/hg_audio/disasm.py) on the
authenticated ELF to inspect the additional consumer, notes and allocator
branches. Selected ranges were `2a96b8:2a9970`, `2add90:2ae030`,
`2ab508:2abb30`, `2ae9b8:2aedb0`, `2afed8:2b0540`, `2af1d0:2af750`
and `2b1098:2b1188`. The local listings contain original words and stay
under ignored storage.
