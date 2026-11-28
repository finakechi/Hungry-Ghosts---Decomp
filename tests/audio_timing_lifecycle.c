/* M26 connected audio timing and voice management lifecycle driver.
 *
 * Exercises the full connected original lifecycle on the existing M22/M24
 * audio stack (src/audio/{audio_engine,audio_iop,audio_sequencer}.c):
 *
 *   Bank registration -> play request -> scheduled event consumption ->
 *   voice allocation and contention -> ordered service operations ->
 *   release or cancellation -> retirement -> teardown -> clean repeat.
 *
 * Seven positive scenario families (with 3 schedules for multi-schedule
 * families, 2 full cycles per scenario, and repeat-run determinism):
 *   1. s1-short-timed-cue          (3 schedules, 2 cycles)
 *   2. s2-overlapping-owners       (3 schedules, 2 cycles, multi-track + retrigger)
 *   3. s3-exhaustion-contention    (3 schedules, 2 cycles, steal + refuse + round-robin)
 *   4. s4-pending-active-cancel    (3 schedules, 2 cycles, pending + active cancel)
 *   5. s5-release-retire-reuse     (3 schedules, 2 cycles, >32 requests wrap + hold)
 *   6. s6-bank-replace-reset-rebind(2 cycles, Bank A -> Bank B replace + reset/rebind)
 *   7. s7-irregular-bounds-malformed (2 cycles, irregular batch + malformed + overflow)
 *
 * Ten targeted negative controls (each runs to completion and must fail its
 * intended independent check safely):
 *   1. nc-dropped-timing
 *   2. nc-reordered-events
 *   3. nc-missing-channel-consumption
 *   4. nc-ownership-violation
 *   5. nc-missing-key-off
 *   6. nc-premature-reuse
 *   7. nc-bad-cancel-retirement
 *   8. nc-bad-bank-retirement
 *   9. nc-stale-reset
 *  10. nc-service-order-loss
 *
 * Synthetic only: uses independently authored cues and ADPCM samples. No
 * retail equivalence is claimed.
 */
#include "audio/audio_sequencer.h"

#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum {
    EE_RAM_SIZE = 0x2000000u,
    IOP_WINDOW = 0x40000000u,
    IOP_HEAP = 0x4000u,
    BANK_A_ADDR = 0x01000000u,
    BANK_B_ADDR = 0x01010000u,
    BANK_OFF = 0x30u,
    BANK_SIZE = 0x900u,
    PAYLOAD_OFF = BANK_OFF + BANK_SIZE,
    PAYLOAD_SIZE = 0x40u,
    SPU_DEST_A = 0x5010u,
    SPU_DEST_B = 0x6010u,
    T4_OFF = 0x90u,
    CUE_AREA = 0x100u,
    CUE_STRIDE = 0x80u,
    MAX_CUES = 14u,
    FRAMES_PER_TICK = 800u, /* synthetic 48 kHz / 60 Hz model render ratio */
    MAX_WRITES = 16384u,
    MAX_RING_SNAP = 512u,
    MAX_FAIL = 24u,
};

enum Check {
    C_KEY_ON = 1,
    C_KEY_OFF,
    C_SSA,
    C_ENVX_UNOWNED,
    C_ENVX_SILENT,
    C_OWNERSHIP,
    C_MODEL_MASK,
    C_TIMELINE,
    C_EVENT_ORDER,
    C_CHANNEL_CONSUMPTION,
    C_PREMATURE_REUSE,
    C_RETIREMENT,
    C_BANK_RETIREMENT,
    C_RESET_CLEANUP,
    C_SERVICE_ORDER,
    C_END_STATE,
    C_SCHEDULE_PARITY,
    C_CYCLE_PARITY,
    C_DETERMINISM,
};

static const char *const CHECK_NAME[] = {
    "",
    "key-on",
    "key-off",
    "start-address",
    "envx-unowned",
    "envx-silent",
    "ownership",
    "model-mask",
    "timeline",
    "event-order",
    "channel-consumption",
    "premature-reuse",
    "retirement",
    "bank-retirement",
    "reset-cleanup",
    "service-order",
    "end-state",
    "schedule-parity",
    "cycle-parity",
    "determinism",
};

enum ShadowState { V_IDLE = 0, V_ACTIVE = 1, V_RELEASING = 2 };

typedef struct { uint32_t address; uint16_t value; } SpuWrite;

typedef struct Faults {
    bool drop_timing;            /* pass 0 ticks on update index 2 */
    bool reorder_events;         /* swap two decisions' due_tick after run */
    bool corrupt_cancel_marker;  /* clobber ff 41 f7 in guest channel ring before cancel */
    bool mutate_ownership;       /* relabel a key-off decision owner before audit */
    bool filter_key_off;         /* strip KEY_OFF packets from EE ring before flush */
    bool inject_premature_reuse; /* mutate a VOICE_RETIRE / KEY_ON pair to overlap hold */
    bool dirty_cancel_retire;    /* restore guest HG_CMD_STATE=2 after cancel retirement */
    bool bypass_bank_retire;     /* force-stage a key-on after slot clear */
    bool skip_reset_cleanup;     /* skip hg_seq_reset on cycle teardown */
    bool swap_service_order;     /* swap KEY_ON ahead of START_ADDRESS in EE ring */
} Faults;

static const Faults NO_FAULTS = {0};

typedef struct CycleSummary {
    uint32_t updates;
    uint32_t flushes;
    uint32_t decisions;
    uint32_t key_on;
    uint32_t key_off;
    uint32_t voice_retire;
    uint32_t steal;
    uint32_t drop;
    uint32_t reject;
    uint32_t stage_fail;
    uint32_t cancel_active;
    uint32_t cancel_pending;
    uint32_t lost;
    uint32_t ring_records;
    uint32_t spu_writes;
    uint32_t channel_bytes;
    uint32_t pcm_nonzero;
    uint64_t render_frames;
    uint64_t elapsed_ticks;
    uint64_t logical_hash;
    uint64_t pcm_hash;
    uint32_t allocs;
    uint32_t frees;
    bool clean_end;
} CycleSummary;

typedef struct Rig {
    uint8_t *ram;
    uint8_t heap[IOP_HEAP];
    size_t alloc;
    unsigned allocs, frees;
    bool dma_active;
    uint32_t dma_src, dma_dest, dma_size, dma_handle;
    uint32_t tick_source;
    uint32_t active_spu_dest;
    uint32_t active_bank_addr;

    HGAudioBus bus;
    HGAudioIop *iop;
    HGAudioEeService ee;
    HGSequencer *seq;

    SpuWrite *writes;
    uint32_t write_count;
    uint32_t total_spu_writes;
    HGSg2Packet ring_snap[MAX_RING_SNAP];
    uint32_t ring_snap_count;
    HGSg2Reply reply;

    uint64_t pcm_hash;
    uint32_t pcm_nonzero, updates, flushes, ring_records;
    bool last_window_silent;

    Faults faults;
    bool did_swap_order;
    bool did_dirty_cancel;

    /* Independent checker shadow state. */
    uint32_t checked;
    uint64_t last_due_tick;
    uint32_t last_order_seq;
    bool have_decision;
    struct {
        uint32_t state;
        uint32_t request, track, note, tone;
        uint32_t spu_base;
        uint64_t release_done_tick;
    } owner[HG_SG2_VOICES];
    uint32_t looping_tones;
    uint32_t fail[MAX_FAIL], fail_count;
    char detail[256];
} Rig;

/* ---- FNV-1a 64 helper -------------------------------------------------- */

static uint64_t fnv_init(void) { return 1469598103934665603ull; }
static uint64_t fnv_u32(uint64_t h, uint32_t v)
{
    for (int i = 0; i < 4; ++i)
        h = (h ^ ((v >> (8 * i)) & 0xffu)) * 1099511628211ull;
    return h;
}
static uint64_t fnv_u64(uint64_t h, uint64_t v)
{
    for (int i = 0; i < 8; ++i)
        h = (h ^ ((v >> (8 * i)) & 0xffu)) * 1099511628211ull;
    return h;
}

/* ---- guest memory & bus ------------------------------------------------ */

static uint32_t ld(Rig *r, uint32_t a, unsigned w)
{
    assert(a <= EE_RAM_SIZE - w);
    uint32_t v = 0;
    for (unsigned i = 0; i < w; ++i)
        v |= (uint32_t)r->ram[a + i] << (8 * i);
    return v;
}

static void st(Rig *r, uint32_t a, unsigned w, uint32_t v)
{
    assert(a <= EE_RAM_SIZE - w);
    for (unsigned i = 0; i < w; ++i)
        r->ram[a + i] = (uint8_t)(v >> (8 * i));
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
    assert(r->write_count < MAX_WRITES);
    r->writes[r->write_count++] = (SpuWrite){address, value};
    r->total_spu_writes++;
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
        assert(a0 == IOP_WINDOW && r->alloc && !r->dma_active);
        r->alloc = 0;
        r->frees++;
        return 0;
    case HG_X_SIF_SET_DMA:
        assert(a1 == 1 && !r->dma_active);
        r->dma_src = ld(r, a0, 4);
        r->dma_dest = ld(r, a0 + 4, 4);
        r->dma_size = ld(r, a0 + 8, 4);
        r->dma_active = true;
        return ++r->dma_handle;
    case HG_X_SIF_DMA_STAT:
        if (r->dma_active && a0 == r->dma_handle) {
            uint32_t off = r->dma_dest - IOP_WINDOW;
            assert(r->dma_dest >= IOP_WINDOW && off + r->dma_size <= r->alloc);
            memcpy(r->heap + off, r->ram + r->dma_src, r->dma_size);
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
        fprintf(stderr, "unexpected bus call %08x\n", target);
        abort();
    }
}

/* ---- synthetic HSQ1 cues and banks ------------------------------------- */

typedef struct { uint8_t b[CUE_STRIDE]; uint32_t n; } Blob;

static void put8(Blob *x, uint32_t v) { assert(x->n < CUE_STRIDE); x->b[x->n++] = (uint8_t)v; }
static void put16(Blob *x, uint32_t v) { put8(x, v & 0xffu); put8(x, v >> 8); }

typedef struct { uint16_t offset; uint8_t root; int8_t fine; uint8_t volume, pan; } ToneSpec;

static const ToneSpec TONES[3] = {
    {0x00, 60, 0, 127, 64},
    {0x10, 60, 0, 100, 32},
    {0x30, 60, 0, 127, 96},
};
#define LOOPING_TONES 0x3u

static void hsq_begin(Blob *x, uint32_t tracks, uint32_t priority)
{
    memset(x, 0, sizeof(*x));
    put16(x, 0x5348); put16(x, 0x3151); /* "HSQ1" */
    put8(x, HG_SEQ_VERSION); put8(x, tracks); put8(x, 3); put8(x, priority);
    put16(x, HG_SEQ_HEADER_SIZE);
    put16(x, 0);
    for (int i = 0; i < 4; ++i) put16(x, 0);
    for (int t = 0; t < 3; ++t) {
        put16(x, TONES[t].offset); put8(x, TONES[t].root); put8(x, (uint8_t)TONES[t].fine);
        put16(x, 0x00ff); put16(x, 0x1fc0); put16(x, 0x116b);
        put8(x, TONES[t].volume); put8(x, TONES[t].pan);
    }
}
static void hsq_track(Blob *x, uint32_t t) { x->b[12 + 2 * t] = (uint8_t)x->n; x->b[13 + 2 * t] = 0; }
static void hsq_end(Blob *x) { x->b[10] = (uint8_t)x->n; x->b[11] = (uint8_t)(x->n >> 8); }
static void ev_wait(Blob *x, uint32_t n) { put8(x, HG_SEQ_OP_WAIT); put16(x, n); }
static void ev_on(Blob *x, uint32_t note, uint32_t vel, uint32_t tone) { put8(x, HG_SEQ_OP_NOTE_ON); put8(x, note); put8(x, vel); put8(x, tone); }
static void ev_off(Blob *x, uint32_t note) { put8(x, HG_SEQ_OP_NOTE_OFF); put8(x, note); }
static void ev_note(Blob *x, uint32_t note, uint32_t vel, uint32_t tone, uint32_t dur) { put8(x, HG_SEQ_OP_NOTE); put8(x, note); put8(x, vel); put8(x, tone); put16(x, dur); }
static void ev_vol(Blob *x, uint32_t v) { put8(x, HG_SEQ_OP_VOLUME); put8(x, v); }
static void ev_bend(Blob *x, uint32_t v) { put8(x, HG_SEQ_OP_BEND); put8(x, v); }
static void ev_end(Blob *x) { put8(x, HG_SEQ_OP_END); }

