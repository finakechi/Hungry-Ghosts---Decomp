#ifndef HG_AUDIO_IOP_H
#define HG_AUDIO_IOP_H

#include "audio_engine.h"
#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#define HG_SG2_RPC_ID 0x11223344u
#define HG_SG2_EE_CURSOR 0x00a80180u
#define HG_SG2_EE_PACKETS 0x00a801c0u
#define HG_SG2_QUEUE_SIZE 512u
#define HG_SG2_VOICES 48u
#define HG_SPU2_RAM_SIZE 0x200000u
#define HG_SG2_UPLOAD_START 0x002a7560u
#define HG_SG2_UPLOAD_POLL 0x002a7590u
#define HG_SG2_BANK_BIND 0x002b5428u

enum HGSg2Command {
    HG_SG2_NOP = 0x00,
    HG_SG2_GAIN = 0x01,
    HG_SG2_ADSR = 0x02,
    HG_SG2_START_ADDRESS = 0x03,
    HG_SG2_PITCH = 0x04,
    HG_SG2_KEY_ON = 0x07,
    HG_SG2_KEY_OFF = 0x08,
    HG_SG2_WET_MASK = 0x09,
    HG_SG2_NOISE_MASK = 0x0a,
    HG_SG2_UPLOAD = 0x13,
};

typedef struct HGSg2Packet {
    uint32_t command, arg1, arg2, arg3;
} HGSg2Packet;

typedef struct HGSg2Reply {
    uint32_t envelope[HG_SG2_VOICES]; /* core-major order */
    uint32_t upload_busy;           /* original reply +0x184 */
} HGSg2Reply;

typedef struct HGSpu2Io {
    void *ctx;
    /* Source availability and copy are atomic from the service's perspective. */
    bool (*read_iop)(void *ctx, uint32_t src, uint8_t *out, size_t size);
    void (*write16)(void *ctx, uint32_t address, uint16_t value);
    void (*voice_transfer)(void *ctx, uint32_t core, uint32_t mode,
                           uint32_t src, uint32_t dest, uint32_t size);
    /* Optional external read of ENVX. By default the model reports activity. */
    uint16_t (*read16)(void *ctx, uint32_t address);
} HGSpu2Io;

typedef struct HGAudioIop HGAudioIop;

HGAudioIop *hg_audio_iop_create(HGSpu2Io io);
void hg_audio_iop_destroy(HGAudioIop *iop);

/* Serialized RPC transport. Each little-endian record occupies 16 bytes.
 * Rejection leaves the command/transfer queues intact. Service 0 is supported.
 * The reply exposes the selected fields of SG2IOP's 0x1c0-byte response. */
bool hg_audio_iop_rpc(HGAudioIop *iop, uint32_t server, uint32_t function,
                     const uint8_t *bytes, size_t size, HGSg2Reply *reply);
/* Run an outstanding upload. Its completion becomes visible on the next RPC.
 * A false return with queued work means the source must be made readable. */
bool hg_audio_iop_step_dma(HGAudioIop *iop);
bool hg_audio_iop_read_spu(const HGAudioIop *iop, uint32_t address,
                          uint8_t *out, size_t size);
/* Advance the bounded hardware model by frames at 48 kHz. Interleaved stereo
 * or NULL to advance without producing output. */
void hg_audio_iop_render(HGAudioIop *iop, int16_t *stereo, size_t frames);

typedef struct HGAudioEeService {
    HGAudioBus *bus;
    HGAudioIop *iop;
    HGSg2Packet upload;
    uint32_t upload_state;
} HGAudioEeService;

/* Bind the existing guest bus and initialize the EE packet cursor. All entry
 * points are single-owner and must run on the same serialized audio timeline. */
void hg_audio_ee_service_init(HGAudioEeService *service, HGAudioBus *bus,
                              HGAudioIop *iop);
/* EE staging corresponding to 002acf60. At most 511 queued records. */
bool hg_audio_ee_post(HGAudioEeService *service, HGSg2Packet packet);
/* EE RPC boundary corresponding to the selected path of 002ad138. An idle
 * flush polls an outstanding upload and updates its EE completion latch. */
bool hg_audio_ee_flush(HGAudioEeService *service, HGSg2Reply *reply);
/* Route 002a7560, 002a7590 and 002b5428 from HGAudioBus.call here. Ticket 0 belongs to
 * this adapter's single outstanding registration. Returns UINT32_MAX on a
 * rejected start, 1 while pending and 0 on successful polling completion. */
uint32_t hg_audio_ee_bank_call(HGAudioEeService *service, uint32_t target,
                                uint32_t arg1, uint32_t arg2, uint32_t arg3);

/* Resolve one aligned ADPCM block in an uploaded, bound EE bank slot. This
 * is the adapter for a caller that has already resolved a sample offset. */
bool hg_audio_ee_sample_address(const HGAudioEeService *service, uint32_t slot,
                                uint32_t offset, uint32_t *address);

typedef struct HGSg2VoiceParams {
    uint32_t note, root, scale;
    uint32_t left, right;
    uint16_t adsr1, adsr2;
    int8_t fine_offset;
    uint8_t bend; /* 64 is neutral. */
    uint8_t bend_scale;
} HGSg2VoiceParams;

/* Stage the resolved physical voice parameters. Addresses, gain values and
 * note fields are packed as on the EE. Stage routing and key events separately
 * so several voices can be configured before the same key-on edge. */
bool hg_audio_ee_stage_voice(HGAudioEeService *service, uint32_t voice, uint32_t slot,
                             uint32_t sample_offset, HGSg2VoiceParams params);
/* Resolve the bank through an active 002ad478 play request. The sequence
 * caller supplies the physical voice, sample offset and resolved parameters. */
bool hg_audio_ee_stage_play_voice(HGAudioEeService *service, uint32_t play_index,
                                  uint32_t voice, uint32_t sample_offset,
                                  HGSg2VoiceParams params);
bool hg_audio_ee_stage_key(HGAudioEeService *service, uint32_t voice, bool on);
/* M24 parameter updates for a voice that is already configured: stage one
 * GAIN record, or one PITCH record packed and validated like
 * hg_audio_ee_stage_voice. Neither touches ADSR, SSA or key state. */
bool hg_audio_ee_stage_gain(HGAudioEeService *service, uint32_t voice,
                            uint32_t left, uint32_t right);
bool hg_audio_ee_stage_pitch(HGAudioEeService *service, uint32_t voice,
                             HGSg2VoiceParams params);

#ifdef __cplusplus
}
#endif
#endif
