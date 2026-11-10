#define _POSIX_C_SOURCE 200809L
#include "channel.h"
#include "l2.h"
#include "utils.h"
#include "test_util.h"

#include <arpa/inet.h>
#include <signal.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>

#define DATA_PORT 19600
#define TEL_PORT  19700

/* The test is the fake bridge (telemetry) and the fake NICs. The channel runs in-process,
 * driven by channel_step(). Client reads use MSG_DONTWAIT: delivery is synchronous. */
static int tel_listen = -1, bridge_fd = -1;
static int nic[3] = { -1, -1, -1 };
static channel_t ch;
static char log_buf[8192];
static size_t log_len;

static void close_fd(int *fd) {
    close_conn(*fd);
    *fd = -1;
}

static void mac_of(uint8_t m[6], uint8_t last) {
    memset(m, 0, 6);
    m[0] = 2;
    m[5] = last;
}

/* Appends what the channel wrote to the fake bridge so far. */
static void drain_bridge(void) {
    ssize_t n;
    while (log_len < sizeof log_buf - 1 &&
           (n = recv(bridge_fd, log_buf + log_len, sizeof log_buf - 1 - log_len, MSG_DONTWAIT)) > 0) {
        log_len += (size_t)n;
    }
}

static int log_has(const char *needle) {
    drain_bridge();
    return strstr(log_buf, needle) != NULL;
}

/* Fresh channel in bus or star mode; the bridge side of its telemetry is accepted. */
static int setup(int star) {
    channel_cfg_t cfg = { 0, DATA_PORT, star, TEL_PORT, 1 };
    log_len = 0;
    log_buf[0] = '\0';
    if (channel_init(&ch, &cfg) != 0) return -1;
    bridge_fd = tcp_accept(tel_listen);
    return bridge_fd < 0 ? -1 : 0;
}

static void teardown(void) {
    for (int i = 0; i < 3; i++) close_fd(&nic[i]);
    channel_close(&ch);
    close_fd(&bridge_fd);
}

/* Connects a fake NIC, sends its hello, lets the channel accept it, reads the 0x01 reply. */
static int nic_connect(int idx, const char *node, uint8_t last) {
    nic_hello_t h;
    uint8_t ack = 0;
    struct timeval tv = { 0, 500000 };
    memset(&h, 0, sizeof h);
    memcpy(h.node, node, strlen(node));
    mac_of(h.mac, last);
    nic[idx] = tcp_connect("127.0.0.1", DATA_PORT);
    if (nic[idx] < 0) return -1;
    setsockopt(nic[idx], SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
    if (send_all(nic[idx], &h, sizeof h) != (ssize_t)sizeof h) return -1;
    channel_step(&ch, 10);
    return recv_all(nic[idx], &ack, 1) == 1 && ack == 0x01 ? 0 : -1;
}

/* Sends one frame from NIC `from` and runs one channel step, which delivers it. */
static void send_frame(int from, uint8_t src, uint8_t dst, uint8_t tag) {
    frame_t f;
    memset(&f, 0, sizeof f);
    f.header.frame_type = FT_IPV4;
    mac_of(f.header.src_addr, src);
    mac_of(f.header.dest_addr, dst);
    f.header.frame_len = htons(44);
    f.header.frame_seq = htons(tag);
    f.payload[0] = tag;
    assert_true(send_all(nic[from], &f, sizeof f) == (ssize_t)sizeof f, "frame written to channel");
    channel_step(&ch, 10);
}

static int got_frame(int fd, uint8_t tag) {
    frame_t f;
    return recv(fd, &f, sizeof f, MSG_DONTWAIT) == (ssize_t)sizeof f && f.payload[0] == tag;
}

static int nothing(int fd) {
    uint8_t b;
    return recv(fd, &b, 1, MSG_DONTWAIT) < 0;
}

static int three_nics(void) {
    return nic_connect(0, "h1", 1) == 0 && nic_connect(1, "h2", 2) == 0 && nic_connect(2, "h3", 3) == 0;
}

static void test_bus(void) {
    TEST_BEGIN("CH1 bus delivers to all but the sender");
    int ok = setup(0) == 0 && three_nics();
    assert_true(ok, "channel and three NICs up");
    if (ok) {
        send_frame(0, 1, 2, 0x41);
        assert_true(got_frame(nic[1], 0x41), "h2 gets the frame");
        assert_true(got_frame(nic[2], 0x41), "h3 gets the frame");
        assert_true(nothing(nic[0]), "sender h1 gets no copy");
        assert_true(log_has("\"fate\":\"ok\""), "medium event says ok");
        assert_true(log_has("\"from\":\"h1\""), "medium event names the sender");
    }
    teardown();
}

static void test_star(void) {
    TEST_BEGIN("CH2 star floods unknown, then unicasts after learning");
    int ok = setup(1) == 0 && three_nics();
    assert_true(ok, "channel and three NICs up");
    if (ok) {
        send_frame(0, 1, 2, 0x51);       /* h1 -> h2, h2 unknown: flood */
        assert_true(got_frame(nic[1], 0x51), "unknown dst floods: h2 gets it");
        assert_true(got_frame(nic[2], 0x51), "unknown dst floods: h3 gets it");
        send_frame(1, 2, 1, 0x52);       /* h2 -> h1: h2 learned, h1 known: unicast */
        assert_true(got_frame(nic[0], 0x52), "reply unicast to learned h1");
        assert_true(nothing(nic[2]), "h3 is not sent the unicast");
        send_frame(0, 1, 2, 0x53);       /* h1 -> h2: known: unicast */
        assert_true(got_frame(nic[1], 0x53), "known dst h2 gets unicast");
        assert_true(nothing(nic[2]), "h3 gets nothing once learned");
        assert_true(log_has("\"ev\":\"switch_learn\""), "switch_learn event emitted");
    }
    teardown();
}

static void test_impair(void) {
    TEST_BEGIN("CH3 IMPAIR 1 0 0 loses every frame");
    int ok = setup(0) == 0 && three_nics();
    assert_true(ok, "channel and three NICs up");
    if (ok) {
        assert_true(send_all(bridge_fd, "IMPAIR 1 0 0\n", 13) == 13, "IMPAIR sent to channel");
        channel_step(&ch, 10);
        send_frame(0, 1, 2, 0x61);
        assert_true(nothing(nic[1]), "loss 1: h2 gets nothing");
        assert_true(nothing(nic[2]), "loss 1: h3 gets nothing");
        assert_true(log_has("\"fate\":\"lost\""), "medium event says lost");
    }
    teardown();
}

static void test_quit(void) {
    TEST_BEGIN("CH4 QUIT ends the loop with exit 0");
    if (setup(0) == 0) {
        assert_true(send_all(bridge_fd, "QUIT\n", 5) == 5, "QUIT sent to channel");
        assert_eq_i64(channel_run(&ch), 0, "channel_run returns 0 after QUIT");
    } else {
        assert_true(0, "channel up");
    }
    teardown();
}

int main(void) {
    signal(SIGPIPE, SIG_IGN);
    tel_listen = tcp_listen(TEL_PORT, 4);
    if (tel_listen < 0) return 1;
    test_bus();
    test_star();
    test_impair();
    test_quit();
    close_fd(&tel_listen);
    return test_summary();
}
