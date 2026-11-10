#define _POSIX_C_SOURCE 200809L
#include "test_util.h"
#include "simclock.h"

#include <stdint.h>
#include <time.h>

static void sleep_ms(int ms) {
    struct timespec ts = { ms / 1000, (long)(ms % 1000) * 1000000L };
    nanosleep(&ts, NULL);
}

int main(void) {
    TEST_BEGIN("K1 changing speed does not make sim time jump");
    {
        simclock_set_speed(1.0);
        sleep_ms(30);
        uint64_t a = sim_now_ms();
        simclock_set_speed(4.0);
        uint64_t b = sim_now_ms();
        assert_true(b >= a, "time does not go backwards at set_speed");
        assert_true(b - a < 5, "time does not leap at set_speed (difference tiny)");

        sleep_ms(10);
        uint64_t c = sim_now_ms();
        assert_true(c - b >= 30, "speed 4 advances about 4x: 10 ms real gives >= 30 sim ms");
    }

    TEST_BEGIN("K2 speed 0 freezes time, speed 1 resumes");
    {
        simclock_set_speed(0.0);
        uint64_t a = sim_now_ms();
        sleep_ms(20);
        uint64_t b = sim_now_ms();
        assert_eq_i64(b, a, "paused: two reads 20 ms apart are equal");

        simclock_set_speed(1.0);
        uint64_t c = sim_now_ms();
        sleep_ms(20);
        uint64_t d = sim_now_ms();
        assert_true(d >= c + 15, "resumed: time advances after speed 1");
        assert_true(d - c < 200, "resumed: time advances at about real speed");
    }

    return test_summary();
}
