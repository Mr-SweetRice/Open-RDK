#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "driver/usb_serial_jtag.h"
#include "esp_err.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define HELLO_BYTE ((uint8_t)0x01)
#define ACK_BYTE ((uint8_t)0x06)
#define MODULE_QUERY_BYTE ((uint8_t)0x04)
#define MODULE_INFO_PREFIX_BYTE ((uint8_t)0x05)
#define HOST_MODULE_ID ((uint8_t)0x00)
#define TEST_MODULE_ID ((uint8_t)0x11)

#define MESSAGE_TYPE_CMD ((uint8_t)0x01)
#define MESSAGE_TYPE_TEST ((uint8_t)0x02)
#define MESSAGE_TYPE_TELEMETRY ((uint8_t)0x03)
#define TELEMETRY_INTERVAL_MS 20U
#define TELEMETRY_INTERVAL_US ((int64_t)TELEMETRY_INTERVAL_MS * 1000LL)
#define CMD_TELEMETRY_START "TELEMETRY_START"
#define CMD_TELEMETRY_SYNC "TELEMETRY_SYNC"
#define CMD_TELEMETRY_STOP "TELEMETRY_STOP"
#define ACK_TELEMETRY_STARTED "TELEMETRY STARTED"
#define ACK_TELEMETRY_SYNCED "TELEMETRY SYNCED"
#define ACK_TELEMETRY_STOPPED "TELEMETRY STOPPED"

#define MODULE_NAME "test_module"
#define MODULE_NAME_LEN ((uint8_t)(sizeof(MODULE_NAME) - 1U))

#define FRAME_SYNC_LEN 4U
#define STREAM_HEADER_LEN (FRAME_SYNC_LEN + 1U)
#define STREAM_SEQUENCE_BYTES 3U
#define STREAM_TRAILER_LEN (1U + STREAM_SEQUENCE_BYTES)
#define STREAM_MAX_MESSAGE_LEN 200U
#define STREAM_SEQUENCE_MAX 16777215U
#define RX_BUFFER_SIZE 512U

static const uint8_t FRAME_SYNC_BYTES[FRAME_SYNC_LEN] = {0xAA, 0x55, 0xAA, 0x55};
static bool g_telemetry_active = false;
static bool g_has_last_host_seq = false;
static uint32_t g_last_host_seq = 0U;
static uint8_t g_last_ack_type = MESSAGE_TYPE_TEST;
static char g_last_ack[STREAM_MAX_MESSAGE_LEN + 1U] = "I RECIEVED TEST";
static uint32_t g_telemetry_seq = 0U;
static uint32_t g_telemetry_counter = 0U;
static int64_t g_next_telemetry_due_us = 0;
static bool g_has_host_time_offset = false;
static int64_t g_host_time_offset_ms = 0;

static void update_host_time_offset_ms(int64_t host_epoch_ms)
{
    if (host_epoch_ms <= 0) {
        return;
    }
    int64_t module_epoch_ms = esp_timer_get_time() / 1000;
    int64_t measured_offset_ms = host_epoch_ms - module_epoch_ms;
    if (!g_has_host_time_offset) {
        g_host_time_offset_ms = measured_offset_ms;
        g_has_host_time_offset = true;
        return;
    }
    // First-order smoothing keeps periodic sync from injecting jitter spikes.
    g_host_time_offset_ms = ((g_host_time_offset_ms * 7LL) + measured_offset_ms) / 8LL;
}

static void write_bytes(const uint8_t *data, size_t len)
{
    if (data == NULL || len == 0U) {
        return;
    }
    (void)usb_serial_jtag_write_bytes(data, len, pdMS_TO_TICKS(50));
}

static void send_legacy_framed_byte(uint8_t module_id, uint8_t payload)
{
    uint8_t frame[FRAME_SYNC_LEN + 2U] = {
        FRAME_SYNC_BYTES[0],
        FRAME_SYNC_BYTES[1],
        FRAME_SYNC_BYTES[2],
        FRAME_SYNC_BYTES[3],
        module_id,
        payload,
    };
    write_bytes(frame, sizeof(frame));
}

