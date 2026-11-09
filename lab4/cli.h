#ifndef LAB4_CLI_H
#define LAB4_CLI_H

/* Table-driven `--flag value` parser shared by channel_main.c,
 * sender.c, and receiver.c (CLAUDE.md §5.6). */

typedef enum { CLI_INT, CLI_UINT, CLI_STRING, CLI_BOOL } cli_type_t;

typedef struct {
    const char *name;        /* matched as --name (no leading dashes)      */
    cli_type_t  type;
    void       *dest;        /* int*, unsigned int*, const char**, or
                                 int* for CLI_BOOL                          */
    long        default_i;   /* default for CLI_INT / CLI_UINT / CLI_BOOL  */
    const char *default_s;   /* default for CLI_STRING; ignored otherwise  */
    const char *help;        /* one-line description, for --help           */
} cli_spec_t;

/* Writes every spec's default into *dest first, then walks argv applying
 * "--name value" pairs (CLI_BOOL takes no value; its presence just sets
 * *dest = 1). "--help" prints usage built from specs/nspecs to stdout and
 * returns 1 without touching any dest. Returns 0 on success. Returns -1,
 * after printing one line to stderr, on: an unrecognized "--flag", a flag
 * with no following value, a non-numeric value for CLI_INT/CLI_UINT, or a
 * negative value for CLI_UINT. */

 
int cli_parse(int argc, char **argv, const cli_spec_t *specs, int nspecs);

#endif
