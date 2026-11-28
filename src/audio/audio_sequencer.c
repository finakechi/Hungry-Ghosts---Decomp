/* M24/M26 synthetic sequence processor and physical voice manager.
 * Original host model; see audio_sequencer.h, docs/M24.md and docs/M26.md.
 * The retail sequencer and allocator are mapped in docs/M24.md and docs/M26.md
 * but not reproduced. */
#include "audio_sequencer.h"

#include <string.h>

#define GUEST_LIMIT 0x02000000u /* 32 MiB EE main RAM */
#define DEFAULT_EVENT_BUDGET 256u
#define MAX_CHRONO_TICKS_PER_UPDATE 4096u
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

static uint32_t ee_ring_cursor(const HGSequencer *seq)
{
    return bound(seq) ? r32(seq, HG_SG2_EE_CURSOR) : 0;
}

static void record_timed(HGSequencer *seq, uint64_t due_tick, uint32_t kind,
                         const HGSeqInstance *inst, uint32_t track, uint32_t note,
                         uint32_t voice, uint32_t detail,
                         uint32_t ring_start, uint32_t ring_count)
{
    uint32_t order = seq->total_decisions++;
    if (seq->log_count >= HG_SEQ_LOG_CAPACITY) {
        seq->log_overflow++;
        return;
    }
    uint32_t idx = seq->log_count++;
    uint32_t legacy_tick = seq->timing.timing_mode == HG_SEQ_TIMING_CHRONO
        ? (uint32_t)due_tick : seq->tick;
    seq->log[idx] = (HGSeqDecision){
        legacy_tick, kind, inst ? inst->request : UINT32_MAX,
        inst ? inst->play_index : UINT32_MAX, track, note, voice, detail};
    seq->timing_log[idx] = (HGSeqDecisionTiming){
        .due_tick = due_tick,
        .service_tick = seq->clock.elapsed_ticks,
        .request_tick = inst ? inst->request_tick : UINT64_MAX,
        .service_seq = seq->clock.service_updates,
        .order_seq = order,
        .ring_start = ring_start,
        .ring_count = ring_count};
}

