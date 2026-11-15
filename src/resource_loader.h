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
 * followed by M4 fixup -> M6 geometry/material -> M3 rebasing as directed.
 * frame must be 16-byte aligned and reserve [frame-0x70, frame+0x240)
 * of guest scratch, away from resources and live state. */
void hg_resource_worker_fixed(HGResourceEnv *, uint32_t frame);
#endif
