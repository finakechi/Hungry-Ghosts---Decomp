/* M24 synthetic sequence processor and physical voice manager.
 *
 * This is an ORIGINAL host model, not a reconstruction of the retail EE
 * sequencer (002add90 / 002a96b8 / 002ab508) or its allocator (002ae9b8 /
 * 002afed8 / 002af1d0 / 002b1098). It reads an independently defined
 * sequence format ("HSQ1", below) from the cue address that the existing
 * hg_audio_play (002ad478) resolves into a play command, owns all timing,
 * note and voice decisions, and reaches SPU2 only through the M22 EE bridge
 * (hg_audio_ee_stage_play_voice / _stage_key / _stage_gain / _stage_pitch).
 * The caller still flushes (hg_audio_ee_flush) and renders
 * (hg_audio_iop_render) on its own serialized audio timeline.
 *
 * HSQ1 resource (little-endian, at the cue address in guest memory):
 *   +0x00 u32  magic 0x31515348 ("HSQ1")
 *   +0x04 u8   version (1)
 *   +0x05 u8   track count, 1..HG_SEQ_MAX_TRACKS
 *   +0x06 u8   tone count, 1..HG_SEQ_MAX_TONES
 *   +0x07 u8   priority (higher wins voice contention)
 *   +0x08 u16  tone table offset
 *   +0x0a u16  total size in bytes, 0x14..HG_SEQ_MAX_SIZE; nothing past it is read
 *   +0x0c u16  track offsets [4] (unused entries 0)
 *   tone (12 bytes): u16 sample offset into the bound bank (16-byte aligned),
 *                    u8 root note, i8 fine offset, u16 ADSR1, u16 ADSR2,
 *                    u16 rate scale (<= 0x3fff), u8 volume, u8 pan (0 L, 64 C, 127 R)
 *   track events (opcode, operands):
 *     00 END                          release the track's held notes
 *     01 WAIT     u16 ticks (>= 1)
 *     02 NOTE_ON  u8 note, u8 velocity (1..127), u8 tone
 *     03 NOTE_OFF u8 note             newest held instance of that note
 *     04 VOLUME   u8 0..127           restages gain of the track's voices
 *     05 BEND     u8 0..127 (64 = 0)  restages pitch of the track's voices
 *     06 NOTE     u8 note, u8 velocity, u8 tone, u16 duration ticks (>= 1)
 */
#ifndef HG_AUDIO_SEQUENCER_H
#define HG_AUDIO_SEQUENCER_H

#include "audio_iop.h"

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define HG_SEQ_MAGIC 0x31515348u
#define HG_SEQ_VERSION 1u
#define HG_SEQ_HEADER_SIZE 0x14u
#define HG_SEQ_TONE_SIZE 12u
#define HG_SEQ_MAX_SIZE 0x1000u
#define HG_SEQ_MAX_TRACKS 4u
#define HG_SEQ_MAX_TONES 16u
#define HG_SEQ_MAX_INSTANCES 32u
#define HG_SEQ_LOG_CAPACITY 2048u
#define HG_SEQ_BEND_SCALE 2u /* semitones at full bend */

enum HGSeqOpcode {
    HG_SEQ_OP_END = 0x00,
    HG_SEQ_OP_WAIT = 0x01,
    HG_SEQ_OP_NOTE_ON = 0x02,
    HG_SEQ_OP_NOTE_OFF = 0x03,
    HG_SEQ_OP_VOLUME = 0x04,
    HG_SEQ_OP_BEND = 0x05,
    HG_SEQ_OP_NOTE = 0x06,
};

