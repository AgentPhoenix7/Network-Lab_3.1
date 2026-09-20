#ifndef LAB3_STATION_H
#define LAB3_STATION_H
#include <stdint.h>
#include "protocol.h"
#include "csma.h"

#define CSMA_PORT_DEFAULT 9300

// Workload generator type
typedef enum {
    WORKLOAD_SATURATED = 0,
    WORKLOAD_POISSON = 1
} workload_type_t;

// Station state: tracks the current frame being transmitted, queue state, etc.
typedef struct {
    int station_id;
    csma_state_t csma;
    uint32_t frame_uid;
    uint32_t enqueue_slot;
    int has_frame;
    frame_t frame;
    result_t pending_result;
    uint64_t total_frames_sent;
    uint64_t total_collisions;
} station_state_t;

// Workload config passed from main
typedef struct {
    workload_type_t type;
    double lambda;
    uint32_t max_frames;
    unsigned seed;
} workload_config_t;

#endif
