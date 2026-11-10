#ifndef CHANNEL_H
#define CHANNEL_H

#include <stdint.h>
#include "delayq.h"
#include "switch.h"

#define CH_MAX_PORTS 32

typedef struct {
    int      lan;        /* LAN id, goes into every event */
    uint16_t port;       /* NIC listen port (9600 + lan in the launcher) */
    int      star;       /* 0 = bus (hub), 1 = star (learning switch) */
    uint16_t tel_port;   /* bridge telemetry port, 0 = stdio mode */
    unsigned seed;       /* seeds the impairment draws */
} channel_cfg_t;

typedef struct {
    int  fd;             /* -1 once the NIC has gone */
    char node[9];        /* from nic_hello_t, NUL-terminated here */
    uint8_t iface;
} channel_port_t;

typedef struct {
    int  lan, star, listen_fd, nports;
    channel_port_t port[CH_MAX_PORTS];  /* slots are never reused: switch ports index here */
    switch_t sw;
    delayq_t q;
    double   loss, corrupt;
    uint64_t delay_ms;
    int      quit;
} channel_t;

/* Listens for NICs and connects to telemetry; emits hello and ready. 0 ok; -1 failed. */
int channel_init(channel_t *ch, const channel_cfg_t *cfg);

/* Closes every socket. */
void channel_close(channel_t *ch);

/* One poll round, waiting at most timeout_ms (capped at 50 ms, or until the next due frame).
 * 0 running; 1 QUIT received; -1 poll failed. */
int channel_step(channel_t *ch, int timeout_ms);

/* Steps until QUIT. 0 clean exit; -1 failed. */
int channel_run(channel_t *ch);

#endif
