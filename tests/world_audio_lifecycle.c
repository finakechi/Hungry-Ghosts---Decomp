/* M23-M24 integration: connected original synthetic world/audio lifecycle.
 *
 * One deterministic process drives the M23 synthetic world harness
 * (src/scene/world_init.c with the existing M1-M21 C it calls) and the
 * M22/M24 audio path (src/audio/audio_engine.c, audio_iop.c and
 * audio_sequencer.c) on ONE guest EE RAM: the audio bus reads and writes the
 * world harness's memory through hg_world_harness_guest_ram().
 *
 * Per cycle:
 *   cold publication: world resources (M23) and an original synthetic audio
 *   bank registered through hg_audio_register_entry (SIF allocation, DMA,
 *   002a7560/002a7590/002b5428 bank calls, flush and DMA steps);
 *   world entry: actor/player/camera setup, then the M23 controller script
 *   (fade, neutral, forward walk, turn, wall-constrained walk, release);
 *   per world frame: explicit synthetic audio events derived from the carried
 *   world state are posted with hg_audio_play; hg_seq_update(1 tick) makes
 *   every note/voice decision; hg_audio_ee_flush, DMA and 800 rendered frames
 *   follow (externally supplied 60 Hz / 48 kHz schedule - NOT retail EE
 *   thread timing or service order);
 *   teardown: cancel the ambient request, drain to natural completion, reset,
 *   release the bank slot, destroy the IOP model, tear down the world;
 *   then a second cycle re-creates the services and rebinds the sequencer.
 *
 * The audio events are an explicit original rule over world snapshots, not a
 * retail callback: AMBIENT at world entry, CHIME when the fade completes,
 * STEP after every 1.0 unit of planar travel, BUMP when the wall-contact
 * counter increases (8-frame refractory).
 *
 * An independent checker (expected timeline from the authored cue lengths
 * and the harness's own tick schedule, SPU2 key registers, start addresses,
 * ENVX, ownership, memory ownership, end state, standalone world parity and
 * determinism) runs alongside. Pass --json for the report consumed by
 * tools/verify_world_audio.py. Exit 0 only when the positive run passes and
 * every control fails exactly in its intended check.
 */
#include "scene/world_init.h"
#include "audio/audio_sequencer.h"

#include <assert.h>
#include <math.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum {
    EE_RAM_SIZE = 0x02000000u,
    IOP_WINDOW = 0x40000000u,
    IOP_HEAP = 0x4000u,
    BANK_ADDR = 0x01800000u, /* unused by the M23 scene (checked zero) */
    BANK_OFF = 0x30u,
    BANK_SIZE = 0x800u,
    PAYLOAD_OFF = BANK_OFF + BANK_SIZE,
    PAYLOAD_SIZE = 0x50u,
    BANK_TOTAL = PAYLOAD_OFF + PAYLOAD_SIZE,
    SPU_DEST = 0x5010u,
    T4_OFF = 0x90u,
    CUE_AREA = 0x100u,
    CUE_STRIDE = 0x80u,
    FRAMES = 800u, /* 48 kHz / 60 world frames per second */
    MAX_WRITES = 8192u,
    MAX_EVENTS = 256u,
    MAX_FAIL = 24u,
    DRAIN_FRAMES = 16u, /* fixed, identical in connected and standalone runs */
    PLAYER_ADDR = 0x003f92d0u, /* M23 world-owned player block */
};

/* ---- checks ---------------------------------------------------------------- */

enum Check {
    C_WORLD_INIT = 1, C_AUDIO_INIT, C_KEY_ON, C_KEY_OFF, C_SSA, C_ENVX_UNOWNED,
    C_ENVX_SILENT, C_OWNERSHIP, C_MODEL_MASK, C_TIMELINE, C_TEARDOWN_ORDER,
    C_AUDIO_END, C_WORLD_END, C_WORLD_REENTRY, C_MEM_OWNERSHIP, C_AUDIO_MEMORY,
    C_WORLD_PARITY, C_CYCLE_PARITY, C_OUTPUT, C_UNEXPECTED_CALL, C_COUNT
};
static const char *check_name[C_COUNT] = {
    "", "world-init", "audio-init", "key-on", "key-off", "start-address",
    "envx-unowned", "envx-silent", "ownership", "model-mask", "timeline",
    "teardown-order", "audio-end-state", "world-end-state", "world-reentry",
    "memory-ownership", "audio-memory-integrity", "world-parity", "cycle-parity",
    "nonzero-output", "unexpected-call",
};

/* Audio-owned guest ranges [start, end). Every audio-bus write must land in
 * one of these, and world updates must leave them unchanged. */
typedef struct { uint32_t start, end; const char *name; } Range;
static const Range AUDIO_RANGES[] = {
    {0x003e0720u, 0x003e0734u, "context save"},
    {0x003eaa00u, 0x003eaa80u, "registration descriptors"},
    {0x003ead60u, 0x003ead70u, "SIF DMA descriptor"},
    {0x003f2740u, 0x003f2740u + 120u * 8u, "bank id table"},
    {0x00a6c4d8u, 0x00a6c4d8u + 32u * 0x9e4u, "channels"},
    {0x00a80158u, 0x00a80180u, "voice cursor"},
    {0x00a80180u, 0x00a801c0u + 16u * 512u, "SG2 packet ring"},
    {0x00a82640u, 0x00a832b0u, "play commands"},
    {0x00a87440u, 0x00a87440u + 8u * 0x2cu, "bank slots"},
    {BANK_ADDR, BANK_ADDR + BANK_TOTAL, "synthetic bank resource"},
};
#define AUDIO_RANGE_COUNT (sizeof(AUDIO_RANGES) / sizeof(*AUDIO_RANGES))

/* ---- original synthetic cues (HSQ1) ---------------------------------------- */

enum { CUE_AMBIENT, CUE_CHIME, CUE_STEP, CUE_BUMP, CUE_COUNT };
static const char *cue_name[CUE_COUNT] = {"ambient", "chime", "step", "bump"};
/* Authored length in ticks from adoption to natural completion (0 = runs
 * until cancelled), derived by hand from the cue definitions below. */
static const uint32_t CUE_LENGTH[CUE_COUNT] = {0, 12, 4, 6};

typedef struct { uint16_t offset; uint8_t root; int8_t fine; uint8_t volume, pan; } ToneSpec;
/* 0 one-block loop, 1 two-block loop, 2 one-shot, 3 one-shot, 4 one-shot. */
static const ToneSpec TONES[5] = {
    {0x00, 48, 0, 90, 40}, {0x10, 55, 0, 80, 88}, {0x30, 60, 0, 127, 64},
    {0x40, 40, 0, 127, 64}, {0x40, 72, 4, 110, 70},
};
#define LOOPING_TONES 0x3u

typedef struct { uint8_t b[CUE_STRIDE]; uint32_t n; } Blob;
static void put8(Blob *x, uint32_t v) { assert(x->n < CUE_STRIDE); x->b[x->n++] = (uint8_t)v; }
static void put16(Blob *x, uint32_t v) { put8(x, v & 0xffu); put8(x, v >> 8); }
static void hsq_begin(Blob *x, uint32_t tracks, uint32_t priority)
{
    memset(x, 0, sizeof(*x));
    put16(x, 0x5348); put16(x, 0x3151);
    put8(x, HG_SEQ_VERSION); put8(x, tracks); put8(x, 5); put8(x, priority);
    put16(x, HG_SEQ_HEADER_SIZE); put16(x, 0);
    for (int i = 0; i < 4; ++i) put16(x, 0);
    for (int t = 0; t < 5; ++t) {
        put16(x, TONES[t].offset); put8(x, TONES[t].root); put8(x, (uint8_t)TONES[t].fine);
        put16(x, 0x00ff); put16(x, 0x1fc0); put16(x, 0x1000);
        put8(x, TONES[t].volume); put8(x, TONES[t].pan);
    }
}
static void hsq_track(Blob *x, uint32_t t) { x->b[12 + 2 * t] = (uint8_t)x->n; x->b[13 + 2 * t] = 0; }
static void hsq_end(Blob *x) { x->b[10] = (uint8_t)x->n; x->b[11] = (uint8_t)(x->n >> 8); }
static void ev_wait(Blob *x, uint32_t n) { put8(x, HG_SEQ_OP_WAIT); put16(x, n); }
static void ev_on(Blob *x, uint32_t note, uint32_t vel, uint32_t tone) { put8(x, HG_SEQ_OP_NOTE_ON); put8(x, note); put8(x, vel); put8(x, tone); }
static void ev_note(Blob *x, uint32_t note, uint32_t vel, uint32_t tone, uint32_t dur) { put8(x, HG_SEQ_OP_NOTE); put8(x, note); put8(x, vel); put8(x, tone); put16(x, dur); }
static void ev_end(Blob *x) { put8(x, HG_SEQ_OP_END); }

