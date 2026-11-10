#include "test_util.h"
#include "delayq.h"

#include <stdint.h>
#include <string.h>

/* A frame whose first byte is `id`, so each popped item can be identified. */
static void make_frame(uint8_t *f, uint8_t id) {
    memset(f, 0, FRAME_SIZE);
    f[0] = id;
}

int main(void) {
    TEST_BEGIN("D1 delayq releases in due order, holds future items");
    {
        delayq_t q;
        delayq_init(&q);
        uint8_t f[FRAME_SIZE];
        int dests[2] = {3, 7};
        assert_eq_i64(delayq_next_due(&q), (long long)UINT64_MAX, "empty queue: next_due is UINT64_MAX");

        make_frame(f, 30); assert_eq_i64(delayq_push(&q, 30, f, dests, 2), 0, "push due 30 ok");
        make_frame(f, 10); assert_eq_i64(delayq_push(&q, 10, f, dests, 2), 0, "push due 10 ok");
        make_frame(f, 20); assert_eq_i64(delayq_push(&q, 20, f, dests, 2), 0, "push due 20 ok");
        assert_eq_i64(delayq_next_due(&q), 10, "next_due is earliest (10)");

        delayq_item_t it;
        assert_eq_i64(delayq_pop_due(&q, 5, &it), 0, "nothing due at now=5");
        assert_eq_i64(delayq_pop_due(&q, 10, &it), 1, "due 10 released at now=10");
        assert_eq_i64(it.frame[0], 10, "first released is the due-10 frame");
        assert_eq_i64(it.due, 10, "item carries its due time");
        assert_eq_i64(it.ndest, 2, "item carries dest count");
        assert_eq_i64(it.dests[0], 3, "item carries dest[0]");
        assert_eq_i64(it.dests[1], 7, "item carries dest[1]");
        uint8_t want10[FRAME_SIZE];
        make_frame(want10, 10);
        assert_eq_mem(it.frame, want10, FRAME_SIZE, "frame bytes copied intact");

        assert_eq_i64(delayq_pop_due(&q, 25, &it), 1, "due 20 released at now=25");
        assert_eq_i64(it.frame[0], 20, "second released is the due-20 frame");
        assert_eq_i64(delayq_pop_due(&q, 25, &it), 0, "due 30 still held at now=25");
        assert_eq_i64(delayq_next_due(&q), 30, "next_due is 30 while it is held");
        assert_eq_i64(delayq_pop_due(&q, 30, &it), 1, "due 30 released at now=30");
        assert_eq_i64(it.frame[0], 30, "third released is the due-30 frame");
        assert_eq_i64(delayq_next_due(&q), (long long)UINT64_MAX, "drained: next_due is UINT64_MAX");
    }
    {
        delayq_t q;
        delayq_init(&q);
        uint8_t f[FRAME_SIZE];
        int ok = 1;
        for (int i = 0; i < DELAYQ_SIZE; i++) {
            make_frame(f, (uint8_t)i);
            if (delayq_push(&q, (uint64_t)i, f, NULL, 0) != 0) ok = 0;
        }
        assert_true(ok, "64 pushes succeed");
        make_frame(f, 99);
        assert_eq_i64(delayq_push(&q, 1000, f, NULL, 0), -1, "push into full queue returns -1");
        assert_eq_i64(delayq_next_due(&q), 0, "full queue unchanged: earliest still 0");
    }

    return test_summary();
}
