#ifndef LAB3_BACKOFF_H
#define LAB3_BACKOFF_H

#include <stdint.h>

// Backoff state
typedef struct {
    uint32_t countdown; // slots remaining to wait
    uint32_t attempts; // number of collision attempts
} backoff_state_t;

// Initialize backoff state on the first transmission attempt.
backoff_state_t backoff_init(void);

// On collision failure, compute BEB window and pick random wait (uses rand())
backoff_state_t backoff_on_collision(backoff_state_t state);

// Decrement the countdown by 1 slot, called on every slot
backoff_state_t backoff_tick(backoff_state_t state);

// 16-attempt give-up
int backoff_give_up(backoff_state_t state);

#endif
