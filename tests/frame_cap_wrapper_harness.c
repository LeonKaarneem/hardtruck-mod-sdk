#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "frame_cap_wrapper.h"

typedef void (*wrapper_function)(void);

static const uint64_t *clock_samples;
static size_t clock_sample_count;
static size_t clock_sample_index;
static int clock_exhausted;
static unsigned long clock_calls;
static unsigned long frames;

uint64_t clock_stub(void)
{
    clock_calls++;
    if (clock_sample_index >= clock_sample_count) {
        clock_exhausted = 1;
        return 0;
    }
    return clock_samples[clock_sample_index++];
}

static int check(int condition, const char *expression, int line)
{
    if (!condition) {
        fprintf(stderr, "check failed at line %d: %s\n", line, expression);
        return 0;
    }
    return 1;
}

#define CHECK(expression) do { if (!check((expression), #expression, __LINE__)) return 0; } while (0)

static uint64_t read_state(size_t offset)
{
    uint64_t value;
    memcpy(&value, (const void *)(uintptr_t)(WRAPPER_BASE + offset), sizeof(value));
    return value;
}

static void write_state(size_t offset, uint64_t value)
{
    memcpy((void *)(uintptr_t)(WRAPPER_BASE + offset), &value, sizeof(value));
}

static void reset_state(void)
{
    memset((void *)(uintptr_t)(WRAPPER_BASE + STATE_OFFSET), 0, 40);
}

static int run_frame(const uint64_t *samples, size_t count)
{
    clock_samples = samples;
    clock_sample_count = count;
    clock_sample_index = 0;
    clock_exhausted = 0;
    ((wrapper_function)(uintptr_t)WRAPPER_BASE)();
    frames++;
    CHECK(!clock_exhausted);
    CHECK(clock_sample_index == count);
    return 1;
}

static int initialization_scenario(void)
{
    static const uint64_t samples[] = {1000000};
    reset_state();
    CHECK(run_frame(samples, 1));
    CHECK(read_state(COUNTER_OFFSET) == 1);
    CHECK(read_state(INITIALIZED_OFFSET) == 1);
    CHECK(read_state(NOW_OFFSET) == 1000000);
    CHECK(read_state(LAST_OFFSET) == 1000000);
    CHECK(read_state(NEXT_OFFSET) == 1000000 + INTERVAL_US);
    return 1;
}

static int ordinary_scenario(void)
{
    static const uint64_t first[] = {0};
    static const uint64_t second[] = {10000, 16667};
    static const uint64_t third[] = {20000, 30000, 33334};
    static const uint64_t fourth[] = {40000, 50001};
    reset_state();
    CHECK(run_frame(first, 1));
    CHECK(read_state(NEXT_OFFSET) == 16667);
    CHECK(run_frame(second, 2));
    CHECK(read_state(NEXT_OFFSET) == 33334);
    CHECK(read_state(LAST_OFFSET) == 16667);
    CHECK(run_frame(third, 3));
    CHECK(read_state(NEXT_OFFSET) == 50001);
    CHECK(read_state(LAST_OFFSET) == 33334);
    CHECK(run_frame(fourth, 2));
    CHECK(read_state(NEXT_OFFSET) == 66668);
    CHECK(read_state(LAST_OFFSET) == 50001);
    CHECK(read_state(COUNTER_OFFSET) == 4);
    return 1;
}

static int missed_frame_scenario(void)
{
    static const uint64_t first[] = {0};
    static const uint64_t second[] = {20000};
    static const uint64_t third[] = {100000};
    reset_state();
    CHECK(run_frame(first, 1));
    CHECK(run_frame(second, 1));
    CHECK(read_state(NEXT_OFFSET) == 36667);
    CHECK(read_state(LAST_OFFSET) == 20000);
    CHECK(run_frame(third, 1));
    CHECK(read_state(NEXT_OFFSET) == 116667);
    CHECK(read_state(LAST_OFFSET) == 100000);
    CHECK(read_state(COUNTER_OFFSET) == 3);
    return 1;
}

