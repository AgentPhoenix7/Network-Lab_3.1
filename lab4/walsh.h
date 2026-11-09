#ifndef LAB4_WALSH_H
#define LAB4_WALSH_H
#include <stdint.h>

#define WALSH_MAX_L 1024

int walsh_is_pow2(int L);
int walsh_length_for(int n);  // smallest power of 2 >= n
int walsh_matrix(int L, int8_t *W);  // fill L*L row major Hadamard matrix 1/-1

int walsh_code(int L, int index, int8_t *code);

#endif 