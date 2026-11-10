#ifndef IPV4_H
#define IPV4_H
#include <stdint.h>
#include <stddef.h>

/* Named ipv4_parse, not ip_parse: addr.h already owns ip_parse (dotted text to uint32). */
#define IPV4_PKT_LEN   44   /* 20 B IPv4 + 8 B ICMP + 16 B data (contract 1.2) */
#define IPV4_TTL_INIT  64
#define IPV4_PROTO_ICMP 1
#define ICMP_ECHO_REPLY   0
#define ICMP_ECHO_REQUEST 8

typedef struct { uint32_t src, dst; uint16_t id, seq; uint8_t type, ttl; } ipv4_info_t;

/* Writes a 44-byte echo request (is_reply 0) or reply (1). IPs in host order; wire order is done here. */
void ip_build_echo(uint8_t buf[IPV4_PKT_LEN], uint32_t src, uint32_t dst, uint16_t id, uint16_t seq, int is_reply);
/* 0 ok; -1 len != 44; -2 not IPv4 / ICMP echo; -3 bad IPv4 header checksum; -4 bad ICMP checksum */
int ipv4_parse(const uint8_t *buf, size_t len, ipv4_info_t *out);
/* Decrements TTL and recomputes the header checksum. Returns the new TTL. Caller must not pass TTL 0. */
int ip_dec_ttl(uint8_t *buf);
#endif
