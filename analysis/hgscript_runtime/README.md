# Resident HGScript consumers

The authenticated image and ELF identities are in [M20](../../docs/M20.md).
[Function and table fingerprints](functions.json) describe original resident
ELF ranges. The verifier uses those bytes for its EE instruction run.

- The selected timed instance lives at `00a6bcc0`. The caller supplies its
  program and pool at `+0c/+10`. It preserves a return pair at `+14/+18`
  and the page pair at `+1c/+20`.
- Instance phase at `+00` is `-1` while idle, 0 while dispatching and 1 in
  a countdown. The remaining countdown is at `+04`, and the tick count is
  at `+08`. The `+24` display buffer uses token 0 for text, 1 for a line
  break and 2 for the tail marker. Its tail pointer is at `+224`. The
  draw pass owns page opacity and resets the buffer when the page fades out.
- The timed pad reader uses the guest edge halfword `0095e568` and mask
  `0x20`. Around a voice request, `+245` gates dispatch on voice status 3.
  The dispatcher's loop writes 2 on acceptance. The timed audio requests go
  to `001564a0` and `00156550`.
- Command 7 uses two 32-bit words at its pool offset. The first is the voice
  request ID. The second is carried in `+24c`, with `ffffffff` disabling
  the schedule. Non-sentinel schedules start eight bytes from the reference
  target. Entries use bits 31:28 for slot, 27:24 for action, 23:16 for value
  and 15:0 for frame. The consumer walks entries through the all-one sentinel.
  The current cue pointer is at `+248` and its frame counter at `+250`.
- The timed slot arrays at `00a6bf48`, `00a6bf58`, and `00a6bf68` store
  cue flags, per-tick cue values, and cue intensity. The terminal path
  clears a field through an initialized actor pointer in slots 2–15.
- The static path drives `00182850` with text and style pointers,
  flags and two floats. Its persistent style block is `009e7300`. The
  handle table base `003f4300` is shared with the resource loader.
- The alternate consumer `0026e9f0` has its own state structure and command
  table at `003ddcc0`. Its table was identified from the authenticated ELF.
  Its runtime is an open reconstruction task.
