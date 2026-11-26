#include "audio_iop.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#define SPU_BASE 0xbf900000u
#define CORE_STRIDE 0x400u
#define ADPCM_BLOCK 16u
#define VOICES_PER_CORE 24u
#define VOICE_MASK 0x00ffffffu
#define FRACTION_ONE 0x1000u
#define DMA_IDLE 0u
#define DMA_QUEUED 1u
#define DMA_DONE 2u
#define EE_UPLOAD_STAGED 1u
#define EE_UPLOAD_WAITING 2u

typedef struct HGVoice {
    uint32_t start, block, loop, phase;
    uint16_t left, right, pitch;
    int16_t decoded[28];
    int16_t recent, older;
    unsigned index, flags;
    bool active;
} HGVoice;

struct HGAudioIop {
    HGSpu2Io io;
    HGSg2Packet queue[HG_SG2_QUEUE_SIZE];
    unsigned head, tail;
    HGSg2Packet upload;
    unsigned dma_state;
    HGVoice voice[2][VOICES_PER_CORE];
    uint32_t noise[2];
    uint8_t ram[HG_SPU2_RAM_SIZE];
    uint8_t uploaded[HG_SPU2_RAM_SIZE / ADPCM_BLOCK];
};

static uint32_t read_le(const uint8_t *bytes)
{
    return (uint32_t)bytes[0] | (uint32_t)bytes[1] << 8 |
           (uint32_t)bytes[2] << 16 | (uint32_t)bytes[3] << 24;
}

static void write_le(uint8_t *bytes, uint32_t word)
{
    for (unsigned i = 0; i < 4; ++i)
        bytes[i] = (uint8_t)(word >> (8 * i));
}

static HGSg2Packet read_packet(const uint8_t *bytes)
{
    return (HGSg2Packet){read_le(bytes), read_le(bytes + 4),
                        read_le(bytes + 8), read_le(bytes + 12)};
}

static bool valid_span(uint32_t address, size_t size)
{
    return address <= HG_SPU2_RAM_SIZE && size <= HG_SPU2_RAM_SIZE - address;
}

static void write_spu(HGAudioIop *iop, uint32_t core, uint32_t offset, uint16_t value)
{
    if (iop->io.write16)
        iop->io.write16(iop->io.ctx, SPU_BASE + core * CORE_STRIDE + offset, value);
}

/* The original pitch table is generated from an exponential curve. These
 * measured residuals preserve its integer rounding and two larger anomalies. */
static uint32_t pitch_anchor(int index)
{
    static const struct { int index, delta; } corrections[] = {
        {1,-1}, {5,1}, {13,1}, {62,1}, {64,-75}, {121,1}, {131,1}, {141,1},
        {144,1}, {156,1}, {159,1}, {170,1}, {171,1}, {172,-256}, {173,1},
        {174,1}, {175,1}, {179,1}, {187,1}, {198,1}, {201,1}, {205,1},
        {444,1}, {448,1}, {490,1}, {491,1}, {528,1}, {550,1}, {552,1},
        {586,1}, {587,1}, {591,1}
    };
    int value = (int)(4096.0 * exp2((index - 208) / 192.0));
    for (size_t n = 0; n < sizeof(corrections) / sizeof(corrections[0]); ++n)
        if (corrections[n].index == index)
            return (uint32_t)(value + corrections[n].delta);
    return (uint32_t)value;
}

static int pitch_delta(HGSg2Packet packet)
{
    return (int)(packet.arg2 >> 16 & 0xffu) - (int)(packet.arg2 >> 24);
}

static int pitch_index(HGSg2Packet packet)
{
    int delta = pitch_delta(packet);
    int semitone = delta < 0 ? 12 - (-delta % 12) : delta % 12;
    int fine = (int)(packet.arg2 >> 8 & 0xffu);
    if (fine >= 128)
        fine -= 256;
    int bend = ((int)(packet.arg2 & 0xffu) - 64) * (int)(packet.arg3 >> 24);
    return 208 + 16 * semitone + fine + (bend - (bend < 0 ? 3 : 0)) / 4;
}