enum {
    CUE_SHORT_TIMED = 0,
    CUE_OVERLAP_2TRK,
    CUE_RETRIGGER,
    CUE_CHORD_LOW,
    CUE_LEAD_HIGH,
    CUE_BLIP_LOWEST,
    CUE_HOLD,
    CUE_PULSE,
    CUE_BAD_MAGIC,
    CUE_BAD_TRACK,
    CUE_BAD_OPCODE,
    CUE_RUNAWAY,
    CUE_BAD_PITCH,
    CUE_BAD_TONE_ALIGN,
};

static void build_cues(Blob cue[MAX_CUES])
{
    Blob *x;
    /* 0: short timed cue with duration note, volume, bend, second note, explicit off */
    x = &cue[CUE_SHORT_TIMED]; hsq_begin(x, 1, 64); hsq_track(x, 0);
    ev_note(x, 60, 120, 0, 4); ev_wait(x, 2); ev_vol(x, 72);
    ev_wait(x, 1); ev_bend(x, 96); ev_wait(x, 3);
    ev_on(x, 67, 100, 1); ev_wait(x, 2); ev_off(x, 67); ev_end(x); hsq_end(x);

    /* 1: overlapping 2-track cue */
    x = &cue[CUE_OVERLAP_2TRK]; hsq_begin(x, 2, 64);
    hsq_track(x, 0); ev_on(x, 60, 100, 0); ev_wait(x, 4); ev_off(x, 60); ev_end(x);
    hsq_track(x, 1); ev_wait(x, 2); ev_on(x, 64, 100, 1); ev_wait(x, 3); ev_off(x, 64); ev_end(x);
    hsq_end(x);

    /* 2: repeated note on same track (for retrigger policy test) */
    x = &cue[CUE_RETRIGGER]; hsq_begin(x, 1, 64); hsq_track(x, 0);
    ev_on(x, 62, 100, 0); ev_wait(x, 2); ev_on(x, 62, 110, 1); ev_wait(x, 3); ev_off(x, 62); ev_end(x); hsq_end(x);

    /* 3: low-priority 3-note chord */
    x = &cue[CUE_CHORD_LOW]; hsq_begin(x, 1, 32); hsq_track(x, 0);
    ev_on(x, 60, 80, 0); ev_on(x, 64, 80, 0); ev_on(x, 67, 80, 1); ev_wait(x, 10); ev_end(x); hsq_end(x);

    /* 4: high-priority lead */
    x = &cue[CUE_LEAD_HIGH]; hsq_begin(x, 1, 96); hsq_track(x, 0);
    ev_on(x, 72, 120, 0); ev_wait(x, 2); ev_end(x); hsq_end(x);

    /* 5: lowest-priority blip */
    x = &cue[CUE_BLIP_LOWEST]; hsq_begin(x, 1, 16); hsq_track(x, 0);
    ev_on(x, 48, 70, 0); ev_wait(x, 1); ev_end(x); hsq_end(x);

    /* 6: long held note */
    x = &cue[CUE_HOLD]; hsq_begin(x, 1, 64); hsq_track(x, 0);
    ev_on(x, 60, 100, 0); ev_wait(x, 1000); ev_end(x); hsq_end(x);

    /* 7: brief 3-tick pulse (long enough to render audible PCM under coarse-2 too) */
    x = &cue[CUE_PULSE]; hsq_begin(x, 1, 64); hsq_track(x, 0);
    ev_on(x, 72, 100, 0); ev_wait(x, 3); ev_off(x, 72); ev_end(x); hsq_end(x);

    /* 8..13: malformed cues */
    x = &cue[CUE_BAD_MAGIC]; hsq_begin(x, 1, 64); hsq_track(x, 0); ev_end(x); hsq_end(x);
    x->b[0] = 'Z';

    x = &cue[CUE_BAD_TRACK]; hsq_begin(x, 1, 64); hsq_track(x, 0); ev_end(x); hsq_end(x);
    x->b[12] = 0xf0;

    x = &cue[CUE_BAD_OPCODE]; hsq_begin(x, 1, 64); hsq_track(x, 0);
    ev_on(x, 60, 100, 0); ev_wait(x, 1); put8(x, 0x7e); hsq_end(x);

    x = &cue[CUE_RUNAWAY]; hsq_begin(x, 1, 64); hsq_track(x, 0);
    for (int i = 0; i < 12; ++i) ev_vol(x, 100);
    ev_end(x); hsq_end(x);

    x = &cue[CUE_BAD_PITCH]; hsq_begin(x, 1, 64); hsq_track(x, 0);
    ev_on(x, 120, 100, 0); ev_wait(x, 1); ev_on(x, 62, 100, 3); ev_end(x); hsq_end(x);

    x = &cue[CUE_BAD_TONE_ALIGN]; hsq_begin(x, 1, 64); hsq_track(x, 0); ev_end(x); hsq_end(x);
    x->b[HG_SEQ_HEADER_SIZE] = 0x04; /* misaligned tone 0 sample offset */
}

