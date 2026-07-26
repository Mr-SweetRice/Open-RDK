#include "openrdkc/framing.h"

#include <assert.h>
#include <stdint.h>
#include <stdio.h>

static uint32_t next_random(uint32_t *state)
{
    uint32_t value = *state;
    value ^= value << 13U;
    value ^= value >> 17U;
    value ^= value << 5U;
    *state = value;
    return value;
}

static void count_frame(const ordkc_frame_t *frame, void *context)
{
    size_t *count = context;
    assert(frame->payload_len >= 1U);
    assert(frame->payload_len <= ORDKC_FRAME_MAX_PAYLOAD);
    assert(ordkc_message_type_is_valid(frame->message_type));
    assert(frame->sequence <= ORDKC_FRAME_SEQUENCE_MAX);
    (*count)++;
}

int main(void)
{
    ordkc_frame_parser_t *parser = ordkc_frame_parser_create();
    uint32_t random_state = 0xC0FFEE42U;
    uint8_t buffer[257];
    size_t callbacks = 0U;
    size_t iteration;

    assert(parser != NULL);
    for (iteration = 0U; iteration < 20000U; iteration++) {
        size_t length = (size_t)(next_random(&random_state) % sizeof(buffer));
        size_t index;
        for (index = 0U; index < length; index++) {
            buffer[index] = (uint8_t)next_random(&random_state);
        }
        assert(ordkc_frame_parser_feed(
            parser, buffer, length, count_frame, &callbacks, NULL) == 0);
        if ((iteration % 257U) == 0U) {
            ordkc_frame_parser_reset(parser);
        }
    }
    ordkc_frame_parser_destroy(parser);
    printf("fuzz_framing_smoke: ok (%zu callbacks)\n", callbacks);
    return 0;
}

