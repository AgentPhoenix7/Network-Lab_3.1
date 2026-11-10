/* M5 tests: network layer (plan.md 5.4, rows N4-N7), plus N8 for the drop paths. */
#define _POSIX_C_SOURCE 200809L
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "test_util.h"
#include "addr.h"
#include "net.h"

static const uint8_t BCAST[6]  = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
static const uint8_t MAC_R0[6] = {0x02, 0x00, 0x00, 0x00, 0x00, 0x01};
static const uint8_t MAC_R1[6] = {0x02, 0x00, 0x00, 0x00, 0x01, 0x01};
static const uint8_t MAC_H1[6] = {0x02, 0x00, 0x00, 0x00, 0x00, 0x10};
static const uint8_t MAC_H2[6] = {0x02, 0x00, 0x00, 0x00, 0x00, 0x11};
static const uint8_t MAC_H3[6] = {0x02, 0x00, 0x00, 0x00, 0x01, 0x21};
static const uint8_t MAC_X[6]  = {0x02, 0x00, 0x00, 0x00, 0x09, 0x05};   /* a cached neighbour */
static const uint8_t MAC_G[6]  = {0x02, 0x00, 0x00, 0x00, 0x09, 0x01};   /* a gateway */

#define LAN_A 0
#define LAN_B 1
#define EV_MAX 64
#define PING_ID 0x1234

/* A station: its net layer plus everything the test wants to see from it. */
typedef struct {
    net_t n;
    txq_t txq;
    int lan[MAX_IFACES];                 /* LAN id per interface */
    net_ev_t ev[EV_MAX]; int nev;        /* ip events, copied */
    int arp_ops[AEV_QUEUE_FULL + 1];     /* ARP events, counted by op */
} host_t;

typedef struct { host_t *h[4]; int nh; int lan_a_requests; } wire_t;

static uint32_t ip_of(const char *s) {
    uint32_t ip = 0;
    ip_parse(s, &ip);
    return ip;
}

static void rec(void *u, const net_ev_t *ev) {
    host_t *h = u;
    if (ev->arp) { h->arp_ops[ev->arp->op]++; return; }
    if (h->nev < EV_MAX) h->ev[h->nev++] = *ev;
}

/* Zeroes the host and wires its net layer. Interfaces and routes come from the helpers below. */
static void host_init(host_t *h, int is_router) {
    memset(h, 0, sizeof *h);
    net_init(&h->n);
    h->n.is_router = is_router;
    h->n.on_ip = rec;
    h->n.u = h;
}

static void add_if(host_t *h, int i, int lan, const char *ip, uint8_t prefix, const uint8_t mac[6]) {
    arp_iface_t *f = &h->n.arp.ifs[i];
    f->ip = ip_of(ip);
    f->prefix = prefix;
    memcpy(f->mac, mac, 6);
    h->lan[i] = lan;
    if (h->n.arp.nifs <= i) h->n.arp.nifs = i + 1;
}

/* cidr is "a.b.c.d/n"; via NULL means on-link. */
static void add_route(host_t *h, const char *cidr, int iface, const char *via) {
    route_t *r = &h->n.rt.r[h->n.rt.n++];
    cidr_parse(cidr, &r->net, &r->prefix);
    r->iface = (uint8_t)iface;
    r->via = via ? ip_of(via) : 0;
}

/* Pre-seeds a RESOLVED neighbour, so the test knows which MAC the frame must carry. */
static void seed(host_t *h, uint8_t iface, const char *ip, const uint8_t mac[6]) {
    arp_entry_t *e = arp_cache_alloc(&h->n.arp.cache, iface, ip_of(ip));
    e->state = ARP_RESOLVED;
    memcpy(e->mac, mac, 6);
    e->expires_at = 1000000;
}

/* Router R at 10.0.0.1 (LAN A) and 10.1.0.1 (LAN B), with both connected routes. */
static void router_setup(host_t *r) {
    host_init(r, 1);
    add_if(r, 0, LAN_A, "10.0.0.1", 24, MAC_R0);
    add_if(r, 1, LAN_B, "10.1.0.1", 24, MAC_R1);
    add_route(r, "10.0.0.0/24", 0, NULL);
    add_route(r, "10.1.0.0/24", 1, NULL);
}

static const net_ev_t *find(const host_t *h, net_op_t op, net_reason_t r) {
    for (int i = 0; i < h->nev; i++)
        if (h->ev[i].op == op && h->ev[i].reason == r) return &h->ev[i];
    return NULL;
}

