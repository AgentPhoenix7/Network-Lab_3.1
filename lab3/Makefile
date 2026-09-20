CC      := gcc
CFLAGS  := -Wall -Wextra -std=c11 -g
UTILS_DIR := ../utils
LAB1_DIR  := ../lab1
INCLUDES  := -I. -I$(UTILS_DIR) -I$(LAB1_DIR)

SRCS_COMMON := channel.c backoff.c csma.c $(UTILS_DIR)/utils.c $(LAB1_DIR)/error.c $(LAB1_DIR)/scheme.c
OBJS_COMMON := $(SRCS_COMMON:.c=.o)

TEST_DIR := tests

.PHONY: all clean test

all: $(TEST_DIR)/channel_test $(TEST_DIR)/backoff_test $(TEST_DIR)/csma_test $(TEST_DIR)/station_test station

$(TEST_DIR)/channel_test: $(TEST_DIR)/channel_test.o $(OBJS_COMMON)
	$(CC) $(CFLAGS) -o $@ $^

$(TEST_DIR)/backoff_test: $(TEST_DIR)/backoff_test.o backoff.o
	$(CC) $(CFLAGS) -o $@ $^

$(TEST_DIR)/csma_test: $(TEST_DIR)/csma_test.o backoff.o csma.o
	$(CC) $(CFLAGS) -o $@ $^

$(TEST_DIR)/station_test: $(TEST_DIR)/station_test.o
	$(CC) $(CFLAGS) -o $@ $^

station: station.o $(OBJS_COMMON)
	$(CC) $(CFLAGS) -o $@ $^

%.o: %.c
	$(CC) $(CFLAGS) $(INCLUDES) -c -o $@ $<

test: all
	./$(TEST_DIR)/channel_test
	./$(TEST_DIR)/backoff_test
	./$(TEST_DIR)/csma_test
	./$(TEST_DIR)/station_test

clean:
	rm -f *.o $(UTILS_DIR)/*.o $(LAB1_DIR)/*.o $(TEST_DIR)/*.o $(TEST_DIR)/channel_test $(TEST_DIR)/backoff_test $(TEST_DIR)/csma_test $(TEST_DIR)/station_test station
