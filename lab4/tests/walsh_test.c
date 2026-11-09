#include "test_util.h"
#include "../walsh.h"

#include <string.h>

#define MAXBUF 256

static int8_t buf[MAXBUF * MAXBUF];
static int8_t buf2[MAXBUF * MAXBUF];
static int8_t code_buf[MAXBUF];
static int8_t code_buf2[MAXBUF];

/* W1 */
static void test_walsh_length_for(void) {
    TEST_BEGIN("W1 walsh_length_for");
    assert_eq_i64(walsh_length_for(1), 1, "1 -> 1");
    assert_eq_i64(walsh_length_for(2), 2, "2 -> 2");
    assert_eq_i64(walsh_length_for(3), 4, "3 -> 4");
    assert_eq_i64(walsh_length_for(4), 4, "4 -> 4");
    assert_eq_i64(walsh_length_for(5), 8, "5 -> 8");
    assert_eq_i64(walsh_length_for(8), 8, "8 -> 8");
    assert_eq_i64(walsh_length_for(9), 16, "9 -> 16");
    assert_eq_i64(walsh_length_for(17), 32, "17 -> 32");
    assert_eq_i64(walsh_length_for(33), 64, "33 -> 64");
    assert_eq_i64(walsh_length_for(64), 64, "64 -> 64");
    assert_eq_i64(walsh_length_for(0), -1, "0 -> -1");
    assert_eq_i64(walsh_length_for(-3), -1, "-3 -> -1");
}

/* W2 */
static void test_walsh_is_pow2(void) {
    TEST_BEGIN("W2 walsh_is_pow2");
    int true_vals[]  = { 1, 2, 4, 64, 1024 };
    int false_vals[] = { 0, -4, 3, 6, 12, 1023 };
    for (size_t i = 0; i < sizeof(true_vals) / sizeof(true_vals[0]); i++) {
        assert_true(walsh_is_pow2(true_vals[i]) == 1, "true case");
    }
    for (size_t i = 0; i < sizeof(false_vals) / sizeof(false_vals[0]); i++) {
        assert_true(walsh_is_pow2(false_vals[i]) == 0, "false case");
    }
}

/* W3 */
static void test_base_case(void) {
    TEST_BEGIN("W3 base case L=1");
    int rc = walsh_matrix(1, buf);
    assert_true(rc == 0, "walsh_matrix(1) returns 0");
    assert_eq_i64(buf[0], 1, "W_1 == [+1]");
}

/* W4 */
static void test_literal_small_matrices(void) {
    TEST_BEGIN("W4 literal W_2 and W_4");
    int8_t expected_w2[4] = { 1, 1, 1, -1 };
    int rc = walsh_matrix(2, buf);
    assert_true(rc == 0, "walsh_matrix(2) returns 0");
    assert_eq_mem(buf, expected_w2, sizeof(expected_w2), "W_2 matches literal");

    int8_t expected_w4[16] = {
        1,  1,  1,  1,
        1, -1,  1, -1,
        1,  1, -1, -1,
        1, -1, -1,  1,
    };
    rc = walsh_matrix(4, buf);
    assert_true(rc == 0, "walsh_matrix(4) returns 0");
    assert_eq_mem(buf, expected_w4, sizeof(expected_w4), "W_4 matches literal");
}

/* W5 */
static void test_entries_are_pm1(void) {
    TEST_BEGIN("W5 entries are +-1 only");
    for (int L = 1; L <= MAXBUF; L <<= 1) {
        int rc = walsh_matrix(L, buf);
        assert_true(rc == 0, "walsh_matrix returns 0");
        int ok = 1;
        for (int i = 0; i < L * L; i++) {
            if (buf[i] != 1 && buf[i] != -1) { ok = 0; break; }
        }
        assert_true(ok, "every entry is +1 or -1");
    }
}

