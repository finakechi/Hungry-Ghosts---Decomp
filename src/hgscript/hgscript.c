#include "hgscript.h"
#include "script_fp.h"

static uint32_t r(HGScriptEnv *e, uint32_t a, uint32_t n) {
    return e->load(e->user, a, n);
}
static void w(HGScriptEnv *e, uint32_t a, uint32_t n, uint32_t v) {
    e->store(e->user, a, n, v);
}
static uint32_t r32(HGScriptEnv *e, uint32_t a) { return r(e, a, 4); }
static void w32(HGScriptEnv *e, uint32_t a, uint32_t v) { w(e, a, 4, v); }
/* Fixed addresses used by the original runtime family. */
#define ADDR_TEXT_BUF_DEFAULT 0x003e0f80u
#define ADDR_COLOR_TABLE     0x003e0fc0u
#define ADDR_POS_VECTOR      0x0039b3e0u
#define ADDR_BUTTON_TEXT     0x003dbb80u
#define ADDR_BUTTON_MEASURE  0x003dbb78u
#define ADDR_LINE_ADVANCE    0x003eb050u

/* 00182490: SJIS lead-byte classification. */
uint32_t hg_text_classify(uint32_t byte) {
    uint32_t v1 = byte & 0xff;
    if (v1 >= 0x81 && v1 < 0xa0) return 1;
    if (v1 >= 0xe0 && v1 < 0xfd) return 1;
    return 0;
}

/* 0014b330: proportional width, primary font. */
uint32_t hg_text_width_ascii(uint32_t ch) {
    switch (ch) {
    case 'w': return 12;
    case 'r': return 9;
    case 'm': return 12;
    case 'l': return 4;
    case 'j': return 6;
    case 'i': return 4;
    case 'f': return 8;
    case 'W': return 12;
    case 'M': return 12;
    case 'J': return 7;
    case 'I': return 4;
    case '\'': return 4;
    default: return 9;
    }
}

/* 0014b690: proportional width, secondary (wider) font. */
uint32_t hg_text_width_ascii_alt(uint32_t ch) {
    switch (ch) {
    case 'v': case 'q': case 'l': case 'k': case 'h': case 'a': case 'S':
        return 14;
    case '~': case '|': case 'y': case 'x': case 'u': case 'p': case 'o':
    case 'g': case 'e': case 'd': case 'c': case 'b': case 'J': case 'F':
    case '9': case '8': case '7': case '6': case '5': case '4': case '3':
    case '2': case '1': case '0':
        return 13;
    case 'z': case 's': case 'r':
        return 12;
    case 't':
        return 10;
    case 'j': case 'i': case 'f': case 'I': case '\'': case '"':
        return 9;
    default:
        return 16;
    }
}

/* 00182540: pixel width of a NUL-terminated string. Single-byte characters
 * use the primary width table; SJIS pairs consume two bytes and 0x18 px. */
uint32_t hg_text_width(HGScriptEnv *e, uint32_t text) {
    uint32_t s1 = text, s0 = 0;
    uint32_t v0 = r(e, s1, 1);
    while (v0 != 0) {
        if (hg_text_classify(v0) == 1) {
            s0 += 0x18;
            s1 += 2;
        } else {
            s0 += hg_text_width_ascii(r(e, s1, 1));
            s1 += 1;
        }
        v0 = r(e, s1, 1);
    }
    return s0;
}

/* 001824e0: byte-wise measure using the secondary width table. */
uint32_t hg_text_width_bytes(HGScriptEnv *e, uint32_t text) {
    uint32_t s1 = text, s0 = 0;
    int32_t v0 = (int8_t)r(e, s1, 1);
    while (v0 != 0) {
        s0 += hg_text_width_ascii_alt((uint32_t)(uint8_t)v0);
        s1 += 1;
        v0 = (int8_t)r(e, s1, 1);
    }
    return s0;
}

/* 001839f0: if the record's first word has command 0xc, return the pool
 * entry pointer pool + 4*operand; otherwise zero. */
uint32_t hg_script_entry(HGScriptEnv *e, uint32_t record, uint32_t pool) {
    uint32_t v1 = r32(e, record);
    if ((v1 & 0x3f) != 0xc) return 0;
    return pool + ((v1 >> 6) << 2);
}

static uint32_t skip_pages(HGScriptEnv *e, uint32_t cursor, uint32_t count) {
    while (count) {
        uint32_t command = r32(e, cursor) & 63;
        if (!command) break;
        cursor += 4;
        if (command == 5) --count;
    }
    return cursor;
}

/* 00182ae0. The guest pointer cell is the only output. */
void hg_script_advance(HGScriptEnv *e, uint32_t cell, uint32_t count) {
    while ((int32_t)count > 0) {
        uint32_t cursor = r32(e, cell);
        uint32_t command = r32(e, cursor) & 63;
        if (!command) break;
        w32(e, cell, cursor + 4);
        if (command == 5) --count;
    }
}

