#ifndef NIC_H
#define NIC_H
#include <stdint.h>
#include "l2.h"

/* Connects to the channel on 127.0.0.1:port (retries 50 x 100 ms), sends nic_hello_t, reads the 0x01 ack.
 * Returns the connected fd; -1 cannot connect or no ack. */
int nic_join(uint16_t port, const char *node, uint8_t iface, const uint8_t mac[6]);
/* 0 sent; -1 short write (connection gone). */
int nic_send(int fd, const frame_t *f);
/* 0 got one whole frame; -1 connection closed or short read. */
int nic_recv(int fd, frame_t *f);
#endif
