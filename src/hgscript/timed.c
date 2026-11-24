#include "hgscript.h"

#include "script_fp.h"

enum {
    PHASE = 0, WAIT = 4, FRAME = 8, CURSOR = 0xc, POOL = 0x10,
    RETURN_CURSOR = 0x14, RETURN_POOL = 0x18, PAGE = 0x1c, PAGE_POOL = 0x20,
    DISPLAY = 0x24, DISPLAY_TAIL = 0x224, PAGE_CHANGED = 0x228,
    SHOW_TEXT = 0x22c, OPACITY = 0x230, X = 0x234, Y = 0x238,
    CENTER = 0x23c, TRANSIENT = 0x244, VOICE_PENDING = 0x245,
    STYLE = 0x246, CUE_MODE = 0x247, CUE_CURSOR = 0x248,
    CUE_PARAMETER = 0x24c, CUE_FRAME = 0x250, ADVANCE = 0x254,
    LAST_COMMAND = 0x255, INPUT_LOCK = 0x256, ADVANCE_TIMER = 0x258,
    FADE = 0x260,
    VOICE_STATE = 0x003f2fa0, MODE = 0x003fa205, PAD_EDGE = 0x0095e568,
    CUE_FLAGS = 0x00a6bf48, CUE_VALUE = 0x00a6bf58, CUE_INTENSITY = 0x00a6bf68
};

static uint32_t load(HGScriptEnv *e, uint32_t address, uint32_t width) {
    return e->load(e->user, address, width);
}

static void store(HGScriptEnv *e, uint32_t address, uint32_t width, uint32_t value) {
    e->store(e->user, address, width, value);
}

static uint32_t word(HGScriptEnv *e, uint32_t address) {
    return load(e, address, 4);
}

static void set_word(HGScriptEnv *e, uint32_t address, uint32_t value) {
    store(e, address, 4, value);
}

static void end_display(HGScriptEnv *e, uint32_t tail) {
    store(e, tail, 1, 2);
    store(e, tail + 1, 1, 0);
    store(e, tail + 2, 1, 0);
}

static void cue_slots(HGScriptEnv *e, uint32_t state, uint32_t active) {
    for (uint32_t channel = 0; channel < 4; ++channel) {
        set_word(e, 0x00a6bf58 + channel * 4, state);
        set_word(e, 0x00a6bf48 + channel * 4, active);
        if (state == 0) set_word(e, CUE_INTENSITY + channel * 4, 0);
    }
}

void hg_script_timed_init(HGScriptEnv *e, uint32_t s, uint32_t collection,
                          uint32_t record_index, uint32_t x, uint32_t y) {
    set_word(e, s + PHASE, UINT32_MAX);
    set_word(e, s + WAIT, 0);
    set_word(e, s + FRAME, 0);
    uint32_t cursor = collection + word(e, collection + 8 + record_index * 4);
    uint32_t pool = collection + word(e, collection + 4);
    set_word(e, s + CURSOR, cursor);
    set_word(e, s + POOL, pool);
    set_word(e, s + RETURN_CURSOR, 0);
    set_word(e, s + RETURN_POOL, 0);
    set_word(e, s + PAGE, cursor);
    set_word(e, s + PAGE_POOL, pool);
    store(e, s + CUE_MODE, 1, 0);
    set_word(e, s + DISPLAY_TAIL, s + DISPLAY);
    end_display(e, s + DISPLAY);
    set_word(e, s + PAGE_CHANGED, 0);
    set_word(e, s + SHOW_TEXT, 0);
    set_word(e, s + OPACITY, 0x3f800000);
    set_word(e, s + X, x);
    set_word(e, s + Y, y);
    set_word(e, s + CENTER, 0xffff);
    store(e, s + TRANSIENT, 1, 0);
    store(e, s + VOICE_PENDING, 1, 0);
    cue_slots(e, UINT32_MAX, UINT32_MAX);
    for (uint32_t channel = 0; channel < 4; ++channel)
        set_word(e, CUE_INTENSITY + channel * 4, 0);
    store(e, s + STYLE, 1, 0);
    set_word(e, s + CUE_FRAME, 0);
    set_word(e, s + CUE_CURSOR, 0);
    store(e, s + LAST_COMMAND, 1, 0);
    store(e, s + INPUT_LOCK, 1, 0);
    store(e, s + ADVANCE, 1, 0);
    set_word(e, s + ADVANCE_TIMER, 0);
    set_word(e, s + 0x25c, 0);
    set_word(e, s + FADE, 0x3f800000);
}

