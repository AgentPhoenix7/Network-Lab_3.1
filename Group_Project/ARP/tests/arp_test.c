/* M4 tests: ARP engine incl. proxy (plan.md 5.3, rows R1-R15). */
#define _POSIX_C_SOURCE 200809L
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "test_util.h"
#include "arp.h"

/* Two interfaces. Iface 0 = 10.0.0.0/24 (host A and B live here), iface 1 = 10.0.1.0/24. */
#define MY0_IP    0x0A000001u   /* 10.0.0.1  */
#define MY1_IP    0x0A000101u   /* 10.0.1.1  */
#define A_IP      0x0A00000Au   /* 10.0.0.10, asker on iface 0 */
#define B_IP      0x0A00000Bu   /* 10.0.0.11 */
#define OTHER_IP  0x0A00004Du   /* 10.0.0.77, in no proxy list */
#define PROXY_IP  0x0A000032u   /* 10.0.0.50, the proxied target */
#define NET_MASK  0xFFFFFF00u
#define NET0      0x0A000000u
#define NET1      0x0A000100u

static const uint8_t BCAST[6]  = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
static const uint8_t MY0_MAC[6] = {0x02, 0x00, 0x00, 0x00, 0x00, 0x01};
static const uint8_t MY1_MAC[6] = {0x02, 0x00, 0x00, 0x00, 0x01, 0x01};
static const uint8_t A_MAC[6]   = {0x02, 0x00, 0x00, 0x00, 0x00, 0x0a};
static const uint8_t A_NEW_MAC[6] = {0x02, 0x00, 0x00, 0x00, 0x00, 0x1a};
static const uint8_t B_MAC[6]   = {0x02, 0x00, 0x00, 0x00, 0x00, 0x0b};

#define REC_MAX 64

/* Recorder: every event is copied, because ev.mac points into the cache. */
typedef struct { arp_ev_t ev; uint8_t mac[6]; } rec_t;

typedef struct {
    arp_ctx_t ctx;
    rec_t rec[REC_MAX]; int nrec;
    int ovr_on; uint32_t ovr_ip; int ovr_iface;   /* one egress override (R11-R13) */
} h_t;

/* Route lookup: override first, else the /24 of each iface, else no route. */
static int h_egress(void *u, uint32_t ip) {
    const h_t *h = u;
    if (h->ovr_on && h->ovr_ip == ip) return h->ovr_iface;
    if ((ip & NET_MASK) == NET0) return 0;
    if ((ip & NET_MASK) == NET1) return 1;
    return -1;
}

static void h_event(void *u, const arp_ev_t *ev) {
    h_t *h = u;
    if (h->nrec >= REC_MAX) return;
    rec_t *r = &h->rec[h->nrec++];
    r->ev = *ev;
    memset(r->mac, 0, 6);
    if (ev->mac) memcpy(r->mac, ev->mac, 6);
    r->ev.mac = NULL;
}

/* arp_ctx_t has no init function, so the test sets every field itself. */
static void h_init(h_t *h) {
    memset(h, 0, sizeof *h);
    h->ctx.nifs = 2;
    h->ctx.ifs[0].ip = MY0_IP; h->ctx.ifs[0].prefix = 24;
    memcpy(h->ctx.ifs[0].mac, MY0_MAC, 6);
    h->ctx.ifs[1].ip = MY1_IP; h->ctx.ifs[1].prefix = 24;
    memcpy(h->ctx.ifs[1].mac, MY1_MAC, 6);
    h->ctx.ttl_ms = 30000;
    h->ctx.retry_ms = 1000;
    h->ctx.max_attempts = 3;
    h->ctx.egress = h_egress;
    h->ctx.on_event = h_event;
    h->ctx.u = h;
}

static void h_route(h_t *h, uint32_t ip, int iface) {
    h->ovr_on = 1; h->ovr_ip = ip; h->ovr_iface = iface;
}

/* Proxy list on iface 0: exactly one /32 entry, the proxied target. */
static void h_proxy(h_t *h, uint32_t ip) {
    h->ctx.ifs[0].nproxy = 1;
    h->ctx.ifs[0].proxy_net[0] = ip;
    h->ctx.ifs[0].proxy_prefix[0] = 32;
}

static int n_op(const h_t *h, arp_ev_op_t op) {
    int n = 0;
    for (int i = 0; i < h->nrec; i++) if (h->rec[i].ev.op == op) n++;
    return n;
}

