#include "test_util.h"
#include "../protocol.h"
#include "../framing.h"
#include "../cdma.h"
#include "../walsh.h"
#include "scheme.h"

#include <arpa/inet.h>
#include <stdlib.h>
#include <string.h>

#define FCS_COVERED (FRAME_SIZE - TRAILER_SIZE) /* 61 */

/* F1 */
static void test_wire_sizes(void) {
    TEST_BEGIN("F1 wire sizes");
    assert_eq_i64(sizeof(frame_t), 65, "sizeof(frame_t) == 65");
    assert_eq_i64(sizeof(hello_msg_t), 4, "sizeof(hello_msg_t) == 4");
    assert_eq_i64(sizeof(config_msg_t), 12, "sizeof(config_msg_t) == 12");
    assert_eq_i64(sizeof(snd_hdr_t), 8, "sizeof(snd_hdr_t) == 8");
    assert_eq_i64(sizeof(rcv_hdr_t), 8, "sizeof(rcv_hdr_t) == 8");
    assert_eq_i64(CDMA_FRAME_CHIPS(8), 4160, "CDMA_FRAME_CHIPS(8) == 4160");
}

/* F2 */
static void test_build_fields(void) {
    TEST_BEGIN("F2 build fields");
    frame_t f;
    memset(&f, 0xFF, sizeof(f)); /* build must overwrite every byte, not rely on a zeroed frame */
    int rc = framing_build(&f, 3, 7, (const uint8_t *)"HELLO", 5);
    assert_true(rc == 0, "framing_build returns 0");
    assert_eq_i64(f.header.frame_type, 0, "frame_type == 0 (DATA)");

    uint8_t addr[6] = { 0, 0, 0, 0, 0, 3 };
    assert_eq_mem(f.header.src_addr, addr, 6, "src_addr == {0,0,0,0,0,3}");
    assert_eq_mem(f.header.dest_addr, addr, 6, "dest_addr == {0,0,0,0,0,3}");
    assert_eq_i64(ntohs(f.header.frame_len), 5, "ntohs(frame_len) == 5");
    assert_eq_i64(ntohs(f.header.frame_seq), 7, "ntohs(frame_seq) == 7");
    assert_eq_mem(f.payload, "HELLO", 5, "payload[0..4] == \"HELLO\"");

    int pad_zero = 1;
    for (int i = 5; i < PAYLOAD_SIZE; i++) if (f.payload[i] != 0) pad_zero = 0;
    assert_true(pad_zero, "payload[5..43] all zero");
}

/* F3 */
static void test_fcs(void) {
    TEST_BEGIN("F3 FCS is CRC-32 over the first 61 bytes");
    frame_t f;
    framing_build(&f, 3, 7, (const uint8_t *)"HELLO", 5);
    assert_eq_i64(FCS_COVERED, 61, "FCS covers 61 bytes");
    uint32_t want = compute_fcs(SCHEME_CRC32, (const uint8_t *)&f, FCS_COVERED);
    assert_true(ntohl(f.trailer.fcs) == want, "ntohl(trailer.fcs) == compute_fcs(CRC32, f, 61)");
}

/* F4 */
static void test_check_accepts(void) {
    TEST_BEGIN("F4 check accepts a built frame");
    frame_t f;
    framing_build(&f, 3, 7, (const uint8_t *)"HELLO", 5);

    uint8_t id = 0xEE;
    uint16_t seq = 0xEEEE;
    size_t len = 0xEE;
    assert_true(framing_check(&f, &id, &seq, &len) == 0, "check returns 0");
    assert_eq_i64(id, 3, "id == 3");
    assert_eq_i64(seq, 7, "seq == 7");
    assert_eq_i64(len, 5, "len == 5");
    assert_true(framing_check(&f, NULL, NULL, NULL) == 0, "NULL out-pointers accepted");
}

/* F5 */
static void test_single_bit_flip_rejected(void) {
    TEST_BEGIN("F5 any single bit flip is rejected");
    frame_t f;
    framing_build(&f, 3, 7, (const uint8_t *)"HELLO", 5);

    int rejected = 0;
    for (int bit = 0; bit < FRAME_SIZE * 8; bit++) {
        frame_t g = f;
        ((uint8_t *)&g)[bit / 8] ^= (uint8_t)(1u << (bit % 8));
        if (framing_check(&g, NULL, NULL, NULL) == -1) rejected++;
    }
    assert_eq_i64(rejected, 520, "all 520 single-bit flips return -1");
}

