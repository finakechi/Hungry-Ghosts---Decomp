/* M17 standalone synthetic lifecycle harness under ASan/UBSan; no retail data.
 * Randomized mode-block and flag states drive the C mode dispatcher, the
 * gameplay/lifecycle/boot modes and the main-loop frame. Lifecycle entries
 * are routed to C through hg_lifecycle_call; every other callee returns a
 * small pseudo-random value. Guest addresses outside RAM/scratch/hardware are
 * counted, never dereferenced. */
#include "game_lifecycle.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    unsigned char *ram, scratch[0x4000];
    HGResourceEnv *env;
    uint32_t seed, unmapped, faults, calls, interrupts, depth;
} Test;

static uint32_t rnd(Test *t) { t->seed = t->seed * 1103515245u + 12345u; return t->seed >> 16; }

static unsigned char *byte_at(Test *t, uint32_t a) {
    if (a < 0x2000000u) return t->ram + a;
    if (a >= 0x70000000u && a < 0x70004000u) return t->scratch + (a - 0x70000000u);
    return NULL;
}
static uint32_t load(void *u, uint32_t a, uint32_t n) {
    Test *t = u; uint32_t v = 0;
    assert(n == 1 || n == 2 || n == 4);
    for (uint32_t i = 0; i < n; i++) {
        unsigned char *p = byte_at(t, a + i);
        if (!p) { t->unmapped++; continue; }
        v |= (uint32_t)*p << (8 * i);
    }
    return v;
}
static void store(void *u, uint32_t a, uint32_t n, uint32_t v) {
    Test *t = u;
    assert(n == 1 || n == 2 || n == 4);
    for (uint32_t i = 0; i < n; i++) {
        unsigned char *p = byte_at(t, a + i);
        if (!p) { t->unmapped++; continue; }
        *p = (unsigned char)(v >> (8 * i));
    }
}
static uint32_t call(void *u, uint32_t pc, uint32_t a, uint32_t b, uint32_t c, uint32_t d) {
    Test *t = u; uint32_t result = 0;
    t->calls++;
    if (pc == 0xfffffff0u) { t->faults++; return 0; }
    if (pc == HG_WAIT_INTERRUPT) {
        t->interrupts++;
        hg_vblank_start_handler(t->env);
        hg_vblank_end_handler(t->env);
        return 0;
    }
    if (pc >= 0xfffffff0u) return 0;
    if (t->depth < 8) {
        t->depth++;
        int routed = hg_lifecycle_call(t->env, pc, a, b, c, d, &result);
        t->depth--;
        if (routed) return result;
    }
    if (pc == 0x1219e0) return rnd(t) & 1;
    return rnd(t) % 4;
}

static const uint32_t handlers[] = {0x121810, 0x121be0, 0x12d1c0, 0x12345678};

static void randomize(Test *t) {
    unsigned char *m = t->ram + 0x3ead30;
    for (int i = 0; i < 0x28; i++) m[i] = (unsigned char)(rnd(t) % 8);
    uint32_t h = handlers[rnd(t) % 4];
    memcpy(m + 4, &h, 4);
    m[0] = (unsigned char)(rnd(t) % 6);
    static const uint32_t bytes[] = {0x3eacec, 0x3eaeb0, 0x3f9f3e, 0x3f9e39, 0x3f9f3f, 0x3f9e3a, 0x95e44a,
                                     0x3fa1b0, 0x3fa1b1, 0x3fa1b2, 0x3f9f6e, 0x3f9f3d, 0x3eaee8, 0x3eaee4};
    for (size_t i = 0; i < sizeof bytes / sizeof *bytes; i++) t->ram[bytes[i]] = (unsigned char)(rnd(t) % 4);
    uint32_t pad = rnd(t) & 0xffff, flag = rnd(t) & 1, accum = rnd(t) % 3 ? 0x3f800000u : 0x47c35800u;
    memcpy(t->ram + 0x3eaf04, &pad, 2);
    memcpy(t->ram + 0x46d90c, &flag, 4);
    memcpy(t->ram + 0x3eaeec, &accum, 4);
    uint32_t ptr = 0x700000u + (rnd(t) & 0xfff0u);
    memcpy(t->ram + 0x3eb008, &ptr, 4);
    memcpy(t->scratch + 0xc0, (uint32_t[]){0x3ead30}, 4);
}

int main(void) {
    Test t = {0};
    t.ram = calloc(1, 0x2000000);
    assert(t.ram);
    HGResourceEnv env = {&t, load, store, call, NULL, NULL};
    t.env = &env; t.seed = 0x4d3137u;
    for (uint32_t i = 0; i < 0x3e01b0 - 0x3e0170; i++) t.ram[0x3e0170 + i] = (unsigned char)(i * 7u % 0x40u);
    hg_main_init(&env);
    for (int iteration = 0; iteration < 20000; iteration++) {
        randomize(&t);
        if (iteration % 3 == 0) hg_main_frame(&env);
        else hg_mode_dispatch(&env);
        if (iteration % 5 == 0) (void)hg_area_pack_id(&env, rnd(&t) & 1);
    }
    printf("lifecycle sanitizer: 20000 iterations, %u calls, %u interrupts, %u domain faults, %u unmapped\n",
           t.calls, t.interrupts, t.faults, t.unmapped);
    free(t.ram);
    return 0;
}
