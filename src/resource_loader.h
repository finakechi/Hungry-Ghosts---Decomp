#ifndef HG_RESOURCE_LOADER_H
#define HG_RESOURCE_LOADER_H
#include "disc_read.h"
#include <stdint.h>

/* All addresses are original EE addresses, never host pointer casts. The bus
 * owns ordinary RAM and scratchpad storage. External calls are unreconstructed
 * kernel, codec, audio/resource fixup, or other loader dependencies. */
typedef struct {
    void *user;
    uint32_t (*load)(void *, uint32_t address, uint32_t width);
    void (*store)(void *, uint32_t address, uint32_t width, uint32_t value);
    uint32_t (*call)(void *, uint32_t entry, uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3);
    HGReadState *read_state;
    HGReadEnv *read_env;
} HGResourceEnv;

/* Original thread block is 0x50 bytes: kernel descriptor +00..2f,
 * u16 active +30, u16 kind +32, s32 thread ID +34,
 * bytes +38..3b, u16 counter +3c, u16 allocation mode +3e,
 * u32 +40 (retained), directory ID +44, destination +48, member cursor +4c.
 * Normal block: 003f8e00; priority-kind-100 block: 003f6d80.
 */
void hg_resource_init(HGResourceEnv *);                         /* 001431f0 */
void hg_resource_schedule(HGResourceEnv *, uint32_t, uint32_t, uint32_t); /* 00143360 */
void hg_resource_finish(HGResourceEnv *);                       /* 001434f0 */
void hg_resource_request_raw(HGResourceEnv *, uint32_t, uint32_t); /* 00130150 */
void hg_resource_request(HGResourceEnv *, uint32_t, uint32_t);   /* 00130170 */
void hg_resource_worker(HGResourceEnv *);                       /* 00130220 */
/* M7 directory preparation. Isolated entries retain their original call seams;
 * worker_fixed resolves these and their general PACK loads on the same bus. */
void hg_resource_load_indexed(HGResourceEnv *, uint32_t);        /* 0012fcf0 */
void hg_resource_load_context(HGResourceEnv *, uint32_t);        /* 0012fde0 */
void hg_resource_prepare_context(HGResourceEnv *, uint32_t);     /* 0012fe90 */
void hg_resource_prepare_retained(HGResourceEnv *, uint32_t);    /* 00130020 */
uint32_t hg_pack_special_step(HGResourceEnv *, uint32_t, uint32_t, uint32_t); /* 001303e0 */
void hg_pack_bootstrap(HGResourceEnv *, uint32_t);                /* 001306a0 */
void hg_pack_general(HGResourceEnv *, uint32_t);                  /* 00130900 */
void hg_pack_alternative(HGResourceEnv *, uint32_t);              /* 00131420 */
void hg_resource_clear_dynamic_handles(HGResourceEnv *);         /* 0012fb30 */
void hg_resource_clear_selected_handles(HGResourceEnv *);        /* 0012fb90 */
uint32_t hg_area_index(HGResourceEnv *);                         /* 0012fbc0 */
uint32_t hg_area_read_step(HGResourceEnv *, uint32_t);            /* 00131810 */
void hg_area_finalize(HGResourceEnv *, uint32_t, uint32_t);       /* 00131dd0 */

/* Host-only adapter: one iteration of the original bootstrap loop, without
 * SleepThread. 1 means the original function would return, 0 means sleep/yield.
 * Invalid phase values keep yielding, matching the original nonprogress loop.
 * This helper is not counted as another reconstructed retail function. */
uint32_t hg_pack_bootstrap_step(HGResourceEnv *, uint32_t);
/* Host-only split of 00130900: prepare is called ONCE per invocation; step
 * returns 1 at the original return, otherwise 0 at its SleepThread boundary.
 * Repeating prepare while a load is pending repeats original entry effects. */
