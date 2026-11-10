#ifndef STATION_EV_H
#define STATION_EV_H
#include "arp.h"
#include "l2.h"
#include "net.h"

/* Contract 2.1 events from the station. Each returns 0 ok; -1 failed (telemetry is down). */
int ev_hello(const char *node);
int ev_ready(const char *node);
int ev_tx(const char *node, int iface, const frame_t *f);
/* verdict is "accepted", "ignored" or "fcs_fail". uid is the sender's MAC and frame_seq (see report). */
int ev_rx(const char *node, int iface, const frame_t *f, const char *verdict);
/* AEV_QUEUE_FULL is skipped: net reports it as ip drop queue_full. */
int ev_arp(const char *node, const arp_ev_t *e);
int ev_ip(const char *node, const net_ev_t *e);
/* DUMP: every non-FREE cache entry as an arp learn line, with its real state. */
void ev_dump_cache(const char *node, const arp_cache_t *c, uint64_t ttl_ms);
#endif
