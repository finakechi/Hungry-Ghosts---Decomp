/* M25 connected world-session driver and independent checker.
 *
 * Runs two original scenes through cold boot -> publication -> world setup ->
 * first tick -> fade -> >= 120 post-readiness controller updates -> teardown,
 * interleaved A1, B1, A2, B2 on one harness, then targeted faults and invalid
 * inputs. The checker below never calls model logic: it reads public
 * snapshots, guest memory, the call log and the ledgers, and keeps its own
 * layout constants, 0012f850 order, floor evaluation and ledger replay.
 *
 * Usage: world_session_driver [--json]. Exit status 0 only when every positive
 * check passes, every control is detected by its intended check without a
 * forbidden one, and every invalid input fails with the expected error. */
#include "scene/world_init.h"

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---- Checker-owned guest layout (ELF-verified addresses, see docs/M23.md) */
enum {
    PLAYER = 0x003f92d0u, CAMERA = 0x003f9860u, HANDLES = 0x003f4300u,
    NODE_BASE = 0x0046e140u, NODE_STRIDE = 0xd0u, NODE_COUNT = 2048u,
    NODE_STACK = 0x004d6140u, NODE_TOP = 0x004d8140u, NODE_FREE = 0x004d8144u,
    CTRL = 0x00539430u,
    LIST_MAIN = 0x00539448u, LIST_PRIO = 0x00539450u,
    LIST_T2 = 0x00539458u, LIST_T2_PRIO = 0x00539460u,
    ARENA_SPAN = 0x00010000u, HANDLE_SCAN = 0x28fu, /* ids 0..0x28e; 003f4d3c starts the arena slots */
    MAX_ROWS = 512u, MAX_FAIL = 64u, MAX_CYCLES = 4u
};
static const uint32_t POOL_BASE[3] = {0x004d8150u, 0x0051c3b0u, 0x00528430u};
static const uint32_t POOL_STRIDE[3] = {0x3d0u, 0x4d0u, 0x110u};
static const uint32_t POOL_COUNT[3] = {286u, 40u, 256u};
/* Independent copy of the 0012f850 order; tools/verify_world_session.py
 * compares it with the jal sequence scanned from the authenticated ELF. */
static const uint32_t ELF_ORDER[18] = {
    0x0031bb80u, 0x00198f60u, 0x0017f6d0u, 0x0017f2d0u, 0x00158560u, 0x0012c790u,
    0x0013b6e0u, 0x001430a0u, 0x00143100u, 0x0012bc70u, 0x0018c520u, 0x00143150u,
    0x001431a0u, 0x0025c170u, 0x00182480u, 0x0013ce80u, 0x00137890u, 0x00189be0u
};
/* Required order of world-setup effects (dependency order, docs/M25.md). */
static const uint32_t SETUP_ORDER[12] = {
    0x00141b10u, 0x00141910u, 0x00141210u, 0x0013fef0u, 0x0012bf70u, 0x0013a4c0u,
    0x00189aa0u, 0x00158a10u, 0x001e3440u, 0x00158470u, 0x00158968u, 0x0012c790u
};

typedef enum { SEG_NEUTRAL, SEG_WALK, SEG_TURN, SEG_WALL, SEG_RELEASE, SEG_RETURN, SEG_HOLD } SegKind;
static const char *SEG_NAMES[] = {"neutral", "walk-floor", "turn", "wall-slide", "release", "return-turn", "hold"};

typedef struct { SegKind kind; uint32_t frames; uint8_t lx, ly; } Segment;

/* Script: 12 + 26 + 20 + 34 + 16 + 14 + 12 = 134 post-readiness updates. */
static const Segment SCRIPT[] = {
    {SEG_NEUTRAL, 12u, 0x80u, 0x80u}, {SEG_WALK, 26u, 0x80u, 0x00u},
    {SEG_TURN, 20u, 0x10u, 0x00u},    {SEG_WALL, 34u, 0x80u, 0x00u},
    {SEG_RELEASE, 16u, 0x80u, 0x80u}, {SEG_RETURN, 14u, 0xf0u, 0x00u},
    {SEG_RELEASE, 12u, 0x80u, 0x80u}
};
enum { SCRIPT_LEN = sizeof(SCRIPT) / sizeof(SCRIPT[0]) };

typedef struct {
    uint32_t step, readiness, post_ready, seg;
    uint8_t lx, ly, grounded, phase;
    float pos[3], rot_y, eye[3], target[3], pitch, yaw, speed;
    uint32_t walls, floors, c3fa190, c46e118, c46d930, c3eaef0, coord, free_nodes;
    uint32_t main_n, prio_n, t2_n, block6e, fade;
} Row;

typedef struct {
    char detector[24];
    char message[160];
} Failure;

typedef struct {
    const HGWorldSceneSpec *spec;
    uint32_t cycle;
    HGWorldInitError publish_err, enter_err, step_err, teardown_err;
    uint32_t fade_frames, post_ready, rows;
    Row row[MAX_ROWS];
    Failure fail[MAX_FAIL];
    uint32_t fail_count, fail_total;
    /* Checkpoint facts for the report. */
    uint32_t enter_free_nodes, enter_player_nodes, enter_owned_nodes, enter_main, enter_prio, enter_t2;
    uint32_t pool_free_after[3], free_after_teardown, owned_after_teardown;
    uint32_t wall_contact_frames, slide_frames, floor_changes, coord_ticks_checked;
    uint32_t handle_player, handle_area, block6e_first;
    float first_pos[3], first_eye[3], first_target[3], final_pos[3], final_yaw;
    uint32_t effects_in_order, ledger_events, history_events;
    uint32_t calls_logged;
    uint32_t ledger_by_op[7];
} CycleReport;

static void failf(CycleReport *c, const char *det, const char *fmt, ...) {
    c->fail_total++;
    for (uint32_t i = 0; i < c->fail_count; ++i)
        if (strcmp(c->fail[i].detector, det) == 0)
            return; /* first message per detector; total keeps counting */
    if (c->fail_count >= MAX_FAIL)
        return;
    Failure *f = &c->fail[c->fail_count++];
    snprintf(f->detector, sizeof(f->detector), "%s", det);
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(f->message, sizeof(f->message), fmt, ap);
    va_end(ap);
}

static bool fired(const CycleReport *c, const char *det) {
    for (uint32_t i = 0; i < c->fail_count; ++i)
        if (strcmp(c->fail[i].detector, det) == 0)
            return true;
    return false;
}

static uint32_t g32(const HGWorldInitHarness *h, uint32_t a) { return hg_world_harness_guest_load(h, a, 4u); }
static uint32_t g8(const HGWorldInitHarness *h, uint32_t a) { return hg_world_harness_guest_load(h, a, 1u); }

/* ---- Independent floor evaluation ---------------------------------------- */
static float expected_floor(const HGWorldSceneSpec *s, float x, float z) {
    float y = s->base.floor_y;
    for (uint32_t i = 0; i < s->tier_count; ++i) {
        const HGWorldFloorTier *t = &s->tiers[i];
        if (x >= t->min_x && x <= t->max_x && z >= t->min_z && z <= t->max_z && t->height > y)
            y = t->height;
    }
    return y;
}

/* ---- Independent ownership audit ----------------------------------------- */
static bool node_ok(uint32_t n) {
    return n >= NODE_BASE && n < NODE_BASE + NODE_COUNT * NODE_STRIDE && (n - NODE_BASE) % NODE_STRIDE == 0u;
}

static int pool_of(uint32_t a, uint32_t *slot) {
    for (int p = 0; p < 3; ++p) {
        if (a >= POOL_BASE[p] && a < POOL_BASE[p] + POOL_COUNT[p] * POOL_STRIDE[p] &&
            (a - POOL_BASE[p]) % POOL_STRIDE[p] == 0u) {
            *slot = (a - POOL_BASE[p]) / POOL_STRIDE[p];
            return p;
        }
    }
    return -1;
}

typedef struct {
    uint8_t node_state[NODE_COUNT];   /* 0 unseen, 1 free, 2 owned */
    uint8_t actor_state[3][286];      /* 0 unseen, 1 free, 2 active */
    uint32_t free_nodes, owned_nodes, active[4], free_actors[3];
} Audit;

/* Walk guest memory: free stack, every owner table, every list, every free
 * list. Report double accounting, gaps and ledger disagreements. */
