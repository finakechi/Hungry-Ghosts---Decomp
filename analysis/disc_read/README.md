# Disc-read group: reconstruction notes

Original reference: Japanese ELF SHA-256
`a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61`.
[functions.json](functions.json) is the complete instruction evidence for four
connected entries, 283 instructions / 1,132 bytes. Names are analyst assignments.
[C](../../src/disc_read.c) uses host callbacks to make external effects testable;
its extra context parameters are not the original ABI.

## Connection and state

`hg_read_setup` (`001319c0`) stores a file-record EE pointer, destination EE
address, byte offset and length. It resets words +00 and +04, leaving derived
fields +18..28 untouched. `hg_read_poll` (`001318f0`) operates on singleton
`003eacf0`, using its **signed low state byte**, not the whole word.

State byte 0 with length 0 returns 1 without issuing anything. Otherwise start
(`00131bf0`) calculates a range and attempts an issue. Its -1 result leaves poll
in state 0; any other result increments the low byte to 1. In state 1, continue
(`001319e0`) returns 0 for pending work, 1 for completion, or 2 for an error.
Poll returns 1 on completion and decrements state 1 back to 0 after an error,
causing a fresh whole-request start on a later poll. Other low-byte values return
0 without modifying the state. High three bytes survive byte increments.

| Offset | Recovered field |
|---|---|
| +00 | State word; poll uses low byte |
| +04 | Last completion error |
| +08 | EE pointer to file record `{LBA, byte_size, ...}` |
| +0c | Original destination address |
| +10 | Signed byte offset |
| +14 | Signed byte length; negative means whole file size |
| +18 | Next LBA |
| +1c | Total sector count |
| +20 | Issued sector count |
| +24 | Next destination |
| +28 | Between-chunks flag |

The caller `00130900` sets up 2,048-byte PACK directory reads with byte offset
`directory_id << 11`, then polls completion before interpreting `003f3b00`.
`00131810` uses the same setup/poll pair for AREA files, length -1.
These callers were analyzed from local Ghidra exports and actual ELF words;
their complete implementation has not been reconstructed.

## Arithmetic and memory details checked against instructions

- `001319d8` clears the error word in the **JR delay slot**. Returning early
  before this store would change the recovered setup behavior.
- The zero-length branch at `00131934` jumps directly into the epilogue, skipping
  `move v0,s1` at `00131998`; v0 is still 1. This explains the return despite
  s1's usual pending value of 0.
- Signed offsets are divided by 2,048 with truncation toward zero: negatives
  receive +0x7ff before arithmetic shift. This does not implement arbitrary
  byte-aligned subfile extraction and does not subtract offset from whole length.
- A negative length reads the file's unsigned size, adds 0x7ff with 32-bit wrap,
  then uses a logical shift. Nonnegative lengths follow the actual addiu / bgez /
  arithmetic-shift sequence, including signed overflow-wrap edge cases. The C
  version uses unsigned storage and explicit signed interpretation to avoid C
  signed-overflow undefined behavior.
- Globals `003f2c80` or `003f2fa0` being nonzero limit an issue to **128 sectors**.
  Each chunk advances next LBA and destination (count × 0x800). Without either
  flag the routine issues all remaining sectors, leaving the next pointers as
  observed in the original. Flags' gameplay meanings are unknown.
- Global `003eace4` blocks issue only when exactly **1**. Value 2 still permits
  issuing. `003eace8` marks an issue active and clears on successful completion;
  the error path retains it. Busy paths short-circuit later external calls.
- Completion with more sectors remaining first sets +28 = 1 and returns 0;
  the next invocation attempts the next issue. The final result is not returned
  merely because the final chunk was submitted.
- Rejected read submissions repeat a ready-style operation with argument 0 until
  accepted; there is no recovered retry limit. The test interpreter has an
  instruction ceiling to catch nontermination, not a game-behavior timeout.
- Stack mode bytes are `{0,1,0}`. The fourth byte is not initialized by these
  routines and is not assigned a meaning. `lq`/`sq` preserve the complete 128-bit
  callee-saved registers, while scalar arithmetic uses low word/doubleword rules.

## External dependencies and verification level

| Original entry | Callback label used here | Observed contract at these callers |
|---|---|---|
| `0010f520` | sync | Argument 1; zero permits progress |
| `0010fd28` | disk_ready | Argument 1 must return 2; argument 0 used in retry loop |
| `0010ff30` | read | LBA, sector count, EE destination, mode pointer; nonzero accepted |
| `00110268` | get_error | Zero success; nonzero retained and reported |

The API names are interpretations consistent with a libcdvd-style read path,
not recovered original symbols. Their implementations and IOP internals remain
dependencies. They are mocked in differential checks. That makes byte-level
branch/memory comparisons reproducible without claiming the mocks prove PS2
hardware behavior. Interrupts, reentrancy and concurrent mutation are not modeled.

Original instructions, state bytes, active flag, return values and the ordered
external-call trace are compared against the compiled C for 1,448 invocations/
transitions, including 12 complete request lifecycles. The restricted instruction
runner rejects unsupported opcodes and unmapped reads; its signed arithmetic,
delay slots and quadword handling also have synthetic tests.

PCSX2 independently confirms the four function bodies are present unchanged in
original runtime memory and that the file lookup data match the disc. No captured
breakpoint trace proves execution of every reconstructed branch. Status is
**original-byte checked, reconstructed, model differential tested**; gameplay
behavioral verification and byte matching are **not tested**.
