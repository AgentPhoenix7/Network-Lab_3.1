#ifndef CONF_H
#define CONF_H
#include <stddef.h>
#include <stdint.h>
#include "l2.h"

#define CONF_MAX_PROXY  8   /* arp_iface_t holds 8 proxy ranges */
#define CONF_MAX_ROUTES 8   /* plus connected and default routes, still under MAX_ROUTES */

typedef struct {
    int lan, port;
    uint8_t mac[6];
    uint32_t ip; uint8_t prefix;
    uint32_t proxy_net[CONF_MAX_PROXY]; uint8_t proxy_prefix[CONF_MAX_PROXY]; int nproxy;
} conf_iface_t;

typedef struct { uint32_t net, via; uint8_t prefix; } conf_route_t;   /* via is a next hop, not an iface */

typedef struct {
    char node[8]; int is_router;
    int nifaces; conf_iface_t ifaces[MAX_IFACES];
    int nroutes; conf_route_t routes[CONF_MAX_ROUTES];
    int has_gateway; uint32_t gateway;
    uint64_t ttl_ms, retry_ms; int retries;
} conf_t;

/* Parses the plan 5.6 conf text. 0 ok; -1 malformed, err holds "line N: reason". */
int conf_parse(const char *text, conf_t *c, char *err, size_t errlen);
#endif
