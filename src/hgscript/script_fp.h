#ifndef HG_SCRIPT_FP_H
#define HG_SCRIPT_FP_H

#include <stdint.h>
#include <string.h>

static inline float hg_script_float(uint32_t bits) {
    float value;
    memcpy(&value, &bits, sizeof value);
    return value;
}

static inline uint32_t hg_script_bits(float value) {
    uint32_t bits;
    memcpy(&bits, &value, sizeof bits);
    return bits;
}

/* Finite EE display arithmetic. Division uses host binary32 nearest;
 * addition has one alignment guard bit and addition/multiply chop. */
static inline double hg_script_fp_input(uint32_t bits) {
    return (bits & 0x7f800000u) ? (double)hg_script_float(bits) : 0.0;
}

static inline uint32_t hg_script_chop(double result) {
    uint32_t bits = hg_script_bits((float)result);
    double rounded = (double)hg_script_float(bits);
    if ((result > 0 && rounded > result) || (result < 0 && rounded < result))
        --bits;
    return (bits & 0x7f800000u) ? bits : 0;
}

static inline uint32_t hg_script_add(uint32_t left, uint32_t right) {
    int delta = (int)((left >> 23) & 255) - (int)((right >> 23) & 255);
    unsigned shift = (unsigned)(delta > 0 ? delta : -delta);
    if (shift) {
        uint32_t mask = shift >= 25 ? 0x80000000u : UINT32_MAX << (shift - 1);
        if (delta > 0) right &= mask;
        else left &= mask;
    }
    return hg_script_chop(hg_script_fp_input(left) + hg_script_fp_input(right));
}

static inline uint32_t hg_script_sub(uint32_t left, uint32_t right) {
    return hg_script_add(left, right ^ 0x80000000u);
}

static inline uint32_t hg_script_mul(uint32_t left, uint32_t right) {
    return hg_script_chop(hg_script_fp_input(left) * hg_script_fp_input(right));
}

#endif
