#include "switch.h"
#include "test_util.h"

static const uint8_t MAC_A[6] = {2, 0, 0, 0, 0, 1};
static const uint8_t MAC_B[6] = {2, 0, 0, 0, 0, 2};
static const uint8_t BCAST[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};

static void mac_n(uint8_t mac[6], int n) {
    mac[0] = 2; mac[1] = 0; mac[2] = 0; mac[3] = 0; mac[4] = 0x10; mac[5] = (uint8_t)n;
}

int main(void) {
    switch_t t;
    uint8_t m[6];
    int i;

    TEST_BEGIN("S1 learn, move, flood, unicast");
    switch_init(&t);
    assert_eq_i64(switch_lookup(&t, MAC_A), -1, "unknown MAC floods (-1)");
    assert_eq_i64(switch_learn(&t, MAC_A, 0), 1, "new MAC learned -> 1");
    assert_eq_i64(switch_learn(&t, MAC_A, 0), 0, "same MAC same port -> 0 (unchanged)");
    assert_eq_i64(switch_lookup(&t, MAC_A), 0, "unicast to learned port");
    assert_eq_i64(switch_learn(&t, MAC_A, 2), 1, "MAC moved to port 2 -> 1");
    assert_eq_i64(switch_lookup(&t, MAC_A), 2, "lookup returns the new port");
    assert_eq_i64(switch_lookup(&t, MAC_B), -1, "other MAC still unknown");
    assert_eq_i64(switch_learn(&t, MAC_B, 1), 1, "second MAC learned -> 1");
    assert_eq_i64(switch_lookup(&t, MAC_B), 1, "second MAC unicast to port 1");
    assert_eq_i64(switch_lookup(&t, MAC_A), 2, "first MAC unaffected by second");
    assert_eq_i64(switch_lookup(&t, BCAST), -1, "broadcast always floods");
    assert_eq_i64(switch_learn(&t, BCAST, 3), 0, "broadcast source is never learned");
    assert_eq_i64(switch_lookup(&t, BCAST), -1, "broadcast still floods after learn try");

    TEST_BEGIN("S1b full table evicts oldest (FIFO)");
    switch_init(&t);
    for (i = 0; i < SWITCH_SIZE; i++) {
        mac_n(m, i);
        switch_learn(&t, m, i % 4);
    }
    mac_n(m, 0);
    assert_eq_i64(switch_lookup(&t, m), 0, "entry 0 present before eviction");
    mac_n(m, SWITCH_SIZE);
    assert_eq_i64(switch_learn(&t, m, 5), 1, "17th MAC learned into full table -> 1");
    assert_eq_i64(switch_lookup(&t, m), 5, "17th MAC found");
    mac_n(m, 0);
    assert_eq_i64(switch_lookup(&t, m), -1, "oldest MAC evicted");
    mac_n(m, 1);
    assert_eq_i64(switch_lookup(&t, m), 1, "second-oldest MAC kept");

    return test_summary();
}
