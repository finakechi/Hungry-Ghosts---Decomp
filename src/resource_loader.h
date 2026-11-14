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
uint32_t hg_area_index(HGResourceEnv *);                         /* 0012fbc0 */
uint32_t hg_area_read_step(HGResourceEnv *, uint32_t);            /* 00131810 */
void hg_area_finalize(HGResourceEnv *, uint32_t, uint32_t);       /* 00131dd0 */

/* Host-only adapter: one iteration of the original bootstrap loop, without
 * SleepThread. 1 means the original function would return, 0 means sleep/yield.
 * Invalid phase values keep yielding, matching the original nonprogress loop.
 * This helper is not counted as another reconstructed retail function. */
uint32_t hg_pack_bootstrap_step(HGResourceEnv *, uint32_t);
/* 001385b0: linked resource address rebasing; 001389d0 remains external. */
void hg_resource_rebase(HGResourceEnv *, uint32_t, uint32_t);
#endif
