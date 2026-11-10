#include "addr.h"
#include <stdio.h>

/* 0 ok; -1 bad text. "%c" catches trailing junk: it makes sscanf return 7, not 6. */
int mac_parse(const char *s, uint8_t mac[6]) {
    unsigned v[6];
    char tail;
    if (sscanf(s, "%2x:%2x:%2x:%2x:%2x:%2x%c", &v[0], &v[1], &v[2], &v[3], &v[4], &v[5], &tail) != 6)
        return -1;
    for (int i = 0; i < 6; i++) mac[i] = (uint8_t)v[i];
    return 0;
}

void mac_fmt(const uint8_t mac[6], char out[18]) {
    snprintf(out, 18, "%02x:%02x:%02x:%02x:%02x:%02x",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

/* 0 ok; -1 bad text or an octet above 255. Host order, so 10.0.1.21 is 0x0A000115. */
int ip_parse(const char *s, uint32_t *ip) {
    unsigned a, b, c, d;
    char tail;
    if (sscanf(s, "%u.%u.%u.%u%c", &a, &b, &c, &d, &tail) != 4) return -1;
    if (a > 255 || b > 255 || c > 255 || d > 255) return -1;
    *ip = (a << 24) | (b << 16) | (c << 8) | d;
    return 0;
}

void ip_fmt(uint32_t ip, char out[16]) {
    snprintf(out, 16, "%u.%u.%u.%u", ip >> 24, (ip >> 16) & 255, (ip >> 8) & 255, ip & 255);
}

/* 0 ok; -1 bad text, prefix above 32, or missing "/n". */
int cidr_parse(const char *s, uint32_t *ip, uint8_t *prefix) {
    unsigned a, b, c, d, p;
    char tail;
    if (sscanf(s, "%u.%u.%u.%u/%u%c", &a, &b, &c, &d, &p, &tail) != 5) return -1;
    if (a > 255 || b > 255 || c > 255 || d > 255 || p > 32) return -1;
    *ip = (a << 24) | (b << 16) | (c << 8) | d;
    *prefix = (uint8_t)p;
    return 0;
}

/* 1 inside; prefix 0 matches all. A 32-bit shift by 32 is undefined, so /0 gets mask 0 explicitly. */
int ip_in_prefix(uint32_t ip, uint32_t net, uint8_t prefix) {
    uint32_t mask = prefix == 0 ? 0 : 0xffffffffu << (32 - prefix);
    return ((ip ^ net) & mask) == 0;
}
