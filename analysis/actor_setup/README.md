# M8 setup, active dispatch and player input — verified partial work

Thirteen resident C functions add 915 words / 3,660 instruction bytes. The
Japanese ELF SHA-256 was checked before address/semantic analysis:
`a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61`.
Completed read-only Ghidra exports were checked against original PT_LOAD bytes.
No external implementation was copied.

| Entry | C operation | Boundary |
|---|---|---|
| 0018a330 | hg_actor_setup | six arguments, including original t0/t1 |
| 0018a4c0 | hg_actor_setup_resources | three conditional resource lookups |
| 0018a5b0 | hg_actor_setup_animation | reload handle, mode byte, sequence/default pose |
| 00138370 | hg_actor_resource_lookup | encoded type, fallback scan, optional subresource |
| 00139d20 | hg_actor_bind_resources | node binding and defaults |
| 00141e40 | hg_actor_allocate_nodes | signed capacity check and node pointer publication |
| 00141de0 | hg_actor_take_node | original stack/count mutation, no invented heap |
| 001235c0 | hg_actor_set_palette | table word and four unity values |
| 00139fc0 | hg_actor_set_mode | byte store, including original delay slot |
| 0013a0a0 | hg_actor_default_pose | node pose publication; quaternion math external |
| 00267300 | hg_actor_bind_attachment | optional allocations/copies; allocators external |
| 0018c280 | hg_actor_active | transform/visibility and render publication dispatch |
| 0015de80 | hg_player_input_transitions | player stick/button transition counters |

Setup retains the fifth argument: actor +9c receives its low byte. The sixth
selects animation/default pose. Animation setup reloads the resource handle
*after* resource binding. Node allocation reloads the object's +54 after each
allocator call. Binding computes +e8 using the post-call +e4, before overwriting
+e4 with the returned count. Node stores reload the pointer table for every
original store, preserving relevant aliases. The optional zero attachment still
stores +274=0. Nonzero attachment paths retain the original allocator failures
and ordered copy seams.

`hg_area1_actor_fixed` connects the accepted M5 AREA1 callback to setup and
`hg_actor_active`, sharing HGResourceEnv and its existing bus. The standalone
`hg_actor_setup_fixed` resolves the same setup dependencies for comparison.
No second engine or runtime is introduced. Quaternion conversion 143e60,
flag leaf 1330f0, nonzero animation selection 139ff0, attachment allocation,
transform/visibility 18b6c0/18c9c0, and render consumers remain external as
applicable. The selected AREA1 six-argument setup uses sequence and attachment
zero. Its default pose does reach 143e60.

Active dispatch retains the original null actor/data branches, finite FP alpha
comparisons, mode selection, deferred queue bound and post-call reloads of
publication globals/counter. It does not establish input consumption. Flags 24
in the selected data skip some work in 18b6c0 but do **not** eliminate its
visibility/matrix dependencies.

Static original-byte review identifies 159570 as a player update caller of
15de80 when actor byte +49f has bit 1 set. The consumer compares the maximum
absolute axis of each stick against 0.5, counts alternating crossings, counts
button edges with mask f0, and maintains two signed-halfword countdown fields.
A timer decremented from 1 to 0 does not clear the counters until the following
update. Its final 15e0c0 call **does retain a0=actor**: inspecting that leaf
corrected the decompiler's omitted argument. The leaf changes actor +4dc from
stick input; it remains external. This conditional input path has not yet been
observed in the selected original-game continuation and is not claimed to be
normal player movement or a complete M8 input path.

The instruction comparison covers 2,944 cases and all 915 instruction addresses,
ordered defined calls, non-stack memory, defined returns, and full 128-bit
callee-saved registers. The finite FP model supports signed add/sub/mul/div
with exact rational evaluation and chop rounding. Exceptional operands, division
by zero, flags, timing, overflow and arbitrary aliasing into omitted save slots
are outside the verified domain. Binding's exact power-of-two scale excludes
overflow; normal values and zero are supported. No compiler-byte match is claimed.

A recovered original-game setup pair, corroborated by its screenshots at entry
18a330 and caller return a8a0f8, independently matches 1,496 bytes: the 976-byte
actor, two 256-byte nodes and 8-byte pool state. The computation uses C setup
plus authenticated original-instruction execution of 1330f0 and 143e60; expected
capture bytes are used only for comparison. `math-dependencies.json` contains
those 151 **model-only** instruction words, not more reconstructed C coverage.
This is saved-runtime hybrid evidence, not a fresh all-C gameplay comparison.

See [M8 checkpoint](../../docs/M8.md),
[instruction results](../../evidence/m8/setup-instruction-comparison.json),
[saved setup comparison](../../evidence/m8/setup-runtime-comparison.json), and
[live code presence](../../evidence/m8/setup-runtime-code-presence.json).