static void w16le(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static void w32le(uint8_t *p, uint32_t v) { for (int i = 0; i < 4; ++i) p[i] = (uint8_t)(v >> (8 * i)); }

static void build_bank_at(Rig *r, uint32_t addr, uint8_t pattern_xor)
{
    uint8_t *res = r->ram + addr, *bank = res + BANK_OFF, *t4 = bank + T4_OFF;
    memset(res, 0, PAYLOAD_OFF + PAYLOAD_SIZE);
    w32le(res + 0x00, PAYLOAD_OFF + PAYLOAD_SIZE);
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
    w16le(t4 + 0x10, MAX_CUES - 1);
    Blob cue[MAX_CUES];
    build_cues(cue);
    for (uint32_t i = 0; i < MAX_CUES; ++i) {
        uint32_t at = CUE_AREA + i * CUE_STRIDE;
        w16le(t4 + 0x12 + 2 * i, (uint16_t)at);
        memcpy(t4 + at, cue[i].b, cue[i].n);
    }
    assert(T4_OFF + CUE_AREA + MAX_CUES * CUE_STRIDE <= BANK_SIZE);
    uint8_t *pcm = res + PAYLOAD_OFF;
    pcm[0x00] = 0x02; pcm[0x01] = 0x07; memset(pcm + 0x02, 0x77 ^ pattern_xor, 7); memset(pcm + 0x09, 0x99 ^ pattern_xor, 7);
    pcm[0x10] = 0x02; pcm[0x11] = 0x04; memset(pcm + 0x12, 0x33 ^ pattern_xor, 14);
    pcm[0x20] = 0x02; pcm[0x21] = 0x03; memset(pcm + 0x22, 0xdd ^ pattern_xor, 14);
    pcm[0x30] = 0x02; pcm[0x31] = 0x01; memset(pcm + 0x32, 0x55 ^ pattern_xor, 14);
}

static uint32_t register_bank_at(Rig *r, uint32_t addr, uint32_t spu_dest)
{
    st(r, HG_AUDIO_CTX_SAVE, 4, spu_dest);
    r->active_spu_dest = spu_dest;
    r->active_bank_addr = addr;
    for (unsigned step = 0; step < 32; ++step) {
        if (hg_audio_register_entry(&r->bus, addr))
            return ld(r, HG_AUDIO_ID_TABLE, 4);
        assert(hg_audio_ee_flush(&r->ee, &r->reply));
        if (r->reply.upload_busy) {
            assert(hg_audio_iop_step_dma(r->iop));
            assert(hg_audio_ee_flush(&r->ee, &r->reply));
        }
    }
    abort();
}

static void service_up(Rig *r)
{
    HGSpu2Io io = {r, iop_read, spu_write, NULL, NULL};
    r->iop = hg_audio_iop_create(io);
    assert(r->iop);
    hg_audio_ee_service_init(&r->ee, &r->bus, r->iop);
}

static void reset_guest_channels(Rig *r)
{
    for (uint32_t ch = 0; ch < 32; ++ch) {
        st(r, HG_AUDIO_CHANNEL_BASE + ch * HG_CH_STRIDE, 4, UINT32_MAX);
        st(r, HG_AUDIO_CHANNEL_QUEUE + ch * HG_CH_STRIDE + HG_CH_CMD_RING, 4, 0);
    }
    for (uint32_t i = 0; i < 32; ++i)
        memset(r->ram + HG_AUDIO_COMMAND_BASE + i * HG_CMD_STRIDE, 0, HG_CMD_STRIDE);
    st(r, HG_AUDIO_COMMAND_CURSOR, 4, 1);
    st(r, HG_AUDIO_VOICE_CURSOR, 4, 1);
}

static Rig *rig_create(HGSeqConfig cfg, HGSeqTimingConfig timing, Faults faults)
{
    Rig *r = calloc(1, sizeof(*r));
    assert(r);
    r->ram = calloc(EE_RAM_SIZE, 1);
    r->writes = calloc(MAX_WRITES, sizeof(*r->writes));
    r->seq = calloc(1, sizeof(*r->seq));
    assert(r->ram && r->writes && r->seq);
    r->bus = (HGAudioBus){b_r8, b_r16, b_r32, b_w8, b_w16, b_w32, bus_call, r};
    r->looping_tones = LOOPING_TONES;
    r->pcm_hash = fnv_init();
    r->faults = faults;
    service_up(r);
    reset_guest_channels(r);
    hg_audio_slots_init(&r->bus);
    build_bank_at(r, BANK_A_ADDR, 0x00);
    build_bank_at(r, BANK_B_ADDR, 0x11);
    assert(register_bank_at(r, BANK_A_ADDR, SPU_DEST_A) == 0);
    assert(r->allocs == 1 && r->frees == 1);
    assert(hg_seq_init_timed(r->seq, &r->ee, cfg, timing));
    r->write_count = 0;
    r->total_spu_writes = 0;
    return r;
}

static void rig_destroy(Rig *r)
{
    if (!r)
        return;
    hg_audio_iop_destroy(r->iop);
    free(r->seq);
    free(r->writes);
    free(r->ram);
    free(r);
}

/* ---- independent checker ----------------------------------------------- */

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

static bool has_fail(const Rig *r, uint32_t check)
{
    for (uint32_t i = 0; i < r->fail_count; ++i)
        if (r->fail[i] == check)
            return true;
    return false;
}

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

/* Verify EE ring packet ordering:
 *   1. For every KEY_ON of voice v, ADSR, START_ADDRESS, GAIN, PITCH for v
 *      must precede that KEY_ON in order since the previous KEY_ON/KEY_OFF.
 *   2. Expected SSA for each KEY_ON matches the active bank + tone offset.
 *   3. Per-voice KEY_ON / KEY_OFF sequence in the ring matches exact decision order. */
static void check_ring_service_order(Rig *r, uint32_t first_dec, uint32_t end_dec)
{
    const HGSequencer *s = r->seq;
    /* Check per-voice setup-before-key-on invariant in the raw ring snapshot. */
    uint8_t seen_setup[HG_SG2_VOICES] = {0};
    uint32_t ring_ssa[HG_SG2_VOICES] = {0};
    uint32_t ring_events[HG_SG2_VOICES][16];
    uint32_t ring_ev_n[HG_SG2_VOICES] = {0};

    for (uint32_t i = 0; i < r->ring_snap_count; ++i) {
        HGSg2Packet p = r->ring_snap[i];
        if (p.command == HG_SG2_ADSR && p.arg1 < HG_SG2_VOICES) {
            seen_setup[p.arg1] |= 1u;
        } else if (p.command == HG_SG2_START_ADDRESS && p.arg1 < HG_SG2_VOICES) {
            if (!(seen_setup[p.arg1] & 1u))
                fail(r, C_SERVICE_ORDER, "voice %u START_ADDRESS before ADSR", p.arg1);
            seen_setup[p.arg1] |= 2u;
            ring_ssa[p.arg1] = p.arg2;
        } else if (p.command == HG_SG2_GAIN && p.arg1 < HG_SG2_VOICES) {
            seen_setup[p.arg1] |= 4u;
        } else if (p.command == HG_SG2_PITCH && p.arg1 < HG_SG2_VOICES) {
            seen_setup[p.arg1] |= 8u;
        } else if (p.command == HG_SG2_KEY_ON || p.command == HG_SG2_KEY_OFF) {
            bool on = p.command == HG_SG2_KEY_ON;
            for (uint32_t v = 0; v < HG_SG2_VOICES; ++v) {
                uint32_t mask = v < 24 ? p.arg2 : p.arg3;
                if (!(mask & (1u << (v % 24))))
                    continue;
                if (on && (seen_setup[v] & 0x0fu) != 0x0fu)
                    fail(r, C_SERVICE_ORDER, "voice %u KEY_ON staged before ADSR/SSA/GAIN/PITCH", v);
                if (ring_ev_n[v] < 16)
                    ring_events[v][ring_ev_n[v]++] = on ? 1u : 2u;
                seen_setup[v] = 0;
            }
        }
    }

    /* Compare per-voice KEY_ON/KEY_OFF order in decisions vs ring packets. */
    uint32_t dec_events[HG_SG2_VOICES][16];
    uint32_t dec_ev_n[HG_SG2_VOICES] = {0};
    for (uint32_t i = first_dec; i < end_dec; ++i) {
        const HGSeqDecision *d = &s->log[i];
        if (d->kind == HG_SEQ_D_KEY_ON && d->voice < HG_SG2_VOICES) {
            if (dec_ev_n[d->voice] < 16)
                dec_events[d->voice][dec_ev_n[d->voice]++] = 1u;
            uint32_t expect_ssa = r->active_spu_dest + TONES[d->detail].offset;
            if (ring_ssa[d->voice] != expect_ssa)
                fail(r, C_SSA, "voice %u SSA %#x != expected %#x", d->voice,
                     ring_ssa[d->voice], expect_ssa);
        } else if (d->kind == HG_SEQ_D_KEY_OFF && d->voice < HG_SG2_VOICES) {
            if (dec_ev_n[d->voice] < 16)
                dec_events[d->voice][dec_ev_n[d->voice]++] = 2u;
        }
    }
    for (uint32_t v = 0; v < HG_SG2_VOICES; ++v) {
        if (ring_ev_n[v] != dec_ev_n[v])
            continue; /* mismatch already caught by C_KEY_ON / C_KEY_OFF */
        for (uint32_t k = 0; k < dec_ev_n[v]; ++k) {
            if (ring_events[v][k] != dec_events[v][k])
                fail(r, C_SERVICE_ORDER, "voice %u ring key order %u != decision %u",
                     v, ring_events[v][k], dec_events[v][k]);
        }
    }
}

static void check_window(Rig *r)
{
    const HGSequencer *s = r->seq;
    uint32_t first = r->checked;
    uint32_t want_on[HG_SG2_VOICES] = {0}, want_off[HG_SG2_VOICES] = {0};
    uint32_t got_on[HG_SG2_VOICES], got_off[HG_SG2_VOICES];
    uint32_t hold = s->timing.release_hold_ticks;

    check_ring_service_order(r, first, s->log_count);

    for (uint32_t i = first; i < s->log_count; ++i) {
        const HGSeqDecision *d = &s->log[i];
        const HGSeqDecisionTiming *tm = &s->timing_log[i];

        /* Monotonic logical event due time and global order sequence. */
        if (r->have_decision) {
            if (tm->due_tick < r->last_due_tick || tm->order_seq <= r->last_order_seq)
                fail(r, C_EVENT_ORDER, "decision %u out of chronological order (%llu < %llu)",
                     i, (unsigned long long)tm->due_tick, (unsigned long long)r->last_due_tick);
        }
        if (tm->due_tick > tm->service_tick)
            fail(r, C_EVENT_ORDER, "decision %u due_tick %llu > service_tick %llu",
                 i, (unsigned long long)tm->due_tick, (unsigned long long)tm->service_tick);
        r->last_due_tick = tm->due_tick;
        r->last_order_seq = tm->order_seq;
        r->have_decision = true;

        if (d->kind == HG_SEQ_D_KEY_ON) {
            want_on[d->voice]++;
            if (r->owner[d->voice].state == V_RELEASING)
                fail(r, C_PREMATURE_REUSE, "voice %u reused while releasing (due %llu)",
                     d->voice, (unsigned long long)tm->due_tick);
            else if (r->owner[d->voice].state == V_ACTIVE)
                fail(r, C_OWNERSHIP, "voice %u keyed on while already owned (due %llu)",
                     d->voice, (unsigned long long)tm->due_tick);
            r->owner[d->voice].state = V_ACTIVE;
            r->owner[d->voice].request = d->request;
            r->owner[d->voice].track = d->track;
            r->owner[d->voice].note = d->note;
            r->owner[d->voice].tone = d->detail;
            r->owner[d->voice].spu_base = r->active_spu_dest;
            r->owner[d->voice].release_done_tick = 0;
        } else if (d->kind == HG_SEQ_D_KEY_OFF) {
            want_off[d->voice]++;
            if (r->owner[d->voice].state != V_ACTIVE ||
                r->owner[d->voice].request != d->request ||
                r->owner[d->voice].track != d->track ||
                r->owner[d->voice].note != d->note)
                fail(r, C_OWNERSHIP, "voice %u released by non-owner request %u",
                     d->voice, d->request);
            if (hold > 0 && d->detail != HG_SEQ_R_STOLEN && d->detail != HG_SEQ_R_RESET) {
                r->owner[d->voice].state = V_RELEASING;
                r->owner[d->voice].release_done_tick = tm->due_tick + hold;
            } else {
                r->owner[d->voice].state = V_IDLE;
            }
        } else if (d->kind == HG_SEQ_D_VOICE_RETIRE) {
            if (r->owner[d->voice].state != V_RELEASING ||
                r->owner[d->voice].request != d->request)
                fail(r, C_OWNERSHIP, "voice %u retired without releasing owner", d->voice);
            if (tm->due_tick < r->owner[d->voice].release_done_tick &&
                d->detail != HG_SEQ_R_RESET)
                fail(r, C_PREMATURE_REUSE, "voice %u retired early at %llu < %llu",
                     d->voice, (unsigned long long)tm->due_tick,
                     (unsigned long long)r->owner[d->voice].release_done_tick);
            r->owner[d->voice].state = V_IDLE;
        } else if (d->kind == HG_SEQ_D_CANCEL || d->kind == HG_SEQ_D_CANCEL_PENDING) {
            if (d->detail != 1)
                fail(r, C_CHANNEL_CONSUMPTION, "cancel req %u missing ff 41 f7 marker", d->request);
        }

        /* On request retirement, check no voices remain owned/releasing and guest cmd retired. */
        if (d->kind == HG_SEQ_D_COMPLETE || d->kind == HG_SEQ_D_CANCEL ||
            d->kind == HG_SEQ_D_CANCEL_PENDING || d->kind == HG_SEQ_D_LOST ||
            d->kind == HG_SEQ_D_REJECT) {
            for (uint32_t v = 0; v < HG_SG2_VOICES; ++v) {
                if (r->owner[v].state != V_IDLE && r->owner[v].request == d->request)
                    fail(r, C_RETIREMENT, "req %u retired while voice %u still state %u",
                         d->request, v, r->owner[v].state);
            }
            if (d->play_index < 32 && d->kind != HG_SEQ_D_LOST) {
                uint32_t caddr = HG_AUDIO_COMMAND_BASE + d->play_index * HG_CMD_STRIDE;
                uint32_t cstate = ld(r, caddr + HG_CMD_STATE, 4);
                /* Guest slot may have been immediately reused later in the same coarse batch,
                 * so only flag state == 2 (stuck cancel). */
                if (cstate == 2)
                    fail(r, C_RETIREMENT, "cmd %u still state 2 after cancel retirement",
                         d->play_index);
            }
        }
    }
    r->checked = s->log_count;

    spu_key_masks(r, 0x1a0, got_on);
    spu_key_masks(r, 0x1a4, got_off);
    uint32_t model_mask[2] = {0, 0}, rel_mask[2] = {0, 0};
    hg_seq_owned_voices(s, model_mask);
    hg_seq_releasing_voices(s, rel_mask);
    for (uint32_t v = 0; v < HG_SG2_VOICES; ++v) {
        if (got_on[v] != want_on[v])
            fail(r, C_KEY_ON, "voice %u: %u key-on decisions, %u SPU2 key-ons",
                 v, want_on[v], got_on[v]);
        if (got_off[v] != want_off[v])
            fail(r, C_KEY_OFF, "voice %u: %u key-off decisions, %u SPU2 key-offs",
                 v, want_off[v], got_off[v]);
        bool active_owner = r->owner[v].state == V_ACTIVE;
        bool releasing_owner = r->owner[v].state == V_RELEASING;
        bool occupied = active_owner || releasing_owner;
        bool sounding = r->reply.envelope[v] != 0;
        if (sounding && !active_owner)
            fail(r, C_ENVX_UNOWNED, "voice %u sounding without active owner (state %u)",
                 v, r->owner[v].state);
        if (active_owner && !sounding && (r->looping_tones >> r->owner[v].tone & 1u))
            fail(r, C_ENVX_SILENT, "active looping voice %u silent", v);
        bool m_occ = (model_mask[v / 24] >> (v % 24) & 1u) != 0;
        bool m_rel = (rel_mask[v / 24] >> (v % 24) & 1u) != 0;
        if (occupied != m_occ || releasing_owner != m_rel)
            fail(r, C_MODEL_MASK, "voice %u shadow state (%u) disagrees with model (%u/%u)",
                 v, r->owner[v].state, (unsigned)m_occ, (unsigned)m_rel);
    }
}

/* ---- connected service step with optional fault injection -------------- */

static void mutate_ring(Rig *r)
{
    uint32_t count = ld(r, HG_SG2_EE_CURSOR, 4);
    if (r->faults.filter_key_off) {
        uint32_t kept = 0;
        for (uint32_t i = 0; i < count; ++i) {
            uint32_t src = HG_SG2_EE_PACKETS + 16 * i;
            uint32_t dst = HG_SG2_EE_PACKETS + 16 * kept;
            if (ld(r, src, 4) == HG_SG2_KEY_OFF)
                continue;
            for (uint32_t w = 0; w < 16; w += 4)
                st(r, dst + w, 4, ld(r, src + w, 4));
            kept++;
        }
        st(r, HG_SG2_EE_CURSOR, 4, kept);
        count = kept;
    }
    if (r->faults.swap_service_order && !r->did_swap_order && count >= 5) {
        /* Move the first KEY_ON packet ahead of START_ADDRESS (breaks service order!). */
        for (uint32_t i = 1; i < count; ++i) {
            uint32_t p = HG_SG2_EE_PACKETS + 16 * i;
            if (ld(r, p, 4) == HG_SG2_KEY_ON) {
                uint32_t prev = HG_SG2_EE_PACKETS + 16 * (i - 1);
                for (uint32_t w = 0; w < 16; w += 4) {
                    uint32_t a = ld(r, prev + w, 4), b = ld(r, p + w, 4);
                    st(r, prev + w, 4, b);
                    st(r, p + w, 4, a);
                }
                r->did_swap_order = true;
                break;
            }
        }
    }
}

static void snapshot_ring(Rig *r)
{
    uint32_t count = ld(r, HG_SG2_EE_CURSOR, 4);
    r->ring_snap_count = count < MAX_RING_SNAP ? count : MAX_RING_SNAP;
    for (uint32_t i = 0; i < r->ring_snap_count; ++i) {
        uint32_t p = HG_SG2_EE_PACKETS + 16 * i;
        r->ring_snap[i] = (HGSg2Packet){
            ld(r, p, 4), ld(r, p + 4, 4), ld(r, p + 8, 4), ld(r, p + 12, 4)};
    }
}

static void rig_step(Rig *r, uint64_t ticks)
{
    if (r->faults.drop_timing && r->updates >= 3)
        ticks = 0;
    assert(hg_seq_update_u64(r->seq, ticks));
    if (r->faults.dirty_cancel_retire && !r->did_dirty_cancel) {
        for (uint32_t i = r->checked; i < r->seq->log_count; ++i) {
            if (r->seq->log[i].kind == HG_SEQ_D_CANCEL && r->seq->log[i].play_index < 32) {
                uint32_t caddr = HG_AUDIO_COMMAND_BASE + r->seq->log[i].play_index * HG_CMD_STRIDE;
                st(r, caddr + HG_CMD_STATE, 4, 2);
                r->did_dirty_cancel = true;
                break;
            }
        }
    }
    mutate_ring(r);
    snapshot_ring(r);
    uint32_t ring_n = ld(r, HG_SG2_EE_CURSOR, 4);
    r->ring_records += ring_n;
    r->write_count = 0;
    assert(hg_audio_ee_flush(&r->ee, &r->reply));
    hg_seq_record_flush(r->seq, &r->reply, ring_n);
    r->flushes++;
    check_window(r);

    size_t frames = (size_t)(ticks * FRAMES_PER_TICK);
    r->last_window_silent = true;
    if (frames > 0) {
        int16_t pcm[FRAMES_PER_TICK * 2];
        size_t left = frames;
        while (left > 0) {
            size_t chunk = left > FRAMES_PER_TICK ? FRAMES_PER_TICK : left;
            hg_audio_iop_render(r->iop, pcm, chunk);
            for (size_t f = 0; f < chunk; ++f) {
                if (pcm[2 * f] || pcm[2 * f + 1]) {
                    r->pcm_nonzero++;
                    r->last_window_silent = false;
                }
                for (int c = 0; c < 2; ++c) {
                    uint16_t sample = (uint16_t)pcm[2 * f + c];
                    r->pcm_hash = (r->pcm_hash ^ (sample & 0xffu)) * 1099511628211ull;
                    r->pcm_hash = (r->pcm_hash ^ (sample >> 8)) * 1099511628211ull;
                }
            }
            left -= chunk;
        }
        hg_seq_record_render(r->seq, frames);
    }
    r->updates++;
}

static uint32_t play_cue(Rig *r, uint32_t slot, uint32_t cue)
{
    return hg_audio_play(&r->bus, slot, 0, cue, 0, 0);
}

static bool check_end_state(Rig *r)
{
    uint32_t mask[2] = {0, 0}, rel[2] = {0, 0};
    bool ok = hg_seq_active_requests(r->seq) == 0 &&
              hg_seq_owned_voices(r->seq, mask) == 0 &&
              hg_seq_releasing_voices(r->seq, rel) == 0 &&
              r->last_window_silent;
    for (uint32_t i = 0; i < 32; ++i) {
        ok &= ld(r, HG_AUDIO_COMMAND_BASE + i * HG_CMD_STRIDE + HG_CMD_STATE, 4) == 0;
        ok &= ld(r, HG_AUDIO_CHANNEL_BASE + i * HG_CH_STRIDE, 4) == UINT32_MAX;
    }
    for (uint32_t v = 0; v < HG_SG2_VOICES; ++v) {
        ok &= r->reply.envelope[v] == 0;
        ok &= r->owner[v].state == V_IDLE;
    }
    ok &= (r->allocs == r->frees && r->alloc == 0 && !r->dma_active);
    if (!ok)
        fail(r, C_END_STATE, "stale request, voice, command, channel, staging or sound");
    return ok;
}

static CycleSummary summarize_cycle(Rig *r, uint32_t log_start, uint64_t base_tick,
                                    uint32_t base_req, uint32_t start_updates,
                                    uint32_t start_flushes, uint32_t start_ring,
                                    uint32_t start_spu, uint32_t start_chbytes,
                                    uint32_t start_pcm_nz, uint64_t start_render,
                                    uint32_t start_allocs, uint32_t start_frees)
{
    CycleSummary cs;
    memset(&cs, 0, sizeof(cs));
    uint64_t lh = fnv_init();
    for (uint32_t i = log_start; i < r->seq->log_count; ++i) {
        const HGSeqDecision *d = &r->seq->log[i];
        const HGSeqDecisionTiming *tm = &r->seq->timing_log[i];
        if (d->kind == HG_SEQ_D_RESET)
            continue;
        cs.decisions++;
        uint64_t rel_due = tm->due_tick >= base_tick ? tm->due_tick - base_tick : UINT64_MAX;
        uint32_t rel_req = d->request == UINT32_MAX ? UINT32_MAX : d->request - base_req;
        uint32_t rel_det = d->detail;
        if (d->kind == HG_SEQ_D_STEAL && d->detail != UINT32_MAX)
            rel_det = d->detail - base_req;
        lh = fnv_u64(lh, rel_due);
        lh = fnv_u32(lh, d->kind);
        lh = fnv_u32(lh, rel_req);
        lh = fnv_u32(lh, d->track);
        lh = fnv_u32(lh, d->note);
        lh = fnv_u32(lh, d->voice);
        lh = fnv_u32(lh, rel_det);
        switch (d->kind) {
        case HG_SEQ_D_KEY_ON: cs.key_on++; break;
        case HG_SEQ_D_KEY_OFF: cs.key_off++; break;
        case HG_SEQ_D_VOICE_RETIRE: cs.voice_retire++; break;
        case HG_SEQ_D_STEAL: cs.steal++; break;
        case HG_SEQ_D_DROP: cs.drop++; break;
        case HG_SEQ_D_REJECT: cs.reject++; break;
        case HG_SEQ_D_STAGE_FAIL: cs.stage_fail++; break;
        case HG_SEQ_D_CANCEL: cs.cancel_active++; break;
        case HG_SEQ_D_CANCEL_PENDING: cs.cancel_pending++; break;
        case HG_SEQ_D_LOST: cs.lost++; break;
        default: break;
        }
    }
    HGSeqClock clk = hg_seq_clock(r->seq);
    cs.updates = r->updates - start_updates;
    cs.flushes = r->flushes - start_flushes;
    cs.ring_records = r->ring_records - start_ring;
    cs.spu_writes = r->total_spu_writes - start_spu;
    cs.channel_bytes = clk.channel_bytes_consumed - start_chbytes;
    cs.pcm_nonzero = r->pcm_nonzero - start_pcm_nz;
    cs.render_frames = clk.render_frames - start_render;
    cs.elapsed_ticks = clk.elapsed_ticks - base_tick;
    cs.logical_hash = lh;
    cs.pcm_hash = r->pcm_hash;
    cs.allocs = r->allocs - start_allocs;
    cs.frees = r->frees - start_frees;
    cs.clean_end = check_end_state(r);
    return cs;
}

/* ---- schedule engine --------------------------------------------------- */

typedef struct ScheduledAction {
    uint64_t rel_tick;
    enum { ACT_PLAY = 1, ACT_CANCEL_ACTIVE = 2, ACT_CANCEL_PENDING = 3 } kind;
    uint32_t cue;
    uint32_t target_slot_ref; /* index into submitted[] array for cancel */
} ScheduledAction;

typedef struct Schedule {
    const char *name;
    const uint32_t *steps;
    uint32_t count;
} Schedule;

static const uint32_t STEPS_UNIT[] = {
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1
};
static const uint32_t STEPS_COARSE2[] = {
    2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2
};
static const uint32_t STEPS_IRREG_ZERO[] = {
    0, 1, 3, 0, 2, 4, 1, 2, 3, 0, 3, 3
};

static const Schedule SCHEDULES[3] = {
    {"unit-tick", STEPS_UNIT, (uint32_t)(sizeof(STEPS_UNIT) / sizeof(STEPS_UNIT[0]))},
    {"coarse-2", STEPS_COARSE2, (uint32_t)(sizeof(STEPS_COARSE2) / sizeof(STEPS_COARSE2[0]))},
    {"irregular-zero", STEPS_IRREG_ZERO, (uint32_t)(sizeof(STEPS_IRREG_ZERO) / sizeof(STEPS_IRREG_ZERO[0]))},
};

/* Run an authored action list over a declared step schedule from base_tick.
 * Inter-service arrivals and mid-batch cancels are tagged with their exact
 * logical timestamps so all schedules execute the identical logical timeline. */
static void run_authored_schedule(Rig *r, const Schedule *sched,
                                  const ScheduledAction *actions, uint32_t n_actions)
{
    uint64_t base_tick = hg_seq_clock(r->seq).elapsed_ticks;
    uint32_t submitted[32];
    bool submitted_ok[32] = {0};
    bool done[32] = {0};

    uint64_t cur = 0;
    for (uint32_t s = 0; s < sched->count; ++s) {
        uint64_t dt = sched->steps[s];
        uint64_t win_end = cur + dt;

        /* Submit any actions whose logical tick falls in [cur, win_end] (or == 0 at start). */
        for (uint32_t a = 0; a < n_actions; ++a) {
            if (done[a])
                continue;
            if (actions[a].rel_tick > win_end)
                continue;
            uint64_t abs_t = base_tick + actions[a].rel_tick;
            if (actions[a].kind == ACT_PLAY) {
                uint32_t idx = play_cue(r, 0, actions[a].cue);
                assert(idx < 32);
                assert(hg_seq_tag_request(r->seq, idx, abs_t));
                submitted[a] = idx;
                submitted_ok[a] = true;
            } else if (actions[a].kind == ACT_CANCEL_PENDING) {
                uint32_t idx = play_cue(r, 0, actions[a].cue);
                assert(idx < 32);
                assert(hg_audio_cancel(&r->bus, idx) == 0);
                if (r->faults.corrupt_cancel_marker) {
                    uint32_t ch = ld(r, HG_AUDIO_COMMAND_BASE + idx * HG_CMD_STRIDE + HG_CMD_CHANNEL, 4);
                    uint32_t q = HG_AUDIO_CHANNEL_QUEUE + ch * HG_CH_STRIDE;
                    uint32_t w = ld(r, q + HG_CH_CMD_RING, 4);
                    st(r, q + ((w - 2) & 0x1ffu), 1, 0x00);
                }
                submitted[a] = idx;
                submitted_ok[a] = true;
            } else if (actions[a].kind == ACT_CANCEL_ACTIVE) {
                uint32_t ref = actions[a].target_slot_ref;
                assert(ref < n_actions && submitted_ok[ref]);
                uint32_t idx = submitted[ref];
                if (r->faults.corrupt_cancel_marker) {
                    /* Invoke cancel immediately and corrupt its ff 41 f7 byte. */
                    assert(hg_audio_cancel(&r->bus, idx) == 0);
                    uint32_t ch = ld(r, HG_AUDIO_COMMAND_BASE + idx * HG_CMD_STRIDE + HG_CMD_CHANNEL, 4);
                    uint32_t q = HG_AUDIO_CHANNEL_QUEUE + ch * HG_CH_STRIDE;
                    uint32_t w = ld(r, q + HG_CH_CMD_RING, 4);
                    st(r, q + ((w - 2) & 0x1ffu), 1, 0x00);
                } else {
                    assert(hg_seq_tag_cancel(r->seq, idx, abs_t));
                }
            }
            done[a] = true;
        }
        rig_step(r, dt);
        cur = win_end;
    }
}

/* ---- expected logical timeline checker --------------------------------- */

typedef struct { uint64_t rel_due; uint32_t kind, rel_req, note, detail; } Expect;
#define ANY UINT32_MAX

static void check_expected_timeline(Rig *r, uint32_t log_start, uint64_t base_tick,
                                    uint32_t base_req, const Expect *e, uint32_t n,
                                    uint32_t kinds_mask)
{
    uint32_t k = 0;
    for (uint32_t i = log_start; i < r->seq->log_count; ++i) {
        const HGSeqDecision *d = &r->seq->log[i];
        const HGSeqDecisionTiming *tm = &r->seq->timing_log[i];
        if (!(kinds_mask >> d->kind & 1u))
            continue;
        if (k >= n) {
            fail(r, C_TIMELINE, "unexpected decision kind %u at rel_due %llu",
                 d->kind, (unsigned long long)(tm->due_tick - base_tick));
            return;
        }
        uint64_t rel_due = tm->due_tick - base_tick;
        uint32_t rel_req = d->request == UINT32_MAX ? UINT32_MAX : d->request - base_req;
        uint32_t det = d->detail;
        if (d->kind == HG_SEQ_D_STEAL && det != UINT32_MAX)
            det -= base_req;
        if (rel_due != e[k].rel_due || d->kind != e[k].kind ||
            (e[k].rel_req != ANY && rel_req != e[k].rel_req) ||
            (e[k].note != ANY && d->note != e[k].note) ||
            (e[k].detail != ANY && det != e[k].detail)) {
            fail(r, C_TIMELINE, "decision %u: got kind %u due %llu req %u note %u det %u, "
                 "want kind %u due %llu", k, d->kind, (unsigned long long)rel_due,
                 rel_req, d->note, det, e[k].kind, (unsigned long long)e[k].rel_due);
            return;
        }
        k++;
    }
    if (k != n)
        fail(r, C_TIMELINE, "%u of %u expected decisions observed", k, n);
}

#define K(x) (1u << (x))
#define ALL_KINDS (K(HG_SEQ_D_START) | K(HG_SEQ_D_KEY_ON) | K(HG_SEQ_D_KEY_OFF) | \
                   K(HG_SEQ_D_VOICE_RETIRE) | K(HG_SEQ_D_COMPLETE) | K(HG_SEQ_D_CANCEL) | \
                   K(HG_SEQ_D_CANCEL_PENDING) | K(HG_SEQ_D_LOST) | K(HG_SEQ_D_STEAL) | \
                   K(HG_SEQ_D_DROP) | K(HG_SEQ_D_REJECT) | K(HG_SEQ_D_VOLUME) | \
                   K(HG_SEQ_D_BEND) | K(HG_SEQ_D_TRACK_END) | K(HG_SEQ_D_STAGE_FAIL))

