#include "cdma.h"

size_t cdma_chip_count(size_t nbytes, int L) {
    return nbytes * 8 * (size_t)L;
}

void cdma_spread(const uint8_t *data, size_t nbytes, const int8_t *code, int L, int8_t *chips) {
    size_t idx = 0;

    for(size_t b = 0 ; b<nbytes ; b++) {
        uint8_t byte = data[b];
        
        for(int i = 7 ; i>=0 ; i--) { // iterating down from MSB
            int bit = (byte >> i) & 1;
            int sign = bit ? 1 : -1;

            for(int k = 0 ; k<L ; k++) {
                chips[idx] = sign * code[k];
                idx++;
            }
        }
    }
}

void cdma_sum(const int8_t *const *chips, int n, size_t nchips, int16_t *sum) {
    for(size_t k = 0 ; k<nchips ; k++) {
        int total = 0;

        for(int i = 0; i<n ; i++) {
            if(chips[i] != NULL) { // if station not silent
                total += chips[i][k];
            }
        }

        sum[k] = (int16_t)total;
    }
}


int32_t cdma_correlate(const int16_t *sum, const int8_t *code, int L) {
    int32_t total = 0;
    for(int k = 0 ; k<L ; k++) {
        total += (int32_t)sum[k] * (int32_t)code[k]; // get single station sum of bits
    }

    return total;
}


cdma_bit_t cdma_decide(int32_t corr, int L) {
    if (corr == L)  return CDMA_BIT_ONE;
    if (corr == -L) return CDMA_BIT_ZERO;
    if (corr == 0)  return CDMA_BIT_SILENT;
    return CDMA_BIT_INVALID;
}   


cdma_frame_t cdma_despread(const int16_t *sum, size_t nbytes, const int8_t *code, int L, uint8_t *data, cdma_stats_t *st) {
    size_t ones = 0, zeros = 0, silent = 0, invalid = 0;
    size_t bit_index = 0; // runs 0..nbytes*8-1, same order cdma_spread wrote chips in

    for(size_t b = 0; b<nbytes; b++) {
        uint8_t byte = 0;

        for(int pos = 7 ; pos>= 0 ; pos--) {
            const int16_t *block = sum + (bit_index * (size_t)L); // this bit's L-chip slice
            int32_t corr = cdma_correlate(block , code , L);
            cdma_bit_t decision = cdma_decide(corr , L);

            if (decision == CDMA_BIT_ONE) {
                ones++;
                byte |= (1 << pos);
            }
            else if(decision == CDMA_BIT_ZERO) zeros++;
            else if(decision == CDMA_BIT_SILENT) silent++;
            else invalid++;

            bit_index++;
        }

        data[b] = byte;
    }

    if (st) {
        st->ones += ones;
        st->zeros += zeros;
        st->silent += silent;
        st->invalid += invalid;
    }

    size_t total_bits = nbytes * 8;
    if (invalid > 0) return CDMA_FRAME_ERROR;
    if (total_bits == silent) return CDMA_FRAME_SILENT;
    if (silent == 0) return CDMA_FRAME_DATA;

    return CDMA_FRAME_ERROR;
}