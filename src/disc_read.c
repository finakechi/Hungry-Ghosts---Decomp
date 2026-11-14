/* Evidence: analysis/disc_read/functions.json; manually reviewed R5900 words.
 * Behavior reconstruction, not original source and not a byte-matching build.
 * Arithmetic deliberately preserves 32-bit wrap and signed shifts.
 */
#include "disc_read.h"
#include <stddef.h>
_Static_assert(sizeof(HGReadState) == 0x2c, "EE state layout");
_Static_assert(offsetof(HGReadState, between_chunks) == 0x28, "EE state offsets");

static int64_t signed32(uint32_t value) {
    return value & UINT32_C(0x80000000) ? (int64_t)value - INT64_C(0x100000000) : value;
}
static uint32_t asr11(uint32_t value) {
    return (value >> 11) | (value & UINT32_C(0x80000000) ? UINT32_C(0xffe00000) : 0);
}
static int32_t result32(uint32_t value) { return (int32_t)signed32(value); }
static int chunked(const HGReadEnv *env) { return env->chunk_flag_a || env->chunk_flag_b; }
static int can_issue(HGReadEnv *env) {
    return env->sync(env->user, 1) == 0 && env->disk_ready(env->user, 1) == 2 && env->inhibit != 1;
}
static void issue(HGReadEnv *env, uint32_t lba, uint32_t count, uint32_t destination) {
    const uint8_t mode[3] = {0, 1, 0};
    env->active = 1;
    while (env->read(env->user, lba, count, destination, mode) == 0)
        env->disk_ready(env->user, 0);
}

void hg_read_setup(HGReadState *s, uint32_t file, uint32_t dst, uint32_t offset, uint32_t length) {
    s->file_record = file;
    s->destination = dst;
    s->byte_offset = offset;
    s->byte_length = length;
    s->state_word = 0;
    s->error = 0;
    /* +18..28 deliberately retain their previous values. */
}

int32_t hg_read_start(HGReadState *s, HGReadEnv *env) {
    uint32_t sectors;
    if (signed32(s->byte_length) < 0) {
        sectors = (env->read32(env->user, s->file_record + 4) + UINT32_C(0x7ff)) >> 11;
    } else {
        uint32_t rounded = s->byte_length + UINT32_C(0x7ff);
        if (signed32(rounded) < 0) rounded += UINT32_C(0x7ff);
        sectors = asr11(rounded);
    }
    uint32_t offset = s->byte_offset;
    if (signed32(offset) < 0) offset += UINT32_C(0x7ff);
    uint32_t lba = asr11(offset) + env->read32(env->user, s->file_record);
    s->next_lba = lba;
    s->sector_count = sectors;
    s->sectors_issued = 0;
    s->next_destination = s->destination;
    s->between_chunks = 0;
    if (!can_issue(env)) return -1;
    if (chunked(env)) {
        if (sectors >= 0x80) sectors = 0x80;
        s->sectors_issued = sectors;
        s->next_lba += sectors;
        s->next_destination += sectors << 11;
    } else {
        s->sectors_issued = s->sector_count;
    }
    issue(env, lba, sectors, s->destination);
    return result32(sectors << 11);
}

int32_t hg_read_continue(HGReadState *s, HGReadEnv *env) {
    if (s->between_chunks == 0) {
        if (env->sync(env->user, 1) != 0) return 0;
        s->error = 0; /* in the call delay slot, before get_error */
        int32_t error = env->get_error(env->user);
        if (error != 0) {
            s->error = (uint32_t)error;
            return 2; /* active is intentionally not cleared on error */
        }
        env->active = 0;
        if (s->sectors_issued == s->sector_count) return 1;
        s->between_chunks = 1;
        return 0;
    }
    if (!can_issue(env)) return 0;
    uint32_t lba = s->next_lba;
    uint32_t destination = s->next_destination;
    uint32_t count = s->sector_count - s->sectors_issued;
    if (chunked(env)) {
        if (count >= 0x80) count = 0x80;
        s->sectors_issued += count;
        s->next_lba += count;
        s->next_destination += count << 11;
    } else {
        s->sectors_issued = s->sector_count;
    }
    s->between_chunks = 0;
    issue(env, lba, count, destination);
    return 0;
}

int32_t hg_read_poll(HGReadState *s, HGReadEnv *env) {
    uint8_t state = (uint8_t)s->state_word;
    if (state == 1) {
        int32_t result = hg_read_continue(s, env);
        if (result == 1) return 1;
        if (result != 0) s->state_word = (s->state_word & UINT32_C(0xffffff00)) | (uint8_t)((uint8_t)s->state_word - 1);
    } else if (state == 0) {
        if (s->byte_length == 0) return 1;
        if (hg_read_start(s, env) != -1)
            s->state_word = (s->state_word & UINT32_C(0xffffff00)) | (uint8_t)((uint8_t)s->state_word + 1);
    }
    return 0;
}
