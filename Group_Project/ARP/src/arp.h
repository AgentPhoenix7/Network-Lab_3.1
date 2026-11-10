#ifndef ARP_H
#define ARP_H
#include "arp_cache.h"
#include "arp_pkt.h"
typedef struct {
    uint32_t ip; uint8_t prefix; uint8_t mac[6];
    uint32_t proxy_net[8]; uint8_t proxy_prefix[8]; uint8_t nproxy;   /* proxy ARP list */
} arp_iface_t;

typedef enum { AEV_PENDING, AEV_QUEUED, AEV_RETRY, AEV_FAIL, AEV_RESOLVED, AEV_LEARN,
               AEV_REPLY_SENT, AEV_PROXY_REPLY, AEV_EXPIRE, AEV_FLUSH, AEV_QUEUE_FULL } arp_ev_op_t;
typedef struct { arp_ev_op_t op; uint8_t iface; uint32_t ip; const uint8_t *mac;
                 arp_state_t state; uint64_t ttl_ms; uint8_t attempts, queued; } arp_ev_t;

typedef struct {
    arp_iface_t ifs[MAX_IFACES]; int nifs;
    arp_cache_t cache;
    uint64_t ttl_ms, retry_ms; uint8_t max_attempts;      /* defaults 30000, 1000, 0; max_attempts 0 = retry forever */
    int  (*egress)(void *u, uint32_t ip);                 /* route lookup, injected; -1 = no route */
    void (*on_event)(void *u, const arp_ev_t *ev);        /* telemetry, or a recorder in tests */
    void *u;
} arp_ctx_t;

/* Send ip_pkt toward next_hop on iface. 0 sent now; 1 queued behind a request; -1 dropped. */
int  arp_output(arp_ctx_t *a, uint8_t iface, uint32_t next_hop,
                const uint8_t *ip_pkt, size_t len, uint64_t now, txq_t *out);
/* RFC 826 reception: merge sender, answer if the target is us or a proxy target. */
void arp_input (arp_ctx_t *a, uint8_t iface, const uint8_t *payload, size_t len,
                uint64_t now, txq_t *out);
/* Retry PENDING entries, expire RESOLVED ones. Call every loop pass. */
void arp_tick  (arp_ctx_t *a, uint64_t now, txq_t *out);
/* Earliest retry_at / expires_at; UINT64_MAX if none. */
uint64_t arp_next_deadline(const arp_ctx_t *a);
void arp_flush (arp_ctx_t *a);                               /* clear cache; emits AEV_FLUSH */
#endif