uint32_t hg_script_line_count(HGScriptEnv *e, uint32_t record, uint32_t pages) {
    uint32_t lines = 0;
    for (uint32_t cursor = skip_pages(e, record, pages & 0xffff);; cursor += 4) {
        uint32_t command = r32(e, cursor) & 63;
        if (!command || command == 5) return lines;
        if (command == 2) ++lines;
    }
}

static uint32_t copy_text(HGScriptEnv *e, uint32_t dest, uint32_t source) {
    uint32_t byte;
    do {
        byte = r(e, source++, 1);
        w(e, dest++, 1, byte);
    } while (byte);
    return dest - 1;
}

/* 00182e90. The wrapped signed quotient for INT32_MIN is intentional. */
void hg_script_format_number(HGScriptEnv *e, uint32_t dest,
                             int32_t value, uint32_t full_width) {
    int wide = full_width == 1;
    if (!value && wide) {
        copy_text(e, dest, ADDR_BUTTON_TEXT);
        return;
    }
    if (value < 0) {
        if (wide) dest = copy_text(e, dest, ADDR_BUTTON_TEXT + 8);
        else w(e, dest++, 1, '-');
        value = (int32_t)(0u - (uint32_t)value);
    }
    int started = 0;
    for (int32_t divisor = 1000000000; divisor; divisor /= 10) {
        int32_t digit = value / divisor;
        value %= divisor;
        if (digit || started) {
            if (wide) {
                w(e, dest++, 1, r(e, ADDR_BUTTON_TEXT, 1));
                w(e, dest++, 1, r(e, ADDR_BUTTON_TEXT + 1, 1) + (uint32_t)digit);
            } else {
                w(e, dest++, 1, (uint32_t)('0' + digit));
            }
            started = 1;
        }
    }
    w(e, dest, 1, 0);
}

/* Shared address calculation used by the original handle entry points. */
static uint32_t resolve(HGScriptEnv *e, uint32_t handle, uint32_t index,
                        uint32_t *pool) {
    uint32_t collection = r32(e, HG_SCRIPT_HANDLES + handle * 4);
    *pool = collection + r32(e, collection + 4);
    return collection + r32(e, collection + 8 + index * 4);
}

uint32_t hg_script_handle_entry(HGScriptEnv *e, uint32_t handle, uint32_t index) {
    uint32_t pool;
    uint32_t record = resolve(e, handle, index, &pool);
    return hg_script_entry(e, record, pool);
}

uint32_t hg_script_page_count(HGScriptEnv *e, uint32_t handle, uint32_t index) {
    uint32_t pool, pages = 0;
    for (uint32_t cursor = resolve(e, handle, index, &pool);; cursor += 4) {
        uint32_t command = r32(e, cursor) & 63;
        if (!command) return pages;
        if (command == 5) ++pages;
    }
}

static uint32_t measure(HGScriptEnv *e, uint32_t cursor, uint32_t pool,
                        uint32_t flags, uint32_t scratch_top,
                        uint32_t parent, uint32_t parent_pool) {
    uint32_t widest = 0, width = 0, slot = 0;
    for (;;) {
        uint32_t event = r32(e, cursor);
        uint32_t command = event & 63, operand = event >> 6;
        switch (command) {
        case 0:
        case 5:
            if (parent) {
                cursor = parent;
                pool = parent_pool;
                parent = 0;
                break;
            }
            return (int32_t)widest < (int32_t)width ? width : widest;
        case 1: {
            uint32_t entry = pool + operand * 4;
            width += (flags & 0x80) ? hg_text_width_bytes(e, entry + 4) : r32(e, entry);
            break;
        }
        case 2:
            if ((int32_t)widest < (int32_t)width) widest = width;
            if (flags) return widest;
            width = 0;
            break;
        case 4:
            if (!(flags & 0x80)) {
                uint32_t entry = HG_SCRIPT_LINE_TABLE + slot++ * 16;
                uint32_t type = r32(e, entry), value = r32(e, entry + 4);
                if (type == 3 && !parent) {
                    parent = cursor;
                    parent_pool = pool;
                    cursor = resolve(e, value, r32(e, entry + 8), &pool);
                    continue;
                }
                if (type == 1 || type == 2) {
                    uint32_t buffer = scratch_top - 0x110;
                    hg_script_format_number(e, buffer, (int32_t)value, type == 1);
                    width += hg_text_width(e, buffer);
                } else if (type == 0) {
                    width += hg_text_width(e, value);
                }
            } else {
                ++slot;
            }
            break;
        case 14:
            width += hg_text_width(e, ADDR_BUTTON_MEASURE);
            break;
        default:
            break;
        }
        cursor += 4;
    }
}

