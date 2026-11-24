#ifndef HG_HGSCRIPT_H
#define HG_HGSCRIPT_H

#include <stdint.h>

/* Guest bus for authenticated collections and their initialized game state.
 * Load/store widths are in bytes. The renderer/audio callbacks execute
 * synchronously and may inspect guest pointers before the call returns. */
typedef struct {
    void *user;
    uint32_t (*load)(void *, uint32_t address, uint32_t width);
    void (*store)(void *, uint32_t address, uint32_t width, uint32_t value);
    uint32_t (*call)(void *, uint32_t entry, uint32_t a0, uint32_t a1,
                     uint32_t a2, uint32_t a3);
    uint32_t (*call_fp)(void *, uint32_t entry, uint32_t a0, uint32_t a1,
                        uint32_t a2, uint32_t a3, uint32_t f12, uint32_t f13);
} HGScriptEnv;

#define HG_SCRIPT_HANDLES     0x003f4300u
#define HG_SCRIPT_LINE_TABLE  0x009e7320u
#define HG_SCRIPT_TEXT_STATE  0x009e7300u
#define HG_SCRIPT_POS_STATE   0x009e7310u

/* Static and paged text. scratch_top is an aligned guest stack top.
 * Reserve at least 0x400 bytes below it. Position arguments are f32 bits. */
uint32_t hg_script_walk(HGScriptEnv *, uint32_t record, uint32_t pool,
                        uint32_t flags, uint32_t scratch_top);
void hg_script_walk_positioned(HGScriptEnv *, uint32_t record, uint32_t pool,
                               uint32_t flags, uint32_t x, uint32_t y,
                               uint32_t scratch_top);
void hg_script_draw_handle(HGScriptEnv *, uint32_t handle, uint32_t index,
                           uint32_t flags, uint32_t x, uint32_t y,
                           uint32_t scratch_top);
uint32_t hg_script_page_count(HGScriptEnv *, uint32_t handle, uint32_t index);
uint32_t hg_script_line_count(HGScriptEnv *, uint32_t record, uint32_t pages);
uint32_t hg_script_entry(HGScriptEnv *, uint32_t record, uint32_t pool);
uint32_t hg_script_handle_entry(HGScriptEnv *, uint32_t handle, uint32_t index);
void hg_script_advance(HGScriptEnv *, uint32_t cursor_cell, uint32_t pages);

uint32_t hg_text_width(HGScriptEnv *, uint32_t text);
uint32_t hg_text_width_bytes(HGScriptEnv *, uint32_t text);
uint32_t hg_text_classify(uint32_t byte);
uint32_t hg_text_width_ascii(uint32_t ch);
uint32_t hg_text_width_ascii_alt(uint32_t ch);

/* 00182e90 writes a NUL-terminated result. Reserve at least 24 bytes. */
void hg_script_format_number(HGScriptEnv *, uint32_t buffer,
                             int32_t value, uint32_t full_width);

/* Timed dialogue, original entries 001ccd80 and 001ccfa0.
 * A caller supplies the aligned EE address of an authenticated collection and
 * a valid record index. Reserve HG_SCRIPT_TIMED_SIZE bytes for state. As in
 * the original, initialize idle (-1), then set state word +0 to 0 to play.
 * Call tick once per frame and preserve guest memory between calls. Return
 * values are -1 idle/done, 0 dispatching or waiting for input/audio, 1 timer.
 * Slot globals and voice status are shared, as on the EE. The caller owns
 * audio servicing and display consumption. Callbacks are required. */
#define HG_SCRIPT_TIMED_SIZE 0x264u
void hg_script_timed_init(HGScriptEnv *, uint32_t state, uint32_t collection,
                          uint32_t record_index, uint32_t x_bits, uint32_t y_bits);
/* Optional caller prelude (0025cf60). Run once on entry paths that use it. */
void hg_script_timed_prelude(HGScriptEnv *, uint32_t state);
int32_t hg_script_timed_tick(HGScriptEnv *, uint32_t state);
void hg_script_timed_draw(HGScriptEnv *, uint32_t state, uint32_t scratch_top);

#endif
