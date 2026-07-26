#include "openrdkc/framing.h"

#include <stdlib.h>
#include <string.h>

static const uint8_t FRAME_SYNC[ORDKC_FRAME_SYNC_SIZE] = {
    0xAAU, 0x55U, 0xAAU, 0x55U
};

typedef enum parser_state {
    PARSER_SYNC = 0,
    PARSER_LENGTH,
    PARSER_PAYLOAD,
    PARSER_MESSAGE_TYPE,
    PARSER_SEQUENCE
} parser_state_t;

struct ordkc_frame_parser {
    parser_state_t state;
    size_t sync_match;
    ordkc_frame_t current;
    size_t payload_index;
    size_t sequence_index;
    uint32_t sequence_accumulator;
    bool have_last_sequence;
    uint8_t last_message_type;
    uint32_t last_sequence;
    ordkc_frame_parser_stats_t stats;
};

static void reset_current_frame(ordkc_frame_parser_t *parser)
{
    parser->state = PARSER_SYNC;
    parser->sync_match = 0U;
    memset(&parser->current, 0, sizeof(parser->current));
    parser->payload_index = 0U;
    parser->sequence_index = 0U;
    parser->sequence_accumulator = 0U;
}

static void consume_sync_byte(ordkc_frame_parser_t *parser, uint8_t value)
{
    if (value == FRAME_SYNC[parser->sync_match]) {
        parser->sync_match++;
        if (parser->sync_match == ORDKC_FRAME_SYNC_SIZE) {
            parser->sync_match = 0U;
            parser->state = PARSER_LENGTH;
        }
        return;
    }
    parser->sync_match = value == FRAME_SYNC[0] ? 1U : 0U;
}

bool ordkc_message_type_is_valid(uint8_t message_type)
{
    return message_type >= (uint8_t)ORDKC_MESSAGE_CMD
        && message_type <= (uint8_t)ORDKC_MESSAGE_CONTROL;
}

ordkc_frame_parser_t *ordkc_frame_parser_create(void)
{
    ordkc_frame_parser_t *parser = calloc(1U, sizeof(*parser));
    if (parser != NULL) {
        reset_current_frame(parser);
    }
    return parser;
}

void ordkc_frame_parser_reset(ordkc_frame_parser_t *parser)
{
    if (parser == NULL) {
        return;
    }
    memset(&parser->stats, 0, sizeof(parser->stats));
    parser->have_last_sequence = false;
    parser->last_message_type = 0U;
    parser->last_sequence = 0U;
    reset_current_frame(parser);
}

void ordkc_frame_parser_destroy(ordkc_frame_parser_t *parser)
{
    free(parser);
}

int ordkc_frame_parser_feed(
    ordkc_frame_parser_t *parser,
    const uint8_t *data,
    size_t data_len,
    ordkc_frame_callback_t callback,
    void *context,
    size_t *out_frames_emitted)
{
    size_t emitted = 0U;
    size_t index;

    if (parser == NULL || (data == NULL && data_len != 0U)) {
        return -1;
    }

    for (index = 0U; index < data_len; index++) {
        uint8_t value = data[index];
        parser->stats.bytes_consumed++;

        switch (parser->state) {
        case PARSER_SYNC:
            consume_sync_byte(parser, value);
            break;

        case PARSER_LENGTH:
            if (value == 0U || value > ORDKC_FRAME_MAX_PAYLOAD) {
                parser->stats.invalid_lengths++;
                reset_current_frame(parser);
                consume_sync_byte(parser, value);
                break;
            }
            parser->current.payload_len = value;
            parser->payload_index = 0U;
            parser->state = PARSER_PAYLOAD;
            break;

        case PARSER_PAYLOAD:
            parser->current.payload[parser->payload_index++] = value;
            if (parser->payload_index == parser->current.payload_len) {
                parser->state = PARSER_MESSAGE_TYPE;
            }
            break;

        case PARSER_MESSAGE_TYPE:
            if (!ordkc_message_type_is_valid(value)) {
                parser->stats.unknown_message_types++;
                reset_current_frame(parser);
                consume_sync_byte(parser, value);
                break;
            }
            parser->current.message_type = value;
            parser->sequence_index = 0U;
            parser->sequence_accumulator = 0U;
            parser->state = PARSER_SEQUENCE;
            break;

        case PARSER_SEQUENCE:
            parser->sequence_accumulator =
                (parser->sequence_accumulator << 8U) | (uint32_t)value;
            parser->sequence_index++;
            if (parser->sequence_index == 3U) {
                parser->current.sequence =
                    parser->sequence_accumulator & ORDKC_FRAME_SEQUENCE_MAX;
                parser->current.duplicate_sequence =
                    parser->have_last_sequence
                    && parser->last_message_type == parser->current.message_type
                    && parser->last_sequence == parser->current.sequence;
                if (parser->current.duplicate_sequence) {
                    parser->stats.duplicate_sequences++;
                }
                parser->have_last_sequence = true;
                parser->last_message_type = parser->current.message_type;
                parser->last_sequence = parser->current.sequence;
                parser->stats.frames_emitted++;
                emitted++;
                if (callback != NULL) {
                    callback(&parser->current, context);
                }
                reset_current_frame(parser);
            }
            break;

        default:
            reset_current_frame(parser);
            break;
        }
    }

    if (out_frames_emitted != NULL) {
        *out_frames_emitted = emitted;
    }
    return 0;
}

