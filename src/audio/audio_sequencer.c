/* M24 synthetic sequence processor and physical voice manager.
 * Original host model; see audio_sequencer.h and docs/M24.md. The retail
 * sequencer and allocator are mapped in docs/M24.md but not reproduced. */
#include "audio_sequencer.h"

#include <string.h>

#define GUEST_LIMIT 0x02000000u /* 32 MiB EE main RAM */
#define DEFAULT_EVENT_BUDGET 256u
#define STAGE_PLAY 1u
#define STAGE_KEY_ON 2u
#define STAGE_KEY_OFF 3u
#define STAGE_GAIN 4u
#define STAGE_PITCH 5u

typedef struct Tone {
    uint32_t offset, root, scale, volume, pan;
    int8_t fine;
    uint16_t adsr1, adsr2;
} Tone;

static bool bound(const HGSequencer *seq)
{
    return seq && seq->service && seq->service->bus && seq->service->iop;
}

static HGAudioBus *bus_of(const HGSequencer *seq)
{
    return seq->service->bus;
}

static uint32_t r32(const HGSequencer *seq, uint32_t address)
{
    HGAudioBus *bus = bus_of(seq);
    return bus->r32(bus->ctx, address);
}

static void w32(const HGSequencer *seq, uint32_t address, uint32_t value)
{
    HGAudioBus *bus = bus_of(seq);
    bus->w32(bus->ctx, address, value);
}

static uint32_t command_address(uint32_t index)
{
    return HG_AUDIO_COMMAND_BASE + index * HG_CMD_STRIDE;
}

static void record(HGSequencer *seq, uint32_t kind, const HGSeqInstance *inst,
                   uint32_t track, uint32_t note, uint32_t voice, uint32_t detail)
{
    if (seq->log_count >= HG_SEQ_LOG_CAPACITY) {
        seq->log_overflow++;
        return;
    }
    seq->log[seq->log_count++] = (HGSeqDecision){
        seq->tick, kind, inst ? inst->request : UINT32_MAX,
        inst ? inst->play_index : UINT32_MAX, track, note, voice, detail};
}

/* ---- bounded reads of the HSQ1 resource ------------------------------- */

static bool read8(const HGSequencer *seq, const HGSeqInstance *inst,
                  uint32_t offset, uint32_t *out)
{
    if (offset >= inst->size)
        return false;
    HGAudioBus *bus = bus_of(seq);
    *out = bus->r8(bus->ctx, inst->base + offset);
    return true;
}

static bool read16(const HGSequencer *seq, const HGSeqInstance *inst,
                   uint32_t offset, uint32_t *out)
{
    uint32_t lo, hi;
    if (!read8(seq, inst, offset, &lo) || !read8(seq, inst, offset + 1, &hi))
        return false;
    *out = lo | hi << 8;
    return true;
}

static Tone read_tone(const HGSequencer *seq, const HGSeqInstance *inst, uint32_t index)
{
    /* Bounds were validated when the request was adopted. */
    uint32_t at = inst->tones + index * HG_SEQ_TONE_SIZE, v[12];
    for (uint32_t i = 0; i < 12; ++i)
        read8(seq, inst, at + i, &v[i]);
    return (Tone){
        .offset = v[0] | v[1] << 8, .root = v[2], .fine = (int8_t)v[3],
        .adsr1 = (uint16_t)(v[4] | v[5] << 8), .adsr2 = (uint16_t)(v[6] | v[7] << 8),
        .scale = v[8] | v[9] << 8, .volume = v[10], .pan = v[11]};
}

/* ---- voice ownership --------------------------------------------------- */

static bool release_voice(HGSequencer *seq, uint32_t voice, uint32_t reason)
{
    HGSeqVoice *v = &seq->voice[voice];
    const HGSeqInstance *inst = v->instance < HG_SEQ_MAX_INSTANCES &&
        seq->instance[v->instance].active &&
        seq->instance[v->instance].request == v->request
        ? &seq->instance[v->instance] : NULL;
    HGSeqInstance shadow = {.request = v->request, .play_index = v->instance};
    if (!inst)
        inst = &shadow;
    v->owned = false;
    if (!hg_audio_ee_stage_key(seq->service, voice, false)) {
        seq->refused_calls++;
        if (!v->off_pending)
            record(seq, HG_SEQ_D_STAGE_FAIL, inst, v->track, v->note, voice, STAGE_KEY_OFF);
        v->off_pending = true;
        v->pending_reason = reason;
        return false;
    }
    seq->staged_calls++;
    record(seq, HG_SEQ_D_KEY_OFF, inst, v->track, v->note, voice, reason);
    memset(v, 0, sizeof(*v));
    return true;
}

