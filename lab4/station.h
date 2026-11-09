#ifndef LAB4_STATION_H
#define LAB4_STATION_H
#include <stdint.h>
#include "protocol.h"

/* Shared hello/config handshake for sender.c and receiver.c (CLAUDE.md R1).
 * Connects to host:port (retrying ~5 s while the channel starts), sends
 * hello {role, id}, receives config_msg_t into *cfg (host byte order).
 * Returns the connected fd, or -1 after printing why. */
int station_join(const char *host, unsigned int port, role_t role,
                 unsigned int id, config_msg_t *cfg);

#endif