void hg_pack_general_prepare(HGResourceEnv *, uint32_t);
uint32_t hg_pack_general_step(HGResourceEnv *, uint32_t);
uint32_t hg_pack_alternative_step(HGResourceEnv *, uint32_t);
/* A1_B00 AREA entry. This callback uses original fixed addresses after its
 * bytes are loaded at a87800; dependencies remain explicit bus calls.
 * The extra host callback is REQUIRED for resident 0018a330's six defined EE
 * arguments, including t0/t1=0. It uses resources->user; the existing M1-M4
 * HGResourceEnv layout and its four-argument seam are unchanged. No generic
 * AREA dispatch, graphics/actor dependency implementation, or FPU is implied. */
typedef uint32_t (*HGArea1Call6)(void *, uint32_t entry, uint32_t a0, uint32_t a1,
                               uint32_t a2, uint32_t a3, uint32_t a4, uint32_t a5);
void hg_area1_actor(HGResourceEnv *, uint32_t, HGArea1Call6);      /* 00a8a070 */
void hg_actor_data_init(HGResourceEnv *, uint32_t, uint32_t);     /* 0018c220 */
/* M8 isolated setup entries retain original-address call seams. The fixed
 * adapters resolve lookup, binding, node allocation, palette/mode, default pose
 * and attachment orchestration on the same bus. Math leaves, nonzero animation
 * sequence selection and attachment allocators remain external. */
void hg_actor_setup(HGResourceEnv *, uint32_t actor, uint32_t model,
                    uint32_t animation, uint32_t attachment,
                    uint32_t mode, uint32_t sequence);           /* 0018a330 */
void hg_actor_setup_resources(HGResourceEnv *, uint32_t, uint32_t, uint32_t, uint32_t); /* 0018a4c0 */
void hg_actor_setup_animation(HGResourceEnv *, uint32_t, uint32_t, uint32_t); /* 0018a5b0 */
void hg_actor_set_mode(HGResourceEnv *, uint32_t, uint32_t);      /* 00139fc0 */
void hg_actor_set_palette(HGResourceEnv *, uint32_t, uint32_t);   /* 001235c0 */
/* Active publication dispatcher; transform/visibility and render consumers
 * remain bus dependencies. Comparisons cover finite normal values and zero. */
void hg_actor_active(HGResourceEnv *, uint32_t);                 /* 0018c280 */
/* Same AREA1 callback with setup and active dispatch resolved. Quaternion math,
 * animation selection, transform/visibility and render work remain external. */
void hg_area1_actor_fixed(HGResourceEnv *, uint32_t actor);
void hg_actor_setup_fixed(HGResourceEnv *, uint32_t, uint32_t, uint32_t,
                          uint32_t, uint32_t, uint32_t);
/* Player input transition counter (00159570 calls this on actor flag +49f&1).
 * Finite normal/zero analog values; original 0015e0c0 remains a bus call. */
uint32_t hg_player_input_transitions(HGResourceEnv *, uint32_t actor); /* 0015de80 */
/* Ordinary player path, observed at +448==0. Dependencies retain address seams;
 * raw FP call arguments use the existing bus convention. */
void hg_player_ordinary_update(HGResourceEnv *, uint32_t);        /* 00159ad0 */
void hg_player_apply_transition(HGResourceEnv *, uint32_t);      /* 00159380 */
void hg_player_reset_transition(HGResourceEnv *, uint32_t);      /* 00158d30 */
/* Host-only split of 159ad0 after its eligibility checks; original transition
 * callees remain external. Not counted as an additional original function. */
void hg_player_ordinary_input_stage(HGResourceEnv *, uint32_t);
void hg_player_initialize(HGResourceEnv *, uint32_t);           /* 00158a10 */
void hg_actor_set_animation_flag(HGResourceEnv *, uint32_t, uint32_t); /* 00139fe0 */
void hg_player_unlink_effects(HGResourceEnv *, uint32_t, uint32_t); /* 00158ce0 */
/* Raw f12 blend bits precede original actor and sequence arguments. */
uint32_t hg_player_select_animation(HGResourceEnv *, uint32_t, uint32_t, uint32_t); /* 0015e190 */
void hg_actor_copy_node_matrix(HGResourceEnv *, uint32_t, uint32_t, uint32_t); /* 0013a2a0 */
/* frame is the original guest sp after its 0x70-byte frame allocation. The
 * input save/restore uses frame+0x30; no host pointer enters the EE bus. */