static void release_matching(HGSequencer *seq, const HGSeqInstance *inst,
                             uint32_t track, uint32_t reason)
{
    for (uint32_t voice = 0; voice < HG_SG2_VOICES; ++voice) {
        const HGSeqVoice *v = &seq->voice[voice];
        if (v->owned && v->request == inst->request &&
            (track == UINT32_MAX || v->track == track))
            release_voice(seq, voice, reason);
    }
}

static void voice_gain(const Tone *tone, uint32_t velocity, uint32_t volume,
                       uint32_t *left, uint32_t *right)
{
    uint32_t amp = (uint32_t)((uint64_t)0x3fffu * velocity * volume * tone->volume /
                              (127u * 127u * 127u));
    uint32_t pan = tone->pan > 127 ? 127 : tone->pan;
    *left = amp * (127 - pan < 64 ? 127 - pan : 64) / 64;
    *right = amp * (pan < 64 ? pan : 64) / 64;
}

/* Allocation policy (original, M24): the lowest-numbered pool voice that is
 * neither owned nor awaiting a staged key-off. When none is free, the owned
 * voice with the lowest priority is stolen, oldest start first, then lowest
 * request, then lowest voice number, provided its priority does not exceed
 * the requester's. Otherwise the note is dropped. */
static uint32_t acquire_voice(HGSequencer *seq, const HGSeqInstance *inst,
                              uint32_t track, uint32_t note)
{
    uint32_t victim = UINT32_MAX;
    for (uint32_t voice = seq->config.voice_first;
         voice < seq->config.voice_first + seq->config.voice_count; ++voice) {
        const HGSeqVoice *v = &seq->voice[voice];
        if (!v->owned && !v->off_pending) {
            record(seq, HG_SEQ_D_ALLOC, inst, track, note, voice, 0);
            return voice;
        }
        if (!v->owned)
            continue;
        if (victim == UINT32_MAX)
            victim = voice;
        else {
            const HGSeqVoice *b = &seq->voice[victim];
            if (v->priority < b->priority ||
                (v->priority == b->priority &&
                 (v->start_tick < b->start_tick ||
                  (v->start_tick == b->start_tick && v->request < b->request))))
                victim = voice;
        }
    }
    if (victim != UINT32_MAX && seq->voice[victim].priority <= inst->priority) {
        uint32_t loser = seq->voice[victim].request;
        if (release_voice(seq, victim, HG_SEQ_R_STOLEN)) {
            record(seq, HG_SEQ_D_STEAL, inst, track, note, victim, loser);
            return victim;
        }
    }
    record(seq, HG_SEQ_D_DROP, inst, track, note, UINT32_MAX, 0);
    return UINT32_MAX;
}

static void note_on(HGSequencer *seq, HGSeqInstance *inst, uint32_t track,
                    uint32_t note, uint32_t velocity, uint32_t tone_index,
                    uint32_t event_tick, uint32_t duration)
{
    Tone tone = read_tone(seq, inst, tone_index);
    uint32_t voice = acquire_voice(seq, inst, track, note);
    if (voice == UINT32_MAX)
        return;
    HGSeqTrack *t = &inst->track[track];
    uint32_t left, right;
    voice_gain(&tone, velocity, t->volume, &left, &right);
    HGSg2VoiceParams params = {
        .note = note, .root = tone.root, .scale = tone.scale,
        .left = left, .right = right, .adsr1 = tone.adsr1, .adsr2 = tone.adsr2,
        .fine_offset = tone.fine, .bend = t->bend, .bend_scale = HG_SEQ_BEND_SCALE};
    if (!hg_audio_ee_stage_play_voice(seq->service, inst->play_index, voice,
                                      tone.offset, params)) {
        seq->refused_calls++;
        record(seq, HG_SEQ_D_STAGE_FAIL, inst, track, note, voice, STAGE_PLAY);
        return;
    }
    if (!hg_audio_ee_stage_key(seq->service, voice, true)) {
        seq->refused_calls++;
        record(seq, HG_SEQ_D_STAGE_FAIL, inst, track, note, voice, STAGE_KEY_ON);
        return;
    }
    seq->staged_calls += 2;
    seq->voice[voice] = (HGSeqVoice){
        .owned = true, .request = inst->request, .instance = inst->play_index,
        .track = track, .note = note, .start_tick = event_tick,
        .end_tick = duration ? event_tick + duration : 0,
        .priority = inst->priority, .tone = tone_index, .velocity = velocity,
        .params = params};
    record(seq, HG_SEQ_D_KEY_ON, inst, track, note, voice, tone_index);
}