uint32_t hg_script_walk(HGScriptEnv *e, uint32_t record, uint32_t pool,
                        uint32_t flags, uint32_t scratch_top) {
    return measure(e, record, pool, flags, scratch_top, 0, 0);
}

static void copy_words(HGScriptEnv *e, uint32_t dest, uint32_t source, uint32_t words) {
    for (uint32_t i = 0; i < words; ++i)
        w32(e, dest + 4 * i, r32(e, source + 4 * i));
}

static uint32_t button_text(uint32_t key) {
    switch (key) {
    case 0x80: return ADDR_BUTTON_MEASURE;
    case 0x40: return 0x003dbb90;
    case 0x20: return 0x003dbb98;
    case 0x10: return 0x003dbba0;
    default: return 0;
    }
}

static uint32_t center(uint32_t x, uint32_t width) {
    uint32_t half = hg_script_mul(0x3f000000, hg_script_bits((float)(int32_t)width));
    return hg_script_sub(x, half);
}

/* 001830b0. 0x20000 centers the page, 0x40000 centers its first line.
 * Subsequent lines center independently with 0x40000. */
void hg_script_walk_positioned(HGScriptEnv *e, uint32_t record, uint32_t pool,
                               uint32_t flags, uint32_t x, uint32_t y,
                               uint32_t scratch_top) {
    uint32_t cursor = skip_pages(e, record, flags & 0xffff);
    uint32_t origin = x, parent = 0, parent_pool = 0, slot = 0, key = 0;
    uint32_t frame = scratch_top - 0x1f0;
    if (flags & 0x20000) {
        origin = center(x, hg_script_walk(e, cursor, pool, 0, frame));
        x = origin;
    } else if (flags & 0x40000) {
        x = center(x, hg_script_walk(e, cursor, pool, 1, frame));
    }
    if (!(flags & 0x80000)) copy_words(e, HG_SCRIPT_TEXT_STATE, ADDR_TEXT_BUF_DEFAULT, 8);
    for (;; cursor += 4) {
        uint32_t event = r32(e, cursor);
        uint32_t command = event & 63, operand = event >> 6;
        uint32_t style = HG_SCRIPT_TEXT_STATE, text = 0;
        switch (command) {
        case 0:
        case 5:
            if (!parent) return;
            cursor = parent;
            pool = parent_pool;
            parent = 0;
            break;
        case 1:
            text = pool + operand * 4 + 4;
            break;
        case 2:
            x = origin;
            y = hg_script_add(y, hg_script_add(0x41400000, r32(e, ADDR_LINE_ADVANCE)));
            if (!(flags & 0x20000) && (flags & 0x40000))
                x = center(x, measure(e, cursor + 4, pool, 1, frame, parent, parent_pool));
            break;
        case 3:
            copy_words(e, HG_SCRIPT_POS_STATE, ADDR_POS_VECTOR + operand * 16, 3);
            break;
        case 4: {
            uint32_t entry = HG_SCRIPT_LINE_TABLE + slot++ * 16;
            uint32_t type = r32(e, entry), value = r32(e, entry + 4);
            if (type == 3 && !parent) {
                parent = cursor;
                parent_pool = pool;
                cursor = resolve(e, value, r32(e, entry + 8), &pool) - 4;
            } else if (type == 1 || type == 2) {
                text = frame + 0xe0;
                hg_script_format_number(e, text, (int32_t)value, type == 1);
            } else if (type == 0) {
                int32_t width = (int32_t)e->call_fp(e->user, 0x00182850, value,
                                                    style, flags, 0, x, y);
                x = hg_script_add(x, hg_script_bits((float)width));
            }
            break;
        }
        case 13:
            copy_words(e, HG_SCRIPT_TEXT_STATE + 4, ADDR_COLOR_TABLE + operand * 8, 2);
            break;
        case 14: {
            static const uint32_t bindings[] = {0x3eaec8, 0x3eaecc, 0x3eaec4, 0x3eaed0};
            if (operand < 4) key = r(e, bindings[operand], 2);
            style = frame + 0xc0;
            copy_words(e, style, HG_SCRIPT_TEXT_STATE, 8);
            text = button_text(key);
            break;
        }
        default:
            break;
        }
        if (text) {
            int32_t width = (int32_t)e->call_fp(e->user, 0x00182850, text, style, flags, 0, x, y);
            x = hg_script_add(x, hg_script_bits((float)width));
        }
    }
}

void hg_script_draw_handle(HGScriptEnv *e, uint32_t handle, uint32_t index,
                           uint32_t flags, uint32_t x, uint32_t y,
                           uint32_t scratch_top) {
    uint32_t pool;
    uint32_t record = resolve(e, handle, index, &pool);
    hg_script_walk_positioned(e, record, pool, flags, x, y, scratch_top);
}