static int n_type(const txq_t *q, uint8_t type) {
    int n = 0;
    for (int i = 0; i < q->n; i++) if (q->v[i].type == type) n++;
    return n;
}

/* Decodes an ARP frame's payload. 1 ok, 0 not an ARP frame. */
static int arp_of(const tx_t *t, arp_pkt_t *p) {
    return t->type == FT_ARP && arp_pkt_parse(t->payload, t->len, p) == 0;
}

/* 1 if t is a broadcast request out of iface with the given target, sent from that iface's MAC/IP. */
static int is_bcast_req(const h_t *h, const tx_t *t, uint32_t tpa) {
    arp_pkt_t p;
    memset(&p, 0, sizeof p);
    if (!arp_of(t, &p) || memcmp(t->dst, BCAST, 6) != 0) return 0;
    return p.oper == ARP_REQUEST && p.tpa == tpa &&
           p.spa == h->ctx.ifs[t->iface].ip &&
           memcmp(p.sha, h->ctx.ifs[t->iface].mac, 6) == 0;
}

/* 1 if t is an IPv4 frame to dst carrying exactly the n bytes of pkt. */
static int is_ip_frame(const tx_t *t, uint8_t iface, const uint8_t dst[6],
                       const uint8_t *pkt, size_t n) {
    return t->type == FT_IPV4 && t->iface == iface && memcmp(t->dst, dst, 6) == 0 &&
           t->len == n && memcmp(t->payload, pkt, n) == 0;
}

static const arp_entry_t *ent(h_t *h, uint8_t iface, uint32_t ip) {
    return arp_cache_find(&h->ctx.cache, iface, ip);
}

/* 1 if the entry is gone: freed, or never created. */
static int is_gone(h_t *h, uint8_t iface, uint32_t ip) {
    const arp_entry_t *e = ent(h, iface, ip);
    return e == NULL || e->state == ARP_FREE;
}

/* Feeds an ARP request from sha/spa asking for tpa into iface. */
static void req_from(h_t *h, uint8_t iface, const uint8_t sha[6], uint32_t spa,
                     uint32_t tpa, uint64_t now, txq_t *out) {
    arp_pkt_t p;
    arp_pkt_request(&p, sha, spa, tpa);
    arp_input(&h->ctx, iface, (const uint8_t *)&p, sizeof p, now, out);
}

/* Feeds an ARP reply from sha/spa to tpa (tha = our MAC on iface) into iface. */
static void reply_from(h_t *h, uint8_t iface, const uint8_t sha[6], uint32_t spa,
                       uint32_t tpa, uint64_t now, txq_t *out) {
    arp_pkt_t p;
    arp_pkt_reply(&p, sha, spa, h->ctx.ifs[iface].mac, tpa);
    arp_input(&h->ctx, iface, (const uint8_t *)&p, sizeof p, now, out);
}

/* Deterministic IP-looking bytes; seed tells packets apart. */
static void ip_fill(uint8_t *b, size_t n, uint8_t seed) {
    for (size_t i = 0; i < n; i++) b[i] = (uint8_t)(seed + i);
}

#define PKT_LEN 20

static void test_r1(void) {
    TEST_BEGIN("R1");
    h_t h; h_init(&h);
    txq_t out; out.n = 0;
    uint8_t pkt[PKT_LEN]; ip_fill(pkt, PKT_LEN, 1);

    int rc = arp_output(&h.ctx, 0, A_IP, pkt, PKT_LEN, 0, &out);
    assert_eq_i64(rc, 1, "R1 arp_output returns 1 (queued)");
    assert_eq_i64(out.n, 1, "R1 exactly one frame: the broadcast request");
    assert_true(out.n == 1 && is_bcast_req(&h, &out.v[0], A_IP),
                "R1 request is broadcast on iface 0, asks for A, from my MAC/IP");

    const arp_entry_t *e = ent(&h, 0, A_IP);
    assert_true(e != NULL && e->state == ARP_PENDING, "R1 entry is PENDING");
    assert_true(e != NULL && e->qn == 1, "R1 packet is queued on the entry");
    assert_true(e != NULL && e->attempts == 1, "R1 attempts = 1");
    assert_eq_i64(n_op(&h, AEV_PENDING), 1, "R1 one AEV_PENDING");
    assert_eq_i64(n_op(&h, AEV_QUEUED), 1, "R1 one AEV_QUEUED");
}

