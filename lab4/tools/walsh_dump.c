#include "../walsh.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void print_matrix_csv(int L, const int8_t *W) {
    for (int i = 0; i < L; i++) {
        for (int j = 0; j < L; j++) {
            printf("%d%s", W[i * L + j], (j + 1 < L) ? "," : "\n");
        }
    }
}

static void print_gram_csv(int L, const int8_t *W) {
    for (int i = 0; i < L; i++) {
        for (int j = 0; j < L; j++) {
            long dot = 0;
            for (int k = 0; k < L; k++) dot += (long)W[i * L + k] * (long)W[j * L + k];
            printf("%ld%s", dot, (j + 1 < L) ? "," : "\n");
        }
    }
}

static void print_lengths_csv(int N) {
    printf("n,L\n");
    for (int n = 1; n <= N; n++) {
        printf("%d,%d\n", n, walsh_length_for(n));
    }
}

int main(int argc, char **argv) {
    if (argc == 3 && strcmp(argv[1], "--gram") == 0) {
        int L = atoi(argv[2]);
        int8_t *W = malloc((size_t)L * (size_t)L * sizeof(int8_t));
        if (W == NULL || walsh_matrix(L, W) != 0) {
            fprintf(stderr, "walsh_dump: invalid L=%d\n", L);
            free(W);
            return 1;
        }
        print_gram_csv(L, W);
        free(W);
        return 0;
    }

    if (argc == 3 && strcmp(argv[1], "--lengths") == 0) {
        int N = atoi(argv[2]);
        if (N < 1) {
            fprintf(stderr, "walsh_dump: invalid N=%d\n", N);
            return 1;
        }
        print_lengths_csv(N);
        return 0;
    }

    if (argc == 2) {
        int L = atoi(argv[1]);
        int8_t *W = malloc((size_t)L * (size_t)L * sizeof(int8_t));
        if (W == NULL || walsh_matrix(L, W) != 0) {
            fprintf(stderr, "walsh_dump: invalid L=%d\n", L);
            free(W);
            return 1;
        }
        print_matrix_csv(L, W);
        free(W);
        return 0;
    }

    fprintf(stderr,
            "usage: walsh_dump L\n"
            "       walsh_dump --gram L\n"
            "       walsh_dump --lengths N\n");
    return 1;
}
