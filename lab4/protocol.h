#ifndef LAB4_PROTOCOL_H
#define LAB4_PROTOCOL_H
#include <stdint.h>
#include <stddef.h>
#include "frame.h"
#include "utils.h"

#define CDMA_PORT_DEFAULT 9400
#define CDMA_FRAME_BITS (FRAME_SIZE * 8)
#define CDMA_FRAME_CHIPS(L) ((size_t)CDMA_FRAME_BITS * (size_t)(L))

typedef enum { ROLE_SENDER = 0, ROLE_RECEIVER = 1 } role_t;
typedef enum { SND_DATA = 0, SND_IDLE = 1, SND_DONE = 2 } snd_kind_t;

#pragma pack(push,1)
typedef struct {
    uint8_t role; // role_t enum
    uint8_t id;
    uint8_t _pad[2];
} hello_msg_t;

typedef struct {
    uint32_t n; // stations
    uint32_t L; // walsh-length
    uint8_t code_index; // id
    uint8_t _pad[3];
} config_msg_t;

typedef struct {
    uint32_t round;
    uint8_t kind; // snd_kind_t enum
    uint8_t _pad[3];
} snd_hdr_t;

typedef struct {
    uint32_t round;
    uint8_t should_stop; // done flag
    uint8_t active; // active senders
    uint8_t _pad[2];
} rcv_hdr_t;

#pragma pack(pop)

#endif