static void test_r2(void) {
    TEST_BEGIN("R2");
    h_t h; h_init(&h);
    txq_t out; out.n = 0;
    uint8_t p1[PKT_LEN], p2[PKT_LEN];
    ip_fill(p1, PKT_LEN, 1); ip_fill(p2, PKT_LEN, 50);
    arp_output(&h.ctx, 0, A_IP, p1, PKT_LEN, 0, &out);

    out.n = 0;
    int rc = arp_output(&h.ctx, 0, A_IP, p2, PKT_LEN, 0, &out);
    assert_eq_i64(rc, 1, "R2 second arp_output returns 1 (queued)");
    assert_eq_i64(out.n, 0, "R2 no new frame: no second request");
    assert_eq_i64(n_op(&h, AEV_PENDING), 1, "R2 still one AEV_PENDING");
    const arp_entry_t *e = ent(&h, 0, A_IP);
    assert_true(e != NULL && e->qn == 2, "R2 entry now holds 2 packets");
    assert_eq_i64(n_op(&h, AEV_QUEUED), 2, "R2 two AEV_QUEUED events");
}

static void test_r3(void) {
    TEST_BEGIN("R3");
    h_t h; h_init(&h);
    txq_t out; out.n = 0;
    uint8_t p1[PKT_LEN], p2[PKT_LEN];
    ip_fill(p1, PKT_LEN, 1); ip_fill(p2, PKT_LEN, 50);
    arp_output(&h.ctx, 0, A_IP, p1, PKT_LEN, 0, &out);
    arp_output(&h.ctx, 0, A_IP, p2, PKT_LEN, 0, &out);

    out.n = 0;
    reply_from(&h, 0, A_MAC, A_IP, MY0_IP, 100, &out);

    const arp_entry_t *e = ent(&h, 0, A_IP);
    assert_true(e != NULL && e->state == ARP_RESOLVED, "R3 entry is RESOLVED");
    assert_true(e != NULL && memcmp(e->mac, A_MAC, 6) == 0, "R3 entry learned A's MAC");
    assert_true(e != NULL && e->qn == 0, "R3 queue flushed");
    assert_eq_i64(n_op(&h, AEV_RESOLVED), 1, "R3 one AEV_RESOLVED");

    assert_eq_i64(out.n, 2, "R3 exactly the two queued packets are sent");
    assert_true(out.n == 2 && is_ip_frame(&out.v[0], 0, A_MAC, p1, PKT_LEN),
                "R3 first flushed frame is p1, to A's MAC, FT_IPV4");
    assert_true(out.n == 2 && is_ip_frame(&out.v[1], 0, A_MAC, p2, PKT_LEN),
                "R3 second flushed frame is p2, in order, to A's MAC");
}

static void test_r4(void) {
    TEST_BEGIN("R4");
    h_t h; h_init(&h);
    txq_t out; out.n = 0;
    reply_from(&h, 0, A_MAC, A_IP, MY0_IP, 0, &out);   /* learn A */

    uint8_t pkt[PKT_LEN]; ip_fill(pkt, PKT_LEN, 7);
    out.n = 0;
    h.nrec = 0;
    int rc = arp_output(&h.ctx, 0, A_IP, pkt, PKT_LEN, 10, &out);
    assert_eq_i64(rc, 0, "R4 arp_output returns 0 (sent now)");
    assert_eq_i64(out.n, 1, "R4 one frame");
    assert_true(out.n == 1 && is_ip_frame(&out.v[0], 0, A_MAC, pkt, PKT_LEN),
                "R4 FT_IPV4 frame straight to A's MAC");
    assert_eq_i64(n_type(&out, FT_ARP), 0, "R4 no ARP frame sent");
    assert_eq_i64(n_op(&h, AEV_PENDING), 0, "R4 no AEV_PENDING");
    const arp_entry_t *e = ent(&h, 0, A_IP);
    assert_true(e != NULL && e->expires_at == 30000, "R4 using an entry does not refresh its TTL");
}

