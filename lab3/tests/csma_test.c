#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include "../csma.h"

static int pass = 0, fail = 0;

#define assert_eq(a, b, msg) do { \
    if((a) != (b)) { \
        printf("FAIL: %s (got %d, expected %d)\n", msg, (int)(a), (int)(b)); \
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
    csma_state_t s = csma_init();
    assert_eq(s.active, 0, "init: active=0");
    assert_eq(s.sensing, 1, "init: sensing=1");
    assert_eq(s.jamming, 0, "init: jamming=0");
    assert_eq(s.frame_age, 0, "init: frame_age=0");
}

void test_onepersistent_idle(void) {
    srand(42);
    csma_state_t s = csma_init();
    int T_frame = 8;

    // Medium is IDLE, should START
    action_type_t a = csma_step(&s, MEDIUM_IDLE, T_frame, CSMA_ONEPERSISTENT, 0, RES_NONE);
    assert_eq(a, ACT_START, "1-persistent IDLE: returns ACT_START");
    assert_eq(s.active, 1, "1-persistent IDLE: sets active=1");
    assert_eq(s.frame_age, 1, "1-persistent IDLE: sets frame_age=1");
}

void test_onepersistent_busy(void) {
    srand(42);
    csma_state_t s = csma_init();
    int T_frame = 8;

    // Medium is BUSY, should keep sensing
    action_type_t a = csma_step(&s, MEDIUM_BUSY, T_frame, CSMA_ONEPERSISTENT, 0, RES_NONE);
    assert_eq(a, ACT_IDLE, "1-persistent BUSY: returns ACT_IDLE");
    assert_eq(s.sensing, 1, "1-persistent BUSY: keeps sensing=1");
}

void test_nonpersistent_busy(void) {
    srand(42);
    csma_state_t s = csma_init();
    int T_frame = 8;

    // Medium is BUSY, should backoff
    action_type_t a = csma_step(&s, MEDIUM_BUSY, T_frame, CSMA_NONPERSISTENT, 0, RES_NONE);
    assert_eq(a, ACT_IDLE, "non-persistent BUSY: returns ACT_IDLE");
    assert_eq(s.backoff.attempts, 2, "non-persistent BUSY: increments attempts to 2");
}

void test_ppersistent_idle_coin_flip(void) {
    // With p=1.0, should always START on IDLE
    srand(42);
    csma_state_t s = csma_init();
    int T_frame = 8;

    action_type_t a = csma_step(&s, MEDIUM_IDLE, T_frame, CSMA_PPERSISTENT, 1.0, RES_NONE);
    assert_eq(a, ACT_START, "p-persistent (p=1.0) IDLE: returns ACT_START");

    // With p=0.0, should always wait
    srand(42);
    s = csma_init();
    a = csma_step(&s, MEDIUM_IDLE, T_frame, CSMA_PPERSISTENT, 0.0, RES_NONE);
    assert_eq(a, ACT_IDLE, "p-persistent (p=0.0) IDLE: returns ACT_IDLE");
    assert_true(s.backoff.countdown <= 1, "p-persistent (p=0.0) IDLE: sets countdown");
}

void test_ppersistent_busy_wait(void) {
    srand(42);
    csma_state_t s = csma_init();
    int T_frame = 8;

    // Medium is BUSY, always wait 1 slot
    action_type_t a = csma_step(&s, MEDIUM_BUSY, T_frame, CSMA_PPERSISTENT, 0.5, RES_NONE);
    assert_eq(a, ACT_IDLE, "p-persistent BUSY: returns ACT_IDLE");
    assert_eq(s.backoff.countdown, 0, "p-persistent BUSY: countdown=0 (waits 1 slot next time)");
}

