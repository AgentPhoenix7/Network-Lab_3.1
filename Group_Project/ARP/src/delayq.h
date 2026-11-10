#ifndef DELAYQ_H
#define DELAYQ_H

#include <stdint.h>
#include "frame.h"   /* lab1: FRAME_SIZE */

#define DELAYQ_SIZE      64
#define DELAYQ_MAX_DESTS 8

typedef struct {
    uint8_t  frame[FRAME_SIZE];
    int      dests[DELAYQ_MAX_DESTS];
    int      ndest;
    uint64_t due;                 /* sim ms */
} delayq_item_t;

/* Sorted by due; equal dues keep push order. Pure: no clock, no malloc. */
typedef struct { delayq_item_t item[DELAYQ_SIZE]; int n; } delayq_t;

void delayq_init(delayq_t *q);
/* 0 ok; -1 queue full or n > DELAYQ_MAX_DESTS */
int delayq_push(delayq_t *q, uint64_t due, const uint8_t *frame, const int *dests, int n);
/* 1 and removes the earliest item if its due <= now (copied to *out); 0 otherwise */
int delayq_pop_due(delayq_t *q, uint64_t now, delayq_item_t *out);
/* earliest due, or UINT64_MAX when empty */
uint64_t delayq_next_due(const delayq_t *q);

#endif