/* Model-generated decisions, recorded separately from service operations. */
enum HGSeqDecisionKind {
    HG_SEQ_D_START = 1,      /* request adopted: detail = slot */
    HG_SEQ_D_REJECT,         /* malformed request: detail = HGSeqError */
    HG_SEQ_D_ALLOC,          /* free voice taken for note */
    HG_SEQ_D_STEAL,          /* voice taken from detail = victim request */
    HG_SEQ_D_DROP,           /* no voice available for note */
    HG_SEQ_D_KEY_ON,         /* note staged and keyed on */
    HG_SEQ_D_KEY_OFF,        /* voice keyed off: detail = HGSeqReleaseReason */
    HG_SEQ_D_VOLUME,         /* track volume: detail = value */
    HG_SEQ_D_BEND,           /* track bend: detail = value */
    HG_SEQ_D_TRACK_END,      /* track finished: detail = HGSeqError (0 normal) */
    HG_SEQ_D_COMPLETE,       /* request retired after natural end */
    HG_SEQ_D_CANCEL,         /* request retired after hg_audio_cancel */
    HG_SEQ_D_LOST,           /* request retired: bank/command vanished
                              * (detail = command state) or model reset
                              * (detail = UINT32_MAX) */
    HG_SEQ_D_STAGE_FAIL,     /* service refused a staging call: detail = call */
    HG_SEQ_D_RESET,          /* model reset */
};

enum HGSeqReleaseReason {
    HG_SEQ_R_NOTE_OFF = 1, HG_SEQ_R_DURATION, HG_SEQ_R_TRACK_END,
    HG_SEQ_R_STOLEN, HG_SEQ_R_CANCEL, HG_SEQ_R_LOST, HG_SEQ_R_RESET,
};

enum HGSeqError {
    HG_SEQ_E_NONE = 0, HG_SEQ_E_HEADER, HG_SEQ_E_BOUNDS, HG_SEQ_E_OPCODE,
    HG_SEQ_E_OPERAND, HG_SEQ_E_RUNAWAY,
};

typedef struct HGSeqDecision {
    uint32_t tick, kind, request, play_index, track, note, voice, detail;
} HGSeqDecision;

typedef struct HGSeqConfig {
    uint32_t voice_first;       /* physical voice pool [first, first+count) */
    uint32_t voice_count;       /* within 0..47 */
    uint32_t events_per_update; /* per-track runaway guard; 0 selects 256 */
} HGSeqConfig;

typedef struct HGSeqVoice {
    bool owned, off_pending;
    uint32_t request, instance, track, note, start_tick, end_tick;
    uint32_t priority, tone, velocity, pending_reason;
    HGSg2VoiceParams params;
} HGSeqVoice;

typedef struct HGSeqTrack {
    bool active;
    uint32_t cursor;
    int32_t wait;
    uint8_t volume, bend;
} HGSeqTrack;

typedef struct HGSeqInstance {
    bool active;
    uint32_t request, play_index, command_seq, slot, bank, channel;
    uint32_t base, size, tones, priority, ring_read, start_tick;
    uint32_t track_count, tone_count;
    HGSeqTrack track[HG_SEQ_MAX_TRACKS];
} HGSeqInstance;

typedef struct HGSequencer {
    HGAudioEeService *service;
    HGSeqConfig config;
    uint32_t tick, next_request;
    HGSeqInstance instance[HG_SEQ_MAX_INSTANCES];
    HGSeqVoice voice[HG_SG2_VOICES];
    HGSeqDecision log[HG_SEQ_LOG_CAPACITY];
    uint32_t log_count, log_overflow;
    uint32_t staged_calls, refused_calls;
} HGSequencer;

/* Bind to an initialized EE service. Fails on an invalid voice pool. */
bool hg_seq_init(HGSequencer *seq, HGAudioEeService *service, HGSeqConfig config);
/* One service update on the serialized audio timeline: advance the model
 * clock by ticks, adopt new active play commands, retire cancelled or lost
 * ones, consume every due event and stage the resulting service requests.
 * Returns false (changing nothing) if the service is unbound. */
bool hg_seq_update(HGSequencer *seq, uint32_t ticks);
/* Key off every owned voice (when the service is still bound), retire the
 * adopted play commands and channels (when the guest bus is still bound), and
 * clear all request and voice state. The clock, request counter, decision log,
 * binding and configuration persist. The caller flushes. */
void hg_seq_reset(HGSequencer *seq);
/* Rebind after a service teardown/recreation; implies hg_seq_reset first. */
bool hg_seq_rebind(HGSequencer *seq, HGAudioEeService *service);
uint32_t hg_seq_active_requests(const HGSequencer *seq);
uint32_t hg_seq_owned_voices(const HGSequencer *seq, uint32_t mask[2]);

#ifdef __cplusplus
}
#endif
#endif