static void audit(const HGWorldInitHarness *h, CycleReport *c, Audit *a, const char *when,
                  size_t ledger_end) {
    memset(a, 0, sizeof(*a));
    uint32_t count = g32(h, NODE_FREE), top = g32(h, NODE_TOP);
    bool never = count == 0u && top == 0u;
    for (int p = 0; p < 3; ++p)
        never = never && g32(h, CTRL + (uint32_t)p * 4u) == 0u && g32(h, CTRL + 0x0cu + (uint32_t)p * 4u) == 0u;
    if (never) {
        /* Pools never initialized (no world setup ran): nothing may be owned. */
        a->free_nodes = NODE_COUNT;
        for (int p = 0; p < 3; ++p) a->free_actors[p] = POOL_COUNT[p];
        for (int l = 0; l < 4; ++l)
            if (g32(h, LIST_MAIN + (uint32_t)l * 8u) != 0u)
                failf(c, "ownership", "%s: list linked before pools exist", when);
        if (g8(h, PLAYER) != 0u) failf(c, "ownership", "%s: player active before pools exist", when);
        for (size_t i = 0; i < ledger_end; ++i) {
            const HGOwnershipEvent *e = hg_world_session_ownership_at(h, i);
            if (e && e->op >= HG_OWN_ACTOR_ALLOC && e->op <= HG_OWN_NODE_RELEASE)
                failf(c, "ownership", "%s: ledger allocation without pools", when);
        }
        return;
    }
    if (count > NODE_COUNT || top != NODE_STACK + count * 4u)
        failf(c, "ownership", "%s: free count %u inconsistent with stack top %08x", when, count, top);
    for (uint32_t i = 0; i < count && i < NODE_COUNT; ++i) {
        uint32_t n = g32(h, NODE_STACK + i * 4u);
        if (!node_ok(n)) { failf(c, "ownership", "%s: invalid free node %08x", when, n); continue; }
        uint32_t k = (n - NODE_BASE) / NODE_STRIDE;
        if (a->node_state[k]) failf(c, "ownership", "%s: node %08x free twice", when, n);
        a->node_state[k] = 1u;
        a->free_nodes++;
    }
    const uint32_t heads[4] = {LIST_MAIN, LIST_PRIO, LIST_T2, LIST_T2_PRIO};
    uint32_t owners[600];
    uint32_t owner_count = 0;
    owners[owner_count++] = PLAYER;
    for (int l = 0; l < 4; ++l) {
        uint32_t cur = g32(h, heads[l]), guard = 0, prev = 0;
        while (cur != 0u && guard++ < 600u) {
            uint32_t slot;
            int p = pool_of(cur, &slot);
            if (p < 0) { failf(c, "ownership", "%s: list %08x holds non-pool %08x", when, heads[l], cur); break; }
            if (a->actor_state[p][slot]) failf(c, "ownership", "%s: actor %08x linked twice", when, cur);
            a->actor_state[p][slot] = 2u;
            if (g32(h, cur + 0x20u) != prev) failf(c, "ownership", "%s: actor %08x back link broken", when, cur);
            a->active[l]++;
            if (owner_count < 600u) owners[owner_count++] = cur;
            prev = cur;
            cur = g32(h, cur + 0x24u);
        }
        if (g32(h, heads[l] + 4u) != prev) failf(c, "ownership", "%s: list %08x tail mismatch", when, heads[l]);
    }
    for (int p = 0; p < 3; ++p) {
        uint32_t cur = g32(h, CTRL + (uint32_t)p * 4u), guard = 0;
        while (cur != 0u && guard++ < 600u) {
            uint32_t slot;
            if (pool_of(cur, &slot) != p) { failf(c, "ownership", "%s: pool %d free list holds %08x", when, p, cur); break; }
            if (a->actor_state[p][slot]) failf(c, "ownership", "%s: actor %08x both free and %s", when, cur,
                                               a->actor_state[p][slot] == 2u ? "active" : "free");
            a->actor_state[p][slot] = 1u;
            a->free_actors[p]++;
            cur = g32(h, cur + 0x24u);
        }
        if (g32(h, CTRL + 0x0cu + (uint32_t)p * 4u) != a->free_actors[p])
            failf(c, "ownership", "%s: pool %d free count %u, free list %u", when, p,
                  g32(h, CTRL + 0x0cu + (uint32_t)p * 4u), a->free_actors[p]);
        for (uint32_t s = 0; s < POOL_COUNT[p]; ++s)
            if (!a->actor_state[p][s]) {
                failf(c, "ownership", "%s: actor %08x neither free nor linked", when, POOL_BASE[p] + s * POOL_STRIDE[p]);
                break;
            }
    }
    for (uint32_t o = 0; o < owner_count; ++o) {
        uint32_t obj = owners[o] + 0x90u;
        uint32_t n = g32(h, obj + 0x54u), table = g32(h, obj + 0x58u);
        if (owners[o] == PLAYER && g8(h, PLAYER) == 0u)
            n = 0u; /* inactive player owns nothing */
        if (n > 64u) { failf(c, "ownership", "%s: owner %08x claims %u nodes", when, owners[o], n); continue; }
        for (uint32_t i = 0; i < n; ++i) {
            uint32_t node = g32(h, table + i * 4u);
            if (!node_ok(node)) { failf(c, "ownership", "%s: owner %08x invalid node %08x", when, owners[o], node); continue; }
            uint32_t k = (node - NODE_BASE) / NODE_STRIDE;
            if (a->node_state[k]) failf(c, "ownership", "%s: node %08x owned and %s", when, node,
                                         a->node_state[k] == 1u ? "free" : "owned twice");
            a->node_state[k] = 2u;
            a->owned_nodes++;
        }
    }
    uint32_t gaps = 0;
    for (uint32_t k = 0; k < NODE_COUNT; ++k)
        gaps += a->node_state[k] == 0u;
    if (gaps) failf(c, "ownership", "%s: %u nodes neither free nor owned (leak)", when, gaps);

    /* Independent ledger replay must agree with memory. */
    static uint8_t live_node[NODE_COUNT];
    static uint8_t live_actor[3][286];
    memset(live_node, 0, sizeof(live_node));
    memset(live_actor, 0, sizeof(live_actor));
    for (size_t i = 0; i < ledger_end; ++i) {
        const HGOwnershipEvent *e = hg_world_session_ownership_at(h, i);
        if (!e) break;
        uint32_t slot;
        int p;
        switch (e->op) {
        case HG_OWN_NODE_ALLOC: case HG_OWN_NODE_RELEASE:
            if (!node_ok(e->object)) { failf(c, "ownership", "ledger node %08x invalid", e->object); break; }
            if ((e->op == HG_OWN_NODE_ALLOC) == (live_node[(e->object - NODE_BASE) / NODE_STRIDE] != 0u))
                failf(c, "ownership", "ledger %s of node %08x out of order", e->op == HG_OWN_NODE_ALLOC ? "double alloc" : "release", e->object);
            live_node[(e->object - NODE_BASE) / NODE_STRIDE] = e->op == HG_OWN_NODE_ALLOC;
            break;
        case HG_OWN_ACTOR_ALLOC: case HG_OWN_ACTOR_RELEASE:
            p = pool_of(e->object, &slot);
            if (p < 0) { failf(c, "ownership", "ledger actor %08x invalid", e->object); break; }
            live_actor[p][slot] = e->op == HG_OWN_ACTOR_ALLOC;
            break;
        default: break;
        }
    }
    for (uint32_t k = 0; k < NODE_COUNT; ++k)
        if ((live_node[k] != 0u) != (a->node_state[k] == 2u)) {
            failf(c, "ownership", "%s: ledger and memory disagree on node %08x", when, NODE_BASE + k * NODE_STRIDE);
            break;
        }
    for (int p = 0; p < 3; ++p)
        for (uint32_t s = 0; s < POOL_COUNT[p]; ++s)
            if ((live_actor[p][s] != 0u) != (a->actor_state[p][s] == 2u)) {
                failf(c, "ownership", "%s: ledger and memory disagree on actor %08x", when, POOL_BASE[p] + s * POOL_STRIDE[p]);
                p = 3;
                break;
            }
}

/* ---- Publication audit ---------------------------------------------------- */
static const HGWorldSceneSpec *g_scenes[2];

static bool in_arena(uint32_t v, uint32_t base) { return v >= base && v < base + ARENA_SPAN; }

