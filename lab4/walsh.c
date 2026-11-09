#include "walsh.h"

int walsh_is_pow2(int L) {
    if(L <= 0) return 0;
    return (L & (L - 1)) == 0 ? 1 : 0;
}

int walsh_length_for(int n) {
    if(n < 1) return -1;

    int L = 1;
    while(L < n) {
        L <<= 1;
    }

    return L;
}

int walsh_matrix(int L, int8_t *W) {
    if (!walsh_is_pow2(L) || L > WALSH_MAX_L) return -1;

    W[0] = 1;
    int k=1;

    while (k < L) {
        for(int i = 0 ; i<k ; i++) {
            for(int j = 0 ; j<k ; j++) {
                int v = W[i*L + j]; // top left
                W[(i+k)*L + j] = v;
                W[i*L + j+k] = v;
                W[(i+k)*L + (k+j)] = -v;
            }
        }

        k<<=1; // matrix doubles in size each iteration
    }

    return 0;
}

int walsh_code(int L, int index, int8_t *code) {
    if (!walsh_is_pow2(L) || L > WALSH_MAX_L || index < 0 || index >= L) return -1;

    code[0] = 1;
    int k = 1;
    while(k < L) {
        int flip = ((index & k) != 0); // if index of bottom half
        for(int j = 0 ; j<k ; j++) {
            int v = code[j];
            code[j+k] = flip ? -v : v;
        }

        k<<=1;
    }

    return 0;
}