static void test_r5(void) {
    TEST_BEGIN("R5");
    h_t h; h_init(&h);
    txq_t out; out.n = 0;
    uint8_t pkt[PKT_LEN]; ip_fill(pkt, PKT_LEN, 1);
    arp_output(&h.ctx, 0, A_IP, pkt, PKT_LEN, 0, &out);   /* request at t=0, retry_at=1000 */

    out.n = 0;
    arp_tick(&h.ctx, 999, &out);
    assert_eq_i64(out.n, 0, "R5 no retry before retry_ms");
    assert_eq_i64(ent(&h, 0, A_IP)->attempts, 1, "R5 attempts still 1 at 999");

    arp_tick(&h.ctx, 1000, &out);
    assert_eq_i64(out.n, 1, "R5 retry at retry_ms sends one frame");
    assert_true(out.n == 1 && is_bcast_req(&h, &out.v[0], A_IP), "R5 retry is a broadcast request for A");
    assert_eq_i64(ent(&h, 0, A_IP)->attempts, 2, "R5 attempts grew to 2");
    assert_eq_i64(n_op(&h, AEV_RETRY), 1, "R5 one AEV_RETRY");
    assert_true(arp_next_deadline(&h.ctx) == 2000, "R5 next retry at 1000 + retry_ms");

    arp_tick(&h.ctx, 2000, &out);
    assert_eq_i64(ent(&h, 0, A_IP)->attempts, 3, "R5 attempts grew to 3");
}

static void test_r6(void) {
    TEST_BEGIN("R6");
    h_t h; h_init(&h);
    txq_t out; out.n = 0;
    uint8_t pkt[PKT_LEN]; ip_fill(pkt, PKT_LEN, 1);
    arp_output(&h.ctx, 0, A_IP, pkt, PKT_LEN, 0, &out);   /* request 1 at t=0 */
    arp_tick(&h.ctx, 1000, &out);                          /* request 2 */
    arp_tick(&h.ctx, 2000, &out);                          /* request 3 = max_attempts */

    out.n = 0;
    h.nrec = 0;
    arp_tick(&h.ctx, 3000, &out);
    assert_eq_i64(out.n, 0, "R6 failure sends nothing, the queue is dropped");
    assert_eq_i64(n_op(&h, AEV_FAIL), 1, "R6 one AEV_FAIL");
    assert_true(is_gone(&h, 0, A_IP), "R6 entry is FREE after failure");

    /* Total requests: 1 initial + 2 retries = max_attempts. */
    assert_eq_i64(n_op(&h, AEV_RETRY), 0, "R6 no retry event after the failure");

    out.n = 0;
    reply_from(&h, 0, A_MAC, A_IP, MY0_IP, 3500, &out);
    assert_eq_i64(n_type(&out, FT_IPV4), 0, "R6 late reply does not send the dropped packet");
}

static void test_r6b(void) {
    TEST_BEGIN("R6b");
    h_t h; h_init(&h);
    h.ctx.max_attempts = 0;                               /* 0 = retry forever */
    txq_t out; out.n = 0;
    uint8_t pkt[PKT_LEN]; ip_fill(pkt, PKT_LEN, 1);
    arp_output(&h.ctx, 0, A_IP, pkt, PKT_LEN, 0, &out);
    int retries = 0, fails = 0;
    for (int i = 1; i <= 400; i++) {                      /* the recorder holds 64 events, so count per tick */
        h.nrec = 0;
        arp_tick(&h.ctx, (uint64_t)i * 1000, &out);
        retries += n_op(&h, AEV_RETRY);
        fails += n_op(&h, AEV_FAIL);
    }
    assert_eq_i64(fails, 0, "R6b never fails");
    assert_true(ent(&h, 0, A_IP) != NULL && ent(&h, 0, A_IP)->state == ARP_PENDING, "R6b still PENDING after 400 retries");
    assert_eq_i64(retries, 400, "R6b kept retrying every second");
    out.n = 0;
    reply_from(&h, 0, A_MAC, A_IP, MY0_IP, 401000, &out);
    assert_eq_i64(n_type(&out, FT_IPV4), 1, "R6b a late reply still sends the parked packet");
}

static void test_r7(void) {
    TEST_BEGIN("R7");
    h_t h; h_init(&h);
    txq_t out; out.n = 0;
    reply_from(&h, 0, A_MAC, A_IP, MY0_IP, 0, &out);   /* learned at t=0, expires at 30000 */
    assert_true(ent(&h, 0, A_IP) != NULL && ent(&h, 0, A_IP)->state == ARP_RESOLVED,
                "R7 precondition: A is RESOLVED");

    h.nrec = 0;
    arp_tick(&h.ctx, 29999, &out);
    assert_eq_i64(n_op(&h, AEV_EXPIRE), 0, "R7 no expiry one ms early");
    assert_true(ent(&h, 0, A_IP) != NULL && ent(&h, 0, A_IP)->state == ARP_RESOLVED,
                "R7 still RESOLVED at 29999");

    arp_tick(&h.ctx, 30000, &out);
    assert_eq_i64(n_op(&h, AEV_EXPIRE), 1, "R7 one AEV_EXPIRE at ttl");
    assert_true(is_gone(&h, 0, A_IP), "R7 entry is FREE after expiry");
}