static void note_off(HGSequencer *seq, const HGSeqInstance *inst, uint32_t track,
                     uint32_t note)
{
    uint32_t newest = UINT32_MAX;
    for (uint32_t voice = 0; voice < HG_SG2_VOICES; ++voice) {
        const HGSeqVoice *v = &seq->voice[voice];
        if (v->owned && v->request == inst->request && v->track == track &&
            v->note == note &&
            (newest == UINT32_MAX || v->start_tick >= seq->voice[newest].start_tick))
            newest = voice;
    }
    if (newest != UINT32_MAX)
        release_voice(seq, newest, HG_SEQ_R_NOTE_OFF);
}

static void update_track_voices(HGSequencer *seq, HGSeqInstance *inst,
                                uint32_t track, bool gain)
{
    for (uint32_t voice = 0; voice < HG_SG2_VOICES; ++voice) {
        HGSeqVoice *v = &seq->voice[voice];
        if (!v->owned || v->request != inst->request || v->track != track)
            continue;
        bool ok;
        if (gain) {
            Tone tone = read_tone(seq, inst, v->tone);
            voice_gain(&tone, v->velocity, inst->track[track].volume,
                       &v->params.left, &v->params.right);
            ok = hg_audio_ee_stage_gain(seq->service, voice, v->params.left,
                                        v->params.right);
        } else {
            v->params.bend = inst->track[track].bend;
            ok = hg_audio_ee_stage_pitch(seq->service, voice, v->params);
        }
        if (ok)
            seq->staged_calls++;
        else {
            seq->refused_calls++;
            record(seq, HG_SEQ_D_STAGE_FAIL, inst, track, v->note, voice,
                   gain ? STAGE_GAIN : STAGE_PITCH);
        }
    }
}

/* ---- request lifetime -------------------------------------------------- */

static void retire(HGSequencer *seq, HGSeqInstance *inst, uint32_t kind, uint32_t detail)
{
    uint32_t command = command_address(inst->play_index);
    if (r32(seq, command + HG_CMD_SEQ) == inst->command_seq &&
        r32(seq, command + HG_CMD_STATE) != 0) {
        w32(seq, command + HG_CMD_SLOT, UINT32_MAX);
        w32(seq, command + HG_CMD_STATE, 0);
    }
    if (inst->channel < 32) {
        uint32_t channel = HG_AUDIO_CHANNEL_BASE + inst->channel * HG_CH_STRIDE;
        if (r32(seq, channel) == inst->slot)
            w32(seq, channel, UINT32_MAX);
    }
    record(seq, kind, inst, UINT32_MAX, UINT32_MAX, UINT32_MAX, detail);
    memset(inst, 0, sizeof(*inst));
}

static uint32_t ring_index(const HGSequencer *seq, uint32_t channel)
{
    return r32(seq, HG_AUDIO_CHANNEL_QUEUE + channel * HG_CH_STRIDE + HG_CH_CMD_RING) & 0x1ffu;
}

/* Consume the channel bytes queued since adoption. hg_audio_cancel (002ad748)
 * appends ff 41 f7; report whether that marker was present. */
static uint32_t drain_channel(HGSequencer *seq, HGSeqInstance *inst)
{
    if (inst->channel >= 32)
        return 0;
    HGAudioBus *bus = bus_of(seq);
    uint32_t queue = HG_AUDIO_CHANNEL_QUEUE + inst->channel * HG_CH_STRIDE;
    uint32_t end = ring_index(seq, inst->channel), marker = 0, seen = 0;
    static const uint8_t cancel[3] = {0xff, 0x41, 0xf7};
    for (uint32_t at = inst->ring_read; at != end; at = (at + 1) & 0x1ffu) {
        uint8_t byte = bus->r8(bus->ctx, queue + at);
        marker = byte == cancel[marker] ? marker + 1 : byte == cancel[0];
        if (marker == 3) {
            seen = 1;
            marker = 0;
        }
    }
    inst->ring_read = end;
    return seen;
}

