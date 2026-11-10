/* Station events as contract-shaped JSON lines. Built with snprintf only (no JSON in C). */
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <arpa/inet.h>
#include "addr.h"
#include "ipv4.h"
#include "station_ev.h"
#include "telemetry.h"

#define LINE_BUF 512

static const char *arp_op_name(arp_ev_op_t op) {
    static const char *names[] = { "pending", "queued", "retry", "fail", "resolved", "learn",
                                   "reply_sent", "proxy_reply", "expire", "flush" };
    return names[op];   /* caller has excluded AEV_QUEUE_FULL */
}

static const char *state_name(arp_state_t s) {
    return s == ARP_PENDING ? "\"PENDING\"" : s == ARP_RESOLVED ? "\"RESOLVED\"" : "null";
}

int ev_hello(const char *node) {
    char b[LINE_BUF];
    snprintf(b, sizeof b, "{\"ev\":\"hello\",\"role\":\"station\",\"node\":\"%s\"}", node);
    return tel_emit(b);
}

int ev_ready(const char *node) {
    char b[LINE_BUF];
    snprintf(b, sizeof b, "{\"ev\":\"ready\",\"node\":\"%s\"}", node);
    return tel_emit(b);
}

/* Full 65-byte frame as 130 hex chars. uid is "<node>:<iface>:<seq>" as in contract 2.1. */
int ev_tx(const char *node, int iface, const frame_t *f) {
    char b[LINE_BUF], hex[2 * FRAME_SIZE + 1];
    const uint8_t *p = (const uint8_t *)f;
    for (int i = 0; i < FRAME_SIZE; i++) snprintf(hex + 2 * i, 3, "%02x", p[i]);
    snprintf(b, sizeof b, "{\"ev\":\"tx\",\"node\":\"%s\",\"iface\":%d,\"uid\":\"%s:%d:%u\",\"hex\":\"%s\"}",
             node, iface, node, iface, ntohs(f->header.frame_seq), hex);
    return tel_emit(b);
}

/* The frame carries no sender node name, so the uid is the sender's MAC and seq. */
int ev_rx(const char *node, int iface, const frame_t *f, const char *verdict) {
    char b[LINE_BUF], mac[18];
    mac_fmt(f->header.src_addr, mac);
    snprintf(b, sizeof b, "{\"ev\":\"rx\",\"node\":\"%s\",\"iface\":%d,\"uid\":\"%s:%u\",\"verdict\":\"%s\"}",
             node, iface, mac, ntohs(f->header.frame_seq), verdict);
    return tel_emit(b);
}

int ev_arp(const char *node, const arp_ev_t *e) {
    char b[LINE_BUF], ip[16], mac[20] = "null";
    if (e->op == AEV_QUEUE_FULL) return 0;
    ip_fmt(e->ip, ip);
    if (e->mac && e->state != ARP_PENDING) {   /* contract 4: an unresolved mac is null */
        char m[18];
        mac_fmt(e->mac, m);
        snprintf(mac, sizeof mac, "\"%s\"", m);
    }
    snprintf(b, sizeof b,
             "{\"ev\":\"arp\",\"node\":\"%s\",\"iface\":%d,\"op\":\"%s\",\"ip\":\"%s\",\"mac\":%s,"
             "\"state\":%s,\"ttl_ms\":%llu,\"attempts\":%d,\"queued\":%d}",
             node, e->iface, arp_op_name(e->op), ip, mac,
             state_name(e->state), (unsigned long long)e->ttl_ms, e->attempts, e->queued);
    return tel_emit(b);
}

static const char *ip_op_name(net_op_t op) {
    static const char *names[] = { "send", "deliver", "forward", "drop" };
    return names[op];
}

static const char *ip_reason_name(net_reason_t r) {
    static const char *names[] = { "", "ttl", "no_route", "arp_fail", "queue_full" };
    return names[r];
}

int ev_ip(const char *node, const net_ev_t *e) {
    char b[LINE_BUF], src[16], dst[16], reason[32] = "";
    const char *icmp = e->icmp == ICMP_ECHO_REQUEST ? "echo_req" : "echo_rep";
    ip_fmt(e->src, src);
    ip_fmt(e->dst, dst);
    if (e->op == NET_OP_DROP) snprintf(reason, sizeof reason, ",\"reason\":\"%s\"", ip_reason_name(e->reason));
    snprintf(b, sizeof b, "{\"ev\":\"ip\",\"node\":\"%s\",\"op\":\"%s\",\"src\":\"%s\",\"dst\":\"%s\","
             "\"icmp\":\"%s\",\"icmp_seq\":%u%s}", node, ip_op_name(e->op), src, dst, icmp, e->icmp_seq, reason);
    return tel_emit(b);
}

void ev_dump_cache(const char *node, const arp_cache_t *c, uint64_t ttl_ms) {
    for (int i = 0; i < ARP_CACHE_SIZE; i++) {
        const arp_entry_t *e = &c->e[i];
        if (e->state == ARP_FREE) continue;
        arp_ev_t ev = { .op = AEV_LEARN, .iface = e->iface, .ip = e->ip, .mac = e->mac,
                        .state = e->state, .ttl_ms = ttl_ms, .attempts = e->attempts, .queued = e->qn };
        ev_arp(node, &ev);
    }
}
