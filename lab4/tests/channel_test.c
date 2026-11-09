#define _POSIX_C_SOURCE 200809L
#include "test_util.h"
#include "../channel.h"
#include "../protocol.h"
#include "../cdma.h"
#include "utils.h"

#include <arpa/inet.h>
#include <fcntl.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

/* Every test forks scripted mock clients (children) and runs
 * channel_serve() in this process. Children verify what they receive and
 * report through their exit status; the parent asserts every child exited 0.
 * Port for CHk is 9400 + 7*k (CH8 uses four consecutive ports). */

#define MAX_N 64
#define MOCK_TIMEOUT_S 5
#define PORT(k) ((uint16_t)(9400 + 7 * (k)))

static int16_t g_zeros[CDMA_FRAME_CHIPS(MAX_N)];

typedef struct {
    snd_kind_t     kind;
    uint32_t       round;    /* value put on the wire (normally the step index) */
    const int8_t  *chips;    /* used when kind == SND_DATA */
} snd_step_t;

typedef struct {
    int                   nrounds;  /* rounds expected before should_stop; -1 = drain until EOF */
    const int16_t *const *sum;      /* [nrounds] expected sums, host order */
    const int            *active;   /* [nrounds] expected `active` counts */
} rcv_expect_t;

typedef struct {
    int                 role;           /* raw hello byte (7 is allowed on purpose) */
    int                 id;
    int                 start_delay_ms;
    int                 expect_reject;  /* config never arrives: exit 0 when it doesn't */
    int                 expect_n, expect_L; /* checked against config when expect_n != 0 */
    int                 nsteps;         /* sender script */
    const snd_step_t   *steps;
    const rcv_expect_t *expect;         /* receiver script */
} mock_t;

static mock_t mk_sender(int id, const snd_step_t *steps, int nsteps, int n, int L) {
    mock_t m;
    memset(&m, 0, sizeof m);
    m.role = ROLE_SENDER; m.id = id; m.steps = steps; m.nsteps = nsteps;
    m.expect_n = n; m.expect_L = L;
    return m;
}

static mock_t mk_receiver(int id, const rcv_expect_t *ex, int n, int L) {
    mock_t m;
    memset(&m, 0, sizeof m);
    m.role = ROLE_RECEIVER; m.id = id; m.expect = ex;
    m.expect_n = n; m.expect_L = L;
    return m;
}

static void sleep_ms(int ms) {
    struct timespec ts = { ms / 1000, (long)(ms % 1000) * 1000000L };
    nanosleep(&ts, NULL);
}

/* tcp_connect perror()s on every refused attempt; mute stderr while the
 * server isn't listening yet. */
static int connect_retry(uint16_t port) {
    int saved = dup(2);
    int devnull = open("/dev/null", O_WRONLY);
    if (devnull >= 0) dup2(devnull, 2);
    int fd = -1;
    for (int i = 0; i < 400 && fd < 0; i++) {
        fd = tcp_connect("127.0.0.1", port);
        if (fd < 0) sleep_ms(5);
    }
    if (saved >= 0) { dup2(saved, 2); close(saved); }
    if (devnull >= 0) close(devnull);
    return fd;
}

static void mock_sender_script(int fd, const mock_t *m, size_t fc) {
    for (int s = 0; s < m->nsteps; s++) {
        const snd_step_t *st = &m->steps[s];
        snd_hdr_t hd;
        memset(&hd, 0, sizeof hd);
        hd.round = htonl(st->round);
        hd.kind = (uint8_t)st->kind;
        if (send_all(fd, &hd, sizeof hd) < 0) return;
        if (st->kind == SND_DATA && send_all(fd, st->chips, fc) < 0) return;
    }
}

