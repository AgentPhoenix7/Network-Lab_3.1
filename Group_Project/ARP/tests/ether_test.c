#include "ether.h"
#include "scheme.h"
#include "bit_flip.h"
#include "test_util.h"
#include <arpa/inet.h>
#include <string.h>

static const uint8_t SRC[6] = {0x02, 0, 0, 0, 0, 0x01};
static const uint8_t DST[6] = {0x02, 0, 0, 0, 0, 0x02};

int main(void) {
    frame_t f, g;
    uint8_t pl[28], raw_fcs[4];
    uint32_t want_fcs;
    txq_t q;
    size_t b;
    int i;

    for (i = 0; i < 28; i++) pl[i] = (uint8_t)(i * 7 + 1);

    TEST_BEGIN("E1 build then check");
    assert_eq_i64(sizeof f, FRAME_SIZE, "frame_t is 65 bytes");
    assert_eq_i64(eth_build(&f, FT_ARP, SRC, DST, 0x1234, pl, sizeof pl), 0, "build ok");
    assert_eq_i64(eth_check(&f), 0, "fresh frame checks ok");
    assert_eq_i64(eth_build(&f, FT_ARP, SRC, DST, 0, pl, PAYLOAD_SIZE + 1), -1,
                  "len > 44 refused");

    TEST_BEGIN("E2 every single-bit flip is caught");
    assert_eq_i64(FRAME_SIZE * 8, 520, "520 bit positions");
    for (b = 0; b < FRAME_SIZE * 8; b++) {
        g = f;
        flip_bit((uint8_t *)&g, FRAME_SIZE, b);
        assert_true(eth_check(&g) != 0, "flipped bit detected");
    }

    TEST_BEGIN("E2 frame_len > 44 with a valid FCS gives -2");
    g = f;
    g.header.frame_len = htons(45);
    want_fcs = compute_fcs(SCHEME_CRC32, (const uint8_t *)&g, FRAME_SIZE - TRAILER_SIZE);
    g.trailer.fcs = htonl(want_fcs);
    assert_eq_i64(eth_check(&g), -2, "oversize length rejected");

    TEST_BEGIN("E3 fields preserved and big-endian");
    assert_true(f.header.frame_type == FT_ARP, "type kept");
    assert_eq_i64(ntohs(f.header.frame_len), 28, "len kept");
    assert_eq_i64(ntohs(f.header.frame_seq), 0x1234, "seq kept");
    assert_eq_mem(f.header.src_addr, SRC, 6, "src kept");
    assert_eq_mem(f.header.dest_addr, DST, 6, "dst kept");
    assert_eq_mem(f.payload, pl, 28, "payload kept");
    assert_eq_i64(f.payload[28], 0, "payload padded with zeros");
    assert_eq_i64(((uint8_t *)&f)[15], 0x12, "seq high byte first on the wire");
    assert_eq_i64(((uint8_t *)&f)[16], 0x34, "seq low byte second on the wire");
    want_fcs = compute_fcs(SCHEME_CRC32, (const uint8_t *)&f, FRAME_SIZE - TRAILER_SIZE);
    memcpy(raw_fcs, (uint8_t *)&f + FRAME_SIZE - TRAILER_SIZE, 4);
    assert_true(raw_fcs[0] == (uint8_t)(want_fcs >> 24) &&
                raw_fcs[3] == (uint8_t)want_fcs, "FCS stored big-endian over 61 bytes");

    TEST_BEGIN("txq_push");
    memset(&q, 0, sizeof q);
    for (i = 0; i < 32; i++)
        assert_eq_i64(txq_push(&q, 1, FT_IPV4, DST, pl, 10), 0, "push while not full");
    assert_eq_i64(txq_push(&q, 1, FT_IPV4, DST, pl, 10), -1, "33rd push refused");
    assert_eq_i64(q.n, 32, "queue holds 32");

    memset(&q, 0, sizeof q);
    assert_eq_i64(txq_push(&q, 2, FT_ARP, DST, pl, PAYLOAD_SIZE + 1), -1, "oversize refused");
    assert_eq_i64(q.n, 0, "refused push leaves queue empty");
    assert_eq_i64(txq_push(&q, 2, FT_ARP, DST, pl, PAYLOAD_SIZE), 0, "44 bytes accepted");
    assert_eq_i64(q.v[0].iface, 2, "iface stored");
    assert_eq_i64(q.v[0].type, FT_ARP, "type stored");
    assert_eq_i64(q.v[0].len, PAYLOAD_SIZE, "len stored");
    assert_eq_mem(q.v[0].dst, DST, 6, "dst stored");

    return test_summary();
}