/* 0025cf60 checks only the leading word. The frame dispatcher skips it. */
void hg_script_timed_prelude(HGScriptEnv *e, uint32_t s) {
    uint32_t event = word(e, word(e, s + CURSOR));
    if ((event & 63) == 11) {
        uint32_t operand = event >> 6;
        e->call(e->user, 0x0011f880, operand & 0xfff, operand >> 12, 0, 0);
    }
}

static void center_page(HGScriptEnv *e, uint32_t s) {
    if (word(e, s + CENTER) != 0xffff) return;
    uint32_t cursor = word(e, s + PAGE);
    uint32_t pool = word(e, s + PAGE_POOL);
    uint32_t width = 0, max_width = 0;
    for (;;) {
        uint32_t event = word(e, cursor);
        uint32_t command = event & 63;
        if (command == 1) width += word(e, pool + ((event >> 6) << 2));
        if (command == 0) {
            uint32_t parent = word(e, s + RETURN_CURSOR);
            if (parent) {
                cursor = parent + 4;
                pool = word(e, s + RETURN_POOL);
                set_word(e, s + RETURN_CURSOR, 0);
                set_word(e, s + RETURN_POOL, 0);
                continue;
            }
        }
        if (command == 0 || command == 2 || command == 5) {
            if ((int32_t)max_width < (int32_t)width) max_width = width;
            width = 0;
            if (command != 2) break;
        }
        cursor += 4;
    }
    int32_t x = -((int32_t)max_width / 2);
    set_word(e, s + CENTER, (uint32_t)x);
    set_word(e, s + X, hg_script_bits((float)x));
}

static void append_text(HGScriptEnv *e, uint32_t s, uint32_t text) {
    uint32_t tail = word(e, s + DISPLAY_TAIL);
    store(e, tail, 1, 0);
    store(e, tail + 1, 1, 3);
    store(e, tail + 2, 1, 0);
    store(e, tail + 3, 1, load(e, s + STYLE, 1));
    store(e, tail + 4, 1, 0);
    tail += 5;
    uint32_t byte;
    do {
        byte = load(e, text++, 1);
        store(e, tail++, 1, byte);
    } while (byte);
    set_word(e, s + DISPLAY_TAIL, tail);
    end_display(e, tail);
}

static void append_break(HGScriptEnv *e, uint32_t s) {
    uint32_t tail = word(e, s + DISPLAY_TAIL);
    store(e, tail, 1, 1);
    store(e, tail + 1, 1, 2);
    store(e, tail + 2, 1, word(e, s + CENTER));
    store(e, tail + 3, 1, word(e, s + CENTER) >> 8);
    store(e, tail + 4, 1, 0);
    set_word(e, s + DISPLAY_TAIL, tail + 5);
    end_display(e, tail + 5);
}

static void stop_voice(HGScriptEnv *e) {
    e->call(e->user, 0x00156550, 1, 0, 0, 0);
}

static void clear_pending_voice(HGScriptEnv *e, uint32_t s) {
    store(e, s + VOICE_PENDING, 1, 0);
    set_word(e, s + CUE_FRAME, 0);
    store(e, s + CUE_MODE, 1, 2);
}

