#ifndef HG_GAME_LIFECYCLE_H
#define HG_GAME_LIFECYCLE_H
#include "resource_loader.h"

/* M17 game lifecycle layer (original Japanese SCPS-15046, ELF a74248c5...3c61).
 *
 * The main loop 0011d830 calls the mode dispatcher 00143240 once per frame.
 * The dispatcher calls the current mode handler stored in the mode block
 * gp+2dc0 (0x003ead30; the scratch word 0x700000c0 points to it):
 *   +0 u8 changed/active flag, +4 handler, +8 major, +9 minor, +a sub,
 *   +b step, +c, +d, +1c u16, +1e u16 timer; +1 u8 area index.
 * The gameplay mode 0012d1c0 owns area loading and active play:
 *   major 0..4 enter/leave paths; major 5 dispatches the minor state
 *   (0012d9a0): 0 entry 0012dc90, 1 active play 0012e700, 2 0012f270,
 *   3 003372a0, 4 teardown 0012f800, 5 area load 0012dd60, 6 0012e020.
 * Active play (0012e700) has seven sub states: 0 entry setup, 1 active frame
 * (coordinator 0012f850 + event flags 0012fa00), 2 pause menu, 3, 4, 5, 6.
 *
 * Every callee outside this file is a bus call with the original entry
 * address. Argument registers follow the original call sites: a0..a3 in
 * order; an FP argument f12 is passed in the first bus slot ahead of a0
 * (00156d50: f12, a0, a1); t0 for 00156ad0 is supplied first through the
 * existing host seam HG_CALL_T_REGS (t0, t1, t2). Mode handlers and other
 * lifecycle callees in this file are also reached through bus calls so the
 * original call structure stays observable; hg_lifecycle_call routes them. */

void hg_mode_set(HGResourceEnv *, uint32_t handler);            /* 00143300 */
void hg_mode_init(HGResourceEnv *, uint32_t handler);           /* 001432c0 */
void hg_mode_dispatch(HGResourceEnv *);                         /* 00143240 */
void hg_gameplay_mode(HGResourceEnv *);                         /* 0012d1c0 */
void hg_gameplay_major5(HGResourceEnv *);                       /* 0012d9a0 */
uint32_t hg_gameplay_entry_step(HGResourceEnv *);               /* 0012dc90 */
uint32_t hg_area_load_step(HGResourceEnv *);                    /* 0012dd60 */
uint32_t hg_area_pack_id(HGResourceEnv *, uint32_t);            /* 0012dbd0 */
uint32_t hg_gameplay_enter_step(HGResourceEnv *);               /* 0012d7d0 */
void hg_gameplay_reset_services(HGResourceEnv *);               /* 0012d850 */
uint32_t hg_gameplay_title_step(HGResourceEnv *);               /* 0012d680 */
uint32_t hg_gameplay_reload_step(HGResourceEnv *, uint32_t step, uint32_t mode); /* 0012e020 */
void hg_gameplay_frame(HGResourceEnv *);                        /* 0012e700 */
void hg_gameplay_event_frame(HGResourceEnv *);                  /* 0012e6d0 */
uint32_t hg_gameplay_event_flags(HGResourceEnv *);              /* 0012fa00 */
void hg_gameplay_request_exit(HGResourceEnv *);                 /* 0012f7d0 */
void hg_gameplay_teardown(HGResourceEnv *);                     /* 0012f800 */
/* 0011d830: main. hg_main_init is its body up to the loop (0011d830..
 * 0011d91c); hg_main_frame is one iteration of the loop 0011d938..0011d9fc.
 * The vblank wait loop polls 0095e308; while it is zero the host seam
 * HG_WAIT_INTERRUPT delivers pending interrupt handlers (no guest effect of
 * its own). EE timer register stores (0x10000000/0x10000010) are bus stores. */
#define HG_WAIT_INTERRUPT 0xfffffffau
void hg_main_init(HGResourceEnv *);                             /* 0011d830 */
void hg_main_frame(HGResourceEnv *);
void hg_boot_mode(HGResourceEnv *);                             /* 00121810 */
uint32_t hg_logo_sequence_step(HGResourceEnv *, uint32_t table, uint32_t first,
                               uint32_t kind, uint32_t id, uint32_t last); /* 001219e0 */
void hg_lifecycle_mode(HGResourceEnv *);                        /* 00121be0 */
uint32_t hg_vblank_start_handler(HGResourceEnv *);              /* 0011df10 */
uint32_t hg_vblank_end_handler(HGResourceEnv *);                /* 0011df80 */
/* Host connected frame: coordinator entry of frame N to the coordinator
 * entry of frame N+1 through the lifecycle layer (see game_lifecycle.c).
 * Returns 1 when it stopped at the next coordinator call. HG_WAIT_INTERRUPT
 * is reported to the outer bus, then the C vblank handlers run. */
uint32_t hg_game_frame_connected(HGResourceEnv *, uint32_t frame);
/* Bus router for the functions above: returns 1 and sets *result when pc is
 * one of the lifecycle entries reconstructed here. */
int hg_lifecycle_call(HGResourceEnv *, uint32_t pc, uint32_t a, uint32_t b,
                      uint32_t c, uint32_t d, uint32_t *result);
#endif