/* Full cycle teardown + fresh re-entry for Cycle 2. */
static void cycle_teardown_and_reenter(Rig *r, bool skip_reset)
{
    if (!skip_reset) {
        hg_seq_reset(r->seq);
        r->write_count = 0;
        snapshot_ring(r);
        assert(hg_audio_ee_flush(&r->ee, &r->reply));
        check_window(r);
        if (hg_seq_active_requests(r->seq) != 0 || hg_seq_owned_voices(r->seq, NULL) != 0 ||
            hg_seq_releasing_voices(r->seq, NULL) != 0)
            fail(r, C_RESET_CLEANUP, "reset left active requests or voices");
    } else {
        /* Fault control: leave an active command/voice stale across cycle boundary! */
        uint32_t idx = play_cue(r, 0, CUE_HOLD);
        assert(idx < 32);
        rig_step(r, 1);
    }
    hg_audio_slot_clear(&r->bus, 0);
    hg_audio_iop_destroy(r->iop);
    r->iop = NULL;
    r->ee.iop = NULL;
    if (hg_seq_update(r->seq, 1))
        fail(r, C_RESET_CLEANUP, "unbound service accepted update after destroy");

    service_up(r);
    if (!skip_reset) {
        reset_guest_channels(r);
        hg_audio_slots_init(&r->bus);
        assert(register_bank_at(r, BANK_A_ADDR, SPU_DEST_A) == 0);
        assert(hg_seq_rebind(r->seq, &r->ee));
    } else {
        hg_audio_slots_init(&r->bus);
        assert(register_bank_at(r, BANK_A_ADDR, SPU_DEST_A) == 0);
        r->seq->service = &r->ee; /* bypass hg_seq_rebind/reset to simulate stale state */
        if (hg_seq_active_requests(r->seq) != 0 || hg_seq_owned_voices(r->seq, NULL) != 0)
            fail(r, C_RESET_CLEANUP, "stale requests or voices survived teardown into Cycle 2");
    }
    memset(&r->reply, 0, sizeof(r->reply));
    r->write_count = 0;
    r->pcm_hash = fnv_init();
}

