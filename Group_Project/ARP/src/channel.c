#define _POSIX_C_SOURCE 200809L
#include "channel.h"
#include "impair.h"
#include "l2.h"
#include "simclock.h"
#include "telemetry.h"
#include "utils.h"

#include <arpa/inet.h>
#include <errno.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define POLL_CAP_MS 50

static double draw(void) {
    return rand() / ((double)RAND_MAX + 1.0);   /* [0,1), one per transmission */
}

static void drop_port(channel_t *ch, int p) {
    close_conn(ch->port[p].fd);
    ch->port[p].fd = -1;
    tel_log("info", "NIC disconnected");
}

/* Star: learn the sender, then unicast to the learned port (drop if it is the ingress port).
 * Unknown or broadcast: flood. Bus: everyone but the ingress. Fills d[], returns the count. */
static int pick_dests(channel_t *ch, int p, const frame_t *f, int *d) {
    if (ch->star) {
        if (switch_learn(&ch->sw, f->header.src_addr, p) == 1)
            tel_switch_learn(ch->lan, f->header.src_addr, ch->port[p].node, ch->port[p].iface);
        int to = switch_lookup(&ch->sw, f->header.dest_addr);
        if (to >= 0) {
            if (to == p || ch->port[to].fd < 0) return 0;
            d[0] = to;
            return 1;
        }
    }
    int nd = 0;
    for (int i = 0; i < ch->nports; i++) {
        if (i != p && ch->port[i].fd >= 0) d[nd++] = i;
    }
    return nd;
}

static void emit_medium(const channel_t *ch, int p, const frame_t *f, fate_t fate,
                        const int *d, int nd, long bit) {
    tel_dest_t to[CH_MAX_PORTS];
    const char *fs = fate == FATE_LOST ? "lost" : fate == FATE_CORRUPT ? "corrupted" : "ok";
    for (int i = 0; i < nd; i++) to[i] = (tel_dest_t){ ch->port[d[i]].node, ch->port[d[i]].iface };
    tel_medium(ch->lan, ch->port[p].node, ch->port[p].iface, ntohs(f->header.frame_seq),
               to, nd, fs, ch->delay_ms, bit);
}

static void on_frame(channel_t *ch, int p) {
    frame_t f;
    int d[CH_MAX_PORTS];
    if (recv_all(ch->port[p].fd, &f, sizeof f) != (ssize_t)sizeof f) {
        drop_port(ch, p);
        return;
    }
    int nd = pick_dests(ch, p, &f, d);
    fate_t fate = impair_decide(ch->loss, ch->corrupt, draw());
    if (fate == FATE_LOST) {
        emit_medium(ch, p, &f, fate, d, 0, -1);
        return;
    }
    long bit = fate == FATE_CORRUPT ? (long)impair_corrupt((uint8_t *)&f) : -1;
    emit_medium(ch, p, &f, fate, d, nd, bit);
    for (int i = 0; i < nd; i++) {
        /* one queue item per destination, so the DELAYQ_MAX_DESTS limit never applies */
        if (delayq_push(&ch->q, sim_now_ms() + ch->delay_ms, (const uint8_t *)&f, &d[i], 1) != 0) {
            tel_log("warn", "delay queue full, frame dropped");
            break;
        }
    }
}

static void accept_nic(channel_t *ch) {
    int fd = tcp_accept(ch->listen_fd);
    if (fd < 0) return;               /* a failed accept loses only that client */

    nic_hello_t h;
    uint8_t ok = 0x01;
    /* shortcut: the 16-byte hello is read blocking, so a client that stalls here stalls the loop */
    if (ch->nports == CH_MAX_PORTS || recv_all(fd, &h, sizeof h) != (ssize_t)sizeof h ||
        send_all(fd, &ok, 1) != 1) {
        close_conn(fd);
        return;
    }
    channel_port_t *pt = &ch->port[ch->nports++];
    pt->fd = fd;
    memcpy(pt->node, h.node, sizeof h.node);
    pt->node[8] = '\0';
    pt->iface = h.iface;
}

static void send_to(channel_t *ch, int p, const uint8_t *frame) {
    if (ch->port[p].fd < 0) return;
    if (send_all(ch->port[p].fd, frame, FRAME_SIZE) != (ssize_t)FRAME_SIZE) drop_port(ch, p);
}

