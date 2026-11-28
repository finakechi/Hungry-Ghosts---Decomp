/* M24 connected lifecycle harness: synthetic bank -> hg_audio_play request ->
 * HSQ1 sequence processor + physical voice manager (src/audio/audio_sequencer.c)
 * -> M22 EE bridge -> SG2IOP host model -> deterministic PCM.
 *
 * The harness supplies only configuration, synthetic resources, logical
 * requests (hg_audio_play / hg_audio_cancel / slot release / reset) and the
 * service clock. Every note and voice decision comes from the model.
 *
 * Four record streams are kept apart: inputs (requests and ticks), model
 * decisions (HGSequencer.log), service operations (EE ring records observed
 * at each flush) and model output (ordered SPU2 writes, ENVX replies, PCM).
 * An independent checker cross-validates decisions against the SPU2 writes
 * and ENVX replies, audits ownership from the decision stream alone and
 * compares decision timing with each scenario's authored expectation.
 *
 * Negative controls inject real divergences into the connected run (skipped
 * clock advancement, dropped or filtered ring records, a foreign key-off)
 * plus one declared log-mutation control. A control is accepted only when
 * the run itself completed and the checker reports the expected check.
 *
 * Output: one JSON document on stdout. Exit status 0 only when every
 * positive scenario passes and every control fails exactly as expected.
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
    BANK_ADDR = 0x01000000u,
    BANK_OFF = 0x30u,
    BANK_SIZE = 0x800u,
    PAYLOAD_OFF = BANK_OFF + BANK_SIZE,
    PAYLOAD_SIZE = 0x40u,
    SPU_DEST = 0x5010u,
    T4_OFF = 0x90u,      /* table4 inside the bank */
    CUE_AREA = 0x100u,   /* cue blobs start at table4 + CUE_AREA */
    CUE_STRIDE = 0x80u,
    MAX_CUES = 12u,
    FRAMES = 800u,       /* 48 kHz / 60 updates per second */
    MAX_WRITES = 8192u,
    MAX_FAIL = 16u,
};

enum Check {
    C_KEY_ON = 1, C_KEY_OFF, C_SSA, C_ENVX_UNOWNED, C_ENVX_SILENT, C_OWNERSHIP,
    C_MODEL_MASK, C_TIMELINE, C_END_STATE, C_DETERMINISM,
};
static const char *check_name[] = {
    "", "key-on", "key-off", "start-address", "envx-unowned", "envx-silent",
    "ownership", "model-mask", "timeline", "end-state", "determinism",
};

/* ---- guest ------------------------------------------------------------- */

typedef struct { uint32_t address; uint16_t value; } SpuWrite;

typedef struct Rig {
    uint8_t *ram;
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

    SpuWrite *writes;
    uint32_t write_count;
    HGSg2Reply reply;
    uint64_t pcm_hash;
    uint32_t pcm_nonzero, updates, flushes, ring_records;
    bool last_window_silent;

    /* Fault injection (negative controls). */
    int zero_tick_from, zero_tick_until; /* updates in [from, until) pass 0 ticks */
    bool drop_ring_on_key_on;            /* clear the ring when it holds a KEY_ON */
    bool filter_key_off;                 /* strip KEY_OFF records from the ring */
    int foreign_key_off_at;              /* stage an unowned key-off at this update */
    uint32_t foreign_voice;

    /* Checker. */
    uint32_t checked;                    /* decisions already cross-checked */
    struct { bool owned; uint32_t request, track, note, tone; } owner[HG_SG2_VOICES];
    uint32_t looping_tones;              /* bitmask of looping tone indices */
    uint32_t fail[MAX_FAIL], fail_count;
    char detail[256];
} Rig;

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

/* ---- synthetic resources ----------------------------------------------- */

typedef struct { uint8_t b[CUE_STRIDE]; uint32_t n; } Blob;

static void put8(Blob *x, uint32_t v) { assert(x->n < CUE_STRIDE); x->b[x->n++] = (uint8_t)v; }
static void put16(Blob *x, uint32_t v) { put8(x, v & 0xff); put8(x, v >> 8); }

typedef struct { uint16_t offset; uint8_t root; int8_t fine; uint8_t volume, pan; } ToneSpec;

/* Original synthetic tones: 0 one-block loop, 1 two-block loop, 2 one-shot. */
static const ToneSpec TONES[3] = {
    {0x00, 60, 0, 127, 64}, {0x10, 60, 0, 100, 32}, {0x30, 60, 0, 127, 96},
};
#define LOOPING_TONES 0x3u

