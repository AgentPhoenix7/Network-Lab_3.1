#ifndef LAB4_CHANNEL_H
#define LAB4_CHANNEL_H
#include <stdint.h>
#include "protocol.h"
#include "walsh.h"

typedef struct {
    uint16_t port;
    int n; // n stations
    uint32_t max_rounds;
} channel_params_t;

typedef struct {
    uint32_t n, L;
    uint64_t rounds;
    uint64_t data_sends; // sum over rounds 
    uint64_t idle_sends; // sum of SND_IDLE
    uint64_t bytes_in; // all bytes read from senders
    uint64_t bytes_out; // all bytes written to receivers
    int32_t  max_abs_sum; // max |S[k]| seen
    uint64_t elapsed_ns; // first round start -> stop sent
    uint64_t sum_ns; // time spent inside cdma_sum
} channel_metrics_t;

int channel_serve(const channel_params_t *p, channel_metrics_t *out);

#endif