static uint16_t voice_pitch(HGSg2Packet packet)
{
    int delta = pitch_delta(packet);
    uint32_t anchor = pitch_anchor(pitch_index(packet));
    uint32_t rate = delta < 0 ? anchor >> (-delta / 12 + 1) : anchor << (delta / 12);
    return (uint16_t)(((rate * 441u / 480u) * (packet.arg3 & 0xffffu)) >> 12);
}

static bool valid_packet(const HGAudioIop *iop, HGSg2Packet packet, bool *upload)
{
    switch (packet.command) {
    case HG_SG2_NOP:
        return true;
    case HG_SG2_GAIN:
    case HG_SG2_ADSR:
        return packet.arg1 < HG_SG2_VOICES;
    case HG_SG2_START_ADDRESS:
        return packet.arg1 < HG_SG2_VOICES &&
               packet.arg2 % ADPCM_BLOCK == 0 && valid_span(packet.arg2, ADPCM_BLOCK);
    case HG_SG2_PITCH: {
        int index = pitch_index(packet);
        int delta = pitch_delta(packet);
        return packet.arg1 < HG_SG2_VOICES && index >= 0 && index < 608 &&
               (packet.arg3 & 0xffffu) <= 0x3fffu && delta >= -36 && delta <= 36;
    }
    case HG_SG2_KEY_ON:
    case HG_SG2_KEY_OFF:
    case HG_SG2_WET_MASK:
    case HG_SG2_NOISE_MASK:
        return (packet.arg2 | packet.arg3) <= VOICE_MASK;
    case HG_SG2_UPLOAD:
        if (*upload || !iop->io.read_iop || !packet.arg3 ||
            (packet.arg1 | packet.arg2 | packet.arg3) % ADPCM_BLOCK ||
            !valid_span(packet.arg2, packet.arg3))
            return false;
        *upload = true;
        return true;
    default:
        return false;
    }
}

static void set_voice_mask(HGAudioIop *iop, uint32_t core, uint32_t offset, uint32_t mask)
{
    write_spu(iop, core, offset, (uint16_t)mask);
    write_spu(iop, core, offset + 2, (uint16_t)(mask >> 16));
}

static void set_keys(HGAudioIop *iop, uint32_t core, uint32_t mask, bool on)
{
    set_voice_mask(iop, core, on ? 0x1a0u : 0x1a4u, mask);
    for (unsigned index = 0; index < VOICES_PER_CORE; ++index) {
        HGVoice *voice = &iop->voice[core][index];
        if (!(mask & (1u << index)))
            continue;
        voice->active = on;
        if (on) {
            voice->loop = voice->block = voice->start;
            voice->phase = voice->flags = 0;
            voice->recent = voice->older = 0;
            voice->index = 28;
        }
    }
}

static uint16_t gain(uint32_t input)
{
    return (uint16_t)(input >> 31 ? 0x7fffu + input : input);
}

