#ifndef STATION_NET_H
#define STATION_NET_H
#include <stddef.h>
#include "conf.h"
#include "net.h"

/* Fills net from the parsed conf: ifaces, ARP timers, and routes (connected, conf routes, default).
 * 0 ok; -1 bad conf (next hop not on a LAN, table full), err says which. */
int station_net_setup(const conf_t *c, net_t *n, char *err, size_t errlen);
/* Reads and parses the conf file. 0 ok; -1 unreadable or malformed, err says why. */
int station_load(const char *path, conf_t *c, char *err, size_t errlen);
#endif
