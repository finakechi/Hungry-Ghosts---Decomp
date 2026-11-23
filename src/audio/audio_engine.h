/* Hungry Ghosts audio subsystem - reconstructed original EE functions.
 *
 * Independent C reconstruction from verified original instruction bytes of
 * the Japanese build (ELF SHA-256
 * a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61).
 *
 * This header declares the audio-owned guest-bus interface. It does NOT
 * modify or depend on src/resource_loader.c/.h; integration with the
 * existing resource environment is provided by an adapter (tools/hg_audio)
 * so that M17/M18 and shared loading code stay untouched.
 *
 * Original function map (entry -> symbol):
 *   0x00156a50 hg_audio_register_entry        (state gate, calls worker)
 *   0x00157060 hg_audio_register_worker       (descriptor phase machine)
 *   0x00156f20 hg_audio_transfer_setup        (SIF DMA setup/poll helper)
 *   0x00156fc0 hg_audio_align16               (alloc + 16-byte alignment)
 *   0x002b4db0 hg_audio_slot_find             (find free 8-slot entry)
 *   0x002b4df0 hg_audio_bank_register         (SShd bank -> slot tables)
 *   0x002b4f68 hg_audio_bank_index            (bank table lookup)
 *   0x002b4f88 hg_audio_slot_active           (slot state test)
 *   0x002b4fc8 hg_audio_slot_state            (read slot state/aux)
 *   0x002b5008 hg_audio_table_lookup4         (bank+0x3c table walk)
 *   0x002b50e0 hg_audio_table_lookup6         (bank+0x44 table walk)
 *   0x002b5178 hg_audio_table_lookup1         (bank+0x30 table walk)
 *   0x002b5228 hg_audio_null8                 (x ? x+8 : 0)
 *   0x002b5240 hg_audio_slot_range18          (read slot+0x18/0x1c)
 *   0x002b5278 hg_audio_slot_range20          (read slot+0x20..0x28)
 *   0x002b52c0 hg_audio_slot_swap             (exchange aux word)
 *   0x002b52f0 hg_audio_slot_get             (read aux word)
 *   0x002b5318 hg_audio_bank_register_wrapper (ints-off slot+bank init)
 *   0x002b53a0 hg_audio_slot_bank             (slot -> bank+4 word)
 *   0x002b54e8 hg_audio_slot_clear            (zero a slot)
 *   0x002b55a8 hg_audio_slots_init            (zero all 8 slots)
 *   0x002a99b8 hg_audio_open_channel_a        (voice alloc + prepare A)
 *   0x002a9b68 hg_audio_open_channel_b        (voice alloc + prepare B)
 *   0x002a9050 hg_audio_channel_init_a        (channel struct init A)
 *   0x002a9180 hg_audio_channel_init_b        (channel struct init B)
 *   0x002ad478 hg_audio_play                  (post play command)
 *   0x002ad748 hg_audio_cancel                (stop/cancel command)
 *   0x002a94b8 hg_audio_channel_command       (queue IOP command byte)
 *   0x002ae998 hg_audio_next_sequence         (global sequence counter)
 *   0x002adcf0 hg_audio_bank_bind            (bind bank to IOP service)
 *
 * Explicit external seams (documented contracts, host-modeled):
 *   0x0010cca0 / 0x0010cce8  EE interrupt disable/enable (EI/DI + Status)
 *   0x001041e0 FlushCache(0)                   Sony EE kernel syscall 0x64
 *   0x00104320 sceSifSetDma                    Sony EE kernel syscall 0x77
 *   0x00104300 sceSifDmaStat                   Sony EE kernel syscall 0x76
 *   0x0010af40 hg_sif_alloc                   SIF-side memory allocation
 *   0x0010b0a8 hg_sif_mscom                   SIF0_MSCOM byte send
 *   0x002a74d0 hg_audio_global_tick           audio tick source
 *   0x001165d0 / 0x00116320                   time math helpers
 *   0x002ad358 hg_audio_play_extra            play variant tail
 *   0x002adab0 hg_audio_flag_service          flag/stream service call
 *
 * The Sony kernel syscall wrappers and EI/DI are SDK/system boundaries:
 * they are invoked through the bus, never reimplemented here.
 */
