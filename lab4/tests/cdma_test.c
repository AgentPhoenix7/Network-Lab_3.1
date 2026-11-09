#include "test_util.h"
#include "../cdma.h"
#include "../walsh.h"

#include <stdlib.h>
#include <string.h>

#define MAX_N 64
#define MAX_L 64
#define NBYTES 65 /* one lab1 frame's worth, matches tests.md examples */

static void fill_random(uint8_t *buf, size_t n, unsigned seed) {
    srand(seed);
    for (size_t i = 0; i < n; i++) buf[i] = (uint8_t)(rand() & 0xFF);
}

/* Spreads frames[i] (NULL => station i silent) with station i's Walsh
 * code and sums them all. chip_storage must hold n * cdma_chip_count(nbytes,L)
 * int8_t; chip_ptrs must hold n pointers (scratch, filled by this helper). */
static void spread_and_sum(int n, int L, uint8_t *const *frames, size_t nbytes,
                            int8_t *chip_storage, const int8_t **chip_ptrs,
                            int16_t *sum) {
    size_t nchips = cdma_chip_count(nbytes, L);
    int8_t code[MAX_L];
    for (int i = 0; i < n; i++) {
        if (frames[i] == NULL) {
            chip_ptrs[i] = NULL;
            continue;
        }
        walsh_code(L, i, code);
        int8_t *dst = chip_storage + (size_t)i * nchips;
        cdma_spread(frames[i], nbytes, code, L, dst);
        chip_ptrs[i] = dst;
    }
    cdma_sum(chip_ptrs, n, nchips, sum);
}

/* C1 */
static void test_chip_count(void) {
    TEST_BEGIN("C1 cdma_chip_count");
    assert_eq_i64(cdma_chip_count(65, 8), 4160, "(65,8) -> 4160");
    assert_eq_i64(cdma_chip_count(1, 1), 8, "(1,1) -> 8");
    assert_eq_i64(cdma_chip_count(0, 4), 0, "(0,4) -> 0");
}

/* C2 */
static void test_bit_order_and_mapping(void) {
    TEST_BEGIN("C2 bit order and mapping");
    uint8_t byte = 0x80;
    int8_t code[1] = { 1 };
    int8_t chips[8];
    cdma_spread(&byte, 1, code, 1, chips);
    int8_t expected[8] = { 1, -1, -1, -1, -1, -1, -1, -1 };
    assert_eq_mem(chips, expected, sizeof(expected), "0x80, L=1 gives MSB-first +1/-1 chips");
}

/* C3 */
static void test_spread_block_structure(void) {
    TEST_BEGIN("C3 spread block structure");
    uint8_t byte = 0xA5;
    int8_t code[2] = { 1, -1 };
    int8_t chips[16];
    cdma_spread(&byte, 1, code, 2, chips);
    int8_t expected[16] = {
         1, -1,  -1,  1,   1, -1,  -1,  1,
        -1,  1,   1, -1,  -1,  1,   1, -1,
    };
    assert_eq_mem(chips, expected, sizeof(expected), "0xA5, L=2 matches +c,-c,+c,-c,-c,+c,-c,+c");
}

/* C4 */
static void test_spread_range(void) {
    TEST_BEGIN("C4 spread range");
    uint8_t data[65];
    fill_random(data, sizeof(data), 4);
    int L = 16;
    int8_t code[16];
    walsh_code(L, 0, code);
    int8_t chips[65 * 8 * 16];
    cdma_spread(data, sizeof(data), code, L, chips);

    int ok_pm1 = 1, ok_blocks = 1;
    size_t idx = 0;
    for (size_t b = 0; b < sizeof(data) && ok_blocks; b++) {
        for (int pos = 7; pos >= 0 && ok_blocks; pos--) {
            int bit = (data[b] >> pos) & 1;
            int sign = bit ? 1 : -1;
            for (int k = 0; k < L; k++) {
                int8_t c = chips[idx + k];
                if (c != 1 && c != -1) ok_pm1 = 0;
                if (c != sign * code[k]) ok_blocks = 0;
            }
            idx += L;
        }
    }
    assert_true(ok_pm1, "every chip is +-1");
    assert_true(ok_blocks, "every block equals +-code");
}

