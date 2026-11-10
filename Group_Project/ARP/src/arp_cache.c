/* ARP cache: fixed table, no malloc. Entries are keyed by (iface, ip). plan.md 5.2. */
#include <string.h>
#include "arp_cache.h"

/* FREE slots never match, so a freed or flushed entry is invisible. */
arp_entry_t *arp_cache_find(arp_cache_t *c, uint8_t iface, uint32_t ip) {
    for (int i = 0; i < ARP_CACHE_SIZE; i++) {
        arp_entry_t *e = &c->e[i];
        if (e->state != ARP_FREE && e->iface == iface && e->ip == ip) return e;
    }
    return NULL;
}

/* Returns a zeroed slot; state stays FREE until the caller sets it. */
arp_entry_t *arp_cache_alloc(arp_cache_t *c, uint8_t iface, uint32_t ip) {
    arp_entry_t *victim = NULL;
    for (int i = 0; i < ARP_CACHE_SIZE; i++) {
        arp_entry_t *e = &c->e[i];
        if (e->state == ARP_FREE) { victim = e; break; }   /* a free slot always wins */
        /* RESOLVED only: PENDING is never evicted, since its packets are still waiting. */
        if (e->state == ARP_RESOLVED && (!victim || e->expires_at < victim->expires_at))
            victim = e;
    }
    if (!victim) return NULL;
    memset(victim, 0, sizeof *victim);
    victim->iface = iface;
    victim->ip = ip;
    return victim;
}

/* 0 ok; -1 queue full or packet too big. */
int arp_cache_enqueue(arp_entry_t *e, const uint8_t *pkt, size_t len) {
    if (e->qn == ARP_QUEUE_MAX || len > PAYLOAD_SIZE) return -1;
    arp_qpkt_t *q = &e->q[e->qn++];
    q->len = (uint8_t)len;
    memcpy(q->pkt, pkt, len);
    return 0;
}

void arp_cache_free(arp_entry_t *e) {
    memset(e, 0, sizeof *e);   /* ARP_FREE is 0 */
}

void arp_cache_flush(arp_cache_t *c) {
    memset(c, 0, sizeof *c);
}
