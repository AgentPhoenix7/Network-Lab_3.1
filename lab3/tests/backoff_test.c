#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include "../backoff.h"

// Simple test framework
static int pass = 0, fail = 0;

#define assert_eq(a, b, msg) do { \
    if((a) != (b)) { \
        printf("FAIL: %s (got %lu, expected %lu)\n", msg, (uint64_t)(a), (uint64_t)(b)); \
        fail++; \
    } else { \
        printf("PASS: %s\n", msg); \
        pass++; \
    } \
} while(0)

#define assert_true(cond, msg) do { \
    if(!(cond)) { \
        printf("FAIL: %s\n", msg); \
        fail++; \
    } else { \
        printf("PASS: %s\n", msg); \
        pass++; \
    } \
} while(0)

void test_init(void) {
    backoff_state_t s = backoff_init();
    assert_eq(s.countdown, 0, "init: countdown=0");
    assert_eq(s.attempts, 1, "init: attempts=1");
}

void test_window_sizes(void) {
    // BEB window = 2^min(k,10) - 1
    // k=1: 2^1 - 1 = 1
    // k=2: 2^2 - 1 = 3
    // k=3: 2^3 - 1 = 7
    // k=10: 2^10 - 1 = 1023
    // k=11+: 2^10 - 1 = 1023 (capped)

    srand(42);

    backoff_state_t s = backoff_init();

    // Attempt 1 -> attempt 2, window should be 2^1 - 1 = 1
    s = backoff_on_collision(s);
    assert_eq(s.attempts, 2, "after collision 1: attempts=2");
    assert_true(s.countdown <= 1, "after collision 1: countdown <= 1");

    // Attempt 2 -> attempt 3, window should be 2^2 - 1 = 3
    s = backoff_on_collision(s);
    assert_eq(s.attempts, 3, "after collision 2: attempts=3");
    assert_true(s.countdown <= 3, "after collision 2: countdown <= 3");

    // Attempt 3 -> attempt 4, window should be 2^3 - 1 = 7
    s = backoff_on_collision(s);
    assert_eq(s.attempts, 4, "after collision 3: attempts=4");
    assert_true(s.countdown <= 7, "after collision 3: countdown <= 7");
}

void test_tick(void) {
    srand(42);
    backoff_state_t s = backoff_init();

    // Keep trying until we get a non-zero countdown
    while(s.countdown == 0) {
        s = backoff_on_collision(s);
    }

    uint32_t initial = s.countdown;
    s = backoff_tick(s);
    assert_eq(s.countdown, initial - 1, "tick: decrements countdown");

    // Tick down to 0
    while(s.countdown > 0) {
        s = backoff_tick(s);
    }
    assert_eq(s.countdown, 0, "tick: reaches 0");

    // Tick at 0 should not go negative
    s = backoff_tick(s);
    assert_eq(s.countdown, 0, "tick: does not go below 0");
}

void test_give_up(void) {
    backoff_state_t s = backoff_init();

    for(int i = 1; i <= 16; i++) {
        s.attempts = i;
        assert_eq(backoff_give_up(s), 0, "give_up: returns 0 for attempts <= 16");
    }

    s.attempts = 17;
    assert_eq(backoff_give_up(s), 1, "give_up: returns 1 for attempts > 16");
}

void test_determinism(void) {
    // Same seed should produce same sequence
    srand(12345);
    backoff_state_t s1 = backoff_init();
    for(int i = 0; i < 5; i++) {
        s1 = backoff_on_collision(s1);
        s1 = backoff_tick(s1);
    }

    srand(12345);
    backoff_state_t s2 = backoff_init();
    for(int i = 0; i < 5; i++) {
        s2 = backoff_on_collision(s2);
        s2 = backoff_tick(s2);
    }

    assert_eq(s1.countdown, s2.countdown, "determinism: same seed, same countdown");
    assert_eq(s1.attempts, s2.attempts, "determinism: same seed, same attempts");
}

int main(void) {
    printf("\n=== backoff_test ===\n");

    test_init();
    test_window_sizes();
    test_tick();
    test_give_up();
    test_determinism();

    printf("\n%d passed, %d failed\n", pass, fail);
    return fail > 0 ? 1 : 0;
}
