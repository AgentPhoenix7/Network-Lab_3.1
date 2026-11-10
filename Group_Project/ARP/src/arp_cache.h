#ifndef ARP_CACHE_H
#define ARP_CACHE_H
#include "l2.h"
#define ARP_CACHE_SIZE 32
#define ARP_QUEUE_MAX   4
typedef enum { ARP_FREE = 0, ARP_PENDING, ARP_RESOLVED } arp_state_t;
typedef struct { uint8_t len; uint8_t pkt[PAYLOAD_SIZE]; } arp_qpkt_t;
typedef struct {
    arp_state_t state;
    uint8_t  iface;  uint32_t ip;  uint8_t mac[6];
    uint64_t expires_at;          /* RESOLVED: TTL deadline (sim ms)  */
    uint64_t retry_at;            /* PENDING: next request retransmit */
    uint8_t  attempts;            /* requests sent so far             */
    arp_qpkt_t q[ARP_QUEUE_MAX];  uint8_t qn;   /* IPv4 packets waiting */
} arp_entry_t;
typedef struct { arp_entry_t e[ARP_CACHE_SIZE]; } arp_cache_t;

/* Keyed by (iface, ip): a router can see the same range on two interfaces. */
arp_entry_t *arp_cache_find (arp_cache_t *c, uint8_t iface, uint32_t ip);  /* NULL if absent */
/* New PENDING-ready slot (state still FREE->caller sets it): a FREE slot, else evict the RESOLVED
 * entry with the earliest expires_at; never evicts PENDING; NULL if full. Zeroed, iface/ip set. */
arp_entry_t *arp_cache_alloc(arp_cache_t *c, uint8_t iface, uint32_t ip);
int  arp_cache_enqueue(arp_entry_t *e, const uint8_t *pkt, size_t len);    /* 0 ok; -1 queue full */
void arp_cache_free (arp_entry_t *e);                                      /* back to FREE */
void arp_cache_flush(arp_cache_t *c);                                      /* all FREE */
#endif
