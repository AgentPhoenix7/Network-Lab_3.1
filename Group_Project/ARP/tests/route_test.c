#include "route.h"
#include "addr.h"
#include "test_util.h"

static uint32_t ip_of(const char *s) {
    uint32_t v = 0;
    (void)ip_parse(s, &v);
    return v;
}

int main(void) {
    uint8_t ifc = 99;
    uint32_t nh = 0;

    TEST_BEGIN("N1 longest prefix wins (/29 > /24 > /0)");
    rtable_t t = { .r = {
        { ip_of("0.0.0.0"), 0, 2, ip_of("10.0.1.254") },   /* default via gateway */
        { ip_of("10.0.1.0"), 24, 0, 0 },                    /* on-link LAN */
        { ip_of("10.0.1.16"), 29, 1, 0 },                   /* small on-link subnet */
    }, .n = 3 };
    assert_eq_i64(route_lookup(&t, ip_of("10.0.1.20"), &ifc, &nh), 0, "/29 host found");
    assert_eq_i64(ifc, 1, "/29 host uses /29 iface");
    assert_eq_i64(nh, ip_of("10.0.1.20"), "on-link next hop is dst");
    assert_eq_i64(route_lookup(&t, ip_of("10.0.1.8"), &ifc, &nh), 0, "/24 host found");
    assert_eq_i64(ifc, 0, "outside /29 falls to /24");
    assert_eq_i64(route_lookup(&t, ip_of("10.0.1.100"), &ifc, &nh), 0, "/24 host found");
    assert_eq_i64(ifc, 0, "/24 beats /0");
    assert_eq_i64(route_lookup(&t, ip_of("8.8.8.8"), &ifc, &nh), 0, "default matches");
    assert_eq_i64(ifc, 2, "default iface");
    assert_eq_i64(nh, ip_of("10.0.1.254"), "default next hop is gateway");

    TEST_BEGIN("N1 result does not depend on table order");
    rtable_t r = { .r = { t.r[2], t.r[1], t.r[0] }, .n = 3 };
    assert_eq_i64(route_lookup(&r, ip_of("10.0.1.20"), &ifc, &nh), 0, "reversed: found");
    assert_eq_i64(ifc, 1, "reversed: /29 still wins");

    TEST_BEGIN("N1 no route returns -1");
    rtable_t e = { .r = { { ip_of("10.0.1.0"), 24, 0, 0 } }, .n = 1 };
    assert_eq_i64(route_lookup(&e, ip_of("192.168.1.1"), &ifc, &nh), -1, "no match is -1");

    return test_summary();
}