/* W6 */
static void test_row_structure(void) {
    TEST_BEGIN("W6 row 0 all +1, other rows balanced");
    for (int L = 1; L <= MAXBUF; L <<= 1) {
        int rc = walsh_matrix(L, buf);
        assert_true(rc == 0, "walsh_matrix returns 0");

        int row0_ok = 1;
        for (int j = 0; j < L; j++) {
            if (buf[0 * L + j] != 1) { row0_ok = 0; break; }
        }
        assert_true(row0_ok, "row 0 is all +1");

        int balanced = 1;
        for (int i = 1; i < L; i++) {
            long sum = 0;
            for (int j = 0; j < L; j++) sum += buf[i * L + j];
            if (sum != 0) { balanced = 0; break; }
        }
        assert_true(balanced, "every other row sums to 0");
    }
}

/* W7 */
static void test_orthogonality(void) {
    TEST_BEGIN("W7 orthogonality: W * W^T == L * I");
    for (int L = 1; L <= MAXBUF; L <<= 1) {
        int rc = walsh_matrix(L, buf);
        assert_true(rc == 0, "walsh_matrix returns 0");

        int ok = 1;
        for (int i = 0; i < L && ok; i++) {
            for (int j = 0; j < L && ok; j++) {
                long dot = 0;
                for (int k = 0; k < L; k++) dot += (int)buf[i * L + k] * (int)buf[j * L + k];
                long expected = (i == j) ? L : 0;
                if (dot != expected) ok = 0;
            }
        }
        assert_true(ok, "W . W^T == L*I exactly");
    }
}

/* W8 */
static void test_sylvester_recursion(void) {
    TEST_BEGIN("W8 Sylvester recursion W_2L from W_L");
    for (int L = 1; L <= 128; L <<= 1) {
        int rc = walsh_matrix(L, buf);
        assert_true(rc == 0, "walsh_matrix(L) returns 0");
        rc = walsh_matrix(2 * L, buf2);
        assert_true(rc == 0, "walsh_matrix(2L) returns 0");

        int ok = 1;
        for (int i = 0; i < L && ok; i++) {
            for (int j = 0; j < L && ok; j++) {
                int8_t v = buf[i * L + j];
                if (buf2[i * (2 * L) + j] != v) ok = 0;              /* top-left  */
                if (buf2[i * (2 * L) + (j + L)] != v) ok = 0;        /* top-right */
                if (buf2[(i + L) * (2 * L) + j] != v) ok = 0;        /* bot-left  */
                if (buf2[(i + L) * (2 * L) + (j + L)] != -v) ok = 0; /* bot-right */
            }
        }
        assert_true(ok, "W_2L quadrants match [[W_L,W_L],[W_L,-W_L]]");
    }
}

/* W9 */
static void test_walsh_code_agrees_with_matrix(void) {
    TEST_BEGIN("W9 walsh_code agrees with walsh_matrix");
    const int L = 16;
    int rc = walsh_matrix(L, buf);
    assert_true(rc == 0, "walsh_matrix(16) returns 0");

    int ok = 1;
    for (int i = 0; i < L && ok; i++) {
        rc = walsh_code(L, i, code_buf);
        if (rc != 0) { ok = 0; break; }
        if (memcmp(code_buf, &buf[i * L], L) != 0) ok = 0;
    }
    assert_true(ok, "walsh_code(16, i) == row i of walsh_matrix(16) for every i");
    (void)code_buf2;
}

/* W10 */
static void test_argument_errors(void) {
    TEST_BEGIN("W10 argument errors");
    assert_true(walsh_matrix(3, buf) == -1, "walsh_matrix(3) -> -1 (not pow2)");
    assert_true(walsh_matrix(0, buf) == -1, "walsh_matrix(0) -> -1");
    assert_true(walsh_matrix(2048, buf) == -1, "walsh_matrix(2048) -> -1 (> WALSH_MAX_L)");
    assert_true(walsh_code(8, 8, code_buf) == -1, "walsh_code(8, 8) -> -1 (index == L)");
    assert_true(walsh_code(8, -1, code_buf) == -1, "walsh_code(8, -1) -> -1");
}

int main(void) {
    test_walsh_length_for();
    test_walsh_is_pow2();
    test_base_case();
    test_literal_small_matrices();
    test_entries_are_pm1();
    test_row_structure();
    test_orthogonality();
    test_sylvester_recursion();
    test_walsh_code_agrees_with_matrix();
    test_argument_errors();
    return test_summary();
}
