#include "channel.h"
#include "cdma.h"
#include "walsh.h"
#include "timer.h"
#include "utils.h"

#include <arpa/inet.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>

#define MAX_STATIONS 64

int channel_serve(const channel_params_t *p, channel_metrics_t *out) {
    if (p->n < 1 || p->n > MAX_STATIONS) return -1;

    int n = p->n;
    int L = walsh_length_for(n);
    size_t fc = CDMA_FRAME_CHIPS(L);

    /* A peer that vanishes mid-round must give EPIPE, not kill the channel. */
    signal(SIGPIPE, SIG_IGN);

    memset(out, 0, sizeof *out);
    out->n = (uint32_t)n;
    out->L = (uint32_t)L;

    int rc = -1;
    int listen_fd = -1;
    int sfd[MAX_STATIONS], rfd[MAX_STATIONS];
    int done[MAX_STATIONS] = {0};
    const int8_t *ptrs[MAX_STATIONS];
    int8_t *chips = NULL;
    int16_t *sum = NULL;
    uint16_t *wire = NULL;
    uint64_t t_start = 0;
    uint32_t r = 0;
    int live = n;   /* senders not yet DONE */

    for (int i = 0; i < n; i++) sfd[i] = rfd[i] = -1;

    listen_fd = tcp_listen(p->port, 2 * n + 8);
    if (listen_fd < 0) goto cleanup;

    /* Hellos may arrive in any order; a bad or duplicate one aborts. */
    for (int k = 0; k < 2 * n; k++) {
        int fd = tcp_accept(listen_fd);
        if (fd < 0) goto cleanup;

        hello_msg_t h;
        if (recv_all(fd, &h, sizeof h) != (ssize_t)sizeof h) { close_conn(fd); goto cleanup; }
        if (h.id >= n || (h.role != ROLE_SENDER && h.role != ROLE_RECEIVER)) {
            close_conn(fd);
            goto cleanup;
        }
        int *slot = (h.role == ROLE_SENDER) ? &sfd[h.id] : &rfd[h.id];
        if (*slot != -1) { close_conn(fd); goto cleanup; }
        *slot = fd;
    }

    for (int i = 0; i < n; i++) {
        config_msg_t cfg;
        memset(&cfg, 0, sizeof cfg);
        cfg.n = htonl((uint32_t)n);
        cfg.L = htonl((uint32_t)L);
        cfg.code_index = (uint8_t)i;
        if (send_all(sfd[i], &cfg, sizeof cfg) != (ssize_t)sizeof cfg) goto cleanup;
        if (send_all(rfd[i], &cfg, sizeof cfg) != (ssize_t)sizeof cfg) goto cleanup;
    }

    chips = malloc((size_t)n * fc);
    sum = malloc(fc * sizeof *sum);
    wire = malloc(fc * sizeof *wire);
    if (!chips || !sum || !wire) goto cleanup;

    t_start = timer_now_ns();

    /* The all-DONE and max_rounds checks sit at the top, so a round in
     * which the last senders finish is still broadcast. */
    while (live > 0 && !(p->max_rounds != 0 && r == p->max_rounds)) {
        int active = 0;

        for (int i = 0; i < n; i++) {
            ptrs[i] = NULL;
            if (done[i]) continue;

            snd_hdr_t sh;
            if (recv_all(sfd[i], &sh, sizeof sh) != (ssize_t)sizeof sh) goto cleanup;
            if (ntohl(sh.round) != r) goto cleanup;   /* desync guard */
            out->bytes_in += sizeof sh;

            if (sh.kind == SND_DATA) {
                int8_t *dst = chips + (size_t)i * fc;
                if (recv_all(sfd[i], dst, fc) != (ssize_t)fc) goto cleanup;
                ptrs[i] = dst;
                out->bytes_in += fc;
                out->data_sends++;
                active++;
            } else if (sh.kind == SND_IDLE) {
                out->idle_sends++;
            } else if (sh.kind == SND_DONE) {
                done[i] = 1;
                live--;
            } else {
                goto cleanup;
            }
        }

        uint64_t t0 = timer_now_ns();
        cdma_sum(ptrs, n, fc, sum);
        out->sum_ns += timer_now_ns() - t0;

        for (size_t k = 0; k < fc; k++) {
            int32_t a = sum[k] < 0 ? -(int32_t)sum[k] : sum[k];
            if (a > out->max_abs_sum) out->max_abs_sum = a;
            wire[k] = htons((uint16_t)sum[k]);
        }

        rcv_hdr_t rh;
        memset(&rh, 0, sizeof rh);
        rh.round = htonl(r);
        rh.active = (uint8_t)active;
        for (int i = 0; i < n; i++) {
            if (send_all(rfd[i], &rh, sizeof rh) != (ssize_t)sizeof rh) goto cleanup;
            if (send_all(rfd[i], wire, fc * sizeof *wire) != (ssize_t)(fc * sizeof *wire)) goto cleanup;
            out->bytes_out += sizeof rh + fc * sizeof *wire;
        }

        r++;
        out->rounds = r;
    }

    for (int i = 0; i < n; i++) {
        rcv_hdr_t stop;
        memset(&stop, 0, sizeof stop);
        stop.round = htonl(r);
        stop.should_stop = 1;
        if (send_all(rfd[i], &stop, sizeof stop) != (ssize_t)sizeof stop) goto cleanup;
        out->bytes_out += sizeof stop;
    }

    out->elapsed_ns = timer_now_ns() - t_start;
    rc = 0;

cleanup:
    for (int i = 0; i < n; i++) {
        close_conn(sfd[i]);
        close_conn(rfd[i]);
    }
    close_conn(listen_fd);
    free(chips);
    free(sum);
    free(wire);
    return rc;
}
