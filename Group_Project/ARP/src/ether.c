#include "ether.h"
#include "scheme.h"
#include <arpa/inet.h>
#include <string.h>

const uint8_t MAC_BCAST[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};

/* FCS covers header + payload only (61 bytes), same span as lab1 framing.c. */
#define FCS_SPAN (FRAME_SIZE - TRAILER_SIZE)

static uint32_t fcs_of(const frame_t *f) {
    return compute_fcs(SCHEME_CRC32, (const uint8_t *)f, FCS_SPAN);
}

int eth_build(frame_t *f, uint8_t type, const uint8_t src[6], const uint8_t dst[6],
              uint16_t seq, const void *payload, size_t len) {
    if (len > PAYLOAD_SIZE) return -1;
    memset(f, 0, sizeof *f);   /* zero padding after the payload */
    f->header.frame_type = type;
    memcpy(f->header.src_addr, src, 6);
    memcpy(f->header.dest_addr, dst, 6);
    f->header.frame_len = htons((uint16_t)len);
    f->header.frame_seq = htons(seq);
    memcpy(f->payload, payload, len);
    f->trailer.fcs = htonl(fcs_of(f));
    return 0;
}

/* 0 ok; -1 FCS mismatch; -2 frame_len > PAYLOAD_SIZE. FCS first, as in lab1. */
int eth_check(const frame_t *f) {
    if (fcs_of(f) != ntohl(f->trailer.fcs)) return -1;
    if (ntohs(f->header.frame_len) > PAYLOAD_SIZE) return -2;
    return 0;
}

/* 0 queued; -1 queue full or len > PAYLOAD_SIZE */
int txq_push(txq_t *q, uint8_t iface, uint8_t type, const uint8_t dst[6], const void *p, size_t len) {
    if (q->n >= (int)(sizeof q->v / sizeof q->v[0]) || len > PAYLOAD_SIZE) return -1;
    tx_t *t = &q->v[q->n++];
    t->iface = iface;
    t->type = type;
    t->len = (uint8_t)len;
    memcpy(t->dst, dst, 6);
    memcpy(t->payload, p, len);
    return 0;
}