static int n_ev(const host_t *h, net_op_t op, net_reason_t r) {
    int n = 0;
    for (int i = 0; i < h->nev; i++)
        if (h->ev[i].op == op && h->ev[i].reason == r) n++;
    return n;
}

/* 1 if frame i of q is an IPv4 frame on iface, to dst, of one full packet. */
static int ip_frame(const txq_t *q, int i, uint8_t iface, const uint8_t dst[6]) {
    const tx_t *t = &q->v[i];
    return t->type == FT_IPV4 && t->iface == iface &&
           memcmp(t->dst, dst, 6) == 0 && t->len == IPV4_PKT_LEN;
}

static int is_arp_req(const tx_t *t) {
    arp_pkt_t p;
    return t->type == FT_ARP && arp_pkt_parse(t->payload, t->len, &p) == 0 && p.oper == ARP_REQUEST;
}

/* Hands one frame to every NIC on its LAN that accepts it: dst MAC is ours or broadcast. */
static void deliver(wire_t *w, host_t *src, const tx_t *t, uint64_t now) {
    for (int d = 0; d < w->nh; d++) {
        host_t *r = w->h[d];
        if (r == src) continue;
        for (int j = 0; j < r->n.arp.nifs; j++) {
            if (r->lan[j] != src->lan[t->iface]) continue;
            if (memcmp(t->dst, BCAST, 6) != 0 && memcmp(t->dst, r->n.arp.ifs[j].mac, 6) != 0) continue;
            if (t->type == FT_ARP) arp_input(&r->n.arp, j, t->payload, t->len, now, &r->txq);
            else net_input_ipv4(&r->n, j, t->payload, t->len, now, &r->txq);
        }
    }
}

/* Runs the wire until no station has a frame left to send. */
static void pump(wire_t *w, uint64_t now) {
    for (int pass = 0; pass < 16; pass++) {
        int busy = 0;
        for (int s = 0; s < w->nh; s++) {
            txq_t snap = w->h[s]->txq;      /* deliveries append to the receivers' queues */
            w->h[s]->txq.n = 0;
            for (int i = 0; i < snap.n; i++) {
                busy = 1;
                if (w->h[s]->lan[snap.v[i].iface] == LAN_A && is_arp_req(&snap.v[i])) w->lan_a_requests++;
                deliver(w, w->h[s], &snap.v[i], now);
            }
        }
        if (!busy) return;
    }
    assert_true(0, "wire reached quiescence within 16 passes");
}

/* N4: a router forwards a packet that is not for it. TTL drops, the checksum still verifies. */
static void test_n4(void) {
    TEST_BEGIN("N4");
    host_t r;
    router_setup(&r);
    seed(&r, 1, "10.1.0.5", MAC_X);

    uint8_t pkt[IPV4_PKT_LEN];
    ip_build_echo(pkt, ip_of("10.0.0.10"), ip_of("10.1.0.5"), PING_ID, 7, 0);
    net_input_ipv4(&r.n, 0, pkt, sizeof pkt, 0, &r.txq);

    assert_eq_i64(r.txq.n, 1, "N4 router sends exactly one frame");
    assert_true(r.txq.n == 1 && ip_frame(&r.txq, 0, 1, MAC_X),
                "N4 frame leaves iface 1, to the cached MAC of 10.1.0.5");
    ipv4_info_t in;
    assert_true(r.txq.n == 1 && ipv4_parse(r.txq.v[0].payload, r.txq.v[0].len, &in) == 0,
                "N4 forwarded packet still verifies (both checksums)");
    assert_eq_i64(in.ttl, 63, "N4 TTL 64 -> 63");
    assert_true(in.dst == ip_of("10.1.0.5") && in.src == ip_of("10.0.0.10"), "N4 addresses unchanged");
    assert_eq_i64(n_ev(&r, NET_OP_FORWARD, NET_R_NONE), 1, "N4 one ip forward event");
    assert_eq_i64(n_ev(&r, NET_OP_DROP, NET_R_NONE) + n_ev(&r, NET_OP_DROP, NET_R_TTL), 0,
                  "N4 no drop events");

    /* The same packet at a host that is not its destination: silent drop. */
    host_t h;
    host_init(&h, 0);
    add_if(&h, 0, LAN_A, "10.0.0.10", 24, MAC_H1);
    add_route(&h, "10.0.0.0/24", 0, NULL);
    add_route(&h, "0.0.0.0/0", 0, "10.0.0.1");
    net_input_ipv4(&h.n, 0, pkt, sizeof pkt, 0, &h.txq);
    assert_eq_i64(h.txq.n, 0, "N4 host sends nothing for a non-local packet");
    assert_eq_i64(h.nev, 0, "N4 host emits no event for a non-local packet");
}