static void test_r8(void) {
    TEST_BEGIN("R8");
    h_t h; h_init(&h);
    txq_t out; out.n = 0;
    req_from(&h, 0, A_MAC, A_IP, MY0_IP, 500, &out);

    assert_eq_i64(out.n, 1, "R8 exactly one reply frame");
    arp_pkt_t p; memset(&p, 0, sizeof p);
    assert_true(out.n == 1 && arp_of(&out.v[0], &p), "R8 reply payload parses as ARP");
    assert_true(out.n == 1 && out.v[0].iface == 0, "R8 reply leaves iface 0");
    assert_true(out.n == 1 && memcmp(out.v[0].dst, A_MAC, 6) == 0, "R8 reply unicast to asker's MAC");
    assert_eq_i64(p.oper, ARP_REPLY, "R8 oper = REPLY");
    assert_true(memcmp(p.sha, MY0_MAC, 6) == 0, "R8 sha = my MAC");
    assert_true(p.spa == MY0_IP, "R8 spa = my IP");
    assert_true(memcmp(p.tha, A_MAC, 6) == 0, "R8 tha = asker MAC");
    assert_true(p.tpa == A_IP, "R8 tpa = asker IP");

    assert_eq_i64(n_op(&h, AEV_REPLY_SENT), 1, "R8 one AEV_REPLY_SENT");
    assert_eq_i64(n_op(&h, AEV_LEARN), 1, "R8 asker learned (AEV_LEARN)");
    const arp_entry_t *e = ent(&h, 0, A_IP);
    assert_true(e != NULL && e->state == ARP_RESOLVED && memcmp(e->mac, A_MAC, 6) == 0,
                "R8 asker cached as RESOLVED with its MAC");
}

static void test_r9(void) {
    TEST_BEGIN("R9");
    h_t h; h_init(&h);
    txq_t out; out.n = 0;
    req_from(&h, 0, A_MAC, A_IP, B_IP, 0, &out);   /* asks for B, not me, no proxy */

    assert_eq_i64(out.n, 0, "R9 no reply sent");
    assert_true(is_gone(&h, 0, A_IP), "R9 unknown asker is not learned");
    assert_eq_i64(n_op(&h, AEV_LEARN), 0, "R9 no AEV_LEARN");
    assert_eq_i64(n_op(&h, AEV_REPLY_SENT), 0, "R9 no AEV_REPLY_SENT");
}

static void test_r10(void) {
    TEST_BEGIN("R10");
    h_t h; h_init(&h);
    txq_t out; out.n = 0;
    reply_from(&h, 0, A_MAC, A_IP, MY0_IP, 0, &out);   /* A known, expires at 30000 */

    h.nrec = 0; out.n = 0;
    req_from(&h, 0, A_NEW_MAC, A_IP, B_IP, 20000, &out);   /* A asks for B, not me */

    assert_eq_i64(out.n, 0, "R10 no reply (request not for me)");
    const arp_entry_t *e = ent(&h, 0, A_IP);
    assert_true(e != NULL && memcmp(e->mac, A_NEW_MAC, 6) == 0, "R10 MAC refreshed to the new sha");
    assert_true(e != NULL && e->expires_at == 50000, "R10 expires_at advanced to now + ttl");
    assert_eq_i64(n_op(&h, AEV_LEARN), 1, "R10 merge reported as AEV_LEARN");
}