/* C5 */
static void test_cdma_sum(void) {
    TEST_BEGIN("C5 cdma_sum");
    {
        int8_t a[4] = { 1, -1, 1, -1 };
        int8_t b[4] = { 1, 1, -1, -1 };
        const int8_t *chips[2] = { a, b };
        int16_t sum[4];
        cdma_sum(chips, 2, 4, sum);
        int16_t expected[4] = { 2, 0, 0, -2 };
        assert_true(memcmp(sum, expected, sizeof(expected)) == 0, "two known vectors sum elementwise");
    }
    {
        int8_t a[4] = { 1, -1, 1, -1 };
        const int8_t *chips[2] = { a, NULL };
        int16_t sum[4];
        cdma_sum(chips, 2, 4, sum);
        int ok = 1;
        for (int k = 0; k < 4; k++) if (sum[k] != a[k]) ok = 0;
        assert_true(ok, "NULL entry contributes 0");
    }
    {
        const int8_t *chips[3] = { NULL, NULL, NULL };
        int16_t sum[4] = { 9, 9, 9, 9 };
        cdma_sum(chips, 3, 4, sum);
        int16_t zeros[4] = { 0, 0, 0, 0 };
        assert_true(memcmp(sum, zeros, sizeof(zeros)) == 0, "all NULL gives all zeros");
    }
    {
        int n = 8, L = 8;
        int8_t storage[8][8];
        const int8_t *chips[8];
        srand(5);
        for (int i = 0; i < n; i++) {
            for (int k = 0; k < L; k++) storage[i][k] = (rand() % 2) ? 1 : -1;
            chips[i] = storage[i];
        }
        int16_t sum[8];
        cdma_sum(chips, n, L, sum);
        int ok = 1;
        for (int k = 0; k < L; k++) if (sum[k] > n || sum[k] < -n) ok = 0;
        assert_true(ok, "random n=8: max|S| <= 8");
    }
}

/* C6 */
static void test_worked_example(void) {
    TEST_BEGIN("C6 worked example (L=4)");
    int16_t S[4] = { 1, 1, -1, 3 };
    int8_t c0[4], c1[4], c2[4], c3[4];
    walsh_code(4, 0, c0);
    walsh_code(4, 1, c1);
    walsh_code(4, 2, c2);
    walsh_code(4, 3, c3);
    assert_eq_i64(cdma_correlate(S, c0, 4), 4, "S.c0 == 4");
    assert_eq_i64(cdma_correlate(S, c1, 4), -4, "S.c1 == -4");
    assert_eq_i64(cdma_correlate(S, c2, 4), 0, "S.c2 == 0");
    assert_eq_i64(cdma_correlate(S, c3, 4), 4, "S.c3 == 4");
}

/* C7 */
static void test_cdma_decide(void) {
    TEST_BEGIN("C7 cdma_decide");
    assert_true(cdma_decide(8, 8) == CDMA_BIT_ONE, "+8 -> ONE");
    assert_true(cdma_decide(-8, 8) == CDMA_BIT_ZERO, "-8 -> ZERO");
    assert_true(cdma_decide(0, 8) == CDMA_BIT_SILENT, "0 -> SILENT");
    int bad[] = { 2, -6, 9, 7 };
    for (size_t i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
        assert_true(cdma_decide(bad[i], 8) == CDMA_BIT_INVALID, "off value -> INVALID");
    }
}

/* C8 */
static void test_single_sender_round_trip(void) {
    TEST_BEGIN("C8 single sender round trip");
    uint8_t data[NBYTES];
    fill_random(data, sizeof(data), 8);

    for (int trial = 0; trial < 2; trial++) {
        int L = (trial == 0) ? 1 : 2;
        int8_t code[2];
        walsh_code(L, 0, code);
        size_t nchips = cdma_chip_count(NBYTES, L);
        int8_t *chips = malloc(nchips);
        cdma_spread(data, NBYTES, code, L, chips);
        int16_t *sum = malloc(nchips * sizeof(int16_t));
        for (size_t k = 0; k < nchips; k++) sum[k] = chips[k];

        uint8_t out[NBYTES];
        cdma_stats_t st = { 0, 0, 0, 0 };
        cdma_frame_t fr = cdma_despread(sum, NBYTES, code, L, out, &st);

        assert_true(fr == CDMA_FRAME_DATA, "single sender returns DATA");
        assert_true(memcmp(data, out, NBYTES) == 0, "decodes identically");
        assert_eq_i64(st.ones + st.zeros, 520, "ones+zeros == 520");
        assert_eq_i64(st.silent, 0, "silent == 0");
        assert_eq_i64(st.invalid, 0, "invalid == 0");

        free(chips);
        free(sum);
    }
}