static void hsq_begin(Blob *x, uint32_t tracks, uint32_t priority)
{
    memset(x, 0, sizeof(*x));
    put16(x, 0x5348); put16(x, 0x3151);     /* "HSQ1" */
    put8(x, HG_SEQ_VERSION); put8(x, tracks); put8(x, 3); put8(x, priority);
    put16(x, HG_SEQ_HEADER_SIZE);           /* tone table */
    put16(x, 0);                            /* size, patched by hsq_end */
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
static void ev_end(Blob *x) { put8(x, HG_SEQ_OP_END); }

/* Cue catalogue (param1 = 0, param2 = cue). */
enum { CUE_NOTIFY, CUE_TIMED, CUE_OVERLAP, CUE_CHORD_LOW, CUE_LEAD_HIGH, CUE_BLIP_LOWEST,
       CUE_HOLD, CUE_BAD_MAGIC, CUE_BAD_TRACK, CUE_BAD_OPCODE, CUE_RUNAWAY, CUE_BAD_PITCH };

static void build_cues(Blob cue[MAX_CUES])
{
    Blob *x;
    x = &cue[CUE_NOTIFY]; hsq_begin(x, 1, 64); hsq_track(x, 0);
    ev_on(x, 72, 100, 0); ev_wait(x, 3); ev_off(x, 72); ev_end(x); hsq_end(x);

    x = &cue[CUE_TIMED]; hsq_begin(x, 1, 64); hsq_track(x, 0);
    ev_note(x, 60, 127, 0, 4); ev_wait(x, 2); put8(x, HG_SEQ_OP_VOLUME); put8(x, 64);
    ev_wait(x, 1); put8(x, HG_SEQ_OP_BEND); put8(x, 96); ev_wait(x, 3);
    ev_on(x, 67, 90, 1); ev_wait(x, 2); ev_end(x); hsq_end(x);

    x = &cue[CUE_OVERLAP]; hsq_begin(x, 2, 64);
    hsq_track(x, 0); ev_on(x, 60, 100, 0); ev_wait(x, 4); ev_off(x, 60); ev_end(x);
    hsq_track(x, 1); ev_wait(x, 2); ev_on(x, 64, 100, 1); ev_wait(x, 4); ev_off(x, 64); ev_end(x);
    hsq_end(x);

    x = &cue[CUE_CHORD_LOW]; hsq_begin(x, 1, 32); hsq_track(x, 0);
    ev_on(x, 60, 80, 0); ev_on(x, 64, 80, 0); ev_on(x, 67, 80, 1); ev_wait(x, 10); ev_end(x); hsq_end(x);

    x = &cue[CUE_LEAD_HIGH]; hsq_begin(x, 1, 96); hsq_track(x, 0);
    ev_on(x, 72, 120, 0); ev_wait(x, 2); ev_end(x); hsq_end(x);

    x = &cue[CUE_BLIP_LOWEST]; hsq_begin(x, 1, 16); hsq_track(x, 0);
    ev_on(x, 48, 60, 0); ev_wait(x, 1); ev_end(x); hsq_end(x);

    x = &cue[CUE_HOLD]; hsq_begin(x, 1, 64); hsq_track(x, 0);
    ev_on(x, 60, 100, 0); ev_wait(x, 1000); ev_end(x); hsq_end(x);

    x = &cue[CUE_BAD_MAGIC]; hsq_begin(x, 1, 64); hsq_track(x, 0); ev_end(x); hsq_end(x);
    x->b[0] = 'X';

    x = &cue[CUE_BAD_TRACK]; hsq_begin(x, 1, 64); hsq_track(x, 0); ev_end(x); hsq_end(x);
    x->b[12] = 0xf0; /* track offset past the declared size */

    x = &cue[CUE_BAD_OPCODE]; hsq_begin(x, 1, 64); hsq_track(x, 0);
    ev_on(x, 60, 100, 0); ev_wait(x, 1); put8(x, 0x7e); hsq_end(x);

    x = &cue[CUE_RUNAWAY]; hsq_begin(x, 1, 64); hsq_track(x, 0);
    for (int i = 0; i < 12; ++i) { put8(x, HG_SEQ_OP_VOLUME); put8(x, 100); }
    ev_end(x); hsq_end(x);

    x = &cue[CUE_BAD_PITCH]; hsq_begin(x, 1, 64); hsq_track(x, 0);
    ev_on(x, 120, 100, 0); ev_wait(x, 1); ev_on(x, 62, 100, 3); ev_end(x); hsq_end(x);
}

static void w16le(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static void w32le(uint8_t *p, uint32_t v) { for (int i = 0; i < 4; ++i) p[i] = (uint8_t)(v >> (8 * i)); }

static void build_bank(Rig *r)
{
    uint8_t *res = r->ram + BANK_ADDR, *bank = res + BANK_OFF, *t4 = bank + T4_OFF;
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
    /* table4: index 0 -> subtable at +0x10 with MAX_CUES extras. */
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
    /* Original synthetic ADPCM (filter 0, shift 2). */
    uint8_t *pcm = res + PAYLOAD_OFF;
    pcm[0x00] = 0x02; pcm[0x01] = 0x07; memset(pcm + 0x02, 0x77, 7); memset(pcm + 0x09, 0x99, 7);
    pcm[0x10] = 0x02; pcm[0x11] = 0x04; memset(pcm + 0x12, 0x33, 14);
    pcm[0x20] = 0x02; pcm[0x21] = 0x03; memset(pcm + 0x22, 0xdd, 14);
    pcm[0x30] = 0x02; pcm[0x31] = 0x01; memset(pcm + 0x32, 0x55, 14);
}

static uint32_t register_bank(Rig *r)
{
    for (unsigned step = 0; step < 32; ++step) {
        if (hg_audio_register_entry(&r->bus, BANK_ADDR))
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

static Rig *rig_create(HGSeqConfig config)
{
    Rig *r = calloc(1, sizeof(*r));
    assert(r);
    r->ram = calloc(EE_RAM_SIZE, 1);
    r->writes = calloc(MAX_WRITES, sizeof(*r->writes));
    r->seq = calloc(1, sizeof(*r->seq));
    assert(r->ram && r->writes && r->seq);
    r->bus = (HGAudioBus){b_r8, b_r16, b_r32, b_w8, b_w16, b_w32, bus_call, r};
    r->zero_tick_from = r->zero_tick_until = r->foreign_key_off_at = -1;
    r->looping_tones = LOOPING_TONES;
    r->pcm_hash = 1469598103934665603ull;
    service_up(r);
    st(r, HG_AUDIO_CTX_SAVE, 4, SPU_DEST);
    for (uint32_t ch = 0; ch < 32; ++ch)
        st(r, HG_AUDIO_CHANNEL_BASE + ch * HG_CH_STRIDE, 4, UINT32_MAX);
    build_bank(r);
    assert(register_bank(r) == 0);
    assert(r->allocs == 1 && r->frees == 1);
    assert(hg_seq_init(r->seq, &r->ee, config));
    r->write_count = 0;
    return r;
}

static void rig_destroy(Rig *r)
{
    hg_audio_iop_destroy(r->iop);
    free(r->seq);
    free(r->writes);
    free(r->ram);
    free(r);
}

/* ---- checker ------------------------------------------------------------ */

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

/* Voices named by SPU2 key-on (0x1a0/0x1a2) or key-off (0x1a4/0x1a6) writes. */
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
            uint32_t expect = SPU_DEST + TONES[d->detail].offset;
            if (!spu_start_address(r, d->voice, &address) || address != expect)
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
            fail(r, C_KEY_ON, "voice %u: %u key-on decisions, %u SPU2 key-ons (update %u)",
                 v, want_on[v], got_on[v], r->updates);
        if (got_off[v] != want_off[v])
            fail(r, C_KEY_OFF, "voice %u: %u key-off decisions, %u SPU2 key-offs (update %u)",
                 v, want_off[v], got_off[v], r->updates);
        bool owned = r->owner[v].owned;
        bool active = r->reply.envelope[v] != 0;
        if (active && !owned)
            fail(r, C_ENVX_UNOWNED, "voice %u sounding without an owner (update %u)", v, r->updates);
        if (owned && !active && (r->looping_tones >> r->owner[v].tone & 1u))
            fail(r, C_ENVX_SILENT, "owned looping voice %u silent (update %u)", v, r->updates);
        if (owned != (bool)(model_mask[v / 24] >> (v % 24) & 1u))
            fail(r, C_MODEL_MASK, "voice %u: checker ownership disagrees with model", v);
    }
}

/* ---- connected service update ------------------------------------------ */

static void mutate_ring(Rig *r)
{
    uint32_t count = ld(r, HG_SG2_EE_CURSOR, 4), kept = 0;
    bool key_on = false;
    for (uint32_t i = 0; i < count; ++i)
        key_on |= ld(r, HG_SG2_EE_PACKETS + 16 * i, 4) == HG_SG2_KEY_ON;
    if (r->drop_ring_on_key_on && key_on) {
        st(r, HG_SG2_EE_CURSOR, 4, 0);
        r->drop_ring_on_key_on = false; /* one dropped batch */
        return;
    }
    if (!r->filter_key_off)
        return;
    for (uint32_t i = 0; i < count; ++i) {
        uint32_t src = HG_SG2_EE_PACKETS + 16 * i, dst = HG_SG2_EE_PACKETS + 16 * kept;
        if (ld(r, src, 4) == HG_SG2_KEY_OFF)
            continue;
        for (uint32_t w = 0; w < 16; w += 4)
            st(r, dst + w, 4, ld(r, src + w, 4));
        kept++;
    }
    st(r, HG_SG2_EE_CURSOR, 4, kept);
}

static void rig_update(Rig *r, uint32_t ticks)
{
    int u = (int)r->updates;
    if (u >= r->zero_tick_from && u < r->zero_tick_until)
        ticks = 0;
    assert(hg_seq_update(r->seq, ticks));
    if (u == r->foreign_key_off_at)
        assert(hg_audio_ee_stage_key(&r->ee, r->foreign_voice, false));
    mutate_ring(r);
    r->ring_records += ld(r, HG_SG2_EE_CURSOR, 4);
    r->write_count = 0;
    assert(hg_audio_ee_flush(&r->ee, &r->reply));
    r->flushes++;
    check_window(r);
    int16_t pcm[FRAMES * 2];
    hg_audio_iop_render(r->iop, pcm, FRAMES);
    r->last_window_silent = true;
    for (uint32_t f = 0; f < FRAMES; ++f) {
        if (pcm[2 * f] || pcm[2 * f + 1]) {
            r->pcm_nonzero++;
            r->last_window_silent = false;
        }
        for (int c = 0; c < 2; ++c) {
            uint16_t sample = (uint16_t)pcm[2 * f + c];
            r->pcm_hash = (r->pcm_hash ^ (sample & 0xff)) * 1099511628211ull;
            r->pcm_hash = (r->pcm_hash ^ (sample >> 8)) * 1099511628211ull;
        }
    }
    r->updates++;
}

static void run(Rig *r, uint32_t updates)
{
    for (uint32_t i = 0; i < updates; ++i)
        rig_update(r, 1);
}

static uint32_t play(Rig *r, uint32_t slot, uint32_t cue)
{
    return hg_audio_play(&r->bus, slot, 0, cue, 0, 0);
}

/* Expected decision timeline, independent of the model's implementation. */
typedef struct { uint32_t tick, kind, request, note, detail; } Expect;
#define ANY UINT32_MAX

static void check_timeline(Rig *r, const Expect *e, uint32_t n, uint32_t kinds_mask)
{
    uint32_t k = 0;
    for (uint32_t i = 0; i < r->seq->log_count; ++i) {
        const HGSeqDecision *d = &r->seq->log[i];
        if (!(kinds_mask >> d->kind & 1u))
            continue;
        if (k >= n) {
            fail(r, C_TIMELINE, "unexpected decision kind %u at tick %u", d->kind, d->tick);
            return;
        }
        if (d->tick != e[k].tick || d->kind != e[k].kind ||
            (e[k].request != ANY && d->request != e[k].request) ||
            (e[k].note != ANY && d->note != e[k].note) ||
            (e[k].detail != ANY && d->detail != e[k].detail)) {
            fail(r, C_TIMELINE, "decision %u: got kind %u tick %u req %u note %u detail %u, "
                 "want kind %u tick %u", k, d->kind, d->tick, d->request, d->note, d->detail,
                 e[k].kind, e[k].tick);
            return;
        }
        k++;
    }
    if (k != n)
        fail(r, C_TIMELINE, "%u of %u expected decisions observed", k, n);
}

static void check_end_state(Rig *r)
{
    uint32_t mask[2];
    bool ok = hg_seq_active_requests(r->seq) == 0 && hg_seq_owned_voices(r->seq, mask) == 0 &&
              r->last_window_silent;
    for (uint32_t i = 0; i < 32; ++i) {
        ok &= ld(r, HG_AUDIO_COMMAND_BASE + i * HG_CMD_STRIDE + HG_CMD_STATE, 4) == 0;
        ok &= ld(r, HG_AUDIO_CHANNEL_BASE + i * HG_CH_STRIDE, 4) == UINT32_MAX;
    }
    for (uint32_t v = 0; v < HG_SG2_VOICES; ++v)
        ok &= r->reply.envelope[v] == 0;
    if (!ok)
        fail(r, C_END_STATE, "stale request, voice, command, channel or sound after the scenario");
}

static uint32_t count_kind(const Rig *r, uint32_t kind)
{
    uint32_t n = 0;
    for (uint32_t i = 0; i < r->seq->log_count; ++i)
        n += r->seq->log[i].kind == kind;
    return n;
}

/* ---- JSON output -------------------------------------------------------- */

static bool first_record = true;

static void emit(const char *name, const char *group, Rig *r, uint32_t expect_check)
{
    printf("%s\n    {\"name\": \"%s\", \"group\": \"%s\", \"harness_completed\": true, "
           "\"updates\": %u, \"decisions\": %u, \"key_on\": %u, \"key_off\": %u, "
           "\"steal\": %u, \"drop\": %u, \"reject\": %u, \"stage_fail\": %u, "
           "\"ring_records\": %u, \"pcm_nonzero_frames\": %u, \"pcm_fnv1a64\": \"%016llx\", "
           "\"failures\": [",
           first_record ? "" : ",", name, group, r->updates, r->seq->log_count,
           count_kind(r, HG_SEQ_D_KEY_ON), count_kind(r, HG_SEQ_D_KEY_OFF),
           count_kind(r, HG_SEQ_D_STEAL), count_kind(r, HG_SEQ_D_DROP),
           count_kind(r, HG_SEQ_D_REJECT), count_kind(r, HG_SEQ_D_STAGE_FAIL),
           r->ring_records, r->pcm_nonzero, (unsigned long long)r->pcm_hash);
    for (uint32_t i = 0; i < r->fail_count; ++i)
        printf("%s\"%s\"", i ? ", " : "", check_name[r->fail[i]]);
    printf("], \"expected_failure\": \"%s\", \"detail\": \"%s\"}",
           expect_check ? check_name[expect_check] : "", r->detail);
    first_record = false;
}

static void emit_log(const Rig *r)
{
    printf(",\n  \"connected_decisions\": [");
    for (uint32_t i = 0; i < r->seq->log_count; ++i) {
        const HGSeqDecision *d = &r->seq->log[i];
        printf("%s\n    [%u, %u, %d, %d, %d, %d, %d, %d]", i ? "," : "", d->tick, d->kind,
               (int)d->request, (int)d->play_index, (int)d->track, (int)d->note,
               (int)d->voice, (int)d->detail);
    }
    printf("\n  ]");
}

/* ---- scenarios ---------------------------------------------------------- */

static const HGSeqConfig POOL4 = {8, 4, 0};
#define K(x) (1u << (x))
#define NOTE_KINDS (K(HG_SEQ_D_START) | K(HG_SEQ_D_KEY_ON) | K(HG_SEQ_D_KEY_OFF) | \
                    K(HG_SEQ_D_COMPLETE) | K(HG_SEQ_D_CANCEL) | K(HG_SEQ_D_LOST) | \
                    K(HG_SEQ_D_STEAL) | K(HG_SEQ_D_DROP) | K(HG_SEQ_D_REJECT) | \
                    K(HG_SEQ_D_VOLUME) | K(HG_SEQ_D_BEND) | K(HG_SEQ_D_TRACK_END) | \
                    K(HG_SEQ_D_STAGE_FAIL))

typedef struct Faults { int zero_from, zero_until, foreign_at; bool drop, filter; } Faults;
static const Faults NONE = {-1, -1, -1, false, false};

static void apply(Rig *r, Faults f)
{
    r->zero_tick_from = f.zero_from;
    r->zero_tick_until = f.zero_until;
    r->foreign_key_off_at = f.foreign_at;
    r->drop_ring_on_key_on = f.drop;
    r->filter_key_off = f.filter;
}

/* S1: short notification, one note, explicit key-off, completion. */
static Rig *scenario_notify(Faults f)
{
    Rig *r = rig_create(POOL4);
    apply(r, f);
    assert(play(r, 0, CUE_NOTIFY) == 0);
    run(r, 6);
    static const Expect e[] = {
        {1, HG_SEQ_D_START, 0, ANY, 0}, {1, HG_SEQ_D_KEY_ON, 0, 72, 0},
        {4, HG_SEQ_D_KEY_OFF, 0, 72, HG_SEQ_R_NOTE_OFF}, {4, HG_SEQ_D_TRACK_END, 0, ANY, 0},
        {4, HG_SEQ_D_COMPLETE, 0, ANY, ANY},
    };
    check_timeline(r, e, 5, NOTE_KINDS);
    check_end_state(r);
    return r;
}

/* S2: timed events: duration note, volume, bend, second note, end release. */
static Rig *scenario_timed(Faults f)
{
    Rig *r = rig_create(POOL4);
    apply(r, f);
    assert(play(r, 0, CUE_TIMED) == 0);
    run(r, 11);
    static const Expect e[] = {
        {1, HG_SEQ_D_START, 0, ANY, 0}, {1, HG_SEQ_D_KEY_ON, 0, 60, 0},
        {3, HG_SEQ_D_VOLUME, 0, ANY, 64}, {4, HG_SEQ_D_BEND, 0, ANY, 96},
        {5, HG_SEQ_D_KEY_OFF, 0, 60, HG_SEQ_R_DURATION},
        {7, HG_SEQ_D_KEY_ON, 0, 67, 1},
        {9, HG_SEQ_D_KEY_OFF, 0, 67, HG_SEQ_R_TRACK_END}, {9, HG_SEQ_D_TRACK_END, 0, ANY, 0},
        {9, HG_SEQ_D_COMPLETE, 0, ANY, ANY},
    };
    check_timeline(r, e, 9, NOTE_KINDS);
    check_end_state(r);
    return r;
}

/* S3: overlapping logical channels: two tracks per request, two requests with
 * the same notes one tick apart; each note-off must release its own voice. */
static Rig *scenario_overlap(Faults f, uint32_t foreign_voice)
{
    Rig *r = rig_create(POOL4);
    apply(r, f);
    r->foreign_voice = foreign_voice;
    assert(play(r, 0, CUE_OVERLAP) == 0);
    rig_update(r, 1);
    assert(play(r, 0, CUE_OVERLAP) == 1);
    run(r, 9);
    static const Expect e[] = {
        {1, HG_SEQ_D_START, 0, ANY, 0}, {1, HG_SEQ_D_KEY_ON, 0, 60, 0},
        {2, HG_SEQ_D_START, 1, ANY, 0}, {2, HG_SEQ_D_KEY_ON, 1, 60, 0},
        {3, HG_SEQ_D_KEY_ON, 0, 64, 1}, {4, HG_SEQ_D_KEY_ON, 1, 64, 1},
        {5, HG_SEQ_D_KEY_OFF, 0, 60, HG_SEQ_R_NOTE_OFF}, {5, HG_SEQ_D_TRACK_END, 0, ANY, 0},
        {6, HG_SEQ_D_KEY_OFF, 1, 60, HG_SEQ_R_NOTE_OFF}, {6, HG_SEQ_D_TRACK_END, 1, ANY, 0},
        {7, HG_SEQ_D_KEY_OFF, 0, 64, HG_SEQ_R_NOTE_OFF}, {7, HG_SEQ_D_TRACK_END, 0, ANY, 0},
        {7, HG_SEQ_D_COMPLETE, 0, ANY, ANY},
        {8, HG_SEQ_D_KEY_OFF, 1, 64, HG_SEQ_R_NOTE_OFF}, {8, HG_SEQ_D_TRACK_END, 1, ANY, 0},
        {8, HG_SEQ_D_COMPLETE, 1, ANY, ANY},
    };
    check_timeline(r, e, 16, NOTE_KINDS);
    /* Policy: lowest free pool voice; voices 8, 9, 10, 11 in key-on order. */
    static const uint32_t voices[4] = {8, 9, 10, 11};
    uint32_t n = 0;
    for (uint32_t i = 0; i < r->seq->log_count; ++i)
        if (r->seq->log[i].kind == HG_SEQ_D_KEY_ON && (n >= 4 || r->seq->log[i].voice != voices[n++]))
            fail(r, C_OWNERSHIP, "key-on %u used an unexpected voice", n);
    check_end_state(r);
    return r;
}

/* S4: exhaustion with priority stealing, dropping and reuse (3-voice pool). */
static Rig *scenario_exhaustion(void)
{
    Rig *r = rig_create((HGSeqConfig){20, 3, 0});
    assert(play(r, 0, CUE_CHORD_LOW) == 0);   /* fills voices 20, 21, 22 */
    rig_update(r, 1);
    assert(play(r, 0, CUE_LEAD_HIGH) == 1);   /* steals oldest low-priority voice 20 */
    rig_update(r, 1);
    assert(play(r, 0, CUE_BLIP_LOWEST) == 2); /* lower priority than everyone: dropped */
    run(r, 4);
    assert(play(r, 0, CUE_BLIP_LOWEST) == 3); /* lead released at tick 4: reuses 20 */
    run(r, 10);
    static const Expect e[] = {
        {1, HG_SEQ_D_START, 0, ANY, 0},
        {1, HG_SEQ_D_KEY_ON, 0, 60, 0}, {1, HG_SEQ_D_KEY_ON, 0, 64, 0}, {1, HG_SEQ_D_KEY_ON, 0, 67, 1},
        {2, HG_SEQ_D_START, 1, ANY, 0}, {2, HG_SEQ_D_KEY_OFF, 0, 60, HG_SEQ_R_STOLEN},
        {2, HG_SEQ_D_STEAL, 1, 72, 0}, {2, HG_SEQ_D_KEY_ON, 1, 72, 0},
        {3, HG_SEQ_D_START, 2, ANY, 0}, {3, HG_SEQ_D_DROP, 2, 48, ANY},
        {4, HG_SEQ_D_KEY_OFF, 1, 72, HG_SEQ_R_TRACK_END}, {4, HG_SEQ_D_TRACK_END, 1, ANY, 0},
        {4, HG_SEQ_D_COMPLETE, 1, ANY, ANY},
        {4, HG_SEQ_D_TRACK_END, 2, ANY, 0}, {4, HG_SEQ_D_COMPLETE, 2, ANY, ANY},
        {7, HG_SEQ_D_START, 3, ANY, 0}, {7, HG_SEQ_D_KEY_ON, 3, 48, 0},
        {8, HG_SEQ_D_KEY_OFF, 3, 48, HG_SEQ_R_TRACK_END}, {8, HG_SEQ_D_TRACK_END, 3, ANY, 0},
        {8, HG_SEQ_D_COMPLETE, 3, ANY, ANY},
        {11, HG_SEQ_D_KEY_OFF, 0, 64, HG_SEQ_R_TRACK_END},
        {11, HG_SEQ_D_KEY_OFF, 0, 67, HG_SEQ_R_TRACK_END},
        {11, HG_SEQ_D_TRACK_END, 0, ANY, 0}, {11, HG_SEQ_D_COMPLETE, 0, ANY, ANY},
    };
    check_timeline(r, e, 24, NOTE_KINDS);
    static const uint32_t voices[5] = {20, 21, 22, 20, 20};
    uint32_t n = 0;
    for (uint32_t i = 0; i < r->seq->log_count; ++i)
        if (r->seq->log[i].kind == HG_SEQ_D_KEY_ON && (n >= 5 || r->seq->log[i].voice != voices[n++]))
            fail(r, C_OWNERSHIP, "exhaustion key-on %u used an unexpected voice", n);
    check_end_state(r);
    return r;
}

/* S5: active cancellation through the existing hg_audio_cancel path. */
static Rig *scenario_cancel(void)
{
    Rig *r = rig_create(POOL4);
    assert(play(r, 0, CUE_HOLD) == 0);
    run(r, 5);
    assert(!r->last_window_silent);
    assert(hg_audio_cancel(&r->bus, 0) == 0);
    run(r, 2);
    static const Expect e[] = {
        {1, HG_SEQ_D_START, 0, ANY, 0}, {1, HG_SEQ_D_KEY_ON, 0, 60, 0},
        {6, HG_SEQ_D_KEY_OFF, 0, 60, HG_SEQ_R_CANCEL}, {6, HG_SEQ_D_CANCEL, 0, ANY, 1},
    };
    check_timeline(r, e, 4, NOTE_KINDS);
    check_end_state(r);
    return r;
}

/* S6: bank release while sounding, rejection of the cleared slot, recovery. */
static Rig *scenario_bank_release(void)
{
    Rig *r = rig_create(POOL4);
    assert(play(r, 0, CUE_HOLD) == 0);
    run(r, 3);
    hg_audio_slot_clear(&r->bus, 0);
    run(r, 1);
    if (play(r, 0, CUE_NOTIFY) != (uint32_t)-4)
        fail(r, C_END_STATE, "cleared slot accepted a request");
    run(r, 1);
    assert(register_bank(r) == 0);            /* same group re-registers into slot 0 */
    r->write_count = 0;
    assert(play(r, 0, CUE_NOTIFY) == 1);
    run(r, 6);
    static const Expect e[] = {
        {1, HG_SEQ_D_START, 0, ANY, 0}, {1, HG_SEQ_D_KEY_ON, 0, 60, 0},
        {4, HG_SEQ_D_KEY_OFF, 0, 60, HG_SEQ_R_LOST}, {4, HG_SEQ_D_LOST, 0, ANY, 1},
        {6, HG_SEQ_D_START, 1, ANY, 0}, {6, HG_SEQ_D_KEY_ON, 1, 72, 0},
        {9, HG_SEQ_D_KEY_OFF, 1, 72, HG_SEQ_R_NOTE_OFF}, {9, HG_SEQ_D_TRACK_END, 1, ANY, 0},
        {9, HG_SEQ_D_COMPLETE, 1, ANY, ANY},
    };
    check_timeline(r, e, 9, NOTE_KINDS);
    if (r->allocs != 2 || r->frees != 2)
        fail(r, C_END_STATE, "staging allocations not released");
    check_end_state(r);
    return r;
}

/* S7: service reset mid-playback, IOP teardown and recreation, fresh request. */
static Rig *scenario_reset(void)
{
    Rig *r = rig_create(POOL4);
    assert(play(r, 0, CUE_HOLD) == 0);
    assert(play(r, 0, CUE_OVERLAP) == 1);
    run(r, 3);
    hg_seq_reset(r->seq);
    r->write_count = 0;
    assert(hg_audio_ee_flush(&r->ee, &r->reply));   /* key-offs staged by the reset */
    check_window(r);
    hg_audio_iop_destroy(r->iop);
    r->iop = NULL;
    r->ee.iop = NULL;
    if (hg_seq_update(r->seq, 1) || hg_seq_owned_voices(r->seq, NULL) != 0)
        fail(r, C_END_STATE, "unbound service accepted an update or kept voices");
    service_up(r);
    hg_audio_slots_init(&r->bus);
    assert(register_bank(r) == 0);
    assert(hg_seq_rebind(r->seq, &r->ee));
    memset(&r->reply, 0, sizeof(r->reply));
    assert(play(r, 0, CUE_NOTIFY) >= 2);
    run(r, 6);
    static const Expect e[] = {
        {1, HG_SEQ_D_START, 0, ANY, 0}, {1, HG_SEQ_D_START, 1, ANY, 0},
        {1, HG_SEQ_D_KEY_ON, 0, 60, 0}, {1, HG_SEQ_D_KEY_ON, 1, 60, 0},
        {3, HG_SEQ_D_KEY_ON, 1, 64, 1},
        {3, HG_SEQ_D_KEY_OFF, 0, 60, HG_SEQ_R_RESET}, {3, HG_SEQ_D_KEY_OFF, 1, 60, HG_SEQ_R_RESET},
        {3, HG_SEQ_D_KEY_OFF, 1, 64, HG_SEQ_R_RESET},
        {3, HG_SEQ_D_LOST, 0, ANY, UINT32_MAX}, {3, HG_SEQ_D_LOST, 1, ANY, UINT32_MAX},
        {4, HG_SEQ_D_START, 2, ANY, 0}, {4, HG_SEQ_D_KEY_ON, 2, 72, 0},
        {7, HG_SEQ_D_KEY_OFF, 2, 72, HG_SEQ_R_NOTE_OFF}, {7, HG_SEQ_D_TRACK_END, 2, ANY, 0},
        {7, HG_SEQ_D_COMPLETE, 2, ANY, ANY},
    };
    check_timeline(r, e, 15, NOTE_KINDS);
    /* After the reset the fresh request starts at the bottom of the pool. */
    for (uint32_t i = 0; i < r->seq->log_count; ++i)
        if (r->seq->log[i].kind == HG_SEQ_D_KEY_ON && r->seq->log[i].request == 2 &&
            r->seq->log[i].voice != 8)
            fail(r, C_OWNERSHIP, "post-reset request did not start at voice 8");
    check_end_state(r);
    return r;
}

/* S8: repeated requests: 40 sequential plus 6 overlapping, beyond the 32
 * command and channel entries, proving retirement and voice reuse. */
static Rig *scenario_repeat(void)
{
    Rig *r = rig_create(POOL4);
    for (uint32_t i = 0; i < 40; ++i) {
        if (play(r, 0, CUE_NOTIFY) >= 32)
            fail(r, C_END_STATE, "request %u refused", i);
        run(r, 5);
    }
    for (uint32_t i = 0; i < 6; ++i) /* one burst into a 4-voice pool */
        if (play(r, 0, CUE_NOTIFY) >= 32)
            fail(r, C_END_STATE, "overlapping request %u refused", i);
    run(r, 6);
    if (count_kind(r, HG_SEQ_D_COMPLETE) + count_kind(r, HG_SEQ_D_DROP) != 46 ||
        count_kind(r, HG_SEQ_D_STEAL) != 2)
        fail(r, C_TIMELINE, "repeat: %u complete, %u steal, %u drop",
             count_kind(r, HG_SEQ_D_COMPLETE), count_kind(r, HG_SEQ_D_STEAL),
             count_kind(r, HG_SEQ_D_DROP));
    check_end_state(r);
    return r;
}

/* S9: malformed inputs are rejected or terminated without stale state. */
static Rig *scenario_malformed(void)
{
    Rig *r = rig_create((HGSeqConfig){8, 4, 8});
    uint32_t cues[5] = {CUE_BAD_MAGIC, CUE_BAD_TRACK, CUE_BAD_OPCODE, CUE_RUNAWAY, CUE_BAD_PITCH};
    for (uint32_t i = 0; i < 5; ++i) {
        assert(play(r, 0, cues[i]) < 32);
        run(r, 3);
    }
    static const Expect e[] = {
        {1, HG_SEQ_D_REJECT, 0, ANY, HG_SEQ_E_HEADER},
        {4, HG_SEQ_D_REJECT, 1, ANY, HG_SEQ_E_BOUNDS},
        {7, HG_SEQ_D_START, 2, ANY, 0}, {7, HG_SEQ_D_KEY_ON, 2, 60, 0},
        {8, HG_SEQ_D_KEY_OFF, 2, 60, HG_SEQ_R_TRACK_END}, {8, HG_SEQ_D_TRACK_END, 2, ANY, HG_SEQ_E_OPCODE},
        {8, HG_SEQ_D_COMPLETE, 2, ANY, ANY},
        {10, HG_SEQ_D_START, 3, ANY, 0},
        {10, HG_SEQ_D_VOLUME, 3, ANY, 100}, {10, HG_SEQ_D_VOLUME, 3, ANY, 100},
        {10, HG_SEQ_D_VOLUME, 3, ANY, 100}, {10, HG_SEQ_D_VOLUME, 3, ANY, 100},
        {10, HG_SEQ_D_VOLUME, 3, ANY, 100}, {10, HG_SEQ_D_VOLUME, 3, ANY, 100},
        {10, HG_SEQ_D_VOLUME, 3, ANY, 100}, {10, HG_SEQ_D_VOLUME, 3, ANY, 100},
        {10, HG_SEQ_D_TRACK_END, 3, ANY, HG_SEQ_E_RUNAWAY}, {10, HG_SEQ_D_COMPLETE, 3, ANY, ANY},
        {13, HG_SEQ_D_START, 4, ANY, 0}, {13, HG_SEQ_D_STAGE_FAIL, 4, 120, 1},
        {14, HG_SEQ_D_TRACK_END, 4, ANY, HG_SEQ_E_OPERAND}, {14, HG_SEQ_D_COMPLETE, 4, ANY, ANY},
    };
    check_timeline(r, e, 22, NOTE_KINDS);
    check_end_state(r);
    /* Unbound service and invalid configurations fail closed. */
    HGSequencer *spare = calloc(1, sizeof(*spare));
    HGAudioEeService unbound = {0};
    assert(spare);
    if (hg_seq_init(spare, &unbound, (HGSeqConfig){40, 9, 0}) ||
        !hg_seq_init(spare, &unbound, (HGSeqConfig){0, 1, 0}) || hg_seq_update(spare, 1) ||
        hg_seq_update(NULL, 1))
        fail(r, C_END_STATE, "invalid configuration or unbound service accepted");
    free(spare);
    return r;
}

static bool passed(const Rig *r) { return r->fail_count == 0; }

int main(void)
{
    bool ok = true;
    printf("{\n  \"format\": \"hg-m24-sequencer/1\",\n  \"frames_per_update\": %u,\n"
           "  \"scenarios\": [", FRAMES);

    /* Positive scenarios, each run twice for determinism. */
    Rig *(*const scenarios[])(void) = {
        scenario_exhaustion, scenario_cancel, scenario_bank_release, scenario_reset,
        scenario_repeat, scenario_malformed};
    const char *names[] = {"exhaustion-steal-reuse", "active-cancel", "bank-release",
                           "service-reset", "repeated-requests", "malformed-input"};
    Rig *connected = NULL;
    for (int pass = 0; pass < 3; ++pass) {
        Rig *a = pass == 0 ? scenario_notify(NONE) : pass == 1 ? scenario_timed(NONE)
                                                               : scenario_overlap(NONE, 0);
        Rig *b = pass == 0 ? scenario_notify(NONE) : pass == 1 ? scenario_timed(NONE)
                                                               : scenario_overlap(NONE, 0);
        if (a->pcm_hash != b->pcm_hash || a->seq->log_count != b->seq->log_count ||
            memcmp(a->seq->log, b->seq->log, a->seq->log_count * sizeof(*a->seq->log)))
            fail(a, C_DETERMINISM, "repeat run differs");
        emit(pass == 0 ? "short-notification" : pass == 1 ? "timed-events" : "overlapping-channels",
             "positive", a, 0);
        ok &= passed(a);
        if (pass == 1)
            connected = a;
        else
            rig_destroy(a);
        rig_destroy(b);
    }
    for (size_t i = 0; i < sizeof(scenarios) / sizeof(*scenarios); ++i) {
        Rig *a = scenarios[i](), *b = scenarios[i]();
        if (a->pcm_hash != b->pcm_hash || a->seq->log_count != b->seq->log_count ||
            memcmp(a->seq->log, b->seq->log, a->seq->log_count * sizeof(*a->seq->log)))
            fail(a, C_DETERMINISM, "repeat run differs");
        emit(names[i], "positive", a, 0);
        ok &= passed(a);
        rig_destroy(a);
        rig_destroy(b);
    }

    /* Negative controls: completed run, expected checker divergence. */
    struct { const char *name; Rig *r; uint32_t check; } controls[5];
    controls[0].name = "nc-missing-event-advancement";
    controls[0].r = scenario_timed((Faults){1, 4, -1, false, false});
    controls[0].check = C_TIMELINE;
    controls[1].name = "nc-dropped-service-requests";
    controls[1].r = scenario_notify((Faults){-1, -1, -1, true, false});
    controls[1].check = C_KEY_ON;
    controls[2].name = "nc-missing-key-off";
    controls[2].r = scenario_notify((Faults){-1, -1, -1, false, true});
    controls[2].check = C_KEY_OFF;
    controls[3].name = "nc-foreign-key-off";
    controls[3].r = scenario_overlap((Faults){-1, -1, 2, false, false}, 8);
    controls[3].check = C_KEY_OFF;
    controls[4].name = "nc-ownership-log-mutation";
    controls[4].r = scenario_overlap(NONE, 0);
    controls[4].check = C_OWNERSHIP;
    {
        /* Declared checker-sensitivity control: re-audit a mutated copy of the
         * decision stream in which request 1's first note-off names request 0. */
        Rig *r = controls[4].r;
        memset(r->owner, 0, sizeof(r->owner));
        for (uint32_t i = 0; i < r->seq->log_count; ++i)
            if (r->seq->log[i].kind == HG_SEQ_D_KEY_OFF && r->seq->log[i].request == 1) {
                r->seq->log[i].request = 0;
                break;
            }
        r->checked = 0;
        r->write_count = 0;
        uint32_t saved = r->seq->log_count;
        /* Ownership-only audit: SPU windows are not replayed. */
        for (uint32_t i = 0; i < saved; ++i) {
            const HGSeqDecision *d = &r->seq->log[i];
            if (d->kind == HG_SEQ_D_KEY_ON) {
                r->owner[d->voice].owned = true;
                r->owner[d->voice].request = d->request;
                r->owner[d->voice].track = d->track;
                r->owner[d->voice].note = d->note;
            } else if (d->kind == HG_SEQ_D_KEY_OFF) {
                if (!r->owner[d->voice].owned || r->owner[d->voice].request != d->request)
                    fail(r, C_OWNERSHIP, "voice %u released by request %u, not its owner",
                         d->voice, d->request);
                r->owner[d->voice].owned = false;
            }
        }
    }
    for (int i = 0; i < 5; ++i) {
        emit(controls[i].name, i == 4 ? "control-log-mutation" : "control-fault-injection",
             controls[i].r, controls[i].check);
        ok &= has_fail(controls[i].r, controls[i].check);
        rig_destroy(controls[i].r);
    }
    printf("\n  ]");
    emit_log(connected);
    printf(",\n  \"ok\": %s\n}\n", ok ? "true" : "false");
    rig_destroy(connected);
    return ok ? 0 : 1;
}
