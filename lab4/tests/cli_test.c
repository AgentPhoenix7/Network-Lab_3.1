#include "test_util.h"
#include "../cli.h"

#include <string.h>

/* T0.1: int, uint, and string flags parse. */
static void test_parses_int_uint_string(void) {
    TEST_BEGIN("T0.1 parses int/uint/string flags");

    int port = -1;
    unsigned int n = 999;
    const char *out = NULL;

    cli_spec_t specs[] = {
        { "port", CLI_INT,    &port, 0, NULL, "port" },
        { "n",    CLI_UINT,   &n,    0, NULL, "stations" },
        { "out",  CLI_STRING, &out,  0, "default.bin", "output file" },
    };
    char *argv[] = { "prog", "--port", "9400", "--n", "5", "--out", "a.bin" };
    int argc = sizeof(argv) / sizeof(argv[0]);

    int rc = cli_parse(argc, argv, specs, 3);
    assert_true(rc == 0, "cli_parse returns 0");
    assert_eq_i64(port, 9400, "port == 9400");
    assert_eq_i64(n, 5, "n == 5");
    assert_true(out != NULL && strcmp(out, "a.bin") == 0, "out == \"a.bin\"");
}

/* T0.2: defaults kept when a flag is absent. */
static void test_defaults_kept(void) {
    TEST_BEGIN("T0.2 keeps defaults when a flag is absent");

    int port = -1;
    unsigned int n = 111;

    cli_spec_t specs[] = {
        { "port", CLI_INT,  &port, 9400, NULL, "port" },
        { "n",    CLI_UINT, &n,    3,    NULL, "stations" },
    };
    char *argv[] = { "prog", "--port", "9401" };
    int argc = sizeof(argv) / sizeof(argv[0]);

    int rc = cli_parse(argc, argv, specs, 2);
    assert_true(rc == 0, "cli_parse returns 0");
    assert_eq_i64(port, 9401, "port overridden to 9401");
    assert_eq_i64(n, 3, "n keeps its default of 3");
}

/* T0.3: errors are rejected. */
static void test_errors_rejected(void) {
    TEST_BEGIN("T0.3 rejects unknown flag, missing value, non-numeric value");

    int port = 0;
    unsigned int n = 0;
    cli_spec_t specs[] = {
        { "port", CLI_INT,  &port, 0, NULL, "port" },
        { "n",    CLI_UINT, &n,    0, NULL, "stations" },
    };

    {
        char *argv[] = { "prog", "--bogus", "1" };
        int rc = cli_parse(3, argv, specs, 2);
        assert_true(rc == -1, "unknown flag '--bogus' returns -1");
    }
    {
        char *argv[] = { "prog", "--port" };
        int rc = cli_parse(2, argv, specs, 2);
        assert_true(rc == -1, "'--port' with no following value returns -1");
    }
    {
        char *argv[] = { "prog", "--n", "x" };
        int rc = cli_parse(3, argv, specs, 2);
        assert_true(rc == -1, "'--n x' (non-numeric) returns -1");
    }
}

int main(void) {
    test_parses_int_uint_string();
    test_defaults_kept();
    test_errors_rejected();
    return test_summary();
}
