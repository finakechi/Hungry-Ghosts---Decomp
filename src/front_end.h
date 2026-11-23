#ifndef HG_FRONT_END_H
#define HG_FRONT_END_H
#include "game_lifecycle.h"

/* M18 front-end services of the original Japanese SCPS-15046 (ELF
 * a74248c5...3c61; title overlay /AREA0/A0_B09.BIN ef257df9...e935, linked at
 * 00a87800). Same conventions as game_lifecycle.h: every callee outside this
 * file is a bus call with its original entry address; argument registers in
 * order a0..a3; t0..t2 through HG_CALL_T_REGS; f12..f14 through
 * HG_CALL_FP_REGS; guest stack frames through HG_GUEST_FRAME.
 *
 * Fade block gp+2f40 (003eaeb0): +0 u8 phase (0 clear, 1 fading in,
 * 2 black, 3 fading out), +1 u8 layer, +2 s8 blend, +3 s8 kind (0..7, the
 * 00139240 jump table 003db1f0), +4 s16 level (0..0xff), +6 s16 step,
 * +8 u32 music-fade state, +c f32 music-fade target, +10 u32. 700000d4 is
 * the pause-fade flag. The per-frame service 00139240 runs in the main-loop
 * tail; its GS draws (0014a0a0, 0014f370) are bus calls.
 *
 * Pad block 0095e340 (libpad DMA buffer 0x100 bytes, then +100 port, +104
 * slot, +108 id, +10a port state, +10b pressure mode, +1a3 terminal id,
 * +1c0..+1fc pressure floats, +200..+220 analog floats, +224 held, +226
 * previous held, +228 pressed edge, +22a repeat, +22c/+22e repeat timer,
 * +230..+23a the same for the second button set). libpad (0010d000..) is the
 * IOP boundary and stays at the bus; 0013e5b0 (analog stick conversion) is a
 * bus call with f12/f13. */

void hg_fade_reset(HGResourceEnv *);                                   /* 00138fa0 */
void hg_fade_clear(HGResourceEnv *, uint32_t blend, uint32_t guard);    /* 00138fd0 */
void hg_fade_black(HGResourceEnv *, uint32_t blend, uint32_t layer);    /* 00139010 */
void hg_fade_out(HGResourceEnv *, uint32_t step, uint32_t blend, uint32_t mode,
                 uint32_t layer);                                      /* 00139040 */
void hg_fade_in(HGResourceEnv *, uint32_t step, uint32_t blend, uint32_t layer,
                uint32_t guard);                                       /* 001390b0 */
void hg_music_fade_2(HGResourceEnv *, uint32_t level);                  /* 001390f0 */
void hg_music_fade_1(HGResourceEnv *, uint32_t level);                  /* 00139140 */
void hg_music_fade_6(HGResourceEnv *, uint32_t level);                  /* 00139190 */
void hg_music_fade_5(HGResourceEnv *, uint32_t level);                  /* 001391f0 */
void hg_fade_service(HGResourceEnv *);                                 /* 00139240 */

void hg_pad_service(HGResourceEnv *);                                  /* 0013d970 */
void hg_pad_port(HGResourceEnv *, uint32_t pad);                       /* 0013dc30 */
uint32_t hg_pad_read(HGResourceEnv *, uint32_t pad, uint32_t analog);   /* 0013e090 */
uint32_t hg_pad_stick_dpad(HGResourceEnv *, uint32_t value, uint32_t axis); /* 0013e500 */
void hg_pad_digital_pressure(HGResourceEnv *, uint32_t pad);           /* 0013e550 */
uint32_t hg_pad_pressure(HGResourceEnv *, uint32_t value, uint32_t low,
                         uint32_t high);                               /* 0013e8f0, f0 */
void hg_pad_clear_buttons(HGResourceEnv *, uint32_t pad);              /* 0013d910 */
void hg_pad_clear_port(HGResourceEnv *, uint32_t pad);                 /* 0013dad0 */

void hg_menu_clear(HGResourceEnv *);                                   /* 00147f10 */
uint32_t hg_title_card_scan(HGResourceEnv *);                          /* 002cf020 */
uint32_t hg_title_menu(HGResourceEnv *);                               /* 00a87880 */
uint32_t hg_pause_menu(HGResourceEnv *);                               /* 00162750 */
void hg_menu_cursor(HGResourceEnv *, uint32_t cell, uint32_t limit);    /* 00161cf0 */

/* Bus router for the entries above (as hg_lifecycle_call). */
int hg_front_end_call(HGResourceEnv *, uint32_t pc, uint32_t a, uint32_t b,
                      uint32_t c, uint32_t d, uint32_t *result);
#endif