static void build_cues(Blob cue[CUE_COUNT])
{
    Blob *x = &cue[CUE_AMBIENT]; /* two held loops until cancelled */
    hsq_begin(x, 2, 32);
    hsq_track(x, 0); ev_on(x, 48, 70, 0); ev_wait(x, 30); put8(x, HG_SEQ_OP_VOLUME); put8(x, 50);
    ev_wait(x, 60000); ev_end(x);
    hsq_track(x, 1); ev_wait(x, 8); ev_on(x, 55, 60, 1); ev_wait(x, 60000); ev_end(x);
    hsq_end(x);
    x = &cue[CUE_CHIME]; /* two overlapping timed notes, complete at +12 */
    hsq_begin(x, 2, 80);
    hsq_track(x, 0); ev_note(x, 72, 110, 4, 8); ev_wait(x, 8); ev_end(x);
    hsq_track(x, 1); ev_wait(x, 4); ev_note(x, 79, 100, 4, 8); ev_wait(x, 8); ev_end(x);
    hsq_end(x);
    x = &cue[CUE_STEP]; /* note off at +3, complete at +4 */
    hsq_begin(x, 1, 64);
    hsq_track(x, 0); ev_note(x, 60, 100, 2, 3); ev_wait(x, 4); ev_end(x); hsq_end(x);
    x = &cue[CUE_BUMP]; /* note off at +5, complete at +6 */
    hsq_begin(x, 1, 96);
    hsq_track(x, 0); ev_note(x, 40, 127, 3, 5); ev_wait(x, 6); ev_end(x); hsq_end(x);
}

/* ---- fault injection --------------------------------------------------------- */

typedef struct Faults {
    bool omit_state0_init;        /* M23 world fault: player never initialized */
    bool skip_bank_registration;  /* audio bank never published */
    int zero_ticks_from, zero_ticks_until; /* frames advancing 0 sequencer ticks */
    bool flush_before_update;     /* service order: flush, then sequence */
    int early_bank_release_frame; /* release the bank slot while requests sound */
    bool skip_audio_teardown;     /* no cancel/drain/reset before release */
    bool skip_world_pool_reset;   /* M23 world fault: stale actors at re-entry */
    int foreign_key_off_frame;    /* unowned key-off of the ambient voice */
    int audio_stray_write_frame;  /* audio bus write into the player block */
    int world_stray_write_frame;  /* world-side write into the channel table */
} Faults;
static const Faults NO_FAULTS = {false, false, -1, -1, false, -1, false, false, -1, -1, -1};

/* ---- connected rig ------------------------------------------------------------ */

typedef struct { uint32_t address; uint16_t value; } SpuWrite;
typedef struct {
    uint32_t cycle, frame, tick, cue, play_index, request;
    bool accepted, adopted;
} Event;

typedef struct {
    uint32_t frame;
    float pos[3], eye[3], target[3], yaw, speed;
    uint32_t actors_main, actors_type2, free_nodes, wall_contacts; /* contacts: this cycle */
    uint64_t masked_ee;
} Checkpoint;

enum { CP_ENTER, CP_NEUTRAL, CP_WALK, CP_TURN, CP_WALL, CP_RELEASE, CP_DRAIN, CP_TEARDOWN, CP_COUNT };
static const char *cp_name[CP_COUNT] = {
    "after_enter", "after_neutral", "after_walk", "after_turn", "after_wall",
    "after_release", "after_drain", "after_teardown",
};

typedef struct {
    bool ran;
    uint32_t frames, updates, events, accepted, rejected;
    uint32_t by_cue[CUE_COUNT];
    uint32_t decisions, key_on, key_off, steal, drop, complete, cancel, lost, stage_fail;
    uint32_t ring_records, spu_writes, pcm_nonzero, audible_moving_updates;
    uint64_t pcm_hash, spu_hash, decision_hash, world_hash;
    uint32_t start_tick, start_request, start_log;
    uint32_t iop_allocs, iop_frees;
    Checkpoint cp[CP_COUNT];
    /* End-of-cycle audit. */
    uint32_t end_active_requests, end_owned_voices, end_busy_commands, end_open_channels;
    uint32_t end_envelopes, end_slot_state, end_actors, end_free_nodes, end_handles, end_unknown_calls;
} CycleStats;

typedef struct Rig {
    HGWorldInitHarness *world;
    bool connected;            /* false: standalone world, no audio */
    uint8_t *ram;              /* the world's EE RAM */
    uint8_t heap[IOP_HEAP];
    size_t alloc;
    unsigned allocs, frees;
    bool dma_active;
    uint32_t dma_src, dma_dest, dma_size, dma_handle;
    uint32_t tick_source;

    HGAudioBus bus;
    HGAudioIop *iop;
    HGAudioEeService ee;
    HGSequencer *seq;
    bool seq_bound;
    HGSg2Reply reply;
    SpuWrite *writes;
    uint32_t write_count;
    bool last_window_silent;

    Faults f;
    uint32_t cycle;            /* 0-based */
    uint32_t frame;            /* world frames in this cycle */
    uint32_t expected_tick;    /* harness schedule: seq start + updates */
    uint32_t ambient_play, wall_base;
    bool ambient_active;
    float step_anchor[2];
    uint32_t last_bump_frame, last_walls, cycle_allocs, cycle_frees;
    bool fade_seen_active, chime_posted;

    Event events[MAX_EVENTS];
    uint32_t event_count, matched;

    struct { bool owned; uint32_t request, track, note, tone; } owner[HG_SG2_VOICES];
    uint32_t checked;

    uint8_t *audio_shadow;     /* audio ranges copied before each world step */
    uint32_t stray_audio_writes;

    CycleStats stats[2];
    HGWorldFrameSnapshot trace[2][512];
    uint32_t trace_count[2];

    uint32_t fail[MAX_FAIL], fail_count;
    char detail[320];
} Rig;

static void fail(Rig *r, uint32_t check, const char *fmt, ...)
{
    for (uint32_t i = 0; i < r->fail_count; ++i)
        if (r->fail[i] == check)
            return;
    if (r->fail_count < MAX_FAIL)
        r->fail[r->fail_count++] = check;
    if (!r->detail[0]) {
        va_list ap;
        va_start(ap, fmt);
        vsnprintf(r->detail, sizeof(r->detail), fmt, ap);
        va_end(ap);
    }
}

static uint64_t fnv(uint64_t h, const void *data, size_t n)
{
    const uint8_t *p = data;
    for (size_t i = 0; i < n; ++i)
        h = (h ^ p[i]) * 1099511628211ull;
    return h;
}
#define FNV0 1469598103934665603ull

static bool in_audio_range(uint32_t a, uint32_t w)
{
    for (size_t i = 0; i < AUDIO_RANGE_COUNT; ++i)
        if (a >= AUDIO_RANGES[i].start && a + w <= AUDIO_RANGES[i].end)
            return true;
    return false;
}

/* Masked digest of the world's EE: every 64-bit word outside the (sorted,
 * disjoint) audio ranges, widened to whole words. */
static uint64_t masked_ee(const Rig *r)
{
    uint64_t h = FNV0;
    uint32_t a = 0;
    for (size_t i = 0; i <= AUDIO_RANGE_COUNT; ++i) {
        uint32_t s = i < AUDIO_RANGE_COUNT ? AUDIO_RANGES[i].start & ~7u : EE_RAM_SIZE;
        for (; a < s; a += 8) {
            uint64_t word;
            memcpy(&word, r->ram + a, 8);
            h = (h ^ word) * 1099511628211ull;
        }
        if (i < AUDIO_RANGE_COUNT && a < ((AUDIO_RANGES[i].end + 7u) & ~7u))
            a = (AUDIO_RANGES[i].end + 7u) & ~7u;
    }
    return h;
}

