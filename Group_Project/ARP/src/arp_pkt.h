#ifndef ARP_PKT_H
#define ARP_PKT_H
#include <stdint.h>
#include <stddef.h>
#pragma pack(push,1)
typedef struct {
    uint16_t htype, ptype; uint8_t hlen, plen; uint16_t oper;
    uint8_t sha[6]; uint32_t spa; uint8_t tha[6]; uint32_t tpa;
} arp_pkt_t;                                   /* 28 bytes, RFC 826 / Forouzan Fig. 21.2 */
#pragma pack(pop)
_Static_assert(sizeof(arp_pkt_t) == 28, "arp_pkt_t must be 28 bytes");
#define ARP_REQUEST 1
#define ARP_REPLY   2

/* IPs are host order in C variables and network order only inside arp_pkt_t. */
void arp_pkt_request(arp_pkt_t *p, const uint8_t sha[6], uint32_t spa, uint32_t tpa);
void arp_pkt_reply  (arp_pkt_t *p, const uint8_t sha[6], uint32_t spa,
                     const uint8_t tha[6], uint32_t tpa);
/* 0 ok; -1 short; -2 htype/ptype/hlen/plen not Ethernet+IPv4; -3 bad oper.
 * On success fills *out in HOST order (oper, spa, tpa). */
int arp_pkt_parse(const uint8_t *buf, size_t len, arp_pkt_t *out);
#endif
