# M8 actor pool and update lists — verified partial implementation

Eight complete resident bodies, **266 words / 1,064 bytes**, are reconstructed
in `src/resource_loader.c`. The original Japanese ELF was authenticated again
before use. The completed private exports from the interrupted M8 session were
preserved and checked against original PT_LOAD bytes. No external implementation
was copied; the pinned PS2DInfo verifier is invoked locally.

| Original entry | C operation | Defined arguments |
|---|---|---|
| 00142810 | hg_actor_register_list | actor, list base, priority |
| 001428f0 | hg_actor_construct | actor, kind, callback, priority |
| 00142970 | hg_actor_construct_short | actor, kind, callback, priority |
| 001429b0 | hg_actor_allocate | pool selector, kind, callback |
| 001430a0 | hg_actor_update_main | none |
| 00143100 | hg_actor_update_priority | none |
| 00143150 | hg_actor_update_type2 | none |
| 001431a0 | hg_actor_update_type2_priority | none |

Allocation truncates the selector to a byte. Bit 7 supplies priority; low seven
bits choose pool 0, 1, or the short-record pool for every other value. Empty
pools return zero without decrementing their count. Nonempty pools decrement
the count and publish the next free head **before** construction.

Registration selects the type-2 list using actor byte +29 before constructor
stores. It reloads the old tail after storing actor +20/+24, retaining alias
behavior. Full construction clears +90..24f using the original memset seam,
then publishes the original defaults, including +24c = 3e4ccccd. Short
construction leaves that region untouched. No heap allocator is invented.

Each dispatcher reads its list head before clearing the halfword counter at
3eaed8. It reads the callback from actor +1c, fetches next from +24 in the
`jalr` delay slot, calls the callback with a0 = actor, then reloads/increments
and stores the halfword counter. Changes to current +24 cannot change the
prefetched next actor. Changes to the counter inside the callback are retained.
Only 1430a0 clears byte 3f9772, including for an empty list. Other callback
arguments are undefined and are not compared. Model `jalr` support reads the
old target before writing the link, including overlapping source/destination.

[Instruction comparisons](../../evidence/m8/instruction-comparison.json) cover
1,280 deterministic cases, all 266 addresses, ordered defined calls, non-stack
bus memory, allocation returns, and callee-saved 128-bit registers. Saved stack
slots remain outside the C interface. The evidence gate requires all original
body ranges, including disjoint allocation returns and delay slots.

[Live code presence](../../evidence/m8/runtime-code-presence.json) verifies all
266 words in the **preserved paused** PCSX2 process. This is code-presence
verification, not a behavioral runtime comparison. Callback bodies, actor
resource initialization, input processing, and interactive equivalence are not
established by these primitives. M8 remains incomplete.