static void publication_after_publish(const HGWorldInitHarness *h, CycleReport *c) {
    const HGWorldSceneSpec *s = c->spec;
    uint32_t hp = g32(h, HANDLES + 0x3fu * 4u), ha = g32(h, HANDLES + 0x96u * 4u);
    c->handle_player = hp;
    c->handle_area = ha;
    if (!in_arena(hp, s->player_arena)) failf(c, "prerequisite", "handle 0x3f %08x outside player arena", hp);
    if (!in_arena(ha, s->area_arena)) failf(c, "prerequisite", "handle 0x96 %08x outside area arena", ha);
    if (g32(h, HANDLES + 0x22fu * 4u) != 0u) failf(c, "publication", "title handle 0x22f still published");
    for (uint32_t id = 0; id < HANDLE_SCAN; ++id) {
        uint32_t v = g32(h, HANDLES + id * 4u);
        for (int k = 0; k < 2; ++k) {
            const HGWorldSceneSpec *o = g_scenes[k];
            if (o == s || !v) continue;
            if ((in_arena(v, o->player_arena) && o->player_arena != s->player_arena) ||
                (in_arena(v, o->area_arena) && o->area_arena != s->area_arena))
                failf(c, "publication", "handle %#x %08x points into %s arena", id, v, o->name);
        }
    }
}

static void publication_after_teardown(const HGWorldInitHarness *h, CycleReport *c) {
    for (uint32_t id = 0; id < HANDLE_SCAN; ++id) {
        uint32_t v = g32(h, HANDLES + id * 4u);
        if (!v) continue;
        for (int k = 0; k < 2; ++k)
            if (in_arena(v, g_scenes[k]->player_arena) || in_arena(v, g_scenes[k]->area_arena))
                failf(c, "publication", "after teardown handle %#x %08x still published", id, v);
    }
}

/* ---- State history -------------------------------------------------------- */
static bool legal(uint32_t from, uint32_t to) {
    switch (from) {
    case HG_READY_NONE: return to == HG_READY_BOOTED;
    case HG_READY_BOOTED: case HG_READY_TORN_DOWN: return to == HG_READY_PUBLISHED || to == HG_READY_FAILED;
    case HG_READY_PUBLISHED: return to == HG_READY_WORLD_SETUP || to == HG_READY_FAILED || to == HG_READY_TORN_DOWN;
    case HG_READY_WORLD_SETUP: return to == HG_READY_FIRST_TICK || to == HG_READY_FAILED;
    case HG_READY_FIRST_TICK: return to == HG_READY_FADING;
    case HG_READY_FADING: return to == HG_READY_ACTIVE || to == HG_READY_FAILED || to == HG_READY_TORN_DOWN;
    case HG_READY_ACTIVE: return to == HG_READY_FAILED || to == HG_READY_TORN_DOWN;
    case HG_READY_FAILED: return to == HG_READY_TORN_DOWN;
    default: return false;
    }
}

static void check_history(const HGWorldInitHarness *h, CycleReport *c, size_t start, bool positive) {
    static const uint32_t want[6] = {HG_READY_PUBLISHED, HG_READY_WORLD_SETUP, HG_READY_FIRST_TICK,
                                     HG_READY_FADING, HG_READY_ACTIVE, HG_READY_TORN_DOWN};
    size_t n = hg_world_session_history_count(h);
    uint32_t prev_seq = 0, k = 0;
    for (size_t i = start; i < n; ++i) {
        const HGWorldStateEvent *e = hg_world_session_history_at(h, i);
        if (!legal(e->from, e->to)) failf(c, "state-history", "illegal %s -> %s",
                                          hg_world_readiness_name((HGWorldReadiness)e->from),
                                          hg_world_readiness_name((HGWorldReadiness)e->to));
        if (i > start && e->seq <= prev_seq) failf(c, "state-history", "non-monotonic sequence");
        prev_seq = e->seq;
        if (positive && (k >= 6u || e->to != want[k]))
            failf(c, "state-history", "event %u is %s", k, hg_world_readiness_name((HGWorldReadiness)e->to));
        k++;
    }
    c->history_events = k;
    if (positive && k != 6u) failf(c, "state-history", "%u transitions, expected 6", k);
}

/* ---- Effect order and coordinator order ----------------------------------- */
static void check_setup_order(const HGWorldInitHarness *h, CycleReport *c, size_t start, bool complete) {
    uint32_t next = 0;
    size_t n = hg_world_session_effect_count(h);
    for (size_t i = start; i < n && next < 12u; ++i) {
        uint32_t pc = hg_world_session_effect_at(h, i)->pc;
        bool known = false;
        for (uint32_t k = 0; k < 12u; ++k)
            if (SETUP_ORDER[k] == pc) known = true;
        if (!known) continue;
        if (pc != SETUP_ORDER[next]) {
            failf(c, "ordering", "setup effect %08x ran where %08x was required", pc, SETUP_ORDER[next]);
            return;
        }
        next++;
    }
    c->effects_in_order = next;
    if (complete && next != 12u) failf(c, "ordering", "only %u of 12 setup effects ran in order", next);
}

static bool is_service(uint32_t pc) {
    for (uint32_t k = 0; k < 18u; ++k)
        if (ELF_ORDER[k] == pc) return true;
    return false;
}

static void check_coordinator(const HGWorldInitHarness *h, CycleReport *c) {
    size_t n = hg_world_harness_call_count(h);
    c->calls_logged = (uint32_t)n;
    if (n + 256u >= HG_WORLD_MAX_CALLS) failf(c, "ordering", "call log saturated (%zu)", n);
    uint32_t ticks = 0;
    uint32_t last_fade_in = 0xffffffffu, last_actors = 0xffffffffu, first_tick = 0xffffffffu;
    for (size_t i = 0; i < n; ++i) {
        const HGCallRecord *r = hg_world_harness_call_at(h, i);
        if (r->entry_pc == 0x001390b0u && last_fade_in == 0xffffffffu) last_fade_in = (uint32_t)i;
        if (r->entry_pc == 0x00189aa0u && last_actors == 0xffffffffu) last_actors = (uint32_t)i;
        if (r->entry_pc != 0x0012f850u) continue;
        if (first_tick == 0xffffffffu) first_tick = (uint32_t)i;
        uint32_t k = 0;
        size_t j = i + 1;
        for (; j < n; ++j) {
            uint32_t pc = hg_world_harness_call_at(h, j)->entry_pc;
            if (pc == 0x0012f850u || pc == 0x0013d970u) break;
            if (!is_service(pc)) continue;
            if (k >= 18u || pc != ELF_ORDER[k]) {
                failf(c, "ordering", "tick %u service %u is %08x, ELF order has %08x", ticks, k, pc, k < 18u ? ELF_ORDER[k] : 0u);
                break;
            }
            k++;
        }
        if (k != 18u) failf(c, "ordering", "tick %u ran %u of 18 services", ticks, k);
        ticks++;
    }
    if (!(last_actors < last_fade_in && last_fade_in < first_tick))
        failf(c, "ordering", "00189aa0 -> 001390b0 -> 0012f850 order violated");
    c->coord_ticks_checked = ticks;
}

/* ---- Per-frame checks ----------------------------------------------------- */
static float wrap_pi(float a) {
    while (a > 3.14159265f) a -= 6.2831853f;
    while (a < -3.14159265f) a += 6.2831853f;
    return a;
}

static void check_camera(CycleReport *c, const Row *r) {
    const HGWorldSceneSpec *s = c->spec;
    float want[3] = {r->pos[0], r->pos[1] + s->head_offset, r->pos[2]};
    for (int i = 0; i < 3; ++i)
        if (fabsf(r->eye[i] - want[i]) > 1e-3f) {
            failf(c, "camera-geometry", "step %u eye[%d] %.5f, head %.5f", r->step, i, (double)r->eye[i], (double)want[i]);
            return;
        }
    float d[3] = {r->target[0] - r->eye[0], r->target[1] - r->eye[1], r->target[2] - r->eye[2]};
    float len = sqrtf(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]);
    if (fabsf(len - 20.0f) > 2e-3f) { failf(c, "camera-geometry", "step %u boom %.5f", r->step, (double)len); return; }
    float e[3] = {20.0f * sinf(r->yaw) * cosf(r->pitch), -20.0f * sinf(r->pitch), 20.0f * cosf(r->yaw) * cosf(r->pitch)};
    for (int i = 0; i < 3; ++i)
        if (fabsf(d[i] - e[i]) > 2e-3f) {
            failf(c, "camera-geometry", "step %u boom[%d] %.5f disagrees with yaw/pitch %.5f", r->step, i, (double)d[i], (double)e[i]);
            return;
        }
    if (fabsf(wrap_pi(r->yaw - r->rot_y)) > 2e-2f)
        failf(c, "camera-geometry", "step %u camera yaw %.5f vs player %.5f", r->step, (double)r->yaw, (double)r->rot_y);
    /* No right-stick input in any script: the pitch must stay at the
     * 00141210 spawn bias 0x3d567750 whatever the heading (a world-frame
     * pitch composition loses it at yaw = pi/2). */
    if (fabsf(r->pitch - 0.05235988f) > 1e-4f)
        failf(c, "camera-geometry", "step %u pitch %.6f is not the spawn bias at yaw %.4f", r->step, (double)r->pitch, (double)r->yaw);
}

