#ifndef SIMCLOCK_H
#define SIMCLOCK_H

#include <stdint.h>

/* Sim time in ms. Starts at 0 on the first call; speed starts at 1. */
uint64_t sim_now_ms(void);

/* Sim ms per real ms: 1 = real time, 0 = paused, negative is treated as 0.
 * Re-bases first, so the change never makes sim time jump. */
void simclock_set_speed(double x);

#endif
