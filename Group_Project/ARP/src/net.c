/* Network layer: route, ping, deliver / forward / drop (plan 5.4). Pure: time comes in as `now`. */
#include <string.h>
#include "net.h"

#define NET_PING_ID 1   /* ICMP identifier for our own pings */

static void net_emit(net_t *n, net_op_t op, net_reason_t r, const ipv4_info_t *i) {
    net_ev_t e = { .op = op, .reason = r, .src = i->src, .dst = i->dst, .icmp = i->type, .icmp_seq = i->seq };
    if (n->on_ip) n->on_ip(n->u, &e);
}

/* ARP's proxy rule asks which iface reaches ip. -1 = no route. */
static int net_egress(void *u, uint32_t ip) {
    const net_t *n = u;
    uint8_t iface; uint32_t nh;
    return route_lookup(&n->rt, ip, &iface, &nh) == 0 ? iface : -1;
}

/* An AEV_FAIL drops every packet parked on the entry. The entry is still in the cache during this callback. */
static void net_fail_drops(net_t *n, const arp_ev_t *ev) {
    arp_entry_t *e = arp_cache_find(&n->arp.cache, ev->iface, ev->ip);
    for (int i = 0; e && i < e->qn; i++) {
        ipv4_info_t in;
        if (ipv4_parse(e->q[i].pkt, e->q[i].len, &in) == 0) net_emit(n, NET_OP_DROP, NET_R_ARP_FAIL, &in);
    }
}

/* Passes every ARP event up, then turns a failed resolution into ip drops. Queue-full is reported by
 * net_send_ip, because only the caller still holds the packet. */
static void net_on_arp(void *u, const arp_ev_t *ev) {
    net_t *n = u;
    net_ev_t e = { .arp = ev };
    if (n->on_ip) n->on_ip(n->u, &e);
    if (ev->op == AEV_FAIL) net_fail_drops(n, ev);
}

static int net_is_local(const net_t *n, uint32_t ip) {
    for (int i = 0; i < n->arp.nifs; i++)
        if (n->arp.ifs[i].ip == ip) return 1;
    return 0;
}

/* Routes pkt and hands it to ARP. 0 sent or parked; -1 no route; -2 queue full. Drops are emitted here. */
static int net_send_ip(net_t *n, const uint8_t *pkt, const ipv4_info_t *in, uint64_t now, txq_t *txq) {
    uint8_t iface; uint32_t nh;
    if (route_lookup(&n->rt, in->dst, &iface, &nh) != 0) { net_emit(n, NET_OP_DROP, NET_R_NO_ROUTE, in); return -1; }
    if (arp_output(&n->arp, iface, nh, pkt, IPV4_PKT_LEN, now, txq) < 0) {
        net_emit(n, NET_OP_DROP, NET_R_QUEUE_FULL, in);
        return -2;
    }
    return 0;
}

void net_init(net_t *n) {
    n->arp.ttl_ms = 30000; n->arp.retry_ms = 1000; n->arp.max_attempts = 0;
    n->arp.egress = net_egress;
    n->arp.on_event = net_on_arp;
    n->arp.u = n;
}

int net_send_ping(net_t *n, uint32_t dst, uint16_t seq, uint64_t now, txq_t *txq) {
    ipv4_info_t in = { .dst = dst, .seq = seq, .type = ICMP_ECHO_REQUEST };
    uint8_t pkt[IPV4_PKT_LEN], iface = 0; uint32_t nh;
    in.src = route_lookup(&n->rt, dst, &iface, &nh) == 0 ? n->arp.ifs[iface].ip : 0;   /* src = egress IP */
    ip_build_echo(pkt, in.src, dst, NET_PING_ID, seq, 0);
    int rc = net_send_ip(n, pkt, &in, now, txq);
    if (rc == 0) net_emit(n, NET_OP_SEND, NET_R_NONE, &in);
    return rc;
}

void net_input_ipv4(net_t *n, uint8_t iface, const uint8_t *pkt, size_t len, uint64_t now, txq_t *txq) {
    (void)iface;   /* routing alone picks the egress iface */
    ipv4_info_t in;
    uint8_t out[IPV4_PKT_LEN];
    if (ipv4_parse(pkt, len, &in) != 0) return;            /* bad length, checksum or type: dropped, no event */

    if (net_is_local(n, in.dst)) {
        net_emit(n, NET_OP_DELIVER, NET_R_NONE, &in);
        if (in.type == ICMP_ECHO_REQUEST) {               /* answer the ping from the address it hit */
            ipv4_info_t rep = { .src = in.dst, .dst = in.src, .seq = in.seq, .type = ICMP_ECHO_REPLY };
            ip_build_echo(out, in.dst, in.src, in.id, in.seq, 1);
            if (net_send_ip(n, out, &rep, now, txq) == 0) net_emit(n, NET_OP_SEND, NET_R_NONE, &rep);
        }
        return;
    }
    if (!n->is_router) return;                             /* host: not mine, so dropped silently (no event) */
    if (in.ttl <= 1) { net_emit(n, NET_OP_DROP, NET_R_TTL, &in); return; }
    memcpy(out, pkt, IPV4_PKT_LEN);
    ip_dec_ttl(out);                                       /* checksum is recomputed inside */
    if (net_send_ip(n, out, &in, now, txq) == 0) net_emit(n, NET_OP_FORWARD, NET_R_NONE, &in);
}

void net_tick(net_t *n, uint64_t now, txq_t *txq) {
    arp_tick(&n->arp, now, txq);
}
