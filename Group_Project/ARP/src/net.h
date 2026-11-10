#ifndef NET_H
#define NET_H
#include <stdint.h>
#include <stddef.h>
#include "arp.h"
#include "ipv4.h"
#include "route.h"

typedef enum { NET_OP_SEND, NET_OP_DELIVER, NET_OP_FORWARD, NET_OP_DROP } net_op_t;
typedef enum { NET_R_NONE, NET_R_TTL, NET_R_NO_ROUTE, NET_R_ARP_FAIL, NET_R_QUEUE_FULL } net_reason_t;

/* One `ip` event (contract 2.1). With arp set it is an ARP event passed through instead.
 * arp points into the ARP cache and is valid only inside the callback. */
typedef struct {
    net_op_t op; net_reason_t reason;
    uint32_t src, dst; uint8_t icmp; uint16_t icmp_seq;
    const arp_ev_t *arp;
} net_ev_t;

typedef struct {
    arp_ctx_t arp;                 /* arp.ifs[] holds my IPs and MACs */
    rtable_t rt;
    int is_router;
    void (*on_ip)(void *u, const net_ev_t *ev);
    void *u;
} net_t;

/* Caller zeroes n and fills arp.ifs/nifs, rt, is_router, on_ip and u first. Wires the ARP hooks. */
void net_init(net_t *n);
/* Echo request to dst. 0 sent or queued; -1 no route; -2 queue full (each emits its drop). */
int  net_send_ping(net_t *n, uint32_t dst, uint16_t seq, uint64_t now, txq_t *txq);
/* One 44-byte IPv4 packet received on iface. A host drops non-local packets silently (no event). */
void net_input_ipv4(net_t *n, uint8_t iface, const uint8_t *pkt, size_t len, uint64_t now, txq_t *txq);
/* ARP retries and expiry. Call every loop pass. */
void net_tick(net_t *n, uint64_t now, txq_t *txq);
#endif
