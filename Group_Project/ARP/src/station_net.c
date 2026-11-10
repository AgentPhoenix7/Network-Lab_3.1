/* Conf to net_t: the glue between the parsed plan 5.6 file and the pure network layer. */
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "addr.h"
#include "station_net.h"
#include "utils.h"

static uint32_t mask_of(uint8_t prefix) {
    return prefix == 0 ? 0 : 0xffffffffu << (32 - prefix);
}

/* 0 ok; -1 table full. next hop 0 means on-link (route.h). */
static int route_add(net_t *n, uint32_t net_ip, uint8_t prefix, uint8_t iface, uint32_t via) {
    if (n->rt.n >= MAX_ROUTES) return -1;
    route_t *r = &n->rt.r[n->rt.n++];
    r->net = net_ip & mask_of(prefix);
    r->prefix = prefix;
    r->iface = iface;
    r->via = via;
    return 0;
}

/* Index of the iface whose LAN contains ip, or -1 if ip is not on a connected LAN. */
static int on_link_iface(const net_t *n, uint32_t ip) {
    for (int i = 0; i < n->arp.nifs; i++)
        if (ip_in_prefix(ip, n->arp.ifs[i].ip, n->arp.ifs[i].prefix)) return i;
    return -1;
}

static void copy_iface(net_t *n, int i, const conf_iface_t *f) {
    arp_iface_t *a = &n->arp.ifs[i];
    a->ip = f->ip;
    a->prefix = f->prefix;
    memcpy(a->mac, f->mac, 6);
    a->nproxy = (uint8_t)f->nproxy;
    memcpy(a->proxy_net, f->proxy_net, sizeof a->proxy_net);
    memcpy(a->proxy_prefix, f->proxy_prefix, sizeof a->proxy_prefix);
}

/* Adds one static route, or fails with a message naming the bad next hop. */
static int add_next_hop_route(net_t *n, uint32_t net_ip, uint8_t prefix, uint32_t via,
                              char *err, size_t errlen) {
    int j = on_link_iface(n, via);
    if (j < 0) {
        snprintf(err, errlen, "next hop is not on a connected LAN");
        return -1;
    }
    if (route_add(n, net_ip, prefix, (uint8_t)j, via) != 0) {
        snprintf(err, errlen, "too many routes");
        return -1;
    }
    return 0;
}

int station_net_setup(const conf_t *c, net_t *n, char *err, size_t errlen) {
    net_init(n);
    n->is_router = c->is_router;
    n->arp.ttl_ms = c->ttl_ms;
    n->arp.retry_ms = c->retry_ms;
    n->arp.max_attempts = (uint8_t)c->retries;
    n->arp.nifs = c->nifaces;
    for (int i = 0; i < c->nifaces; i++) {
        copy_iface(n, i, &c->ifaces[i]);
        if (route_add(n, n->arp.ifs[i].ip, n->arp.ifs[i].prefix, (uint8_t)i, 0) != 0) {
            snprintf(err, errlen, "too many routes");
            return -1;
        }
    }
    for (int i = 0; i < c->nroutes; i++) {
        const conf_route_t *r = &c->routes[i];
        if (add_next_hop_route(n, r->net, r->prefix, r->via, err, errlen) != 0) return -1;
    }
    if (c->has_gateway) {   /* hosts: default route through the gateway */
        if (add_next_hop_route(n, 0, 0, c->gateway, err, errlen) != 0) return -1;
    }
    return 0;
}

int station_load(const char *path, conf_t *c, char *err, size_t errlen) {
    size_t n = 0;
    uint8_t *raw = read_file(path, &n);
    if (!raw) return -1;
    char *text = malloc(n + 1);
    if (!text) { free(raw); return -1; }
    memcpy(text, raw, n);
    text[n] = '\0';
    free(raw);
    int rc = conf_parse(text, c, err, errlen);
    free(text);
    return rc;
}