/* ---- shared guest bus ------------------------------------------------------------ */

static uint8_t *guest(Rig *r, uint32_t a, unsigned w)
{
    uint8_t *p = hg_world_harness_guest_ram(r->world, a, w);
    assert(p);
    return p;
}

static uint32_t ld(Rig *r, uint32_t a, unsigned w)
{
    uint8_t *p = guest(r, a, w);
    uint32_t v = 0;
    for (unsigned i = 0; i < w; ++i)
        v |= (uint32_t)p[i] << (8 * i);
    return v;
}

static void st_raw(Rig *r, uint32_t a, unsigned w, uint32_t v)
{
    uint8_t *p = guest(r, a, w);
    for (unsigned i = 0; i < w; ++i)
        p[i] = (uint8_t)(v >> (8 * i));
}

/* Audio-side writes: checked against the declared audio ownership. */
static void st(Rig *r, uint32_t a, unsigned w, uint32_t v)
{
    if (!in_audio_range(a, w)) {
        r->stray_audio_writes++;
        fail(r, C_MEM_OWNERSHIP, "audio write %08x/%u outside audio-owned ranges (cycle %u frame %u)",
             a, w, r->cycle, r->frame);
    }
    st_raw(r, a, w, v);
}

static uint8_t b_r8(void *c, uint32_t a) { return (uint8_t)ld(c, a, 1); }
static uint16_t b_r16(void *c, uint32_t a) { return (uint16_t)ld(c, a, 2); }
static uint32_t b_r32(void *c, uint32_t a) { return ld(c, a, 4); }
static void b_w8(void *c, uint32_t a, uint8_t v) { st(c, a, 1, v); }
static void b_w16(void *c, uint32_t a, uint16_t v) { st(c, a, 2, v); }
static void b_w32(void *c, uint32_t a, uint32_t v) { st(c, a, 4, v); }

static bool iop_read(void *c, uint32_t src, uint8_t *out, size_t size)
{
    Rig *r = c;
    if (src < IOP_WINDOW || src - IOP_WINDOW > r->alloc || size > r->alloc - (src - IOP_WINDOW))
        return false;
    memcpy(out, r->heap + (src - IOP_WINDOW), size);
    return true;
}

static void spu_write(void *c, uint32_t address, uint16_t value)
{
    Rig *r = c;
    if (r->write_count < MAX_WRITES)
        r->writes[r->write_count++] = (SpuWrite){address, value};
    CycleStats *s = &r->stats[r->cycle];
    s->spu_writes++;
    uint32_t pair[2] = {address, value};
    s->spu_hash = fnv(s->spu_hash, pair, sizeof(pair));
}

static uint32_t bus_call(void *c, uint32_t target, uint32_t a0, uint32_t a1,
                         uint32_t a2, uint32_t a3, uint32_t t0)
{
    Rig *r = c;
    (void)a3;
    (void)t0;
    switch (target) {
    case HG_X_INTS_OFF: case HG_X_INTS_ON: case HG_X_FLUSHCACHE:
    case 0x002a9d70u: case HG_X_PLAY_EXTRA: case HG_X_FLAG_SVC:
        return 0;
    case 0x002a9180u:
        hg_audio_channel_init_b(&r->bus, a0);
        return 0;
    case 0x002b54e8u:
        hg_audio_slot_clear(&r->bus, a0);
        return 0;
    case HG_X_SIF_ALLOC:
        if (r->alloc || !a0 || a0 > IOP_HEAP)
            return 0;
        memset(r->heap, 0, a0);
        r->alloc = a0;
        r->allocs++;
        return IOP_WINDOW;
    case HG_X_SIF_FREE:
        if (a0 != IOP_WINDOW || !r->alloc || r->dma_active)
            fail(r, C_UNEXPECTED_CALL, "invalid SIF free %08x", a0);
        r->alloc = 0;
        r->frees++;
        return 0;
    case HG_X_SIF_SET_DMA:
        if (a1 != 1 || r->dma_active)
            fail(r, C_UNEXPECTED_CALL, "invalid SIF DMA start");
        r->dma_src = ld(r, a0, 4);
        r->dma_dest = ld(r, a0 + 4, 4);
        r->dma_size = ld(r, a0 + 8, 4);
        r->dma_active = true;
        return ++r->dma_handle;
    case HG_X_SIF_DMA_STAT:
        if (r->dma_active && a0 == r->dma_handle) {
            uint32_t off = r->dma_dest - IOP_WINDOW;
            if (r->dma_dest < IOP_WINDOW || off + r->dma_size > r->alloc)
                fail(r, C_UNEXPECTED_CALL, "SIF DMA outside the IOP staging allocation");
            else
                memcpy(r->heap + off, guest(r, r->dma_src, r->dma_size), r->dma_size);
            r->dma_active = false;
        }
        return UINT32_MAX;
    case HG_SG2_UPLOAD_START: case HG_SG2_UPLOAD_POLL: case HG_SG2_BANK_BIND:
        return hg_audio_ee_bank_call(&r->ee, target, a0, a1, a2);
    case HG_X_GLOBAL_TICK:
        return ++r->tick_source;
    case HG_X_TIME_A:
        return a0 * 300u;
    case HG_X_TIME_B:
        return a0 ^ (a1 >> 16);
    default:
        fail(r, C_UNEXPECTED_CALL, "unexpected audio bus call %08x", target);
        return 0;
    }
}

/* ---- synthetic bank ------------------------------------------------------------------ */