static void cue_flags(HGScriptEnv *e, uint32_t channel, uint32_t value,
                      uint32_t type, int scheduled) {
    if (type == 2) {
        set_word(e, CUE_VALUE + channel * 4, value);
        return;
    }
    uint32_t address = CUE_FLAGS + channel * 4;
    uint32_t flags = word(e, address);
    if (flags == UINT32_MAX) {
        flags = 0;
        set_word(e, address, flags);
    }
    if (type == 1) {
        flags &= ~15u;
        set_word(e, address, flags);
        set_word(e, address, flags | (value + (scheduled != 0)));
    } else if (value) {
        flags |= 16;
        set_word(e, address, flags);
        if (!(flags & 15)) set_word(e, address, flags | 1);
        if (scheduled) set_word(e, CUE_INTENSITY + channel * 4, value);
    } else {
        set_word(e, address, flags & ~16u);
    }
}

static void tick_cues(HGScriptEnv *e, uint32_t s) {
    if (load(e, s + CUE_MODE, 1) != 2 || (int32_t)word(e, s + PHASE) < 0) {
        set_word(e, s + CUE_FRAME, 0);
        return;
    }
    uint32_t cursor = word(e, s + CUE_CURSOR);
    uint32_t frame = word(e, s + CUE_FRAME);
    while (cursor) {
        uint32_t cue = word(e, cursor);
        if (cue == UINT32_MAX || (int32_t)frame < (int32_t)(cue & 0xffff)) break;
        uint32_t type = (cue >> 24) & 15;
        if (type <= 2) cue_flags(e, cue >> 28, (cue >> 16) & 255, type, 1);
        cursor += 4;
        set_word(e, s + CUE_CURSOR, cursor);
    }
    set_word(e, s + CUE_FRAME, frame + 1);
}

static uint32_t page_ready(HGScriptEnv *e, uint32_t s) {
    uint32_t advance = load(e, s + ADVANCE, 1);
    if (advance == 0 || advance == 1 || load(e, s + DISPLAY, 1) == 2) return 1;
    if (advance != 2) return 0;
    if (load(e, PAD_EDGE, 2) & 0x20) return 2;
    uint32_t timer = hg_script_sub(word(e, s + ADVANCE_TIMER), 0x3f800000);
    set_word(e, s + ADVANCE_TIMER, timer);
    return hg_script_float(timer) <= 0.0f;
}

static void tick_wait(HGScriptEnv *e, uint32_t s) {
    uint32_t command = load(e, s + LAST_COMMAND, 1);
    uint32_t advance = load(e, s + ADVANCE, 1);
    if (((command >= 1 && command <= 4) || (command >= 8 && command <= 10)) &&
        !load(e, s + INPUT_LOCK, 1) &&
        (load(e, PAD_EDGE, 2) & 0x20)) {
        if (advance == 0) {
            store(e, s + ADVANCE, 1, 1);
            stop_voice(e);
            set_word(e, s + CUE_CURSOR, 0);
            cue_slots(e, 0, 1);
            if (load(e, MODE, 1) == 2 && word(e, 0x700000c4) == 2) advance = 1;
        }
        if (advance == 1) {
            store(e, s + ADVANCE, 1, 2);
            set_word(e, s + WAIT, 0);
            set_word(e, s + ADVANCE_TIMER, 0);
            set_word(e, s + PHASE, 0);
        }
    } else if (command == 7 && advance) {
        set_word(e, s + WAIT, 0);
    } else if (command == 5 && advance && (int32_t)word(e, s + WAIT) >= 21) {
        set_word(e, s + WAIT, 20);
    }
    uint32_t wait = word(e, s + WAIT) - 1;
    set_word(e, s + WAIT, wait);
    if ((int32_t)wait <= 0) {
        if (command == 5) set_word(e, s + ADVANCE_TIMER, 0);
        set_word(e, s + PHASE, 0);
    }
}

