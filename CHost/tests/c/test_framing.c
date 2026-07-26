#include "openrdkc/framing.h"

#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef struct capture {
    ordkc_frame_t frames[16];
    size_t count;
} capture_t;

static void capture_frame(const ordkc_frame_t *frame, void *context)
{
    capture_t *capture = context;
    assert(capture->count < 16U);
    capture->frames[capture->count++] = *frame;
}

static size_t build(
    const char *text,
    uint8_t type,
    uint32_t sequence,
    uint8_t *output,
    size_t capacity)
{
    size_t output_len = 0U;
    assert(ordkc_build_stream_frame(
        (const uint8_t *)text,
        strlen(text),
        type,
        sequence,
        output,
        capacity,
        &output_len) == 0);
    return output_len;
}

static void test_fixture_hex(void)
{
    static const uint8_t expected[] = {
        0xAA, 0x55, 0xAA, 0x55, 0x0B,
        'G', 'E', 'T', ' ', 'P', 'I', 'D', ' ', 'R', 'P', 'M',
        0x01, 0x00, 0x00, 0x01
    };
    uint8_t output[256];
    size_t output_len = build(
        "GET PID RPM", ORDKC_MESSAGE_CMD, 1U, output, sizeof(output));
    assert(output_len == sizeof(expected));
    assert(memcmp(output, expected, sizeof(expected)) == 0);
}

static void test_chunking_noise_and_partial(void)
{
    uint8_t frame[256];
    uint8_t noisy[300] = {0x00, 0x01, 0x02, 0x7F};
    size_t frame_len = build(
        "OK", ORDKC_MESSAGE_CMD, 9U, frame, sizeof(frame));
    ordkc_frame_parser_t *parser = ordkc_frame_parser_create();
    capture_t capture = {0};
    size_t emitted = 0U;

    assert(parser != NULL);
    memcpy(noisy + 4U, frame, frame_len);
    assert(ordkc_frame_parser_feed(
        parser, noisy, 3U, capture_frame, &capture, &emitted) == 0);
    assert(emitted == 0U);
    assert(ordkc_frame_parser_feed(
        parser, noisy + 3U, 4U, capture_frame, &capture, &emitted) == 0);
    assert(emitted == 0U);
    assert(ordkc_frame_parser_feed(
        parser,
        noisy + 7U,
        frame_len - 3U,
        capture_frame,
        &capture,
        &emitted) == 0);
    assert(emitted == 1U);
    assert(capture.count == 1U);
    assert(capture.frames[0].sequence == 9U);
    assert(capture.frames[0].payload_len == 2U);
    assert(memcmp(capture.frames[0].payload, "OK", 2U) == 0);
    ordkc_frame_parser_destroy(parser);
}

static void test_invalid_length_and_recovery(void)
{
    uint8_t input[300] = {0xAA, 0x55, 0xAA, 0x55, 0x00};
    uint8_t valid[256];
    size_t valid_len = build(
        "RECOVER", ORDKC_MESSAGE_TEST, 10U, valid, sizeof(valid));
    ordkc_frame_parser_t *parser = ordkc_frame_parser_create();
    ordkc_frame_parser_stats_t stats;
    capture_t capture = {0};

    assert(parser != NULL);
    memcpy(input + 5U, valid, valid_len);
    assert(ordkc_frame_parser_feed(
        parser, input, valid_len + 5U, capture_frame, &capture, NULL) == 0);
    assert(capture.count == 1U);
    ordkc_frame_parser_get_stats(parser, &stats);
    assert(stats.invalid_lengths == 1U);
    ordkc_frame_parser_destroy(parser);
}