#ifndef HG_AUDIO_ENGINE_H
#define HG_AUDIO_ENGINE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Guest-bus interface. All fields mandatory. */
typedef struct HGAudioBus {
    uint8_t (*r8)(void *ctx, uint32_t address);
    uint16_t (*r16)(void *ctx, uint32_t address);
    uint32_t (*r32)(void *ctx, uint32_t address);
    void (*w8)(void *ctx, uint32_t address, uint8_t value);
    void (*w16)(void *ctx, uint32_t address, uint16_t value);
    void (*w32)(void *ctx, uint32_t address, uint32_t value);
    /* a0..a3, t0 are the original register arguments; returns v0. */
    uint32_t (*call)(void *ctx, uint32_t target,
                     uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3,
                     uint32_t t0);
    void *ctx;
} HGAudioBus;

/* Constant guest addresses used by the reconstructed functions. */
#define HG_AUDIO_GATE_STATE      0x003eaa50u /* gp+0x2ae0: gate byte  */
#define HG_AUDIO_PHASE           0x003eaa51u /* gp+0x2ae1: phase byte */
#define HG_AUDIO_DESC_COUNT      0x003eaa53u /* gp+0x2ae3: descriptor counter */
#define HG_AUDIO_DESC_CURSOR     0x003eaa5cu /* gp+0x2aec */
#define HG_AUDIO_DESC_SOURCE     0x003eaa60u /* gp+0x2af0 */
#define HG_AUDIO_DESC_SAVED      0x003eaa64u /* gp+0x2af4 */
#define HG_AUDIO_DESC_DMA        0x003eaa68u /* gp+0x2af8 */
#define HG_AUDIO_DESC_ALLOC      0x003eaa6cu /* gp+0x2afc: IOP-side alloc */
#define HG_AUDIO_DESC_ID         0x003eaa70u /* gp+0x2b00 */
#define HG_AUDIO_DESC_IDX_BASE   0x003eaa00u /* gp+0x2a90: per-id counter table */
#define HG_AUDIO_CTX_SAVE        0x003e0720u /* gp-0x7850: 20-byte saved context */
#define HG_AUDIO_DMA_SCRATCH     0x003ead60u /* modeled SIF DMA struct slot */
#define HG_AUDIO_SLOT_TABLE      0x00a87440u /* 8 slots, 0x2c stride  */
#define HG_AUDIO_SLOT_AUX        0x00a87450u /* aux words, 0x2c stride */
#define HG_AUDIO_ID_TABLE        0x003f2740u /* per-id 120-byte structs */
#define HG_AUDIO_CHANNEL_BASE    0x00a6c4d8u /* 32 channels, 0x9e4 stride */
#define HG_AUDIO_CHANNEL_QUEUE   0x00a6c530u /* 32 queues, 0x9e4 stride */
#define HG_AUDIO_VOICE_CURSOR    0x00a80158u /* rotating voice cursor */
#define HG_AUDIO_COMMAND_BASE    0x00a82640u /* 32 commands, 0x50 stride */
#define HG_AUDIO_COMMAND_CURSOR  0x00a83040u /* rotating command cursor */
#define HG_AUDIO_SEQUENCE        0x00a832a4u /* sequence counter */

/* Slots (0x2c stride). */
#define HG_SLOT_STATE  0x00u
#define HG_SLOT_BANK   0x04u
#define HG_SLOT_AUX8   0x08u
#define HG_SLOT_AUXC   0x0cu
#define HG_SLOT_VOLUME 0x10u
#define HG_SLOT_BANK2  0x14u
#define HG_SLOT_T2HDR  0x18u
#define HG_SLOT_T2PTR  0x1cu
#define HG_SLOT_T3HDR  0x20u
#define HG_SLOT_T3PTR  0x24u
#define HG_SLOT_T3PTR2 0x28u
#define HG_SLOT_STRIDE 0x2cu

/* Bank header (SShd at bank+0x0c; bank base = resource+descriptor+4). */
#define HG_BANK_MAGIC_OFF 0x0cu
#define HG_BANK_MAGIC     0x64685353u /* LE bytes "SShd" */
#define HG_BANK_OFF0      0x10u
#define HG_BANK_PTR0      0x30u
#define HG_BANK_STATE     0x7cu

/* 0x50-byte play command entry. */
#define HG_CMD_STATE   0x00u
#define HG_CMD_UNK4    0x04u
#define HG_CMD_SEQ     0x08u
#define HG_CMD_SLOT    0x0cu
#define HG_CMD_CHANNEL 0x10u
#define HG_CMD_PARAM1  0x14u
#define HG_CMD_PARAM2  0x18u
#define HG_CMD_TIME    0x20u
#define HG_CMD_FIELD28 0x28u
#define HG_CMD_FIELD2C 0x2cu
#define HG_CMD_FIELD30 0x30u
#define HG_CMD_FIELD34 0x34u
#define HG_CMD_FIELD38 0x38u
#define HG_CMD_FIELD3C 0x3cu
#define HG_CMD_SAMPLE  0x40u
#define HG_CMD_STRIDE  0x50u