/* C9 */
static void test_full_load(void) {
    TEST_BEGIN("C9 full load n=L=8");
    int n = 8, L = 8;
    uint8_t data[8][NBYTES];
    uint8_t *frames[8];
    for (int i = 0; i < n; i++) {
        fill_random(data[i], NBYTES, 900 + i);
        frames[i] = data[i];
    }
    size_t nchips = cdma_chip_count(NBYTES, L);
    int8_t *storage = malloc((size_t)n * nchips);
    const int8_t *chip_ptrs[8];
    int16_t *sum = malloc(nchips * sizeof(int16_t));
    spread_and_sum(n, L, frames, NBYTES, storage, chip_ptrs, sum);

    int ok = 1;
    for (int i = 0; i < n; i++) {
        int8_t code[8];
        walsh_code(L, i, code);
        uint8_t out[NBYTES];
        cdma_frame_t fr = cdma_despread(sum, NBYTES, code, L, out, NULL);
        if (fr != CDMA_FRAME_DATA || memcmp(data[i], out, NBYTES) != 0) ok = 0;
    }
    assert_true(ok, "every station decodes its own data exactly");
    free(storage);
    free(sum);
}

/* C10 */
static void test_n_not_power_of_2(void) {
    TEST_BEGIN("C10 n not a power of 2");
    int n = 5, L = 8;
    uint8_t data[5][NBYTES];
    uint8_t *frames[5];
    for (int i = 0; i < n; i++) {
        fill_random(data[i], NBYTES, 1000 + i);
        frames[i] = data[i];
    }
    size_t nchips = cdma_chip_count(NBYTES, L);
    int8_t *storage = malloc((size_t)n * nchips);
    const int8_t *chip_ptrs[5];
    int16_t *sum = malloc(nchips * sizeof(int16_t));
    spread_and_sum(n, L, frames, NBYTES, storage, chip_ptrs, sum);

    int ok = 1;
    for (int i = 0; i < n; i++) {
        int8_t code[8];
        walsh_code(L, i, code);
        uint8_t out[NBYTES];
        cdma_frame_t fr = cdma_despread(sum, NBYTES, code, L, out, NULL);
        if (fr != CDMA_FRAME_DATA || memcmp(data[i], out, NBYTES) != 0) ok = 0;
    }
    assert_true(ok, "stations 0..4 are exact");

    int ok_unused = 1;
    for (int i = n; i < L; i++) {
        int8_t code[8];
        walsh_code(L, i, code);
        uint8_t out[NBYTES];
        cdma_frame_t fr = cdma_despread(sum, NBYTES, code, L, out, NULL);
        if (fr != CDMA_FRAME_SILENT) ok_unused = 0;
    }
    assert_true(ok_unused, "unused codes 5,6,7 give SILENT");
    free(storage);
    free(sum);
}

/* C11 */
static void test_partial_silence(void) {
    TEST_BEGIN("C11 partial silence");
    int n = 8, L = 8;
    uint8_t data[8][NBYTES];
    uint8_t *frames[8];
    for (int i = 0; i < n; i++) {
        if (i == 2 || i == 5) {
            frames[i] = NULL;
        } else {
            fill_random(data[i], NBYTES, 1100 + i);
            frames[i] = data[i];
        }
    }
    size_t nchips = cdma_chip_count(NBYTES, L);
    int8_t *storage = malloc((size_t)n * nchips);
    const int8_t *chip_ptrs[8];
    int16_t *sum = malloc(nchips * sizeof(int16_t));
    spread_and_sum(n, L, frames, NBYTES, storage, chip_ptrs, sum);

    int ok = 1;
    for (int i = 0; i < n; i++) {
        int8_t code[8];
        walsh_code(L, i, code);
        uint8_t out[NBYTES];
        cdma_stats_t st = { 0, 0, 0, 0 };
        cdma_frame_t fr = cdma_despread(sum, NBYTES, code, L, out, &st);
        if (i == 2 || i == 5) {
            if (fr != CDMA_FRAME_SILENT || st.silent != 520) ok = 0;
        } else {
            if (fr != CDMA_FRAME_DATA || memcmp(data[i], out, NBYTES) != 0) ok = 0;
        }
    }
    assert_true(ok, "stations 2,5 SILENT (silent==520), the rest exact");
    free(storage);
    free(sum);
}

