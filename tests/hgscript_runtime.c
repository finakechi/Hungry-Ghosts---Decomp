#include "hgscript.h"

#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

enum { STATE = 0xa6bcc0, COLLECTION = 0x1200000, SCRATCH = 0x1400000,
       RAM_SIZE = 0x1500000, POOL = COLLECTION + 48 };

typedef struct {
    uint8_t *ram;
    uint32_t draw_count;
    uint32_t audio_count;
    uint32_t last_draw;
} Fixture;

static uint32_t load(void *user, uint32_t address, uint32_t width) {
    Fixture *f = user;
    if (address == 0x700000c4 && width == 4) return 0;
    assert(width == 1 || width == 2 || width == 4);
    assert(address <= RAM_SIZE - width);
    uint32_t result = 0;
    for (uint32_t i = 0; i < width; ++i)
        result |= (uint32_t)f->ram[address + i] << (i * 8);
    return result;
}

static void store(void *user, uint32_t address, uint32_t width, uint32_t value) {
    Fixture *f = user;
    assert(width == 1 || width == 2 || width == 4);
    assert(address <= RAM_SIZE - width);
    for (uint32_t i = 0; i < width; ++i)
        f->ram[address + i] = (uint8_t)(value >> (i * 8));
}

static uint32_t call(void *user, uint32_t entry, uint32_t a0, uint32_t a1,
                     uint32_t a2, uint32_t a3) {
    Fixture *f = user;
    assert(entry == 0x156550 && a0 == 1 && !a1 && !a2 && !a3);
    ++f->audio_count;
    return 0;
}

static uint32_t call_fp(void *user, uint32_t entry, uint32_t a0, uint32_t a1,
                        uint32_t a2, uint32_t a3, uint32_t x, uint32_t y) {
    Fixture *f = user;
    (void)x;
    (void)y;
    assert(!a3);
    if (entry == 0x182850) {
        assert(a1 && f->ram[a0] != 0);
        f->last_draw = a0;
        ++f->draw_count;
        return 6;
    }
    assert(entry == 0x1564a0 && a0 == 1 && a1 == 17 && a2 == 1);
    assert(x == 0x3f800000 && y == 0x3ca3d70a);
    ++f->audio_count;
    return 0;
}

static void put_words(Fixture *f, uint32_t address, const uint32_t *values,
                      uint32_t count) {
    for (uint32_t i = 0; i < count; ++i)
        store(f, address + i * 4, 4, values[i]);
}

int main(void) {
    Fixture fixture = { calloc(RAM_SIZE, 1), 0, 0, 0 };
    assert(fixture.ram);
    HGScriptEnv env = { &fixture, load, store, call, call_fp };
    const uint32_t records[] = {
        2, 48, 16, 36,
        (2 << 6) | 1, 5, (4 << 6) | 1, 0, 0,
        (6 << 6) | 7, (4 << 6) | 1, 0
    };
    const uint32_t pool[] = {
        0, 0, 36, 'A', 18, 'B', 17, 11, 0x02030000, UINT32_MAX
    };
    put_words(&fixture, COLLECTION, records, sizeof records / sizeof *records);
    put_words(&fixture, POOL, pool, sizeof pool / sizeof *pool);
    store(&fixture, HG_SCRIPT_HANDLES + 12, 4, COLLECTION);

    assert(hg_script_page_count(&env, 3, 0) == 1);
    assert(hg_script_walk(&env, COLLECTION + 16, POOL, 0, SCRATCH) == 36);
    hg_script_draw_handle(&env, 3, 0, 1, 0, 0, SCRATCH);
    assert(fixture.draw_count == 1 && fixture.last_draw == POOL + 20);

    hg_script_timed_init(&env, STATE, COLLECTION, 1, 0, 0);
    assert(load(&fixture, STATE, 4) == UINT32_MAX);
    store(&fixture, STATE, 4, 0);
    assert(hg_script_timed_tick(&env, STATE) == 0);
    assert(fixture.audio_count == 1 && load(&fixture, STATE + 0xc, 4) == COLLECTION + 40);
    assert(hg_script_timed_tick(&env, STATE) == 0);
    assert(load(&fixture, STATE + 0xc, 4) == COLLECTION + 40);
    store(&fixture, 0x3f2fa0, 1, 3);
    assert(hg_script_timed_tick(&env, STATE) == 0);
    assert(load(&fixture, 0x3f2fa0, 1) == 2);
    assert(load(&fixture, 0xa6bf58, 4) == 3);
    assert(fixture.ram[STATE + 0x29] == 'B');
    hg_script_timed_draw(&env, STATE, SCRATCH);
    assert(fixture.draw_count == 2 && fixture.last_draw == STATE + 0x29);
    assert(fixture.audio_count == 2);
    store(&fixture, 0x3f2fa0, 1, 0);
    assert(hg_script_timed_tick(&env, STATE) == -1);

    hg_script_format_number(&env, SCRATCH - 0x20, -2147483647, 0);
    assert(strcmp((const char *)fixture.ram + SCRATCH - 0x20, "-2147483647") == 0);
    free(fixture.ram);
    return 0;
}
