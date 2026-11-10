#include "impair.h"
#include "bit_flip.h"  /* lab1, found via -I$(LAB)/lab1/error_inject */

fate_t impair_decide(double loss, double corrupt, double draw) {
    if (draw < loss) return FATE_LOST;
    if (draw < loss + corrupt) return FATE_CORRUPT;
    return FATE_OK;
}

size_t impair_corrupt(uint8_t frame[IMPAIR_FRAME_LEN]) {
    size_t bit = random_bit_index(IMPAIR_FRAME_LEN);
    flip_bit(frame, IMPAIR_FRAME_LEN, bit);
    return bit;
}