/* ---- Scenario Family 1: Short timed cue (3 schedules, 2 cycles) -------- */

static Rig *run_s1(const Schedule *sched, Faults f, CycleSummary out[2])
{
    HGSeqConfig cfg = {8, 4, 0};
    HGSeqTimingConfig tm = {HG_SEQ_TIMING_CHRONO, HG_SEQ_ALLOC_LOWEST_FREE,
                            HG_SEQ_STEAL_PRIORITY_OLDEST, HG_SEQ_REPEAT_POLY, 2};
    Rig *r = rig_create(cfg, tm, f);
    static const ScheduledAction acts[] = {
        {0, ACT_PLAY, CUE_SHORT_TIMED, 0},
    };
    static const Expect exp[] = {
        {0, HG_SEQ_D_START, 0, ANY, 0},
        {0, HG_SEQ_D_KEY_ON, 0, 60, 0},
        {2, HG_SEQ_D_VOLUME, 0, ANY, 72},
        {3, HG_SEQ_D_BEND, 0, ANY, 96},
        {4, HG_SEQ_D_KEY_OFF, 0, 60, HG_SEQ_R_DURATION},
        {6, HG_SEQ_D_VOICE_RETIRE, 0, 60, HG_SEQ_R_DURATION},
        {6, HG_SEQ_D_KEY_ON, 0, 67, 1},
        {8, HG_SEQ_D_KEY_OFF, 0, 67, HG_SEQ_R_NOTE_OFF},
        {8, HG_SEQ_D_TRACK_END, 0, ANY, 0},
        {10, HG_SEQ_D_VOICE_RETIRE, 0, 67, HG_SEQ_R_NOTE_OFF},
        {10, HG_SEQ_D_COMPLETE, 0, ANY, 0},
    };
    for (int cyc = 0; cyc < 2; ++cyc) {
        if (cyc == 1)
            cycle_teardown_and_reenter(r, f.skip_reset_cleanup);
        uint32_t ls = r->seq->log_count, br = r->seq->next_request;
        uint64_t bt = hg_seq_clock(r->seq).elapsed_ticks;
        uint32_t su = r->updates, sf = r->flushes, sr = r->ring_records;
        uint32_t ss = r->total_spu_writes, sc = hg_seq_clock(r->seq).channel_bytes_consumed;
        uint32_t sp = r->pcm_nonzero, sa = r->allocs, sfr = r->frees;
        uint64_t sren = hg_seq_clock(r->seq).render_frames;

        run_authored_schedule(r, sched, acts, 1);
        check_expected_timeline(r, ls, bt, br, exp, (uint32_t)(sizeof(exp) / sizeof(exp[0])), ALL_KINDS);
        out[cyc] = summarize_cycle(r, ls, bt, br, su, sf, sr, ss, sc, sp, sren, sa, sfr);
    }
    if (out[0].logical_hash != out[1].logical_hash || out[0].pcm_hash != out[1].pcm_hash ||
        out[0].pcm_nonzero != out[1].pcm_nonzero || out[0].spu_writes != out[1].spu_writes)
        fail(r, C_CYCLE_PARITY, "s1 cycle 2 differs from cycle 1");
    return r;
}

/* ---- Scenario Family 2: Overlapping requests, distinct owners, retrigger */

static Rig *run_s2(const Schedule *sched, Faults f, CycleSummary out[2])
{
    HGSeqConfig cfg = {8, 6, 0};
    HGSeqTimingConfig tm = {HG_SEQ_TIMING_CHRONO, HG_SEQ_ALLOC_LOWEST_FREE,
                            HG_SEQ_STEAL_PRIORITY_OLDEST, HG_SEQ_REPEAT_RETRIGGER, 2};
    Rig *r = rig_create(cfg, tm, f);
    static const ScheduledAction acts[] = {
        {0, ACT_PLAY, CUE_OVERLAP_2TRK, 0}, /* req 0: n60@[0..4] (ret 6), n64@[2..5] (ret 7) */
        {1, ACT_PLAY, CUE_OVERLAP_2TRK, 0}, /* req 1: n60@[1..5] (ret 7), n64@[3..6] (ret 8) */
        {2, ACT_PLAY, CUE_RETRIGGER, 0},    /* req 2: n62@2, retrigger n62@4 (old ret 6), off@7 (ret 9) */
    };
    static const Expect exp[] = {
        {0, HG_SEQ_D_START, 0, ANY, 0},
        {0, HG_SEQ_D_KEY_ON, 0, 60, 0},
        {1, HG_SEQ_D_START, 1, ANY, 0},
        {1, HG_SEQ_D_KEY_ON, 1, 60, 0},
        {2, HG_SEQ_D_START, 2, ANY, 0},
        {2, HG_SEQ_D_KEY_ON, 0, 64, 1},
        {2, HG_SEQ_D_KEY_ON, 2, 62, 0},
        {3, HG_SEQ_D_KEY_ON, 1, 64, 1},
        {4, HG_SEQ_D_KEY_OFF, 0, 60, HG_SEQ_R_NOTE_OFF},
        {4, HG_SEQ_D_TRACK_END, 0, ANY, 0},
        {4, HG_SEQ_D_KEY_OFF, 2, 62, HG_SEQ_R_RETRIGGER},
        {4, HG_SEQ_D_KEY_ON, 2, 62, 1},
        {5, HG_SEQ_D_KEY_OFF, 0, 64, HG_SEQ_R_NOTE_OFF},
        {5, HG_SEQ_D_TRACK_END, 0, ANY, 0},
        {5, HG_SEQ_D_KEY_OFF, 1, 60, HG_SEQ_R_NOTE_OFF},
        {5, HG_SEQ_D_TRACK_END, 1, ANY, 0},
        {6, HG_SEQ_D_VOICE_RETIRE, 0, 60, HG_SEQ_R_NOTE_OFF},
        {6, HG_SEQ_D_VOICE_RETIRE, 2, 62, HG_SEQ_R_RETRIGGER},
        {6, HG_SEQ_D_KEY_OFF, 1, 64, HG_SEQ_R_NOTE_OFF},
        {6, HG_SEQ_D_TRACK_END, 1, ANY, 0},
        {7, HG_SEQ_D_VOICE_RETIRE, 1, 60, HG_SEQ_R_NOTE_OFF},
        {7, HG_SEQ_D_VOICE_RETIRE, 0, 64, HG_SEQ_R_NOTE_OFF},
        {7, HG_SEQ_D_KEY_OFF, 2, 62, HG_SEQ_R_NOTE_OFF},
        {7, HG_SEQ_D_TRACK_END, 2, ANY, 0},
        {7, HG_SEQ_D_COMPLETE, 0, ANY, 0},
        {8, HG_SEQ_D_VOICE_RETIRE, 1, 64, HG_SEQ_R_NOTE_OFF},
        {8, HG_SEQ_D_COMPLETE, 1, ANY, 0},
        {9, HG_SEQ_D_VOICE_RETIRE, 2, 62, HG_SEQ_R_NOTE_OFF},
        {9, HG_SEQ_D_COMPLETE, 2, ANY, 0},
    };
    for (int cyc = 0; cyc < 2; ++cyc) {
        if (cyc == 1)
            cycle_teardown_and_reenter(r, f.skip_reset_cleanup);
        uint32_t ls = r->seq->log_count, br = r->seq->next_request;
        uint64_t bt = hg_seq_clock(r->seq).elapsed_ticks;
        uint32_t su = r->updates, sf = r->flushes, sr = r->ring_records;
        uint32_t ss = r->total_spu_writes, sc = hg_seq_clock(r->seq).channel_bytes_consumed;
        uint32_t sp = r->pcm_nonzero, sa = r->allocs, sfr = r->frees;
        uint64_t sren = hg_seq_clock(r->seq).render_frames;

        run_authored_schedule(r, sched, acts, 3);
        check_expected_timeline(r, ls, bt, br, exp, (uint32_t)(sizeof(exp) / sizeof(exp[0])), ALL_KINDS);
        out[cyc] = summarize_cycle(r, ls, bt, br, su, sf, sr, ss, sc, sp, sren, sa, sfr);
    }
    if (out[0].logical_hash != out[1].logical_hash || out[0].pcm_hash != out[1].pcm_hash)
        fail(r, C_CYCLE_PARITY, "s2 cycle 2 differs from cycle 1");
    return r;
}

/* ---- Scenario Family 3: Exhaustion, contention, steal/refusal & reuse -- */