static void record(HGSequencer *seq, uint32_t kind, const HGSeqInstance *inst,
                   uint32_t track, uint32_t note, uint32_t voice, uint32_t detail)
{
    record_timed(seq, seq->clock.elapsed_ticks, kind, inst, track, note, voice,
                 detail, ee_ring_cursor(seq), 0);
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

static const HGSeqInstance *voice_owner_or_shadow(const HGSequencer *seq,
                                                  const HGSeqVoice *v,
                                                  HGSeqInstance *shadow)
{
    if (v->instance < HG_SEQ_MAX_INSTANCES &&
        seq->instance[v->instance].active &&
        seq->instance[v->instance].request == v->request)
        return &seq->instance[v->instance];
    *shadow = (HGSeqInstance){
        .request = v->request,
        .play_index = v->instance,
        .request_tick = UINT64_MAX};
    return shadow;
}

static bool voice_is_idle(const HGSeqVoice *v)
{
    return !v->owned && !v->releasing && !v->off_pending;
}

static bool request_has_voices(const HGSequencer *seq, uint32_t request)
{
    for (uint32_t voice = 0; voice < HG_SG2_VOICES; ++voice) {
        const HGSeqVoice *v = &seq->voice[voice];
        if ((v->owned || v->releasing || v->off_pending) && v->request == request)
            return true;
    }
    return false;
}

static bool release_voice_at(HGSequencer *seq, uint32_t voice, uint32_t reason,
                             uint64_t due_tick, bool force_immediate)
{
    HGSeqVoice *v = &seq->voice[voice];
    HGSeqInstance shadow;
    const HGSeqInstance *inst = voice_owner_or_shadow(seq, v, &shadow);
    uint32_t ring_before = ee_ring_cursor(seq);

    /* If already releasing and force_immediate (e.g. reset), just retire without
     * staging a duplicate KEY_OFF (KEY_OFF was already staged on release entry). */
    if (v->releasing && !v->owned && !v->off_pending) {
        if (force_immediate) {
            record_timed(seq, due_tick, HG_SEQ_D_VOICE_RETIRE, inst, v->track,
                         v->note, voice, v->release_reason, ring_before, 0);
            memset(v, 0, sizeof(*v));
        }
        return true;
    }

    v->owned = false;
    if (!hg_audio_ee_stage_key(seq->service, voice, false)) {
        seq->refused_calls++;
        if (!v->off_pending)
            record_timed(seq, due_tick, HG_SEQ_D_STAGE_FAIL, inst, v->track,
                         v->note, voice, STAGE_KEY_OFF, ring_before, 0);
        v->off_pending = true;
        v->pending_reason = reason;
        return false;
    }
    seq->staged_calls++;
    uint32_t ring_after = ee_ring_cursor(seq);
    uint32_t ring_delta = ring_after >= ring_before ? ring_after - ring_before : 0;
    record_timed(seq, due_tick, HG_SEQ_D_KEY_OFF, inst, v->track, v->note, voice,
                 reason, ring_before, ring_delta);

    /* Deferred exclusive release: when configured (> 0 hold ticks) and not a
     * forced steal/reset, keep exclusive ownership until release_done_tick. */
    uint32_t hold = seq->timing.release_hold_ticks;
    if (hold > 0 && !force_immediate && reason != HG_SEQ_R_STOLEN &&
        reason != HG_SEQ_R_RESET) {
        v->off_pending = false;
        v->releasing = true;
        v->release_reason = reason;
        v->release_done_tick = UINT64_MAX - due_tick < hold
            ? UINT64_MAX : due_tick + hold;
        v->end_tick = 0;
        v->end_tick64 = 0;
        return true;
    }

    memset(v, 0, sizeof(*v));
    return true;
}

static bool release_voice(HGSequencer *seq, uint32_t voice, uint32_t reason)
{
    return release_voice_at(seq, voice, reason, seq->clock.elapsed_ticks, false);
}

static void retire_releasing_voices(HGSequencer *seq, uint64_t due_tick)
{
    for (uint32_t voice = 0; voice < HG_SG2_VOICES; ++voice) {
        HGSeqVoice *v = &seq->voice[voice];
        if (!v->releasing || v->release_done_tick > due_tick)
            continue;
        HGSeqInstance shadow;
        const HGSeqInstance *inst = voice_owner_or_shadow(seq, v, &shadow);
        uint32_t ring_cur = ee_ring_cursor(seq);
        record_timed(seq, due_tick, HG_SEQ_D_VOICE_RETIRE, inst, v->track,
                     v->note, voice, v->release_reason, ring_cur, 0);
        memset(v, 0, sizeof(*v));
    }
}

static void release_matching_at(HGSequencer *seq, const HGSeqInstance *inst,
                                uint32_t track, uint32_t reason, uint64_t due_tick)
{
    for (uint32_t voice = 0; voice < HG_SG2_VOICES; ++voice) {
        const HGSeqVoice *v = &seq->voice[voice];
        if (v->owned && v->request == inst->request &&
            (track == UINT32_MAX || v->track == track))
            release_voice_at(seq, voice, reason, due_tick, false);
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

/* Allocation policy:
 *  - Free search: lowest idle pool voice (M24 default) or rotating cursor
 *    starting at alloc_cursor (M26 round-robin). Voices in RELEASING or
 *    off_pending are never free and never stolen (no premature reuse).
 *  - Contention: HG_SEQ_STEAL_REFUSE drops immediately; otherwise steals the
 *    active owned voice with lowest priority <= requester.priority (ties:
 *    oldest start tick, oldest alloc_seq/request, lowest voice). */
static uint32_t acquire_voice_at(HGSequencer *seq, const HGSeqInstance *inst,
                                 uint32_t track, uint32_t note, uint64_t due_tick)
{
    uint32_t first = seq->config.voice_first;
    uint32_t count = seq->config.voice_count;
    uint32_t start_off = seq->timing.alloc_policy == HG_SEQ_ALLOC_ROUND_ROBIN
        ? (seq->alloc_cursor % count) : 0;

    for (uint32_t step = 0; step < count; ++step) {
        uint32_t off = (start_off + step) % count;
        uint32_t voice = first + off;
        if (voice_is_idle(&seq->voice[voice])) {
            if (seq->timing.alloc_policy == HG_SEQ_ALLOC_ROUND_ROBIN)
                seq->alloc_cursor = (off + 1) % count;
            record_timed(seq, due_tick, HG_SEQ_D_ALLOC, inst, track, note, voice,
                         0, ee_ring_cursor(seq), 0);
            return voice;
        }
    }

    if (seq->timing.contention_policy == HG_SEQ_STEAL_REFUSE) {
        record_timed(seq, due_tick, HG_SEQ_D_DROP, inst, track, note, UINT32_MAX,
                     0, ee_ring_cursor(seq), 0);
        return UINT32_MAX;
    }

    uint32_t victim = UINT32_MAX;
    for (uint32_t voice = first; voice < first + count; ++voice) {
        const HGSeqVoice *v = &seq->voice[voice];
        if (!v->owned || v->releasing)
            continue;
        if (victim == UINT32_MAX) {
            victim = voice;
        } else {
            const HGSeqVoice *b = &seq->voice[victim];
            bool better = false;
            if (v->priority != b->priority) {
                better = v->priority < b->priority;
            } else if (seq->timing.timing_mode == HG_SEQ_TIMING_CHRONO) {
                if (v->start_tick64 != b->start_tick64)
                    better = v->start_tick64 < b->start_tick64;
                else if (v->alloc_seq != b->alloc_seq)
                    better = v->alloc_seq < b->alloc_seq;
            } else {
                if (v->start_tick != b->start_tick)
                    better = v->start_tick < b->start_tick;
                else if (v->request != b->request)
                    better = v->request < b->request;
            }
            if (better)
                victim = voice;
        }
    }
    if (victim != UINT32_MAX && seq->voice[victim].priority <= inst->priority) {
        uint32_t loser = seq->voice[victim].request;
        if (release_voice_at(seq, victim, HG_SEQ_R_STOLEN, due_tick, true)) {
            if (seq->timing.alloc_policy == HG_SEQ_ALLOC_ROUND_ROBIN)
                seq->alloc_cursor = (victim - first + 1) % count;
            record_timed(seq, due_tick, HG_SEQ_D_STEAL, inst, track, note, victim,
                         loser, ee_ring_cursor(seq), 0);
            return victim;
        }
    }
    record_timed(seq, due_tick, HG_SEQ_D_DROP, inst, track, note, UINT32_MAX,
                 0, ee_ring_cursor(seq), 0);
    return UINT32_MAX;
}

static void note_on_at(HGSequencer *seq, HGSeqInstance *inst, uint32_t track,
                       uint32_t note, uint32_t velocity, uint32_t tone_index,
                       uint64_t event_tick, uint32_t duration)
{
    /* Optional M26 retrigger policy: release prior active instance of the same
     * (request, track, note) before acquiring a voice for the new note. */
    if (seq->timing.repeat_policy == HG_SEQ_REPEAT_RETRIGGER) {
        for (uint32_t voice = 0; voice < HG_SG2_VOICES; ++voice) {
            const HGSeqVoice *v = &seq->voice[voice];
            if (v->owned && !v->releasing && v->request == inst->request &&
                v->track == track && v->note == note)
                release_voice_at(seq, voice, HG_SEQ_R_RETRIGGER, event_tick, false);
        }
    }

    Tone tone = read_tone(seq, inst, tone_index);
    uint32_t voice = acquire_voice_at(seq, inst, track, note, event_tick);
    if (voice == UINT32_MAX)
        return;
    HGSeqTrack *t = &inst->track[track];
    uint32_t left, right;
    voice_gain(&tone, velocity, t->volume, &left, &right);
    HGSg2VoiceParams params = {
        .note = note, .root = tone.root, .scale = tone.scale,
        .left = left, .right = right, .adsr1 = tone.adsr1, .adsr2 = tone.adsr2,
        .fine_offset = tone.fine, .bend = t->bend, .bend_scale = HG_SEQ_BEND_SCALE};
    uint32_t ring_before = ee_ring_cursor(seq);
    if (!hg_audio_ee_stage_play_voice(seq->service, inst->play_index, voice,
                                      tone.offset, params)) {
        seq->refused_calls++;
        record_timed(seq, event_tick, HG_SEQ_D_STAGE_FAIL, inst, track, note,
                     voice, STAGE_PLAY, ring_before, 0);
        return;
    }
    if (!hg_audio_ee_stage_key(seq->service, voice, true)) {
        seq->refused_calls++;
        record_timed(seq, event_tick, HG_SEQ_D_STAGE_FAIL, inst, track, note,
                     voice, STAGE_KEY_ON, ring_before, 0);
        return;
    }
    seq->staged_calls += 2;
    uint32_t ring_after = ee_ring_cursor(seq);
    uint32_t ring_delta = ring_after >= ring_before ? ring_after - ring_before : 0;
    uint64_t end64 = 0;
    if (duration) {
        end64 = UINT64_MAX - event_tick < duration
            ? UINT64_MAX : event_tick + duration;
    }
    seq->voice[voice] = (HGSeqVoice){
        .owned = true, .request = inst->request, .instance = inst->play_index,
        .track = track, .note = note,
        .start_tick = (uint32_t)event_tick,
        .end_tick = duration ? (uint32_t)end64 : 0,
        .priority = inst->priority, .tone = tone_index, .velocity = velocity,
        .params = params,
        .releasing = false,
        .start_tick64 = event_tick,
        .end_tick64 = end64,
        .release_done_tick = 0,
        .alloc_seq = ++seq->next_alloc_seq,
        .release_reason = 0};
    record_timed(seq, event_tick, HG_SEQ_D_KEY_ON, inst, track, note, voice,
                 tone_index, ring_before, ring_delta);
}

static void note_off_at(HGSequencer *seq, const HGSeqInstance *inst,
                        uint32_t track, uint32_t note, uint64_t due_tick)
{
    uint32_t newest = UINT32_MAX;
    for (uint32_t voice = 0; voice < HG_SG2_VOICES; ++voice) {
        const HGSeqVoice *v = &seq->voice[voice];
        if (!v->owned || v->releasing || v->request != inst->request ||
            v->track != track || v->note != note)
            continue;
        if (newest == UINT32_MAX) {
            newest = voice;
        } else if (seq->timing.timing_mode == HG_SEQ_TIMING_CHRONO) {
            if (v->alloc_seq >= seq->voice[newest].alloc_seq)
                newest = voice;
        } else if (v->start_tick >= seq->voice[newest].start_tick) {
            newest = voice;
        }
    }
    if (newest != UINT32_MAX)
        release_voice_at(seq, newest, HG_SEQ_R_NOTE_OFF, due_tick, false);
}

static void update_track_voices_at(HGSequencer *seq, HGSeqInstance *inst,
                                   uint32_t track, bool gain, uint64_t due_tick)
{
    for (uint32_t voice = 0; voice < HG_SG2_VOICES; ++voice) {
        HGSeqVoice *v = &seq->voice[voice];
        if (!v->owned || v->releasing || v->request != inst->request || v->track != track)
            continue;
        uint32_t ring_before = ee_ring_cursor(seq);
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
            record_timed(seq, due_tick, HG_SEQ_D_STAGE_FAIL, inst, track, v->note,
                         voice, gain ? STAGE_GAIN : STAGE_PITCH, ring_before, 0);
        }
    }
}

/* ---- request lifetime -------------------------------------------------- */

static void retire_at(HGSequencer *seq, HGSeqInstance *inst, uint32_t kind,
                      uint32_t detail, uint64_t due_tick)
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
    if (inst->play_index < HG_SEQ_MAX_INSTANCES) {
        if (seq->request_tag[inst->play_index].command_seq == inst->command_seq)
            seq->request_tag[inst->play_index].valid = false;
        if (seq->cancel_tag[inst->play_index].command_seq == inst->command_seq)
            seq->cancel_tag[inst->play_index].valid = false;
    }
    record_timed(seq, due_tick, kind, inst, UINT32_MAX, UINT32_MAX, UINT32_MAX,
                 detail, ee_ring_cursor(seq), 0);
    memset(inst, 0, sizeof(*inst));
}

static void retire(HGSequencer *seq, HGSeqInstance *inst, uint32_t kind, uint32_t detail)
{
    retire_at(seq, inst, kind, detail, seq->clock.elapsed_ticks);
}

static uint32_t ring_index(const HGSequencer *seq, uint32_t channel)
{
    return r32(seq, HG_AUDIO_CHANNEL_QUEUE + channel * HG_CH_STRIDE + HG_CH_CMD_RING) & 0x1ffu;
}

/* Consume channel bytes queued from *cursor to ring_index. hg_audio_cancel
 * (002ad748) appends ff 41 f7; report whether that marker was present. */
static uint32_t drain_channel_range(HGSequencer *seq, uint32_t channel, uint32_t *cursor)
{
    if (channel >= 32)
        return 0;
    HGAudioBus *bus = bus_of(seq);
    uint32_t queue = HG_AUDIO_CHANNEL_QUEUE + channel * HG_CH_STRIDE;
    uint32_t end = ring_index(seq, channel), marker = 0, seen = 0, consumed = 0;
    static const uint8_t cancel[3] = {0xff, 0x41, 0xf7};
    for (uint32_t at = *cursor & 0x1ffu; at != end; at = (at + 1) & 0x1ffu) {
        uint8_t byte = bus->r8(bus->ctx, queue + at);
        consumed++;
        marker = byte == cancel[marker] ? marker + 1 : byte == cancel[0];
        if (marker == 3) {
            seen = 1;
            marker = 0;
        }
    }
    *cursor = end;
    seq->channel_ring_read[channel] = end;
    seq->clock.channel_bytes_consumed += consumed;
    return seen;
}

static uint32_t drain_channel(HGSequencer *seq, HGSeqInstance *inst)
{
    return drain_channel_range(seq, inst->channel, &inst->ring_read);
}

static void begin_finish_or_retire(HGSequencer *seq, HGSeqInstance *inst,
                                   uint32_t kind, uint32_t detail, uint64_t due_tick)
{
    if (seq->timing.timing_mode == HG_SEQ_TIMING_CHRONO &&
        request_has_voices(seq, inst->request)) {
        inst->finishing = true;
        inst->finish_kind = kind;
        inst->finish_detail = detail;
        for (uint32_t t = 0; t < inst->track_count; ++t)
            inst->track[t].active = false;
        return;
    }
    retire_at(seq, inst, kind, detail, due_tick);
}

static uint32_t adopt_at(HGSequencer *seq, HGSeqInstance *inst, uint32_t index,
                         uint64_t due_tick)
{
    uint32_t command = command_address(index);
    uint32_t base = r32(seq, command + HG_CMD_SAMPLE);
    uint32_t slot = r32(seq, command + HG_CMD_SLOT) & 7u;
    uint32_t slot_base = HG_AUDIO_SLOT_TABLE + slot * HG_SLOT_STRIDE;
    *inst = (HGSeqInstance){
        .active = true, .request = seq->next_request++, .play_index = index,
        .command_seq = r32(seq, command + HG_CMD_SEQ), .slot = slot,
        .bank = r32(seq, slot_base + HG_SLOT_BANK2),
        .spu_base = r32(seq, slot_base + HG_SLOT_AUXC),
        .channel = r32(seq, command + HG_CMD_CHANNEL), .base = base,
        .size = HG_SEQ_HEADER_SIZE,
        .start_tick = (uint32_t)due_tick,
        .request_tick = due_tick};
    if (inst->channel < 32) {
        inst->ring_read = ring_index(seq, inst->channel);
        seq->channel_ring_read[inst->channel] = inst->ring_read;
    }

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
        inst->track[t] = (HGSeqTrack){
            .active = true, .cursor = offset, .wait = 0,
            .volume = 127, .bend = 64, .next_due_tick = due_tick};
    }
    for (uint32_t t = 0; t < tones; ++t) {
        Tone tone = read_tone(seq, inst, t);
        if (tone.offset % 16 || tone.scale > 0x3fff || tone.volume > 127 || tone.pan > 127)
            return HG_SEQ_E_OPERAND;
    }
    return HG_SEQ_E_NONE;
}

