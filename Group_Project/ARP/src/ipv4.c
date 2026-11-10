#include "ipv4.h"
#include "error.h"
#include <stdio.h>
#include <string.h>

#define OFF_ICMP 20   /* ICMP starts right after the 20 B IPv4 header */
#define ICMP_LEN 24   /* ICMP 8 B + 16 B data: the span the ICMP checksum covers */

/* Wire is big-endian: these helpers are the only byte-order code in this file. */
static void put16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)(v >> 8); p[1] = (uint8_t)v; }
static void put32(uint8_t *p, uint32_t v) { put16(p, (uint16_t)(v >> 16)); put16(p + 2, (uint16_t)v); }
static uint16_t get16(const uint8_t *p) { return (uint16_t)((p[0] << 8) | p[1]); }
static uint32_t get32(const uint8_t *p) { return ((uint32_t)get16(p) << 16) | get16(p + 2); }

/* lab1 verify wants the data WITHOUT the checksum field, plus the field value: copy and zero it. */
static int csum_ok(const uint8_t *b, size_t len, size_t off) {
    uint8_t tmp[IPV4_PKT_LEN];
    memcpy(tmp, b, len);
    tmp[off] = 0;
    tmp[off + 1] = 0;
    return checksum16_verify(tmp, len, get16(b + off)) == 1;
}

void ip_build_echo(uint8_t b[IPV4_PKT_LEN], uint32_t src, uint32_t dst, uint16_t id, uint16_t seq, int is_reply) {
    char text[32];
    memset(b, 0, IPV4_PKT_LEN);
    b[0] = 0x45;                                   /* version 4, IHL 5 */
    put16(b + 2, IPV4_PKT_LEN);
    put16(b + 4, id);
    b[8] = IPV4_TTL_INIT;
    b[9] = IPV4_PROTO_ICMP;
    put32(b + 12, src);
    put32(b + 16, dst);
    put16(b + 10, checksum16_compute(b, 20));      /* field is zero here */
    b[OFF_ICMP] = is_reply ? ICMP_ECHO_REPLY : ICMP_ECHO_REQUEST;
    put16(b + OFF_ICMP + 4, id);
    put16(b + OFF_ICMP + 6, seq);
    snprintf(text, sizeof text, "ARPLAB-PING-%04u", (unsigned)seq);
    memcpy(b + OFF_ICMP + 8, text, 16);
    put16(b + OFF_ICMP + 2, checksum16_compute(b + OFF_ICMP, ICMP_LEN));
}

int ipv4_parse(const uint8_t *b, size_t len, ipv4_info_t *out) {
    if (len != IPV4_PKT_LEN || get16(b + 2) != IPV4_PKT_LEN) return -1;
    if (b[0] != 0x45 || b[9] != IPV4_PROTO_ICMP) return -2;
    if (b[OFF_ICMP] != ICMP_ECHO_REQUEST && b[OFF_ICMP] != ICMP_ECHO_REPLY) return -2;
    if (!csum_ok(b, 20, 10)) return -3;
    if (!csum_ok(b + OFF_ICMP, ICMP_LEN, 2)) return -4;
    out->src = get32(b + 12);
    out->dst = get32(b + 16);
    out->id = get16(b + OFF_ICMP + 4);
    out->seq = get16(b + OFF_ICMP + 6);
    out->type = b[OFF_ICMP];
    out->ttl = b[8];
    return 0;
}

/* TTL drops by one and the header checksum is recomputed, so the packet still verifies. */
int ip_dec_ttl(uint8_t *b) {
    b[8]--;
    put16(b + 10, 0);
    put16(b + 10, checksum16_compute(b, 20));
    return b[8];
}
