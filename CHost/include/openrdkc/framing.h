#ifndef OPENRDKC_FRAMING_H
#define OPENRDKC_FRAMING_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define ORDKC_FRAME_SYNC_SIZE 4U
#define ORDKC_FRAME_MAX_PAYLOAD 200U
#define ORDKC_FRAME_SEQUENCE_MAX 0xFFFFFFU
#define ORDKC_CONTROL_NAME_MAX 64U

typedef enum ordkc_message_type {
    ORDKC_MESSAGE_CMD = 0x01,
    ORDKC_MESSAGE_TEST = 0x02,
    ORDKC_MESSAGE_TELEMETRY = 0x03,
    ORDKC_MESSAGE_CONTROL = 0x04
} ordkc_message_type_t;

typedef enum ordkc_control_code {
    ORDKC_CONTROL_HELLO = 0x01,
    ORDKC_CONTROL_MODULE_QUERY = 0x04,
    ORDKC_CONTROL_MODULE_NAME = 0x05,
    ORDKC_CONTROL_ACK = 0x06
} ordkc_control_code_t;

typedef struct ordkc_frame {
    uint8_t payload[ORDKC_FRAME_MAX_PAYLOAD];
    uint8_t payload_len;
    uint8_t message_type;
    uint32_t sequence;
    bool duplicate_sequence;
} ordkc_frame_t;

typedef struct ordkc_control_frame {
    uint8_t module_id;
    uint8_t control_code;
    uint8_t module_name_len;
    char module_name[ORDKC_CONTROL_NAME_MAX + 1U];
} ordkc_control_frame_t;

typedef struct ordkc_frame_parser_stats {
    uint64_t bytes_consumed;
    uint64_t frames_emitted;
    uint64_t invalid_lengths;
    uint64_t unknown_message_types;
    uint64_t duplicate_sequences;
} ordkc_frame_parser_stats_t;

typedef struct ordkc_frame_parser ordkc_frame_parser_t;
typedef void (*ordkc_frame_callback_t)(const ordkc_frame_t *frame, void *context);

ordkc_frame_parser_t *ordkc_frame_parser_create(void);
void ordkc_frame_parser_reset(ordkc_frame_parser_t *parser);
void ordkc_frame_parser_destroy(ordkc_frame_parser_t *parser);

int ordkc_frame_parser_feed(
    ordkc_frame_parser_t *parser,
    const uint8_t *data,
    size_t data_len,
    ordkc_frame_callback_t callback,
    void *context,
    size_t *out_frames_emitted);

void ordkc_frame_parser_get_stats(
    const ordkc_frame_parser_t *parser,
    ordkc_frame_parser_stats_t *out_stats);

int ordkc_build_stream_frame(
    const uint8_t *payload,
    size_t payload_len,
    uint8_t message_type,
    uint32_t sequence,
    uint8_t *output,
    size_t output_capacity,
    size_t *out_len);

int ordkc_build_control_frame(
    uint8_t module_id,
    uint8_t control_code,
    uint8_t *output,
    size_t output_capacity,
    size_t *out_len);

int ordkc_parse_control_frame(
    const uint8_t *input,
    size_t input_len,
    ordkc_control_frame_t *out_frame);

bool ordkc_message_type_is_valid(uint8_t message_type);

#ifdef __cplusplus
}
#endif

#endif

