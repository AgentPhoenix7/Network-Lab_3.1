#include "csma.h"
#include <stdlib.h>

csma_state_t csma_init(void) {
    return (csma_state_t) {
        .active = 0,
        .sensing = 1,
        .jamming = 0,
        .frame_age = 0,
        .last_result = RES_NONE,
        .backoff = backoff_init()
    };
}

action_type_t csma_step(csma_state_t *state, medium_state_t medium, int slot_frame_len,
                        csma_strategy_t strategy, double p, result_t last_result) {
    //If jamming : return JAM, transition to backoff
    if(state->jamming) {
        state->jamming = 0;
        state->backoff = backoff_on_collision(state->backoff);
        return ACT_JAM;
    }

    //If actively transmitting
    if(state->frame_age > 0) {
        // Check for collision mid-transmission (CSMA/CD only)
        if(medium == MEDIUM_COLLISION && strategy == CSMA_CD) {
            state->jamming = 1;
            state->active = 0;
            state->frame_age = slot_frame_len + 1; // mark transmission done
            return ACT_IDLE;
        }

        state->frame_age++;

        // Check if transmission complete
        if(state->frame_age >= slot_frame_len) {
            state->active = 0;
            state->frame_age = 0;

            if(last_result == RES_COLLISION) {
                state->backoff = backoff_on_collision(state->backoff);
            } else if(last_result == RES_SUCCESS) {
                state->backoff = backoff_init();
            }

            state->sensing = 1;
            return ACT_IDLE;
        }

        // Still transmitting
        return ACT_TRANSMIT;
    }

    // If backoff countdown > 0 (waiting after collision)
    if(state->backoff.countdown > 0) {
        state->backoff = backoff_tick(state->backoff);
        return ACT_IDLE;
    }

    // Sensing for IDLE (not transmitting, backoff done)
    if(medium == MEDIUM_BUSY) {

        if(strategy == CSMA_ONEPERSISTENT || strategy == CSMA_CD) {
            return ACT_IDLE;
        } else if(strategy == CSMA_NONPERSISTENT) {
            state->backoff = backoff_on_collision(state->backoff);
            return ACT_IDLE;
        } else if(strategy == CSMA_PPERSISTENT) {
            state->backoff = backoff_tick(state->backoff);
            return ACT_IDLE;
        }

    } else if(medium == MEDIUM_IDLE) {

        if(strategy == CSMA_ONEPERSISTENT || strategy == CSMA_CD || strategy == CSMA_NONPERSISTENT) {
            state->active = 1;
            state->sensing = 0;
            state->frame_age = 1;
            return ACT_START;

        } else if(strategy == CSMA_PPERSISTENT) {
            // Coin flip: with probability p, start; else wait 1 slot
            double rand_val = (double)rand() / RAND_MAX;
            if(rand_val < p) {
                state->active = 1;
                state->sensing = 0;
                state->frame_age = 1;
                return ACT_START;
                
            } else {
                state->backoff = backoff_tick(state->backoff);
                return ACT_IDLE;
            }
        }
    }

    return ACT_IDLE;
}