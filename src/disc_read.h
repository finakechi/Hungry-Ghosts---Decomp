#ifndef HG_DISC_READ_H
#define HG_DISC_READ_H
#include <stdint.h>

/* Original 32-bit EE address values remain integers, never host pointers. */
typedef struct {
    uint32_t state_word;       /* +00: only low byte used by poll */
    uint32_t error;            /* +04 */
    uint32_t file_record;      /* +08: points to { LBA, byte length, ... } */
    uint32_t destination;      /* +0c */
    uint32_t byte_offset;      /* +10: signed, truncates toward zero to sectors */
    uint32_t byte_length;      /* +14: negative selects whole file length */
    uint32_t next_lba;         /* +18 */
    uint32_t sector_count;     /* +1c */
    uint32_t sectors_issued;   /* +20 */
    uint32_t next_destination; /* +24 */
    uint32_t between_chunks;   /* +28 */
} HGReadState;

/* External operations are isolated because their implementations are not
 * reconstructed here. API names are interpretations of the call sites.
 * mode[0..2] are observed as {0,1,0}; fourth stack byte is unspecified. */
typedef struct {
    void *user;
    uint32_t (*read32)(void *, uint32_t);
    int32_t (*sync)(void *, int32_t);                 /* 0010f520 */
    int32_t (*disk_ready)(void *, int32_t);           /* 0010fd28 */
    int32_t (*read)(void *, uint32_t, uint32_t, uint32_t, const uint8_t *); /* 0010ff30 */
    int32_t (*get_error)(void *);                     /* 00110268 */
    uint8_t inhibit;           /* 003eace4: only value 1 blocks issuing */
    uint8_t active;            /* 003eace8 */
    uint8_t chunk_flag_a;      /* 003f2c80 */
    uint8_t chunk_flag_b;      /* 003f2fa0 */
} HGReadEnv;

void hg_read_setup(HGReadState *, uint32_t, uint32_t, uint32_t, uint32_t); /* 001319c0 */
int32_t hg_read_start(HGReadState *, HGReadEnv *);    /* 00131bf0 */
int32_t hg_read_continue(HGReadState *, HGReadEnv *); /* 001319e0 */
int32_t hg_read_poll(HGReadState *, HGReadEnv *);     /* 001318f0 */
#endif