static void dispatch(HGAudioIop *iop, HGSg2Packet packet)
{
    const uint32_t core = packet.arg1 / VOICES_PER_CORE;
    const uint32_t index = packet.arg1 % VOICES_PER_CORE;
    const uint32_t offset = index * 16;
    HGVoice *voice;

    switch (packet.command) {
    case HG_SG2_GAIN:
        voice = &iop->voice[core][index];
        voice->left = gain(packet.arg2);
        voice->right = gain(packet.arg3);
        write_spu(iop, core, offset, voice->left);
        write_spu(iop, core, offset + 2, voice->right);
        break;
    case HG_SG2_ADSR:
        write_spu(iop, core, offset + 6, (uint16_t)packet.arg2);
        write_spu(iop, core, offset + 8, (uint16_t)packet.arg3);
        break;
    case HG_SG2_START_ADDRESS:
        iop->voice[core][index].start = packet.arg2;
        write_spu(iop, core, 0x1c0u + 12 * index, (uint16_t)(packet.arg2 >> 17));
        write_spu(iop, core, 0x1c2u + 12 * index, (uint16_t)(packet.arg2 >> 1 & 0xfff8u));
        break;
    case HG_SG2_PITCH:
        voice = &iop->voice[core][index];
        voice->pitch = voice_pitch(packet);
        write_spu(iop, core, offset + 4, voice->pitch);
        break;
    case HG_SG2_KEY_ON:
    case HG_SG2_KEY_OFF:
        set_keys(iop, 0, packet.arg2, packet.command == HG_SG2_KEY_ON);
        set_keys(iop, 1, packet.arg3, packet.command == HG_SG2_KEY_ON);
        break;
    case HG_SG2_WET_MASK:
        set_voice_mask(iop, 0, 0x18c, packet.arg2);
        set_voice_mask(iop, 0, 0x194, packet.arg2);
        set_voice_mask(iop, 1, 0x18c, packet.arg3);
        set_voice_mask(iop, 1, 0x194, packet.arg3);
        break;
    case HG_SG2_NOISE_MASK:
        set_voice_mask(iop, 0, 0x184, packet.arg2);
        set_voice_mask(iop, 1, 0x184, packet.arg3);
        iop->noise[0] = packet.arg2;
        iop->noise[1] = packet.arg3;
        break;
    case HG_SG2_UPLOAD:
        iop->upload = packet;
        iop->dma_state = DMA_QUEUED;
        break;
    default:
        break;
    }
}

HGAudioIop *hg_audio_iop_create(HGSpu2Io io)
{
    HGAudioIop *iop = calloc(1, sizeof(*iop));
    if (iop)
        iop->io = io;
    return iop;
}

void hg_audio_iop_destroy(HGAudioIop *iop)
{
    free(iop);
}

bool hg_audio_iop_rpc(HGAudioIop *iop, uint32_t server, uint32_t function,
                     const uint8_t *bytes, size_t size, HGSg2Reply *reply)
{
    if (!iop || !reply || server != HG_SG2_RPC_ID || function != 0 ||
        size % 16 || size / 16 >= HG_SG2_QUEUE_SIZE || (size && !bytes))
        return false;

    bool upload = iop->dma_state != DMA_IDLE;
    for (size_t at = 0; at < size; at += 16)
        if (!valid_packet(iop, read_packet(bytes + at), &upload))
            return false;

    for (size_t at = 0; at < size; at += 16) {
        iop->queue[iop->tail] = read_packet(bytes + at);
        iop->tail = (iop->tail + 1) % HG_SG2_QUEUE_SIZE;
    }
    while (iop->head != iop->tail) {
        dispatch(iop, iop->queue[iop->head]);
        iop->head = (iop->head + 1) % HG_SG2_QUEUE_SIZE;
    }
    reply->upload_busy = iop->dma_state == DMA_QUEUED;
    if (iop->dma_state == DMA_DONE)
        iop->dma_state = DMA_IDLE;
    for (unsigned core = 0; core < 2; ++core) {
        for (unsigned index = 0; index < VOICES_PER_CORE; ++index) {
            uint32_t address = SPU_BASE + core * CORE_STRIDE + index * 16 + 0x0a;
            reply->envelope[core * VOICES_PER_CORE + index] = iop->io.read16
                ? iop->io.read16(iop->io.ctx, address)
                : iop->voice[core][index].active ? 0x7fff : 0;
        }
    }
    return true;
}