/* N5: a router drops a packet whose TTL is 1 instead of forwarding it. */
static void test_n5(void) {
    TEST_BEGIN("N5");
    host_t r;
    router_setup(&r);
    seed(&r, 1, "10.1.0.5", MAC_X);

    uint8_t pkt[IPV4_PKT_LEN];
    ip_build_echo(pkt, ip_of("10.0.0.10"), ip_of("10.1.0.5"), PING_ID, 8, 0);
    for (int i = 0; i < 63; i++) ip_dec_ttl(pkt);          /* TTL 64 -> 1, checksum kept valid */
    ipv4_info_t in;
    assert_true(ipv4_parse(pkt, sizeof pkt, &in) == 0 && in.ttl == 1, "N5 precondition: valid packet, TTL 1");

    net_input_ipv4(&r.n, 0, pkt, sizeof pkt, 0, &r.txq);
    assert_eq_i64(r.txq.n, 0, "N5 nothing is sent");
    assert_eq_i64(n_ev(&r, NET_OP_DROP, NET_R_TTL), 1, "N5 one ip drop ttl");
    const net_ev_t *d = find(&r, NET_OP_DROP, NET_R_TTL);
    assert_true(d != NULL && d->dst == ip_of("10.1.0.5") && d->icmp_seq == 8,
                "N5 drop event names the packet's dst and seq");
}

/* N6: an echo request for us is delivered, and the echo reply goes to the next hop for its source. */
static void test_n6(void) {
    TEST_BEGIN("N6");
    host_t h;
    host_init(&h, 0);
    add_if(&h, 0, LAN_A, "10.0.0.10", 24, MAC_H1);
    add_route(&h, "10.0.0.0/24", 0, NULL);
    add_route(&h, "0.0.0.0/0", 0, "10.0.0.1");
    seed(&h, 0, "10.0.0.1", MAC_G);

    uint8_t req[IPV4_PKT_LEN];
    ip_build_echo(req, ip_of("10.1.0.5"), ip_of("10.0.0.10"), PING_ID, 9, 0);
    net_input_ipv4(&h.n, 0, req, sizeof req, 0, &h.txq);

    const net_ev_t *d = find(&h, NET_OP_DELIVER, NET_R_NONE);
    assert_true(d != NULL && d->icmp == ICMP_ECHO_REQUEST && d->src == ip_of("10.1.0.5") &&
                d->dst == ip_of("10.0.0.10") && d->icmp_seq == 9, "N6 echo request delivered");

    assert_eq_i64(h.txq.n, 1, "N6 exactly one reply frame");
    assert_true(h.txq.n == 1 && ip_frame(&h.txq, 0, 0, MAC_G),
                "N6 reply goes to the gateway's MAC (next hop for 10.1.0.5)");
    ipv4_info_t rep;
    assert_true(h.txq.n == 1 && ipv4_parse(h.txq.v[0].payload, h.txq.v[0].len, &rep) == 0,
                "N6 reply verifies");
    assert_eq_i64(rep.type, ICMP_ECHO_REPLY, "N6 reply is an echo reply");
    assert_true(rep.src == ip_of("10.0.0.10") && rep.dst == ip_of("10.1.0.5") && rep.seq == 9,
                "N6 reply swaps the addresses and keeps the seq");
    assert_eq_i64(n_ev(&h, NET_OP_SEND, NET_R_NONE), 1, "N6 reply is reported as ip send");
}

