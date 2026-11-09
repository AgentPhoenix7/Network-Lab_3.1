CC      := gcc
CFLAGS  := -Wall -Wextra -std=c11 -g

LAB1_DIR  := ../lab1
UTILS_DIR := ../utils
LAB2_DIR  := ../lab2
INCLUDES  := -I. -I$(LAB1_DIR) -I$(UTILS_DIR) -I$(LAB2_DIR)

LAB1_SRCS   := $(LAB1_DIR)/scheme.c $(LAB1_DIR)/error.c
UTILS_SRCS  := $(UTILS_DIR)/utils.c
LAB2_SRCS   := $(LAB2_DIR)/timer.c
COMMON_SRCS := $(LAB1_SRCS) $(UTILS_SRCS) $(LAB2_SRCS)
COMMON_OBJS := $(COMMON_SRCS:.c=.o)

TEST_DIR := tests

# Each module's object only builds once its .c exists (user writes them
# milestone by milestone; Claude writes cli.c up front).
WALSH_OBJ   := $(if $(wildcard walsh.c),walsh.o)
CDMA_OBJ    := $(if $(wildcard cdma.c),cdma.o)
FRAMING_OBJ := $(if $(wildcard framing.c),framing.o)
CHANNEL_OBJ := $(if $(wildcard channel.c),channel.o)
CLI_OBJ     := $(if $(wildcard cli.c),cli.o)

# Binaries that exist once their sources exist: this is how `all`
# grows milestone by milestone without editing the Makefile again.
CLI_TEST_BIN     := $(if $(wildcard tests/cli_test.c),$(TEST_DIR)/cli_test)
WALSH_TEST_BIN    := $(if $(wildcard tests/walsh_test.c),$(if $(WALSH_OBJ),$(TEST_DIR)/walsh_test))
CDMA_TEST_BIN     := $(if $(wildcard tests/cdma_test.c),$(if $(CDMA_OBJ),$(TEST_DIR)/cdma_test))
FRAMING_TEST_BIN  := $(if $(wildcard tests/framing_test.c),$(if $(FRAMING_OBJ),$(TEST_DIR)/framing_test))
CHANNEL_TEST_BIN  := $(if $(wildcard tests/channel_test.c),$(if $(CHANNEL_OBJ),$(TEST_DIR)/channel_test))
WALSH_DUMP_BIN    := $(if $(wildcard tools/walsh_dump.c),$(if $(WALSH_OBJ),tools/walsh_dump))
CHANNEL_BIN       := $(if $(wildcard channel_main.c),$(if $(CHANNEL_OBJ),channel))
SENDER_BIN        := $(if $(wildcard sender.c),$(if $(WALSH_OBJ),sender))
RECEIVER_BIN      := $(if $(wildcard receiver.c),$(if $(WALSH_OBJ),receiver))

TEST_BINS := $(CLI_TEST_BIN) $(WALSH_TEST_BIN) $(CDMA_TEST_BIN) \
             $(FRAMING_TEST_BIN) $(CHANNEL_TEST_BIN)
ALL_BINS  := $(TEST_BINS) $(WALSH_DUMP_BIN) $(CHANNEL_BIN) $(SENDER_BIN) $(RECEIVER_BIN)

.PHONY: all test e2e eval plots report clean

all: $(ALL_BINS)

# --- M0 -----------------------------------------------------------------
$(TEST_DIR)/cli_test: $(TEST_DIR)/cli_test.o cli.o
	$(CC) $(CFLAGS) -o $@ $^

# --- M1 -------------------------------------------------------------------
$(TEST_DIR)/walsh_test: $(TEST_DIR)/walsh_test.o walsh.o
	$(CC) $(CFLAGS) -o $@ $^

tools/walsh_dump: tools/walsh_dump.o walsh.o
	$(CC) $(CFLAGS) -o $@ $^

# --- M2 -------------------------------------------------------------------
$(TEST_DIR)/cdma_test: $(TEST_DIR)/cdma_test.o cdma.o walsh.o
	$(CC) $(CFLAGS) -o $@ $^

# --- M3 -------------------------------------------------------------------
$(TEST_DIR)/framing_test: $(TEST_DIR)/framing_test.o framing.o cdma.o walsh.o $(COMMON_OBJS)
	$(CC) $(CFLAGS) -o $@ $^

# --- M4 -------------------------------------------------------------------
$(TEST_DIR)/channel_test: $(TEST_DIR)/channel_test.o channel.o cdma.o walsh.o $(COMMON_OBJS)
	$(CC) $(CFLAGS) -o $@ $^

channel: channel_main.o channel.o cdma.o walsh.o cli.o $(COMMON_OBJS)
	$(CC) $(CFLAGS) -o $@ $^

# --- M5 -------------------------------------------------------------------
sender: sender.o station.o cdma.o walsh.o framing.o cli.o $(COMMON_OBJS)
	$(CC) $(CFLAGS) -o $@ $^

receiver: receiver.o station.o cdma.o walsh.o framing.o cli.o $(COMMON_OBJS)
	$(CC) $(CFLAGS) -o $@ $^

%.o: %.c
	$(CC) $(CFLAGS) $(INCLUDES) -c -o $@ $<

test: all
	@status=0; \
	for t in $(TEST_BINS); do \
		echo "== $$t =="; \
		./$$t || status=1; \
	done; \
	exit $$status

e2e: channel sender receiver
	./tests/e2e_test.sh

eval: $(WALSH_DUMP_BIN) channel sender receiver
	python3 eval/run_eval.py

plots: eval
	python3 eval/plot.py

report:
	latexmk -pdf report/assignment4_report.tex

clean:
	rm -f *.o $(TEST_DIR)/*.o tools/*.o eval/*.o \
	      $(LAB1_DIR)/*.o $(UTILS_DIR)/*.o $(LAB2_DIR)/*.o \
	      $(TEST_DIR)/cli_test $(TEST_DIR)/walsh_test $(TEST_DIR)/cdma_test \
	      $(TEST_DIR)/framing_test $(TEST_DIR)/channel_test \
	      tools/walsh_dump channel sender receiver