static void mock_receiver_expect(int fd, const mock_t *m, size_t fc) {
    const rcv_expect_t *ex = m->expect;
    if (ex->nrounds < 0) {
        char c;
        while (recv_all(fd, &c, 1) > 0) { }
        return;
    }
    uint16_t *buf = malloc(fc * sizeof(uint16_t));
    if (!buf) _exit(9);
    for (int r = 0; r < ex->nrounds; r++) {
        rcv_hdr_t hd;
        if (recv_all(fd, &hd, sizeof hd) != (ssize_t)sizeof hd) _exit(10);
        if (hd.should_stop) _exit(11);
        if (ntohl(hd.round) != (uint32_t)r) _exit(12);
        if (hd.active != ex->active[r]) _exit(13);
        if (recv_all(fd, buf, fc * sizeof(uint16_t)) != (ssize_t)(fc * sizeof(uint16_t))) _exit(14);
        for (size_t k = 0; k < fc; k++) {
            if ((int16_t)ntohs(buf[k]) != ex->sum[r][k]) _exit(15);
        }
    }
    rcv_hdr_t stop;
    if (recv_all(fd, &stop, sizeof stop) != (ssize_t)sizeof stop) _exit(16);
    if (!stop.should_stop) _exit(17);
    if (ntohl(stop.round) != (uint32_t)ex->nrounds) _exit(18);
    free(buf);
}

static void run_mock(uint16_t port, const mock_t *m) {
    signal(SIGPIPE, SIG_IGN);
    alarm(MOCK_TIMEOUT_S);
    sleep_ms(m->start_delay_ms);

    int fd = connect_retry(port);
    if (fd < 0) _exit(1);

    hello_msg_t h;
    memset(&h, 0, sizeof h);
    h.role = (uint8_t)m->role;
    h.id = (uint8_t)m->id;
    if (send_all(fd, &h, sizeof h) != (ssize_t)sizeof h) _exit(m->expect_reject ? 0 : 1);

    config_msg_t cfg;
    if (recv_all(fd, &cfg, sizeof cfg) != (ssize_t)sizeof cfg) _exit(m->expect_reject ? 0 : 2);
    if (m->expect_reject) _exit(3);
    if (m->expect_n && (ntohl(cfg.n) != (uint32_t)m->expect_n ||
                        ntohl(cfg.L) != (uint32_t)m->expect_L)) _exit(4);
    if (cfg.code_index != m->id) _exit(5);

    size_t fc = CDMA_FRAME_CHIPS(ntohl(cfg.L));
    if (m->role == ROLE_SENDER) mock_sender_script(fd, m, fc);
    else                        mock_receiver_expect(fd, m, fc);

    close_conn(fd);
    _exit(0);
}

/* Forks every mock, runs channel_serve here, reaps. Returns channel_serve's
 * result; *bad = number of mocks that did not exit 0. */
static int run_scenario(uint16_t port, int n, uint32_t max_rounds,
                        const mock_t *mocks, int nmocks,
                        channel_metrics_t *m, int *bad) {
    pid_t pids[2 * MAX_N];
    for (int i = 0; i < nmocks; i++) {
        pids[i] = fork();
        if (pids[i] == 0) run_mock(port, &mocks[i]);
    }

    channel_params_t p = { .port = port, .n = n, .max_rounds = max_rounds };
    memset(m, 0, sizeof *m);
    alarm(2 * MOCK_TIMEOUT_S);
    int rc = channel_serve(&p, m);
    alarm(0);

    int failed = 0;
    for (int i = 0; i < nmocks; i++) {
        int st = 0;
        waitpid(pids[i], &st, 0);
        if (!WIFEXITED(st) || WEXITSTATUS(st) != 0) {
            failed++;
            fprintf(stderr, "  mock %d (role %d id %d): %s %d\n", i, mocks[i].role, mocks[i].id,
                    WIFEXITED(st) ? "exit" : "signal",
                    WIFEXITED(st) ? WEXITSTATUS(st) : WTERMSIG(st));
        }
    }
    *bad = failed;
    return rc;
}

static int8_t *rand_chips(size_t fc, unsigned seed) {
    int8_t *c = malloc(fc);
    srand(seed);
    for (size_t k = 0; k < fc; k++) c[k] = (rand() & 1) ? 1 : -1;
    return c;
}