static void end_track_at(HGSequencer *seq, HGSeqInstance *inst, uint32_t track,
                         uint32_t error, uint64_t due_tick)
{
    release_matching_at(seq, inst, track, HG_SEQ_R_TRACK_END, due_tick);
    inst->track[track].active = false;
    record_timed(seq, due_tick, HG_SEQ_D_TRACK_END, inst, track, UINT32_MAX,
                 UINT32_MAX, error, ee_ring_cursor(seq), 0);
}

/* Execute one event at logical event_tick; returns an HGSeqError (0 on success). */
static uint32_t step_event_at(HGSequencer *seq, HGSeqInstance *inst, uint32_t track,
                              uint64_t event_tick)
{
    HGSeqTrack *t = &inst->track[track];
    uint32_t at = t->cursor, op, a = 0, b = 0, c = 0, d = 0;
    if (!read8(seq, inst, at, &op))
        return HG_SEQ_E_BOUNDS;
    switch (op) {
    case HG_SEQ_OP_END:
        end_track_at(seq, inst, track, HG_SEQ_E_NONE, event_tick);
        return HG_SEQ_E_NONE;
    case HG_SEQ_OP_WAIT:
        if (!read16(seq, inst, at + 1, &a))
            return HG_SEQ_E_BOUNDS;
        if (!a)
            return HG_SEQ_E_OPERAND;
        if (seq->timing.timing_mode == HG_SEQ_TIMING_CHRONO) {
            if (UINT64_MAX - event_tick < a)
                return HG_SEQ_E_BOUNDS;
            t->next_due_tick = event_tick + a;
        } else {
            t->wait += (int32_t)a;
        }
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
        if (seq->timing.timing_mode == HG_SEQ_TIMING_CHRONO &&
            op == HG_SEQ_OP_NOTE && UINT64_MAX - event_tick < d)
            return HG_SEQ_E_BOUNDS;
        t->cursor = at + (op == HG_SEQ_OP_NOTE ? 6 : 4);
        note_on_at(seq, inst, track, a, b, c, event_tick, d);
        return HG_SEQ_E_NONE;
    case HG_SEQ_OP_NOTE_OFF:
        if (!read8(seq, inst, at + 1, &a))
            return HG_SEQ_E_BOUNDS;
        if (a > 127)
            return HG_SEQ_E_OPERAND;
        t->cursor = at + 2;
        note_off_at(seq, inst, track, a, event_tick);
        return HG_SEQ_E_NONE;
    case HG_SEQ_OP_VOLUME:
    case HG_SEQ_OP_BEND: {
        if (!read8(seq, inst, at + 1, &a))
            return HG_SEQ_E_BOUNDS;
        if (a > 127)
            return HG_SEQ_E_OPERAND;
        t->cursor = at + 2;
        if (op == HG_SEQ_OP_VOLUME)
            t->volume = (uint8_t)a;
        else
            t->bend = (uint8_t)a;
        uint32_t ring_before = ee_ring_cursor(seq);
        if (seq->timing.timing_mode == HG_SEQ_TIMING_CHRONO) {
            update_track_voices_at(seq, inst, track, op == HG_SEQ_OP_VOLUME, event_tick);
            uint32_t ring_after = ee_ring_cursor(seq);
            uint32_t delta = ring_after >= ring_before ? ring_after - ring_before : 0;
            record_timed(seq, event_tick,
                         op == HG_SEQ_OP_VOLUME ? HG_SEQ_D_VOLUME : HG_SEQ_D_BEND,
                         inst, track, UINT32_MAX, UINT32_MAX, a, ring_before, delta);
        } else {
            record(seq, op == HG_SEQ_OP_VOLUME ? HG_SEQ_D_VOLUME : HG_SEQ_D_BEND,
                   inst, track, UINT32_MAX, UINT32_MAX, a);
            update_track_voices_at(seq, inst, track, op == HG_SEQ_OP_VOLUME, event_tick);
        }
        return HG_SEQ_E_NONE;
    }
    default:
        return HG_SEQ_E_OPCODE;
    }
}