static void check_frame(CycleReport *c, const Row *prev, const Row *r) {
    const HGWorldSceneSpec *s = c->spec;
    const HGSyntheticSceneConfig *b = &s->base;
    if (r->pos[0] < b->wall_min_x - 0.05f || r->pos[0] > b->wall_max_x + 0.05f ||
        r->pos[2] < b->wall_min_z - 0.05f || r->pos[2] > b->wall_max_z + 0.05f)
        failf(c, "bounds", "step %u position (%.4f, %.4f) outside walls", r->step, (double)r->pos[0], (double)r->pos[2]);
    if (r->grounded == 1u && fabsf(r->pos[1] - expected_floor(s, r->pos[0], r->pos[2])) > 1e-4f)
        failf(c, "floor", "step %u y %.5f, floor at (%.3f, %.3f) is %.5f", r->step, (double)r->pos[1],
              (double)r->pos[0], (double)r->pos[2], (double)expected_floor(s, r->pos[0], r->pos[2]));
    check_camera(c, r);
    if (!prev) return;
    if (r->c3fa190 != prev->c3fa190 + 1u || r->coord != prev->coord + 1u || r->c3eaef0 != prev->c3eaef0 + 1u)
        failf(c, "continuity", "step %u counters 3fa190 %u->%u, ticks %u->%u, 3eaef0 %u->%u", r->step,
              prev->c3fa190, r->c3fa190, prev->coord, r->coord, prev->c3eaef0, r->c3eaef0);
    float dx = r->pos[0] - prev->pos[0], dz = r->pos[2] - prev->pos[2], dy = r->pos[1] - prev->pos[1];
    if (sqrtf(dx * dx + dz * dz) > 1.0f || fabsf(dy) > 1.8f)
        failf(c, "continuity", "step %u displacement (%.4f, %.4f, %.4f)", r->step, (double)dx, (double)dy, (double)dz);
    if (r->walls > prev->walls) {
        c->wall_contact_frames++;
        if (sqrtf(dx * dx + dz * dz) > 0.01f) c->slide_frames++;
    }
    if (fabsf(expected_floor(s, r->pos[0], r->pos[2]) - expected_floor(s, prev->pos[0], prev->pos[2])) > 1e-4f)
        c->floor_changes++;
}

static void check_phases(CycleReport *c) {
    float yaw0 = 0.0f, travel = 0.0f;
    uint32_t walls0 = 0;
    SegKind kind = SEG_HOLD;
    const Row *seg_first = NULL;
    for (uint32_t i = 0; i <= c->rows; ++i) {
        const Row *r = i < c->rows ? &c->row[i] : NULL;
        if (r && r->post_ready == 0u) continue;
        if (!r || (seg_first && (SegKind)r->seg != kind)) {
            const Row *last = &c->row[i - 1];
            switch (kind) {
            case SEG_NEUTRAL: case SEG_HOLD:
                if (travel > 1e-5f || fabsf(last->speed) > 1e-6f)
                    failf(c, "movement-phase", "%s moved %.5f", SEG_NAMES[kind], (double)travel);
                break;
            case SEG_WALK:
                if (travel < 2.0f) failf(c, "movement-phase", "walk travelled %.3f", (double)travel);
                break;
            case SEG_TURN: case SEG_RETURN:
                if (fabsf(wrap_pi(last->rot_y - yaw0)) < 0.25f)
                    failf(c, "movement-phase", "%s turned %.3f", SEG_NAMES[kind], (double)wrap_pi(last->rot_y - yaw0));
                break;
            case SEG_WALL:
                if (last->walls <= walls0) failf(c, "movement-phase", "wall segment made no wall contact");
                break;
            case SEG_RELEASE:
                if (fabsf(last->speed) > 1e-6f) failf(c, "movement-phase", "release ended at speed %.5f", (double)last->speed);
                break;
            }
            seg_first = NULL;
        }
        if (!r) break;
        if (!seg_first) {
            seg_first = r;
            kind = (SegKind)r->seg;
            yaw0 = r->rot_y;
            walls0 = i > 0 ? c->row[i - 1].walls : r->walls;
            travel = 0.0f;
        } else {
            const Row *p = r - 1;
            float dx = r->pos[0] - p->pos[0], dz = r->pos[2] - p->pos[2];
            travel += sqrtf(dx * dx + dz * dz);
        }
    }
    if (c->slide_frames == 0u) failf(c, "movement-phase", "no wall slide frame");
    if (c->floor_changes == 0u) failf(c, "movement-phase", "no floor response");
}

/* ---- One cycle ------------------------------------------------------------ */
static void capture_row(const HGWorldInitHarness *h, Row *r, uint32_t step, uint32_t seg, const HGSyntheticPadInput *pad) {
    HGWorldSessionSnapshot s;
    hg_world_session_snapshot(h, &s);
    memset(r, 0, sizeof(*r));
    r->step = step;
    r->readiness = s.readiness;
    r->post_ready = s.post_ready_updates;
    r->seg = seg;
    r->lx = pad->lx;
    r->ly = pad->ly;
    r->grounded = s.base.player_grounded_flag;
    r->phase = s.base.player_phase;
    for (int i = 0; i < 3; ++i) {
        r->pos[i] = s.base.player_pos[i];
        r->eye[i] = s.base.camera_eye[i];
        r->target[i] = s.base.camera_target[i];
    }
    r->rot_y = s.base.player_rot[1];
    r->pitch = s.base.camera_pitch;
    r->yaw = s.base.camera_yaw;
    r->speed = s.base.player_speed;
    r->walls = s.base.wall_contact_events;
    r->floors = s.base.floor_query_events;
    r->c3fa190 = s.base.counter_3fa190;
    r->c46e118 = s.base.counter_46e118;
    r->c46d930 = s.base.counter_46d930;
    r->c3eaef0 = s.base.frame_tick_3eaef0;
    r->coord = s.coordinator_ticks;
    r->free_nodes = s.base.free_node_count;
    r->main_n = s.base.main_actor_count;
    r->prio_n = s.prio_actor_count;
    r->t2_n = s.base.type2_actor_count;
    r->block6e = s.input_block_3f9f6e;
    r->fade = s.base.fade_phase;
}