static int8_t *const_chips(size_t fc, int8_t v) {
    int8_t *c = malloc(fc);
    memset(c, v, fc);
    return c;
}

/* CH1 */
static void test_handshake_any_order(void) {
    TEST_BEGIN("CH1 handshake in any order");
    const int n = 2, L = 2;
    snd_step_t done[1] = { { SND_DONE, 0, NULL } };
    const int16_t *sums[1] = { g_zeros };
    int active[1] = { 0 };
    rcv_expect_t ex = { 1, sums, active };

    mock_t mocks[4] = {
        mk_receiver(1, &ex, n, L), mk_sender(0, done, 1, n, L),
        mk_receiver(0, &ex, n, L), mk_sender(1, done, 1, n, L),
    };
    for (int i = 0; i < 4; i++) mocks[i].start_delay_ms = 30 * i; /* connect order R1,S0,R0,S1 */

    channel_metrics_t m; int bad;
    int rc = run_scenario(PORT(1), n, 0, mocks, 4, &m, &bad);
    assert_true(rc == 0, "channel_serve returns 0");
    assert_eq_i64(bad, 0, "all mocks got config{n=2,L=2,code_index=id} and exited 0");
    assert_eq_i64(m.n, 2, "metrics n == 2");
    assert_eq_i64(m.L, 2, "metrics L == 2");
}

/* CH2 */
static void test_n_not_power_of_2(void) {
    TEST_BEGIN("CH2 n=3 gives L=4");
    const int n = 3, L = 4;
    snd_step_t done[1] = { { SND_DONE, 0, NULL } };
    const int16_t *sums[1] = { g_zeros };
    int active[1] = { 0 };
    rcv_expect_t ex = { 1, sums, active };

    mock_t mocks[6];
    for (int i = 0; i < n; i++) {
        mocks[i] = mk_sender(i, done, 1, n, L);
        mocks[n + i] = mk_receiver(i, &ex, n, L);
    }
    channel_metrics_t m; int bad;
    int rc = run_scenario(PORT(2), n, 0, mocks, 6, &m, &bad);
    assert_true(rc == 0, "channel_serve returns 0");
    assert_eq_i64(bad, 0, "all mocks saw L == 4");
    assert_eq_i64(m.L, 4, "metrics L == 4");
}

/* CH3 */
static void test_one_round_sum(void) {
    TEST_BEGIN("CH3 one round sum");
    const int n = 2, L = 2;
    size_t fc = CDMA_FRAME_CHIPS(L);
    int8_t *A = rand_chips(fc, 31), *B = rand_chips(fc, 32);
    const int8_t *ptrs[2] = { A, B };
    int16_t *want = malloc(fc * sizeof(int16_t));
    cdma_sum(ptrs, 2, fc, want);

    snd_step_t s0[1] = { { SND_DATA, 0, A } };
    snd_step_t s1[1] = { { SND_DATA, 0, B } };
    const int16_t *sums[1] = { want };
    int active[1] = { 2 };
    rcv_expect_t ex = { 1, sums, active };
    mock_t mocks[4] = {
        mk_sender(0, s0, 1, n, L), mk_sender(1, s1, 1, n, L),
        mk_receiver(0, &ex, n, L), mk_receiver(1, &ex, n, L),
    };
    channel_metrics_t m; int bad;
    int rc = run_scenario(PORT(3), n, 1, mocks, 4, &m, &bad);
    assert_true(rc == 0, "channel_serve returns 0");
    assert_eq_i64(bad, 0, "both receivers got round=0, active=2, sum == A+B");
    assert_eq_i64(m.rounds, 1, "rounds == 1");
    free(A); free(B); free(want);
}

