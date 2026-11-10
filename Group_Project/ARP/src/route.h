#ifndef ROUTE_H
#define ROUTE_H
#include <stdint.h>

#define MAX_ROUTES 16

/* via 0 = on-link (next hop is the destination itself). */
typedef struct { uint32_t net; uint8_t prefix, iface; uint32_t via; } route_t;
typedef struct { route_t r[MAX_ROUTES]; int n; } rtable_t;

/* 0 found (iface and next_hop set); -1 no route. Longest prefix wins; ties go to the earlier entry. */
int route_lookup(const rtable_t *t, uint32_t dst, uint8_t *iface, uint32_t *next_hop);
#endif