static uint32_t adopt(HGSequencer *seq, HGSeqInstance *inst, uint32_t index)
{
    uint32_t command = command_address(index);
    uint32_t base = r32(seq, command + HG_CMD_SAMPLE);
    uint32_t slot = r32(seq, command + HG_CMD_SLOT) & 7u;
    *inst = (HGSeqInstance){
        .active = true, .request = seq->next_request++, .play_index = index,
        .command_seq = r32(seq, command + HG_CMD_SEQ), .slot = slot,
        .bank = r32(seq, HG_AUDIO_SLOT_TABLE + slot * HG_SLOT_STRIDE + HG_SLOT_BANK2),
        .channel = r32(seq, command + HG_CMD_CHANNEL), .base = base,
        .size = HG_SEQ_HEADER_SIZE, .start_tick = seq->tick};
    if (inst->channel < 32)
        inst->ring_read = ring_index(seq, inst->channel);

    uint32_t magic, version = 0, tracks = 0, tones = 0, priority = 0, table = 0, size = 0;
    if (r32(seq, command + HG_CMD_SLOT) >= 8)
        return HG_SEQ_E_HEADER;
    if (!base || base >= GUEST_LIMIT || GUEST_LIMIT - base < HG_SEQ_HEADER_SIZE)
        return HG_SEQ_E_BOUNDS;
    magic = r32(seq, base);
    read8(seq, inst, 4, &version);
    read8(seq, inst, 5, &tracks);
    read8(seq, inst, 6, &tones);
    read8(seq, inst, 7, &priority);
    read16(seq, inst, 8, &table);
    read16(seq, inst, 10, &size);
    if (magic != HG_SEQ_MAGIC || version != HG_SEQ_VERSION ||
        !tracks || tracks > HG_SEQ_MAX_TRACKS || !tones || tones > HG_SEQ_MAX_TONES)
        return HG_SEQ_E_HEADER;
    if (size < HG_SEQ_HEADER_SIZE || size > HG_SEQ_MAX_SIZE ||
        GUEST_LIMIT - base < size || table < HG_SEQ_HEADER_SIZE ||
        table + tones * HG_SEQ_TONE_SIZE > size)
        return HG_SEQ_E_BOUNDS;
    inst->size = size;
    inst->tones = table;
    inst->tone_count = tones;
    inst->track_count = tracks;
    inst->priority = priority;
    for (uint32_t t = 0; t < tracks; ++t) {
        uint32_t offset;
        read16(seq, inst, 12 + 2 * t, &offset);
        if (offset < HG_SEQ_HEADER_SIZE || offset >= size)
            return HG_SEQ_E_BOUNDS;
        inst->track[t] = (HGSeqTrack){.active = true, .cursor = offset,
                                      .volume = 127, .bend = 64};
    }
    for (uint32_t t = 0; t < tones; ++t) {
        Tone tone = read_tone(seq, inst, t);
        if (tone.offset % 16 || tone.scale > 0x3fff || tone.volume > 127 || tone.pan > 127)
            return HG_SEQ_E_OPERAND;
    }
    return HG_SEQ_E_NONE;
}

static void end_track(HGSequencer *seq, HGSeqInstance *inst, uint32_t track, uint32_t error)
{
    release_matching(seq, inst, track, HG_SEQ_R_TRACK_END);
    inst->track[track].active = false;
    record(seq, HG_SEQ_D_TRACK_END, inst, track, UINT32_MAX, UINT32_MAX, error);
}