static void run_cycle(HGWorldInitHarness *h, const HGWorldSceneSpec *spec, uint32_t cycle,
                      CycleReport *c, bool positive) {
    memset(c, 0, sizeof(*c));
    c->spec = spec;
    c->cycle = cycle;
    size_t hist0 = hg_world_session_history_count(h);
    size_t eff0 = hg_world_session_effect_count(h);
    size_t own0 = hg_world_session_ownership_count(h);
    hg_world_session_reset_call_log(h);
    Audit a;

    c->publish_err = hg_world_harness_publish_resources(h);
    if (c->publish_err == HG_WORLD_OK)
        publication_after_publish(h, c);
    c->enter_err = c->publish_err == HG_WORLD_OK ? hg_world_harness_enter_world(h) : c->publish_err;
    HGSyntheticPadInput neutral = hg_synthetic_pad_neutral();
    if (c->enter_err == HG_WORLD_OK) {
        audit(h, c, &a, "after first tick", hg_world_session_ownership_count(h));
        c->enter_free_nodes = a.free_nodes;
        c->enter_owned_nodes = a.owned_nodes;
        c->enter_player_nodes = g32(h, PLAYER + 0x90u + 0x54u);
        c->enter_main = a.active[0];
        c->enter_prio = a.active[1];
        c->enter_t2 = a.active[2];
        if (c->enter_player_nodes != spec->base.player_bones ||
            !in_arena(g32(h, PLAYER + 0x90u + 0x10u), spec->player_arena))
            failf(c, "binding", "player model %08x with %u nodes", g32(h, PLAYER + 0x90u + 0x10u), c->enter_player_nodes);
        uint32_t want_owned = spec->base.player_bones;
        for (uint32_t i = 0; i < spec->prop_count; ++i) want_owned += spec->props[i].nodes;
        if (a.owned_nodes != want_owned)
            failf(c, "allocation", "%u owned nodes, scene needs %u", a.owned_nodes, want_owned);
        Row *r0 = &c->row[c->rows++];
        capture_row(h, r0, 0u, SEG_HOLD, &neutral);
        c->block6e_first = r0->block6e;
        memcpy(c->first_pos, r0->pos, sizeof(c->first_pos));
        memcpy(c->first_eye, r0->eye, sizeof(c->first_eye));
        memcpy(c->first_target, r0->target, sizeof(c->first_target));
        check_frame(c, NULL, r0);

        /* Fade: neutral until readiness is reached (bounded). */
        uint32_t step = 1;
        while (hg_world_session_readiness(h) == HG_READY_FADING && step < 200u && c->rows < MAX_ROWS) {
            c->step_err = hg_world_harness_step_frame(h, &neutral, NULL);
            Row *r = &c->row[c->rows++];
            capture_row(h, r, step++, SEG_HOLD, &neutral);
            check_frame(c, r - 1, r);
            if (c->step_err != HG_WORLD_OK) break;
        }
        c->fade_frames = step - 1u;
        if (hg_world_session_readiness(h) != HG_READY_ACTIVE)
            failf(c, "readiness", "not active after %u fade frames (%s)", c->fade_frames,
                  hg_world_readiness_name(hg_world_session_readiness(h)));
        for (uint32_t s = 0; s < SCRIPT_LEN && c->step_err == HG_WORLD_OK; ++s) {
            HGSyntheticPadInput pad = neutral;
            pad.lx = SCRIPT[s].lx;
            pad.ly = SCRIPT[s].ly;
            for (uint32_t f = 0; f < SCRIPT[s].frames && c->rows < MAX_ROWS; ++f) {
                c->step_err = hg_world_harness_step_frame(h, &pad, NULL);
                Row *r = &c->row[c->rows++];
                capture_row(h, r, step++, SCRIPT[s].kind, &pad);
                check_frame(c, r - 1, r);
                if (c->step_err != HG_WORLD_OK) break;
                if ((r->post_ready % 30u) == 0u)
                    audit(h, c, &a, "mid-run", hg_world_session_ownership_count(h));
            }
        }
        c->post_ready = c->rows ? c->row[c->rows - 1].post_ready : 0u;
        if (c->post_ready < 120u)
            failf(c, "readiness", "%u post-readiness updates", c->post_ready);
        check_coordinator(h, c);
        check_setup_order(h, c, eff0, true);
        if (c->step_err == HG_WORLD_OK) check_phases(c);
        const Row *last = &c->row[c->rows - 1];
        memcpy(c->final_pos, last->pos, sizeof(c->final_pos));
        c->final_yaw = last->rot_y;
    }
    if (c->publish_err != HG_WORLD_OK || c->enter_err != HG_WORLD_OK) {
        check_setup_order(h, c, eff0, false); /* whatever ran must still be in order */
        /* Failed setup must leave pools as an untouched session would. */
        audit(h, c, &a, "after failed setup", hg_world_session_ownership_count(h));
        if (a.owned_nodes != 0u || a.active[0] + a.active[1] + a.active[2] + a.active[3] != 0u)
            failf(c, "ownership", "failed setup left %u nodes and %u actors", a.owned_nodes,
                  a.active[0] + a.active[1] + a.active[2] + a.active[3]);
        if (c->enter_err == HG_WORLD_ERR_MISSING_RESOURCE) failf(c, "prerequisite", "enter refused: MISSING_RESOURCE");
        else if (c->enter_err == HG_WORLD_ERR_CAPACITY) failf(c, "allocation", "enter refused: CAPACITY");
        else if (c->enter_err == HG_WORLD_ERR_INVALID_PLAYER_BINDING) failf(c, "binding", "enter refused: INVALID_PLAYER_BINDING");
        else if (c->enter_err == HG_WORLD_ERR_UNSUPPORTED_ENTRY) failf(c, "entry-selector", "enter refused: UNSUPPORTED_ENTRY");
        else failf(c, "model-error", "setup error %s", hg_world_harness_error_name(c->enter_err));
    } else if (c->step_err != HG_WORLD_OK) {
        failf(c, "model-error", "step error %s", hg_world_harness_error_name(c->step_err));
    }

    c->teardown_err = hg_world_harness_teardown(h);
    if (c->teardown_err != HG_WORLD_OK)
        failf(c, "model-error", "teardown error %s", hg_world_harness_error_name(c->teardown_err));
    audit(h, c, &a, "after teardown", hg_world_session_ownership_count(h));
    c->free_after_teardown = a.free_nodes;
    c->owned_after_teardown = a.owned_nodes;
    for (int p = 0; p < 3; ++p) c->pool_free_after[p] = a.free_actors[p];
    if (a.free_nodes != NODE_COUNT || a.free_actors[0] != POOL_COUNT[0] || a.free_actors[1] != POOL_COUNT[1] ||
        a.free_actors[2] != POOL_COUNT[2])
        failf(c, "ownership", "after teardown %u free nodes, pools %u/%u/%u", a.free_nodes,
              a.free_actors[0], a.free_actors[1], a.free_actors[2]);
    if (g8(h, PLAYER) != 0u || g32(h, PLAYER + 0x90u + 0x54u) != 0u)
        failf(c, "ownership", "player still active after teardown");
    publication_after_teardown(h, c);
    check_history(h, c, hist0, positive);
    c->ledger_events = (uint32_t)(hg_world_session_ownership_count(h) - own0);
    for (size_t i = own0; i < hg_world_session_ownership_count(h); ++i) {
        const HGOwnershipEvent *e = hg_world_session_ownership_at(h, i);
        /* The seeded title handle is a cold-boot input revoked once. */
        if (e->op < 7u && !(e->op == HG_OWN_HANDLE_REVOKE && e->object == 0x22fu))
            c->ledger_by_op[e->op]++;
    }
}

/* Cycle-relative equivalence: identical rows once counters are rebased. */
static void parity(CycleReport *a, CycleReport *b) {
    if (a->rows != b->rows) { failf(b, "cycle-parity", "%u rows vs %u", a->rows, b->rows); return; }
    for (uint32_t i = 0; i < a->rows; ++i) {
        Row x = a->row[i], y = b->row[i];
        x.c3fa190 -= a->row[0].c3fa190; y.c3fa190 -= b->row[0].c3fa190;
        x.c46e118 -= a->row[0].c46e118; y.c46e118 -= b->row[0].c46e118;
        x.c46d930 -= a->row[0].c46d930; y.c46d930 -= b->row[0].c46d930;
        x.c3eaef0 -= a->row[0].c3eaef0; y.c3eaef0 -= b->row[0].c3eaef0;
        x.coord -= a->row[0].coord; y.coord -= b->row[0].coord;
        x.walls -= a->row[0].walls; y.walls -= b->row[0].walls;
        x.floors -= a->row[0].floors; y.floors -= b->row[0].floors;
        if (memcmp(&x, &y, sizeof(x)) != 0) {
            failf(b, "cycle-parity", "row %u differs from the cycle-1 row", i);
            return;
        }
    }
    if (memcmp(a->ledger_by_op, b->ledger_by_op, sizeof(a->ledger_by_op)) != 0 ||
        a->enter_free_nodes != b->enter_free_nodes || a->enter_owned_nodes != b->enter_owned_nodes ||
        a->effects_in_order != b->effects_in_order || a->coord_ticks_checked != b->coord_ticks_checked ||
        memcmp(a->pool_free_after, b->pool_free_after, sizeof(a->pool_free_after)) != 0)
        failf(b, "cycle-parity", "ledger or checkpoint counts differ");
}

/* ---- JSON ----------------------------------------------------------------- */
static void jf(float v) { printf("%.9g", (double)v); }
static void jv3(const float *v) { printf("["); jf(v[0]); printf(","); jf(v[1]); printf(","); jf(v[2]); printf("]"); }