/* ---- legacy M24 update (exact preservation when timing_mode == 0) ------ */

static void run_tracks_legacy(HGSequencer *seq, HGSeqInstance *inst)
{
    uint32_t budget = seq->config.events_per_update ? seq->config.events_per_update
                                                    : DEFAULT_EVENT_BUDGET;
    for (uint32_t track = 0; track < inst->track_count && inst->active; ++track) {
        HGSeqTrack *t = &inst->track[track];
        uint32_t events = 0;
        while (t->active && t->wait <= 0) {
            uint64_t ev_tick = seq->tick - (uint32_t)(-t->wait);
            uint32_t error = ++events > budget ? HG_SEQ_E_RUNAWAY
                                               : step_event_at(seq, inst, track, ev_tick);
            if (error) {
                end_track_at(seq, inst, track, error, seq->clock.elapsed_ticks);
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

static bool update_legacy(HGSequencer *seq, uint64_t ticks)
{
    if (UINT64_MAX - seq->clock.elapsed_ticks < ticks)
        return false;
    seq->clock.elapsed_ticks += ticks;
    seq->clock.last_service_tick = seq->clock.elapsed_ticks;
    seq->clock.service_updates++;
    seq->tick += (uint32_t)ticks;

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
            release_matching_at(seq, inst, UINT32_MAX, HG_SEQ_R_CANCEL,
                                seq->clock.elapsed_ticks);
            retire(seq, inst, HG_SEQ_D_CANCEL, marker);
        } else if (current_seq != inst->command_seq || state != 1 ||
                   !hg_audio_slot_active(bus_of(seq), inst->slot) ||
                   slot_bank != inst->bank) {
            release_matching_at(seq, inst, UINT32_MAX, HG_SEQ_R_LOST,
                                seq->clock.elapsed_ticks);
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
        uint32_t error = adopt_at(seq, inst, pick, seq->clock.elapsed_ticks);
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
        run_tracks_legacy(seq, &seq->instance[pick]);
    }
    return true;
}

/* ---- M26 chronological timing-aware update ----------------------------- */

static bool is_unadopted_cmd(const HGSequencer *seq, uint32_t i, uint32_t want_state,
                             uint32_t *out_seq)
{
    uint32_t command = command_address(i);
    uint32_t state = r32(seq, command + HG_CMD_STATE);
    if (state != want_state)
        return false;
    uint32_t cmd_seq = r32(seq, command + HG_CMD_SEQ);
    if (seq->instance[i].active && seq->instance[i].command_seq == cmd_seq)
        return false;
    if (out_seq)
        *out_seq = cmd_seq;
    return true;
}

static uint64_t effective_request_tick(const HGSequencer *seq, uint32_t i,
                                       uint32_t cmd_seq, uint64_t window_start)
{
    if (seq->request_tag[i].valid && seq->request_tag[i].command_seq == cmd_seq)
        return seq->request_tag[i].tick > window_start ? seq->request_tag[i].tick : window_start;
    return window_start;
}

static bool has_scheduled_cancel_by(const HGSequencer *seq, uint32_t i,
                                    uint64_t limit, uint64_t window_start,
                                    uint64_t *out_tick)
{
    const HGSeqPendingTag *tag = &seq->cancel_tag[i];
    if (!tag->valid)
        return false;
    uint32_t command = command_address(i);
    uint32_t state = r32(seq, command + HG_CMD_STATE);
    uint32_t cmd_seq = r32(seq, command + HG_CMD_SEQ);
    if (state == 0 || cmd_seq != tag->command_seq)
        return false;
    uint64_t at = tag->tick > window_start ? tag->tick : window_start;
    if (at > limit)
        return false;
    if (out_tick)
        *out_tick = at;
    return true;
}

static bool instance_lost(const HGSequencer *seq, const HGSeqInstance *inst)
{
    if (inst->finishing)
        return false;
    uint32_t command = command_address(inst->play_index);
    uint32_t state = r32(seq, command + HG_CMD_STATE);
    uint32_t current_seq = r32(seq, command + HG_CMD_SEQ);
    uint32_t slot_base = HG_AUDIO_SLOT_TABLE + inst->slot * HG_SLOT_STRIDE;
    uint32_t slot_bank = r32(seq, slot_base + HG_SLOT_BANK2);
    uint32_t slot_auxc = r32(seq, slot_base + HG_SLOT_AUXC);
    if (current_seq == inst->command_seq && state == 2)
        return false; /* handled as cancel, not lost */
    return current_seq != inst->command_seq || state != 1 ||
           !hg_audio_slot_active(bus_of(seq), inst->slot) ||
           slot_bank != inst->bank || slot_auxc != inst->spu_base;
}

static bool next_due_time(const HGSequencer *seq, uint64_t min_t,
                          uint64_t window_start, uint64_t window_end,
                          uint64_t *out_t)
{
    bool found = false;
    uint64_t best = window_end;

#define OFFER(cand) do {                                   \
    uint64_t _c = (cand) < min_t ? min_t : (cand);         \
    if (_c <= window_end && (!found || _c < best)) {       \
        best = _c;                                         \
        found = true;                                      \
    }                                                      \
} while (0)

    /* 1. Releasing voices reaching release_done_tick. */
    for (uint32_t v = 0; v < HG_SG2_VOICES; ++v) {
        const HGSeqVoice *vc = &seq->voice[v];
        if (vc->releasing && vc->release_done_tick <= window_end)
            OFFER(vc->release_done_tick);
        if (vc->off_pending)
            OFFER(window_start);
        if (vc->owned && !vc->releasing && vc->end_tick64 && vc->end_tick64 <= window_end)
            OFFER(vc->end_tick64);
    }

    /* 2. Commands: scheduled cancels, external cancels (active or pending),
     *    lost banks, unadopted requests, active tracks, finishing retirements. */
    for (uint32_t i = 0; i < HG_SEQ_MAX_INSTANCES; ++i) {
        uint64_t ctick = 0;
        if (has_scheduled_cancel_by(seq, i, window_end, window_start, &ctick))
            OFFER(ctick);

        uint32_t cmd_seq = 0;
        if (is_unadopted_cmd(seq, i, 2, &cmd_seq))
            OFFER(window_start);
        if (is_unadopted_cmd(seq, i, 1, &cmd_seq)) {
            uint64_t rtick = effective_request_tick(seq, i, cmd_seq, window_start);
            if (rtick <= window_end)
                OFFER(rtick);
        }

        const HGSeqInstance *inst = &seq->instance[i];
        if (!inst->active)
            continue;
        if (inst->finishing) {
            if (!request_has_voices(seq, inst->request))
                OFFER(window_start);
            continue;
        }
        uint32_t command = command_address(i);
        uint32_t state = r32(seq, command + HG_CMD_STATE);
        uint32_t cur_seq = r32(seq, command + HG_CMD_SEQ);
        if ((cur_seq == inst->command_seq && state == 2) || instance_lost(seq, inst)) {
            OFFER(window_start);
            continue;
        }
        for (uint32_t t = 0; t < inst->track_count; ++t) {
            if (inst->track[t].active && inst->track[t].next_due_tick <= window_end)
                OFFER(inst->track[t].next_due_tick);
        }
    }

#undef OFFER
    if (found)
        *out_t = best;
    return found;
}

static void retire_pending_cancelled_cmd(HGSequencer *seq, uint32_t i, uint64_t due_tick)
{
    uint32_t command = command_address(i);
    uint32_t cmd_seq = r32(seq, command + HG_CMD_SEQ);
    uint32_t channel = r32(seq, command + HG_CMD_CHANNEL);
    uint32_t marker = 0;
    if (channel < 32) {
        uint32_t cursor = seq->channel_ring_read[channel];
        marker = drain_channel_range(seq, channel, &cursor);
        uint32_t ch_addr = HG_AUDIO_CHANNEL_BASE + channel * HG_CH_STRIDE;
        if (r32(seq, ch_addr) != UINT32_MAX)
            w32(seq, ch_addr, UINT32_MAX);
    }
    w32(seq, command + HG_CMD_SLOT, UINT32_MAX);
    w32(seq, command + HG_CMD_STATE, 0);
    if (seq->request_tag[i].command_seq == cmd_seq)
        seq->request_tag[i].valid = false;
    if (seq->cancel_tag[i].command_seq == cmd_seq)
        seq->cancel_tag[i].valid = false;
    HGSeqInstance ghost = {
        .request = seq->next_request++,
        .play_index = i,
        .request_tick = due_tick};
    record_timed(seq, due_tick, HG_SEQ_D_CANCEL_PENDING, &ghost, UINT32_MAX,
                 UINT32_MAX, UINT32_MAX, marker, ee_ring_cursor(seq), 0);
}

static void process_chrono_tick(HGSequencer *seq, uint64_t due_tick,
                                uint64_t window_start,
                                uint32_t track_events[HG_SEQ_MAX_INSTANCES][HG_SEQ_MAX_TRACKS])
{
    uint32_t budget = seq->config.events_per_update ? seq->config.events_per_update
                                                    : DEFAULT_EVENT_BUDGET;

    /* Stage 1: Deferred voice release completions (ascending physical voice). */
    retire_releasing_voices(seq, due_tick);

    /* Stage 2a: Fire scheduled cancel tags due at <= due_tick via real hg_audio_cancel. */
    for (uint32_t i = 0; i < HG_SEQ_MAX_INSTANCES; ++i) {
        uint64_t ctick = 0;
        if (!has_scheduled_cancel_by(seq, i, due_tick, window_start, &ctick))
            continue;
        seq->cancel_tag[i].valid = false;
        uint32_t command = command_address(i);
        if (r32(seq, command + HG_CMD_STATE) == 1)
            hg_audio_cancel(bus_of(seq), i);
    }

    /* Stage 2b: Process active cancellations and bank/command losses in request order. */
    for (;;) {
        uint32_t pick = UINT32_MAX;
        bool is_cancel = false;
        for (uint32_t i = 0; i < HG_SEQ_MAX_INSTANCES; ++i) {
            const HGSeqInstance *inst = &seq->instance[i];
            if (!inst->active || inst->finishing)
                continue;
            uint32_t command = command_address(i);
            uint32_t state = r32(seq, command + HG_CMD_STATE);
            uint32_t cur_seq = r32(seq, command + HG_CMD_SEQ);
            bool c = (cur_seq == inst->command_seq && state == 2);
            bool l = !c && instance_lost(seq, inst);
            if ((c || l) && (pick == UINT32_MAX || inst->request < seq->instance[pick].request)) {
                pick = i;
                is_cancel = c;
            }
        }
        if (pick == UINT32_MAX)
            break;
        HGSeqInstance *inst = &seq->instance[pick];
        if (is_cancel) {
            uint32_t marker = drain_channel(seq, inst);
            release_matching_at(seq, inst, UINT32_MAX, HG_SEQ_R_CANCEL, due_tick);
            begin_finish_or_retire(seq, inst, HG_SEQ_D_CANCEL, marker, due_tick);
        } else {
            uint32_t state = r32(seq, command_address(pick) + HG_CMD_STATE);
            release_matching_at(seq, inst, UINT32_MAX, HG_SEQ_R_LOST, due_tick);
            begin_finish_or_retire(seq, inst, HG_SEQ_D_LOST, state, due_tick);
        }
    }

    /* Stage 2c: Pending unadopted cancellations (HG_CMD_STATE == 2 before adoption)
     * in ascending HG_CMD_SEQ order so zero commands or channels leak. */
    for (;;) {
        uint32_t pick = UINT32_MAX, best = 0;
        for (uint32_t i = 0; i < HG_SEQ_MAX_INSTANCES; ++i) {
            uint32_t cmd_seq = 0;
            if (!is_unadopted_cmd(seq, i, 2, &cmd_seq))
                continue;
            if (pick == UINT32_MAX || cmd_seq < best) {
                pick = i;
                best = cmd_seq;
            }
        }
        if (pick == UINT32_MAX)
            break;
        retire_pending_cancelled_cmd(seq, pick, due_tick);
    }

    /* Stage 3: Retry previously refused key-offs. */
    for (uint32_t voice = 0; voice < HG_SG2_VOICES; ++voice)
        if (seq->voice[voice].off_pending)
            release_voice_at(seq, voice, seq->voice[voice].pending_reason, due_tick, false);

    /* Stage 4: Adopt unadopted state-1 commands whose request_tick <= due_tick
     * in ascending HG_CMD_SEQ submission order. */
    for (;;) {
        uint32_t pick = UINT32_MAX, best = 0;
        for (uint32_t i = 0; i < HG_SEQ_MAX_INSTANCES; ++i) {
            uint32_t cmd_seq = 0;
            if (!is_unadopted_cmd(seq, i, 1, &cmd_seq))
                continue;
            if (effective_request_tick(seq, i, cmd_seq, window_start) > due_tick)
                continue;
            if (pick == UINT32_MAX || cmd_seq < best) {
                pick = i;
                best = cmd_seq;
            }
        }
        if (pick == UINT32_MAX)
            break;
        HGSeqInstance *inst = &seq->instance[pick];
        uint32_t error = adopt_at(seq, inst, pick, due_tick);
        if (error)
            retire_at(seq, inst, HG_SEQ_D_REJECT, error, due_tick);
        else
            record_timed(seq, due_tick, HG_SEQ_D_START, inst, UINT32_MAX,
                         UINT32_MAX, UINT32_MAX, inst->slot, ee_ring_cursor(seq), 0);
    }

    /* Stage 5: Expire note durations at due_tick in deterministic
     * (request, track, alloc_seq, voice) order before same-tick NOTE_ON events. */
    for (;;) {
        uint32_t pick = UINT32_MAX;
        for (uint32_t v = 0; v < HG_SG2_VOICES; ++v) {
            const HGSeqVoice *vc = &seq->voice[v];
            if (!vc->owned || vc->releasing || !vc->end_tick64 || vc->end_tick64 > due_tick)
                continue;
            if (pick == UINT32_MAX) {
                pick = v;
            } else {
                const HGSeqVoice *b = &seq->voice[pick];
                if (vc->request < b->request ||
                    (vc->request == b->request &&
                     (vc->track < b->track ||
                      (vc->track == b->track && vc->alloc_seq < b->alloc_seq))))
                    pick = v;
            }
        }
        if (pick == UINT32_MAX)
            break;
        release_voice_at(seq, pick, HG_SEQ_R_DURATION, due_tick, false);
    }

    /* Stage 6: Execute due track events at due_tick in ascending (request, track) order. */
    for (;;) {
        uint32_t pick_i = UINT32_MAX, pick_t = UINT32_MAX;
        for (uint32_t i = 0; i < HG_SEQ_MAX_INSTANCES; ++i) {
            const HGSeqInstance *inst = &seq->instance[i];
            if (!inst->active || inst->finishing)
                continue;
            for (uint32_t t = 0; t < inst->track_count; ++t) {
                if (!inst->track[t].active || inst->track[t].next_due_tick > due_tick)
                    continue;
                if (pick_i == UINT32_MAX ||
                    inst->request < seq->instance[pick_i].request ||
                    (inst->request == seq->instance[pick_i].request && t < pick_t)) {
                    pick_i = i;
                    pick_t = t;
                }
            }
        }
        if (pick_i == UINT32_MAX)
            break;
        HGSeqInstance *inst = &seq->instance[pick_i];
        HGSeqTrack *tr = &inst->track[pick_t];
        while (inst->active && !inst->finishing && tr->active &&
               tr->next_due_tick <= due_tick) {
            uint32_t error = ++track_events[pick_i][pick_t] > budget
                ? HG_SEQ_E_RUNAWAY
                : step_event_at(seq, inst, pick_t, due_tick);
            if (error) {
                end_track_at(seq, inst, pick_t, error, due_tick);
                break;
            }
        }
        if (inst->active && !inst->finishing) {
            bool running = false;
            for (uint32_t t = 0; t < inst->track_count; ++t)
                running |= inst->track[t].active;
            if (!running)
                begin_finish_or_retire(seq, inst, HG_SEQ_D_COMPLETE, 0, due_tick);
        }
    }

    /* Stage 7: Retire finishing instances whose releasing voices have all completed. */
    for (;;) {
        uint32_t pick = UINT32_MAX;
        for (uint32_t i = 0; i < HG_SEQ_MAX_INSTANCES; ++i) {
            const HGSeqInstance *inst = &seq->instance[i];
            if (!inst->active || !inst->finishing || request_has_voices(seq, inst->request))
                continue;
            if (pick == UINT32_MAX || inst->request < seq->instance[pick].request)
                pick = i;
        }
        if (pick == UINT32_MAX)
            break;
        HGSeqInstance *inst = &seq->instance[pick];
        retire_at(seq, inst, inst->finish_kind, inst->finish_detail, due_tick);
    }
}

static bool update_chrono(HGSequencer *seq, uint64_t ticks)
{
    if (UINT64_MAX - seq->clock.elapsed_ticks < ticks)
        return false;
    uint64_t window_start = seq->clock.elapsed_ticks;
    uint64_t window_end = window_start + ticks;
    seq->clock.elapsed_ticks = window_end;
    seq->clock.last_service_tick = window_end;
    seq->clock.service_updates++;
    seq->tick = (uint32_t)window_end;

    uint32_t track_events[HG_SEQ_MAX_INSTANCES][HG_SEQ_MAX_TRACKS];
    memset(track_events, 0, sizeof(track_events));

    uint64_t cur_t = window_start;
    for (uint32_t guard = 0; guard < MAX_CHRONO_TICKS_PER_UPDATE; ++guard) {
        uint64_t due_t = 0;
        if (!next_due_time(seq, cur_t, window_start, window_end, &due_t))
            break;
        cur_t = due_t;
        process_chrono_tick(seq, due_t, window_start, track_events);
        /* If another item becomes due at the exact same tick (e.g., freshly
         * adopted request with track due at due_t), next_due_time finds it;
         * if all items at due_t are done, next_due_time advances past due_t.
         * Guard against stalled off_pending looping forever at cur_t: */
        bool any_stalled_only = false;
        uint64_t next_t = 0;
        if (!next_due_time(seq, cur_t, window_start, window_end, &next_t))
            break;
        if (next_t == cur_t) {
            /* Only retry same tick if a non-off_pending event is due. */
            bool has_work = false;
            for (uint32_t i = 0; i < HG_SEQ_MAX_INSTANCES && !has_work; ++i) {
                const HGSeqInstance *inst = &seq->instance[i];
                if (inst->active && !inst->finishing) {
                    for (uint32_t t = 0; t < inst->track_count; ++t)
                        if (inst->track[t].active && inst->track[t].next_due_tick <= cur_t)
                            has_work = true;
                } else if (inst->active && inst->finishing &&
                           !request_has_voices(seq, inst->request)) {
                    has_work = true;
                }
            }
            if (!has_work) {
                any_stalled_only = true;
                if (cur_t == window_end)
                    break;
                cur_t++;
            }
        }
        (void)any_stalled_only;
    }
    return true;
}

/* ---- public API -------------------------------------------------------- */

static bool valid_timing(HGSeqTimingConfig timing)
{
    return timing.timing_mode <= HG_SEQ_TIMING_CHRONO &&
           timing.alloc_policy <= HG_SEQ_ALLOC_ROUND_ROBIN &&
           timing.contention_policy <= HG_SEQ_STEAL_REFUSE &&
           timing.repeat_policy <= HG_SEQ_REPEAT_RETRIGGER;
}

bool hg_seq_init(HGSequencer *seq, HGAudioEeService *service, HGSeqConfig config)
{
    if (!seq || !config.voice_count || config.voice_first >= HG_SG2_VOICES ||
        config.voice_count > HG_SG2_VOICES - config.voice_first)
        return false;
    memset(seq, 0, sizeof(*seq));
    seq->service = service;
    seq->config = config;
    if (service && service->bus) {
        for (uint32_t ch = 0; ch < HG_SEQ_MAX_INSTANCES; ++ch)
            seq->channel_ring_read[ch] = ring_index(seq, ch);
    }
    return true;
}

bool hg_seq_configure_timing(HGSequencer *seq, HGSeqTimingConfig timing)
{
    if (!seq || !valid_timing(timing))
        return false;
    seq->timing = timing;
    return true;
}

bool hg_seq_init_timed(HGSequencer *seq, HGAudioEeService *service,
                       HGSeqConfig config, HGSeqTimingConfig timing)
{
    if (!valid_timing(timing) || !hg_seq_init(seq, service, config))
        return false;
    seq->timing = timing;
    return true;
}

bool hg_seq_update_u64(HGSequencer *seq, uint64_t ticks)
{
    if (!bound(seq))
        return false;
    if (seq->timing.timing_mode == HG_SEQ_TIMING_CHRONO)
        return update_chrono(seq, ticks);
    if (ticks > UINT32_MAX)
        return false;
    return update_legacy(seq, ticks);
}

bool hg_seq_update(HGSequencer *seq, uint32_t ticks)
{
    return hg_seq_update_u64(seq, ticks);
}

void hg_seq_reset(HGSequencer *seq)
{
    if (!seq)
        return;
    bool live = bound(seq);
    uint64_t now = seq->clock.elapsed_ticks;
    if (live)
        for (uint32_t voice = 0; voice < HG_SG2_VOICES; ++voice)
            if (seq->voice[voice].owned || seq->voice[voice].off_pending ||
                seq->voice[voice].releasing)
                release_voice_at(seq, voice, HG_SEQ_R_RESET, now, true);
    if (seq->service && seq->service->bus)
        for (uint32_t i = 0; i < HG_SEQ_MAX_INSTANCES; ++i)
            if (seq->instance[i].active)
                retire_at(seq, &seq->instance[i], HG_SEQ_D_LOST, UINT32_MAX, now);
    memset(seq->instance, 0, sizeof(seq->instance));
    memset(seq->voice, 0, sizeof(seq->voice));
    memset(seq->request_tag, 0, sizeof(seq->request_tag));
    memset(seq->cancel_tag, 0, sizeof(seq->cancel_tag));
    seq->alloc_cursor = 0;
    record_timed(seq, now, HG_SEQ_D_RESET, NULL, UINT32_MAX, UINT32_MAX,
                 UINT32_MAX, live, ee_ring_cursor(seq), 0);
}

bool hg_seq_rebind(HGSequencer *seq, HGAudioEeService *service)
{
    if (!seq)
        return false;
    hg_seq_reset(seq);
    seq->service = service;
    if (service && service->bus) {
        for (uint32_t ch = 0; ch < HG_SEQ_MAX_INSTANCES; ++ch)
            seq->channel_ring_read[ch] = ring_index(seq, ch);
    }
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
        if (seq->voice[voice].owned || seq->voice[voice].releasing ||
            seq->voice[voice].off_pending) {
            count++;
            if (mask)
                mask[voice / 24] |= 1u << (voice % 24);
        }
    return count;
}

uint32_t hg_seq_releasing_voices(const HGSequencer *seq, uint32_t mask[2])
{
    uint32_t count = 0;
    if (mask)
        mask[0] = mask[1] = 0;
    for (uint32_t voice = 0; seq && voice < HG_SG2_VOICES; ++voice)
        if (seq->voice[voice].releasing) {
            count++;
            if (mask)
                mask[voice / 24] |= 1u << (voice % 24);
        }
    return count;
}

bool hg_seq_tag_request(HGSequencer *seq, uint32_t play_index, uint64_t request_tick)
{
    if (!bound(seq) || play_index >= HG_SEQ_MAX_INSTANCES)
        return false;
    uint32_t command = command_address(play_index);
    if (r32(seq, command + HG_CMD_STATE) != 1)
        return false;
    seq->request_tag[play_index] = (HGSeqPendingTag){
        .valid = true,
        .command_seq = r32(seq, command + HG_CMD_SEQ),
        .tick = request_tick};
    return true;
}

bool hg_seq_tag_cancel(HGSequencer *seq, uint32_t play_index, uint64_t cancel_tick)
{
    if (!bound(seq) || play_index >= HG_SEQ_MAX_INSTANCES)
        return false;
    uint32_t command = command_address(play_index);
    uint32_t state = r32(seq, command + HG_CMD_STATE);
    if (state == 0)
        return false;
    seq->cancel_tag[play_index] = (HGSeqPendingTag){
        .valid = true,
        .command_seq = r32(seq, command + HG_CMD_SEQ),
        .tick = cancel_tick};
    return true;
}

void hg_seq_record_flush(HGSequencer *seq, const HGSg2Reply *reply, uint32_t ring_records)
{
    if (!seq)
        return;
    (void)reply;
    seq->clock.last_flush_tick = seq->clock.elapsed_ticks;
    seq->clock.flush_count++;
    seq->clock.flush_ring_total += ring_records;
}

void hg_seq_record_render(HGSequencer *seq, uint64_t frames)
{
    if (!seq || UINT64_MAX - seq->clock.render_frames < frames)
        return;
    seq->clock.render_frames += frames;
}

HGSeqClock hg_seq_clock(const HGSequencer *seq)
{
    if (!seq)
        return (HGSeqClock){0};
    return seq->clock;
}