static int exact_gap_scenario(void)
{
    static const uint64_t samples[] = {100000 + LONG_GAP_US, 390000, 400000};
    reset_state();
    write_state(INITIALIZED_OFFSET, 1);
    write_state(LAST_OFFSET, 100000);
    write_state(NEXT_OFFSET, 400000);
    CHECK(run_frame(samples, 3));
    CHECK(read_state(NEXT_OFFSET) == 416667);
    CHECK(read_state(LAST_OFFSET) == 400000);
    CHECK(read_state(COUNTER_OFFSET) == 1);
    return 1;
}

static int overlong_gap_scenario(void)
{
    static const uint64_t samples[] = {100000 + LONG_GAP_US + 1};
    reset_state();
    write_state(INITIALIZED_OFFSET, 1);
    write_state(LAST_OFFSET, 100000);
    write_state(NEXT_OFFSET, 400000);
    CHECK(run_frame(samples, 1));
    CHECK(read_state(NEXT_OFFSET) == samples[0] + INTERVAL_US);
    CHECK(read_state(LAST_OFFSET) == samples[0]);
    CHECK(read_state(COUNTER_OFFSET) == 1);
    return 1;
}

static int deadline_overflow_scenario(void)
{
    static const uint64_t first[] = {UINT64_MAX - 9999};
    static const uint64_t second[] = {UINT64_MAX - 4999};
    reset_state();
    CHECK(run_frame(first, 1));
    CHECK(read_state(NEXT_OFFSET) == 6667);
    CHECK(run_frame(second, 1));
    CHECK(read_state(NEXT_OFFSET) == 11667);
    CHECK(read_state(LAST_OFFSET) == UINT64_MAX - 4999);
    CHECK(read_state(COUNTER_OFFSET) == 2);
    return 1;
}

static int waiting_wrap_scenario(void)
{
    static const uint64_t samples[] = {UINT64_MAX - 14999, 5000};
    reset_state();
    write_state(INITIALIZED_OFFSET, 1);
    write_state(LAST_OFFSET, UINT64_MAX - 19999);
    write_state(NEXT_OFFSET, UINT64_MAX - 999);
    CHECK(run_frame(samples, 2));
    CHECK(read_state(NEXT_OFFSET) == 21667);
    CHECK(read_state(LAST_OFFSET) == 5000);
    CHECK(read_state(COUNTER_OFFSET) == 1);
    return 1;
}

static int install_wrapper(void)
{
    if (*(unsigned char *)(uintptr_t)CLOCK_VA != 0xe9 ||
            *(unsigned char *)(uintptr_t)SYS_FRAME_VA != 0xc3 ||
            *(unsigned char *)(uintptr_t)RESUME_VA != 0xc3) {
        fprintf(stderr, "linked stubs are not at the required addresses\n");
        return 0;
    }
    return 1;
}

#define RUN_SCENARIO(function) do { scenarios++; if (!function()) return 1; } while (0)

int main(void)
{
    unsigned scenarios = 0;
    if (!install_wrapper()) {
        return 2;
    }
    RUN_SCENARIO(initialization_scenario);
    RUN_SCENARIO(ordinary_scenario);
    RUN_SCENARIO(missed_frame_scenario);
    RUN_SCENARIO(exact_gap_scenario);
    RUN_SCENARIO(overlong_gap_scenario);
    RUN_SCENARIO(deadline_overflow_scenario);
    RUN_SCENARIO(waiting_wrap_scenario);
    printf("machine-code-executed scenarios=%u frames=%lu clock_calls=%lu\n",
           scenarios, frames, clock_calls);
    printf("wrapper-base=0x%lx wrapper-bytes=%u\n",
           (unsigned long)WRAPPER_BASE, (unsigned)WRAPPER_CODE_SIZE);
    return 0;
}
