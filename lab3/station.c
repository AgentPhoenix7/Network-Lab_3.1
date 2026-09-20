#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include "station.h"
#include "backoff.h"
#include "../utils/utils.h"
#include "../lab1/frame.h"
#include "../lab1/scheme.h"

// CLI parsing: returns 0 on success, -1 on error
// Fills strategy, seed, workload_cfg, port, host on success
static int parse_cli(int argc, char *argv[],
                     csma_strategy_t *strategy, double *p,
                     unsigned *seed, workload_config_t *workload_cfg,
                     uint16_t *port, const char **host) {
    *strategy = CSMA_ONEPERSISTENT;
    *p = 0.5;
    *seed = 1;
    *port = CSMA_PORT_DEFAULT;
    *host = "localhost";
    workload_cfg->type = WORKLOAD_SATURATED;
    workload_cfg->lambda = 0.0;
    workload_cfg->max_frames = 0;
    workload_cfg->seed = *seed;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--strategy") == 0) {
            if (++i >= argc) {
                fprintf(stderr, "error: --strategy requires an argument\n");
                return -1;
            }
            if (strcmp(argv[i], "nonpersistent") == 0)
                *strategy = CSMA_NONPERSISTENT;
            else if (strcmp(argv[i], "1persistent") == 0)
                *strategy = CSMA_ONEPERSISTENT;
            else if (strcmp(argv[i], "ppersistent") == 0)
                *strategy = CSMA_PPERSISTENT;
            else if (strcmp(argv[i], "csmacd") == 0)
                *strategy = CSMA_CD;
            else {
                fprintf(stderr, "error: unknown strategy '%s'\n", argv[i]);
                return -1;
            }
        } else if (strcmp(argv[i], "--seed") == 0) {
            if (++i >= argc) {
                fprintf(stderr, "error: --seed requires an argument\n");
                return -1;
            }
            *seed = (unsigned)atoi(argv[i]);
            workload_cfg->seed = *seed;
        } else if (strcmp(argv[i], "--p") == 0) {
            if (++i >= argc) {
                fprintf(stderr, "error: --p requires an argument\n");
                return -1;
            }
            *p = atof(argv[i]);
            if (*p < 0.0 || *p > 1.0) {
                fprintf(stderr, "error: p must be in [0.0, 1.0]\n");
                return -1;
            }
        } else if (strcmp(argv[i], "--workload") == 0) {
            if (++i >= argc) {
                fprintf(stderr, "error: --workload requires an argument\n");
                return -1;
            }
            if (strcmp(argv[i], "saturated") == 0) {
                workload_cfg->type = WORKLOAD_SATURATED;
            } else if (strcmp(argv[i], "poisson") == 0) {
                workload_cfg->type = WORKLOAD_POISSON;
            } else {
                fprintf(stderr, "error: unknown workload '%s'\n", argv[i]);
                return -1;
            }
        } else if (strcmp(argv[i], "--lambda") == 0) {
            if (++i >= argc) {
                fprintf(stderr, "error: --lambda requires an argument\n");
                return -1;
            }
            workload_cfg->lambda = atof(argv[i]);
            if (workload_cfg->lambda <= 0.0) {
                fprintf(stderr, "error: lambda must be > 0\n");
                return -1;
            }
        } else if (strcmp(argv[i], "--max-frames") == 0) {
            if (++i >= argc) {
                fprintf(stderr, "error: --max-frames requires an argument\n");
                return -1;
            }
            workload_cfg->max_frames = (uint32_t)atoi(argv[i]);
        } else if (strcmp(argv[i], "--port") == 0) {
            if (++i >= argc) {
                fprintf(stderr, "error: --port requires an argument\n");
                return -1;
            }
            *port = (uint16_t)atoi(argv[i]);
        } else if (strcmp(argv[i], "--host") == 0) {
            if (++i >= argc) {
                fprintf(stderr, "error: --host requires an argument\n");
                return -1;
            }
            *host = argv[i];
        } else {
            fprintf(stderr, "error: unknown flag '%s'\n", argv[i]);
            return -1;
        }
    }

    return 0;
}