static void print_failures(const CycleReport *c) {
    printf("[");
    for (uint32_t i = 0; i < c->fail_count; ++i)
        printf("%s{\"detector\":\"%s\",\"message\":\"%s\"}", i ? "," : "", c->fail[i].detector, c->fail[i].message);
    printf("]");
}

static void print_cycle(const CycleReport *c, bool rows) {
    const HGWorldSceneSpec *s = c->spec;
    printf("{\"scene\":\"%s\",\"cycle\":%u,\"publish\":\"%s\",\"enter\":\"%s\",\"step\":\"%s\",\"teardown\":\"%s\",",
           s->name, c->cycle, hg_world_harness_error_name(c->publish_err), hg_world_harness_error_name(c->enter_err),
           hg_world_harness_error_name(c->step_err), hg_world_harness_error_name(c->teardown_err));
    printf("\"fade_frames\":%u,\"post_ready_updates\":%u,\"rows\":%u,\"coordinator_ticks_checked\":%u,"
           "\"setup_effects_in_order\":%u,\"history_events\":%u,\"ledger_events\":%u,\"calls_logged\":%u,",
           c->fade_frames, c->post_ready, c->rows, c->coord_ticks_checked, c->effects_in_order,
           c->history_events, c->ledger_events, c->calls_logged);
    printf("\"handles\":{\"0x3f\":\"%08x\",\"0x96\":\"%08x\"},", c->handle_player, c->handle_area);
    printf("\"ledger_by_op\":[%u,%u,%u,%u,%u,%u],", c->ledger_by_op[1], c->ledger_by_op[2], c->ledger_by_op[3],
           c->ledger_by_op[4], c->ledger_by_op[5], c->ledger_by_op[6]);
    printf("\"first_tick\":{\"pos\":"); jv3(c->first_pos); printf(",\"eye\":"); jv3(c->first_eye);
    printf(",\"target\":"); jv3(c->first_target); printf(",\"input_block_3f9f6e\":%u,", c->block6e_first);
    printf("\"free_nodes\":%u,\"owned_nodes\":%u,\"player_nodes\":%u,\"main\":%u,\"priority\":%u,\"type2\":%u},",
           c->enter_free_nodes, c->enter_owned_nodes, c->enter_player_nodes, c->enter_main, c->enter_prio, c->enter_t2);
    printf("\"final\":{\"pos\":"); jv3(c->final_pos); printf(",\"yaw\":"); jf(c->final_yaw); printf("},");
    printf("\"motion\":{\"wall_contact_frames\":%u,\"slide_frames\":%u,\"floor_changes\":%u},",
           c->wall_contact_frames, c->slide_frames, c->floor_changes);
    printf("\"after_teardown\":{\"free_nodes\":%u,\"owned_nodes\":%u,\"pool_free\":[%u,%u,%u]},",
           c->free_after_teardown, c->owned_after_teardown, c->pool_free_after[0], c->pool_free_after[1], c->pool_free_after[2]);
    printf("\"failures\":"); print_failures(c);
    if (rows) {
        printf(",\"trace_fields\":[\"step\",\"readiness\",\"post_ready\",\"segment\",\"lx\",\"ly\",\"grounded\",\"phase\","
               "\"x\",\"y\",\"z\",\"rot_y\",\"eye_x\",\"eye_y\",\"eye_z\",\"tgt_x\",\"tgt_y\",\"tgt_z\",\"pitch\",\"yaw\",\"speed\","
               "\"walls\",\"floors\",\"c3fa190\",\"c46e118\",\"c46d930\",\"c3eaef0\",\"coord\",\"free_nodes\",\"main\",\"prio\",\"type2\","
               "\"block_3f9f6e\",\"fade\"],\"trace\":[");
        for (uint32_t i = 0; i < c->rows; ++i) {
            const Row *r = &c->row[i];
            printf("%s[%u,%u,%u,%u,%u,%u,%u,%u,", i ? "," : "", r->step, r->readiness, r->post_ready, r->seg,
                   r->lx, r->ly, r->grounded, r->phase);
            jf(r->pos[0]); printf(","); jf(r->pos[1]); printf(","); jf(r->pos[2]); printf(","); jf(r->rot_y); printf(",");
            jf(r->eye[0]); printf(","); jf(r->eye[1]); printf(","); jf(r->eye[2]); printf(",");
            jf(r->target[0]); printf(","); jf(r->target[1]); printf(","); jf(r->target[2]); printf(",");
            jf(r->pitch); printf(","); jf(r->yaw); printf(","); jf(r->speed);
            printf(",%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u]", r->walls, r->floors, r->c3fa190, r->c46e118, r->c46d930,
                   r->c3eaef0, r->coord, r->free_nodes, r->main_n, r->prio_n, r->t2_n, r->block6e, r->fade);
        }
        printf("]");
    }
    printf("}");
}

/* ---- Controls and invalid inputs ------------------------------------------ */
typedef struct {
    const char *name;
    uint32_t scene;
    HGWorldSessionFaults faults;
    const char *intended;
    const char *forbidden; /* detector that must stay silent (or NULL) */
} Control;

typedef struct {
    const char *name;
    int expected, actual;
} InvalidCase;

static CycleReport g_cycles[MAX_CYCLES];
static CycleReport g_control_cycle;

