#define _POSIX_C_SOURCE 200809L
#include "station.h"
#include "utils.h"
#include "walsh.h"

#include <arpa/inet.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#define CONNECT_TRIES 50   /* 50 x 100 ms: the channel may still be starting */

int station_join(const char *host, unsigned int port, role_t role,
                 unsigned int id, config_msg_t *cfg) {
    if (port > 65535 || id > 255) {
        fprintf(stderr, "station: --port must be 0..65535 and --id 0..255\n");
        return -1;
    }

    /* A dead channel must give EPIPE, not kill us. */
    signal(SIGPIPE, SIG_IGN);

    int fd = -1;
    for (int t = 0; t < CONNECT_TRIES && fd < 0; t++) {
        fd = tcp_connect(host, (uint16_t)port);
        if (fd < 0) {
            struct timespec pause = { 0, 100000000L };   /* 100 ms */
            nanosleep(&pause, NULL);
        }
    }
    if (fd < 0) return -1;

    hello_msg_t h;
    memset(&h, 0, sizeof h);
    h.role = (uint8_t)role;
    h.id = (uint8_t)id;

    if (send_all(fd, &h, sizeof h) != (ssize_t)sizeof h ||
        recv_all(fd, cfg, sizeof *cfg) != (ssize_t)sizeof *cfg) {
        fprintf(stderr, "station: handshake with channel failed\n");
        close_conn(fd);
        return -1;
    }

    cfg->n = ntohl(cfg->n);
    cfg->L = ntohl(cfg->L);
    if (cfg->n < 1 || cfg->n > 64 || id >= cfg->n ||
        cfg->L != (uint32_t)walsh_length_for((int)cfg->n) ||
        cfg->code_index != id) {
        fprintf(stderr, "station: bad config from channel (n=%u L=%u id=%u)\n",
                cfg->n, cfg->L, id);
        close_conn(fd);
        return -1;
    }
    return fd;
}
