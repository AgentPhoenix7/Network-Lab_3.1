#include "ipv4.h"
#include "addr.h"
#include "test_util.h"
#include <string.h>

static uint32_t ip_of(const char *s) {
    uint32_t v = 0;
    (void)ip_parse(s, &v);
    return v;
}

int main(void) {
    uint8_t b[IPV4_PKT_LEN], r[IPV4_PKT_LEN];
    ipv4_info_t o, ro;
    uint32_t a = ip_of("10.0.1.10"), c = ip_of("10.0.1.20");

    TEST_BEGIN("N2 checksums valid on build");
    ip_build_echo(b, a, c, 0x1234, 7, 0);
    assert_eq_i64(ipv4_parse(b, sizeof b, &o), 0, "fresh request parses");
    assert_eq_i64(o.src, a, "src host order");
    assert_eq_i64(o.dst, c, "dst host order");
    assert_eq_i64(o.id, 0x1234, "id");
    assert_eq_i64(o.seq, 7, "seq");
    assert_eq_i64(o.type, 8, "echo request type");
    assert_eq_i64(o.ttl, 64, "TTL 64");
    assert_eq_i64(b[9], 1, "proto ICMP");
    assert_eq_mem(b + 28, "ARPLAB-PING-0007", 16, "16 B ping data");

    TEST_BEGIN("N2 header bit flip breaks IPv4 checksum");
    b[15] ^= 0x04;
    assert_eq_i64(ipv4_parse(b, sizeof b, &o), -3, "bad IPv4 checksum");
    b[15] ^= 0x04;
    assert_eq_i64(ipv4_parse(b, sizeof b, &o), 0, "restored packet parses");

    TEST_BEGIN("N2 ICMP bit flip breaks ICMP checksum");
    b[30] ^= 0x01;
    assert_eq_i64(ipv4_parse(b, sizeof b, &o), -4, "bad ICMP checksum");
    b[30] ^= 0x01;

    TEST_BEGIN("N3 TTL decrement recomputes checksum");
    assert_eq_i64(ip_dec_ttl(b), 63, "new TTL returned");
    assert_eq_i64(ipv4_parse(b, sizeof b, &o), 0, "packet still parses");
    assert_eq_i64(o.ttl, 63, "TTL read back");
    assert_eq_i64(ip_dec_ttl(b), 62, "second decrement");
    assert_eq_i64(ipv4_parse(b, sizeof b, &o), 0, "still parses after two");

    TEST_BEGIN("Echo reply swaps src/dst, keeps id/seq");
    ip_build_echo(b, a, c, 0x1234, 7, 0);
    assert_eq_i64(ipv4_parse(b, sizeof b, &o), 0, "request parses");
    ip_build_echo(r, o.dst, o.src, o.id, o.seq, 1);
    assert_eq_i64(ipv4_parse(r, sizeof r, &ro), 0, "reply parses");
    assert_eq_i64(ro.src, c, "reply src is old dst");
    assert_eq_i64(ro.dst, a, "reply dst is old src");
    assert_eq_i64(ro.id, 0x1234, "reply keeps id");
    assert_eq_i64(ro.seq, 7, "reply keeps seq");
    assert_eq_i64(ro.type, 0, "echo reply type");

    TEST_BEGIN("Short or non-ICMP packets rejected");
    assert_eq_i64(ipv4_parse(b, 43, &o), -1, "43 B packet");
    b[9] = 17;
    assert_eq_i64(ipv4_parse(b, sizeof b, &o), -2, "UDP proto rejected");

    return test_summary();
}
