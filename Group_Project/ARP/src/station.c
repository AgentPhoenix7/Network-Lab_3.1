/* Station process: one NIC per LAN port, the net layer, a PING scheduler, and the telemetry loop (plan 5.6). */
#define _POSIX_C_SOURCE 200809L
#include <arpa/inet.h>
#include <errno.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include "addr.h"
#include "cli.h"
#include "conf.h"
#include "ether.h"
#include "net.h"
#include "nic.h"
#include "simclock.h"
#include "station_ev.h"
#include "station_net.h"
#include "telemetry.h"
#include "utils.h"

#define POLL_CAP_MS 50          /* poll at least this often, so commands and PING stay responsive */
#define PING_INTERVAL_MS 1000   /* one echo request per sim-second */

/* Static module state: one station per process (allowed in the I/O shell). */
static net_t net;
static txq_t txq;
static conf_t cf;
static int nic_fd[MAX_IFACES];
static uint16_t nic_seq[MAX_IFACES];
static int quit;
static int ping_left;
static uint32_t ping_dst;
static uint64_t ping_next;
static uint16_t ping_seq;

/* Every net event goes out as a contract line. ARP events arrive with ev->arp set. */
static void on_ip(void *u, const net_ev_t *ev) {
    (void)u;
    if (ev->arp) ev_arp(cf.node, ev->arp);
    else ev_ip(cf.node, ev);
}

static void drop_nic(int i) {
    close_conn(nic_fd[i]);
    nic_fd[i] = -1;
    tel_log("warn", "NIC connection lost");
}

/* Checks one received frame: FCS, then destination, then dispatch to ARP or IPv4 (plan 5.6). */
static void on_rx(int i) {
    frame_t f;
    if (nic_recv(nic_fd[i], &f) != 0) { drop_nic(i); return; }
    if (eth_check(&f) != 0) { ev_rx(cf.node, i, &f, "fcs_fail"); return; }
    const uint8_t *dst = f.header.dest_addr;
    if (memcmp(dst, net.arp.ifs[i].mac, 6) != 0 && memcmp(dst, MAC_BCAST, 6) != 0) {
        ev_rx(cf.node, i, &f, "ignored");
        return;
    }
    ev_rx(cf.node, i, &f, "accepted");
    uint16_t len = ntohs(f.header.frame_len);
    uint64_t now = sim_now_ms();
    if (f.header.frame_type == FT_ARP) arp_input(&net.arp, (uint8_t)i, f.payload, len, now, &txq);
    else if (f.header.frame_type == FT_IPV4) net_input_ipv4(&net, (uint8_t)i, f.payload, len, now, &txq);
}

/* Turns each queued packet into a frame with its NIC's next seq, emits tx, then sends it. */
static void flush_tx(void) {
    for (int k = 0; k < txq.n; k++) {
        tx_t *t = &txq.v[k];
        frame_t f;
        eth_build(&f, t->type, net.arp.ifs[t->iface].mac, t->dst, nic_seq[t->iface]++, t->payload, t->len);
        ev_tx(cf.node, t->iface, &f);
        if (nic_fd[t->iface] >= 0 && nic_send(nic_fd[t->iface], &f) != 0) drop_nic(t->iface);
    }
    txq.n = 0;
}

/* One echo request per PING_INTERVAL_MS until the count runs out. */
static void ping_tick(uint64_t now) {
    if (ping_left <= 0 || now < ping_next) return;
    net_send_ping(&net, ping_dst, ping_seq++, now, &txq);   /* a failed send is already an ip drop event */
    ping_left--;
    ping_next = now + PING_INTERVAL_MS;
}

/* Text commands from the bridge (contract 2.2). Unknown or badly formed lines get a warning. */
static void on_cmd(const char *line, void *arg) {
    (void)arg;
    char verb[16] = "", ip[32] = "";
    unsigned count = 0;
    double x = 0;
    sscanf(line, "%15s", verb);
    if (strcmp(verb, "PING") == 0 && sscanf(line, "%*s %31s %u", ip, &count) == 2 && ip_parse(ip, &ping_dst) == 0) {
        ping_left = (int)count;
        ping_next = sim_now_ms();
    } else if (strcmp(verb, "FLUSH") == 0) {
        arp_flush(&net.arp);
    } else if (strcmp(verb, "DUMP") == 0) {
        ev_dump_cache(cf.node, &net.arp.cache, net.arp.ttl_ms);
    } else if (strcmp(verb, "SPEED") == 0 && sscanf(line, "%*s %lf", &x) == 1) {
        simclock_set_speed(x);
    } else if (strcmp(verb, "QUIT") == 0) {
        quit = 1;
    } else {
        tel_log("warn", "bad or unknown command");
    }
}