int32_t hg_script_timed_tick(HGScriptEnv *e, uint32_t s) {
    store(e, s + TRANSIENT, 1, 0);
    for (uint32_t channel = 0; channel < 4; ++channel)
        set_word(e, 0x00a6bf58 + channel * 4, UINT32_MAX);

    uint32_t phase = word(e, s + PHASE);
    if (phase == 1) tick_wait(e, s);
    int yield = phase != 0;
    while (!yield) {
        if (load(e, s + VOICE_PENDING, 1)) {
            if (load(e, VOICE_STATE, 1) != 3) break;
            store(e, VOICE_STATE, 1, 2);
            clear_pending_voice(e, s);
        }
        uint32_t cursor = word(e, s + CURSOR);
        uint32_t event = word(e, cursor);
        uint32_t command = event & 63;
        uint32_t operand = event >> 6;
        uint32_t pool = word(e, s + POOL);
        switch (command) {
        case 0: {
            uint32_t parent = word(e, s + RETURN_CURSOR);
            if (parent) {
                cursor = parent;
                set_word(e, s + CURSOR, parent);
                set_word(e, s + POOL, word(e, s + RETURN_POOL));
                set_word(e, s + RETURN_CURSOR, 0);
                set_word(e, s + RETURN_POOL, 0);
            } else if (load(e, VOICE_STATE, 1)) {
                stop_voice(e);
                yield = 1;
                continue;
            } else {
                set_word(e, s + PHASE, UINT32_MAX);
                store(e, s + ADVANCE, 1, 0);
                cue_slots(e, UINT32_MAX, UINT32_MAX);
                for (uint32_t actor = 2; actor < 16; ++actor) {
                    uint32_t address = word(e, 0x00a6b410 + (actor - 2) * 0x70);
                    if (address && load(e, address + 0x2c2, 1))
                        set_word(e, word(e, address + 0x2bc) + 0x14, 0);
                }
                yield = 1;
            }
            break;
        }
        case 1: {
            uint32_t text = pool + operand * 4 + 4;
            if (load(e, text, 1)) {
                store(e, s + LAST_COMMAND, 1, 1);
                set_word(e, s + PAGE_CHANGED, 0);
                set_word(e, s + OPACITY, 0x3f800000);
                set_word(e, s + SHOW_TEXT, 1);
                center_page(e, s);
                append_text(e, s, text);
            }
            break;
        }
        case 2:
            set_word(e, s + CURSOR, cursor + 4);
            center_page(e, s);
            append_break(e, s);
            continue;
        case 3:
            store(e, s + LAST_COMMAND, 1, 3);
            store(e, s + STYLE, 1, operand);
            break;
        case 4:
            store(e, s + LAST_COMMAND, 1, 3);
            break;
        case 5: {
            uint32_t ready = page_ready(e, s);
            if (!ready) {
                yield = 1;
                continue;
            }
            if (load(e, s + ADVANCE, 1) == 1) store(e, s + ADVANCE, 1, 2);
            store(e, s + LAST_COMMAND, 1, 5);
            set_word(e, s + PAGE_CHANGED, 1);
            set_word(e, s + SHOW_TEXT, 0);
            set_word(e, s + CENTER, 0xffff);
            set_word(e, s + PAGE, cursor);
            set_word(e, s + PAGE_POOL, pool);
            set_word(e, s + PAGE, cursor + 4);
            if (ready == 2) cue_slots(e, 0, 1);
            break;
        }
        case 6:
            if (operand) {
                uint32_t count = operand - 1;
                if (load(e, s + LAST_COMMAND, 1) == 5 || !load(e, s + ADVANCE, 1)) {
                    set_word(e, s + WAIT, count);
                    set_word(e, s + PHASE, 1);
                    yield = 1;
                }
                uint32_t timer = hg_script_add(word(e, s + ADVANCE_TIMER), hg_script_bits((float)count));
                set_word(e, s + ADVANCE_TIMER, timer);
            }
            break;
        case 7: {
            if (load(e, MODE, 1) != 2) set_word(e, s + FADE, 0);
            store(e, s + LAST_COMMAND, 1, 7);
            store(e, s + ADVANCE, 1, 0);
            if (!load(e, s + CUE_MODE, 1)) store(e, s + CUE_MODE, 1, 1);
            uint32_t block = pool + operand * 4;
            uint32_t voice = word(e, block);
            uint32_t parameter = word(e, block + 4);
            set_word(e, s + CUE_PARAMETER, parameter);
            set_word(e, s + CUE_CURSOR, parameter == UINT32_MAX ? 0 : block + 8);
            if (load(e, VOICE_STATE, 1)) {
                stop_voice(e);
                yield = 1;
                continue;
            }
            if (voice) {
                e->call_fp(e->user, 0x001564a0, 1, voice, 1, 0, 0x3f800000, 0x3ca3d70a);
                store(e, s + VOICE_PENDING, 1, 1);
            } else {
                clear_pending_voice(e, s);
                stop_voice(e);
            }
            break;
        }
        case 8:
        case 9:
        case 10: {
            store(e, s + LAST_COMMAND, 1, command);
            uint32_t channel = (operand >> 4) & 15;
            if (channel < 4) cue_flags(e, channel, operand & 15, command - 8, 0);
            break;
        }
        default:
            break;
        }
        set_word(e, s + CURSOR, cursor + 4);
    }
    set_word(e, s + FRAME, word(e, s + FRAME) + 1);
    tick_cues(e, s);
    return (int32_t)word(e, s + PHASE);
}

