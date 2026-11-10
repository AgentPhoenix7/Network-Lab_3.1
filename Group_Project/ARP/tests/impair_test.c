#include <stdlib.h>
#include <string.h>
#include "impair.h"
#include "test_util.h"

/* Counts FATE_LOST over n draws from rand() with srand(1). */
static int lost_count(double loss, int n) {
    int lost = 0;
    srand(1);
    for (int i = 0; i < n; i++) {
        double draw = (double)rand() / RAND_MAX;
        if (impair_decide(loss, 0.0, draw) == FATE_LOST) lost++;
    }
    return lost;
}

static int popcount_xor(const uint8_t *a, const uint8_t *b, size_t n) {
    int bits = 0;
    for (size_t i = 0; i < n; i++) {
        uint8_t x = a[i] ^ b[i];
        for (int k = 0; k < 8; k++) bits += (x >> k) & 1;
    }
    return bits;
}

int main(void) {
    TEST_BEGIN("I1 loss 0 -> never lost");
    assert_eq_i64(lost_count(0.0, 10000), 0, "0 of 10000 lost at loss 0");

    TEST_BEGIN("I1 loss 1 -> always lost");
    assert_eq_i64(lost_count(1.0, 10000), 10000, "10000 of 10000 lost at loss 1");

    TEST_BEGIN("I1 loss 0.3 -> 30 +/- 3 % over 10000 draws");
    assert_approx(lost_count(0.3, 10000) / 10000.0, 0.3, 0.03, "loss rate near 0.3");

    TEST_BEGIN("I1b one draw partitions into lost / corrupt / ok");
    assert_eq_i64(impair_decide(0.2, 0.3, 0.1), FATE_LOST, "draw below loss -> lost");
    assert_eq_i64(impair_decide(0.2, 0.3, 0.3), FATE_CORRUPT, "draw in corrupt band -> corrupt");
    assert_eq_i64(impair_decide(0.2, 0.3, 0.9), FATE_OK, "draw above both -> ok");

    TEST_BEGIN("I2 corruption flips exactly one bit");
    {
        uint8_t orig[IMPAIR_FRAME_LEN], frame[IMPAIR_FRAME_LEN];
        int bad = 0;
        srand(2);
        for (int trial = 0; trial < 1000; trial++) {
            for (int i = 0; i < IMPAIR_FRAME_LEN; i++) orig[i] = (uint8_t)rand();
            memcpy(frame, orig, sizeof frame);
            size_t idx = impair_corrupt(frame);
            if (popcount_xor(orig, frame, IMPAIR_FRAME_LEN) != 1) bad++;
            if (idx >= 8 * IMPAIR_FRAME_LEN) bad++;
            else if (((orig[idx / 8] ^ frame[idx / 8]) >> (idx % 8) & 1) != 1) bad++;
        }
        assert_eq_i64(bad, 0, "1000 frames: one bit flipped, returned index matches");
    }

    return test_summary();
}