/* Execute one event; returns an HGSeqError (0 on success). */
static uint32_t step_event(HGSequencer *seq, HGSeqInstance *inst, uint32_t track)
{
    HGSeqTrack *t = &inst->track[track];
    uint32_t at = t->cursor, op, a = 0, b = 0, c = 0, d = 0;
    uint32_t event_tick = seq->tick - (uint32_t)(-t->wait);
    if (!read8(seq, inst, at, &op))
        return HG_SEQ_E_BOUNDS;
    switch (op) {
    case HG_SEQ_OP_END:
        end_track(seq, inst, track, HG_SEQ_E_NONE);
        return HG_SEQ_E_NONE;
    case HG_SEQ_OP_WAIT:
        if (!read16(seq, inst, at + 1, &a))
            return HG_SEQ_E_BOUNDS;
        if (!a)
            return HG_SEQ_E_OPERAND;
        t->wait += (int32_t)a;
        t->cursor = at + 3;
        return HG_SEQ_E_NONE;
    case HG_SEQ_OP_NOTE_ON:
    case HG_SEQ_OP_NOTE:
        if (!read8(seq, inst, at + 1, &a) || !read8(seq, inst, at + 2, &b) ||
            !read8(seq, inst, at + 3, &c) ||
            (op == HG_SEQ_OP_NOTE && !read16(seq, inst, at + 4, &d)))
            return HG_SEQ_E_BOUNDS;
        if (a > 127 || !b || b > 127 || c >= inst->tone_count ||
            (op == HG_SEQ_OP_NOTE && !d))
            return HG_SEQ_E_OPERAND;
        t->cursor = at + (op == HG_SEQ_OP_NOTE ? 6 : 4);
        note_on(seq, inst, track, a, b, c, event_tick, d);
        return HG_SEQ_E_NONE;
    case HG_SEQ_OP_NOTE_OFF:
        if (!read8(seq, inst, at + 1, &a))
            return HG_SEQ_E_BOUNDS;
        if (a > 127)
            return HG_SEQ_E_OPERAND;
        t->cursor = at + 2;
        note_off(seq, inst, track, a);
        return HG_SEQ_E_NONE;
    case HG_SEQ_OP_VOLUME:
    case HG_SEQ_OP_BEND:
        if (!read8(seq, inst, at + 1, &a))
            return HG_SEQ_E_BOUNDS;
        if (a > 127)
            return HG_SEQ_E_OPERAND;
        t->cursor = at + 2;
        if (op == HG_SEQ_OP_VOLUME)
            t->volume = (uint8_t)a;
        else
            t->bend = (uint8_t)a;
        record(seq, op == HG_SEQ_OP_VOLUME ? HG_SEQ_D_VOLUME : HG_SEQ_D_BEND,
               inst, track, UINT32_MAX, UINT32_MAX, a);
        update_track_voices(seq, inst, track, op == HG_SEQ_OP_VOLUME);
        return HG_SEQ_E_NONE;
    default:
        return HG_SEQ_E_OPCODE;
    }
}

static void run_tracks(HGSequencer *seq, HGSeqInstance *inst)
{
    uint32_t budget = seq->config.events_per_update ? seq->config.events_per_update
                                                    : DEFAULT_EVENT_BUDGET;
    for (uint32_t track = 0; track < inst->track_count && inst->active; ++track) {
        HGSeqTrack *t = &inst->track[track];
        uint32_t events = 0;
        while (t->active && t->wait <= 0) {
            uint32_t error = ++events > budget ? HG_SEQ_E_RUNAWAY
                                               : step_event(seq, inst, track);
            if (error) {
                end_track(seq, inst, track, error);
                break;
            }
        }
    }
    bool running = false;
    for (uint32_t track = 0; track < inst->track_count; ++track)
        running |= inst->track[track].active;
    if (!running)
        retire(seq, inst, HG_SEQ_D_COMPLETE, 0);
}

/* ---- public API -------------------------------------------------------- */

bool hg_seq_init(HGSequencer *seq, HGAudioEeService *service, HGSeqConfig config)
{
    if (!seq || !config.voice_count || config.voice_first >= HG_SG2_VOICES ||
        config.voice_count > HG_SG2_VOICES - config.voice_first)
        return false;
    memset(seq, 0, sizeof(*seq));
    seq->service = service;
    seq->config = config;
    return true;
}