static Rig *run_s3(const Schedule *sched, Faults f, CycleSummary out[2])
{
    HGSeqConfig cfg = {20, 3, 0};
    HGSeqTimingConfig tm = {HG_SEQ_TIMING_CHRONO, HG_SEQ_ALLOC_ROUND_ROBIN,
                            HG_SEQ_STEAL_PRIORITY_OLDEST, HG_SEQ_REPEAT_POLY, 2};
    Rig *r = rig_create(cfg, tm, f);
    /* Timeline:
     *  t=0: req 0 (prio 32) chord -> v20, v21, v22 (ends t=10, retires t=12)
     *  t=1: req 1 (prio 96) lead  -> steals oldest low-prio v20 (ends t=3, enters RELEASING until t=5!)
     *  t=2: req 2 (prio 16) blip  -> dropped (prio < all active)
     *  t=4: req 3 (prio 16) blip  -> STILL DROPPED! v20 is RELEASING until t=5 (no premature reuse!)
     *  t=5: req 4 (prio 16) blip  -> v20 retired at t=5 -> CLEANLY REUSES v20! (ends t=6, retires t=8)
     *  t=13: configure HG_SEQ_STEAL_REFUSE? No, keep 3-schedule actions pure and test refuse in sub-phase! */
    static const ScheduledAction acts[] = {
        {0, ACT_PLAY, CUE_CHORD_LOW, 0},
        {1, ACT_PLAY, CUE_LEAD_HIGH, 0},
        {2, ACT_PLAY, CUE_BLIP_LOWEST, 0},
        {4, ACT_PLAY, CUE_BLIP_LOWEST, 0},
        {5, ACT_PLAY, CUE_BLIP_LOWEST, 0},
    };
    static const Expect exp[] = {
        {0, HG_SEQ_D_START, 0, ANY, 0},
        {0, HG_SEQ_D_KEY_ON, 0, 60, 0},
        {0, HG_SEQ_D_KEY_ON, 0, 64, 0},
        {0, HG_SEQ_D_KEY_ON, 0, 67, 1},
        {1, HG_SEQ_D_START, 1, ANY, 0},
        {1, HG_SEQ_D_KEY_OFF, 0, 60, HG_SEQ_R_STOLEN},
        {1, HG_SEQ_D_STEAL, 1, 72, 0},
        {1, HG_SEQ_D_KEY_ON, 1, 72, 0},
        {2, HG_SEQ_D_START, 2, ANY, 0},
        {2, HG_SEQ_D_DROP, 2, 48, 0},
        {3, HG_SEQ_D_KEY_OFF, 1, 72, HG_SEQ_R_TRACK_END},
        {3, HG_SEQ_D_TRACK_END, 1, ANY, 0},
        {3, HG_SEQ_D_TRACK_END, 2, ANY, 0},
        {3, HG_SEQ_D_COMPLETE, 2, ANY, 0},
        {4, HG_SEQ_D_START, 3, ANY, 0},
        {4, HG_SEQ_D_DROP, 3, 48, 0},
        {5, HG_SEQ_D_VOICE_RETIRE, 1, 72, HG_SEQ_R_TRACK_END},
        {5, HG_SEQ_D_START, 4, ANY, 0},
        {5, HG_SEQ_D_TRACK_END, 3, ANY, 0},
        {5, HG_SEQ_D_COMPLETE, 3, ANY, 0},
        {5, HG_SEQ_D_KEY_ON, 4, 48, 0},
        {5, HG_SEQ_D_COMPLETE, 1, ANY, 0},
        {6, HG_SEQ_D_KEY_OFF, 4, 48, HG_SEQ_R_TRACK_END},
        {6, HG_SEQ_D_TRACK_END, 4, ANY, 0},
        {8, HG_SEQ_D_VOICE_RETIRE, 4, 48, HG_SEQ_R_TRACK_END},
        {8, HG_SEQ_D_COMPLETE, 4, ANY, 0},
        {10, HG_SEQ_D_KEY_OFF, 0, 64, HG_SEQ_R_TRACK_END},
        {10, HG_SEQ_D_KEY_OFF, 0, 67, HG_SEQ_R_TRACK_END},
        {10, HG_SEQ_D_TRACK_END, 0, ANY, 0},
        {12, HG_SEQ_D_VOICE_RETIRE, 0, 64, HG_SEQ_R_TRACK_END},
        {12, HG_SEQ_D_VOICE_RETIRE, 0, 67, HG_SEQ_R_TRACK_END},
        {12, HG_SEQ_D_COMPLETE, 0, ANY, 0},
    };
    for (int cyc = 0; cyc < 2; ++cyc) {
        if (cyc == 1)
            cycle_teardown_and_reenter(r, f.skip_reset_cleanup);
        uint32_t ls = r->seq->log_count, br = r->seq->next_request;
        uint64_t bt = hg_seq_clock(r->seq).elapsed_ticks;
        uint32_t su = r->updates, sf = r->flushes, sr = r->ring_records;
        uint32_t ss = r->total_spu_writes, sc = hg_seq_clock(r->seq).channel_bytes_consumed;
        uint32_t sp = r->pcm_nonzero, sa = r->allocs, sfr = r->frees;
        uint64_t sren = hg_seq_clock(r->seq).render_frames;

        assert(hg_seq_configure_timing(r->seq, tm));
        run_authored_schedule(r, sched, acts, 5);
        check_expected_timeline(r, ls, bt, br, exp, (uint32_t)(sizeof(exp) / sizeof(exp[0])), ALL_KINDS);

        /* Also exercise strict refusal policy (HG_SEQ_STEAL_REFUSE): even a
         * higher-priority lead is refused when the 3-voice pool is full. */
        HGSeqTimingConfig tm_refuse = tm;
        tm_refuse.contention_policy = HG_SEQ_STEAL_REFUSE;
        assert(hg_seq_configure_timing(r->seq, tm_refuse));
        uint64_t bt2 = hg_seq_clock(r->seq).elapsed_ticks;
        uint32_t c_idx = play_cue(r, 0, CUE_CHORD_LOW);
        uint32_t l_idx = play_cue(r, 0, CUE_LEAD_HIGH);
        assert(c_idx < 32 && l_idx < 32);
        assert(hg_seq_tag_request(r->seq, c_idx, bt2));
        assert(hg_seq_tag_request(r->seq, l_idx, bt2 + 1));
        uint32_t drops_before = 0;
        for (uint32_t i = ls; i < r->seq->log_count; ++i)
            drops_before += r->seq->log[i].kind == HG_SEQ_D_DROP;
        rig_step(r, 2);
        uint32_t drops_after = 0;
        for (uint32_t i = ls; i < r->seq->log_count; ++i)
            drops_after += r->seq->log[i].kind == HG_SEQ_D_DROP;
        if (drops_after != drops_before + 1)
            fail(r, C_OWNERSHIP, "HG_SEQ_STEAL_REFUSE did not drop high-priority contender");
        rig_step(r, 12);

        out[cyc] = summarize_cycle(r, ls, bt, br, su, sf, sr, ss, sc, sp, sren, sa, sfr);
    }
    if (out[0].logical_hash != out[1].logical_hash || out[0].pcm_hash != out[1].pcm_hash)
        fail(r, C_CYCLE_PARITY, "s3 cycle 2 differs from cycle 1");
    return r;
}

/* ---- Scenario Family 4: Pending & active cancellation ------------------ */

static Rig *run_s4(const Schedule *sched, Faults f, CycleSummary out[2])
{
    HGSeqConfig cfg = {8, 4, 0};
    HGSeqTimingConfig tm = {HG_SEQ_TIMING_CHRONO, HG_SEQ_ALLOC_ROUND_ROBIN,
                            HG_SEQ_STEAL_PRIORITY_OLDEST, HG_SEQ_REPEAT_POLY, 2};
    Rig *r = rig_create(cfg, tm, f);
    static const ScheduledAction acts[] = {
        {0, ACT_CANCEL_PENDING, CUE_HOLD, 0}, /* req 0: play+cancel before adopt -> CANCEL_PENDING */
        {0, ACT_PLAY, CUE_HOLD, 0},           /* req 1: active hold on v8 */
        {4, ACT_CANCEL_ACTIVE, 0, 1},         /* cancel req 1 at t=4 -> KEY_OFF@4, retire@6 */
        {7, ACT_PLAY, CUE_PULSE, 0},          /* req 2: clean follow-up @[7..9], retire@11 */
    };
    static const Expect exp[] = {
        {0, HG_SEQ_D_CANCEL_PENDING, 0, ANY, 1},
        {0, HG_SEQ_D_START, 1, ANY, 0},
        {0, HG_SEQ_D_KEY_ON, 1, 60, 0},
        {4, HG_SEQ_D_KEY_OFF, 1, 60, HG_SEQ_R_CANCEL},
        {6, HG_SEQ_D_VOICE_RETIRE, 1, 60, HG_SEQ_R_CANCEL},
        {6, HG_SEQ_D_CANCEL, 1, ANY, 1},
        {7, HG_SEQ_D_START, 2, ANY, 0},
        {7, HG_SEQ_D_KEY_ON, 2, 72, 0},
        {10, HG_SEQ_D_KEY_OFF, 2, 72, HG_SEQ_R_NOTE_OFF},
        {10, HG_SEQ_D_TRACK_END, 2, ANY, 0},
        {12, HG_SEQ_D_VOICE_RETIRE, 2, 72, HG_SEQ_R_NOTE_OFF},
        {12, HG_SEQ_D_COMPLETE, 2, ANY, 0},
    };
    for (int cyc = 0; cyc < 2; ++cyc) {
        if (cyc == 1)
            cycle_teardown_and_reenter(r, f.skip_reset_cleanup);
        uint32_t ls = r->seq->log_count, br = r->seq->next_request;
        uint64_t bt = hg_seq_clock(r->seq).elapsed_ticks;
        uint32_t su = r->updates, sf = r->flushes, sr = r->ring_records;
        uint32_t ss = r->total_spu_writes, sc = hg_seq_clock(r->seq).channel_bytes_consumed;
        uint32_t sp = r->pcm_nonzero, sa = r->allocs, sfr = r->frees;
        uint64_t sren = hg_seq_clock(r->seq).render_frames;

        run_authored_schedule(r, sched, acts, 4);
        check_expected_timeline(r, ls, bt, br, exp, (uint32_t)(sizeof(exp) / sizeof(exp[0])), ALL_KINDS);
        out[cyc] = summarize_cycle(r, ls, bt, br, su, sf, sr, ss, sc, sp, sren, sa, sfr);
    }
    if (out[0].logical_hash != out[1].logical_hash || out[0].pcm_hash != out[1].pcm_hash)
        fail(r, C_CYCLE_PARITY, "s4 cycle 2 differs from cycle 1");
    return r;
}

/* ---- Scenario Family 5: Release, retirement & reuse (>32 requests) ----- */

static Rig *run_s5(const Schedule *sched, Faults f, CycleSummary out[2])
{
    HGSeqConfig cfg = {8, 4, 0};
    HGSeqTimingConfig tm = {HG_SEQ_TIMING_CHRONO, HG_SEQ_ALLOC_ROUND_ROBIN,
                            HG_SEQ_STEAL_PRIORITY_OLDEST, HG_SEQ_REPEAT_POLY, 2};
    Rig *r = rig_create(cfg, tm, f);

    for (int cyc = 0; cyc < 2; ++cyc) {
        if (cyc == 1)
            cycle_teardown_and_reenter(r, f.skip_reset_cleanup);
        uint32_t ls = r->seq->log_count, br = r->seq->next_request;
        uint64_t bt = hg_seq_clock(r->seq).elapsed_ticks;
        uint32_t su = r->updates, sf = r->flushes, sr = r->ring_records;
        uint32_t ss = r->total_spu_writes, sc = hg_seq_clock(r->seq).channel_bytes_consumed;
        uint32_t sp = r->pcm_nonzero, sa = r->allocs, sfr = r->frees;
        uint64_t sren = hg_seq_clock(r->seq).render_frames;

        /* 36 sequential pulses (each lasts 3 ticks + 2 ticks release hold = 5 ticks),
         * exceeding the 32 guest command and channel slots! Drive in groups using
         * the schedule's step sizes. */
        uint32_t sidx = 0;
        for (uint32_t req_i = 0; req_i < 36; ++req_i) {
            uint64_t t_req = bt + (uint64_t)req_i * 6u;
            uint32_t idx = play_cue(r, 0, CUE_PULSE);
            if (idx >= 32) {
                fail(r, C_RETIREMENT, "request %u refused after prior retirements", req_i);
                break;
            }
            assert(hg_seq_tag_request(r->seq, idx, t_req));
            uint64_t target = t_req + 6u;
            while (hg_seq_clock(r->seq).elapsed_ticks < target) {
                uint64_t rem = target - hg_seq_clock(r->seq).elapsed_ticks;
                uint64_t dt = sched->steps[sidx++ % sched->count];
                if (dt > rem)
                    dt = rem;
                rig_step(r, dt);
            }
        }
        out[cyc] = summarize_cycle(r, ls, bt, br, su, sf, sr, ss, sc, sp, sren, sa, sfr);
        if (out[cyc].key_on != 36 || out[cyc].key_off != 36 || out[cyc].voice_retire != 36)
            fail(r, C_TIMELINE, "s5 expected 36 key-on/off/retire, got %u/%u/%u",
                 out[cyc].key_on, out[cyc].key_off, out[cyc].voice_retire);
    }
    if (out[0].logical_hash != out[1].logical_hash || out[0].pcm_hash != out[1].pcm_hash)
        fail(r, C_CYCLE_PARITY, "s5 cycle 2 differs from cycle 1");
    return r;
}

