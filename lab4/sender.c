#include "cdma.h"
#include "cli.h"
#include "framing.h"
#include "station.h"
#include "utils.h"
#include "walsh.h"

#include <arpa/inet.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
    unsigned int port, id, start_round;
    const char *host, *in_path;
    cli_spec_t specs[] = {
        { "port",        CLI_UINT,   &port,        CDMA_PORT_DEFAULT, NULL,        "channel port" },
        { "id",          CLI_UINT,   &id,          0,                 NULL,        "station id 0..n-1" },
        { "in",          CLI_STRING, &in_path,     0,                 NULL,        "input file" },
        { "host",        CLI_STRING, &host,        0,                 "127.0.0.1", "channel host" },
        { "start-round", CLI_UINT,   &start_round, 0,                 NULL,        "IDLE for rounds 0..K-1 first" },
    };
    int rc = cli_parse(argc, argv, specs, 5);
    if (rc == 1) return 0;               /* --help */
    if (rc < 0) return 2;                /* cli_parse already said why */
    if (!in_path) { fprintf(stderr, "sender: --in is required\n"); return 2; }

    size_t size = 0;
    uint8_t *data = read_file(in_path, &size);
    if (!data) return 1;

    config_msg_t cfg;
    int fd = station_join(host, port, ROLE_SENDER, id, &cfg);
    if (fd < 0) { free(data); return 1; }

    int L = (int)cfg.L;
    int8_t code[WALSH_MAX_L];
    int8_t *chips = malloc(CDMA_FRAME_CHIPS(L));
    int status = 1;
    if (!chips || walsh_code(L, (int)id, code) != 0) goto done;

    size_t off = 0;
    uint16_t seq = 0;
    uint32_t round = 0;
    for (;;) {
        snd_kind_t kind;
        if (size == 0 || off >= size) kind = SND_DONE;
        else if (round < start_round)  kind = SND_IDLE;
        else                           kind = SND_DATA;

        snd_hdr_t h;
        memset(&h, 0, sizeof h);
        h.round = htonl(round);
        h.kind = (uint8_t)kind;
        if (send_all(fd, &h, sizeof h) != (ssize_t)sizeof h) goto done;

        if (kind == SND_DONE) break;
        if (kind == SND_DATA) {
            size_t len = size - off < PAYLOAD_SIZE ? size - off : PAYLOAD_SIZE;
            frame_t frame;
            if (framing_build(&frame, (uint8_t)id, seq, data + off, len) != 0) goto done;
            cdma_spread((const uint8_t *)&frame, FRAME_SIZE, code, L, chips);
            if (send_all(fd, chips, CDMA_FRAME_CHIPS(L)) != (ssize_t)CDMA_FRAME_CHIPS(L)) goto done;
            off += len;
            seq++;
        }
        round++;
    }
    status = 0;

done:
    close_conn(fd);
    free(chips);
    free(data);
    return status;
}