static void test_r11(void) {
    TEST_BEGIN("R11");
    h_t h; h_init(&h);
    h_proxy(&h, PROXY_IP);
    h_route(&h, PROXY_IP, 1);                    /* the proxied host is behind iface 1 */
    txq_t out; out.n = 0;
    req_from(&h, 0, A_MAC, A_IP, PROXY_IP, 0, &out);

    assert_eq_i64(out.n, 1, "R11 exactly one proxy reply frame");
    arp_pkt_t p; memset(&p, 0, sizeof p);
    assert_true(out.n == 1 && arp_of(&out.v[0], &p), "R11 reply payload parses as ARP");
    assert_true(out.n == 1 && memcmp(out.v[0].dst, A_MAC, 6) == 0, "R11 reply unicast to asker");
    assert_eq_i64(p.oper, ARP_REPLY, "R11 oper = REPLY");
    assert_true(memcmp(p.sha, MY0_MAC, 6) == 0, "R11 sha = my MAC (iface 0)");
    assert_true(p.spa == PROXY_IP, "R11 spa = proxied target");
    assert_true(memcmp(p.tha, A_MAC, 6) == 0, "R11 tha = asker MAC");
    assert_true(p.tpa == A_IP, "R11 tpa = asker IP");
    assert_eq_i64(n_op(&h, AEV_PROXY_REPLY), 1, "R11 one AEV_PROXY_REPLY");
    assert_eq_i64(n_op(&h, AEV_REPLY_SENT), 0, "R11 not counted as a reply for my own IP");
}

static void test_r12(void) {
    TEST_BEGIN("R12");
    h_t h; h_init(&h);
    h_proxy(&h, PROXY_IP);
    h_route(&h, PROXY_IP, 0);                    /* egress == asking iface */
    txq_t out; out.n = 0;
    req_from(&h, 0, A_MAC, A_IP, PROXY_IP, 0, &out);

    assert_eq_i64(out.n, 0, "R12 no proxy reply when the host is on the asking segment");
    assert_eq_i64(n_op(&h, AEV_PROXY_REPLY), 0, "R12 no AEV_PROXY_REPLY");
}

static void test_r13(void) {
    TEST_BEGIN("R13");
    h_t h; h_init(&h);
    h_proxy(&h, PROXY_IP);
    h_route(&h, OTHER_IP, 1);                    /* routable elsewhere, but not in the list */
    txq_t out; out.n = 0;
    req_from(&h, 0, A_MAC, A_IP, OTHER_IP, 0, &out);

    assert_eq_i64(out.n, 0, "R13 no reply for a target outside the proxy list");
    assert_eq_i64(n_op(&h, AEV_PROXY_REPLY), 0, "R13 no AEV_PROXY_REPLY");
}

static void test_r14(void) {
    TEST_BEGIN("R14");
    h_t h; h_init(&h);
    txq_t out; out.n = 0;
    reply_from(&h, 0, B_MAC, B_IP, MY0_IP, 0, &out);   /* no request was ever sent */

    const arp_entry_t *e = ent(&h, 0, B_IP);
    assert_true(e != NULL && e->state == ARP_RESOLVED, "R14 unsolicited reply is learned as RESOLVED");
    assert_true(e != NULL && memcmp(e->mac, B_MAC, 6) == 0, "R14 learned B's MAC");
    assert_eq_i64(n_op(&h, AEV_LEARN), 1, "R14 one AEV_LEARN");
    assert_eq_i64(out.n, 0, "R14 a reply is never answered");
}

static void test_r15(void) {
    TEST_BEGIN("R15");
    h_t h; h_init(&h);
    txq_t out; out.n = 0;
    uint8_t pkt[PKT_LEN]; ip_fill(pkt, PKT_LEN, 1);

    assert_true(arp_next_deadline(&h.ctx) == UINT64_MAX, "R15 empty cache: UINT64_MAX");

    arp_output(&h.ctx, 0, A_IP, pkt, PKT_LEN, 5000, &out);   /* PENDING, retry_at 6000 */
    assert_true(arp_next_deadline(&h.ctx) == 6000, "R15 PENDING: deadline = retry_at");

    /* RESOLVED: fresh context, learned at t=0, expires at 30000. */
    h_t r; h_init(&r);
    reply_from(&r, 0, A_MAC, A_IP, MY0_IP, 0, &out);
    assert_true(arp_next_deadline(&r.ctx) == 30000, "R15 RESOLVED: deadline = expires_at");

    /* Mixed: the earlier PENDING retry wins over the later RESOLVED expiry. */
    reply_from(&h, 0, B_MAC, B_IP, MY0_IP, 0, &out);
    assert_true(arp_next_deadline(&h.ctx) == 6000, "R15 mixed: earliest of retry_at and expires_at");
}

int main(void) {
    test_r1();
    test_r2();
    test_r3();
    test_r4();
    test_r5();
    test_r6();
    test_r6b();
    test_r7();
    test_r8();
    test_r9();
    test_r10();
    test_r11();
    test_r12();
    test_r13();
    test_r14();
    test_r15();
    return test_summary();
}