/* 001cd9c0 walks the compiled display buffer and owns the fade/reset between
 * pages. The synchronous text draw receives the style bytes in guest scratch. */
void hg_script_timed_draw(HGScriptEnv *e, uint32_t s, uint32_t scratch_top) {
    uint32_t style = scratch_top - 0x30;
    uint32_t x = word(e, s + X), y = word(e, s + Y);
    uint32_t fade = word(e, s + FADE);
    if (load(e, MODE, 1) != 2 && word(e, 0x700000c4) == 2 &&
        !load(e, s + ADVANCE, 1)) {
        fade = hg_script_sub(fade, 0x3d75c28f);
        if (hg_script_float(fade) <= 0.0f) fade = 0;
    } else {
        fade = hg_script_add(fade, 0x3d75c28f);
        if (hg_script_float(fade) >= 1.0f) fade = 0x3f800000;
    }
    set_word(e, s + FADE, fade);

    uint32_t cursor = s + DISPLAY;
    for (;;) {
        uint32_t command = load(e, cursor, 1);
        uint32_t text = cursor + 2 + load(e, cursor + 1, 1);
        if (command == 2) break;
        if (command == 0) {
            uint32_t opacity = (load(e, cursor + 2, 1) + 4) & 255;
            if (opacity >= 127) opacity = 127;
            store(e, cursor + 2, 1, opacity);
            set_word(e, style, 2);
            uint32_t color = 0x0039b3e0 + load(e, cursor + 3, 1) * 16;
            for (uint32_t i = 0; i < 16; i += 4)
                set_word(e, style + 16 + i, word(e, color + i));
            uint32_t coverage = hg_script_bits((float)opacity / 127.0f);
            coverage = hg_script_mul(coverage, word(e, s + OPACITY));
            coverage = hg_script_mul(fade, coverage);
            set_word(e, style + 28, hg_script_mul(word(e, style + 28), coverage));
            store(e, style + 13, 1, load(e, cursor + 4, 1));
            int32_t width = (int32_t)e->call_fp(e->user, 0x00182850, text, style,
                                                word(e, s + 0x240), 0, x, y);
            x = hg_script_add(x, hg_script_bits((float)width));
        } else if (command == 1) {
            y = hg_script_add(y, hg_script_add(0x41400000, word(e, 0x003eb050)));
            uint32_t offset = load(e, cursor + 2, 1) | (load(e, cursor + 3, 1) << 8);
            x = hg_script_bits((float)(int16_t)offset);
        }
        while (load(e, text, 1)) ++text;
        cursor = text + 1;
    }
    if (word(e, s + PAGE_CHANGED)) {
        uint32_t opacity = hg_script_sub(word(e, s + OPACITY), 0x3d75c28f);
        if (hg_script_float(opacity) <= 0.0f) {
            set_word(e, s + DISPLAY_TAIL, s + DISPLAY);
            end_display(e, s + DISPLAY);
            set_word(e, s + PAGE_CHANGED, 0);
            opacity = 0x3f800000;
            set_word(e, s + SHOW_TEXT, 1);
        }
        set_word(e, s + OPACITY, opacity);
    }
}
