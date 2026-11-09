#include "cli.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <limits.h>

static const cli_spec_t *find_spec(const cli_spec_t *specs, int nspecs,
                                    const char *name) {
    for (int i = 0; i < nspecs; i++) {
        if (strcmp(specs[i].name, name) == 0) return &specs[i];
    }
    return NULL;
}

static void print_usage(const cli_spec_t *specs, int nspecs) {
    printf("usage:\n");
    for (int i = 0; i < nspecs; i++) {
        const cli_spec_t *s = &specs[i];
        const char *type_name =
            s->type == CLI_INT    ? "INT"    :
            s->type == CLI_UINT   ? "UINT"   :
            s->type == CLI_STRING ? "STRING" : "BOOL";
        printf("  --%-15s %-6s", s->name, type_name);
        if (s->type == CLI_STRING) {
            printf(" (default: %s)", s->default_s ? s->default_s : "");
        } else {
            printf(" (default: %ld)", s->default_i);
        }
        if (s->help) printf("  %s", s->help);
        printf("\n");
    }
}

static void apply_defaults(const cli_spec_t *specs, int nspecs) {
    for (int i = 0; i < nspecs; i++) {
        const cli_spec_t *s = &specs[i];
        switch (s->type) {
        case CLI_INT:
            *(int *)s->dest = (int)s->default_i;
            break;
        case CLI_UINT:
            *(unsigned int *)s->dest = (unsigned int)s->default_i;
            break;
        case CLI_BOOL:
            *(int *)s->dest = (int)s->default_i;
            break;
        case CLI_STRING:
            *(const char **)s->dest = s->default_s;
            break;
        }
    }
}

/* strict decimal integer: optional leading '-', at least one digit, no
 * trailing garbage. Returns 0 ok, -1 malformed. */
static int parse_long_strict(const char *str, long *out) {
    if (str == NULL || str[0] == '\0') return -1;
    char *end = NULL;
    long v = strtol(str, &end, 10);
    if (end == str || *end != '\0') return -1;
    *out = v;
    return 0;
}

int cli_parse(int argc, char **argv, const cli_spec_t *specs, int nspecs) {
    apply_defaults(specs, nspecs);

    for (int i = 1; i < argc; i++) {
        const char *arg = argv[i];
        if (strncmp(arg, "--", 2) != 0 || arg[2] == '\0') {
            fprintf(stderr, "cli: unexpected argument '%s'\n", arg);
            return -1;
        }
        const char *name = arg + 2;

        if (strcmp(name, "help") == 0) {
            print_usage(specs, nspecs);
            return 1;
        }

        const cli_spec_t *s = find_spec(specs, nspecs, name);
        if (s == NULL) {
            fprintf(stderr, "cli: unknown flag '--%s'\n", name);
            return -1;
        }

        if (s->type == CLI_BOOL) {
            *(int *)s->dest = 1;
            continue;
        }

        if (i + 1 >= argc) {
            fprintf(stderr, "cli: '--%s' needs a value\n", name);
            return -1;
        }
        const char *value = argv[++i];

        if (s->type == CLI_STRING) {
            *(const char **)s->dest = value;
            continue;
        }

        long v;
        if (parse_long_strict(value, &v) != 0) {
            fprintf(stderr, "cli: '--%s' expects a number, got '%s'\n",
                    name, value);
            return -1;
        }
        /* range-check before the narrowing cast, or 4294967377 wraps to 81 */
        int in_range = (s->type == CLI_UINT)
                     ? (v >= 0 && v <= UINT_MAX)
                     : (v >= INT_MIN && v <= INT_MAX);
        if (!in_range) {
            fprintf(stderr, "cli: '--%s' is out of range, got '%s'\n",
                    name, value);
            return -1;
        }
        if (s->type == CLI_UINT) {
            *(unsigned int *)s->dest = (unsigned int)v;
        } else {
            *(int *)s->dest = (int)v;
        }
    }
    return 0;
}