/* CH4 */
static void test_idle_contributes_zero(void) {
    TEST_BEGIN("CH4 IDLE contributes 0");
    const int n = 2, L = 2;
    size_t fc = CDMA_FRAME_CHIPS(L);
    int8_t *A = rand_chips(fc, 41);
    int16_t *want = malloc(fc * sizeof(int16_t));
    for (size_t k = 0; k < fc; k++) want[k] = A[k];

    snd_step_t s0[1] = { { SND_DATA, 0, A } };
    snd_step_t s1[1] = { { SND_IDLE, 0, NULL } };
    const int16_t *sums[1] = { want };
    int active[1] = { 1 };
    rcv_expect_t ex = { 1, sums, active };
    mock_t mocks[4] = {
        mk_sender(0, s0, 1, n, L), mk_sender(1, s1, 1, n, L),
        mk_receiver(0, &ex, n, L), mk_receiver(1, &ex, n, L),
    };
    channel_metrics_t m; int bad;
    int rc = run_scenario(PORT(4), n, 1, mocks, 4, &m, &bad);
    assert_true(rc == 0, "channel_serve returns 0");
    assert_eq_i64(bad, 0, "sum == A and active == 1");
    assert_eq_i64(m.data_sends, 1, "data_sends == 1");
    assert_eq_i64(m.idle_sends, 1, "idle_sends == 1");
    free(A); free(want);
}

/* CH5 */
static void test_done_contributes_zero_forever(void) {
    TEST_BEGIN("CH5 DONE contributes 0 from then on");
    const int n = 2, L = 2;
    size_t fc = CDMA_FRAME_CHIPS(L);
    int8_t *A[4];
    int16_t *want[4];
    int8_t *B = rand_chips(fc, 59);
    snd_step_t s0[4];
    for (int r = 0; r < 4; r++) {
        A[r] = rand_chips(fc, 50 + r);
        s0[r] = (snd_step_t){ SND_DATA, (uint32_t)r, A[r] };
        want[r] = malloc(fc * sizeof(int16_t));
        for (size_t k = 0; k < fc; k++) want[r][k] = (int16_t)(A[r][k] + (r == 0 ? B[k] : 0));
    }
    snd_step_t s1[2] = { { SND_DATA, 0, B }, { SND_DONE, 1, NULL } };

    const int16_t *sums[4] = { want[0], want[1], want[2], want[3] };
    int active[4] = { 2, 1, 1, 1 };
    rcv_expect_t ex = { 4, sums, active };
    mock_t mocks[4] = {
        mk_sender(0, s0, 4, n, L), mk_sender(1, s1, 2, n, L),
        mk_receiver(0, &ex, n, L), mk_receiver(1, &ex, n, L),
    };
    channel_metrics_t m; int bad;
    int rc = run_scenario(PORT(5), n, 4, mocks, 4, &m, &bad);
    assert_true(rc == 0, "channel_serve returns 0 (never reads S1 after DONE)");
    assert_eq_i64(bad, 0, "round 0 = A0+B, rounds 1..3 = A only, active 2,1,1,1");
    assert_eq_i64(m.rounds, 4, "rounds == 4");
    for (int r = 0; r < 4; r++) { free(A[r]); free(want[r]); }
    free(B);
}

/* CH6 */
static void test_termination_all_done(void) {
    TEST_BEGIN("CH6 termination when every sender is DONE");
    const int n = 2, L = 2;
    size_t fc = CDMA_FRAME_CHIPS(L);
    int8_t *A = rand_chips(fc, 61);
    int16_t *want = malloc(fc * sizeof(int16_t));
    for (size_t k = 0; k < fc; k++) want[k] = A[k];

    snd_step_t s0[2] = { { SND_DATA, 0, A }, { SND_DONE, 1, NULL } };
    snd_step_t s1[2] = { { SND_IDLE, 0, NULL }, { SND_DONE, 1, NULL } };
    const int16_t *sums[2] = { want, g_zeros };
    int active[2] = { 1, 0 };
    rcv_expect_t ex = { 2, sums, active };
    mock_t mocks[4] = {
        mk_sender(0, s0, 2, n, L), mk_sender(1, s1, 2, n, L),
        mk_receiver(0, &ex, n, L), mk_receiver(1, &ex, n, L),
    };
    channel_metrics_t m; int bad;
    int rc = run_scenario(PORT(6), n, 0, mocks, 4, &m, &bad);
    assert_true(rc == 0, "channel_serve returns 0");
    assert_eq_i64(bad, 0, "round 0 = A (active 1), round 1 = zeros (active 0), then should_stop with round=2");
    assert_eq_i64(m.rounds, 2, "rounds == 2 (the all-DONE round is still broadcast)");
    assert_eq_i64(m.data_sends, 1, "data_sends == 1");
    assert_eq_i64(m.idle_sends, 1, "idle_sends == 1 (DONE is not IDLE)");
    free(A); free(want);
}

