#ifndef ADDR_H
#define ADDR_H
#include <stdint.h>
/* IPs are host-order uint32_t everywhere in C. */
int  mac_parse(const char *s, uint8_t mac[6]);        /* 0 ok; -1 bad text */
void mac_fmt(const uint8_t mac[6], char out[18]);     /* "aa:bb:cc:dd:ee:ff" */
int  ip_parse(const char *s, uint32_t *ip);           /* 0 ok; -1 bad text */
void ip_fmt(uint32_t ip, char out[16]);               /* "a.b.c.d" */
int  cidr_parse(const char *s, uint32_t *ip, uint8_t *prefix); /* "a.b.c.d/n"; 0 ok; -1 bad */
int  ip_in_prefix(uint32_t ip, uint32_t net, uint8_t prefix);  /* 1 inside; prefix 0 matches all */
#endif