static void w16le(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static void w32le(uint8_t *p, uint32_t v) { for (int i = 0; i < 4; ++i) p[i] = (uint8_t)(v >> (8 * i)); }

static uint64_t build_bank(Rig *r)
{
    uint8_t *res = guest(r, BANK_ADDR, BANK_TOTAL), *bank = res + BANK_OFF, *t4 = bank + T4_OFF;
    memset(res, 0, BANK_TOTAL);
    w32le(res + 0x00, BANK_TOTAL);
    w32le(res + 0x04, BANK_OFF);
    w32le(res + 0x0c, 1);
    w32le(res + 0x10, PAYLOAD_OFF);
    w32le(res + 0x14, PAYLOAD_SIZE);
    w32le(res + 0x18, PAYLOAD_OFF);
    w32le(res + 0x20, PAYLOAD_SIZE);
    w32le(res + 0x24, BANK_OFF);
    w32le(res + 0x2c, 0x7f);
    w32le(bank + 0x04, PAYLOAD_SIZE);
    w32le(bank + 0x0c, HG_BANK_MAGIC);
    w32le(bank + 0x10, 0x80);
    w32le(bank + 0x14, UINT32_MAX);
    w32le(bank + 0x18, UINT32_MAX);
    w32le(bank + 0x1c, T4_OFF);
    w32le(bank + 0x20, UINT32_MAX);
    w32le(bank + 0x24, UINT32_MAX);
    w32le(bank + 0x7c, UINT32_MAX);
    w16le(t4, 0);
    w16le(t4 + 2, 0x10);
    w16le(t4 + 0x10, CUE_COUNT - 1);
    Blob cue[CUE_COUNT];
    build_cues(cue);
    for (uint32_t i = 0; i < CUE_COUNT; ++i) {
        uint32_t at = CUE_AREA + i * CUE_STRIDE;
        w16le(t4 + 0x12 + 2 * i, (uint16_t)at);
        memcpy(t4 + at, cue[i].b, cue[i].n);
    }
    assert(T4_OFF + CUE_AREA + CUE_COUNT * CUE_STRIDE <= BANK_SIZE);
    /* Original synthetic ADPCM blocks (filter 0, shift 3). */
    uint8_t *pcm = res + PAYLOAD_OFF;
    pcm[0x00] = 0x03; pcm[0x01] = 0x07; memset(pcm + 0x02, 0x6a, 7); memset(pcm + 0x09, 0xa6, 7);
    pcm[0x10] = 0x03; pcm[0x11] = 0x04; memset(pcm + 0x12, 0x25, 14);
    pcm[0x20] = 0x03; pcm[0x21] = 0x03; memset(pcm + 0x22, 0xc3, 14);
    pcm[0x30] = 0x03; pcm[0x31] = 0x01; memset(pcm + 0x32, 0x71, 14);
    pcm[0x40] = 0x03; pcm[0x41] = 0x01; memset(pcm + 0x42, 0x4e, 14);
    return fnv(FNV0, res, BANK_TOTAL);
}

static bool register_bank(Rig *r)
{
    for (unsigned step = 0; step < 32; ++step) {
        if (hg_audio_register_entry(&r->bus, BANK_ADDR))
            return ld(r, HG_AUDIO_ID_TABLE, 4) == 0;
        if (!hg_audio_ee_flush(&r->ee, &r->reply))
            return false;
        if (r->reply.upload_busy) {
            if (!hg_audio_iop_step_dma(r->iop) || !hg_audio_ee_flush(&r->ee, &r->reply))
                return false;
        }
    }
    return false;
}

static uint64_t g_bank_hash;

static bool audio_up(Rig *r)
{
    HGSpu2Io io = {r, iop_read, spu_write, NULL, NULL};
    r->iop = hg_audio_iop_create(io);
    if (!r->iop)
        return false;
    hg_audio_ee_service_init(&r->ee, &r->bus, r->iop);
    hg_audio_slots_init(&r->bus);
    st(r, HG_AUDIO_CTX_SAVE, 4, SPU_DEST);
    for (uint32_t ch = 0; ch < 32; ++ch)
        st(r, HG_AUDIO_CHANNEL_BASE + ch * HG_CH_STRIDE, 4, UINT32_MAX);
    for (uint32_t a = BANK_ADDR; a < BANK_ADDR + BANK_TOTAL; a += 4)
        if (ld(r, a, 4) && r->cycle == 0) {
            fail(r, C_AUDIO_INIT, "bank region %08x already in use by the world", a);
            break;
        }
    g_bank_hash = build_bank(r);
    memset(&r->reply, 0, sizeof(r->reply));
    if (!r->f.skip_bank_registration && !register_bank(r)) {
        fail(r, C_AUDIO_INIT, "bank registration did not complete (cycle %u)", r->cycle);
        return false;
    }
    bool bound = r->seq_bound ? hg_seq_rebind(r->seq, &r->ee)
                              : hg_seq_init(r->seq, &r->ee, (HGSeqConfig){8, 3, 0});
    if (!bound) {
        fail(r, C_AUDIO_INIT, "sequencer did not bind");
        return false;
    }
    r->seq_bound = true;
    r->write_count = 0;
    return true;
}

/* ---- independent checker ------------------------------------------------------------- */

static void spu_key_masks(const Rig *r, uint32_t lo_off, uint32_t count[HG_SG2_VOICES])
{
    memset(count, 0, HG_SG2_VOICES * sizeof(*count));
    for (uint32_t i = 0; i < r->write_count; ++i) {
        uint32_t a = r->writes[i].address - 0xbf900000u, core = a / 0x400, off = a % 0x400;
        if (core > 1 || (off != lo_off && off != lo_off + 2))
            continue;
        uint32_t bits = (uint32_t)r->writes[i].value << (off == lo_off ? 0 : 16);
        for (uint32_t v = 0; v < 24; ++v)
            if (bits & (1u << v))
                count[core * 24 + v]++;
    }
}

static bool spu_start_address(const Rig *r, uint32_t voice, uint32_t *address)
{
    uint32_t core = voice / 24, idx = voice % 24, hi = UINT32_MAX, lo = UINT32_MAX;
    for (uint32_t i = 0; i < r->write_count; ++i) {
        uint32_t a = r->writes[i].address - 0xbf900000u - core * 0x400;
        if (a == 0x1c0 + 12 * idx) hi = r->writes[i].value;
        if (a == 0x1c2 + 12 * idx) lo = r->writes[i].value;
    }
    if (hi == UINT32_MAX || lo == UINT32_MAX)
        return false;
    *address = hi << 17 | lo << 1;
    return true;
}

static void check_window(Rig *r)
{
    const HGSequencer *s = r->seq;
    uint32_t want_on[HG_SG2_VOICES] = {0}, want_off[HG_SG2_VOICES] = {0};
    uint32_t got_on[HG_SG2_VOICES], got_off[HG_SG2_VOICES];
    for (uint32_t i = r->checked; i < s->log_count; ++i) {
        const HGSeqDecision *d = &s->log[i];
        if (d->kind == HG_SEQ_D_KEY_ON) {
            want_on[d->voice]++;
            uint32_t address;
            if (!spu_start_address(r, d->voice, &address) || address != (uint32_t)SPU_DEST + TONES[d->detail].offset)
                fail(r, C_SSA, "voice %u start address mismatch (tick %u)", d->voice, d->tick);
            if (r->owner[d->voice].owned)
                fail(r, C_OWNERSHIP, "voice %u keyed on while owned (tick %u)", d->voice, d->tick);
            r->owner[d->voice].owned = true;
            r->owner[d->voice].request = d->request;
            r->owner[d->voice].track = d->track;
            r->owner[d->voice].note = d->note;
            r->owner[d->voice].tone = d->detail;
        } else if (d->kind == HG_SEQ_D_KEY_OFF) {
            want_off[d->voice]++;
            if (!r->owner[d->voice].owned || r->owner[d->voice].request != d->request ||
                r->owner[d->voice].track != d->track || r->owner[d->voice].note != d->note)
                fail(r, C_OWNERSHIP, "voice %u released by request %u, not its owner (tick %u)",
                     d->voice, d->request, d->tick);
            r->owner[d->voice].owned = false;
        }
    }
    r->checked = s->log_count;
    spu_key_masks(r, 0x1a0, got_on);
    spu_key_masks(r, 0x1a4, got_off);
    uint32_t model_mask[2];
    hg_seq_owned_voices(s, model_mask);
    for (uint32_t v = 0; v < HG_SG2_VOICES; ++v) {
        if (got_on[v] != want_on[v])
            fail(r, C_KEY_ON, "voice %u: %u key-on decisions, %u SPU2 key-ons (cycle %u frame %u)",
                 v, want_on[v], got_on[v], r->cycle, r->frame);
        if (got_off[v] != want_off[v])
            fail(r, C_KEY_OFF, "voice %u: %u key-off decisions, %u SPU2 key-offs (cycle %u frame %u)",
                 v, want_off[v], got_off[v], r->cycle, r->frame);
        bool owned = r->owner[v].owned;
        bool active = r->reply.envelope[v] != 0;
        if (active && !owned)
            fail(r, C_ENVX_UNOWNED, "voice %u sounding without an owner (cycle %u frame %u)", v, r->cycle, r->frame);
        if (owned && !active && (LOOPING_TONES >> r->owner[v].tone & 1u))
            fail(r, C_ENVX_SILENT, "owned looping voice %u silent (cycle %u frame %u)", v, r->cycle, r->frame);
        if (owned != (bool)(model_mask[v / 24] >> (v % 24) & 1u))
            fail(r, C_MODEL_MASK, "voice %u: checker ownership disagrees with model", v);
    }
}

/* Map adoptions to posted events (in order, by play index) and check each
 * request against the harness schedule and the authored cue length. */
static void check_timeline(Rig *r)
{
    const HGSequencer *s = r->seq;
    CycleStats *cs = &r->stats[r->cycle];
    for (uint32_t i = cs->start_log; i < s->log_count; ++i) {
        const HGSeqDecision *d = &s->log[i];
        if (d->kind != HG_SEQ_D_START)
            continue;
        uint32_t k = 0;
        for (; k < r->event_count; ++k)
            if (r->events[k].cycle == r->cycle && r->events[k].accepted && !r->events[k].adopted &&
                r->events[k].play_index == d->play_index)
                break;
        if (k == r->event_count) {
            fail(r, C_TIMELINE, "adoption of play %u without a posted event", d->play_index);
            continue;
        }
        Event *e = &r->events[k];
        e->adopted = true;
        e->request = d->request;
        if (d->tick != e->tick)
            fail(r, C_TIMELINE, "%s request adopted at tick %u, schedule %u", cue_name[e->cue], d->tick, e->tick);
    }
    for (uint32_t k = 0; k < r->event_count; ++k) {
        Event *e = &r->events[k];
        if (e->cycle != r->cycle || !e->accepted)
            continue;
        if (!e->adopted) {
            fail(r, C_TIMELINE, "%s request at frame %u never adopted", cue_name[e->cue], e->frame);
            continue;
        }
        uint32_t want = CUE_LENGTH[e->cue] ? HG_SEQ_D_COMPLETE : HG_SEQ_D_CANCEL, seen = 0, at = 0;
        for (uint32_t i = 0; i < s->log_count; ++i) {
            const HGSeqDecision *d = &s->log[i];
            if (d->request == e->request && (d->kind == HG_SEQ_D_COMPLETE || d->kind == HG_SEQ_D_CANCEL ||
                                             d->kind == HG_SEQ_D_LOST)) {
                seen = d->kind;
                at = d->tick;
            }
        }
        if (seen != want)
            fail(r, seen == HG_SEQ_D_LOST ? C_TEARDOWN_ORDER : C_TIMELINE,
                 "%s request %u retired as kind %u, expected %u", cue_name[e->cue], e->request, seen, want);
        else if (CUE_LENGTH[e->cue] && at != e->tick + CUE_LENGTH[e->cue])
            fail(r, C_TIMELINE, "%s request %u completed at tick %u, authored %u", cue_name[e->cue],
                 e->request, at, e->tick + CUE_LENGTH[e->cue]);
    }
}

/* ---- per-frame update ---------------------------------------------------------------- */

static void post(Rig *r, uint32_t cue)
{
    if (r->event_count >= MAX_EVENTS) {
        fail(r, C_AUDIO_INIT, "event capacity exceeded");
        return;
    }
    uint32_t play = hg_audio_play(&r->bus, 0, 0, cue, 0, 0);
    Event *e = &r->events[r->event_count++];
    *e = (Event){r->cycle, r->frame, r->expected_tick + 1u, cue, play, 0, play < 32u, false};
    CycleStats *s = &r->stats[r->cycle];
    s->events++;
    s->by_cue[cue]++;
    if (e->accepted) {
        s->accepted++;
        if (cue == CUE_AMBIENT) {
            r->ambient_play = play;
            r->ambient_active = true;
        }
    } else {
        s->rejected++;
        fail(r, C_AUDIO_INIT, "%s request rejected (%d) at cycle %u frame %u", cue_name[cue],
             (int)play, r->cycle, r->frame);
    }
}

static void derive_events(Rig *r, const HGWorldFrameSnapshot *snap)
{
    if (snap->fade_phase != 0u)
        r->fade_seen_active = true;
    else if (r->fade_seen_active && !r->chime_posted) {
        r->chime_posted = true;
        post(r, CUE_CHIME);
    }
    float dx = snap->player_pos[0] - r->step_anchor[0], dz = snap->player_pos[2] - r->step_anchor[1];
    if (dx * dx + dz * dz >= 1.0f) {
        r->step_anchor[0] = snap->player_pos[0];
        r->step_anchor[1] = snap->player_pos[2];
        post(r, CUE_STEP);
    }
    if (snap->wall_contact_events > r->last_walls && r->frame >= r->last_bump_frame + 8u) {
        r->last_bump_frame = r->frame;
        post(r, CUE_BUMP);
    }
    r->last_walls = snap->wall_contact_events;
}

static void audio_update(Rig *r, bool moving)
{
    CycleStats *s = &r->stats[r->cycle];
    int f = (int)r->frame;
    uint32_t ticks = (f >= r->f.zero_ticks_from && f < r->f.zero_ticks_until) ? 0u : 1u;
    r->write_count = 0;
    if (r->f.flush_before_update) {
        if (!hg_audio_ee_flush(&r->ee, &r->reply))
            fail(r, C_AUDIO_INIT, "flush refused");
    }
    if (f == r->f.early_bank_release_frame && r->cycle == 0)
        hg_audio_slot_clear(&r->bus, 0);
    if (!hg_seq_update(r->seq, ticks))
        fail(r, C_AUDIO_INIT, "sequencer update refused (cycle %u frame %u)", r->cycle, r->frame);
    r->expected_tick++;
    if (f == r->f.foreign_key_off_frame && r->cycle == 0)
        hg_audio_ee_stage_key(&r->ee, 8, false);
    if (f == r->f.audio_stray_write_frame && r->cycle == 0)
        b_w32(r, PLAYER_ADDR + 0x40u, 0x41200000u);
    s->ring_records += ld(r, HG_SG2_EE_CURSOR, 4);
    if (!r->f.flush_before_update) {
        if (!hg_audio_ee_flush(&r->ee, &r->reply))
            fail(r, C_AUDIO_INIT, "flush refused");
    }
    if (r->reply.upload_busy && !hg_audio_iop_step_dma(r->iop))
        fail(r, C_AUDIO_INIT, "DMA step refused");
    check_window(r);
    int16_t pcm[FRAMES * 2];
    hg_audio_iop_render(r->iop, pcm, FRAMES);
    r->last_window_silent = true;
    for (uint32_t i = 0; i < FRAMES; ++i) {
        if (pcm[2 * i] || pcm[2 * i + 1]) {
            s->pcm_nonzero++;
            r->last_window_silent = false;
        }
    }
    s->pcm_hash = fnv(s->pcm_hash, pcm, sizeof(pcm));
    if (moving && !r->last_window_silent)
        s->audible_moving_updates++;
    s->updates++;
}

static void shadow_audio(Rig *r)
{
    size_t o = 0;
    for (size_t i = 0; i < AUDIO_RANGE_COUNT; ++i) {
        uint32_t n = AUDIO_RANGES[i].end - AUDIO_RANGES[i].start;
        memcpy(r->audio_shadow + o, guest(r, AUDIO_RANGES[i].start, n), n);
        o += n;
    }
}

static void check_audio_shadow(Rig *r)
{
    size_t o = 0;
    for (size_t i = 0; i < AUDIO_RANGE_COUNT; ++i) {
        uint32_t n = AUDIO_RANGES[i].end - AUDIO_RANGES[i].start;
        if (memcmp(r->audio_shadow + o, guest(r, AUDIO_RANGES[i].start, n), n))
            fail(r, C_AUDIO_MEMORY, "world update changed audio-owned %s (cycle %u frame %u)",
                 AUDIO_RANGES[i].name, r->cycle, r->frame);
        o += n;
    }
}

static HGWorldInitError step(Rig *r, const HGSyntheticPadInput *pad, HGWorldFrameSnapshot *snap)
{
    if (r->connected)
        shadow_audio(r);
    HGWorldInitError err = hg_world_harness_step_frame(r->world, pad, snap);
    if (err != HG_WORLD_OK) {
        fail(r, C_WORLD_INIT, "world step failed: %s", hg_world_harness_error_name(err));
        return err;
    }
    if (r->trace_count[r->cycle] < 512)
        r->trace[r->cycle][r->trace_count[r->cycle]++] = *snap;
    r->stats[r->cycle].frames++;
    r->frame++;
    if (r->connected) {
        if ((int)r->frame == r->f.world_stray_write_frame && r->cycle == 0)
            st_raw(r, HG_AUDIO_CHANNEL_BASE + 4u, 4, 0x5a5a5a5au); /* stray world-side write */
        check_audio_shadow(r);
        derive_events(r, snap);
        audio_update(r, snap->player_speed > 0.0f);
    }
    return HG_WORLD_OK;
}

static void checkpoint(Rig *r, uint32_t which, const HGWorldFrameSnapshot *snap)
{
    Checkpoint *c = &r->stats[r->cycle].cp[which];
    c->frame = r->frame;
    for (int i = 0; i < 3; ++i) {
        c->pos[i] = snap->player_pos[i];
        c->eye[i] = snap->camera_eye[i];
        c->target[i] = snap->camera_target[i];
    }
    c->yaw = snap->player_rot[1];
    c->speed = snap->player_speed;
    c->actors_main = snap->main_actor_count;
    c->actors_type2 = snap->type2_actor_count;
    c->free_nodes = snap->free_node_count;
    c->wall_contacts = snap->wall_contact_events - r->wall_base;
    c->masked_ee = masked_ee(r);
}

static bool run_frames(Rig *r, const HGSyntheticPadInput *pad, unsigned n, HGWorldFrameSnapshot *snap)
{
    for (unsigned i = 0; i < n; ++i)
        if (step(r, pad, snap) != HG_WORLD_OK)
            return false;
    return true;
}

static void audit_end(Rig *r)
{
    CycleStats *s = &r->stats[r->cycle];
    uint32_t mask[2];
    if (r->connected) {
        s->end_active_requests = hg_seq_active_requests(r->seq);
        s->end_owned_voices = hg_seq_owned_voices(r->seq, mask);
        for (uint32_t i = 0; i < 32; ++i) {
            s->end_busy_commands += ld(r, HG_AUDIO_COMMAND_BASE + i * HG_CMD_STRIDE + HG_CMD_STATE, 4) != 0;
            s->end_open_channels += ld(r, HG_AUDIO_CHANNEL_BASE + i * HG_CH_STRIDE, 4) != UINT32_MAX;
        }
        for (uint32_t v = 0; v < HG_SG2_VOICES; ++v)
            s->end_envelopes += r->reply.envelope[v] != 0;
        s->end_slot_state = ld(r, HG_AUDIO_SLOT_TABLE + HG_SLOT_STATE, 4);
        s->iop_allocs = r->allocs - r->cycle_allocs;
        s->iop_frees = r->frees - r->cycle_frees;
        if (s->end_active_requests || s->end_owned_voices || s->end_busy_commands ||
            s->end_open_channels || s->end_envelopes || !r->last_window_silent ||
            r->allocs != r->frees || r->dma_active)
            fail(r, C_AUDIO_END, "stale audio state after cycle %u: %u requests, %u voices, %u commands, "
                 "%u channels, %u envelopes", r->cycle, s->end_active_requests, s->end_owned_voices,
                 s->end_busy_commands, s->end_open_channels, s->end_envelopes);
        if (s->end_slot_state)
            fail(r, C_AUDIO_END, "bank slot still bound after release (cycle %u)", r->cycle);
    }
}

/* One full cycle. Returns false when the world could not be (re)entered. */
static bool run_cycle(Rig *r)
{
    CycleStats *s = &r->stats[r->cycle];
    s->ran = true;
    s->pcm_hash = s->spu_hash = s->decision_hash = s->world_hash = FNV0;
    r->frame = 0;
    r->fade_seen_active = r->chime_posted = false;
    r->last_bump_frame = 0;
    r->ambient_active = false;
    memset(r->owner, 0, sizeof(r->owner));

    HGWorldInitError err = hg_world_harness_publish_resources(r->world);
    if (err == HG_WORLD_OK)
        err = hg_world_harness_enter_world(r->world);
    if (err != HG_WORLD_OK) {
        fail(r, r->cycle ? C_WORLD_REENTRY : C_WORLD_INIT, "world entry failed in cycle %u: %s",
             r->cycle, hg_world_harness_error_name(err));
        return false;
    }
    HGWorldFrameSnapshot snap;
    assert(hg_world_harness_snapshot(r->world, &snap) == HG_WORLD_OK);
    /* The M23 harness's wall-contact diagnostic counts across cycles. */
    r->wall_base = r->last_walls = snap.wall_contact_events;
    r->cycle_allocs = r->allocs;
    r->cycle_frees = r->frees;
    checkpoint(r, CP_ENTER, &snap);
    r->step_anchor[0] = snap.player_pos[0];
    r->step_anchor[1] = snap.player_pos[2];

    if (r->connected) {
        if (!audio_up(r))
            return false;
        s->start_tick = r->seq->tick;
        s->start_request = r->seq->next_request;
        s->start_log = r->seq->log_count;
        r->expected_tick = r->seq->tick;
        r->checked = r->seq->log_count;
        post(r, CUE_AMBIENT);
    }

    /* M23 controller script. */
    HGSyntheticPadInput neutral = hg_synthetic_pad_neutral(), walk = neutral, turn = neutral;
    walk.ly = 0x00u;
    turn.lx = 0x10u;
    turn.ly = 0x00u;
    unsigned fade = 0;
    do {
        if (step(r, fade == 10u ? &walk : &neutral, &snap) != HG_WORLD_OK)
            return false;
        fade++;
    } while (snap.fade_phase != 0u && fade < 128u);
    if (!run_frames(r, &neutral, 4, &snap)) return false;
    checkpoint(r, CP_NEUTRAL, &snap);
    if (!run_frames(r, &walk, 14, &snap)) return false;
    checkpoint(r, CP_WALK, &snap);
    if (!run_frames(r, &turn, 12, &snap)) return false;
    checkpoint(r, CP_TURN, &snap);
    if (!run_frames(r, &walk, 20, &snap)) return false;
    checkpoint(r, CP_WALL, &snap);
    if (!run_frames(r, &neutral, 14, &snap)) return false;
    checkpoint(r, CP_RELEASE, &snap);

    /* Audio teardown: cancel the ambient request, drain to completion. */
    if (r->connected) {
        if (!r->f.skip_audio_teardown && r->ambient_active) {
            if (hg_audio_cancel(&r->bus, r->ambient_play) != 0)
                fail(r, C_TEARDOWN_ORDER, "ambient cancel refused");
            r->ambient_active = false;
        }
    }
    if (!run_frames(r, &neutral, DRAIN_FRAMES, &snap)) return false;
    checkpoint(r, CP_DRAIN, &snap);

    if (r->connected) {
        check_timeline(r);
        if (!r->f.skip_audio_teardown) {
            if (hg_seq_active_requests(r->seq) || hg_seq_owned_voices(r->seq, NULL))
                fail(r, C_TEARDOWN_ORDER, "requests or voices remain before bank release");
            hg_seq_reset(r->seq);
            r->write_count = 0;
            if (!hg_audio_ee_flush(&r->ee, &r->reply))
                fail(r, C_AUDIO_END, "final flush refused");
            check_window(r);
        }
        hg_audio_slot_clear(&r->bus, 0); /* bank release */
        for (uint32_t i = s->start_log; i < r->seq->log_count; ++i) {
            const HGSeqDecision *d = &r->seq->log[i];
            s->decisions++;
            s->key_on += d->kind == HG_SEQ_D_KEY_ON;
            s->key_off += d->kind == HG_SEQ_D_KEY_OFF;
            s->steal += d->kind == HG_SEQ_D_STEAL;
            s->drop += d->kind == HG_SEQ_D_DROP;
            s->complete += d->kind == HG_SEQ_D_COMPLETE;
            s->cancel += d->kind == HG_SEQ_D_CANCEL;
            s->lost += d->kind == HG_SEQ_D_LOST;
            s->stage_fail += d->kind == HG_SEQ_D_STAGE_FAIL;
            /* Cycle-relative decision digest: tick and request ids (including a
             * steal's victim, but not the reset sentinel) are offset to the
             * cycle start; the play index
             * rotates in guest memory and is excluded. */
            uint32_t rel[7] = {d->tick - s->start_tick, d->kind,
                               d->request == UINT32_MAX ? UINT32_MAX : d->request - s->start_request,
                               d->track, d->note, d->voice,
                               d->kind == HG_SEQ_D_START ? 0u
                               : d->kind == HG_SEQ_D_STEAL ? d->detail - s->start_request : d->detail};
            s->decision_hash = fnv(s->decision_hash, rel, sizeof(rel));
        }
        if (s->lost)
            fail(r, C_TEARDOWN_ORDER, "%u requests lost their bank or command before retirement", s->lost);
        audit_end(r);
        hg_audio_iop_destroy(r->iop);
        r->iop = NULL;
        r->ee.iop = NULL;
    }

    err = hg_world_harness_teardown(r->world);
    if (err != HG_WORLD_OK) {
        fail(r, C_WORLD_END, "world teardown failed: %s", hg_world_harness_error_name(err));
        return false;
    }
    assert(hg_world_harness_snapshot(r->world, &snap) == HG_WORLD_OK);
    checkpoint(r, CP_TEARDOWN, &snap);
    s->end_actors = snap.main_actor_count + snap.type2_actor_count;
    s->end_free_nodes = snap.free_node_count;
    s->end_handles = (snap.handle_area != 0u) + (snap.handle_title != 0u);
    s->end_unknown_calls = hg_world_harness_unknown_call_count(r->world);
    if (s->end_actors || s->end_free_nodes != 2048u || s->end_handles || s->end_unknown_calls)
        fail(r, C_WORLD_END, "stale world state after cycle %u", r->cycle);
    for (uint32_t i = 0; i < r->trace_count[r->cycle]; ++i) {
        const HGWorldFrameSnapshot *t = &r->trace[r->cycle][i];
        s->world_hash = fnv(s->world_hash, t->player_pos, sizeof(t->player_pos));
        s->world_hash = fnv(s->world_hash, t->player_rot, sizeof(t->player_rot));
        s->world_hash = fnv(s->world_hash, t->camera_eye, sizeof(t->camera_eye));
        s->world_hash = fnv(s->world_hash, t->camera_target, sizeof(t->camera_target));
        s->world_hash = fnv(s->world_hash, &t->player_speed, sizeof(t->player_speed));
    }
    return true;
}

static Rig *rig_create(bool connected, Faults f)
{
    Rig *r = calloc(1, sizeof(*r));
    assert(r);
    r->connected = connected;
    r->f = f;
    HGSyntheticSceneConfig cfg = hg_synthetic_scene_default();
    HGWorldFaultInject wf = {0};
    wf.omit_state0_init = f.omit_state0_init;
    wf.skip_teardown_pool_reset = f.skip_world_pool_reset;
    r->world = hg_world_harness_create(&cfg, &wf);
    assert(r->world);
    r->ram = hg_world_harness_guest_ram(r->world, 0, EE_RAM_SIZE);
    assert(r->ram);
    r->writes = calloc(MAX_WRITES, sizeof(*r->writes));
    r->seq = calloc(1, sizeof(*r->seq));
    size_t shadow = 0;
    for (size_t i = 0; i < AUDIO_RANGE_COUNT; ++i)
        shadow += AUDIO_RANGES[i].end - AUDIO_RANGES[i].start;
    r->audio_shadow = calloc(shadow, 1);
    assert(r->writes && r->seq && r->audio_shadow);
    r->bus = (HGAudioBus){b_r8, b_r16, b_r32, b_w8, b_w16, b_w32, bus_call, r};
    return r;
}

static void rig_destroy(Rig *r)
{
    if (r->iop)
        hg_audio_iop_destroy(r->iop);
    hg_world_harness_destroy(r->world);
    free(r->audio_shadow);
    free(r->seq);
    free(r->writes);
    free(r);
}

static Rig *run_scenario(bool connected, Faults f)
{
    Rig *r = rig_create(connected, f);
    if (hg_world_harness_cold_boot(r->world) != HG_WORLD_OK) {
        fail(r, C_WORLD_INIT, "cold boot failed");
        return r;
    }
    for (r->cycle = 0; r->cycle < 2; ++r->cycle)
        if (!run_cycle(r))
            break;
    if (r->cycle == 2)
        r->cycle = 1;
    return r;
}

/* Cross-run comparisons (standalone M23 world parity, second-cycle parity). */
static void compare(Rig *r, const Rig *standalone)
{
    if (!r->stats[1].ran)
        return;
    for (uint32_t c = 0; c < 2; ++c) {
        if (r->trace_count[c] != standalone->trace_count[c]) {
            fail(r, C_WORLD_PARITY, "cycle %u ran %u world frames, standalone %u", c,
                 r->trace_count[c], standalone->trace_count[c]);
            continue;
        }
        for (uint32_t i = 0; i < r->trace_count[c]; ++i) {
            const HGWorldFrameSnapshot *a = &r->trace[c][i], *b = &standalone->trace[c][i];
            if (memcmp(a->player_pos, b->player_pos, sizeof(a->player_pos)) ||
                memcmp(a->player_rot, b->player_rot, sizeof(a->player_rot)) ||
                memcmp(a->camera_eye, b->camera_eye, sizeof(a->camera_eye)) ||
                memcmp(a->camera_target, b->camera_target, sizeof(a->camera_target)) ||
                memcmp(&a->player_speed, &b->player_speed, sizeof(float)) ||
                a->main_actor_count != b->main_actor_count || a->free_node_count != b->free_node_count ||
                a->wall_contact_events != b->wall_contact_events) {
                fail(r, C_WORLD_PARITY, "cycle %u frame %u world state differs from standalone M23", c, i);
                break;
            }
        }
        for (uint32_t k = 0; k < CP_COUNT; ++k)
            if (r->stats[c].cp[k].masked_ee != standalone->stats[c].cp[k].masked_ee)
                fail(r, C_WORLD_PARITY, "cycle %u %s: world-owned EE differs from standalone M23", c, cp_name[k]);
    }
    const CycleStats *a = &r->stats[0], *b = &r->stats[1];
    if (a->decision_hash != b->decision_hash || a->pcm_hash != b->pcm_hash || a->spu_hash != b->spu_hash ||
        a->world_hash != b->world_hash || a->events != b->events || a->pcm_nonzero != b->pcm_nonzero)
        fail(r, C_CYCLE_PARITY, "second cycle differs from the first");
    for (uint32_t c = 0; c < 2; ++c)
        if (!r->stats[c].pcm_nonzero || !r->stats[c].audible_moving_updates)
            fail(r, C_OUTPUT, "cycle %u produced no output alongside carried movement", c);
}

/* ---- JSON ------------------------------------------------------------------------------ */

static void print_vec(const float v[3])
{
    printf("[%.6f, %.6f, %.6f]", (double)v[0], (double)v[1], (double)v[2]);
}

static void emit_cycle(const CycleStats *s)
{
    printf("{\"frames\": %u, \"updates\": %u, \"events\": %u, \"accepted\": %u, \"rejected\": %u, "
           "\"events_by_cue\": {\"ambient\": %u, \"chime\": %u, \"step\": %u, \"bump\": %u}, "
           "\"decisions\": %u, \"key_on\": %u, \"key_off\": %u, \"steal\": %u, \"drop\": %u, "
           "\"complete\": %u, \"cancel\": %u, \"lost\": %u, \"stage_fail\": %u, \"ring_records\": %u, "
           "\"spu_writes\": %u, \"pcm_nonzero_frames\": %u, \"audible_moving_updates\": %u, "
           "\"pcm_fnv1a64\": \"%016llx\", \"spu_fnv1a64\": \"%016llx\", \"decision_fnv1a64\": \"%016llx\", "
           "\"world_fnv1a64\": \"%016llx\", \"iop_allocs\": %u, \"iop_frees\": %u, ",
           s->frames, s->updates, s->events, s->accepted, s->rejected, s->by_cue[0], s->by_cue[1],
           s->by_cue[2], s->by_cue[3], s->decisions, s->key_on, s->key_off, s->steal, s->drop, s->complete,
           s->cancel, s->lost, s->stage_fail, s->ring_records, s->spu_writes, s->pcm_nonzero,
           s->audible_moving_updates, (unsigned long long)s->pcm_hash, (unsigned long long)s->spu_hash,
           (unsigned long long)s->decision_hash, (unsigned long long)s->world_hash, s->iop_allocs, s->iop_frees);
    printf("\"end_state\": {\"active_requests\": %u, \"owned_voices\": %u, \"busy_commands\": %u, "
           "\"open_channels\": %u, \"sounding_envelopes\": %u, \"bank_slot_state\": %u, \"actors\": %u, "
           "\"free_nodes\": %u, \"dynamic_handles\": %u, \"unknown_calls\": %u}, \"checkpoints\": {",
           s->end_active_requests, s->end_owned_voices, s->end_busy_commands, s->end_open_channels,
           s->end_envelopes, s->end_slot_state, s->end_actors, s->end_free_nodes, s->end_handles,
           s->end_unknown_calls);
    for (uint32_t k = 0; k < CP_COUNT; ++k) {
        const Checkpoint *c = &s->cp[k];
        printf("%s\"%s\": {\"frame\": %u, \"player_pos\": ", k ? ", " : "", cp_name[k], c->frame);
        print_vec(c->pos);
        printf(", \"camera_eye\": ");
        print_vec(c->eye);
        printf(", \"camera_target\": ");
        print_vec(c->target);
        printf(", \"yaw\": %.6f, \"speed\": %.6f, \"main_actors\": %u, \"type2_actors\": %u, "
               "\"free_nodes\": %u, \"wall_contacts\": %u, \"masked_ee_fnv1a64\": \"%016llx\"}",
               (double)c->yaw, (double)c->speed, c->actors_main, c->actors_type2, c->free_nodes,
               c->wall_contacts, (unsigned long long)c->masked_ee);
    }
    printf("}}");
}

static void emit_failures(const Rig *r)
{
    printf("[");
    for (uint32_t i = 0; i < r->fail_count; ++i)
        printf("%s\"%s\"", i ? ", " : "", check_name[r->fail[i]]);
    printf("]");
}

static bool has(const Rig *r, uint32_t check)
{
    for (uint32_t i = 0; i < r->fail_count; ++i)
        if (r->fail[i] == check)
            return true;
    return false;
}

typedef struct { const char *name; Faults f; uint32_t expect; } Control;
typedef struct { bool detected; uint32_t fail[MAX_FAIL], fail_count; char detail[320]; } ControlResult;

static void emit_fail_list(const uint32_t *fails, uint32_t n)
{
    printf("[");
    for (uint32_t i = 0; i < n; ++i)
        printf("%s\"%s\"", i ? ", " : "", check_name[fails[i]]);
    printf("]");
}

int main(int argc, char **argv)
{
    bool json = argc > 1 && strcmp(argv[1], "--json") == 0;
    Rig *standalone = run_scenario(false, NO_FAULTS);
    if (standalone->fail_count) {
        fprintf(stderr, "standalone world run failed: %s\n", standalone->detail);
        return 2;
    }
    Rig *a = run_scenario(true, NO_FAULTS);
    compare(a, standalone);
    Rig *b = run_scenario(true, NO_FAULTS);
    compare(b, standalone);
    bool deterministic = true;
    for (uint32_t c = 0; c < 2; ++c)
        deterministic &= a->stats[c].decision_hash == b->stats[c].decision_hash &&
                         a->stats[c].pcm_hash == b->stats[c].pcm_hash &&
                         a->stats[c].spu_hash == b->stats[c].spu_hash &&
                         a->stats[c].world_hash == b->stats[c].world_hash;
    bool positive = a->fail_count == 0 && b->fail_count == 0 && deterministic;
    rig_destroy(b);

    Faults f;
    Control controls[10];
    f = NO_FAULTS; f.omit_state0_init = true;
    controls[0] = (Control){"nc-missing-world-init", f, C_WORLD_INIT};
    f = NO_FAULTS; f.skip_bank_registration = true;
    controls[1] = (Control){"nc-missing-bank-registration", f, C_AUDIO_INIT};
    f = NO_FAULTS; f.zero_ticks_from = 80; f.zero_ticks_until = 83;
    controls[2] = (Control){"nc-missing-sequence-ticks", f, C_TIMELINE};
    f = NO_FAULTS; f.flush_before_update = true;
    controls[3] = (Control){"nc-flush-before-sequence-update", f, C_KEY_ON};
    f = NO_FAULTS; f.early_bank_release_frame = 100;
    controls[4] = (Control){"nc-early-bank-release", f, C_TEARDOWN_ORDER};
    f = NO_FAULTS; f.skip_audio_teardown = true;
    controls[5] = (Control){"nc-stale-audio-teardown", f, C_AUDIO_END};
    f = NO_FAULTS; f.skip_world_pool_reset = true;
    controls[6] = (Control){"nc-stale-world-teardown", f, C_WORLD_REENTRY};
    f = NO_FAULTS; f.foreign_key_off_frame = 90;
    controls[7] = (Control){"nc-foreign-voice-release", f, C_KEY_OFF};
    f = NO_FAULTS; f.audio_stray_write_frame = 95;
    controls[8] = (Control){"nc-audio-writes-world-memory", f, C_MEM_OWNERSHIP};
    f = NO_FAULTS; f.world_stray_write_frame = 95;
    controls[9] = (Control){"nc-world-writes-audio-memory", f, C_AUDIO_MEMORY};
    ControlResult results[10];
    bool controls_ok = true;
    for (int i = 0; i < 10; ++i) {
        Rig *r = run_scenario(true, controls[i].f);
        compare(r, standalone);
        results[i].detected = has(r, controls[i].expect);
        results[i].fail_count = r->fail_count;
        memcpy(results[i].fail, r->fail, sizeof(r->fail));
        memcpy(results[i].detail, r->detail, sizeof(r->detail));
        controls_ok &= results[i].detected;
        rig_destroy(r);
    }

    if (json) {
        size_t audio_bytes = 0;
        for (size_t i = 0; i < AUDIO_RANGE_COUNT; ++i)
            audio_bytes += AUDIO_RANGES[i].end - AUDIO_RANGES[i].start;
        printf("{\n  \"schema\": \"hg-world-audio-lifecycle/v1\",\n  \"synthetic_only\": true,\n"
               "  \"retail_equivalence_claimed\": false,\n");
        printf("  \"schedule\": {\"sequencer_ticks_per_world_frame\": 1, \"rendered_frames_per_update\": %u, "
               "\"sample_rate\": 48000, \"update_order\": [\"world-step\", \"audio-shadow-check\", "
               "\"derive-events\", \"hg_audio_play\", \"hg_seq_update\", \"hg_audio_ee_flush\", "
               "\"hg_audio_iop_step_dma\", \"checker\", \"hg_audio_iop_render\"], \"source\": \"external-harness\"},\n",
               FRAMES);
        printf("  \"voice_pool\": [8, 3],\n  \"drain_frames\": %u,\n  \"bank_fnv1a64\": \"%016llx\",\n"
               "  \"audio_owned_bytes\": %zu,\n  \"audio_ranges\": %zu,\n", DRAIN_FRAMES,
               (unsigned long long)g_bank_hash, audio_bytes, AUDIO_RANGE_COUNT);
        printf("  \"cue_lengths\": {\"ambient\": 0, \"chime\": %u, \"step\": %u, \"bump\": %u},\n",
               CUE_LENGTH[CUE_CHIME], CUE_LENGTH[CUE_STEP], CUE_LENGTH[CUE_BUMP]);
        printf("  \"standalone\": {\"cycles\": [");
        emit_cycle(&standalone->stats[0]);
        printf(", ");
        emit_cycle(&standalone->stats[1]);
        printf("]},\n  \"connected\": {\"cycles\": [");
        emit_cycle(&a->stats[0]);
        printf(", ");
        emit_cycle(&a->stats[1]);
        printf("], \"failures\": ");
        emit_failures(a);
        printf(", \"detail\": \"%s\", \"stray_audio_writes\": %u},\n", a->detail, a->stray_audio_writes);
        printf("  \"repeat_run_identical\": %s,\n  \"positive_passed\": %s,\n  \"controls\": [",
               deterministic ? "true" : "false", positive ? "true" : "false");
        for (int i = 0; i < 10; ++i) {
            printf("%s\n    {\"name\": \"%s\", \"harness_completed\": true, \"expected_failure\": \"%s\", "
                   "\"detected\": %s, \"failures\": ", i ? "," : "", controls[i].name,
                   check_name[controls[i].expect], results[i].detected ? "true" : "false");
            emit_fail_list(results[i].fail, results[i].fail_count);
            printf(", \"detail\": \"%s\"}", results[i].detail);
        }
        printf("\n  ],\n  \"ok\": %s\n}\n", positive && controls_ok ? "true" : "false");
    } else {
        printf("positive=%s deterministic=%s controls=%s\n", positive ? "pass" : "FAIL",
               deterministic ? "yes" : "no", controls_ok ? "pass" : "FAIL");
        if (a->fail_count)
            printf("first failure: %s\n", a->detail);
        for (int i = 0; i < 10; ++i)
            if (!results[i].detected)
                printf("control %s missed: %s\n", controls[i].name, results[i].detail);
    }
    rig_destroy(a);
    rig_destroy(standalone);
    return positive && controls_ok ? 0 : 1;
}
