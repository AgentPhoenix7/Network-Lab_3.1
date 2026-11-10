/* RFC 826 ARP packet: build and parse. Byte order is converted only here. */
#include <arpa/inet.h>
#include <string.h>
#include "arp_pkt.h"

/* Ethernet (htype 1), IPv4 (ptype 0x0800), hlen 6, plen 4 */
void arp_pkt_request(arp_pkt_t *p, const uint8_t sha[6], uint32_t spa, uint32_t tpa) {
    memset(p, 0, sizeof *p);                 /* tha stays all zero */
    p->htype = htons(1);
    p->ptype = htons(0x0800);
    p->hlen = 6;
    p->plen = 4;
    p->oper = htons(ARP_REQUEST);
    memcpy(p->sha, sha, 6);
    p->spa = htonl(spa);
    p->tpa = htonl(tpa);
}

void arp_pkt_reply(arp_pkt_t *p, const uint8_t sha[6], uint32_t spa,
                   const uint8_t tha[6], uint32_t tpa) {
    arp_pkt_request(p, sha, spa, tpa);      /* same layout, only oper and tha differ */
    p->oper = htons(ARP_REPLY);
    memcpy(p->tha, tha, 6);
}

int arp_pkt_parse(const uint8_t *buf, size_t len, arp_pkt_t *out) {
    arp_pkt_t p;
    if (len < sizeof p) return -1;
    /* memcpy, not a cast: buf may not be aligned for 16/32-bit reads */
    memcpy(&p, buf, sizeof p);
    if (ntohs(p.htype) != 1 || ntohs(p.ptype) != 0x0800 || p.hlen != 6 || p.plen != 4) return -2;
    if (ntohs(p.oper) != ARP_REQUEST && ntohs(p.oper) != ARP_REPLY) return -3;

    *out = p;                               /* copy the bytes, then fix the multi-byte fields */
    out->htype = ntohs(p.htype);
    out->ptype = ntohs(p.ptype);
    out->oper = ntohs(p.oper);
    out->spa = ntohl(p.spa);
    out->tpa = ntohl(p.tpa);
    return 0;
}
