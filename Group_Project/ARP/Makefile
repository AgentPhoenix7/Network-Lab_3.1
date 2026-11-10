CC     := gcc
CFLAGS := -Wall -Wextra -std=c11 -g
LAB    := ../..
INC    := -Isrc -I$(LAB)/lab1 -I$(LAB)/lab1/error_inject -I$(LAB)/lab2 -I$(LAB)/lab4 -I$(LAB)/lab4/tests -I$(LAB)/utils

# Lab code is linked, never copied (plan.md section 4).
LAB_SRCS := $(LAB)/lab1/scheme.c $(LAB)/lab1/error.c $(LAB)/lab1/error_inject/bit_flip.c \
            $(LAB)/lab2/timer.c $(LAB)/lab4/cli.c $(LAB)/utils/utils.c
LAB_OBJS := $(patsubst $(LAB)/%.c,build/lab/%.o,$(LAB_SRCS))

# Each test links only the modules it needs, so half-written files elsewhere never break it.
BASE  := addr ether
DEPS_arp_cache := $(BASE)
DEPS_arp_pkt   := $(BASE)
DEPS_arp       := $(BASE) arp_pkt arp_cache
DEPS_route     := $(BASE)
DEPS_ipv4      := $(BASE)
DEPS_net       := $(BASE) arp_pkt arp_cache arp route ipv4
DEPS_switch    := $(BASE)
DEPS_channel   := $(BASE) switch impair delayq simclock telemetry
DEPS_conf      := $(BASE)

TESTS := $(patsubst tests/%.c,build/tests/%,$(wildcard tests/*_test.c))

.PHONY: all test clean
all: $(TESTS) $(wildcard src/channel_main.c src/station.c)
test: $(TESTS)
	@for t in $(TESTS); do echo "== $$t"; ./$$t || exit 1; done

build/lab/%.o: $(LAB)/%.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) $(INC) -c $< -o $@
build/src/%.o: src/%.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) $(INC) -c $< -o $@
build/tests/%.o: tests/%.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) $(INC) -c $< -o $@

# build/tests/foo_test = foo_test.o + src/foo.o + src/<DEPS_foo>.o + lab objects
.SECONDEXPANSION:
build/tests/%_test: build/tests/%_test.o build/src/$$*.o $$(addprefix build/src/,$$(addsuffix .o,$$(DEPS_$$*))) $(LAB_OBJS)
	$(CC) $(CFLAGS) -o $@ $^ -lm

# Programs. Each links every src/*.c except the other program's main.
LIB_SRCS := $(filter-out src/channel_main.c src/station.c,$(wildcard src/*.c))
LIB_OBJS := $(patsubst src/%.c,build/src/%.o,$(LIB_SRCS))
channel: build/src/channel_main.o $(LIB_OBJS) $(LAB_OBJS)
	$(CC) $(CFLAGS) -o $@ $^ -lm
station: build/src/station.o $(LIB_OBJS) $(LAB_OBJS)
	$(CC) $(CFLAGS) -o $@ $^ -lm

clean:
	rm -rf build channel station