static void cmd_impair(channel_t *ch, const char *line) {
    double loss, corrupt;
    int delay;
    if (sscanf(line, "%*s %lf %lf %d", &loss, &corrupt, &delay) != 3 ||
        loss < 0 || loss > 1 || corrupt < 0 || corrupt > 1 || delay < 0) {
        tel_log("warn", "bad IMPAIR, want: IMPAIR loss corrupt delay_ms");
        return;
    }
    ch->loss = loss;
    ch->corrupt = corrupt;
    ch->delay_ms = (uint64_t)delay;
}

static void on_cmd(const char *line, void *arg) {
    channel_t *ch = arg;
    char verb[16] = "";
    double speed;
    sscanf(line, "%15s", verb);
    if (strcmp(verb, "SPEED") == 0 && sscanf(line, "%*s %lf", &speed) == 1) {
        simclock_set_speed(speed);
    } else if (strcmp(verb, "IMPAIR") == 0) {
        cmd_impair(ch, line);
    } else if (strcmp(verb, "DUMP") == 0) {
        for (int i = 0; i < ch->sw.n; i++) {
            int p = ch->sw.port[i];
            tel_switch_learn(ch->lan, ch->sw.mac[i], ch->port[p].node, ch->port[p].iface);
        }
    } else if (strcmp(verb, "QUIT") == 0) {
        ch->quit = 1;
    } else {
        tel_log("warn", "unknown command");
    }
}

/* Waits at most timeout_ms, capped at POLL_CAP_MS, and no longer than the next due frame. */
static int poll_timeout_ms(const channel_t *ch, int timeout_ms) {
    int t = timeout_ms < POLL_CAP_MS ? timeout_ms : POLL_CAP_MS;
    uint64_t next = delayq_next_due(&ch->q), now = sim_now_ms();
    uint64_t wait = next > now ? next - now : 0;
    if (next != UINT64_MAX && wait < (uint64_t)t) t = (int)wait;
    return t;
}

/* A negative fd makes poll() skip the entry, so closed slots need no special case. */
static void set_pfd(struct pollfd *p, int fd) {
    p->fd = fd;
    p->events = POLLIN;
    p->revents = 0;
}

int channel_init(channel_t *ch, const channel_cfg_t *c) {
    memset(ch, 0, sizeof *ch);   /* also empties the MAC table and the delay queue */
    ch->listen_fd = tcp_listen(c->port, 16);
    if (ch->listen_fd < 0) return -1;
    if (tel_open(c->tel_port) != 0) {
        close_conn(ch->listen_fd);
        return -1;
    }
    ch->lan = c->lan;
    ch->star = c->star;
    srand(c->seed);
    tel_channel_ready(ch->lan);
    return 0;
}

void channel_close(channel_t *ch) {
    for (int i = 0; i < ch->nports; i++) close_conn(ch->port[i].fd);
    close_conn(ch->listen_fd);
    tel_close();
}

int channel_step(channel_t *ch, int timeout_ms) {
    struct pollfd pf[2 + CH_MAX_PORTS];
    int n = 2 + ch->nports;
    set_pfd(&pf[0], ch->listen_fd);
    set_pfd(&pf[1], tel_in_fd());
    for (int i = 0; i < ch->nports; i++) set_pfd(&pf[2 + i], ch->port[i].fd);

    if (poll(pf, (nfds_t)n, poll_timeout_ms(ch, timeout_ms)) < 0) {
        return errno == EINTR ? 0 : -1;
    }
    if (pf[0].revents) accept_nic(ch);
    if (pf[1].revents) tel_read(on_cmd, ch);
    for (int i = 2; i < n; i++) if (pf[i].revents) on_frame(ch, i - 2);
    delayq_item_t it;
    uint64_t now = sim_now_ms();
    while (delayq_pop_due(&ch->q, now, &it)) {
        for (int k = 0; k < it.ndest; k++) send_to(ch, it.dests[k], it.frame);
    }
    return ch->quit;
}

int channel_run(channel_t *ch) {
    int r;
    while ((r = channel_step(ch, POLL_CAP_MS)) == 0) { }
    return r < 0 ? -1 : 0;
}
