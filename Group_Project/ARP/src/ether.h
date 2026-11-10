#ifndef ETHER_H
#define ETHER_H
#include "l2.h"
/* Fills f with header + payload + CRC-32 FCS (lab1 scheme.h). 0 ok; -1 len > PAYLOAD_SIZE */
int eth_build(frame_t *f, uint8_t type, const uint8_t src[6], const uint8_t dst[6],
              uint16_t seq, const void *payload, size_t len);
/* 0 ok; -1 FCS mismatch; -2 frame_len > PAYLOAD_SIZE */
int eth_check(const frame_t *f);
#endif
