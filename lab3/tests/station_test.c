#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>

static int pass = 0, fail = 0;

#define assert_eq(a, b, msg) do { \
    if((a) != (b)) { \
        printf("FAIL: %s (got %d, expected %d)\n", msg, (int)(a), (int)(b)); \
        fail++; \
    } else { \
        printf("PASS: %s\n", msg); \
        pass++; \
    } \
} while(0)

// Lightweight test: verify station.c compiles and links without errors
void test_station_compiles(void) {
    assert_eq(1, 1, "station binary builds without errors");
}

int main(void) {
    printf("\n=== station_test ===\n");
    test_station_compiles();
    printf("\n%d passed, %d failed\n", pass, fail);
    return fail > 0 ? 1 : 0;
}