/* CH7 */
static void test_desync_guard(void) {
    TEST_BEGIN("CH7 desync guard");
    const int n = 2, L = 2;
    size_t fc = CDMA_FRAME_CHIPS(L);
    int8_t *A = rand_chips(fc, 71), *B = rand_chips(fc, 72);
    snd_step_t s0[1] = { { SND_DATA, 5, A } };   /* channel expects round 0 */
    snd_step_t s1[1] = { { SND_DATA, 0, B } };
    rcv_expect_t drain = { -1, NULL, NULL };
    mock_t mocks[4] = {
        mk_sender(0, s0, 1, n, L), mk_sender(1, s1, 1, n, L),
        mk_receiver(0, &drain, n, L), mk_receiver(1, &drain, n, L),
    };
    channel_metrics_t m; int bad;
    int rc = run_scenario(PORT(7), n, 0, mocks, 4, &m, &bad);
    assert_true(rc == -1, "round=5 where 0 is expected returns -1");
    assert_eq_i64(bad, 0, "receivers saw EOF, nobody hung");
    free(A); free(B);
}

/* CH8 */
static void test_bad_hello(void) {
    TEST_BEGIN("CH8 bad hello / bad params");
    channel_metrics_t m; int bad;

    {   /* duplicate sender id */
        mock_t mocks[2] = { mk_sender(0, NULL, 0, 0, 0), mk_sender(0, NULL, 0, 0, 0) };
        mocks[0].expect_reject = 1;
        mocks[1].expect_reject = 1; mocks[1].start_delay_ms = 40;
        int rc = run_scenario(PORT(8), 2, 0, mocks, 2, &m, &bad);
        assert_true(rc == -1, "duplicate sender id returns -1");
        assert_eq_i64(bad, 0, "no config was sent / nobody hung");
    }
    {   /* id >= n */
        mock_t mocks[1] = { mk_sender(5, NULL, 0, 0, 0) };
        mocks[0].expect_reject = 1;
        int rc = run_scenario(PORT(8) + 1, 2, 0, mocks, 1, &m, &bad);
        assert_true(rc == -1, "id 5 with n=2 returns -1");
        assert_eq_i64(bad, 0, "client saw EOF instead of config");
    }
    {   /* role 7 */
        mock_t mocks[1] = { mk_sender(0, NULL, 0, 0, 0) };
        mocks[0].role = 7; mocks[0].expect_reject = 1;
        int rc = run_scenario(PORT(8) + 2, 2, 0, mocks, 1, &m, &bad);
        assert_true(rc == -1, "role 7 returns -1");
        assert_eq_i64(bad, 0, "client saw EOF instead of config");
    }
    {   /* n out of range: rejected before any socket work */
        channel_params_t p0 = { .port = PORT(8) + 3, .n = 0, .max_rounds = 0 };
        channel_params_t p65 = { .port = PORT(8) + 3, .n = 65, .max_rounds = 0 };
        assert_true(channel_serve(&p0, &m) == -1, "n=0 returns -1");
        assert_true(channel_serve(&p65, &m) == -1, "n=65 returns -1");
    }
}