static void test_unknown_type_and_recovery(void)
{
    uint8_t invalid[32] = {
        0xAA, 0x55, 0xAA, 0x55, 0x02, 'O', 'K', 0xFF,
        0x00, 0x00, 0x01
    };
    uint8_t valid[256];
    size_t valid_len = build(
        "NEXT", ORDKC_MESSAGE_CMD, 11U, valid, sizeof(valid));
    ordkc_frame_parser_t *parser = ordkc_frame_parser_create();
    ordkc_frame_parser_stats_t stats;
    capture_t capture = {0};

    assert(parser != NULL);
    assert(ordkc_frame_parser_feed(
        parser, invalid, sizeof(invalid), capture_frame, &capture, NULL) == 0);
    assert(ordkc_frame_parser_feed(
        parser, valid, valid_len, capture_frame, &capture, NULL) == 0);
    assert(capture.count == 1U);
    ordkc_frame_parser_get_stats(parser, &stats);
    assert(stats.unknown_message_types == 1U);
    ordkc_frame_parser_destroy(parser);
}

static void test_duplicates_and_wrap(void)
{
    uint8_t frame[256];
    size_t frame_len;
    ordkc_frame_parser_t *parser = ordkc_frame_parser_create();
    ordkc_frame_parser_stats_t stats;
    capture_t capture = {0};

    assert(parser != NULL);
    frame_len = build(
        "A", ORDKC_MESSAGE_TELEMETRY, ORDKC_FRAME_SEQUENCE_MAX,
        frame, sizeof(frame));
    assert(ordkc_frame_parser_feed(
        parser, frame, frame_len, capture_frame, &capture, NULL) == 0);
    assert(ordkc_frame_parser_feed(
        parser, frame, frame_len, capture_frame, &capture, NULL) == 0);
    frame_len = build(
        "B", ORDKC_MESSAGE_TELEMETRY, 0U, frame, sizeof(frame));
    assert(ordkc_frame_parser_feed(
        parser, frame, frame_len, capture_frame, &capture, NULL) == 0);

    assert(capture.count == 3U);
    assert(!capture.frames[0].duplicate_sequence);
    assert(capture.frames[1].duplicate_sequence);
    assert(!capture.frames[2].duplicate_sequence);
    assert(capture.frames[2].sequence == 0U);
    ordkc_frame_parser_get_stats(parser, &stats);
    assert(stats.duplicate_sequences == 1U);
    ordkc_frame_parser_destroy(parser);
}

static void test_control_frames(void)
{
    uint8_t output[128];
    size_t output_len = 0U;
    ordkc_control_frame_t parsed;
    static const uint8_t module_reply[] = {
        0xAA, 0x55, 0xAA, 0x55, 0x13, 0x05, 0x0C,
        'c', 'o', 'l', 'o', 'r', '_', 'm', 'o', 'd', 'u', 'l', 'e'
    };

    assert(ordkc_build_control_frame(
        0x00, ORDKC_CONTROL_HELLO, output, sizeof(output), &output_len) == 0);
    assert(output_len == 6U);
    assert(memcmp(output, "\xAA\x55\xAA\x55\x00\x01", 6U) == 0);

    assert(ordkc_parse_control_frame(
        module_reply, sizeof(module_reply), &parsed) == 0);
    assert(parsed.module_id == 0x13U);
    assert(parsed.control_code == ORDKC_CONTROL_MODULE_NAME);
    assert(strcmp(parsed.module_name, "color_module") == 0);
}

static void test_argument_validation(void)
{
    uint8_t output[16];
    size_t output_len = 0U;
    ordkc_frame_parser_t *parser = ordkc_frame_parser_create();

    assert(parser != NULL);
    assert(ordkc_frame_parser_feed(NULL, output, 1U, NULL, NULL, NULL) == -1);
    assert(ordkc_frame_parser_feed(parser, NULL, 1U, NULL, NULL, NULL) == -1);
    assert(ordkc_build_stream_frame(
        NULL, 1U, ORDKC_MESSAGE_CMD, 0U,
        output, sizeof(output), &output_len) == -1);
    assert(ordkc_build_stream_frame(
        (const uint8_t *)"A", 1U, 0xFFU, 0U,
        output, sizeof(output), &output_len) == -1);
    ordkc_frame_parser_destroy(parser);
}

int main(void)
{
    test_fixture_hex();
    test_chunking_noise_and_partial();
    test_invalid_length_and_recovery();
    test_unknown_type_and_recovery();
    test_duplicates_and_wrap();
    test_control_frames();
    test_argument_validation();
    puts("test_framing: ok");
    return 0;
}

