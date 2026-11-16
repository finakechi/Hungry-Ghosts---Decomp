# M8 connected player operation — verified reconstruction

Eleven complete resident functions add **880 words / 3,520 bytes**. Together with
the setup, list and ordinary-input work they connect the global player actor
003f92d0 from initialization through a real Square input, its gameplay-state
transition and the following update. The original Japanese ELF was SHA-256
authenticated before addresses were interpreted; all listings in
[functions.json](functions.json) match original file-backed bytes and JAL targets.

| Entry | C operation | Role in the observed operation |
|---|---|---|
| 00158a10 | hg_player_initialize | Player initializer, reached from 158560 state 0 |
| 00139fe0 | hg_actor_set_animation_flag | Initializer animation flag |
| 00158ce0 | hg_player_unlink_effects | Effect unlink used by transition reset |
| 0015e190 | hg_player_select_animation | Animation selection (raw f12 blend bits first) |
| 0013a2a0 | hg_actor_copy_node_matrix | Initializer node matrix copy |
| 00159570 | hg_player_active_update | Per-frame player update; mode dispatch on +448 |
| 00158e20 | hg_player_equipment_transition | Mode transition selected by +449 = 1 or 2 |
| 001594f0 | hg_player_condition_timer | Conditional countdown at 46d90c |
| 0015b260 | hg_player_flag_status | Flag status test used by 158e20 |
| 0015c160 | hg_player_spawn_equipment | Allocates and binds the held-equipment actor |
| 00158d90 | hg_player_ordinary_transition | Return to ordinary mode |

`hg_player_equipment_enter` is a **host split of 175190 state 0** only. It is
not counted as a complete function; other states must be supplied by the caller.

## Observed operation

One continuous original-game run (PCSX2 2.8.2, SCPS-15046) was stopped natively
at each step and captured in full (32 MiB EE RAM, plus scratchpad from the
ready-neutral return onward):

1. `158a10` entry/return: player initialization.
2. `159570` entry with game pad block 3eaf00 edge/held = 0080 (Square, Keyboard/J).
3. `158e20` entry (ra 1593d8, from 159380) and return.
4. `159570` return to 158778: +448/+44a 0→1, +19/+1a 1→0, +45f 0→1.
5. Next `159570` entry: release edge, +448 = 1 observed.
6. `175190` entry (ra 1596fc) and return; update return with +19 = 1 and the
   equipment actor pointer +450 = 004d9460.

Visually, the action raises the equipped weapon in the first-person view (HUD
label 始まりの槍). That label comes from a later screenshot; no further gameplay
semantics are assigned.

## Comparison rules

C control and resource orchestration run on one bus; engine, animation and math
leaves execute the original instructions in the bounded `ee_subset` model
(finite values, no flags or timing). Comparisons are exact over all 32 MiB
except two explicit domains:

- Guest stack 01fe0000–01ffffff (host and guest frames differ).
- libpad receive buffers, derived from the port table at 003f0a50 that original
  port-open 0010d840 fills (64-byte-aligned 2 × 0x80 halves; 0010dae0 picks the
  newer half by frame counters +58/+d8). The IOP writes them by SIF DMA once per
  frame. A difference there is accepted only if modeled execution neither read
  nor wrote that buffer. Each accepted byte is still listed.

Measured model correction: PCSX2's default EE **division** rounding is Nearest,
while its other FPU operations chop. Quaternion normalization 001440c0
(`div.S f2,f2,f0`) otherwise differs by 1–2 ULP in 149 words. `DIV.S` now
rounds to nearest-even. The earlier 1,496-byte setup comparison still passes.

## Limits

This is hybrid C plus original-instruction-modeled leaves, not all-C or
byte-matching equivalence. Between updates, code outside this reconstruction
rewrites actor +304/+344/+384/+42b. Between initializer return and the first
observed update, it changes 112 actor bytes. Those writers are not reconstructed.
The no-input control's entry capture predates scratchpad sampling: its 64-byte
camera matrix at 70000000 comes from a separate capture. See
[docs/M8.md](../../docs/M8.md) and
[connected-gameplay-runtime-comparison.json](../../evidence/m8/connected-gameplay-runtime-comparison.json).