void ordkc_frame_parser_get_stats(
    const ordkc_frame_parser_t *parser,
    ordkc_frame_parser_stats_t *out_stats)
{
    if (parser == NULL || out_stats == NULL) {
        return;
    }
    *out_stats = parser->stats;
}

int ordkc_build_stream_frame(
    const uint8_t *payload,
    size_t payload_len,
    uint8_t message_type,
    uint32_t sequence,
    uint8_t *output,
    size_t output_capacity,
    size_t *out_len)
{
    size_t required = ORDKC_FRAME_SYNC_SIZE + 1U + payload_len + 1U + 3U;
    size_t position = 0U;

    if (payload == NULL || output == NULL || out_len == NULL
        || payload_len == 0U || payload_len > ORDKC_FRAME_MAX_PAYLOAD
        || !ordkc_message_type_is_valid(message_type)
        || sequence > ORDKC_FRAME_SEQUENCE_MAX
        || output_capacity < required) {
        return -1;
    }

    memcpy(output + position, FRAME_SYNC, ORDKC_FRAME_SYNC_SIZE);
    position += ORDKC_FRAME_SYNC_SIZE;
    output[position++] = (uint8_t)payload_len;
    memcpy(output + position, payload, payload_len);
    position += payload_len;
    output[position++] = message_type;
    output[position++] = (uint8_t)((sequence >> 16U) & 0xFFU);
    output[position++] = (uint8_t)((sequence >> 8U) & 0xFFU);
    output[position++] = (uint8_t)(sequence & 0xFFU);
    *out_len = position;
    return 0;
}

int ordkc_build_control_frame(
    uint8_t module_id,
    uint8_t control_code,
    uint8_t *output,
    size_t output_capacity,
    size_t *out_len)
{
    if (output == NULL || out_len == NULL || output_capacity < 6U) {
        return -1;
    }
    memcpy(output, FRAME_SYNC, ORDKC_FRAME_SYNC_SIZE);
    output[4] = module_id;
    output[5] = control_code;
    *out_len = 6U;
    return 0;
}

int ordkc_parse_control_frame(
    const uint8_t *input,
    size_t input_len,
    ordkc_control_frame_t *out_frame)
{
    size_t name_len;

    if (input == NULL || out_frame == NULL || input_len < 6U
        || memcmp(input, FRAME_SYNC, ORDKC_FRAME_SYNC_SIZE) != 0) {
        return -1;
    }

    memset(out_frame, 0, sizeof(*out_frame));
    out_frame->module_id = input[4];
    out_frame->control_code = input[5];
    if (out_frame->control_code != (uint8_t)ORDKC_CONTROL_MODULE_NAME) {
        return input_len == 6U ? 0 : -1;
    }
    if (input_len < 7U) {
        return -1;
    }
    name_len = input[6];
    if (name_len == 0U || name_len > ORDKC_CONTROL_NAME_MAX
        || input_len != 7U + name_len) {
        return -1;
    }
    memcpy(out_frame->module_name, input + 7U, name_len);
    out_frame->module_name[name_len] = '\0';
    out_frame->module_name_len = (uint8_t)name_len;
    return 0;
}

