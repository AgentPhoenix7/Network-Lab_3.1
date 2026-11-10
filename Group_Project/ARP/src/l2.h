#ifndef L2_H
#define L2_H
#include <stdint.h>
#include <stddef.h>
#include "frame.h"

#define FT_ARP  0x06   /* EtherType 0x0806 squeezed into lab1's 1-byte frame_type */
#define FT_IPV4 0x08   /* EtherType 0x0800 */
#define MAX_IFACES 4
extern const uint8_t MAC_BCAST[6];

typedef struct { uint8_t iface, type, len; uint8_t dst[6]; uint8_t payload[PAYLOAD_SIZE]; } tx_t;
typedef struct { tx_t v[32]; int n; } txq_t;   /* frames the core wants sent */

/* 0 queued; -1 queue full or len > PAYLOAD_SIZE */
int txq_push(txq_t *q, uint8_t iface, uint8_t type, const uint8_t dst[6], const void *p, size_t len);

#pragma pack(push,1)
typedef struct { char node[8]; uint8_t iface; uint8_t mac[6]; uint8_t _pad; } nic_hello_t; /* 16 B, contract section 1 */
#pragma pack(pop)
_Static_assert(sizeof(nic_hello_t) == 16, "nic_hello_t must be 16 bytes");
#endif