bool hg_audio_iop_step_dma(HGAudioIop *iop)
{
    if (!iop || iop->dma_state != DMA_QUEUED)
        return false;

    HGSg2Packet packet = iop->upload;
    uint8_t *staging = malloc(packet.arg3);
    if (!staging)
        return false;
    if (!iop->io.read_iop(iop->io.ctx, packet.arg1, staging, packet.arg3)) {
        free(staging);
        return false;
    }
    if (iop->io.voice_transfer)
        iop->io.voice_transfer(iop->io.ctx, 0, 0, packet.arg1, packet.arg2, packet.arg3);
    memcpy(iop->ram + packet.arg2, staging, packet.arg3);
    free(staging);
    memset(iop->uploaded + packet.arg2 / ADPCM_BLOCK, 1, packet.arg3 / ADPCM_BLOCK);
    iop->dma_state = DMA_DONE;
    return true;
}

bool hg_audio_iop_read_spu(const HGAudioIop *iop, uint32_t address,
                          uint8_t *out, size_t size)
{
    if (!iop || !out || !valid_span(address, size))
        return false;
    memcpy(out, iop->ram + address, size);
    return true;
}

static int16_t clip16(int32_t sample)
{
    if (sample < INT16_MIN)
        return INT16_MIN;
    if (sample > INT16_MAX)
        return INT16_MAX;
    return (int16_t)sample;
}

static bool decode_block(const HGAudioIop *iop, HGVoice *voice)
{
    static const int coeff[5][2] = {{0, 0}, {60, 0}, {115, -52}, {98, -55}, {122, -60}};
    if (voice->flags & 1u) {
        if (!(voice->flags & 2u))
            return false;
        voice->block = voice->loop;
    }
    if (!valid_span(voice->block, ADPCM_BLOCK) || !iop->uploaded[voice->block / ADPCM_BLOCK])
        return false;
    const uint8_t *bytes = iop->ram + voice->block;
    unsigned filter = bytes[0] >> 4;
    unsigned shift = bytes[0] & 15u;
    if (filter >= 5 || shift > 12)
        return false;

    voice->flags = bytes[1];
    if (voice->flags & 4u)
        voice->loop = voice->block;
    for (unsigned i = 0; i < 28; ++i) {
        int nibble = bytes[2 + i / 2] >> (4 * (i % 2)) & 15;
        if (nibble >= 8)
            nibble -= 16;
        int prediction = (coeff[filter][0] * voice->recent + coeff[filter][1] * voice->older + 32);
        /* Divide with rounding toward negative infinity. */
        prediction = (prediction - (prediction < 0 ? 63 : 0)) / 64;
        int16_t sample = clip16(nibble * (1 << (12 - shift)) + prediction);
        voice->decoded[i] = sample;
        voice->older = voice->recent;
        voice->recent = sample;
    }
    voice->block += ADPCM_BLOCK;
    voice->index = 0;
    return true;
}

static int16_t advance_voice(const HGAudioIop *iop, HGVoice *voice)
{
    if (!voice->active)
        return 0;
    if (voice->index == 28 && !decode_block(iop, voice)) {
        voice->active = false;
        return 0;
    }
    int16_t sample = voice->decoded[voice->index];
    voice->phase += voice->pitch > 0x3fffu ? 0x3fffu : voice->pitch;
    while (voice->phase >= FRACTION_ONE) {
        voice->phase -= FRACTION_ONE;
        if (++voice->index == 28 && !decode_block(iop, voice)) {
            voice->active = false;
            break;
        }
    }
    return sample;
}

static int32_t mix_gain(int16_t sample, uint16_t volume)
{
    /* Fixed-volume mode uses signed 15-bit gain. Sweeps are outside the model. */
    if (volume & 0x8000u)
        return 0;
    int gain15 = (int)(volume & 0x3fffu) - (int)(volume & 0x4000u);
    return sample * gain15 / 0x4000;
}

