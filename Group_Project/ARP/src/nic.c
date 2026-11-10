/* NIC: the station's client side of one channel connection (contract 1). */
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <sys/types.h>
#include "nic.h"
#include "utils.h"

#define CONNECT_TRIES 50          /* 50 x 100 ms: the channel may still be starting */
#define ACK_OK 0x01

int nic_join(uint16_t port, const char *node, uint8_t iface, const uint8_t mac[6]) {
    int fd = -1;
    for (int t = 0; t < CONNECT_TRIES && fd < 0; t++) {
        fd = tcp_connect("127.0.0.1", port);
        if (fd < 0) {
            struct timespec pause = { 0, 100000000L };   /* 100 ms */
            nanosleep(&pause, NULL);
        }
    }
    if (fd < 0) return -1;

    nic_hello_t h;
    uint8_t ack = 0;
    memset(&h, 0, sizeof h);
    snprintf(h.node, sizeof h.node, "%s", node);
    h.iface = iface;
    memcpy(h.mac, mac, 6);
    if (send_all(fd, &h, sizeof h) != (ssize_t)sizeof h || recv_all(fd, &ack, 1) != 1 || ack != ACK_OK) {
        close_conn(fd);
        return -1;
    }
    return fd;
}

int nic_send(int fd, const frame_t *f) {
    return send_all(fd, f, sizeof *f) == (ssize_t)sizeof *f ? 0 : -1;
}

int nic_recv(int fd, frame_t *f) {
    return recv_all(fd, f, sizeof *f) == (ssize_t)sizeof *f ? 0 : -1;
}
