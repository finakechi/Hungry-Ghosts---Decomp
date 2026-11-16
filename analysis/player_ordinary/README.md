# M8 ordinary player input — verified partial reconstruction

Three complete resident functions add **190 words / 760 bytes**. The original
Japanese ELF was SHA-256 authenticated before interpreting addresses. The
existing Ghidra gameplay export and fresh read-only transition exports agree
with all original instruction words.

| Entry | C operation | Dependencies |
|---|---|---|
| 00159ad0 | hg_player_ordinary_update | Animation status, eligibility, movement, target lookup and state transition |
| 00159380 | hg_player_apply_transition | State-specific bodies remain external; actor a0 remains live |
| 00158d30 | hg_player_reset_transition | Effect unlink and animation selection remain external |

The host-only `hg_player_ordinary_input_stage` splits 159ad0 at 159c44 after its
eligibility checks. It is the same code used by the complete ordinary update,
not another retail function. It preserves interaction priority over the secondary
input mask, byte stores, the outgoing actor argument and post-call state reads.
The dispatcher clears **one byte** at 3f9f38, preserving its neighbors. FP bus
calls 13a050 and 15e190 place raw f12 bits before their integer arguments. These
are host seam conventions, not claims about the original integer register ABI.

**1,536 instruction cases cover all 190 recorded addresses**, calls, non-stack
memory and full 128-bit callee-saved registers. Synthetic external dependencies
are not original-game or engine equivalence. Stack save slots remain excluded.

Original observation `M8-NATIVE-INPUT-001` connects native debugger stops with
paused PINE captures of global player 003f92d0. The observed ordinary path has
+448=0 and +49f=0. No input leaves +449=ff. GUI Square (Keyboard/J) produces
held/edge 0080; stepping original 159ad0 reaches its store at 159cb0, changing
+449 to 1. Native 159380 then returns with +448/+44a=1 and +19/+1a=0. The next
159570 dispatch reaches 175190 with the same actor, and its return has +19=1.
PC evidence comes from visually reviewed native screenshots, not PINE alone.
The action's visual/gameplay name remains unassigned.

C matches **all 1,808 sampled bytes per case**, including the complete 1,344-byte
actor range, for the input stage with Square and a no-input control. It checks
the outgoing 159380 call. This comparison ends at that call: it does not compute
158e20, the next 175190 operation, or player initialization. Expected captures
are comparison-only and never used to calculate outputs. Other original stops
establish the connected original behavior separately from this bounded C check.

Compiled wrong-input-source, wide-global-store and lost-actor defects fail.
Prior argument propagation, timer-expiry and active-dispatch controls also fail
as intended. See [M8 checkpoint](../../docs/M8.md),
[original observation](../../evidence/m8/native-input-observation.json),
[instruction comparison](../../evidence/m8/ordinary-instruction-comparison.json),
[input-stage comparison](../../evidence/m8/ordinary-input-runtime-comparison.json)
and [regressions](../../evidence/m8/ordinary-checks.json).

The global player initializer 158a10 (called by 158560) is distinct from the
earlier AREA1 actor setup. The connected initialization → Square → transition →
next-update reconstruction and its original-game comparison are recorded in
[player_connected](../player_connected/README.md).
