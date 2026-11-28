#include "scene/title_scene.h"

#include <assert.h>
#include <stdlib.h>

enum { MODE = 0x3ead30, ROOT = 0x4d8150, TASK = 0xa6b0f0, FADE = 0x3eaeb0 };
typedef struct {
    uint8_t *ram;
    uint32_t calls[32];
    unsigned count;
    unsigned can_start;
    unsigned done;
} Fixture;

static uint32_t load(void *user, uint32_t address, uint32_t width) {
    Fixture *f = user;
    if (address == 0x700000c0 && width == 4) return MODE;
    if (address == 0x700000c4 && width == 4) return 1;
    if (address >= 0x70000000) address -= 0x70000000 - 0x2000000;
    assert(width && width <= 4 && address <= 0x2004000 - width);
    uint32_t value = 0;
    for (uint32_t i = 0; i < width; ++i)
        value |= (uint32_t)f->ram[address + i] << (8 * i);
    return value;
}

static void store(void *user, uint32_t address, uint32_t width, uint32_t value) {
    Fixture *f = user;
    if (address >= 0x70000000) address -= 0x70000000 - 0x2000000;
    assert(width && width <= 4 && address <= 0x2004000 - width);
    for (uint32_t i = 0; i < width; ++i)
        f->ram[address + i] = (uint8_t)(value >> (8 * i));
}

static uint32_t call(void *user, uint32_t entry, uint32_t a0, uint32_t a1,
                     uint32_t a2, uint32_t a3) {
    Fixture *f = user;
    assert(f->count < sizeof f->calls / sizeof *f->calls);
    f->calls[f->count++] = entry;
    switch (entry) {
    case 0x1429b0: assert(a0 == 0 && a1 == 5 && a2 == 0xa88a20 && !a3); return ROOT;
    case 0x25cd90: return f->can_start;
    case 0x25e930: assert(a0 == 0xa8a580 && a1 == 0xff); return 0;
    case 0x25ce90: return f->done;
    case 0x142e90: assert(a0 == ROOT); return 0;
    case 0x166910: assert(a0 == 2 && a1 == 0x70000ba0); return 0;
    case 0x13b6e0:
    case 0x13ce80: assert(a0 == 0x3f97f0); return 0;
    default: return 0;
    }
}

static uint32_t dispatch(HGResourceEnv *env, HGTitleScene *scene, uint32_t entry,
                         uint32_t a0) {
    uint32_t result;
    assert(hg_title_scene_call(env, scene, entry, a0, &result));
    return result;
}

int main(void) {
    Fixture f = {0};
    f.ram = calloc(0x2004000, 1);
    assert(f.ram);
    HGResourceEnv env = { &f, load, store, call, NULL, NULL };
    HGTitleScene scene = {0};
    dispatch(&env, &scene, 0xa88660, 0);
    assert(f.count == 1 && f.calls[0] == 0x1429b0);
    dispatch(&env, &scene, 0xa88a20, ROOT);
    assert(f.count == 2 && f.calls[1] == 0x25cd90);
    assert(load(&f, ROOT + 0x18, 2) == 1 && load(&f, 0x3f9fdf, 1) == 1);

    f.can_start = 1;
    dispatch(&env, &scene, 0xa88a20, ROOT);
    assert(f.calls[3] == 0x25e6c0 && f.calls[4] == 0x25e930);
    assert(load(&f, ROOT + 0x18, 2) == 0x101);
    dispatch(&env, &scene, 0xa88a20, ROOT);
    assert(load(&f, ROOT + 0x19, 1) == 1);
    f.done = 1;
    dispatch(&env, &scene, 0xa88a20, ROOT);
    dispatch(&env, &scene, 0xa88a20, ROOT);
    assert(load(&f, ROOT + 0x18, 1) == 3 && f.calls[f.count - 1] == 0x25ce90);
    dispatch(&env, &scene, 0xa88a20, ROOT);
    assert(f.calls[f.count - 1] == 0x142e90);

    f.count = 0;
    dispatch(&env, &scene, 0x141470, 0);
    const uint32_t order[] = {0x198f60, 0x12c980, 0x13b6e0, 0x1430a0, 0x143100, 0x18c520,
                              0x143150, 0x1431a0, 0x25c170, 0x182480, 0x13ce80, 0x137ac0};
    assert(f.count == sizeof order / sizeof *order);
    for (unsigned i = 0; i < f.count; ++i) assert(f.calls[i] == order[i]);

    f.count = 0;
    assert(dispatch(&env, &scene, 0xa88b40, TASK) == 1);
    assert(f.count == 2 && f.calls[0] == 0x166770 && f.calls[1] == 0x1390b0);
    store(&f, 0x95e568, 2, 0x20);
    assert(dispatch(&env, &scene, 0xa88b40, TASK) == 1);
    assert(load(&f, TASK + 5, 2) == 2 && f.calls[f.count - 1] == 0x139040);
    assert(dispatch(&env, &scene, 0xa88b40, TASK) == 1);
    store(&f, FADE, 1, 2);
    assert(dispatch(&env, &scene, 0xa88b40, TASK) == 0);
    assert(f.calls[f.count - 3] == 0x156550 && f.calls[f.count - 2] == 0x139010);
    assert(load(&f, HG_TITLE_SCRIPT_STATE, 4) == UINT32_MAX);
    assert(load(&f, 0x3f9fdf, 1) == 0);

    store(&f, MODE + 0x1a, 2, 1);
    dispatch(&env, &scene, 0xa886e0, 0);
    assert(load(&f, MODE + 0x1a, 2) == 0);
    free(f.ram);
    return 0;
}
