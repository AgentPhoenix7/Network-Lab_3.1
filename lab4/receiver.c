#include "cdma.h"
#include "cli.h"
#include "framing.h"
#include "station.h"
#include "timer.h"
#include "utils.h"
#include "walsh.h"

#include <arpa/inet.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
    unsigned int port, id;
    const char *host, *out_path, *metrics_file;
    cli_spec_t specs[] = {
        { "port",         CLI_UINT,   &port,         CDMA_PORT_DEFAULT, NULL,        "channel port" },
        { "id",           CLI_UINT,   &id,           0,                 NULL,        "station id 0..n-1" },
        { "out",          CLI_STRING, &out_path,     0,                 NULL,        "output file (always created)" },
        { "host",         CLI_STRING, &host,         0,                 "127.0.0.1", "channel host" },
        { "metrics-file", CLI_STRING, &metrics_file, 0,                 NULL,        "write key=value metrics here" },
    };
    int rc = cli_parse(argc, argv, specs, 5);
    if (rc == 1) return 0;               /* --help */
    if (rc < 0) return 2;
    if (!out_path) { fprintf(stderr, "receiver: --out is required\n"); return 2; }

    FILE *out = fopen(out_path, "wb");   /* exists even if nothing arrives */
    if (!out) { perror("fopen --out"); return 1; }

    config_msg_t cfg;
    int fd = station_join(host, port, ROLE_RECEIVER, id, &cfg);
    if (fd < 0) { fclose(out); return 1; }

    int L = (int)cfg.L;
    size_t fc = CDMA_FRAME_CHIPS(L);
    int8_t code[WALSH_MAX_L];
    uint16_t *wire = malloc(fc * sizeof *wire);
    int16_t *sum = malloc(fc * sizeof *sum);
    int io_ok = wire && sum && walsh_code(L, (int)id, code) == 0;

    cdma_stats_t st = {0};
    unsigned long long frames_ok = 0, fcs_fail = 0, seq_errors = 0;
    unsigned long long silent_rounds = 0, error_frames = 0, bytes_out = 0;
    uint32_t round = 0;
    uint16_t expect_seq = 0;
    uint64_t t0 = timer_now_ns();

    while (io_ok) {
        rcv_hdr_t rh;
        if (recv_all(fd, &rh, sizeof rh) != (ssize_t)sizeof rh) { io_ok = 0; break; }
        if (ntohl(rh.round) != round) {
            fprintf(stderr, "receiver: got round %u, expected %u\n", ntohl(rh.round), round);
            io_ok = 0;
            break;
        }
        if (rh.should_stop) break;

        if (recv_all(fd, wire, fc * sizeof *wire) != (ssize_t)(fc * sizeof *wire)) { io_ok = 0; break; }
        for (size_t k = 0; k < fc; k++) sum[k] = (int16_t)ntohs(wire[k]);

        uint8_t bytes[FRAME_SIZE];
        cdma_frame_t cls = cdma_despread(sum, FRAME_SIZE, code, L, bytes, &st);
        round++;

        if (cls == CDMA_FRAME_SILENT) {
            silent_rounds++;
        } else if (cls == CDMA_FRAME_ERROR) {
            error_frames++;
        } else {
            const frame_t *f = (const frame_t *)bytes;
            uint16_t seq;
            size_t len;
            if (framing_check(f, NULL, &seq, &len) != 0) {
                fcs_fail++;               /* -1 bad FCS, -2 bad length: both rejected */
            } else {
                if (seq != expect_seq) seq_errors++;
                expect_seq = (uint16_t)(seq + 1);
                if (fwrite(f->payload, 1, len, out) != len) { io_ok = 0; break; }
                frames_ok++;
                bytes_out += len;
            }
        }
    }
    uint64_t elapsed = timer_now_ns() - t0;

    if (fclose(out) != 0) io_ok = 0;
    close_conn(fd);
    free(wire);
    free(sum);

    FILE *mf = metrics_file ? fopen(metrics_file, "w") : stdout;
    if (!mf) { perror("metrics-file"); return 1; }
    fprintf(mf, "id=%u\nrounds=%u\nframes_ok=%llu\nfcs_fail=%llu\nseq_errors=%llu\n"
                "silent_rounds=%llu\nerror_frames=%llu\nbits_one=%llu\nbits_zero=%llu\n"
                "bits_silent=%llu\nbits_invalid=%llu\nbytes_out=%llu\nelapsed_ns=%llu\n",
            id, round, frames_ok, fcs_fail, seq_errors, silent_rounds, error_frames,
            (unsigned long long)st.ones, (unsigned long long)st.zeros,
            (unsigned long long)st.silent, (unsigned long long)st.invalid,
            bytes_out, (unsigned long long)elapsed);
    if (mf != stdout) fclose(mf);

    int clean = io_ok && fcs_fail == 0 && seq_errors == 0 &&
                error_frames == 0 && st.invalid == 0;
    return clean ? 0 : 1;
}
