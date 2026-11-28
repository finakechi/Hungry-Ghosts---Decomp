# AREA1 floor snap at 001e3440

**2026-11-28 · Japanese SCPS-15046.** The source of the stop pair is
[M25 live continuity at 969e913](https://github.com/finakechi/Hungry-Ghosts---Decomp/blob/969e913cf9520868ed1d575f108aafc1ab016a72/evidence/m25/live-continuity.json).
The [isolated replay receipt](../evidence/realignment/collision.json) contains the
input identity, decoded call sites, memory ranges and control results. M25's
original live verifier reproduced its receipt byte-for-byte before this replay.

## Entry, branches and effects

- State-0 dispatcher `00158560` reaches `001585c8` immediately after
  `00158a10`. It puts +8.0 and −3.0 in `f12` and `f13`. The delay slot
  of `001585d8` supplies the player pointer in `a0`. In this run it is
  `003f92d0`. The 16-byte position at player +0x30 is copied twice to
  `70000be0` and `70000bf0`. The two offsets modify the Y lane separately,
  so the segment is (48.5, 46, 265, 1) to (48.5, 35, 265, 1).
- At `001e34b4`, `00144820` receives the player, the two scratchpad
  vectors and mask `0x207`. It runs `00145740` on the segment and mask,
  saves candidate result fields if that query hits, then calls `00268ad0`
  with the player, segment and mask. If both hit, it compares the two
  distances from the start using `00120830` and can restore the first
  result. The instruction branch favors the first candidate at
  equal distances. The traversed call chain includes
  `00145b60`, `00145e80` and `001449a0`. M12 documents another route
  into the same scene-query boundary through the grounded response.
  [M12 grounded response](M12.md)
- A zero query result branches at `001e34bc` straight to the epilogue.
  It skips the player hit writes. A hit copies the whole position vector
  from `003f39a0`. Bit `0x100` sets player +0x2c3 to 1, builds +0x47c
  from the old high bit and the selector at `003f39c6`, and copies
  `003f39c8` to +0x47e. When that halfword is zero, bytes +0x480/481
  take the two halves of `003f39cc`. A further condition can set bit 1
  at +0x483. A nonzero +0x47e clears +0x480/481.
- A nonzero result without bit `0x100` sets player +0x2c3 to 2. With
  mask `0x1e00` present it stores the pointer at `003f3a20` into
  +0x4f4, with a type-3 branch that stores a halfword at `003f9ff2`.
  The alternate path clears +0x4f4. The wrapper leaves the query result
  in `v0`. The stop pair contains memory and debugger-PC evidence, so
  the replay supplies the return register value.
- The authenticated ELF contains 15 direct JAL word matches for this
  entry. The Ghidra body map places them in candidate owners `00136bf0`
  (2), `00136f50` (2), `00158560` (1), `001e5060` (8), `00214ed0`
  (1) and `00262080` (1). `001585d8` is the tested state-0 callsite.
  The other sites are static references pending caller and reachability
  analysis. Indirect and overlay callers were outside this scan.

## Same-run result and counterexamples

The existing functional EE subset executed 5,333 steps at 824 distinct
authenticated instruction addresses between `001585c8` and `001585e0`.
It returned `0x100`. The capture records X/Z unchanged, Y rising from
38.0 to 38.0455322265625 and the floor-state byte becoming 1. All 243
replay-written non-stack bytes match the second stop. The stop pair changes
111 non-stack bytes, all inside those writes, across three player ranges,
two EE work ranges and both scratchpad vectors. The inherited guest-stack
exclusion is `01fe0000..02000000`. Register setup uses the documented replay
GP and scratch stack, so the result establishes the measured memory boundary.

Three original-instruction counterexamples reuse the first snapshot with
only the input endpoint offsets changed. `(-8,-3)`, `(+8,+2)` and
`(0,0)` each return zero and leave the player position unchanged. These are
controlled replays against one mesh state. The type-3 branch, other non-floor
hit results, both-hit arbitration in a live run, live wall contact and
retail teardown remain unobserved here. Polygon and material table ownership,
mesh selection, dynamic object queries and alternate caller prerequisites
remain open.

## Reproduction

Use a fresh output path with the original ELF, the committed M25 receipt
and its private full-capture directory. The verifier pins the receipt and
ELF SHA-256 and binds all four memory images and both debugger screenshots
to the receipt. It checks each executed word against the original ELF.

```sh
python3 -B tools/verify_collision_snap.py --elf "$HG_ELF" \
  --m25-receipt "$M25_RECEIPT" --live "$M25_CAPTURES" \
  --output "$OUT/collision.json"
cmp evidence/realignment/collision.json "$OUT/collision.json"
```
