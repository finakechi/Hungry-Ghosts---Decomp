# Downstream engine directory preparation (M7)

This operation is a pair of resident directory-control functions, not a direct
actor constructor. The worker's kind-2/c6 path first loads c6, optionally loads
5/6, converts to kind 3, loads the next alternative directory (c7 in the observed
startup), clears phases, then calls `130020 -> 12fe90`. Kind 8 calls only 130020.
The fresh startup trace observes c6 return, directory 5, c7, then the pair loading
6b, 4c and 85, worker completion, and a later AREA1 state-0 callback.

## Original bodies

| Entry | Symbol | Words | Body ranges, exclusive ends |
|---|---|---:|---|
| 0012fcf0 | hg_resource_load_indexed | 57 | 12fcf0–12fdd4 |
| 0012fde0 | hg_resource_load_context | 41 | 12fde0–12fe84 |
| 0012fe90 | hg_resource_prepare_context | 32 | 12fe90–12fefc; 12ff00–12ff14 |
| 00130020 | hg_resource_prepare_retained | 74 | 130020–130084; 130088–1300fc; 130100–130150 |

[functions.json](functions.json) records authentic bytes/ranges and read-only
Ghidra snapshot hashes. 204 words / 816 bytes join M1–M6: 38 functions / 2,952
words / 11,808 instruction bytes. These are reconstruction counts, not compiler
byte matches. Shared PS2DInfo checks the actual original ELF PT_LOAD mapping.

## Call and side-effect map

- `130020`: enter if scratch word 700000c4 is 2 or byte 3fa1ba is nonzero.
  The desired retained-directory byte is 4d for state 3fa1b0=2, otherwise 4c.
  A matching byte at 3f9fef suppresses both reads and still publishes ready=1
  at 46e10c. A miss loads 4a for state 0/3/3, otherwise 6b. It reloads the state
  after this load, sets task+44 to 4d/4c, publishes its low byte at 3f9fef,
  resets task+38..3f, loads again, then sets ready=1. Each load is 130900.
- `12fe90`: only scratch context 2 enters. It clears the three halfword ring
  entries at 46e130/132/134 and ring index at 46e136. Mode 46e12d=2 with byte
  3fa202 != ff selects `(byte|200) -> 12fcf0`; otherwise it calls 12fde0.
- `12fde0`: byte 3fa201=ff skips. State 3fa200 below 3 indexes 3e01c0 with
  `kind*12 + sub*4`; larger kinds index 3e01e4 with the original `kind*4`.
  It stores the directory at task+44. ffffffff skips the load; other IDs clear
  phases and call 130900. The fresh selected table yields 85.
- `12fcf0`: the arithmetic right-shifted high 24 bits select a pointer at
  `3e0200 + ((signed(id)>>8)-2)*4`; low byte selects a word in that table.
  It stores the resolved directory at task+44. ffffffff or an exact u32/u16
  match to the current ring entry skips. Otherwise phases clear, the halfword
  index increments (including truncation) and values >=3 reset to zero; the
  halfword directory publishes in the new slot before the general loader.
- `130900` and its M1–M6 dependencies calculate directory reads, resource
  processing, handle publications and arena effects. Mode 5 consumes special
  handle 51 to sector-align the next arena at 3f4d48. This is why 6b's audio
  completion endpoint is material to the selected operation.

No new VU, IOP or upload implementation is introduced. The existing geometry
finite-normal and guest-scratch contracts still apply. The main packet's new
functions use integer instructions only, with original delay slots and widths.

## Narrow external audio contract

External 156a50 may yield zero while busy. For the two observed one-bank inputs,
its successful v0 is `(input + word(input+10) + 40) & ffffffc0`, with word wrap.
Original 1573c0–1573f4 establishes this endpoint. The descriptor array starts at
`input+20+word(input+8)*10`; its +4 field locates the SSHd bank. Original
2b4df0–2b4f68 initializes its six +30..44 pointer words from +10..24 offsets:
ffffffff becomes zero; other offsets add the bank address. The registration
wrapper is 2b5318–2b53a0. These audio bodies were inspected read-only, not added
to the reconstructed C-function count or copied from SDK source.

The verification callback calculates these **resource-facing** effects from the
original input. It does not replay saved output. A second fresh boot separately
captures both full original inputs, successful returns and full containers;
only six words at resource+60..74 differ in each. Bank tables, audio globals,
RPC, IOP DMA/results, playback and busy timing remain owned by the external audio
interface and are excluded from the engine-state equivalence claim. This is not
an implementation of the audio engine.

The large 6b transfer is scratch after its retained prefix: 4c legitimately
reuses it from 100a800. Likewise 85's bulk starts at the audio endpoint f78880,
inside its prior special-member read. The runtime comparison checks the final
union of read sectors, not incompatible earlier complete-file expectations.

## Actor relationship and limits

After worker completion, the original engine separately dispatches AREA1 callback
a8a070 with actor 4d8150, state 0. No direct edge from 130020/12fe90 to actor
construction was found; their reconstructed callees are directory loaders. The
later callback and its bytes/state are captured as a continuation boundary, not
promoted to completed actor construction or activation. Resident 18a330's
six-argument setup, matrix/actor dependencies, and publication 18c280 remain the
existing explicit AREA seams. M7 does not invent an actor call in the worker.

[docs/M7.md](../../docs/M7.md) gives commands, provenance and acceptance scope.
All raw captures, original resources, decompiler output and databases stay private.
