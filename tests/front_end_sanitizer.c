/* M18 standalone synthetic front-end harness under ASan/UBSan; no retail data.
 * Randomized mode, fade, pad, title and pause states drive the C front-end
 * services (fade setters and service, controller service, title menu and card
 * poll, card scan, new-game state, pause menu) and the connected main-loop
 * unit hg_main_unit. Front-end and lifecycle entries are routed to C through
 * hg_front_end_call / hg_lifecycle_call; every other callee returns a small
 * pseudo-random value. Host seams (0xfffffff0..) return 0, except the guest
 * frame, which returns a stack address. Guest addresses outside
 * RAM/scratch are counted, never dereferenced. */
#include "front_end.h"
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
    if (pc == 0xfffffff1u) return b ? 0x1ff0000u - (a & 0xfff0u) : 0;   /* guest frame */
    if (pc == HG_WAIT_INTERRUPT) {
        t->interrupts++;
        hg_vblank_start_handler(t->env);
        hg_vblank_end_handler(t->env);
        return 0;
    }
    if (pc >= 0xfffffff0u) return 0;
    if (t->depth < 8) {
        t->depth++;
        int routed = hg_front_end_call(t->env, pc, a, b, c, d, &result);
        if (!routed) routed = hg_lifecycle_call(t->env, pc, a, b, c, d, &result);
        t->depth--;
        if (routed) return result;
    }
    if (pc == 0x10dcf8) return rnd(t) % 8;          /* scePadGetState */
    return rnd(t) % 4;
}

static const uint32_t handlers[] = {0x121810, 0x121be0, 0x12d1c0, 0x12345678};

static void randomize(Test *t) {
    unsigned char *m = t->ram + 0x3ead30;
    for (int i = 0; i < 0x28; i++) m[i] = (unsigned char)(rnd(t) % 8);
    m[0xc] = (unsigned char)(rnd(t) % 0x14);       /* pause state, beyond the table */
    uint32_t h = handlers[rnd(t) % 4];
    memcpy(m + 4, &h, 4);
    m[0] = (unsigned char)(rnd(t) % 6);
    unsigned char *f = t->ram + 0x3eaeb0;           /* fade block */
    for (int i = 0; i < 0x14; i++) f[i] = (unsigned char)rnd(t);
    f[3] = (unsigned char)(rnd(t) % 9);
    unsigned char *p = t->ram + 0x95e340;           /* pad block */
    for (int i = 0x100; i < 0x240; i++) p[i] = (unsigned char)rnd(t);
    p[0x10a] = (unsigned char)(rnd(t) % 12);
    for (int i = 0; i < 0x40; i++) t->ram[0x966500 + i] = (unsigned char)(rnd(t) % 4);
    static const uint32_t bytes[] = {0x3eacec, 0x3fa1b0, 0x3fa1b1, 0x3fa1b2, 0xa6bf95, 0x95e44a};
    for (size_t i = 0; i < sizeof bytes / sizeof *bytes; i++) t->ram[bytes[i]] = (unsigned char)(rnd(t) % 4);
    for (uint32_t a = 0x3eaf00; a < 0x3eaf40; a++) t->ram[a] = (unsigned char)rnd(t);   /* pad copies */
    uint32_t flags[] = {rnd(t) % 3, rnd(t) % 3, rnd(t) % 3};
    memcpy(t->scratch + 0x410, &flags[0], 4);
    memcpy(t->scratch + 0xd4, &flags[1], 4);
    memcpy(t->scratch + 0xf8, &flags[2], 4);
    uint32_t pad = 0x95e340u;
    memcpy(t->ram + 0x3f0a50, &pad, 4);
    memcpy(t->scratch + 0xc0, (uint32_t[]){0x3ead30}, 4);
}

int main(void) {
    Test t = {0};
    t.ram = calloc(1, 0x2000000);
    assert(t.ram);
    HGResourceEnv env = {&t, load, store, call, NULL, NULL};
    t.env = &env; t.seed = 0x4d3138u;
    for (uint32_t i = 0; i < 0x480; i++) t.ram[0x3db1f0 + i] = (unsigned char)(i * 13u % 0x40u);
    hg_main_init(&env);
    for (int iteration = 0; iteration < 20000; iteration++) {
        randomize(&t);
        switch (iteration % 8) {
        case 0: hg_main_unit(&env); break;
        case 1: (void)hg_title_menu(&env); break;
        case 2: (void)hg_pause_menu(&env); break;
        case 3: hg_pad_service(&env); break;
        case 4: hg_fade_service(&env); break;
        case 5: (void)hg_title_card_scan(&env); (void)hg_title_card_poll(&env, rnd(&t) & 1); break;
        case 6: hg_new_game_state(&env); hg_menu_cursor(&env, 0x3ead50, rnd(&t) % 5); break;
        default:
            hg_fade_out(&env, rnd(&t) % 16, rnd(&t) % 3, rnd(&t) % 5, rnd(&t) % 4);
            hg_fade_in(&env, rnd(&t) % 16, rnd(&t) % 3, rnd(&t) % 4, rnd(&t) & 1);
            hg_music_fade_2(&env, rnd(&t) & 0xff);
            (void)hg_pad_pressure(&env, rnd(&t) & 0xff, 0, 0x3f800000u);
            break;
        }
    }
    printf("front-end sanitizer: 20000 iterations, %u calls, %u interrupts, %u domain faults, %u unmapped\n",
           t.calls, t.interrupts, t.faults, t.unmapped);
    free(t.ram);
    return 0;
}