// Generate frame: stamp csma_sim_t, compute FCS
static void generate_frame(frame_t *frame, uint32_t slot, uint32_t *uid, uint8_t sid) {
    memset(frame, 0, sizeof(*frame));
    frame->header.frame_type = 0;
    memset(frame->header.dest_addr, 0xFF, 6);
    frame->header.src_addr[0] = sid;
    memset(&frame->header.src_addr[1], 0, 5);
    frame->header.frame_len = htons(44 - (uint16_t)sizeof(csma_sim_t));
    frame->header.frame_seq = htons(0);
    csma_sim_t *meta = (csma_sim_t *)frame->payload;
    meta->enqueue_slot = htonl(slot);
    meta->frame_uid = htonl(*uid);
    (*uid)++;
    uint32_t fcs = compute_fcs(SCHEME_CHECKSUM16, (uint8_t *)frame, FRAME_SIZE - TRAILER_SIZE);
    frame->trailer.fcs = htonl(fcs);
}

int main(int argc, char *argv[]) {
    csma_strategy_t strategy;
    double p;
    unsigned seed;
    workload_config_t workload_cfg;
    uint16_t port;
    const char *host;

    if (parse_cli(argc, argv, &strategy, &p, &seed, &workload_cfg, &port, &host) < 0) {
        fprintf(stderr, "usage: %s [--strategy nonpersistent|1persistent|ppersistent|csmacd]\n", argv[0]);
        fprintf(stderr, "[--seed UINT] [--p FLOAT] [--workload saturated|poisson]\n");
        fprintf(stderr, "[--lambda FLOAT] [--max-frames UINT] [--port UINT16]\n");
        fprintf(stderr, "[--host HOST]\n");
        return 1;
    }

    srand(seed);

    // Connect to channel
    int conn = tcp_connect(host, port);
    if (conn < 0) {
        perror("tcp_connect");
        return 1;
    }

    // Recv config_msg_t
    config_msg_t cfg;
    if (recv_all(conn, &cfg, sizeof(cfg)) != (int)sizeof(cfg)) {
        perror("recv config");
        return 1;
    }
    cfg.slot_frame_len = ntohl(cfg.slot_frame_len);
    cfg.max_slots = ntohl(cfg.max_slots);

    // Initialize station state
    station_state_t st = {0};
    st.station_id = cfg.station_id;
    st.csma = csma_init();
    st.frame_uid = 1;
    st.has_frame = 0;

    // Main tick loop
    while (1) {
        tick_msg_t tick;
        if (recv_all(conn, &tick, sizeof(tick)) != (int)sizeof(tick)) break;
        tick.slot = ntohl(tick.slot);

        // Check should_stop
        if (tick.should_stop) break;

        // always have a frame ready
        if (workload_cfg.type == WORKLOAD_SATURATED && !st.has_frame) {
            generate_frame(&st.frame, tick.slot, &st.frame_uid, st.station_id);
            st.enqueue_slot = tick.slot;
            st.has_frame = 1;
        }

        // Call CSMA step
        action_type_t action = csma_step(&st.csma, (medium_state_t)tick.medium_state, cfg.slot_frame_len, strategy, p, (result_t)tick.last_result);

        // Send action_msg_t
        action_msg_t act = {0};
        act.slot = htonl(tick.slot);
        act.action = action;

        if (action == ACT_START && st.has_frame) {
            act.frame = st.frame;
            st.has_frame = 0;
        }

        if (send_all(conn, &act, sizeof(act)) != (int)sizeof(act)) {
            perror("send action");
            break;
        }

        // Exit on ACT_DONE (Poisson workload exhausted)
        if (action == ACT_DONE) break;
    }

    close_conn(conn);
    return 0;
}
