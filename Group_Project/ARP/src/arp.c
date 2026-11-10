/* ARP engine: output, input and cache-control modules (plan 5.3). Pure: time comes in as `now`. */
#include <string.h>
#include "arp.h"
#include "addr.h"

/* Entry events copy the entry's state, attempts and queue length. */
static void arp_emit(arp_ctx_t *a, arp_ev_op_t op, const arp_entry_t *e) {
    arp_ev_t ev = { .op = op, .iface = e->iface, .ip = e->ip, .mac = e->mac,
                    .state = e->state, .ttl_ms = a->ttl_ms, .attempts = e->attempts, .queued = e->qn };
    if (a->on_event) a->on_event(a->u, &ev);
}

/* Events with no cache entry (replies, flush). */
static void arp_emit_ip(arp_ctx_t *a, arp_ev_op_t op, uint8_t iface, uint32_t ip, const uint8_t *mac) {
    arp_ev_t ev = { .op = op, .iface = iface, .ip = ip, .mac = mac, .ttl_ms = a->ttl_ms };
    if (a->on_event) a->on_event(a->u, &ev);
}

/* Broadcast "who has tpa?" out of iface, sent from our own MAC and IP. */
static void arp_send_request(arp_ctx_t *a, uint8_t iface, uint32_t tpa, txq_t *out) {
    arp_pkt_t p;
    arp_pkt_request(&p, a->ifs[iface].mac, a->ifs[iface].ip, tpa);
    txq_push(out, iface, FT_ARP, MAC_BCAST, &p, sizeof p);
}

/* Send every packet parked on e to its MAC, then empty the queue. */
static void arp_flush_queue(arp_entry_t *e, txq_t *out) {
    for (int i = 0; i < e->qn; i++)
        txq_push(out, e->iface, FT_IPV4, e->mac, e->q[i].pkt, e->q[i].len);
    e->qn = 0;
}

static int in_proxy_list(const arp_iface_t *f, uint32_t tpa) {
    for (int i = 0; i < f->nproxy; i++)
        if (ip_in_prefix(tpa, f->proxy_net[i], f->proxy_prefix[i])) return 1;
    return 0;
}

static int proxy_should_answer(const arp_ctx_t *a, uint8_t iface, uint32_t tpa) {
    if (!in_proxy_list(&a->ifs[iface], tpa)) return 0;
    int out = a->egress(a->u, tpa);
    return out >= 0 && out != iface;   /* never answer for a host on the asking segment */
}

/* 0 sent now; 1 parked behind a request; -1 dropped. */
int arp_output(arp_ctx_t *a, uint8_t iface, uint32_t next_hop,
               const uint8_t *ip_pkt, size_t len, uint64_t now, txq_t *out) {
    if (iface >= a->nifs) return -1;   /* iface comes from the caller */
    arp_entry_t *e = arp_cache_find(&a->cache, iface, next_hop);
    if (e && e->state == ARP_RESOLVED)
        return txq_push(out, iface, FT_IPV4, e->mac, ip_pkt, len) == 0 ? 0 : -1;
    if (e) {   /* PENDING: a request is already on the wire */
        if (arp_cache_enqueue(e, ip_pkt, len) != 0) { arp_emit(a, AEV_QUEUE_FULL, e); return -1; }
        arp_emit(a, AEV_QUEUED, e);
        return 1;
    }
    e = arp_cache_alloc(&a->cache, iface, next_hop);
    if (!e || arp_cache_enqueue(e, ip_pkt, len) != 0) return -1;   /* slot stays FREE, so invisible */
    e->state = ARP_PENDING;
    e->attempts = 1;
    e->retry_at = now + a->retry_ms;
    arp_send_request(a, iface, next_hop, out);
    arp_emit(a, AEV_PENDING, e);
    arp_emit(a, AEV_QUEUED, e);
    return 1;
}