int main(int argc, char **argv) {
    bool json = argc > 1 && strcmp(argv[1], "--json") == 0;
    static HGWorldSceneSpec scenes[2];
    scenes[0] = hg_world_scene_builtin(0u);
    scenes[1] = hg_world_scene_builtin(1u);
    g_scenes[0] = &scenes[0];
    g_scenes[1] = &scenes[1];
    bool ok = true;

    /* 1. Positive connected run: A1, B1, A2, B2 on one harness. */
    HGWorldInitHarness *h = hg_world_session_create(&scenes[0], NULL);
    if (!h || hg_world_harness_cold_boot(h) != HG_WORLD_OK) {
        fprintf(stderr, "session create/cold boot failed\n");
        return 2;
    }
    const uint32_t order[MAX_CYCLES] = {0u, 1u, 0u, 1u};
    for (uint32_t i = 0; i < MAX_CYCLES; ++i) {
        if (i > 0u && hg_world_session_select_scene(h, &scenes[order[i]]) != HG_WORLD_OK) {
            fprintf(stderr, "scene switch failed\n");
            return 2;
        }
        run_cycle(h, &scenes[order[i]], i / 2u + 1u, &g_cycles[i], true);
    }
    parity(&g_cycles[0], &g_cycles[2]);
    parity(&g_cycles[1], &g_cycles[3]);
    if (hg_world_harness_unknown_call_count(h) != 0u)
        failf(&g_cycles[3], "model-error", "%u undeclared calls", hg_world_harness_unknown_call_count(h));
    const uint32_t *model_order = NULL;
    size_t model_n = hg_world_session_coordinator_order(&model_order);
    if (model_n != 18u || memcmp(model_order, ELF_ORDER, sizeof(ELF_ORDER)) != 0)
        failf(&g_cycles[0], "ordering", "model coordinator table differs from the checker's ELF order");
    for (uint32_t i = 0; i < MAX_CYCLES; ++i)
        if (g_cycles[i].fail_count) ok = false;

    size_t prov_n = hg_world_harness_provenance_count(h);
    uint32_t prov_by_class[7] = {0};
    for (size_t i = 0; i < prov_n; ++i) {
        uint32_t p = (uint32_t)hg_world_harness_provenance_at(h, i)->provenance;
        if (p < 7u) prov_by_class[p]++;
    }
    size_t eff_n = hg_world_session_effect_count(h);
    uint32_t eff_by_class[7] = {0};
    for (size_t i = 0; i < eff_n; ++i) {
        uint32_t p = hg_world_session_effect_at(h, i)->provenance;
        if (p < 7u) eff_by_class[p]++;
    }
    uint32_t own_by_op[7] = {0};
    for (size_t i = 0; i < hg_world_session_ownership_count(h); ++i) {
        uint32_t op = hg_world_session_ownership_at(h, i)->op;
        if (op < 7u) own_by_op[op]++;
    }

    /* 2. Targeted controls (each on a fresh harness, one cycle). */
    const Control controls[] = {
        {"omit-area-publish", 0u, {.omit_area_publish = true}, "prerequisite", "ownership"},
        {"swap-pool-and-spawn", 0u, {.swap_pool_and_spawn = true}, "ordering", NULL},
        {"exhaust-nodes-before-player", 1u, {.exhaust_nodes_before_player = true}, "allocation", "ownership"},
        {"exhaust-actor-pool0", 1u, {.exhaust_actor_pool0 = true}, "allocation", "ownership"},
        {"bind-player-to-area", 0u, {.bind_player_to_area = true}, "binding", "ownership"},
        {"drop-update-40", 0u, {.drop_update_at = 40u}, "continuity", "ownership"},
        {"disable-walls-60", 0u, {.disable_walls_from = 60u}, "bounds", "ownership"},
        {"teleport-50", 1u, {.teleport_at = 50u}, "continuity", "ownership"},
        {"ignore-floor-tiers", 1u, {.ignore_floor_tiers = true}, "floor", "ownership"},
        {"skip-camera-30", 0u, {.skip_camera_from = 30u}, "camera-geometry", "ownership"},
        {"leak-player-node", 0u, {.leak_player_node = true}, "ownership", NULL},
        {"leave-prop-linked", 1u, {.leave_prop_linked = true}, "ownership", NULL},
        {"skip-area-handle-revoke", 1u, {.skip_area_handle_revoke = true}, "publication", "ownership"},
    };
    enum { NCONTROLS = sizeof(controls) / sizeof(controls[0]) };
    bool control_ok[NCONTROLS + 1];
    uint32_t detected = 0;
    char control_fired[NCONTROLS + 1][256];
    char control_api[NCONTROLS + 1][64];
    for (uint32_t i = 0; i <= NCONTROLS; ++i) {
        HGWorldSceneSpec spec = scenes[i < NCONTROLS ? controls[i].scene : 0u];
        HGWorldSessionFaults none = {0};
        const char *intended = i < NCONTROLS ? controls[i].intended : "entry-selector";
        const char *forbidden = i < NCONTROLS ? controls[i].forbidden : "ownership";
        if (i == NCONTROLS)
            spec.entry_event_flag = 1u; /* event-entry branch: 0046e106 == 1 */
        HGWorldInitHarness *ch = hg_world_session_create(&spec, i < NCONTROLS ? &controls[i].faults : &none);
        control_ok[i] = false;
        control_fired[i][0] = '\0';
        snprintf(control_api[i], sizeof(control_api[i]), "create-failed");
        if (!ch || hg_world_harness_cold_boot(ch) != HG_WORLD_OK) {
            hg_world_harness_destroy(ch);
            ok = false;
            continue;
        }
        run_cycle(ch, &spec, 1u, &g_control_cycle, false);
        snprintf(control_api[i], sizeof(control_api[i]), "%s/%s/%s",
                 hg_world_harness_error_name(g_control_cycle.enter_err),
                 hg_world_harness_error_name(g_control_cycle.step_err),
                 hg_world_harness_error_name(g_control_cycle.teardown_err));
        size_t off = 0;
        for (uint32_t k = 0; k < g_control_cycle.fail_count && off + 26u < sizeof(control_fired[i]); ++k)
            off += (size_t)snprintf(control_fired[i] + off, sizeof(control_fired[i]) - off, "%s%s", k ? "," : "",
                                    g_control_cycle.fail[k].detector);
        control_ok[i] = fired(&g_control_cycle, intended) && !(forbidden && fired(&g_control_cycle, forbidden));
        detected += control_ok[i];
        if (!control_ok[i]) {
            ok = false;
            fprintf(stderr, "control %s: intended %s, fired [%s]\n", i < NCONTROLS ? controls[i].name : "event-entry-branch",
                    intended, control_fired[i]);
            for (uint32_t k = 0; k < g_control_cycle.fail_count; ++k)
                fprintf(stderr, "  %s: %s\n", g_control_cycle.fail[k].detector, g_control_cycle.fail[k].message);
        }
        hg_world_harness_destroy(ch);
    }

    /* 3. Invalid inputs fail safely with structured errors. */
    InvalidCase inv[40];
    uint32_t ninv = 0;
#define INV(n, e, expr) do { inv[ninv].name = (n); inv[ninv].expected = (int)(e); inv[ninv].actual = (int)(expr); ninv++; } while (0)
    HGWorldSceneSpec bad;
    INV("validate-null", HG_WORLD_ERR_NULL_ARG, hg_world_scene_validate(NULL));
    INV("create-null-spec", 1, hg_world_session_create(NULL, NULL) == NULL);
    bad = scenes[0]; bad.base.spawn_x = NAN; INV("nan-spawn", HG_WORLD_ERR_INVALID_CONFIG, hg_world_scene_validate(&bad));
    INV("create-nan-spawn", 1, hg_world_session_create(&bad, NULL) == NULL);
    bad = scenes[0]; bad.base.player_bones = 17u; INV("bones-17", HG_WORLD_ERR_INVALID_CONFIG, hg_world_scene_validate(&bad));
    bad = scenes[0]; bad.base.player_bones = 37u; INV("bones-37", HG_WORLD_ERR_INVALID_CONFIG, hg_world_scene_validate(&bad));
    bad = scenes[0]; bad.base.wall_min_x = 30.0f; INV("inverted-walls", HG_WORLD_ERR_INVALID_CONFIG, hg_world_scene_validate(&bad));
    bad = scenes[1]; bad.base.spawn_x = 0.0f; INV("spawn-outside-walls", HG_WORLD_ERR_INVALID_CONFIG, hg_world_scene_validate(&bad));
    bad = scenes[1]; bad.tiers[1].height = 8.0f; INV("tier-beyond-probe", HG_WORLD_ERR_INVALID_CONFIG, hg_world_scene_validate(&bad));
    bad = scenes[1]; bad.tier_count = 5u; INV("tier-count-5", HG_WORLD_ERR_INVALID_CONFIG, hg_world_scene_validate(&bad));
    bad = scenes[1]; bad.props[0].nodes = 33u; INV("prop-nodes-33", HG_WORLD_ERR_INVALID_CONFIG, hg_world_scene_validate(&bad));
    bad = scenes[1]; bad.prop_count = 7u; INV("prop-count-7", HG_WORLD_ERR_INVALID_CONFIG, hg_world_scene_validate(&bad));
    bad = scenes[0]; bad.root_actors = 0u; INV("no-root-actor", HG_WORLD_ERR_INVALID_CONFIG, hg_world_scene_validate(&bad));
    bad = scenes[0]; bad.area_arena = bad.player_arena + 0x8000u; INV("overlapping-arenas", HG_WORLD_ERR_INVALID_CONFIG, hg_world_scene_validate(&bad));
    bad = scenes[0]; bad.player_arena += 4u; INV("misaligned-arena", HG_WORLD_ERR_INVALID_CONFIG, hg_world_scene_validate(&bad));
    bad = scenes[0]; bad.area_dir = 0x96u; INV("area-dir-out-of-family", HG_WORLD_ERR_INVALID_CONFIG, hg_world_scene_validate(&bad));
    bad = scenes[0]; memset(bad.name, 'x', sizeof(bad.name)); INV("unterminated-name", HG_WORLD_ERR_INVALID_CONFIG, hg_world_scene_validate(&bad));
    bad = hg_world_scene_builtin(7u); INV("builtin-out-of-range", HG_WORLD_ERR_INVALID_CONFIG, hg_world_scene_validate(&bad));
    INV("cold-boot-null", HG_WORLD_ERR_NULL_ARG, hg_world_harness_cold_boot(NULL));
    INV("snapshot-null-out", HG_WORLD_ERR_NULL_ARG, hg_world_session_snapshot(h, NULL));
    INV("select-null-spec", HG_WORLD_ERR_NULL_ARG, hg_world_session_select_scene(h, NULL));
    {
        HGWorldInitHarness *m = hg_world_session_create(&scenes[0], NULL);
        INV("enter-before-boot", HG_WORLD_ERR_LIFECYCLE_STATE, hg_world_harness_enter_world(m));
        INV("publish-before-boot", HG_WORLD_ERR_LIFECYCLE_STATE, hg_world_harness_publish_resources(m));
        hg_world_harness_cold_boot(m);
        INV("enter-before-publish", HG_WORLD_ERR_LIFECYCLE_STATE, hg_world_harness_enter_world(m));
        INV("step-before-enter", HG_WORLD_ERR_LIFECYCLE_STATE, hg_world_harness_step_frame(m, NULL, NULL));
        INV("teardown-before-publish", HG_WORLD_ERR_LIFECYCLE_STATE, hg_world_harness_teardown(m));
        INV("publish", HG_WORLD_OK, hg_world_harness_publish_resources(m));
        INV("publish-twice", HG_WORLD_ERR_LIFECYCLE_STATE, hg_world_harness_publish_resources(m));
        INV("enter", HG_WORLD_OK, hg_world_harness_enter_world(m));
        INV("select-while-active", HG_WORLD_ERR_LIFECYCLE_STATE, hg_world_session_select_scene(m, &scenes[1]));
        INV("enter-twice", HG_WORLD_ERR_LIFECYCLE_STATE, hg_world_harness_enter_world(m));
        INV("teardown", HG_WORLD_OK, hg_world_harness_teardown(m));
        INV("teardown-twice", HG_WORLD_ERR_LIFECYCLE_STATE, hg_world_harness_teardown(m));
        INV("step-after-teardown", HG_WORLD_ERR_LIFECYCLE_STATE, hg_world_harness_step_frame(m, NULL, NULL));
        bad = scenes[0]; bad.base.floor_y = INFINITY;
        INV("select-invalid-scene", HG_WORLD_ERR_INVALID_CONFIG, hg_world_session_select_scene(m, &bad));
        INV("guest-ram-out-of-range", 1, hg_world_harness_guest_ram(m, 0x01fffffcu, 8u) == NULL);
        hg_world_harness_destroy(m);
    }
    hg_world_harness_destroy(NULL);
    uint32_t inv_ok = 0;
    for (uint32_t i = 0; i < ninv; ++i) {
        if (inv[i].expected == inv[i].actual) inv_ok++;
        else { ok = false; fprintf(stderr, "invalid input %s: expected %d got %d\n", inv[i].name, inv[i].expected, inv[i].actual); }
    }

    if (json) {
        printf("{\"schema\":\"hg-m25-world-session/v1\",\"synthetic_only\":true,\"retail_equivalence_claimed\":false,");
        printf("\"scenes\":[");
        for (int k = 0; k < 2; ++k) {
            const HGWorldSceneSpec *s = &scenes[k];
            printf("%s{\"name\":\"%s\",\"spawn\":[", k ? "," : "", s->name);
            jf(s->base.spawn_x); printf(","); jf(s->base.spawn_y); printf(","); jf(s->base.spawn_z);
            printf("],\"yaw\":"); jf(s->base.spawn_yaw); printf(",\"floor_y\":"); jf(s->base.floor_y);
            printf(",\"walls\":["); jf(s->base.wall_min_x); printf(","); jf(s->base.wall_max_x); printf(",");
            jf(s->base.wall_min_z); printf(","); jf(s->base.wall_max_z);
            printf("],\"tiers\":[");
            for (uint32_t t = 0; t < s->tier_count; ++t) {
                printf("%s[", t ? "," : ""); jf(s->tiers[t].min_x); printf(","); jf(s->tiers[t].max_x); printf(",");
                jf(s->tiers[t].min_z); printf(","); jf(s->tiers[t].max_z); printf(","); jf(s->tiers[t].height); printf("]");
            }
            printf("],\"player_bones\":%u,\"head_offset\":", s->base.player_bones); jf(s->head_offset);
            printf(",\"root_actors\":%u,\"props\":[", s->root_actors);
            for (uint32_t p = 0; p < s->prop_count; ++p) printf("%s[%u,%u]", p ? "," : "", s->props[p].nodes, s->props[p].priority);
            printf("],\"player_dir\":\"%#x\",\"area_dir\":\"%#x\",\"player_arena\":\"%08x\",\"area_arena\":\"%08x\"}",
                   s->player_dir, s->area_dir, s->player_arena, s->area_arena);
        }
        printf("],\"script\":[");
        for (uint32_t s = 0; s < SCRIPT_LEN; ++s)
            printf("%s{\"segment\":\"%s\",\"frames\":%u,\"lx\":%u,\"ly\":%u}", s ? "," : "", SEG_NAMES[SCRIPT[s].kind],
                   SCRIPT[s].frames, SCRIPT[s].lx, SCRIPT[s].ly);
        printf("],\"checker_coordinator_order\":[");
        for (int k = 0; k < 18; ++k) printf("%s\"%08x\"", k ? "," : "", ELF_ORDER[k]);
        printf("],\"setup_order\":[");
        for (int k = 0; k < 12; ++k) printf("%s\"%08x\"", k ? "," : "", SETUP_ORDER[k]);
        printf("],\"cycles\":[");
        for (uint32_t i = 0; i < MAX_CYCLES; ++i) { if (i) printf(","); print_cycle(&g_cycles[i], true); }
        printf("],\"ledgers\":{\"provenance\":{\"total\":%zu,\"unknown\":%u,\"fixture\":%u,\"c_produced\":%u,"
               "\"synthetic_model\":%u,\"original_bytes\":%u,\"runtime_observation\":%u,\"inference\":%u},",
               prov_n, prov_by_class[0], prov_by_class[1], prov_by_class[2], prov_by_class[3], prov_by_class[4],
               prov_by_class[5], prov_by_class[6]);
        printf("\"effects\":{\"total\":%zu,\"unknown\":%u,\"c_produced\":%u,\"synthetic_model\":%u,\"original_bytes\":%u},",
               eff_n, eff_by_class[0], eff_by_class[2], eff_by_class[3], eff_by_class[4]);
        printf("\"ownership\":{\"actor_alloc\":%u,\"actor_release\":%u,\"node_alloc\":%u,\"node_release\":%u,"
               "\"handle_publish\":%u,\"handle_revoke\":%u}},",
               own_by_op[1], own_by_op[2], own_by_op[3], own_by_op[4], own_by_op[5], own_by_op[6]);
        printf("\"provenance_records\":[");
        for (size_t i = 0; i < prov_n; ++i) {
            const HGProvenanceRecord *p = hg_world_harness_provenance_at(h, i);
            printf("%s{\"label\":\"%s\",\"address\":\"%08x\",\"value\":\"%08x\",\"class\":\"%s\",\"producer\":\"%08x\"}",
                   i ? "," : "", p->label, p->guest_addr, p->value_u32, hg_world_provenance_name(p->provenance), p->producer_pc);
        }
        printf("],\"controls\":[");
        for (uint32_t i = 0; i <= NCONTROLS; ++i)
            printf("%s{\"name\":\"%s\",\"scene\":\"%s\",\"intended\":\"%s\",\"forbidden\":%s%s%s,\"api\":\"%s\",\"fired\":\"%s\",\"detected\":%s}",
                   i ? "," : "", i < NCONTROLS ? controls[i].name : "event-entry-branch",
                   scenes[i < NCONTROLS ? controls[i].scene : 0u].name,
                   i < NCONTROLS ? controls[i].intended : "entry-selector",
                   (i < NCONTROLS ? controls[i].forbidden : "ownership") ? "\"" : "",
                   (i < NCONTROLS ? controls[i].forbidden : "ownership") ? (i < NCONTROLS ? controls[i].forbidden : "ownership") : "null",
                   (i < NCONTROLS ? controls[i].forbidden : "ownership") ? "\"" : "",
                   control_api[i], control_fired[i], control_ok[i] ? "true" : "false");
        printf("],\"invalid_inputs\":[");
        for (uint32_t i = 0; i < ninv; ++i)
            printf("%s{\"name\":\"%s\",\"expected\":%d,\"actual\":%d}", i ? "," : "", inv[i].name, inv[i].expected, inv[i].actual);
        printf("],\"summary\":{\"cycles\":%u,\"controls\":%u,\"controls_detected\":%u,\"invalid_inputs\":%u,\"invalid_inputs_rejected\":%u,\"ok\":%s}}\n",
               MAX_CYCLES, NCONTROLS + 1u, detected, ninv, inv_ok, ok ? "true" : "false");
    } else {
        for (uint32_t i = 0; i < MAX_CYCLES; ++i) {
            CycleReport *c = &g_cycles[i];
            printf("%s cycle %u: fade %u, post-ready %u, failures %u\n", c->spec->name, c->cycle, c->fade_frames,
                   c->post_ready, c->fail_count);
            for (uint32_t k = 0; k < c->fail_count; ++k)
                printf("  %s: %s\n", c->fail[k].detector, c->fail[k].message);
        }
        printf("M25 world session %s\n", ok ? "PASSED" : "FAILED");
    }
    hg_world_harness_destroy(h);
    return ok ? 0 : 1;
}