static void send_module_info_frame(void)
{
    uint8_t header[FRAME_SYNC_LEN + 3U] = {
        FRAME_SYNC_BYTES[0],
        FRAME_SYNC_BYTES[1],
        FRAME_SYNC_BYTES[2],
        FRAME_SYNC_BYTES[3],
        TEST_MODULE_ID,
        MODULE_INFO_PREFIX_BYTE,
        MODULE_NAME_LEN,
    };
    write_bytes(header, sizeof(header));
    write_bytes((const uint8_t *)MODULE_NAME, MODULE_NAME_LEN);
}

static uint32_t normalize_sequence(uint32_t sequence)
{
    return (uint32_t)(sequence % (STREAM_SEQUENCE_MAX + 1U));
}

static void send_stream_frame(const char *message, uint8_t message_type, uint32_t sequence)
{
    if (message == NULL) {
        return;
    }

    size_t message_len = strlen(message);
    if (message_len > STREAM_MAX_MESSAGE_LEN) {
        message_len = STREAM_MAX_MESSAGE_LEN;
    }

    uint8_t frame[STREAM_HEADER_LEN + STREAM_MAX_MESSAGE_LEN + STREAM_TRAILER_LEN] = {0};
    frame[0] = FRAME_SYNC_BYTES[0];
    frame[1] = FRAME_SYNC_BYTES[1];
    frame[2] = FRAME_SYNC_BYTES[2];
    frame[3] = FRAME_SYNC_BYTES[3];
    frame[4] = (uint8_t)message_len;

    if (message_len > 0U) {
        memcpy(&frame[STREAM_HEADER_LEN], (const uint8_t *)message, message_len);
    }

    uint32_t seq = normalize_sequence(sequence);
    size_t trailer_idx = STREAM_HEADER_LEN + message_len;
    frame[trailer_idx] = message_type;
    frame[trailer_idx + 1U] = (uint8_t)((seq >> 16U) & 0xFFU);
    frame[trailer_idx + 2U] = (uint8_t)((seq >> 8U) & 0xFFU);
    frame[trailer_idx + 3U] = (uint8_t)(seq & 0xFFU);

    write_bytes(frame, trailer_idx + STREAM_TRAILER_LEN);
}

static int find_sync_index(const uint8_t *buffer, size_t len)
{
    if (buffer == NULL || len < FRAME_SYNC_LEN) {
        return -1;
    }

    for (size_t i = 0; i <= len - FRAME_SYNC_LEN; i++) {
        if (memcmp(&buffer[i], FRAME_SYNC_BYTES, FRAME_SYNC_LEN) == 0) {
            return (int)i;
        }
    }
    return -1;
}

static void consume_bytes(uint8_t *buffer, size_t *len, size_t count)
{
    if (buffer == NULL || len == NULL || *len == 0U || count == 0U) {
        return;
    }
    if (count >= *len) {
        *len = 0U;
        return;
    }

    memmove(buffer, buffer + count, *len - count);
    *len -= count;
}

static void trim_to_sync_prefix(uint8_t *buffer, size_t *len)
{
    if (buffer == NULL || len == NULL || *len == 0U) {
        return;
    }

    size_t keep = 0U;
    size_t max_prefix = (*len < (FRAME_SYNC_LEN - 1U)) ? *len : (FRAME_SYNC_LEN - 1U);
    for (size_t prefix = max_prefix; prefix > 0U; prefix--) {
        if (memcmp(buffer + (*len - prefix), FRAME_SYNC_BYTES, prefix) == 0) {
            keep = prefix;
            break;
        }
    }

    if (keep == 0U) {
        *len = 0U;
        return;
    }

    memmove(buffer, buffer + (*len - keep), keep);
    *len = keep;
}

static const char *ack_for_message_type(uint8_t message_type)
{
    switch (message_type) {
    case MESSAGE_TYPE_CMD:
        return "I RECIEVED CMD";
    case MESSAGE_TYPE_TEST:
        return "I RECIEVED TEST";
    case MESSAGE_TYPE_TELEMETRY:
        return "I RECIEVED TELEMETRY";
    default:
        return "I RECIEVED UNKNOWN";
    }
}

static bool message_equals(const uint8_t *message, uint8_t message_len, const char *expected)
{
    if (message == NULL || expected == NULL) {
        return false;
    }
    size_t expected_len = strlen(expected);
    if (expected_len != (size_t)message_len) {
        return false;
    }
    return memcmp(message, expected, expected_len) == 0;
}

