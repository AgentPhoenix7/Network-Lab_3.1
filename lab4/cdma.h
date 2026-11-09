#ifndef LAB4_CDMA_H
#define LAB4_CDMA_H
#include <stdint.h>
#include <stddef.h>

typedef enum { CDMA_BIT_ZERO = 0, CDMA_BIT_ONE = 1, CDMA_BIT_SILENT = 2, CDMA_BIT_INVALID = 3 } cdma_bit_t;

typedef enum { CDMA_FRAME_DATA = 0, CDMA_FRAME_SILENT = 1, CDMA_FRAME_ERROR = 2 } cdma_frame_t;

typedef struct {
    size_t ones, zeros, silent, invalid; // per-bit decision counts
} cdma_stats_t;


size_t cdma_chip_count(size_t nbytes, int L); // nbytes * 8 * L

void cdma_spread(const uint8_t *data, size_t nbytes, const int8_t *code, int L, int8_t *chips); // spread cdma code into chips

void cdma_sum(const int8_t *const *chips, int n, size_t nchips, int16_t *sum); // sum of i stations over k bits

int32_t cdma_correlate(const int16_t *sum, const int8_t *code, int L); // extract ONE bit's L chips

cdma_bit_t cdma_decide(int32_t corr, int L);

// despread nbytes worth of chips from sum with code; 
cdma_frame_t cdma_despread(const int16_t *sum, size_t nbytes, const int8_t *code, int L, uint8_t *data, cdma_stats_t *st);

#endif