void hg_audio_iop_render(HGAudioIop *iop, int16_t *stereo, size_t frames)
{
    if (!iop)
        return;
    for (size_t frame = 0; frame < frames; ++frame) {
        int32_t left = 0, right = 0;
        for (unsigned core = 0; core < 2; ++core)
            for (unsigned index = 0; index < VOICES_PER_CORE; ++index) {
                HGVoice *voice = &iop->voice[core][index];
                int16_t sample = advance_voice(iop, voice);
                if (!(iop->noise[core] & (1u << index))) {
                    left += mix_gain(sample, voice->left);
                    right += mix_gain(sample, voice->right);
                }
            }
        if (stereo) {
            *stereo++ = clip16(left);
            *stereo++ = clip16(right);
        }
    }
}

void hg_audio_ee_service_init(HGAudioEeService *service, HGAudioBus *bus,
                              HGAudioIop *iop)
{
    *service = (HGAudioEeService){.bus = bus, .iop = iop};
    bus->w32(bus->ctx, HG_SG2_EE_CURSOR, 0);
}

bool hg_audio_ee_post(HGAudioEeService *service, HGSg2Packet packet)
{
    HGAudioBus *bus = service->bus;
    uint32_t count = bus->r32(bus->ctx, HG_SG2_EE_CURSOR);
    if (count >= HG_SG2_QUEUE_SIZE - 1)
        return false;
    uint32_t dest = HG_SG2_EE_PACKETS + count * 16;
    bus->w32(bus->ctx, dest, packet.command);
    bus->w32(bus->ctx, dest + 4, packet.arg1);
    bus->w32(bus->ctx, dest + 8, packet.arg2);
    bus->w32(bus->ctx, dest + 12, packet.arg3);
    bus->w32(bus->ctx, HG_SG2_EE_CURSOR, count + 1);
    return true;
}

bool hg_audio_ee_flush(HGAudioEeService *service, HGSg2Reply *reply)
{
    HGAudioBus *bus = service->bus;
    uint32_t count = bus->r32(bus->ctx, HG_SG2_EE_CURSOR);
    bool pending = service->upload_state == EE_UPLOAD_STAGED &&
                   count < HG_SG2_QUEUE_SIZE - 1;
    uint8_t wire[(HG_SG2_QUEUE_SIZE - 1) * 16];

    if (count >= HG_SG2_QUEUE_SIZE)
        return false;
    for (uint32_t at = 0; at < count * 16; at += 4)
        write_le(wire + at, bus->r32(bus->ctx, HG_SG2_EE_PACKETS + at));

    if (pending) {
        uint8_t *end = wire + count++ * 16;
        write_le(end, service->upload.command);
        write_le(end + 4, service->upload.arg1);
        write_le(end + 8, service->upload.arg2);
        write_le(end + 12, service->upload.arg3);
    }
    if (!hg_audio_iop_rpc(service->iop, HG_SG2_RPC_ID, 0, wire, count * 16, reply))
        return false;
    bus->w32(bus->ctx, HG_SG2_EE_CURSOR, 0);
    if (pending || service->upload_state == EE_UPLOAD_WAITING)
        service->upload_state = reply->upload_busy ? EE_UPLOAD_WAITING : DMA_IDLE;
    return true;
}

uint32_t hg_audio_ee_bank_call(HGAudioEeService *service, uint32_t target,
                                uint32_t arg1, uint32_t arg2, uint32_t arg3)
{
    if (target == HG_SG2_BANK_BIND) {
        HGAudioBus *bus = service->bus;
        if (!hg_audio_slot_active(bus, arg1))
            return UINT32_MAX;
        bus->w32(bus->ctx, HG_AUDIO_SLOT_TABLE + arg1 * HG_SLOT_STRIDE + HG_SLOT_AUXC, arg2);
        return 0;
    }
    if (target == HG_SG2_UPLOAD_POLL)
        return arg1 ? UINT32_MAX : service->upload_state != DMA_IDLE;
    if (target != HG_SG2_UPLOAD_START || service->upload_state != DMA_IDLE)
        return UINT32_MAX;
    HGSg2Packet packet = {HG_SG2_UPLOAD, arg1, arg2, arg3};
    bool upload = service->iop->dma_state != DMA_IDLE;
    if (!valid_packet(service->iop, packet, &upload))
        return UINT32_MAX;
    service->upload = packet;
    service->upload_state = EE_UPLOAD_STAGED;
    return 0;
}