/* ---- Scenario Family 6: Bank loss/replacement & reset/rebind ----------- */

static Rig *run_s6(Faults f, CycleSummary out[2])
{
    HGSeqConfig cfg = {8, 4, 0};
    HGSeqTimingConfig tm = {HG_SEQ_TIMING_CHRONO, HG_SEQ_ALLOC_ROUND_ROBIN,
                            HG_SEQ_STEAL_PRIORITY_OLDEST, HG_SEQ_REPEAT_POLY, 2};
    Rig *r = rig_create(cfg, tm, f);

    for (int cyc = 0; cyc < 2; ++cyc) {
        if (cyc == 1)
            cycle_teardown_and_reenter(r, f.skip_reset_cleanup);
        uint32_t ls = r->seq->log_count, br = r->seq->next_request;
        uint64_t bt = hg_seq_clock(r->seq).elapsed_ticks;
        uint32_t su = r->updates, sf = r->flushes, sr = r->ring_records;
        uint32_t ss = r->total_spu_writes, sc = hg_seq_clock(r->seq).channel_bytes_consumed;
        uint32_t sp = r->pcm_nonzero, sa = r->allocs, sfr = r->frees;
        uint64_t sren = hg_seq_clock(r->seq).render_frames;

        /* 1. Play on Bank A (SPU2 0x5010). */
        uint32_t idx0 = play_cue(r, 0, CUE_HOLD);
        assert(idx0 < 32);
        rig_step(r, 2);
        assert(!r->last_window_silent);

        /* 2. Clear slot 0 and immediately replace with Bank B (SPU2 0x6010). */
        hg_audio_slot_clear(&r->bus, 0);
        if (f.bypass_bank_retire) {
            /* Fault control: re-register Bank A at same addr/SPU2 BEFORE sequencer sees clear,
             * OR force-stage a voice after clear so Bank A voice stays sounding! */
            HGSg2VoiceParams p = {60, 60, 0x116b, 0x2000, 0x2000, 0x00ff, 0x1fc0, 0, 64, 2};
            assert(hg_audio_ee_post(&r->ee, (HGSg2Packet){HG_SG2_ADSR, 8, p.adsr1, p.adsr2}));
            assert(hg_audio_ee_post(&r->ee, (HGSg2Packet){HG_SG2_START_ADDRESS, 8, SPU_DEST_A, 0}));
            assert(hg_audio_ee_post(&r->ee, (HGSg2Packet){HG_SG2_GAIN, 8, p.left, p.right}));
            assert(hg_audio_ee_post(&r->ee, (HGSg2Packet){HG_SG2_PITCH, 8, (60u << 24) | (60u << 16) | 64u, (2u << 24) | 0x116bu}));
            assert(hg_audio_ee_stage_key(&r->ee, 8, true));
            fail(r, C_BANK_RETIREMENT, "voice remained sounding after bank clear");
        }
        if (play_cue(r, 0, CUE_PULSE) != (uint32_t)-4)
            fail(r, C_BANK_RETIREMENT, "cleared slot accepted play request");
        assert(register_bank_at(r, BANK_B_ADDR, SPU_DEST_B) == 0);
        r->write_count = 0;

        /* Sequencer detects Bank A -> Bank B replacement: keys off req 0 (HG_SEQ_R_LOST),
         * holds 2 ticks, retires req 0 (HG_SEQ_D_LOST). */
        rig_step(r, 3);
        bool saw_lost = false;
        for (uint32_t i = ls; i < r->seq->log_count; ++i)
            saw_lost |= r->seq->log[i].kind == HG_SEQ_D_LOST;
        if (!saw_lost)
            fail(r, C_BANK_RETIREMENT, "bank replacement did not emit HG_SEQ_D_LOST");

        /* 3. Play new cue on Bank B (checker verifies SPU2 SSA == 0x6010 + tone.offset!). */
        uint32_t idx1 = play_cue(r, 0, CUE_PULSE);
        assert(idx1 < 32);
        rig_step(r, 6);

        /* 4. Mid-playback reset, IOP teardown, unbound refusal, recreate, rebind. */
        uint32_t idx2 = play_cue(r, 0, CUE_HOLD);
        assert(idx2 < 32);
        rig_step(r, 2);
        hg_seq_reset(r->seq);
        r->write_count = 0;
        snapshot_ring(r);
        assert(hg_audio_ee_flush(&r->ee, &r->reply));
        check_window(r);
        hg_audio_iop_destroy(r->iop);
        r->iop = NULL;
        r->ee.iop = NULL;
        if (hg_seq_update(r->seq, 1) || hg_seq_owned_voices(r->seq, NULL) != 0)
            fail(r, C_RESET_CLEANUP, "unbound service accepted update or retained voices");
        service_up(r);
        hg_audio_slots_init(&r->bus);
        assert(register_bank_at(r, BANK_A_ADDR, SPU_DEST_A) == 0);
        assert(hg_seq_rebind(r->seq, &r->ee));
        memset(&r->reply, 0, sizeof(r->reply));

        /* 5. Fresh request after rebind starts at voice 8 (cursor reset to 0). */
        uint32_t idx3 = play_cue(r, 0, CUE_PULSE);
        assert(idx3 < 32);
        uint32_t before_fresh = r->seq->log_count;
        rig_step(r, 6);
        bool fresh_v8 = false;
        for (uint32_t i = before_fresh; i < r->seq->log_count; ++i)
            if (r->seq->log[i].kind == HG_SEQ_D_KEY_ON && r->seq->log[i].voice == 8)
                fresh_v8 = true;
        if (!fresh_v8)
            fail(r, C_RESET_CLEANUP, "post-rebind request did not reset rotating cursor to voice 8");

        out[cyc] = summarize_cycle(r, ls, bt, br, su, sf, sr, ss, sc, sp, sren, sa, sfr);
    }
    if (out[0].logical_hash != out[1].logical_hash || out[0].pcm_hash != out[1].pcm_hash)
        fail(r, C_CYCLE_PARITY, "s6 cycle 2 differs from cycle 1");
    return r;
}

/* ---- Scenario Family 7: Irregular batches, malformed HSQ1 & bounds ----- */

static Rig *run_s7(Faults f, CycleSummary out[2])
{
    HGSeqConfig cfg = {8, 4, 8}; /* tight runaway event budget = 8 */
    HGSeqTimingConfig tm = {HG_SEQ_TIMING_CHRONO, HG_SEQ_ALLOC_LOWEST_FREE,
                            HG_SEQ_STEAL_PRIORITY_OLDEST, HG_SEQ_REPEAT_POLY, 1};
    Rig *r = rig_create(cfg, tm, f);
    static const uint32_t IRREG[] = {0, 0, 5, 0, 1, 7, 2, 0, 3};

    for (int cyc = 0; cyc < 2; ++cyc) {
        if (cyc == 1)
            cycle_teardown_and_reenter(r, f.skip_reset_cleanup);
        uint32_t ls = r->seq->log_count, br = r->seq->next_request;
        uint64_t bt = hg_seq_clock(r->seq).elapsed_ticks;
        uint32_t su = r->updates, sf = r->flushes, sr = r->ring_records;
        uint32_t ss = r->total_spu_writes, sc = hg_seq_clock(r->seq).channel_bytes_consumed;
        uint32_t sp = r->pcm_nonzero, sa = r->allocs, sfr = r->frees;
        uint64_t sren = hg_seq_clock(r->seq).render_frames;

        /* Irregular multi-cue batch with simultaneous due events. */
        uint32_t p0 = play_cue(r, 0, CUE_SHORT_TIMED);
        uint32_t p1 = play_cue(r, 0, CUE_OVERLAP_2TRK);
        assert(p0 < 32 && p1 < 32);
        assert(hg_seq_tag_request(r->seq, p0, bt));
        assert(hg_seq_tag_request(r->seq, p1, bt + 2));
        for (size_t i = 0; i < sizeof(IRREG) / sizeof(IRREG[0]); ++i)
            rig_step(r, IRREG[i]);

        /* Malformed cues: bad magic, bad track, bad alignment, bad opcode, runaway, bad pitch. */
        static const uint32_t BAD[] = {
            CUE_BAD_MAGIC, CUE_BAD_TRACK, CUE_BAD_TONE_ALIGN,
            CUE_BAD_OPCODE, CUE_RUNAWAY, CUE_BAD_PITCH,
        };
        for (size_t i = 0; i < sizeof(BAD) / sizeof(BAD[0]); ++i) {
            uint32_t bidx = play_cue(r, 0, BAD[i]);
            assert(bidx < 32);
            rig_step(r, 3);
        }

        /* Clock overflow and invalid config guards must fail closed without mutating clock. */
        uint64_t before_ovf = hg_seq_clock(r->seq).elapsed_ticks;
        if (hg_seq_update_u64(r->seq, UINT64_MAX) ||
            hg_seq_clock(r->seq).elapsed_ticks != before_ovf)
            fail(r, C_END_STATE, "uint64_t clock overflow was not rejected");

        HGSequencer *spare = calloc(1, sizeof(*spare));
        assert(spare);
        HGAudioEeService unbound = {0};
        HGSeqTimingConfig bad_tm = {99, 0, 0, 0, 0};
        if (hg_seq_init_timed(spare, &unbound, (HGSeqConfig){8, 4, 0}, bad_tm) ||
            hg_seq_init(spare, &unbound, (HGSeqConfig){46, 4, 0}) ||
            hg_seq_tag_request(spare, 0, 0) || hg_seq_tag_cancel(spare, 0, 0))
            fail(r, C_END_STATE, "invalid timing config or unbound tag accepted");
        free(spare);

        out[cyc] = summarize_cycle(r, ls, bt, br, su, sf, sr, ss, sc, sp, sren, sa, sfr);
        if (out[cyc].reject != 3 || out[cyc].stage_fail != 1)
            fail(r, C_TIMELINE, "s7 expected 3 rejects and 1 stage_fail, got %u/%u",
                 out[cyc].reject, out[cyc].stage_fail);
    }
    if (out[0].logical_hash != out[1].logical_hash || out[0].pcm_hash != out[1].pcm_hash)
        fail(r, C_CYCLE_PARITY, "s7 cycle 2 differs from cycle 1");
    return r;
}

/* ---- Negative control helpers (post-run log/state audit mutations) ----- */

static void reaudit_log_only(Rig *r)
{
    memset(r->owner, 0, sizeof(r->owner));
    r->have_decision = false;
    r->last_due_tick = 0;
    r->last_order_seq = 0;
    uint32_t hold = r->seq->timing.release_hold_ticks;
    for (uint32_t i = 0; i < r->seq->log_count; ++i) {
        const HGSeqDecision *d = &r->seq->log[i];
        const HGSeqDecisionTiming *tm = &r->seq->timing_log[i];
        if (r->have_decision) {
            if (tm->due_tick < r->last_due_tick || tm->order_seq <= r->last_order_seq)
                fail(r, C_EVENT_ORDER, "decision %u out of chronological order", i);
        }
        r->last_due_tick = tm->due_tick;
        r->last_order_seq = tm->order_seq;
        r->have_decision = true;
        if (d->kind == HG_SEQ_D_KEY_ON) {
            if (r->owner[d->voice].state == V_RELEASING)
                fail(r, C_PREMATURE_REUSE, "voice %u reused before release completed", d->voice);
            else if (r->owner[d->voice].state == V_ACTIVE)
                fail(r, C_OWNERSHIP, "voice %u double-owned", d->voice);
            r->owner[d->voice].state = V_ACTIVE;
            r->owner[d->voice].request = d->request;
            r->owner[d->voice].track = d->track;
            r->owner[d->voice].note = d->note;
        } else if (d->kind == HG_SEQ_D_KEY_OFF) {
            if (r->owner[d->voice].state != V_ACTIVE || r->owner[d->voice].request != d->request)
                fail(r, C_OWNERSHIP, "voice %u released by wrong owner %u", d->voice, d->request);
            if (hold > 0 && d->detail != HG_SEQ_R_STOLEN && d->detail != HG_SEQ_R_RESET) {
                r->owner[d->voice].state = V_RELEASING;
                r->owner[d->voice].release_done_tick = tm->due_tick + hold;
            } else {
                r->owner[d->voice].state = V_IDLE;
            }
        } else if (d->kind == HG_SEQ_D_VOICE_RETIRE) {
            if (tm->due_tick < r->owner[d->voice].release_done_tick)
                fail(r, C_PREMATURE_REUSE, "voice %u retired before hold expired", d->voice);
            r->owner[d->voice].state = V_IDLE;
        }
    }
}

