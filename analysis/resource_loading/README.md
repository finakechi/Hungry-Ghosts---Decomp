# Resumable normal resource worker

The connected path uses the reconstructed request and worker phases on the
original guest bus. M1 disc state is at `003eacf0`. The file records remain
`003ead20` for PACK and `003f3a30 + index*8` for AREA. The directory
workspace is `003f3b00`. The handle table begins at `003f4300`.

| Original address | Role in the connected path |
|---|---|
| `00130150`, `00130170` | Set busy at `003eacec`, clear bulk-ready at `003eace0`, and submit raw or adjusted requests |
| `00143360` | Fill normal descriptor `003f8e00` and call CreateThread and StartThread. An active normal descriptor rejects a second request without replacing the first |
| `00130220` | Dispatch kinds 0–8. Kind 2 runs an AREA read and alternate PACK, may load general PACK 5/6, then changes to kind 3 |
| `001306a0`, `00130900`, `00131420` | Bootstrap, general and alternate PACK. Each pending loop edge reaches SleepThread wrapper `001434e0` |
| `0012ff20` | Kind 6. Compare/update the word at `003f9fe0`, translate via `003e0210 + id*4`, and skip the load for a cache hit or `ffffffff` |
| `0012ffa0` | Kind 7. Set `0046e10c` to 3 for directory `49` or 2 otherwise, compare the request with the byte at `003f9fef`, and load on miss |
| `00130020`, `0012fe90` | Retained pair and context selection. Advance from the first general PACK to the second and publish readiness only after their returns |
| `00131da0` | Rebase the final special-group member to `1e00` through `001385b0`, then call upload `00152ae0` |
| `0011df10`, `0011df80` | Vblank start wakes the normal descriptor's thread. Vblank end targets the separate priority descriptor `003f6d80` |
| `001434f0` | The worker has already cleared busy. Clear normal active at `003f8e30` before calling ExitDeleteThread |
| `00141b10` | Executed as authenticated EE instructions at gameplay entry and teardown. The memset of `003f9e30..003fa080` clears the retained selector, permitting the next gameplay request to reload those PACKs |

The normal thread descriptor declares GP `003e7f70`, stack base `003f6e00`,
stack size `2000` and priority 5. Original branches select priority 3 and a
different stack for kind 100. M19 gives the normal thread one initial slice at
StartThread and one continuation per delivered normal vblank wake. An
outstanding read supplies bytes at the optical backend's completion tick.
The scheduler accepts the original task ID for the live worker and checks that
an exit leaves no pending transfer. The three state owners are the guest PACK
and disc context, the caller-owned continuation, and the host clock/transfer.
The original instruction differential compares C and EE at every modeled
SleepThread and at return, with the same asynchronous transport.

Kinds 2 and 3 have distinct publication edges. Kind 2 can clear dynamic
handles and replace the overlay at `00a87800`, then write its arena endpoint.
Kind 3 sets bulk-ready as its bulk read returns, then publishes handles and
runs fixups. Busy can remain set while late processing and retained/context
PACKs run. A later scene reset invalidates the retained selector. A direct
kind-8 cache hit still clears bulk-ready on request, completes, and preserves
the previously loaded ranges. The measured release effects are handle
invalidation, allocator reuse, and overlay replacement.

The three new C bodies are bounded by [functions.json](functions.json).
`verify_resource_job.py` checks the fixed hashes against the original ELF,
cross-checks the PS2DInfo file mapping, and observes 71 reachable words. The
remaining word at `0012ffc4` is padding skipped by the unconditional branch.
The interpreted scene reset occupies `00141b10..00141c98` with SHA-256
`9298a9e5b72347c9b4d6199b7bf22edc6228e5a0dc559ea7a9ac87f36c3482a3`.
Its libc memset is modeled, and its cosmetic RNG call receives zero in this
runner. The scheduler and optical clock are host models.