bool hg_audio_ee_sample_address(const HGAudioEeService *service, uint32_t slot,
                                uint32_t offset, uint32_t *address)
{
    HGAudioBus *bus = service->bus;
    if (!address || offset % ADPCM_BLOCK || !hg_audio_slot_active(bus, slot))
        return false;
    uint32_t entry = HG_AUDIO_SLOT_TABLE + slot * HG_SLOT_STRIDE;
    uint32_t bank = bus->r32(bus->ctx, entry + HG_SLOT_BANK);
    uint32_t size = bus->r32(bus->ctx, bank + 4);
    uint32_t start = bus->r32(bus->ctx, entry + HG_SLOT_AUXC);
    if (offset > size || size - offset < ADPCM_BLOCK ||
        !start || !valid_span(start, size) || start % ADPCM_BLOCK ||
        !service->iop->uploaded[(start + offset) / ADPCM_BLOCK])
        return false;
    *address = start + offset;
    return true;
}

bool hg_audio_ee_stage_voice(HGAudioEeService *service, uint32_t voice, uint32_t slot,
                             uint32_t sample_offset, HGSg2VoiceParams params)
{
    HGAudioBus *bus = service->bus;
    uint32_t address;
    if (voice >= HG_SG2_VOICES || params.root > 255 || params.note > 255 ||
        params.scale > 0x3fff ||
        bus->r32(bus->ctx, HG_SG2_EE_CURSOR) > HG_SG2_QUEUE_SIZE - 5 ||
        !hg_audio_ee_sample_address(service, slot, sample_offset, &address))
        return false;
    uint32_t note = params.root << 24 | params.note << 16 |
                    (uint32_t)(uint8_t)params.fine_offset << 8 | params.bend;
    HGSg2Packet pitch = {HG_SG2_PITCH, voice, note,
                         (uint32_t)params.bend_scale << 24 | params.scale};
    bool upload = false;
    if (!valid_packet(service->iop, pitch, &upload))
        return false;

    return hg_audio_ee_post(service, (HGSg2Packet){HG_SG2_ADSR, voice, params.adsr1, params.adsr2}) &&
           hg_audio_ee_post(service, (HGSg2Packet){HG_SG2_START_ADDRESS, voice, address, 0}) &&
           hg_audio_ee_post(service, (HGSg2Packet){HG_SG2_GAIN, voice, params.left, params.right}) &&
           hg_audio_ee_post(service, pitch);
}

bool hg_audio_ee_stage_play_voice(HGAudioEeService *service, uint32_t play_index,
                                  uint32_t voice, uint32_t sample_offset,
                                  HGSg2VoiceParams params)
{
    HGAudioBus *bus = service->bus;
    if (play_index >= 32)
        return false;
    uint32_t command = HG_AUDIO_COMMAND_BASE + play_index * HG_CMD_STRIDE;
    if (bus->r32(bus->ctx, command + HG_CMD_STATE) != 1)
        return false;
    return hg_audio_ee_stage_voice(service, voice, bus->r32(bus->ctx, command + HG_CMD_SLOT),
                                   sample_offset, params);
}

bool hg_audio_ee_stage_key(HGAudioEeService *service, uint32_t voice, bool on)
{
    if (voice >= HG_SG2_VOICES)
        return false;
    uint32_t mask = 1u << (voice % VOICES_PER_CORE);
    return hg_audio_ee_post(service, (HGSg2Packet){on ? HG_SG2_KEY_ON : HG_SG2_KEY_OFF, 0,
                         voice < VOICES_PER_CORE ? mask : 0, voice >= VOICES_PER_CORE ? mask : 0});
}
