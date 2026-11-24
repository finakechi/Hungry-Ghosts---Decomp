#include "hgscript/title.h"

#include <assert.h>
#include <stdlib.h>

enum {
    RAM_SIZE = 0x2000000, PARENT = 0x1661500, CHILD = PARENT + 0xc9a00,
    HANDLE = 0x3f4bbc, BUSY = 0x3eacec, ACTIVE = 0x3f8e30, FADE = 0x3eaeb0,
    SCENE = 0x1e00000, SCRATCH = 0x1fffc00,
    STATE = HG_TITLE_SCRIPT_STATE, GUARD = STATE + 0x264
};

typedef struct {
    uint8_t *ram;
    unsigned draws;
    unsigned stops;
} Fixture;

static uint32_t load(void *user, uint32_t address, uint32_t width) {
    Fixture *f = user;
    if (address == 0x700000c4 && width == 4) return 1;
    assert(width == 1 || width == 2 || width == 4);
    assert(address <= RAM_SIZE - width);
    uint32_t value = 0;
    for (uint32_t i = 0; i < width; ++i)
        value |= (uint32_t)f->ram[address + i] << (8 * i);
    return value;
}

static void store(void *user, uint32_t address, uint32_t width, uint32_t value) {
    Fixture *f = user;
    assert(width == 1 || width == 2 || width == 4);
    assert(address <= RAM_SIZE - width);
    for (uint32_t i = 0; i < width; ++i)
        f->ram[address + i] = (uint8_t)(value >> (8 * i));
}

static uint32_t stop(void *user, uint32_t entry, uint32_t a0, uint32_t a1,
                     uint32_t a2, uint32_t a3) {
    Fixture *f = user;
    assert(entry == 0x156550 && a0 == 1 && !a1 && !a2 && !a3);
    ++f->stops;
    return 0;
}

static uint32_t draw(void *user, uint32_t entry, uint32_t text, uint32_t style,
                     uint32_t flags, uint32_t a3, uint32_t x, uint32_t y) {
    Fixture *f = user;
    assert(entry == 0x182850 && text == STATE + 0x29 && style == SCRATCH - 0x30);
    assert(load(f, text, 1) == 'A' && flags == 0x10000 && a3 == 0);
    assert(x == 0xc0800000 && y == 0xc2480000);
    ++f->draws;
    return 8;
}

int main(void) {
    Fixture f = { calloc(RAM_SIZE, 1), 0, 0 };
    assert(f.ram);
    HGResourceEnv env = { &f, load, store, stop, NULL, NULL };
    HGTitleScript title = { 0 };
    assert(!hg_title_script_start(&env, draw, &title, SCENE));
    store(&f, HANDLE, 4, PARENT);
    store(&f, PARENT, 4, 0x354b4c46);
    store(&f, PARENT + 4, 4, 20);
    store(&f, PARENT + 8, 4, 0x1e00);
    store(&f, PARENT + 16 + 18 * 4, 4, 0x0c0c9a00);
    store(&f, PARENT + 16 + 19 * 4, 4, 0x010c9f60);
    store(&f, CHILD, 4, 1);
    store(&f, CHILD + 4, 4, 24);
    store(&f, CHILD + 8, 4, 12);
    store(&f, CHILD + 12, 4, 1);
    store(&f, CHILD + 16, 4, (2 << 6) | 6);
    store(&f, CHILD + 24, 4, 8);
    store(&f, CHILD + 28, 1, 'A');

    store(&f, BUSY, 1, 1);
    assert(!hg_title_script_start(&env, draw, &title, SCENE));
    store(&f, BUSY, 1, 0);
    store(&f, ACTIVE, 2, 1);
    assert(!hg_title_script_start(&env, draw, &title, SCENE));
    store(&f, ACTIVE, 2, 0);
    store(&f, FADE, 1, 1);
    assert(!hg_title_script_start(&env, draw, &title, SCENE));
    store(&f, FADE, 1, 0);
    store(&f, PARENT + 16, 4, 0x0c000100);
    assert(!hg_title_script_start(&env, draw, &title, SCENE));
    store(&f, PARENT + 16, 4, 0);
    store(&f, PARENT + 16 + 19 * 4, 4, 0x010c9f64);
    assert(!hg_title_script_start(&env, draw, &title, SCENE));
    store(&f, PARENT + 16 + 19 * 4, 4, 0x010c9f60);
    assert(hg_title_script_start(&env, draw, &title, SCENE) == 1);
    assert(!hg_title_script_start(&env, draw, &title, SCENE));
    assert(hg_title_script_frame(&env, draw, &title, SCRATCH + 4) == HG_TITLE_SCRIPT_UNAVAILABLE);
    assert(hg_title_script_frame(&env, draw, &title, SCRATCH) == 1 && f.draws == 1);
    store(&f, HANDLE, 4, 0);
    assert(hg_title_script_frame(&env, draw, &title, SCRATCH) == HG_TITLE_SCRIPT_UNAVAILABLE);
    assert(!title.parent && f.draws == 1 && f.stops == 0);

    store(&f, HANDLE, 4, PARENT);
    store(&f, SCENE + 6, 1, 0);
    assert(hg_title_script_start(&env, draw, &title, SCENE));
    for (uint32_t i = 0; i < 512; ++i) store(&f, CHILD + 28 + i, 1, 'A');
    store(&f, GUARD, 4, 0x12345678);
    assert(hg_title_script_frame(&env, draw, &title, SCRATCH) == HG_SCRIPT_DISPLAY_OVERFLOW);
    assert(load(&f, STATE + 0x224, 4) == STATE + 0x24);
    assert(load(&f, GUARD, 4) == 0x12345678 && f.draws == 1);
    hg_title_script_release(&env, &title);
    assert(!title.parent && f.stops == 1 && load(&f, HANDLE, 4) == PARENT);
    assert(load(&f, STATE, 4) == UINT32_MAX && load(&f, STATE + 0x24, 1) == 2);
    free(f.ram);
    return 0;
}