/* N7: in-process proxy scenario, plan 2.2 steps 1-6 (h1 pings h3 across r1). */
static void test_n7(void) {
    TEST_BEGIN("N7");
    host_t h1, h2, r1, h3;
    host_init(&h1, 0);
    host_init(&h2, 0);
    host_init(&r1, 1);
    host_init(&h3, 0);

    add_if(&h1, 0, LAN_A, "141.23.56.10", 24, MAC_H1);
    add_route(&h1, "141.23.56.0/24", 0, NULL);
    add_if(&h2, 0, LAN_A, "141.23.56.11", 24, MAC_H2);
    add_route(&h2, "141.23.56.0/24", 0, NULL);

    add_if(&r1, 0, LAN_A, "141.23.56.1", 24, MAC_R0);
    add_if(&r1, 1, LAN_B, "141.23.56.17", 29, MAC_R1);
    add_route(&r1, "141.23.56.0/24", 0, NULL);
    add_route(&r1, "141.23.56.16/29", 1, NULL);
    r1.n.arp.ifs[0].nproxy = 3;
    r1.n.arp.ifs[0].proxy_net[0] = ip_of("141.23.56.21");
    r1.n.arp.ifs[0].proxy_net[1] = ip_of("141.23.56.22");
    r1.n.arp.ifs[0].proxy_net[2] = ip_of("141.23.56.23");
    for (int i = 0; i < 3; i++) r1.n.arp.ifs[0].proxy_prefix[i] = 32;

    add_if(&h3, 0, LAN_B, "141.23.56.21", 29, MAC_H3);
    add_route(&h3, "0.0.0.0/0", 0, "141.23.56.17");

    wire_t w = { .h = {&h1, &h2, &r1, &h3}, .nh = 4, .lan_a_requests = 0 };

    assert_eq_i64(net_send_ping(&h1.n, ip_of("141.23.56.21"), 1, 0, &h1.txq), 0, "N7 h1 pings .21");
    pump(&w, 0);

    assert_eq_i64(r1.arp_ops[AEV_PROXY_REPLY], 1, "N7 r1 proxy-replies once for .21");
    const arp_entry_t *e = arp_cache_find(&h1.n.arp.cache, 0, ip_of("141.23.56.21"));
    assert_true(e != NULL && e->state == ARP_RESOLVED && memcmp(e->mac, MAC_R0, 6) == 0,
                "N7 h1 maps .21 to r1.if0's MAC");

    const net_ev_t *d = find(&h1, NET_OP_DELIVER, NET_R_NONE);
    assert_true(d != NULL && d->icmp == ICMP_ECHO_REPLY && d->src == ip_of("141.23.56.21") &&
                d->dst == ip_of("141.23.56.10") && d->icmp_seq == 1, "N7 echo reply delivered at h1");
    assert_eq_i64(n_ev(&h3, NET_OP_DELIVER, NET_R_NONE), 1, "N7 h3 delivers the echo request");
    assert_eq_i64(n_ev(&r1, NET_OP_FORWARD, NET_R_NONE), 2, "N7 r1 forwards request and reply");

    assert_eq_i64(w.lan_a_requests, 1, "N7 only the first who-has .21 is broadcast on LAN A");
    assert_true(arp_cache_find(&h2.n.arp.cache, 0, ip_of("141.23.56.21")) == NULL,
                "N7 bystander h2 learns nothing about .21");
}

/* N8 (extra): no route, and an ARP that never resolves. */
static void test_n8(void) {
    TEST_BEGIN("N8");
    host_t h;
    host_init(&h, 0);
    add_if(&h, 0, LAN_A, "10.0.0.10", 24, MAC_H1);
    assert_eq_i64(net_send_ping(&h.n, ip_of("10.9.9.9"), 2, 0, &h.txq), -1, "N8 no route returns -1");
    assert_eq_i64(n_ev(&h, NET_OP_DROP, NET_R_NO_ROUTE), 1, "N8 one drop no_route");
    assert_eq_i64(h.txq.n, 0, "N8 no route sends nothing");

    host_t a;
    host_init(&a, 0);
    a.n.arp.max_attempts = 3;   /* the default is 0 = retry forever; this part needs a give-up */
    add_if(&a, 0, LAN_A, "10.0.0.10", 24, MAC_H1);
    add_route(&a, "10.0.0.0/24", 0, NULL);
    assert_eq_i64(net_send_ping(&a.n, ip_of("10.0.0.99"), 3, 0, &a.txq), 0, "N8 ping to silent host queued");
    net_tick(&a.n, 1000, &a.txq);
    net_tick(&a.n, 2000, &a.txq);
    assert_eq_i64(n_ev(&a, NET_OP_DROP, NET_R_ARP_FAIL), 0, "N8 no arp_fail before the last attempt");
    net_tick(&a.n, 3000, &a.txq);
    assert_eq_i64(a.arp_ops[AEV_FAIL], 1, "N8 ARP gave up once");
    assert_eq_i64(n_ev(&a, NET_OP_DROP, NET_R_ARP_FAIL), 1, "N8 queued packet dropped as arp_fail");
    const net_ev_t *d = find(&a, NET_OP_DROP, NET_R_ARP_FAIL);
    assert_true(d != NULL && d->src == ip_of("10.0.0.10") && d->dst == ip_of("10.0.0.99") &&
                d->icmp_seq == 3, "N8 arp_fail names the dropped packet");
}

int main(void) {
    test_n4();
    test_n5();
    test_n6();
    test_n7();
    test_n8();
    return test_summary();
}
