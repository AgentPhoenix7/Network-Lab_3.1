#include <string.h>
#include "switch.h"

static const uint8_t BCAST[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};

/* Broadcast is never a station address, so it must not be learned or unicast. */
static int is_bcast(const uint8_t mac[6]) {
    return memcmp(mac, BCAST, 6) == 0;
}

static int find(const switch_t *t, const uint8_t mac[6]) {
    for (int i = 0; i < t->n; i++) {
        if (memcmp(t->mac[i], mac, 6) == 0) return i;
    }
    return -1;
}

void switch_init(switch_t *t) {
    memset(t, 0, sizeof *t);
}

int switch_learn(switch_t *t, const uint8_t mac[6], int port) {
    if (is_bcast(mac)) return 0;

    int i = find(t, mac);
    if (i >= 0) {
        if (t->port[i] == port) return 0;   /* unchanged */
        t->port[i] = port;                  /* moved: host was seen on a new port */
        return 1;
    }

    /* Table full: overwrite the oldest slot (FIFO) rather than refuse new hosts. */
    i = t->n < SWITCH_SIZE ? t->n++ : t->next;
    if (t->n == SWITCH_SIZE) t->next = (i + 1) % SWITCH_SIZE;
    memcpy(t->mac[i], mac, 6);
    t->port[i] = port;
    return 1;
}

int switch_lookup(const switch_t *t, const uint8_t mac[6]) {
    if (is_bcast(mac)) return -1;
    int i = find(t, mac);
    return i >= 0 ? t->port[i] : -1;
}
