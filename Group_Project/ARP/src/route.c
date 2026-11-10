#include "route.h"
#include "addr.h"

/* 0 found; -1 no route. Longest prefix wins, so a /29 beats a /24 beats the /0 default. */
int route_lookup(const rtable_t *t, uint32_t dst, uint8_t *iface, uint32_t *next_hop) {
    int best = -1;
    for (int i = 0; i < t->n; i++) {
        const route_t *r = &t->r[i];
        if (!ip_in_prefix(dst, r->net, r->prefix)) continue;
        if (best < 0 || r->prefix > t->r[best].prefix) best = i;
    }
    if (best < 0) return -1;
    *iface = t->r[best].iface;
    *next_hop = t->r[best].via ? t->r[best].via : dst;   /* on-link: ARP for dst itself */
    return 0;
}