void hg_player_active_update(HGResourceEnv *, uint32_t, uint32_t frame); /* 00159570 */
/* Host split of 175190's state-0 path. Returns 0 without effects for another
 * entry state; callers must supply that unreconstructed state via the bus. */
uint32_t hg_player_equipment_enter(HGResourceEnv *, uint32_t);
/* 158e20 sound-denial branch currently verifies finite [0,1] volume under
 * the bounded chop model. Bus 156ad0's fifth argument is the constant 1. */
void hg_player_equipment_transition(HGResourceEnv *, uint32_t);  /* 00158e20 */
void hg_player_condition_timer(HGResourceEnv *);                 /* 001594f0 */
uint32_t hg_player_flag_status(HGResourceEnv *, uint32_t);       /* 0015b260 */
uint32_t hg_player_spawn_equipment(HGResourceEnv *, uint32_t);   /* 0015c160 */
void hg_player_ordinary_transition(HGResourceEnv *, uint32_t);   /* 00158d90 */
void hg_player_initialize_fixed(HGResourceEnv *, uint32_t);
void hg_player_active_update_fixed(HGResourceEnv *, uint32_t, uint32_t frame);
void hg_player_equipment_transition_fixed(HGResourceEnv *, uint32_t);
/* M9 connected turning and camera response; FP values are raw single bits. */
uint32_t hg_ee_fp(uint32_t op, uint32_t a, uint32_t b, uint32_t *fault);
uint32_t hg_angle_wrap(HGResourceEnv *, uint32_t angle);           /* 001204e0 */
uint32_t hg_player_speed_class(HGResourceEnv *, uint32_t, uint32_t mode); /* 0015fcd0 */
uint32_t hg_player_move_limit(HGResourceEnv *, uint32_t);          /* 00160c20 */
uint32_t hg_player_rate_approach(HGResourceEnv *, uint32_t step, uint32_t limit,
                                 uint32_t brake, uint32_t field);  /* 00160420 */
void hg_player_turn(HGResourceEnv *, uint32_t, uint32_t mode);      /* 00160510 */
void hg_player_walk(HGResourceEnv *, uint32_t);                    /* 001608b0 */
uint32_t hg_player_movement(HGResourceEnv *, uint32_t, uint32_t mode); /* 0015fe70 */
uint32_t hg_player_movement_gate(HGResourceEnv *, uint32_t, uint32_t mode); /* 0015fe00 */
uint32_t hg_camera_head_matrix(HGResourceEnv *, uint32_t player, uint32_t camera,
                               uint32_t keep_eye);                 /* 0012bd00 */
void hg_camera_follow(HGResourceEnv *, uint32_t player, uint32_t camera); /* 0012d130 */
void hg_camera_update(HGResourceEnv *);                            /* 0012c790 */
void hg_camera_update_fixed(HGResourceEnv *);
uint32_t hg_actor_resource_lookup(HGResourceEnv *, uint32_t, uint32_t); /* 00138370 */
uint32_t hg_actor_allocate_nodes(HGResourceEnv *, uint32_t, uint32_t); /* 00141e40 */
uint32_t hg_actor_take_node(HGResourceEnv *);                    /* 00141de0 */
void hg_actor_bind_resources(HGResourceEnv *, uint32_t, uint32_t, uint32_t); /* 00139d20 */
void hg_actor_default_pose(HGResourceEnv *, uint32_t);            /* 0013a0a0 */
void hg_actor_bind_attachment(HGResourceEnv *, uint32_t, uint32_t); /* 00267300 */
/* M8 actor pool/list primitives. Callback bodies remain explicit bus calls.
 * register_list takes the original list-base argument, normally 004d8150. */