/* CH9 */
static void test_max_rounds_cap(void) {
    TEST_BEGIN("CH9 max_rounds cap");
    const int n = 2, L = 2;
    size_t fc = CDMA_FRAME_CHIPS(L);
    int8_t *A[5], *B[5];
    int16_t *want[3];
    snd_step_t s0[5], s1[5];
    for (int r = 0; r < 5; r++) {
        A[r] = rand_chips(fc, 90 + r);
        B[r] = rand_chips(fc, 95 + r);
        s0[r] = (snd_step_t){ SND_DATA, (uint32_t)r, A[r] };
        s1[r] = (snd_step_t){ SND_DATA, (uint32_t)r, B[r] };
    }
    for (int r = 0; r < 3; r++) {
        want[r] = malloc(fc * sizeof(int16_t));
        for (size_t k = 0; k < fc; k++) want[r][k] = (int16_t)(A[r][k] + B[r][k]);
    }
    const int16_t *sums[3] = { want[0], want[1], want[2] };
    int active[3] = { 2, 2, 2 };
    rcv_expect_t ex = { 3, sums, active };
    mock_t mocks[4] = {
        mk_sender(0, s0, 5, n, L), mk_sender(1, s1, 5, n, L),
        mk_receiver(0, &ex, n, L), mk_receiver(1, &ex, n, L),
    };
    channel_metrics_t m; int bad;
    int rc = run_scenario(PORT(9), n, 3, mocks, 4, &m, &bad);
    assert_true(rc == 0, "channel_serve returns 0");
    assert_eq_i64(bad, 0, "exactly 3 sums, then should_stop with round=3");
    assert_eq_i64(m.rounds, 3, "rounds == 3");
    for (int r = 0; r < 5; r++) { free(A[r]); free(B[r]); }
    for (int r = 0; r < 3; r++) free(want[r]);
}

/* CH10 */
static void test_metrics_exact(void) {
    TEST_BEGIN("CH10 metrics exact");
    const int n = 2, L = 2;
    size_t fc = CDMA_FRAME_CHIPS(L);   /* 1040 */
    int8_t *plus = const_chips(fc, 1), *minus = const_chips(fc, -1);
    int16_t *two = malloc(fc * sizeof(int16_t)), *neg1 = malloc(fc * sizeof(int16_t));
    for (size_t k = 0; k < fc; k++) { two[k] = 2; neg1[k] = -1; }

    snd_step_t s0[2] = { { SND_DATA, 0, plus }, { SND_DATA, 1, minus } };
    snd_step_t s1[2] = { { SND_DATA, 0, plus }, { SND_IDLE, 1, NULL } };
    const int16_t *sums[2] = { two, neg1 };
    int active[2] = { 2, 1 };
    rcv_expect_t ex = { 2, sums, active };
    mock_t mocks[4] = {
        mk_sender(0, s0, 2, n, L), mk_sender(1, s1, 2, n, L),
        mk_receiver(0, &ex, n, L), mk_receiver(1, &ex, n, L),
    };
    channel_metrics_t m; int bad;
    int rc = run_scenario(PORT(10), n, 2, mocks, 4, &m, &bad);
    assert_true(rc == 0, "channel_serve returns 0");
    assert_eq_i64(bad, 0, "receivers saw the expected sums");
    assert_eq_i64(m.n, 2, "n == 2");
    assert_eq_i64(m.L, 2, "L == 2");
    assert_eq_i64(m.rounds, 2, "rounds == 2");
    assert_eq_i64(m.data_sends, 3, "data_sends == 3");
    assert_eq_i64(m.idle_sends, 1, "idle_sends == 1");
    /* in: r0 = 2*(8+1040), r1 = (8+1040) + 8 */
    assert_eq_i64(m.bytes_in, 3152, "bytes_in == 3152 (handshake excluded)");
    /* out: 2 rounds * 2 receivers * (8 + 2*1040) + 2 stop headers * 8 */
    assert_eq_i64(m.bytes_out, 8368, "bytes_out == 8368 (handshake excluded, stop headers included)");
    assert_eq_i64(m.max_abs_sum, 2, "max_abs_sum == 2");
    assert_true(m.elapsed_ns > 0, "elapsed_ns > 0");
    assert_true(m.sum_ns > 0 && m.sum_ns <= m.elapsed_ns, "0 < sum_ns <= elapsed_ns");
    free(plus); free(minus); free(two); free(neg1);
}