/* ---- JSON emission ----------------------------------------------------- */

static void emit_cycle(const CycleSummary *c)
{
    printf("{\"updates\": %u, \"flushes\": %u, \"decisions\": %u, \"key_on\": %u, "
           "\"key_off\": %u, \"voice_retire\": %u, \"steal\": %u, \"drop\": %u, "
           "\"reject\": %u, \"stage_fail\": %u, \"cancel_active\": %u, \"cancel_pending\": %u, "
           "\"lost\": %u, \"ring_records\": %u, \"spu_writes\": %u, \"channel_bytes\": %u, "
           "\"pcm_nonzero_frames\": %u, \"render_frames\": %llu, \"elapsed_ticks\": %llu, "
           "\"logical_hash\": \"%016llx\", \"pcm_fnv1a64\": \"%016llx\", "
           "\"iop_allocs\": %u, \"iop_frees\": %u, \"clean_end\": %s}",
           c->updates, c->flushes, c->decisions, c->key_on, c->key_off, c->voice_retire,
           c->steal, c->drop, c->reject, c->stage_fail, c->cancel_active, c->cancel_pending,
           c->lost, c->ring_records, c->spu_writes, c->channel_bytes, c->pcm_nonzero,
           (unsigned long long)c->render_frames, (unsigned long long)c->elapsed_ticks,
           (unsigned long long)c->logical_hash, (unsigned long long)c->pcm_hash,
           c->allocs, c->frees, c->clean_end ? "true" : "false");
}

static void emit_failures(const Rig *r)
{
    printf("[");
    for (uint32_t i = 0; i < r->fail_count; ++i)
        printf("%s\"%s\"", i ? ", " : "", CHECK_NAME[r->fail[i]]);
    printf("]");
}

typedef Rig *(*MultiFn)(const Schedule *, Faults, CycleSummary[2]);

static bool run_multi_family(const char *name, MultiFn fn, bool first)
{
    CycleSummary cyc[3][2];
    Rig *rigs[3];
    bool ok = true;
    for (int s = 0; s < 3; ++s) {
        rigs[s] = fn(&SCHEDULES[s], NO_FAULTS, cyc[s]);
        /* Repeat-run determinism check on identical schedule. */
        CycleSummary rep[2];
        Rig *r2 = fn(&SCHEDULES[s], NO_FAULTS, rep);
        if (cyc[s][0].logical_hash != rep[0].logical_hash ||
            cyc[s][0].pcm_hash != rep[0].pcm_hash ||
            cyc[s][1].logical_hash != rep[1].logical_hash ||
            cyc[s][1].pcm_hash != rep[1].pcm_hash)
            fail(rigs[s], C_DETERMINISM, "repeat run differs on schedule %s", SCHEDULES[s].name);
        rig_destroy(r2);
    }
    /* Cross-schedule logical equivalence: unit-tick, coarse-2, irregular-zero must
     * match on logical_hash, key_on, key_off, voice_retire, steal, drop, clean_end! */
    for (int s = 1; s < 3; ++s) {
        if (cyc[s][0].logical_hash != cyc[0][0].logical_hash ||
            cyc[s][0].key_on != cyc[0][0].key_on ||
            cyc[s][0].key_off != cyc[0][0].key_off ||
            cyc[s][0].voice_retire != cyc[0][0].voice_retire ||
            cyc[s][0].steal != cyc[0][0].steal ||
            cyc[s][0].drop != cyc[0][0].drop)
            fail(rigs[s], C_SCHEDULE_PARITY, "schedule %s logical timeline differs from unit-tick",
                 SCHEDULES[s].name);
    }

    printf("%s\n    {\"name\": \"%s\", \"multi_schedule\": true, \"schedule_logical_parity\": %s, \"schedules\": [",
           first ? "" : ",", name,
           (cyc[0][0].logical_hash == cyc[1][0].logical_hash &&
            cyc[0][0].logical_hash == cyc[2][0].logical_hash) ? "true" : "false");
    for (int s = 0; s < 3; ++s) {
        printf("%s\n      {\"schedule\": \"%s\", \"harness_completed\": true, \"failures\": ",
               s ? "," : "", SCHEDULES[s].name);
        emit_failures(rigs[s]);
        printf(", \"detail\": \"%s\", \"cycle1\": ", rigs[s]->detail);
        emit_cycle(&cyc[s][0]);
        printf(", \"cycle2\": ");
        emit_cycle(&cyc[s][1]);
        printf("}");
        ok &= rigs[s]->fail_count == 0;
        rig_destroy(rigs[s]);
    }
    printf("\n    ]}");
    return ok;
}

typedef Rig *(*SingleFn)(Faults, CycleSummary[2]);

static bool run_single_family(const char *name, const char *sched_label, SingleFn fn, bool first)
{
    CycleSummary cyc[2], rep[2];
    Rig *r = fn(NO_FAULTS, cyc);
    Rig *r2 = fn(NO_FAULTS, rep);
    if (cyc[0].logical_hash != rep[0].logical_hash || cyc[0].pcm_hash != rep[0].pcm_hash ||
        cyc[1].logical_hash != rep[1].logical_hash || cyc[1].pcm_hash != rep[1].pcm_hash)
        fail(r, C_DETERMINISM, "repeat run differs");
    rig_destroy(r2);

    printf("%s\n    {\"name\": \"%s\", \"multi_schedule\": false, \"schedule_logical_parity\": true, \"schedules\": [\n"
           "      {\"schedule\": \"%s\", \"harness_completed\": true, \"failures\": ",
           first ? "" : ",", name, sched_label);
    emit_failures(r);
    printf(", \"detail\": \"%s\", \"cycle1\": ", r->detail);
    emit_cycle(&cyc[0]);
    printf(", \"cycle2\": ");
    emit_cycle(&cyc[1]);
    printf("}\n    ]}");
    bool ok = r->fail_count == 0;
    rig_destroy(r);
    return ok;
}

static bool emit_control(const char *name, const char *kind, Rig *r, uint32_t expect, bool first)
{
    bool det = has_fail(r, expect);
    printf("%s\n    {\"name\": \"%s\", \"kind\": \"%s\", \"harness_completed\": true, "
           "\"expected_failure\": \"%s\", \"detected\": %s, \"failures\": ",
           first ? "" : ",", name, kind, CHECK_NAME[expect], det ? "true" : "false");
    emit_failures(r);
    printf(", \"detail\": \"%s\"}", r->detail);
    rig_destroy(r);
    return det;
}

int main(void)
{
    bool ok = true;
    printf("{\n"
           "  \"schema\": \"hg-m26-audio-timing/v1\",\n"
           "  \"synthetic_only\": true,\n"
           "  \"retail_equivalence_claimed\": false,\n"
           "  \"frames_per_tick_model\": %u,\n"
           "  \"schedule_note\": \"Unit-tick, coarse-2, and irregular-zero are independent synthetic schedules; logical due times, order, ownership and final state must agree while raw service timestamps and post-flush PCM windows reflect each schedule's flush boundaries.\",\n"
           "  \"families\": [", FRAMES_PER_TICK);

    ok &= run_multi_family("s1-short-timed-cue", run_s1, true);
    ok &= run_multi_family("s2-overlapping-owners", run_s2, false);
    ok &= run_multi_family("s3-exhaustion-contention", run_s3, false);
    ok &= run_multi_family("s4-pending-active-cancel", run_s4, false);
    ok &= run_multi_family("s5-release-retire-reuse", run_s5, false);
    ok &= run_single_family("s6-bank-replace-reset-rebind", "mixed-lifecycle", run_s6, false);
    ok &= run_single_family("s7-irregular-bounds-malformed", "irregular-stress", run_s7, false);

    printf("\n  ],\n  \"controls\": [");

    CycleSummary sink[2];
    Faults f;
    Rig *cr;

    /* 1. nc-dropped-timing */
    f = NO_FAULTS; f.drop_timing = true;
    cr = run_s1(&SCHEDULES[0], f, sink);
    ok &= emit_control("nc-dropped-timing", "fault-injection", cr, C_TIMELINE, true);

    /* 2. nc-reordered-events */
    cr = run_s1(&SCHEDULES[0], NO_FAULTS, sink);
    assert(cr->seq->log_count >= 6);
    {
        HGSeqDecisionTiming tmp = cr->seq->timing_log[3];
        cr->seq->timing_log[3] = cr->seq->timing_log[5];
        cr->seq->timing_log[5] = tmp;
    }
    reaudit_log_only(cr);
    ok &= emit_control("nc-reordered-events", "log-mutation", cr, C_EVENT_ORDER, false);

    /* 3. nc-missing-channel-consumption */
    f = NO_FAULTS; f.corrupt_cancel_marker = true;
    cr = run_s4(&SCHEDULES[0], f, sink);
    ok &= emit_control("nc-missing-channel-consumption", "fault-injection", cr, C_CHANNEL_CONSUMPTION, false);

    /* 4. nc-ownership-violation */
    cr = run_s2(&SCHEDULES[0], NO_FAULTS, sink);
    for (uint32_t i = 0; i < cr->seq->log_count; ++i) {
        if (cr->seq->log[i].kind == HG_SEQ_D_KEY_OFF && cr->seq->log[i].request == 1) {
            cr->seq->log[i].request = 0;
            break;
        }
    }
    reaudit_log_only(cr);
    ok &= emit_control("nc-ownership-violation", "log-mutation", cr, C_OWNERSHIP, false);

    /* 5. nc-missing-key-off */
    f = NO_FAULTS; f.filter_key_off = true;
    cr = run_s1(&SCHEDULES[0], f, sink);
    ok &= emit_control("nc-missing-key-off", "fault-injection", cr, C_KEY_OFF, false);

    /* 6. nc-premature-reuse */
    cr = run_s3(&SCHEDULES[0], NO_FAULTS, sink);
    /* Pull v20's VOICE_RETIRE 1 tick earlier than its hold expiration (t=4 < t=5). */
    for (uint32_t i = 0; i < cr->seq->log_count; ++i) {
        if (cr->seq->log[i].kind == HG_SEQ_D_VOICE_RETIRE && cr->seq->log[i].voice == 20) {
            cr->seq->timing_log[i].due_tick -= 1;
            break;
        }
    }
    reaudit_log_only(cr);
    ok &= emit_control("nc-premature-reuse", "log-mutation", cr, C_PREMATURE_REUSE, false);

    /* 7. nc-bad-cancel-retirement */
    f = NO_FAULTS; f.dirty_cancel_retire = true;
    cr = run_s4(&SCHEDULES[0], f, sink);
    ok &= emit_control("nc-bad-cancel-retirement", "fault-injection", cr, C_RETIREMENT, false);

    /* 8. nc-bad-bank-retirement */
    f = NO_FAULTS; f.bypass_bank_retire = true;
    cr = run_s6(f, sink);
    ok &= emit_control("nc-bad-bank-retirement", "fault-injection", cr, C_BANK_RETIREMENT, false);

    /* 9. nc-stale-reset */
    f = NO_FAULTS; f.skip_reset_cleanup = true;
    cr = run_s1(&SCHEDULES[0], f, sink);
    ok &= emit_control("nc-stale-reset", "fault-injection", cr, C_RESET_CLEANUP, false);

    /* 10. nc-service-order-loss */
    f = NO_FAULTS; f.swap_service_order = true;
    cr = run_s1(&SCHEDULES[0], f, sink);
    ok &= emit_control("nc-service-order-loss", "fault-injection", cr, C_SERVICE_ORDER, false);

    printf("\n  ],\n  \"ok\": %s\n}\n", ok ? "true" : "false");
    return ok ? 0 : 1;
}