/* Channel (0x9e4 stride) and sub-voice (0x74 stride at +0x2a4). */
#define HG_CH_VOICES   0x2a4u
#define HG_CH_CMD_RING 0x200u
#define HG_CH_STRIDE   0x9e4u
#define HG_VOICE_STRIDE 0x74u
#define HG_VOICE_COUNT 16u

/* External targets on the bus. */
#define HG_X_INTS_OFF    0x0010cca0u
#define HG_X_INTS_ON     0x0010cce8u
#define HG_X_FLUSHCACHE  0x001041e0u
#define HG_X_SIF_SET_DMA 0x00104320u
#define HG_X_SIF_DMA_STAT 0x00104300u
#define HG_X_SIF_ALLOC   0x0010af40u
#define HG_X_SIF_MSCOM   0x0010b0a8u
#define HG_X_GLOBAL_TICK 0x002a74d0u
#define HG_X_TIME_A      0x001165d0u
#define HG_X_TIME_B      0x00116320u
#define HG_X_PLAY_EXTRA  0x002ad358u
#define HG_X_FLAG_SVC    0x002adab0u

uint32_t hg_audio_register_entry(HGAudioBus *bus, uint32_t resource);
uint32_t hg_audio_register_worker(HGAudioBus *bus, uint32_t resource);
uint32_t hg_audio_transfer_setup(HGAudioBus *bus, uint32_t dest, uint32_t src,
                                 uint32_t size, uint32_t wait);
uint32_t hg_audio_align16(HGAudioBus *bus, uint32_t size);
uint32_t hg_audio_slot_find(HGAudioBus *bus);
uint32_t hg_audio_bank_register(HGAudioBus *bus, uint32_t slot, uint32_t bank);
uint32_t hg_audio_bank_index(HGAudioBus *bus, uint32_t slot, uint32_t index);
uint32_t hg_audio_slot_active(HGAudioBus *bus, uint32_t slot);
void hg_audio_slot_state(HGAudioBus *bus, uint32_t slot, uint32_t out_auxc,
                         uint32_t out_state);
uint32_t hg_audio_table_lookup4(HGAudioBus *bus, uint32_t slot, uint32_t index,
                                uint32_t extra);
uint32_t hg_audio_table_lookup6(HGAudioBus *bus, uint32_t slot, uint32_t index);
uint32_t hg_audio_table_lookup1(HGAudioBus *bus, uint32_t slot, uint32_t index);
uint32_t hg_audio_null8(uint32_t value);
void hg_audio_slot_range18(HGAudioBus *bus, uint32_t slot, uint32_t out_18,
                           uint32_t out_1c);
uint32_t hg_audio_slot_range20(HGAudioBus *bus, uint32_t slot, uint32_t out_20,
                               uint32_t out_24);
uint32_t hg_audio_slot_swap(HGAudioBus *bus, uint32_t slot, uint32_t value);
uint32_t hg_audio_slot_get(HGAudioBus *bus, uint32_t slot);
uint32_t hg_audio_bank_register_wrapper(HGAudioBus *bus, uint32_t bank);
uint32_t hg_audio_slot_bank(HGAudioBus *bus, uint32_t slot);
void hg_audio_slot_clear(HGAudioBus *bus, uint32_t slot);
void hg_audio_slots_init(HGAudioBus *bus);
uint32_t hg_audio_open_channel_a(HGAudioBus *bus, uint32_t slot,
                                 uint32_t param);
uint32_t hg_audio_open_channel_b(HGAudioBus *bus, uint32_t slot,
                                 uint32_t param);
uint32_t hg_audio_channel_init_a(HGAudioBus *bus, uint32_t channel);
void hg_audio_channel_init_b(HGAudioBus *bus, uint32_t channel);
uint32_t hg_audio_play(HGAudioBus *bus, uint32_t slot, uint32_t param1,
                       uint32_t param2, uint32_t param3, uint32_t t0);
uint32_t hg_audio_cancel(HGAudioBus *bus, uint32_t index);
void hg_audio_channel_command(HGAudioBus *bus, uint32_t channel, uint32_t command);
uint32_t hg_audio_next_sequence(HGAudioBus *bus);
uint32_t hg_audio_bank_bind(HGAudioBus *bus, uint32_t entry, uint32_t volume);

#ifdef __cplusplus
}
#endif

#endif
