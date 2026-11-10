/* ST1 (plan 5.6, M8): station conf parser. Good host, good router, six malformed inputs. */
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <string.h>
#include "addr.h"
#include "conf.h"
#include "test_util.h"

static const char HOST_OK[] =
    "# two hosts on one bus\n"
    "node h1 host\n"
    "iface 0 lan 0 port 9600 mac 02:00:00:00:01:00 ip 10.0.0.1/24\n"
    "gateway 10.0.0.254\n"
    "arp ttl_ms 30000 retry_ms 1000 retries 3\n";

static const char ROUTER_OK[] =
    "node r1 router\n"
    "iface 0 lan 0 port 9600 mac 02:00:00:00:03:00 ip 141.23.56.1/24   # inline comment\n"
    "iface 1 lan 1 port 9601 mac 02:00:00:00:03:01 ip 141.23.56.17/29\n"
    "proxy 0 141.23.56.21/32\n"
    "route 10.0.3.0/24 via 10.0.2.2\n"
    "gateway 141.23.56.18\n"
    "arp ttl_ms 30000 retry_ms 1000 retries 3\n";

static void test_good_host(void) {
    conf_t c;
    char err[128] = "";
    TEST_BEGIN("ST1 good host conf");
    assert_eq_i64(conf_parse(HOST_OK, &c, err, sizeof err), 0, "host conf parses");
    assert_true(strcmp(c.node, "h1") == 0, "node id h1");
    assert_eq_i64(c.is_router, 0, "kind host");
    assert_eq_i64(c.nifaces, 1, "one iface");
    assert_eq_i64(c.ifaces[0].lan, 0, "iface lan");
    assert_eq_i64(c.ifaces[0].port, 9600, "iface port");
    assert_eq_mem(c.ifaces[0].mac, "\x02\x00\x00\x00\x01\x00", 6, "iface mac");
    assert_eq_i64(c.ifaces[0].ip, 0x0a000001, "iface ip 10.0.0.1");
    assert_eq_i64(c.ifaces[0].prefix, 24, "iface prefix");
    assert_eq_i64(c.has_gateway, 1, "gateway present");
    assert_eq_i64(c.gateway, 0x0a0000fe, "gateway 10.0.0.254");
    assert_eq_i64(c.ttl_ms, 30000, "arp ttl_ms");
    assert_eq_i64(c.retry_ms, 1000, "arp retry_ms");
    assert_eq_i64(c.retries, 3, "arp retries");
}

static void test_good_router(void) {
    conf_t c;
    char err[128] = "";
    TEST_BEGIN("ST1 good router conf");
    assert_eq_i64(conf_parse(ROUTER_OK, &c, err, sizeof err), 0, "router conf parses");
    assert_eq_i64(c.is_router, 1, "kind router");
    assert_eq_i64(c.nifaces, 2, "two ifaces");
    assert_eq_i64(c.ifaces[1].lan, 1, "iface 1 lan");
    assert_eq_i64(c.ifaces[1].prefix, 29, "iface 1 prefix");
    assert_eq_i64(c.ifaces[0].nproxy, 1, "one proxy range on iface 0");
    assert_eq_i64(c.ifaces[0].proxy_net[0], 0x8d173815, "proxy net 141.23.56.21");
    assert_eq_i64(c.ifaces[0].proxy_prefix[0], 32, "proxy prefix /32");
    assert_eq_i64(c.nroutes, 1, "one static route");
    assert_eq_i64(c.routes[0].net, 0x0a000300, "route net 10.0.3.0");
    assert_eq_i64(c.routes[0].prefix, 24, "route prefix /24");
    assert_eq_i64(c.routes[0].via, 0x0a000202, "route via 10.0.2.2");
    assert_eq_i64(c.gateway, 0x8d173812, "gateway 141.23.56.18");
}

/* Each malformed input must fail with a negative code and a non-empty message. */
static void expect_bad(const char *text, const char *what) {
    conf_t c;
    char err[128] = "";
    int rc = conf_parse(text, &c, err, sizeof err);
    assert_true(rc < 0 && err[0] != '\0', what);
    if (rc >= 0) fprintf(stderr, "  accepted: %s\n", what);
    else fprintf(stderr, "  %s -> %s\n", what, err);
}

static void test_malformed(void) {
    TEST_BEGIN("ST1 malformed conf");
    expect_bad("iface 0 lan 0 port 9600 mac 02:00:00:00:01:00 ip 10.0.0.1/24\n",
               "missing node line");
    expect_bad("node h1 host\niface 0 lan 0 port 9600 mac 02:00:00:00:01:zz ip 10.0.0.1/24\n",
               "bad MAC");
    expect_bad("node h1 host\niface 0 lan 0 port 9600 mac 02:00:00:00:01:00 ip 10.0.0.1/33\n",
               "bad IP prefix /33");
    expect_bad("node h1 host\nfrobnicate 3\n", "unknown keyword");
    expect_bad("node h1 host\niface 4 lan 0 port 9600 mac 02:00:00:00:01:00 ip 10.0.0.1/24\n",
               "iface index out of range");
    expect_bad("node r1 router\nproxy 0 141.23.56.21/32\n"
               "iface 0 lan 0 port 9600 mac 02:00:00:00:03:00 ip 141.23.56.1/24\n",
               "proxy before iface");
}

int main(void) {
    test_good_host();
    test_good_router();
    test_malformed();
    return test_summary();
}
