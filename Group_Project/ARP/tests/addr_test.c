#include "addr.h"
#include "test_util.h"
#include <string.h>

int main(void) {
    uint8_t mac[6], want_mac[6] = {0xaa, 0xbb, 0xcc, 0x01, 0x23, 0xff};
    char buf[18];
    uint32_t ip, p_ip;
    uint8_t prefix;

    TEST_BEGIN("A1 MAC parse/format round-trip");
    assert_eq_i64(mac_parse("aa:bb:cc:01:23:ff", mac), 0, "mac_parse ok");
    assert_eq_mem(mac, want_mac, 6, "mac bytes");
    mac_fmt(mac, buf);
    assert_true(strcmp(buf, "aa:bb:cc:01:23:ff") == 0, "mac_fmt round-trip");
    assert_eq_i64(mac_parse("aa:bb:cc:01:23", mac), -1, "short MAC rejected");
    assert_eq_i64(mac_parse("aa:bb:cc:01:23:ff:00", mac), -1, "long MAC rejected");
    assert_eq_i64(mac_parse("zz:bb:cc:01:23:ff", mac), -1, "non-hex MAC rejected");
    assert_eq_i64(mac_parse("aa-bb-cc-01-23-ff", mac), -1, "dashed MAC rejected");

    TEST_BEGIN("A1 IP parse/format round-trip");
    assert_eq_i64(ip_parse("10.0.1.21", &ip), 0, "ip_parse ok");
    assert_eq_i64(ip, 0x0A000115, "ip value host order");
    ip_fmt(ip, buf);
    assert_true(strcmp(buf, "10.0.1.21") == 0, "ip_fmt round-trip");
    assert_eq_i64(ip_parse("10.0.1", &ip), -1, "3-part IP rejected");
    assert_eq_i64(ip_parse("10.0.1.256", &ip), -1, "octet 256 rejected");
    assert_eq_i64(ip_parse("10.0.1.2.3", &ip), -1, "5-part IP rejected");
    assert_eq_i64(ip_parse("10.0.1.21/24", &ip), -1, "trailing text rejected");
    assert_eq_i64(ip_parse("abc", &ip), -1, "non-numeric IP rejected");

    TEST_BEGIN("A1 CIDR parse");
    assert_eq_i64(cidr_parse("10.0.1.0/24", &ip, &prefix), 0, "cidr_parse ok");
    assert_eq_i64(ip, 0x0A000100, "cidr ip value");
    assert_eq_i64(prefix, 24, "cidr prefix value");
    assert_eq_i64(cidr_parse("10.0.1.0/33", &ip, &prefix), -1, "prefix 33 rejected");
    assert_eq_i64(cidr_parse("10.0.1.0", &ip, &prefix), -1, "missing prefix rejected");
    assert_eq_i64(cidr_parse("10.0.1.0/24x", &ip, &prefix), -1, "trailing text rejected");

    TEST_BEGIN("A2 prefix match");
    p_ip = 0x08080808;
    assert_eq_i64(ip_in_prefix(p_ip, 0, 0), 1, "/0 matches any address");
    assert_eq_i64(ip_in_prefix(0xC0A80101, 0, 0), 1, "/0 matches private address");

    assert_eq_i64(ip_in_prefix(0x0A0001C8, 0x0A000100, 24), 1, "/24 inside 10.0.1.200");
    assert_eq_i64(ip_in_prefix(0x0A000201, 0x0A000100, 24), 0, "/24 outside 10.0.2.1");

    assert_eq_i64(ip_in_prefix(0x0A00010F, 0x0A000108, 29), 1, "/29 inside .15");
    assert_eq_i64(ip_in_prefix(0x0A000110, 0x0A000108, 29), 0, "/29 outside .16");
    assert_eq_i64(ip_in_prefix(0x0A000107, 0x0A000108, 29), 0, "/29 outside .7");

    assert_eq_i64(ip_in_prefix(0x0A000105, 0x0A000105, 32), 1, "/32 same address");
    assert_eq_i64(ip_in_prefix(0x0A000104, 0x0A000105, 32), 0, "/32 neighbour rejected");

    return test_summary();
}
