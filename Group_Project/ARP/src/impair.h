#ifndef IMPAIR_H
#define IMPAIR_H
#include <stddef.h>
#include <stdint.h>

#define IMPAIR_FRAME_LEN 65  /* bridge frames are 65 B on the wire (plan 5.5) */

typedef enum { FATE_OK = 0, FATE_LOST, FATE_CORRUPT } fate_t;

/* One draw in [0,1) decides the fate: [0,loss) lost, [loss,loss+corrupt) corrupt, else ok.
 * The bands are disjoint, so loss and corruption rates are exact and never stack. */
fate_t impair_decide(double loss, double corrupt, double draw);

/* Flips one random bit of the frame (rand()-based); returns the bit index flipped. */
size_t impair_corrupt(uint8_t frame[IMPAIR_FRAME_LEN]);

#endif