/* C12 */
static void test_perturbation_detected(void) {
    TEST_BEGIN("C12 perturbation detected");
    int n = 8, L = 8;
    uint8_t data[8][NBYTES];
    uint8_t *frames[8];
    for (int i = 0; i < n; i++) {
        fill_random(data[i], NBYTES, 1200 + i);
        frames[i] = data[i];
    }
    size_t nchips = cdma_chip_count(NBYTES, L);
    int8_t *storage = malloc((size_t)n * nchips);
    const int8_t *chip_ptrs[8];
    int16_t *sum = malloc(nchips * sizeof(int16_t));
    spread_and_sum(n, L, frames, NBYTES, storage, chip_ptrs, sum);

    sum[37] = (int16_t)(sum[37] + 1);

    int ok = 1;
    for (int i = 0; i < n; i++) {
        int8_t code[8];
        walsh_code(L, i, code);
        uint8_t out[NBYTES];
        cdma_stats_t st = { 0, 0, 0, 0 };
        cdma_frame_t fr = cdma_despread(sum, NBYTES, code, L, out, &st);
        if (fr != CDMA_FRAME_ERROR || st.invalid != 1) ok = 0;
    }
    assert_true(ok, "every station returns ERROR with invalid==1");
    free(storage);
    free(sum);
}

/* C13 */
static void test_stats_accumulate(void) {
    TEST_BEGIN("C13 stats accumulate");
    uint8_t data[NBYTES];
    fill_random(data, sizeof(data), 13);
    int L = 4;
    int8_t code[4];
    walsh_code(L, 0, code);
    size_t nchips = cdma_chip_count(NBYTES, L);
    int8_t *chips = malloc(nchips);
    cdma_spread(data, NBYTES, code, L, chips);
    int16_t *sum = malloc(nchips * sizeof(int16_t));
    for (size_t k = 0; k < nchips; k++) sum[k] = chips[k];

    uint8_t out[NBYTES];
    cdma_stats_t st = { 0, 0, 0, 0 };
    cdma_despread(sum, NBYTES, code, L, out, &st);
    size_t first_ones = st.ones, first_zeros = st.zeros;
    cdma_despread(sum, NBYTES, code, L, out, &st);
    assert_eq_i64(st.ones, first_ones * 2, "ones doubles");
    assert_eq_i64(st.zeros, first_zeros * 2, "zeros doubles");
    free(chips);
    free(sum);
}

/* C14 */
static void test_fuzz(void) {
    TEST_BEGIN("C14 fuzz (500 trials)");
    srand(14);
    int ok = 1;
    for (int trial = 0; trial < 500 && ok; trial++) {
        int n = 1 + (rand() % 32);
        int L = walsh_length_for(n);
        uint8_t data[MAX_N][NBYTES];
        uint8_t *frames[MAX_N];
        int silent_mask[MAX_N];
        for (int i = 0; i < n; i++) {
            silent_mask[i] = (rand() % 4 == 0); /* ~25% silent */
            if (silent_mask[i]) {
                frames[i] = NULL;
            } else {
                for (int b = 0; b < NBYTES; b++) data[i][b] = (uint8_t)(rand() & 0xFF);
                frames[i] = data[i];
            }
        }
        size_t nchips = cdma_chip_count(NBYTES, L);
        int8_t *storage = malloc((size_t)n * nchips);
        const int8_t *chip_ptrs[MAX_N];
        int16_t *sum = malloc(nchips * sizeof(int16_t));
        spread_and_sum(n, L, frames, NBYTES, storage, chip_ptrs, sum);

        for (int i = 0; i < n && ok; i++) {
            int8_t code[MAX_L];
            walsh_code(L, i, code);
            uint8_t out[NBYTES];
            cdma_stats_t st = { 0, 0, 0, 0 };
            cdma_frame_t fr = cdma_despread(sum, NBYTES, code, L, out, &st);
            if (st.invalid != 0) ok = 0;
            if (silent_mask[i]) {
                if (fr != CDMA_FRAME_SILENT) ok = 0;
            } else {
                if (fr != CDMA_FRAME_DATA || memcmp(data[i], out, NBYTES) != 0) ok = 0;
            }
        }
        free(storage);
        free(sum);
    }
    assert_true(ok, "500 random trials: every active station exact, every silent SILENT, invalid==0");
}

int main(void) {
    test_chip_count();
    test_bit_order_and_mapping();
    test_spread_block_structure();
    test_spread_range();
    test_cdma_sum();
    test_worked_example();
    test_cdma_decide();
    test_single_sender_round_trip();
    test_full_load();
    test_n_not_power_of_2();
    test_partial_silence();
    test_perturbation_detected();
    test_stats_accumulate();
    test_fuzz();
    return test_summary();
}
