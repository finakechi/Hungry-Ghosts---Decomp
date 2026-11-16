# M9 player turning and camera response — verified reconstruction

Eleven original routines (nine complete, two observed-branch) add **1,147 words /
4,588 bytes**. They connect a held-then-released left-stick turn of the global
player actor 003f92d0 to the camera update that follows it in the same frame. The
original Japanese ELF was SHA-256 authenticated before addresses were
interpreted. Every listing in [functions.json](functions.json) matches the
original file-backed bytes; `tools/hg_m9_evidence.py` assembles the packet from
read-only HGExport snapshots.

| Entry | C operation | Role in the observed operation |
|---|---|---|
| 0015fe00 | hg_player_movement_gate | Called by ordinary update 159ad0 with a1 = 1 (delay slot 159b08) |
| 0015fe70 | hg_player_movement | Movement/look dispatcher: counters, look/turn, walk, idle timers, heading |
| 001608b0 | hg_player_walk | Forward speed from stick Y 3eaf10; calls the turn; advances position |
| 00160510 | hg_player_turn | Stick X 3eaf0c → level and table row 39a3f0; rate +440; heading +44 |
| 00160420 | hg_player_rate_approach | Moves a rate toward a signed limit, braking an opposite-signed rate |
| 0015fcd0 | hg_player_speed_class | Speed class 1–3 (+4ad, +4d4, +416, mode bit 4) |
| 00160c20 | hg_player_move_limit | Movement limit for +2c3 = 2 and 3f9ff2 = 0x68 / 0x8e |
| 001204e0 | hg_angle_wrap | Wraps an angle into (−π, π] |
| 0012c790 | hg_camera_update | **Observed 3eaed4 = 0 branch only**: node refresh, follow, eye, view matrix |
| 0012d130 | hg_camera_follow | Head matrix, then eye/target replacement and look-at 12c3d0 |
| 0012bd00 | hg_camera_head_matrix | **Observed +47a = 0 branch only**: pitch/heading/roll matrix, eye, target |

In C, the two unobserved branches call `HG_DOMAIN_FAULT` (0xfffffff0), a
host-only fail-closed bus entry; they are never executed silently. The same
applies to non-finite FP operands. Two instructions in each of 160510 and 1608b0
form a statically unreachable switch default: the level register a1 is only
assigned 0–3 before `beq a1,3/2/1/zero`, with no reload.

## Turn semantics (original order)

1. 0015fcd0 speed class s; |3eaf0c| quantized at 0.4 / 0.6 / 0.8 to level 0–3
   (any nonzero level becomes 3 when s = 3); level stored at +504.
2. Scale = [3e0870 + 4s]; brake = [39a3f4] × scale; step and limit =
   row [39a3f0 + 16·level] +8 / +12 × scale (negated for a negative stick).
3. Nonzero level: 00160420(step, limit, brake, +440). Zero level: +440 decays by
   the row-0 step, and a sign crossing is clamped to +0.
4. +44 = 001204e0(+44 − +440).

The FP subset is integer-exact: chop rounding, denormals read and written as
zero, overflow saturating to ±FLT_MAX, EE add/subtract masking the smaller
operand below one guard bit, and exact-then-chop VU adds. It mirrors
`tools/ee_subset.py` and is differentially tested against it.

## Observed operation

One continuous PCSX2 v2.8.2 run from save-state slot 1 was stopped natively at
00159570, 00160510 (from 160bc0), 00158778, 0012c790 and 0012f8d4 on each frame
(frame counter 46e118 = 0x752, 0x754–0x75a). Left-stick-left was held for five
updates and then released. The turn rate ramps 0 → −π/4000 per frame to
−0.0039270, then decays by π/1200 to 0. The heading +44 rises from π/2 to
1.5838859, and the camera yaw 3f9934 follows it. Bindings are in
[player-turn-camera-observation.json](../../evidence/m9/player-turn-camera-observation.json).

## Comparison rules

The rules follow M8. Comparisons are exact over 32 MiB of EE RAM (excluding the
guest stack and the libpad SIF-DMA receive buffers) **and all 16 KiB of
scratchpad**. Math, rotation, look-at, strafe and node leaves execute the
original instructions in the bounded `ee_subset` model. M9 added VRSQRT, VDIV
(x/0 gives ±FLT_MAX, as in PCSX2 microVU), VOPMULA/VOPMSUB, VNOP, vmulq, the
MMI pextlw/pextuw/pcpyud forms, `xor` and `nor`. Each was required by an
observed instruction.

See [docs/M9.md](../../docs/M9.md) and
[player-turn-runtime-comparison.json](../../evidence/m9/player-turn-runtime-comparison.json).
