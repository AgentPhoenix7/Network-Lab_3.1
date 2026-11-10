/* M2 tests: ARP packet build and parse (plan.md 5.1, rows P1-P5). */
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <string.h>
#include "test_util.h"
#include "arp_pkt.h"

/* IPs in host order: 130.23.43.20 and 130.23.43.25 */
static const uint32_t SENDER_IP = 0x82172B14u;
static const uint32_t TARGET_IP = 0x82172B19u;
static const uint8_t  SENDER_MAC[6] = {0xB2, 0x34, 0x55, 0x10, 0x22, 0x10};
static const uint8_t  REPLY_MAC[6]  = {0x0A, 0x00, 0x20, 0x00, 0x00, 0x01};

/* Forouzan Example 21.1, request (28 bytes). */
static const uint8_t REQ_BYTES[28] = {
    0x00, 0x01, 0x08, 0x00, 0x06, 0x04, 0x00, 0x01,
    0xB2, 0x34, 0x55, 0x10, 0x22, 0x10, 0x82, 0x17, 0x2B, 0x14,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x82, 0x17, 0x2B, 0x19
};

/* Same layout, oper 2: sha/spa = the target, tha/tpa = the original sender. */
static const uint8_t REPLY_BYTES[28] = {
    0x00, 0x01, 0x08, 0x00, 0x06, 0x04, 0x00, 0x02,
    0x0A, 0x00, 0x20, 0x00, 0x00, 0x01, 0x82, 0x17, 0x2B, 0x19,
    0xB2, 0x34, 0x55, 0x10, 0x22, 0x10, 0x82, 0x17, 0x2B, 0x14
};

static void test_size(void) {
    TEST_BEGIN("P1 sizeof(arp_pkt_t) == 28");
    assert_eq_i64(sizeof(arp_pkt_t), 28, "packed size");
}

static void test_request_bytes(void) {
    TEST_BEGIN("P2 Forouzan 21.1 request, byte-exact");
    arp_pkt_t p;
    memset(&p, 0xEE, sizeof p);             /* junk, so unset fields show up */
    arp_pkt_request(&p, SENDER_MAC, SENDER_IP, TARGET_IP);
    assert_eq_mem(&p, REQ_BYTES, 28, "request bytes");
}

static void test_reply_bytes(void) {
    TEST_BEGIN("P3 Forouzan 21.1 reply, byte-exact");
    arp_pkt_t p;
    memset(&p, 0xEE, sizeof p);
    arp_pkt_reply(&p, REPLY_MAC, TARGET_IP, SENDER_MAC, SENDER_IP);
    assert_eq_mem(&p, REPLY_BYTES, 28, "reply bytes");
}

static void test_parse_rejects(void) {
    TEST_BEGIN("P4 parse rejects bad input");
    uint8_t b[28];
    arp_pkt_t out;

    memcpy(b, REQ_BYTES, 28); b[1] = 0x02;              /* htype 2 */
    assert_eq_i64(arp_pkt_parse(b, 28, &out), -2, "htype != 1");

    memcpy(b, REQ_BYTES, 28); b[2] = 0x86; b[3] = 0xDD; /* ptype IPv6 */
    assert_eq_i64(arp_pkt_parse(b, 28, &out), -2, "ptype != 0x0800");

    memcpy(b, REQ_BYTES, 28); b[4] = 8;                 /* hlen 8 */
    assert_eq_i64(arp_pkt_parse(b, 28, &out), -2, "hlen != 6");

    memcpy(b, REQ_BYTES, 28); b[5] = 16;                /* plen 16 */
    assert_eq_i64(arp_pkt_parse(b, 28, &out), -2, "plen != 4");

    memcpy(b, REQ_BYTES, 28); b[7] = 3;                 /* oper 3 */
    assert_eq_i64(arp_pkt_parse(b, 28, &out), -3, "oper not 1 or 2");

    memcpy(b, REQ_BYTES, 28); b[7] = 0;                 /* oper 0 */
    assert_eq_i64(arp_pkt_parse(b, 28, &out), -3, "oper 0");

    assert_eq_i64(arp_pkt_parse(REQ_BYTES, 27, &out), -1, "len 27 is short");
}

static void test_parse_good(void) {
    TEST_BEGIN("P4 parse accepts good packets, host order out");
    arp_pkt_t out;
    memset(&out, 0, sizeof out);
    assert_eq_i64(arp_pkt_parse(REQ_BYTES, 28, &out), 0, "request parses");
    assert_eq_i64(out.oper, ARP_REQUEST, "oper is 1");
    assert_eq_i64(out.spa, SENDER_IP, "spa in host order");
    assert_eq_i64(out.tpa, TARGET_IP, "tpa in host order");

    assert_eq_i64(arp_pkt_parse(REPLY_BYTES, 28, &out), 0, "reply parses");
    assert_eq_i64(out.oper, ARP_REPLY, "oper is 2");
    assert_eq_mem(out.tha, SENDER_MAC, 6, "tha is the original sender MAC");
}

static void test_golden(void) {
    TEST_BEGIN("P5 request matches tests/golden/arp_request.hex");
    arp_pkt_t p;
    arp_pkt_request(&p, SENDER_MAC, SENDER_IP, TARGET_IP);

    char hex[2 * 28 + 1];
    static const char digits[] = "0123456789abcdef";
    const uint8_t *raw = (const uint8_t *)&p;
    for (int i = 0; i < 28; i++) {
        hex[2 * i]     = digits[raw[i] >> 4];
        hex[2 * i + 1] = digits[raw[i] & 0xF];
    }
    hex[56] = '\0';

    char line[128] = "";
    FILE *f = fopen("tests/golden/arp_request.hex", "r");
    assert_true(f != NULL, "golden file opens (run from the ARP directory)");
    if (f) {
        if (fgets(line, sizeof line, f)) line[strcspn(line, "\r\n")] = '\0';
        fclose(f);
    }
    assert_true(strlen(line) == 56, "golden file holds 56 hex chars");
    assert_true(strcmp(line, hex) == 0, "built packet equals golden hex");
}

int main(void) {
    test_size();
    test_request_bytes();
    test_reply_bytes();
    test_parse_rejects();
    test_parse_good();
    test_golden();
    return test_summary();
}