/* RFC 826 merge: refresh a sender we already know. Using an entry never does this. */
static void arp_merge(arp_ctx_t *a, arp_entry_t *e, const arp_pkt_t *p, uint64_t now, txq_t *out) {
    int was_pending = (e->state == ARP_PENDING);
    memcpy(e->mac, p->sha, 6);
    e->state = ARP_RESOLVED;
    e->expires_at = now + a->ttl_ms;
    if (was_pending) arp_flush_queue(e, out);   /* the packets that were waiting */
    arp_emit(a, was_pending ? AEV_RESOLVED : AEV_LEARN, e);
}

/* Answer the asker: sha is our MAC, spa is the IP that was asked for. */
static void arp_send_reply(arp_ctx_t *a, uint8_t iface, const arp_pkt_t *req, int proxy, txq_t *out) {
    arp_pkt_t r;
    arp_pkt_reply(&r, a->ifs[iface].mac, req->tpa, req->sha, req->spa);
    txq_push(out, iface, FT_ARP, req->sha, &r, sizeof r);
    arp_emit_ip(a, proxy ? AEV_PROXY_REPLY : AEV_REPLY_SENT, iface, req->tpa, a->ifs[iface].mac);
}

/* RFC 826 reception. Merge comes before the target test, so a bystander still refreshes known senders. */
void arp_input(arp_ctx_t *a, uint8_t iface, const uint8_t *payload, size_t len,
               uint64_t now, txq_t *out) {
    arp_pkt_t p;
    if (iface >= a->nifs || arp_pkt_parse(payload, len, &p) != 0) return;   /* not Ethernet/IPv4 ARP: ignore */

    arp_entry_t *e = arp_cache_find(&a->cache, iface, p.spa);
    int merge = (e != NULL);
    if (merge) arp_merge(a, e, &p, now, out);

    int for_me = (p.tpa == a->ifs[iface].ip);
    int proxy = !for_me && proxy_should_answer(a, iface, p.tpa);
    if (!for_me && !proxy) return;                      /* not for us: done */

    if (!merge) {   /* the asker will probably talk to us next */
        arp_entry_t *n = arp_cache_alloc(&a->cache, iface, p.spa);
        if (n) {
            n->state = ARP_RESOLVED;
            memcpy(n->mac, p.sha, 6);
            n->expires_at = now + a->ttl_ms;
            arp_emit(a, AEV_LEARN, n);
        }
    }
    if (p.oper == ARP_REQUEST) arp_send_reply(a, iface, &p, proxy, out);
}

/* Cache control for one slot: retry or fail a PENDING entry, expire a RESOLVED one. */
static void arp_tick_entry(arp_ctx_t *a, arp_entry_t *e, uint64_t now, txq_t *out) {
    if (e->state == ARP_PENDING && now >= e->retry_at) {
        if (a->max_attempts && e->attempts >= a->max_attempts) {   /* max_attempts 0 = retry forever */
            arp_emit(a, AEV_FAIL, e);   /* the queue is dropped with the slot */
            arp_cache_free(e);
        } else {
            if (e->attempts < UINT8_MAX) e->attempts++;   /* saturates: forever means forever */
            e->retry_at = now + a->retry_ms;
            arp_send_request(a, e->iface, e->ip, out);
            arp_emit(a, AEV_RETRY, e);
        }
    } else if (e->state == ARP_RESOLVED && now >= e->expires_at) {
        arp_emit(a, AEV_EXPIRE, e);
        arp_cache_free(e);
    }
}

void arp_tick(arp_ctx_t *a, uint64_t now, txq_t *out) {
    for (int i = 0; i < ARP_CACHE_SIZE; i++) arp_tick_entry(a, &a->cache.e[i], now, out);
}

uint64_t arp_next_deadline(const arp_ctx_t *a) {
    uint64_t best = UINT64_MAX;
    for (int i = 0; i < ARP_CACHE_SIZE; i++) {
        const arp_entry_t *e = &a->cache.e[i];
        if (e->state == ARP_PENDING && e->retry_at < best) best = e->retry_at;
        if (e->state == ARP_RESOLVED && e->expires_at < best) best = e->expires_at;
    }
    return best;
}

void arp_flush(arp_ctx_t *a) {
    arp_cache_flush(&a->cache);
    arp_emit_ip(a, AEV_FLUSH, 0, 0, NULL);
}
