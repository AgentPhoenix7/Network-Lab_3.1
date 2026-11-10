#ifndef SWITCH_H
#define SWITCH_H
#include <stdint.h>

/* Learning-switch MAC table: fixed size, no aging (shortcut: no aging, the table
 * is only 16 entries and a lab run is short; add timestamps if runs get long). */
#define SWITCH_SIZE 16

typedef struct {
    uint8_t mac[SWITCH_SIZE][6];
    int port[SWITCH_SIZE];
    int n;     /* entries in use, up to SWITCH_SIZE */
    int next;  /* slot to overwrite next when full (FIFO eviction) */
} switch_t;

void switch_init(switch_t *t);                               /* empty table */
int  switch_learn(switch_t *t, const uint8_t mac[6], int port); /* 1 new or moved; 0 unchanged or broadcast source */
int  switch_lookup(const switch_t *t, const uint8_t mac[6]);   /* port; -1 unknown or broadcast (flood) */

#endif
