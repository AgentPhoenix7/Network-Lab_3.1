/* M3 tests: ARP cache (plan.md 5.2, rows C1-C5). */
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <string.h>
#include "test_util.h"
#include "arp_cache.h"

/* Static: the cache is large (PAYLOAD_SIZE per queued packet), keep it off the stack. */
static arp_cache_t cache;

static void reset(void) {
    memset(&cache, 0, sizeof cache);       /* ARP_FREE is 0 */
}

static void test_alloc_find(void) {
    TEST_BEGIN("C1 alloc then find by (iface, ip)");
    reset();
    assert_true(arp_cache_find(&cache, 0, 0x0A000001u) == NULL, "empty cache finds nothing");

    arp_entry_t *e = arp_cache_alloc(&cache, 0, 0x0A000001u);
    assert_true(e != NULL, "alloc returns a slot");
    if (!e) return;
    e->state = ARP_PENDING;                /* caller sets the state after alloc */
    assert_eq_i64(e->iface, 0, "alloc sets iface");
    assert_eq_i64(e->ip, 0x0A000001u, "alloc sets ip");
    assert_true(arp_cache_find(&cache, 0, 0x0A000001u) == e, "find returns the same slot");
    assert_true(arp_cache_find(&cache, 0, 0x0A000002u) == NULL, "other ip not found");
}

static void test_two_ifaces(void) {
    TEST_BEGIN("C2 same ip on iface 0 and 1 = two entries");
    reset();
    arp_entry_t *a = arp_cache_alloc(&cache, 0, 0x0A000001u);
    assert_true(a != NULL, "alloc iface 0");
    if (a) a->state = ARP_RESOLVED;
    arp_entry_t *b = arp_cache_alloc(&cache, 1, 0x0A000001u);
    assert_true(b != NULL, "alloc iface 1");
    if (b) b->state = ARP_RESOLVED;
    assert_true(a != b, "two different slots");
    assert_true(arp_cache_find(&cache, 0, 0x0A000001u) == a, "find iface 0 gives first");
    assert_true(arp_cache_find(&cache, 1, 0x0A000001u) == b, "find iface 1 gives second");
}

static void test_evict_earliest(void) {
    TEST_BEGIN("C3 full cache evicts earliest-expiring RESOLVED");
    reset();
    /* Slot i gets expires_at 1000 + ((13i+9) mod 32) * 50, a permutation, so the
     * earliest is not at index 0 or 31. (19*13+9) mod 32 == 0, so slot 19 is earliest. */
    for (int i = 0; i < ARP_CACHE_SIZE; i++) {
        arp_entry_t *e = &cache.e[i];
        e->state = ARP_RESOLVED;
        e->iface = 0;
        e->ip = 0x0A000100u + (uint32_t)i;
        e->expires_at = 1000u + (uint64_t)((13 * i + 9) % 32) * 50u;
    }
    arp_entry_t *n = arp_cache_alloc(&cache, 2, 0x0B000001u);
    assert_true(n == &cache.e[19], "alloc evicts slot 19 (earliest expires_at)");
    assert_true(arp_cache_find(&cache, 0, 0x0A000100u + 19) == NULL, "evicted entry gone");
    assert_true(arp_cache_find(&cache, 0, 0x0A000100u + 0) != NULL, "other entries kept");
}

static void test_never_evict_pending(void) {
    TEST_BEGIN("C3 never evicts PENDING");
    reset();
    for (int i = 0; i < ARP_CACHE_SIZE; i++) {
        cache.e[i].state = ARP_PENDING;
        cache.e[i].iface = 0;
        cache.e[i].ip = 0x0A000100u + (uint32_t)i;
    }
    assert_true(arp_cache_alloc(&cache, 1, 0x0B000001u) == NULL, "all PENDING: alloc is NULL");
    assert_true(arp_cache_find(&cache, 0, 0x0A000100u) == &cache.e[0], "PENDING entry kept");

    /* One RESOLVED among PENDING: that one is the only legal victim. */
    cache.e[7].state = ARP_RESOLVED;
    cache.e[7].expires_at = 5000;
    arp_entry_t *n = arp_cache_alloc(&cache, 1, 0x0B000001u);
    assert_true(n == &cache.e[7], "only the RESOLVED slot is evicted");
}

static void test_queue_limit(void) {
    TEST_BEGIN("C4 queue holds 4, 5th returns -1");
    reset();
    arp_entry_t *e = arp_cache_alloc(&cache, 0, 0x0A000001u);
    assert_true(e != NULL, "alloc");
    if (!e) return;
    e->state = ARP_PENDING;
    uint8_t pkt[20];
    memset(pkt, 0xAB, sizeof pkt);
    for (int i = 0; i < ARP_QUEUE_MAX; i++)
        assert_eq_i64(arp_cache_enqueue(e, pkt, sizeof pkt), 0, "packet queued");
    assert_eq_i64(e->qn, ARP_QUEUE_MAX, "qn is 4");
    assert_eq_i64(arp_cache_enqueue(e, pkt, sizeof pkt), -1, "5th packet refused");
}

static void test_flush(void) {
    TEST_BEGIN("C5 flush makes all FREE, find gives NULL");
    reset();
    for (int i = 0; i < ARP_CACHE_SIZE; i++) {
        cache.e[i].state = (i % 2) ? ARP_PENDING : ARP_RESOLVED;
        cache.e[i].iface = (uint8_t)(i % MAX_IFACES);
        cache.e[i].ip = 0x0A000100u + (uint32_t)i;
    }
    arp_cache_flush(&cache);
    int all_free = 1;
    for (int i = 0; i < ARP_CACHE_SIZE; i++)
        if (cache.e[i].state != ARP_FREE) all_free = 0;
    assert_true(all_free, "every slot FREE after flush");
    assert_true(arp_cache_find(&cache, 1, 0x0A000101u) == NULL, "find after flush is NULL");
    assert_true(arp_cache_find(&cache, 0, 0x0A000100u) == NULL, "find after flush is NULL (2)");
}

int main(void) {
    test_alloc_find();
    test_two_ifaces();
    test_evict_earliest();
    test_never_evict_pending();
    test_queue_limit();
    test_flush();
    return test_summary();
}
