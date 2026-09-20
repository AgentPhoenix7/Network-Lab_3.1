#include "backoff.h"
#include <stdlib.h>
#include <time.h>

backoff_state_t backoff_init(void) {
    return (backoff_state_t) {
        .countdown = 0,
        .attempts = 1
    };
}

backoff_state_t backoff_on_collision(backoff_state_t state) {
    uint32_t k = state.attempts < 10 ? state.attempts : 10;
    uint32_t max_wait = (1 << k) - 1;
    uint32_t random_countdown = rand() % (max_wait + 1);

    state.attempts++;
    state.countdown = random_countdown;

    return state;
}

backoff_state_t backoff_tick(backoff_state_t state) {
    if(state.countdown > 0) {
        state.countdown--;
    }
    return state;
}

int backoff_give_up(backoff_state_t state) {
    if(state.attempts > 16) {
        return 1;
    }
    return 0;
}