bool hg_seq_update(HGSequencer *seq, uint32_t ticks)
{
    if (!bound(seq))
        return false;
    seq->tick += ticks;

    /* 1. Existing requests: advance waits, then retire cancelled/lost ones. */
    for (uint32_t i = 0; i < HG_SEQ_MAX_INSTANCES; ++i) {
        HGSeqInstance *inst = &seq->instance[i];
        if (!inst->active)
            continue;
        for (uint32_t t = 0; t < inst->track_count; ++t)
            if (inst->track[t].active)
                inst->track[t].wait -= (int32_t)ticks;
        uint32_t command = command_address(inst->play_index);
        uint32_t state = r32(seq, command + HG_CMD_STATE);
        uint32_t current_seq = r32(seq, command + HG_CMD_SEQ);
        uint32_t slot_bank = r32(seq, HG_AUDIO_SLOT_TABLE + inst->slot * HG_SLOT_STRIDE +
                                 HG_SLOT_BANK2);
        if (current_seq == inst->command_seq && state == 2) {
            uint32_t marker = drain_channel(seq, inst);
            release_matching(seq, inst, UINT32_MAX, HG_SEQ_R_CANCEL);
            retire(seq, inst, HG_SEQ_D_CANCEL, marker);
        } else if (current_seq != inst->command_seq || state != 1 ||
                   !hg_audio_slot_active(bus_of(seq), inst->slot) ||
                   slot_bank != inst->bank) {
            release_matching(seq, inst, UINT32_MAX, HG_SEQ_R_LOST);
            retire(seq, inst, HG_SEQ_D_LOST, state);
        }
    }

    /* 2. Retry key-offs the service refused earlier. */
    for (uint32_t voice = 0; voice < HG_SG2_VOICES; ++voice)
        if (seq->voice[voice].off_pending)
            release_voice(seq, voice, seq->voice[voice].pending_reason);

    /* 3. Adopt new play commands in submission (HG_CMD_SEQ) order. */
    for (;;) {
        uint32_t pick = UINT32_MAX, best = 0;
        for (uint32_t i = 0; i < HG_SEQ_MAX_INSTANCES; ++i) {
            uint32_t command = command_address(i);
            if (r32(seq, command + HG_CMD_STATE) != 1 ||
                (seq->instance[i].active &&
                 seq->instance[i].command_seq == r32(seq, command + HG_CMD_SEQ)))
                continue;
            uint32_t order = r32(seq, command + HG_CMD_SEQ);
            if (pick == UINT32_MAX || order < best) {
                pick = i;
                best = order;
            }
        }
        if (pick == UINT32_MAX)
            break;
        HGSeqInstance *inst = &seq->instance[pick];
        uint32_t error = adopt(seq, inst, pick);
        if (error)
            retire(seq, inst, HG_SEQ_D_REJECT, error);
        else
            record(seq, HG_SEQ_D_START, inst, UINT32_MAX, UINT32_MAX, UINT32_MAX,
                   inst->slot);
    }

    /* 4. Expire note durations before same-tick events so voices can be reused. */
    for (uint32_t voice = 0; voice < HG_SG2_VOICES; ++voice) {
        const HGSeqVoice *v = &seq->voice[voice];
        if (v->owned && v->end_tick && v->end_tick <= seq->tick)
            release_voice(seq, voice, HG_SEQ_R_DURATION);
    }

    /* 5. Consume every due event, requests in adoption order. */
    for (;;) {
        uint32_t pick = UINT32_MAX;
        for (uint32_t i = 0; i < HG_SEQ_MAX_INSTANCES; ++i) {
            const HGSeqInstance *inst = &seq->instance[i];
            if (!inst->active)
                continue;
            bool due = false;
            for (uint32_t t = 0; t < inst->track_count; ++t)
                due |= inst->track[t].active && inst->track[t].wait <= 0;
            if (due && (pick == UINT32_MAX || inst->request < seq->instance[pick].request))
                pick = i;
        }
        if (pick == UINT32_MAX)
            break;
        run_tracks(seq, &seq->instance[pick]);
    }
    return true;
}

void hg_seq_reset(HGSequencer *seq)
{
    if (!seq)
        return;
    bool live = bound(seq);
    if (live)
        for (uint32_t voice = 0; voice < HG_SG2_VOICES; ++voice)
            if (seq->voice[voice].owned || seq->voice[voice].off_pending)
                release_voice(seq, voice, HG_SEQ_R_RESET);
    if (seq->service && seq->service->bus)
        for (uint32_t i = 0; i < HG_SEQ_MAX_INSTANCES; ++i)
            if (seq->instance[i].active)
                retire(seq, &seq->instance[i], HG_SEQ_D_LOST, UINT32_MAX);
    memset(seq->instance, 0, sizeof(seq->instance));
    memset(seq->voice, 0, sizeof(seq->voice));
    record(seq, HG_SEQ_D_RESET, NULL, UINT32_MAX, UINT32_MAX, UINT32_MAX, live);
}

bool hg_seq_rebind(HGSequencer *seq, HGAudioEeService *service)
{
    if (!seq)
        return false;
    hg_seq_reset(seq);
    seq->service = service;
    return bound(seq);
}

uint32_t hg_seq_active_requests(const HGSequencer *seq)
{
    uint32_t count = 0;
    for (uint32_t i = 0; seq && i < HG_SEQ_MAX_INSTANCES; ++i)
        count += seq->instance[i].active;
    return count;
}

uint32_t hg_seq_owned_voices(const HGSequencer *seq, uint32_t mask[2])
{
    uint32_t count = 0;
    if (mask)
        mask[0] = mask[1] = 0;
    for (uint32_t voice = 0; seq && voice < HG_SG2_VOICES; ++voice)
        if (seq->voice[voice].owned || seq->voice[voice].off_pending) {
            count++;
            if (mask)
                mask[voice / 24] |= 1u << (voice % 24);
        }
    return count;
}
