#ifndef LAB4_TEST_UTIL_H
#define LAB4_TEST_UTIL_H

/* The one assert framework for every lab4 test binary (CLAUDE.md R1).
 * Include it directly; each test file is its own translation unit, so
 * the `static` counters below don't collide across binaries. */

#include <stdio.h>
#include <string.h>
#include <math.h>

static int test_util_pass_count = 0;
static int test_util_fail_count = 0;
static const char *test_util_current = "";

#define TEST_BEGIN(name) do { \
    test_util_current = (name); \
    fprintf(stderr, "-- %s --\n", test_util_current); \
} while (0)

#define assert_true(cond, msg) do { \
    if (cond) { \
        test_util_pass_count++; \
    } else { \
        test_util_fail_count++; \
        fprintf(stderr, "FAIL [%s] %s (%s:%d)\n", \
                test_util_current, (msg), __FILE__, __LINE__); \
    } \
} while (0)

#define assert_eq_i64(actual, expected, msg) do { \
    long long test_util_a_ = (long long)(actual); \
    long long test_util_e_ = (long long)(expected); \
    if (test_util_a_ == test_util_e_) { \
        test_util_pass_count++; \
    } else { \
        test_util_fail_count++; \
        fprintf(stderr, "FAIL [%s] %s: got %lld, want %lld (%s:%d)\n", \
                test_util_current, (msg), test_util_a_, test_util_e_, \
                __FILE__, __LINE__); \
    } \
} while (0)

#define assert_eq_mem(actual, expected, n, msg) do { \
    size_t test_util_n_ = (size_t)(n); \
    if (memcmp((actual), (expected), test_util_n_) == 0) { \
        test_util_pass_count++; \
    } else { \
        test_util_fail_count++; \
        fprintf(stderr, "FAIL [%s] %s: %zu bytes differ (%s:%d)\n", \
                test_util_current, (msg), test_util_n_, __FILE__, __LINE__); \
    } \
} while (0)

#define assert_approx(actual, expected, eps, msg) do { \
    double test_util_a_ = (double)(actual); \
    double test_util_e_ = (double)(expected); \
    double test_util_d_ = fabs(test_util_a_ - test_util_e_); \
    if (test_util_d_ <= (double)(eps)) { \
        test_util_pass_count++; \
    } else { \
        test_util_fail_count++; \
        fprintf(stderr, "FAIL [%s] %s: got %g, want %g +/- %g (%s:%d)\n", \
                test_util_current, (msg), test_util_a_, test_util_e_, \
                (double)(eps), __FILE__, __LINE__); \
    } \
} while (0)

/* Prints the pass/fail totals and returns the process exit code
 * (0 = every assertion passed). */
static int test_summary(void) {
    fprintf(stderr, "%d passed, %d failed\n",
            test_util_pass_count, test_util_fail_count);
    return test_util_fail_count == 0 ? 0 : 1;
}

#endif
