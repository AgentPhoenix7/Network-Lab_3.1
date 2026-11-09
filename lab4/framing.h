#ifndef LAB4_FRAMING_H
#define LAB4_FRAMING_H
#include <stdint.h>
#include <stddef.h>
#include "frame.h"

// len must be 1..PAYLOAD_SIZE.
int framing_build(frame_t *frame, uint8_t id, uint16_t seq, const uint8_t *payload, size_t len);

// 0 ok || -1 FCS mismatch ||-2 frame_len out of 1..PAYLOAD_SIZE 
int framing_check(const frame_t *f, uint8_t *id, uint16_t *seq, size_t *len);

#endif