/* CH11 */
static void test_extremes(void) {
    TEST_BEGIN("CH11 extremes n=1 and n=64");
    channel_metrics_t m; int bad;

    {   /* n = 1, L = 1 */
        size_t fc = CDMA_FRAME_CHIPS(1);
        int8_t *A = rand_chips(fc, 111);
        int16_t *want = malloc(fc * sizeof(int16_t));
        for (size_t k = 0; k < fc; k++) want[k] = A[k];
        snd_step_t s0[1] = { { SND_DATA, 0, A } };
        const int16_t *sums[1] = { want };
        int active[1] = { 1 };
        rcv_expect_t ex = { 1, sums, active };
        mock_t mocks[2] = { mk_sender(0, s0, 1, 1, 1), mk_receiver(0, &ex, 1, 1) };
        int rc = run_scenario(PORT(11), 1, 1, mocks, 2, &m, &bad);
        assert_true(rc == 0, "n=1: channel_serve returns 0");
        assert_eq_i64(bad, 0, "n=1: receiver got sum == chips");
        assert_eq_i64(m.L, 1, "n=1: L == 1");
        assert_eq_i64(m.max_abs_sum, 1, "n=1: max_abs_sum == 1");
        free(A); free(want);
    }
    {   /* n = 64, L = 64 */
        const int n = 64, L = 64;
        size_t fc = CDMA_FRAME_CHIPS(L);
        int8_t *chips[MAX_N];
        const int8_t *ptrs[MAX_N];
        snd_step_t steps[MAX_N][1];
        for (int i = 0; i < n; i++) {
            chips[i] = rand_chips(fc, 1100 + i);
            ptrs[i] = chips[i];
            steps[i][0] = (snd_step_t){ SND_DATA, 0, chips[i] };
        }
        int16_t *want = malloc(fc * sizeof(int16_t));
        cdma_sum(ptrs, n, fc, want);
        int32_t want_max = 0;
        for (size_t k = 0; k < fc; k++) {
            int32_t a = want[k] < 0 ? -want[k] : want[k];
            if (a > want_max) want_max = a;
        }
        const int16_t *sums[1] = { want };
        int active[1] = { n };
        rcv_expect_t ex = { 1, sums, active };
        mock_t mocks[2 * MAX_N];
        for (int i = 0; i < n; i++) {
            mocks[i] = mk_sender(i, steps[i], 1, n, L);
            mocks[n + i] = mk_receiver(i, &ex, n, L);
        }
        int rc = run_scenario(PORT(11) + 1, n, 1, mocks, 2 * n, &m, &bad);
        assert_true(rc == 0, "n=64: channel_serve returns 0");
        assert_eq_i64(bad, 0, "n=64: all 64 receivers got the exact 64-way sum");
        assert_eq_i64(m.L, 64, "n=64: L == 64");
        assert_true(m.max_abs_sum <= 64, "n=64: max_abs_sum <= 64");
        assert_eq_i64(m.max_abs_sum, want_max, "n=64: max_abs_sum matches the local sum");
        for (int i = 0; i < n; i++) free(chips[i]);
        free(want);
    }
}

int main(void) {
    test_handshake_any_order();
    test_n_not_power_of_2();
    test_one_round_sum();
    test_idle_contributes_zero();
    test_done_contributes_zero_forever();
    test_termination_all_done();
    test_desync_guard();
    test_bad_hello();
    test_max_rounds_cap();
    test_metrics_exact();
    test_extremes();
    return test_summary();
}
