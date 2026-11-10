#include "simclock.h"
#include "timer.h"   /* lab2: timer_now_ns() */

/* Static module state: the sim clock is one per process (allowed in I/O shell files). */
static int      started;
static uint64_t base_real_ns;   /* real monotonic ns at the last re-base */
static double   base_sim_ms;    /* sim ms at that same instant (double keeps fractions) */
static double   speed = 1.0;

static void start_if_needed(void) {
    if (!started) {
        started = 1;
        base_real_ns = timer_now_ns();
    }
}

uint64_t sim_now_ms(void) {
    start_if_needed();
    uint64_t real = timer_now_ns();
    return (uint64_t)(base_sim_ms + (double)(real - base_real_ns) / 1e6 * speed);
}

void simclock_set_speed(double x) {
    start_if_needed();
    uint64_t real = timer_now_ns();
    /* Bank the time elapsed at the old speed before the new speed applies. */
    base_sim_ms += (double)(real - base_real_ns) / 1e6 * speed;
    base_real_ns = real;
    speed = x < 0 ? 0 : x;
}