void hg_actor_register_list(HGResourceEnv *, uint32_t actor, uint32_t base, uint32_t priority); /* 00142810 */
void hg_actor_construct(HGResourceEnv *, uint32_t actor, uint32_t kind, uint32_t callback, uint32_t priority); /* 001428f0 */
void hg_actor_construct_short(HGResourceEnv *, uint32_t actor, uint32_t kind, uint32_t callback, uint32_t priority); /* 00142970 */
uint32_t hg_actor_allocate(HGResourceEnv *, uint32_t pool, uint32_t kind, uint32_t callback); /* 001429b0 */
void hg_actor_update_main(HGResourceEnv *);                       /* 001430a0 */
void hg_actor_update_priority(HGResourceEnv *);                   /* 00143100 */
void hg_actor_update_type2(HGResourceEnv *);                      /* 00143150 */
void hg_actor_update_type2_priority(HGResourceEnv *);             /* 001431a0 */
/* 001385b0: linked rebasing; the combined adapter resolves 001389d0. */
void hg_resource_rebase(HGResourceEnv *, uint32_t, uint32_t);
/* M4 original functions. Extra frame arguments are host adapters for the
 * original guest stack scratch; see implementation and docs/M4.md. */
uint32_t hg_resource_fixup(HGResourceEnv *, uint32_t, uint32_t, uint32_t, uint32_t); /* 00137ed0 */
uint32_t hg_resource_map_address(HGResourceEnv *, uint32_t, uint32_t); /* 001389d0 */
uint32_t hg_flk5_entry(HGResourceEnv *, uint32_t, uint32_t);       /* 00138af0 */
uint32_t hg_flk5_type(HGResourceEnv *, uint32_t, uint32_t);        /* 00138b30 */
uint32_t hg_flk5_count(HGResourceEnv *, uint32_t);                 /* 00138b70 */
uint32_t hg_flk5_size(HGResourceEnv *, uint32_t);                  /* 00138b90 */
void hg_flk5_disable(HGResourceEnv *, uint32_t, uint32_t);         /* 00138560 */
uint32_t hg_resource_base_delta(HGResourceEnv *, uint32_t, uint32_t); /* 001385a0 */
/* M6: selected finite-normal geometry/material processing. sqrt takes/returns
 * raw FP bits; descriptor_address preserves the original 64-bit return. */
uint32_t hg_resource_material_next(HGResourceEnv *, uint32_t); /* 00138690 */
void hg_resource_geometry(HGResourceEnv *, uint32_t, uint32_t); /* 001386e0 */
uint64_t hg_resource_descriptor_address(uint64_t, uint32_t); /* 00138a80 */
uint32_t hg_resource_sqrt(uint32_t); /* 00143530 */
/* Combined M2 worker/bootstrap and general/alternative PACK/AREA loading,
 * handle invalidation and publication,
 * followed by M4 fixup -> M6 geometry/material -> M3 rebasing as directed,
 * and M7 retained/context directory preparation on worker kinds 2/3/8.
 * frame must be 16-byte aligned and reserve [frame-0x70, frame+0x240)
 * of guest scratch, away from resources and live state. */
void hg_resource_worker_fixed(HGResourceEnv *, uint32_t frame);
/* Host-only measured continuation after kind-3 loading/phase reset, through
 * the retained/context pair, before worker completion. Same scratch contract.
 * External 00156a50 may return zero while busy; on successful completion its
 * resource endpoint is (input + word(input+0x10) + 0x40) & ~0x3f, modulo 2^32.
 * Audio/IOP state and timing remain the caller's separate responsibility. */
void hg_resource_prepare_engine_fixed(HGResourceEnv *, uint32_t task, uint32_t frame);
/* M10 dispatcher observed active path; frame follows the active-update contract. */
void hg_player_dispatch(HGResourceEnv *);
void hg_player_dispatch_fixed(HGResourceEnv *, uint32_t frame);
void hg_player_walk_camera_fixed(HGResourceEnv *, uint32_t frame);
/* M11 static movement constraint; heading is raw f12 bits. Special-surface
 * hits fail closed. Emits host-only HG_GUEST_FRAME (0xfffffff1) bus calls
 * around its body: (0x70, 1) on entry and (0x70, 0) on exit. */
uint32_t hg_player_static_constraint(HGResourceEnv *, uint32_t heading, uint32_t); /* 0015ebc0 */
void hg_player_constraint_camera_fixed(HGResourceEnv *, uint32_t frame);
uint32_t hg_player_static_constraint_fixed(HGResourceEnv *, uint32_t heading, uint32_t);
#endif
