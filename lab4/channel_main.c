#include "channel.h"
#include "cli.h"
#include <stdio.h>

int main(int argc, char **argv) {
    unsigned int port, n, max_rounds;
    const char *metrics_file;
    cli_spec_t specs[] = {
        { "port",         CLI_UINT,   &port,         CDMA_PORT_DEFAULT, NULL, "listen port" },
        { "n",            CLI_UINT,   &n,            0,                 NULL, "stations (1..64)" },
        { "max-rounds",   CLI_UINT,   &max_rounds,   0,                 NULL, "0 = unlimited" },
        { "metrics-file", CLI_STRING, &metrics_file, 0,                 NULL, "write key=value metrics here" },
    };
    int rc = cli_parse(argc, argv, specs, 4);
    if (rc == 1) return 0;               /* --help */
    if (rc < 0) return 2;                /* cli_parse already said why */
    if (n == 0) { fprintf(stderr, "channel: --n is required (1..64)\n"); return 2; }
    if (n > 64) { fprintf(stderr, "channel: --n must be 1..64\n"); return 2; }
    if (port > 65535) { fprintf(stderr, "channel: --port must be 0..65535\n"); return 2; }

    channel_params_t p = { (uint16_t)port, (int)n, max_rounds };
    channel_metrics_t m;
    if (channel_serve(&p, &m) != 0) return 1;

    FILE *f = metrics_file ? fopen(metrics_file, "w") : stdout;
    if (!f) { perror("metrics-file"); return 1; }
    fprintf(f, "n=%u\nL=%u\nrounds=%llu\ndata_sends=%llu\nidle_sends=%llu\n"
               "bytes_in=%llu\nbytes_out=%llu\nmax_abs_sum=%d\nelapsed_ns=%llu\nsum_ns=%llu\n",
            m.n, m.L, (unsigned long long)m.rounds, (unsigned long long)m.data_sends,
            (unsigned long long)m.idle_sends, (unsigned long long)m.bytes_in,
            (unsigned long long)m.bytes_out, m.max_abs_sum,
            (unsigned long long)m.elapsed_ns, (unsigned long long)m.sum_ns);
    if (f != stdout) fclose(f);
    return 0;
}