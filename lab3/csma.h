#ifndef LAB3_CSMA_H
#define LAB3_CSMA_H

#include <stdint.h>
#include "protocol.h"
#include "backoff.h"

#define P_max 10
#define P_prob 5

typedef enum {
    CSMA_NONPERSISTENT = 0,
    CSMA_ONEPERSISTENT = 1,
    CSMA_PPERSISTENT = 2,
    CSMA_CD = 3
} csma_strategy_t;

typedef struct {
    uint8_t active; // currently transmitting
    uint8_t sensing; // listening for IDLE (1-persistent, p-persistent)
    uint8_t jamming; // sending JAM signal (CSMA/CD)
    uint8_t frame_age; // slots since ACT_START
    result_t last_result; // result of previous attempt
    backoff_state_t backoff; // countdown and attempt count
} csma_state_t;

// Initialize CSMA state for first transmission attempt
csma_state_t csma_init(void);

// return action for this slot given medium state, station state, strategy.
// Uses rand() for random waits and p-persistent coin flip. Updates state in place.
action_type_t csma_step(csma_state_t *state,
                        medium_state_t medium,
                        int slot_frame_len,
                        csma_strategy_t strategy,
                        double p,
                        result_t last_result);

#endif
