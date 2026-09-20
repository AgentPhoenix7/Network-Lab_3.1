#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <inttypes.h>
#include "protocol.h"
#include "channel.h"
#include "station.h"

const char *metrics_file = NULL;

int main(int argc, char *argv[]) {
    channel_params_t params = {
        .port = CSMA_PORT_DEFAULT,
        .n_stations = 2,
        .slot_frame_len = 16,
        .detect_collisions = 0,
        .max_slots = 5000,
        .seed = 0,
        .sink_path = NULL
    };

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--port") == 0) {
            if (++i >= argc) {
                fprintf(stderr, "error: --port requires an argument\n");
                return 1;
            }
            params.port = (uint16_t)atoi(argv[i]);
        } else if (strcmp(argv[i], "--n-stations") == 0) {
            if (++i >= argc) {
                fprintf(stderr, "error: --n-stations requires an argument\n");
                return 1;
            }
            params.n_stations = atoi(argv[i]);
        } else if (strcmp(argv[i], "--T-frame") == 0) {
            if (++i >= argc) {
                fprintf(stderr, "error: --T-frame requires an argument\n");
                return 1;
            }
            params.slot_frame_len = atoi(argv[i]);
        } else if (strcmp(argv[i], "--detect-collisions") == 0) {
            if (++i >= argc) {
                fprintf(stderr, "error: --detect-collisions requires an argument\n");
                return 1;
            }
            params.detect_collisions = atoi(argv[i]);
        } else if (strcmp(argv[i], "--max-slots") == 0) {
            if (++i >= argc) {
                fprintf(stderr, "error: --max-slots requires an argument\n");
                return 1;
            }
            params.max_slots = atoi(argv[i]);
        } else if (strcmp(argv[i], "--metrics-file") == 0) {
            if (++i >= argc) {
                fprintf(stderr, "error: --metrics-file requires an argument\n");
                return 1;
            }
            metrics_file = argv[i];
        } else {
            fprintf(stderr, "error: unknown flag '%s'\n", argv[i]);
            return 1;
        }
    }

    channel_metrics_t metrics;
    int ret = channel_serve(&params, &metrics);

    if (ret == 0 && metrics_file) {
        FILE *f = fopen(metrics_file, "w");
        if (f) {
            fprintf(f, "%" PRIu64 " %" PRIu64 " %" PRIu64 " %" PRIu64 " %f %f\n",
                    metrics.total_slots,
                    metrics.frames_delivered,
                    metrics.collision_events,
                    metrics.collided_attempts,
                    metrics.throughput,
                    metrics.avg_delay_slots);
            fclose(f);
        }
    }

    return ret == 0 ? 0 : 1;
}
