#include "framing.h"
#include "scheme.h"

#include <arpa/inet.h>
#include <string.h>

#define FCS_SPAN (FRAME_SIZE - TRAILER_SIZE)

int framing_build(frame_t *frame, uint8_t id, uint16_t seq, const uint8_t *payload, size_t len) {
    if (len < 1 || len > PAYLOAD_SIZE) return -1;

    memset(frame, 0, sizeof(*frame));

    frame->header.frame_type = 0;
    frame->header.src_addr[5] = id;
    frame->header.dest_addr[5] = id;

    frame->header.frame_len = htons((uint16_t)len);
    frame->header.frame_seq = htons(seq);

    memcpy(frame->payload, payload, len);

    frame->trailer.fcs = htonl(compute_fcs(SCHEME_CRC32, (const uint8_t *)frame, FCS_SPAN));
    return 0;
}


int framing_check(const frame_t *f, uint8_t *id, uint16_t *seq, size_t *len) {
    uint32_t crc = compute_fcs(SCHEME_CRC32, (const uint8_t *)f, FCS_SPAN);
    if (crc != ntohl(f->trailer.fcs)) return -1;

    size_t n = ntohs(f->header.frame_len);
    if (n < 1 || n > PAYLOAD_SIZE) return -2;

    if (id)  *id  = f->header.src_addr[5];
    if (seq) *seq = ntohs(f->header.frame_seq);
    if (len) *len = n;
    return 0;
}