/* F6 */
static void test_build_length_bounds(void) {
    TEST_BEGIN("F6 length bounds on build");
    frame_t f;
    uint8_t buf[64];
    memset(buf, 0xAB, sizeof(buf));
    assert_true(framing_build(&f, 0, 0, buf, 0) == -1, "len 0 -> -1");
    assert_true(framing_build(&f, 0, 0, buf, 45) == -1, "len 45 -> -1");
    assert_true(framing_build(&f, 0, 0, buf, 44) == 0, "len 44 -> 0");
    assert_true(framing_build(&f, 0, 0, buf, 1) == 0, "len 1 -> 0");
}

/* F7 */
static void test_check_length_bounds(void) {
    TEST_BEGIN("F7 length bounds on check (after FCS)");
    frame_t f;
    framing_build(&f, 3, 7, (const uint8_t *)"HELLO", 5);
    f.header.frame_len = htons(50);
    f.trailer.fcs = htonl(compute_fcs(SCHEME_CRC32, (const uint8_t *)&f, FCS_COVERED));
    assert_true(framing_check(&f, NULL, NULL, NULL) == -2, "valid CRC but frame_len=50 -> -2");

    f.header.frame_len = htons(0);
    f.trailer.fcs = htonl(compute_fcs(SCHEME_CRC32, (const uint8_t *)&f, FCS_COVERED));
    assert_true(framing_check(&f, NULL, NULL, NULL) == -2, "valid CRC but frame_len=0 -> -2");
}

/* F8 */
static void test_seq_wraparound(void) {
    TEST_BEGIN("F8 seq 65535 round-trips");
    frame_t f;
    framing_build(&f, 1, 65535, (const uint8_t *)"x", 1);
    uint16_t seq = 0;
    assert_true(framing_check(&f, NULL, &seq, NULL) == 0, "check returns 0");
    assert_eq_i64(seq, 65535, "seq == 65535");
}

/* F9 */
static void test_pure_pipeline(void) {
    TEST_BEGIN("F9 pure pipeline n=3 L=4");
    const int n = 3, L = 4;
    const char *msgs[3] = { "alpha", "bravo-bravo", "c" };
    frame_t frames[3];
    int8_t code[4];
    size_t nchips = CDMA_FRAME_CHIPS(L);
    int8_t *chips[3];
    const int8_t *ptrs[3];

    for (int i = 0; i < n; i++) {
        assert_true(framing_build(&frames[i], (uint8_t)i, (uint16_t)(100 + i),
                                  (const uint8_t *)msgs[i], strlen(msgs[i])) == 0,
                    "build");
        walsh_code(L, i, code);
        chips[i] = malloc(nchips);
        cdma_spread((const uint8_t *)&frames[i], FRAME_SIZE, code, L, chips[i]);
        ptrs[i] = chips[i];
    }

    int16_t *sum = malloc(nchips * sizeof(int16_t));
    cdma_sum(ptrs, n, nchips, sum);

    int all_ok = 1;
    for (int i = 0; i < n; i++) {
        walsh_code(L, i, code);
        frame_t rx;
        cdma_frame_t cls = cdma_despread(sum, FRAME_SIZE, code, L, (uint8_t *)&rx, NULL);
        uint8_t id; uint16_t seq; size_t len;
        if (cls != CDMA_FRAME_DATA) { all_ok = 0; continue; }
        if (framing_check(&rx, &id, &seq, &len) != 0) { all_ok = 0; continue; }
        if (id != i || seq != 100 + i || len != strlen(msgs[i])) all_ok = 0;
        if (memcmp(rx.payload, msgs[i], len) != 0) all_ok = 0;
        if (memcmp(&rx, &frames[i], sizeof(frame_t)) != 0) all_ok = 0;
    }
    assert_true(all_ok, "all 3 frames survive spread -> sum -> despread -> check, payloads identical");

    for (int i = 0; i < n; i++) free(chips[i]);
    free(sum);
}

int main(void) {
    test_wire_sizes();
    test_build_fields();
    test_fcs();
    test_check_accepts();
    test_single_bit_flip_rejected();
    test_build_length_bounds();
    test_check_length_bounds();
    test_seq_wraparound();
    test_pure_pipeline();
    return test_summary();
}