static bool parse_host_epoch_command(
    const uint8_t *message,
    uint8_t message_len,
    const char *prefix,
    int64_t *host_epoch_ms
)
{
    if (message == NULL || prefix == NULL) {
        return false;
    }
    size_t prefix_len = strlen(prefix);
    if ((size_t)message_len < prefix_len) {
        return false;
    }
    if (memcmp(message, prefix, prefix_len) != 0) {
        return false;
    }
    if ((size_t)message_len == prefix_len) {
        return true;
    }
    if (message[prefix_len] != ':') {
        return false;
    }

    int64_t parsed = 0;
    for (size_t idx = prefix_len + 1U; idx < (size_t)message_len; idx++) {
        uint8_t ch = message[idx];
        if (ch < (uint8_t)'0' || ch > (uint8_t)'9') {
            return false;
        }
        parsed = (parsed * 10) + (int64_t)(ch - (uint8_t)'0');
    }
    if (host_epoch_ms != NULL) {
        *host_epoch_ms = parsed;
    }
    return true;
}

static void set_last_ack(uint8_t message_type, const char *ack_text)
{
    if (ack_text == NULL) {
        return;
    }
    size_t ack_len = strlen(ack_text);
    if (ack_len > STREAM_MAX_MESSAGE_LEN) {
        ack_len = STREAM_MAX_MESSAGE_LEN;
    }
    memcpy(g_last_ack, ack_text, ack_len);
    g_last_ack[ack_len] = '\0';
    g_last_ack_type = message_type;
}

static void handle_legacy_command(uint8_t source_module_id, uint8_t command)
{
    (void)source_module_id;
    if (command == HELLO_BYTE) {
        send_legacy_framed_byte(TEST_MODULE_ID, ACK_BYTE);
        // printf(
        //     "HELLO frame received from module_id=0x%02X, ACK sent with module_id=0x%02X\n",
        //     source_module_id,
        //     TEST_MODULE_ID
        // );
        return;
    }

    if (command == MODULE_QUERY_BYTE) {
        send_module_info_frame();
        // printf(
        //     "MODULE query from module_id=0x%02X, sent module_id=0x%02X name=%s\n",
        //     source_module_id,
        //     TEST_MODULE_ID,
        //     MODULE_NAME
        // );
        return;
    }

    // printf(
    //     "Unexpected legacy command from module_id=0x%02X: 0x%02X\n",
    //     source_module_id,
    //     command
    // );
}

static void handle_stream_message(
    const uint8_t *message,
    uint8_t message_len,
    uint8_t message_type,
    uint32_t sequence
)
{
    uint32_t seq = normalize_sequence(sequence);
    if (g_has_last_host_seq && seq == g_last_host_seq) {
        send_stream_frame(g_last_ack, g_last_ack_type, seq);
        return;
    }

    const char *ack = ack_for_message_type(message_type);
    if (message_type == MESSAGE_TYPE_CMD || message_type == MESSAGE_TYPE_TELEMETRY) {
        int64_t host_epoch_ms = 0;
        if (parse_host_epoch_command(message, message_len, CMD_TELEMETRY_START, &host_epoch_ms)) {
            g_telemetry_active = true;
            g_telemetry_counter = 0U;
            g_telemetry_seq = 0U;
            g_next_telemetry_due_us = esp_timer_get_time();
            if (host_epoch_ms > 0) {
                g_has_host_time_offset = false;
                update_host_time_offset_ms(host_epoch_ms);
            } else {
                g_host_time_offset_ms = 0;
                g_has_host_time_offset = false;
            }
            ack = ACK_TELEMETRY_STARTED;
        } else if (parse_host_epoch_command(message, message_len, CMD_TELEMETRY_SYNC, &host_epoch_ms)) {
            if (host_epoch_ms > 0) {
                update_host_time_offset_ms(host_epoch_ms);
            }
            ack = ACK_TELEMETRY_SYNCED;
        } else if (message_equals(message, message_len, CMD_TELEMETRY_STOP)) {
            g_telemetry_active = false;
            g_next_telemetry_due_us = 0;
            g_has_host_time_offset = false;
            g_host_time_offset_ms = 0;
            ack = ACK_TELEMETRY_STOPPED;
        }
    }

    send_stream_frame(ack, message_type, seq);
    set_last_ack(message_type, ack);
    g_last_host_seq = seq;
    g_has_last_host_seq = true;
}