/* Next wake-up: the earliest ARP deadline or PING slot, capped at POLL_CAP_MS. */
static int wait_ms(uint64_t now) {
    uint64_t due = arp_next_deadline(&net.arp);
    if (ping_left > 0 && ping_next < due) due = ping_next;
    if (due <= now) return 0;
    return due - now < POLL_CAP_MS ? (int)(due - now) : POLL_CAP_MS;
}

/* One pass: poll the NICs and the telemetry input, then tick the timers, PING, and drain the txq. */
static void loop(void) {
    while (!quit) {
        struct pollfd pf[MAX_IFACES + 1];
        int map[MAX_IFACES], nfd = 0;
        for (int i = 0; i < cf.nifaces; i++) {
            if (nic_fd[i] < 0) continue;
            pf[nfd] = (struct pollfd){ nic_fd[i], POLLIN, 0 };
            map[nfd++] = i;
        }
        pf[nfd] = (struct pollfd){ tel_in_fd(), POLLIN, 0 };   /* a negative fd is ignored by poll */
        if (poll(pf, (nfds_t)(nfd + 1), wait_ms(sim_now_ms())) < 0 && errno != EINTR) return;

        for (int k = 0; k < nfd; k++) if (pf[k].revents) on_rx(map[k]);
        if (pf[nfd].revents) tel_read(on_cmd, NULL);
        uint64_t now = sim_now_ms();
        net_tick(&net, now, &txq);
        ping_tick(now);
        flush_tx();
    }
}

static void close_nics(void) {
    for (int i = 0; i < cf.nifaces; i++) if (nic_fd[i] >= 0) close_conn(nic_fd[i]);
}

int main(int argc, char **argv) {
    const char *path = "";
    unsigned int tel_port = 9700;
    cli_spec_t specs[] = {
        { "conf",     CLI_STRING, &path,     0,    "",   "station conf file (plan 5.6)" },
        { "tel-port", CLI_UINT,   &tel_port, 9700, NULL, "bridge telemetry port, 0 = stdio" },
    };
    int rc = cli_parse(argc, argv, specs, 2);
    if (rc == 1) return 0;               /* --help */
    if (rc < 0) return 2;
    if (!path[0] || tel_port > 65535) {
        fprintf(stderr, "station: need --conf PATH and --tel-port 0..65535\n");
        return 2;
    }
    signal(SIGPIPE, SIG_IGN);            /* a dead channel gives EPIPE, not a killed station */

    char err[128] = "";
    if (station_load(path, &cf, err, sizeof err) != 0) {
        fprintf(stderr, "station: %s: %s\n", path, err);
        return 2;
    }
    if (station_net_setup(&cf, &net, err, sizeof err) != 0) {
        fprintf(stderr, "station: %s: %s\n", path, err);
        return 2;
    }
    if (tel_open((uint16_t)tel_port) != 0) {
        fprintf(stderr, "station: cannot reach telemetry on %u\n", tel_port);
        return 1;
    }
    net.on_ip = on_ip;
    for (int i = 0; i < MAX_IFACES; i++) nic_fd[i] = -1;
    ev_hello(cf.node);
    for (int i = 0; i < cf.nifaces; i++) {
        nic_fd[i] = nic_join((uint16_t)cf.ifaces[i].port, cf.node, (uint8_t)i, cf.ifaces[i].mac);
        if (nic_fd[i] < 0) {
            fprintf(stderr, "station: cannot join channel on port %d\n", cf.ifaces[i].port);
            close_nics();
            tel_close();
            return 1;
        }
    }
    ev_ready(cf.node);
    loop();
    close_nics();
    tel_close();
    return 0;
}