void test_transmission_lifecycle(void) {
    srand(42);
    csma_state_t s = csma_init();
    int T_frame = 4;

    // Start transmission
    csma_step(&s, MEDIUM_IDLE, T_frame, CSMA_ONEPERSISTENT, 0, RES_NONE);
    assert_eq(s.frame_age, 1, "transmit slot 0: frame_age=1");

    // Transmit slots 1, 2, 3
    for(int slot = 1; slot < T_frame - 1; slot++) {
        action_type_t a = csma_step(&s, MEDIUM_IDLE, T_frame, CSMA_ONEPERSISTENT, 0, RES_NONE);
        assert_eq(a, ACT_TRANSMIT, "transmit slot: returns ACT_TRANSMIT");
        assert_eq(s.frame_age, slot + 1, "transmit slot: frame_age increments");
    }

    // Transmission completes at slot T_frame-1
    action_type_t a = csma_step(&s, MEDIUM_IDLE, T_frame, CSMA_ONEPERSISTENT, 0, RES_SUCCESS);
    assert_eq(a, ACT_IDLE, "transmit complete: returns ACT_IDLE");
    assert_eq(s.active, 0, "transmit complete: active=0");
    assert_eq(s.sensing, 1, "transmit complete: sensing=1");
}

void test_csmacd_collision_jam(void) {
    srand(42);
    csma_state_t s = csma_init();
    int T_frame = 4;

    // Start transmission
    csma_step(&s, MEDIUM_IDLE, T_frame, CSMA_CD, 0, RES_NONE);

    // One slot of transmission
    csma_step(&s, MEDIUM_IDLE, T_frame, CSMA_CD, 0, RES_NONE);

    // Collision detected
    action_type_t a = csma_step(&s, MEDIUM_COLLISION, T_frame, CSMA_CD, 0, RES_NONE);
    assert_eq(a, ACT_IDLE, "CSMA/CD collision: immediate return ACT_IDLE");
    assert_eq(s.jamming, 1, "CSMA/CD collision: sets jamming=1");
    assert_eq(s.active, 0, "CSMA/CD collision: sets active=0");

    // Next slot, jam signal
    a = csma_step(&s, MEDIUM_IDLE, T_frame, CSMA_CD, 0, RES_NONE);
    assert_eq(a, ACT_JAM, "CSMA/CD jam: returns ACT_JAM");
    assert_eq(s.jamming, 0, "CSMA/CD jam: clears jamming");
    assert_eq(s.backoff.attempts, 2, "CSMA/CD jam: increments attempts on backoff");
}

void test_collision_recovery(void) {
    srand(123);
    csma_state_t s = csma_init();
    int T_frame = 4;

    // Slot 0: START
    csma_step(&s, MEDIUM_IDLE, T_frame, CSMA_ONEPERSISTENT, 0, RES_NONE);
    // Slots 1-3: TRANSMIT
    for(int i = 1; i < T_frame - 1; i++) {
        csma_step(&s, MEDIUM_IDLE, T_frame, CSMA_ONEPERSISTENT, 0, RES_NONE);
    }
    // Slot T_frame-1: transmission completes, gets result RES_COLLISION
    action_type_t a = csma_step(&s, MEDIUM_IDLE, T_frame, CSMA_ONEPERSISTENT, 0, RES_COLLISION);
    assert_eq(a, ACT_IDLE, "collision recovery: returns ACT_IDLE");
    assert_eq(s.backoff.attempts, 2, "collision recovery: attempts=2");

    // Now we're in backoff, wait for countdown to reach 0
    while(s.backoff.countdown > 0) {
        csma_step(&s, MEDIUM_IDLE, T_frame, CSMA_ONEPERSISTENT, 0, RES_NONE);
    }

    // After backoff, should START again on IDLE
    a = csma_step(&s, MEDIUM_IDLE, T_frame, CSMA_ONEPERSISTENT, 0, RES_NONE);
    assert_eq(a, ACT_START, "after backoff: can START again");
}

int main(void) {
    printf("\n=== csma_test ===\n");

    test_init();
    test_onepersistent_idle();
    test_onepersistent_busy();
    test_nonpersistent_busy();
    test_ppersistent_idle_coin_flip();
    test_ppersistent_busy_wait();
    test_transmission_lifecycle();
    test_csmacd_collision_jam();
    test_collision_recovery();

    printf("\n%d passed, %d failed\n", pass, fail);
    return fail > 0 ? 1 : 0;
}
