#ifndef TELEMETRY_H
#define TELEMETRY_H

#include <stdint.h>

/* Callback for one complete command line (no newline). */
typedef void (*tel_line_fn)(const char *line, void *arg);

/* One destination of a medium event. */
typedef struct { const char *node; int iface; } tel_dest_t;

/* port 0 = stdio mode (events to stdout, commands from stdin); else connect to 127.0.0.1:port.
 * 0 ok; -1 cannot connect. */
int tel_open(uint16_t port);

/* Writes one JSON line (adds the '\n'). 0 ok; -1 failed. */
int tel_emit(const char *line);

/* fd to poll for commands, -1 once the input is closed. */
int tel_in_fd(void);

/* Call once when tel_in_fd() is readable: reads once, calls fn for each complete line.
 * 0 ok; -1 input closed. */
int tel_read(tel_line_fn fn, void *arg);

void tel_close(void);

/* Contract 2.1 events. Each returns 0 ok; -1 failed. */
int tel_channel_ready(int lan);                  /* hello (role channel) then ready */
int tel_log(const char *level, const char *msg);
int tel_switch_learn(int lan, const uint8_t mac[6], const char *node, int iface);
/* bit < 0 leaves the field out (only corrupted frames carry it); nto <= 32 */
int tel_medium(int lan, const char *from, int from_iface, unsigned seq,
               const tel_dest_t *to, int nto, const char *fate, uint64_t delay_ms, long bit);

#endif
