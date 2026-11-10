#include "delayq.h"

#include <string.h>

void delayq_init(delayq_t *q) {
    q->n = 0;
}

int delayq_push(delayq_t *q, uint64_t due, const uint8_t *frame, const int *dests, int n) {
    if (q->n == DELAYQ_SIZE || n < 0 || n > DELAYQ_MAX_DESTS) return -1;

    /* Insertion sort: shift later items up. Strict '>' keeps equal dues in push order. */
    int i = q->n;
    while (i > 0 && q->item[i - 1].due > due) {
        q->item[i] = q->item[i - 1];
        i--;
    }

    delayq_item_t *it = &q->item[i];
    memcpy(it->frame, frame, FRAME_SIZE);
    for (int k = 0; k < n; k++) it->dests[k] = dests[k];
    it->ndest = n;
    it->due = due;
    q->n++;
    return 0;
}

int delayq_pop_due(delayq_t *q, uint64_t now, delayq_item_t *out) {
    if (q->n == 0 || q->item[0].due > now) return 0;
    *out = q->item[0];
    memmove(&q->item[0], &q->item[1], (size_t)(q->n - 1) * sizeof q->item[0]);
    q->n--;
    return 1;
}

uint64_t delayq_next_due(const delayq_t *q) {
    return q->n ? q->item[0].due : UINT64_MAX;
}
