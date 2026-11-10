#define _POSIX_C_SOURCE 200809L
#include "telemetry.h"
#include "addr.h"
#include "utils.h"

#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#define TEL_LINE_MAX 256

/* Static module state: one telemetry connection per process (allowed in I/O shells). */
static int out_fd = -1, in_fd = -1;
static char pend[TEL_LINE_MAX];
static size_t plen;

int tel_open(uint16_t port) {
    if (port == 0) {                  /* stdio mode: stdout and stdin */
        out_fd = 1;
        in_fd = 0;
        return 0;
    }
    int fd = tcp_connect("127.0.0.1", port);
    if (fd < 0) return -1;
    out_fd = in_fd = fd;
    return 0;
}

int tel_emit(const char *line) {
    size_t n = strlen(line);
    if (out_fd < 0) return -1;
    if (send_all(out_fd, line, n) != (ssize_t)n || send_all(out_fd, "\n", 1) != 1) {
        fprintf(stderr, "telemetry: write failed, events stop\n");
        out_fd = -1;
        return -1;
    }
    return 0;
}

int tel_in_fd(void) {
    return in_fd;
}

int tel_read(tel_line_fn fn, void *arg) {
    if (in_fd < 0) return -1;
    ssize_t n = read(in_fd, pend + plen, sizeof pend - 1 - plen);
    if (n < 0 && errno == EINTR) return 0;
    if (n <= 0) {                     /* EOF or error: stop reading commands */
        in_fd = -1;
        return -1;
    }
    plen += (size_t)n;

    size_t start = 0;
    for (size_t i = 0; i < plen; i++) {
        if (pend[i] == '\n') {
            pend[i] = '\0';
            fn(pend + start, arg);
            start = i + 1;
        }
    }
    memmove(pend, pend + start, plen - start);
    plen -= start;
    if (plen == sizeof pend - 1) plen = 0;   /* overlong line with no newline: drop it */
    return 0;
}

void tel_close(void) {
    if (out_fd > 2) close_conn(out_fd);   /* never close stdio */
    out_fd = in_fd = -1;
    plen = 0;
}

int tel_channel_ready(int lan) {
    char b[96];
    snprintf(b, sizeof b, "{\"ev\":\"hello\",\"role\":\"channel\",\"lan\":%d}", lan);
    if (tel_emit(b) != 0) return -1;
    snprintf(b, sizeof b, "{\"ev\":\"ready\",\"lan\":%d}", lan);
    return tel_emit(b);
}

int tel_log(const char *level, const char *msg) {
    char b[160];
    snprintf(b, sizeof b, "{\"ev\":\"log\",\"level\":\"%s\",\"msg\":\"%s\"}", level, msg);
    return tel_emit(b);
}

int tel_switch_learn(int lan, const uint8_t mac[6], const char *node, int iface) {
    char m[18], b[256];
    mac_fmt(mac, m);
    snprintf(b, sizeof b, "{\"ev\":\"switch_learn\",\"lan\":%d,\"mac\":\"%s\",\"node\":\"%s\",\"iface\":%d}",
             lan, m, node, iface);
    return tel_emit(b);
}

int tel_medium(int lan, const char *from, int from_iface, unsigned seq,
               const tel_dest_t *to, int nto, const char *fate, uint64_t delay_ms, long bit) {
    char b[2048];
    int m = snprintf(b, sizeof b,
        "{\"ev\":\"medium\",\"lan\":%d,\"uid\":\"%s:%d:%u\",\"from\":\"%s\",\"from_iface\":%d,\"to\":[",
        lan, from, from_iface, seq, from, from_iface);
    for (int i = 0; i < nto; i++) {
        m += snprintf(b + m, sizeof b - (size_t)m, "%s{\"node\":\"%s\",\"iface\":%d}",
                      i ? "," : "", to[i].node, to[i].iface);
    }
    m += snprintf(b + m, sizeof b - (size_t)m, "],\"fate\":\"%s\",\"delay_ms\":%llu",
                  fate, (unsigned long long)delay_ms);
    if (bit >= 0) m += snprintf(b + m, sizeof b - (size_t)m, ",\"bit\":%ld", bit);
    snprintf(b + m, sizeof b - (size_t)m, "}");
    return tel_emit(b);
}
