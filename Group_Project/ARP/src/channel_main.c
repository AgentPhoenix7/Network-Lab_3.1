#define _POSIX_C_SOURCE 200809L
#include "channel.h"
#include "cli.h"

#include <signal.h>
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
    unsigned int lan = 0, port = 9600, tel_port = 9700, seed = 1;
    const char *mode = "bus";
    cli_spec_t specs[] = {
        { "lan",      CLI_UINT,   &lan,      0,    NULL,  "LAN id (goes into events)" },
        { "port",     CLI_UINT,   &port,     9600, NULL,  "NIC listen port" },
        { "mode",     CLI_STRING, &mode,     0,    "bus", "bus (hub) or star (learning switch)" },
        { "tel-port", CLI_UINT,   &tel_port, 9700, NULL,  "bridge telemetry port, 0 = stdio" },
        { "seed",     CLI_UINT,   &seed,     1,    NULL,  "random seed for impairments" },
    };
    int rc = cli_parse(argc, argv, specs, 5);
    if (rc == 1) return 0;               /* --help */
    if (rc < 0) return 2;                /* cli_parse already said why */
    if (strcmp(mode, "bus") != 0 && strcmp(mode, "star") != 0) {
        fprintf(stderr, "channel: --mode must be bus or star\n");
        return 2;
    }
    if (port > 65535 || tel_port > 65535) {
        fprintf(stderr, "channel: ports must be 0..65535\n");
        return 2;
    }

    signal(SIGPIPE, SIG_IGN);            /* a dead NIC gives EPIPE, not a killed channel */
    channel_cfg_t cfg = { (int)lan, (uint16_t)port, strcmp(mode, "star") == 0,
                          (uint16_t)tel_port, seed };
    channel_t ch;
    if (channel_init(&ch, &cfg) != 0) {
        fprintf(stderr, "channel: cannot listen on %u or reach telemetry on %u\n", port, tel_port);
        return 1;
    }
    int r = channel_run(&ch);
    channel_close(&ch);
    return r == 0 ? 0 : 1;
}