static void maybe_send_telemetry(void)
{
    if (!g_telemetry_active) {
        return;
    }

    int64_t now_us = esp_timer_get_time();
    if (g_next_telemetry_due_us <= 0) {
        g_next_telemetry_due_us = now_us;
    }
    if (now_us < g_next_telemetry_due_us) {
        return;
    }
    do {
        g_next_telemetry_due_us += TELEMETRY_INTERVAL_US;
    } while (g_next_telemetry_due_us <= now_us);

    char telemetry_payload[64] = {0};
    int64_t module_epoch_ms = esp_timer_get_time() / 1000;
    int64_t host_epoch_ms = module_epoch_ms;
    if (g_has_host_time_offset) {
        host_epoch_ms = module_epoch_ms + g_host_time_offset_ms;
    }
    (void)snprintf(
        telemetry_payload,
        sizeof(telemetry_payload),
        "TELEMETRY %lu %lld",
        (unsigned long)g_telemetry_counter,
        (long long)host_epoch_ms
    );
    g_telemetry_counter++;
    send_stream_frame(telemetry_payload, MESSAGE_TYPE_TELEMETRY, g_telemetry_seq);
    g_telemetry_seq = normalize_sequence(g_telemetry_seq + 1U);
}

void app_main(void)
{
    usb_serial_jtag_driver_config_t usb_cfg = {
        .rx_buffer_size = 256,
        .tx_buffer_size = 256,
    };

    esp_err_t err = usb_serial_jtag_driver_install(&usb_cfg);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        printf("usb_serial_jtag_driver_install failed: %d\n", (int)err);
    }

    uint8_t rx_buffer[RX_BUFFER_SIZE] = {0};
    size_t rx_len = 0U;

    while (1) {
        uint8_t chunk[64] = {0};
        int n = usb_serial_jtag_read_bytes(chunk, sizeof(chunk), 0);
        if (n > 0) {
            size_t got = (size_t)n;
            size_t free_space = RX_BUFFER_SIZE - rx_len;
            if (got > free_space) {
                size_t drop = got - free_space;
                consume_bytes(rx_buffer, &rx_len, drop);
            }
            memcpy(rx_buffer + rx_len, chunk, got);
            rx_len += got;

            while (rx_len > 0U) {
                int sync_index = find_sync_index(rx_buffer, rx_len);
                if (sync_index < 0) {
                    trim_to_sync_prefix(rx_buffer, &rx_len);
                    break;
                }

                if (sync_index > 0) {
                    consume_bytes(rx_buffer, &rx_len, (size_t)sync_index);
                }

                if (rx_len < STREAM_HEADER_LEN) {
                    break;
                }

                uint8_t first = rx_buffer[FRAME_SYNC_LEN];

                if (first == HOST_MODULE_ID) {
                    if (rx_len < FRAME_SYNC_LEN + 2U) {
                        break;
                    }
                    uint8_t source_module_id = first;
                    uint8_t command = rx_buffer[FRAME_SYNC_LEN + 1U];
                    consume_bytes(rx_buffer, &rx_len, FRAME_SYNC_LEN + 2U);
                    handle_legacy_command(source_module_id, command);
                    continue;
                }

                uint8_t msg_len = first;
                if (msg_len == 0U || msg_len > STREAM_MAX_MESSAGE_LEN) {
                    consume_bytes(rx_buffer, &rx_len, 1U);
                    continue;
                }

                size_t frame_len = STREAM_HEADER_LEN + (size_t)msg_len + STREAM_TRAILER_LEN;
                if (rx_len < frame_len) {
                    break;
                }

                const uint8_t *message = &rx_buffer[FRAME_SYNC_LEN + 1U];
                size_t trailer_start = FRAME_SYNC_LEN + 1U + msg_len;
                uint8_t message_type = rx_buffer[trailer_start];
                uint32_t seq = ((uint32_t)rx_buffer[trailer_start + 1U] << 16U)
                    | ((uint32_t)rx_buffer[trailer_start + 2U] << 8U)
                    | (uint32_t)rx_buffer[trailer_start + 3U];

                handle_stream_message(message, msg_len, message_type, seq);
                consume_bytes(rx_buffer, &rx_len, frame_len);
            }
        }

        maybe_send_telemetry();
    }
}
