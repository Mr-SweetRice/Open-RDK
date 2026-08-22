#include "ds_comm.h"

#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "driver/uart.h"
#include "driver/uart_vfs.h"
#include "driver/usb_serial_jtag.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static const char *TAG = "ds_comm";

#define FRAME_SYNC_0                  0xAAU
#define FRAME_SYNC_1                  0x55U
#define FRAME_SYNC_2                  0xAAU
#define FRAME_SYNC_3                  0x55U
#define FRAME_SYNC_LEN                4U
#define FRAME_RX_MAX_LEN              200U
#define FRAME_SEQ_BYTES               3U
#define FRAME_SEQ_MASK                0x00FFFFFFUL
#define FRAME_RX_INTERBYTE_TIMEOUT_US 100000LL

#define HOST_MODULE_ID                0x00U
#define DISTANCE_SENSOR_MODULE_ID     0x14U
#define FRAME_HELLO_BYTE              0x01U
#define FRAME_HELLO_ACK_BYTE          0x06U
#define FRAME_MODULE_QUERY_BYTE       0x04U
#define FRAME_MODULE_INFO_PREFIX_BYTE 0x05U
#define FRAME_MODULE_INFO_TEXT        "distance_sensor_module|1.0|distance-sensor|1.0"

#define FRAME_MSG_TYPE_CMD            0x01U
#define FRAME_MSG_TYPE_TEST           0x02U
#define FRAME_MSG_TYPE_TELEMETRY      0x03U
#define FRAME_MSG_TYPE_CONTROL        0x04U

#define DS_SAMPLE_PERIOD_MIN_MS       60U
#define DS_SAMPLE_PERIOD_MAX_MS       2000U
#define DS_DISTANCE_MIN_MM            20U
#define DS_DISTANCE_MAX_MM            4000U

static const uint8_t s_frame_sync[FRAME_SYNC_LEN] = {
    FRAME_SYNC_0, FRAME_SYNC_1, FRAME_SYNC_2, FRAME_SYNC_3
};

static ds_comm_cfg_t s_cfg = {0};
static bool s_inited = false;
static bool s_usb_jtag_ready = false;
static SemaphoreHandle_t s_tx_mutex = NULL;
static bool s_link_active = false;
static int64_t s_last_rx_us = 0;
static bool s_stream_telem_enabled = false;
static uint32_t s_stream_telem_seq = 0U;
static uint64_t s_stream_last_sample_timestamp_ms = UINT64_MAX;

typedef struct {
    uint8_t payload[1U + FRAME_RX_MAX_LEN + 1U + FRAME_SEQ_BYTES];
    size_t len;
    size_t expected;
    size_t sync_match;
    int64_t last_byte_us;
    bool collecting;
} frame_parser_t;

static void write_bytes_locked(const uint8_t *data, size_t len)
{
    if (!s_inited || !data || len == 0U) {
        return;
    }

    bool locked = false;
    if (s_tx_mutex) {
        locked = xSemaphoreTake(s_tx_mutex, pdMS_TO_TICKS(20)) == pdTRUE;
    }
    uart_write_bytes(UART_NUM_0, data, len);
    if (s_usb_jtag_ready) {
        usb_serial_jtag_write_bytes(data, len, 0);
    }
    if (locked) {
        xSemaphoreGive(s_tx_mutex);
    }
}

static void sanitize_text_field(char *text, size_t len)
{
    if (!text || len == 0U) {
        return;
    }
    text[len - 1U] = '\0';
    for (size_t i = 0U; i < len && text[i] != '\0'; ++i) {
        if (text[i] == ',' || text[i] == '\r' || text[i] == '\n') {
            text[i] = '-';
        }
    }
}

static bool filter_window_is_valid(uint8_t window)
{
    return window == 1U || window == 3U || window == 5U || window == 7U;
}

static bool get_sensor_snapshot(ds_comm_sensor_state_t *state)
{
    if (!state || !s_cfg.get_sensor_state) {
        return false;
    }
    memset(state, 0, sizeof(*state));
    return s_cfg.get_sensor_state(s_cfg.ctx, state);
}

static bool get_cfg_snapshot(ds_comm_cfg_state_t *state)
{
    if (!state || !s_cfg.get_cfg_state) {
        return false;
    }
    memset(state, 0, sizeof(*state));
    return s_cfg.get_cfg_state(s_cfg.ctx, state);
}

static bool format_sensor_state(
    const ds_comm_sensor_state_t *state,
    char *out,
    size_t out_len)
{
    if (!state || !out || out_len == 0U) {
        return false;
    }
    snprintf(
        out,
        out_len,
        "DS,%" PRId32 ",%" PRId32 ",%" PRIu32 ",%u,%u,%" PRIu64,
        state->filtered_distance_mm,
        state->raw_distance_mm,
        state->echo_time_us,
        state->valid ? 1U : 0U,
        (unsigned)state->health_flags,
        state->sample_timestamp_ms);
    return true;
}

static bool format_sensor_snapshot(char *out, size_t out_len)
{
    ds_comm_sensor_state_t state = {0};
    if (!out || out_len == 0U || !get_sensor_snapshot(&state)) {
        return false;
    }
    return format_sensor_state(&state, out, out_len);
}

static bool format_cfg_snapshot(char *out, size_t out_len)
{
    ds_comm_cfg_state_t state = {0};
    if (!out || out_len == 0U || !get_cfg_snapshot(&state)) {
        return false;
    }
    sanitize_text_field(state.sensor_name, sizeof(state.sensor_name));
    snprintf(
        out,
        out_len,
        "CFG,%s,%" PRIu32 ",%" PRIu32 ",%u",
        state.sensor_name,
        state.sample_period_ms,
        state.max_distance_mm,
        (unsigned)state.filter_window);
    return true;
}

static bool format_info_snapshot(char *out, size_t out_len)
{
    ds_comm_cfg_state_t cfg = {0};
    ds_comm_sensor_state_t sensor = {0};
    if (!out || out_len == 0U ||
        !get_cfg_snapshot(&cfg) ||
        !get_sensor_snapshot(&sensor)) {
        return false;
    }
    sanitize_text_field(cfg.sensor_name, sizeof(cfg.sensor_name));
    snprintf(
        out,
        out_len,
        "INFO,%s,distance_sensor_module,distance_sensor_module,20,HC-SR04,3,10,%u,1.0,distance-sensor,1.0",
        cfg.sensor_name,
        (unsigned)sensor.health_flags);
    return true;
}

static bool format_selftest_snapshot(char *out, size_t out_len)
{
    ds_comm_selftest_state_t state = {0};
    if (!out || out_len == 0U || !s_cfg.run_selftest ||
        !s_cfg.run_selftest(s_cfg.ctx, &state)) {
        return false;
    }
    snprintf(
        out,
        out_len,
        "SELFTEST,%u,%u,%" PRId32,
        state.ok ? 1U : 0U,
        (unsigned)state.health_flags,
        state.distance_mm);
    return true;
}

static bool try_apply_command(
    const char *line,
    char *response_out,
    size_t response_out_len,
    bool *ok_out)
{
    bool handled = true;
    bool ok = false;
    if (response_out && response_out_len > 0U) {
        response_out[0] = '\0';
    }
    if (!line) {
        if (ok_out) {
            *ok_out = false;
        }
        return false;
    }

    if (strcmp(line, "GET DATA") == 0 || strcmp(line, "GET TELEM") == 0) {
        ok = format_sensor_snapshot(response_out, response_out_len);
    } else if (strcmp(line, "GET CFG") == 0) {
        ok = format_cfg_snapshot(response_out, response_out_len);
    } else if (strcmp(line, "GET INFO") == 0) {
        ok = format_info_snapshot(response_out, response_out_len);
    } else if (strcmp(line, "RUN SELFTEST") == 0) {
        ok = format_selftest_snapshot(response_out, response_out_len);
    } else if (strcmp(line, "SAVE CFG") == 0) {
        ok = s_cfg.save_cfg && s_cfg.save_cfg(s_cfg.ctx);
    } else if (strcmp(line, "RESET CFG") == 0) {
        ok = s_cfg.reset_cfg && s_cfg.reset_cfg(s_cfg.ctx);
        if (ok) {
            ok = format_cfg_snapshot(response_out, response_out_len);
        }
    } else if (strncmp(line, "SET CFG ", 8U) == 0) {
        ds_comm_cfg_state_t cfg = {0};
        const char *arg = line + 8U;
        int value = 0;
        if (!get_cfg_snapshot(&cfg) || !s_cfg.set_cfg_state) {
            ok = false;
        } else if (strncmp(arg, "NAME ", 5U) == 0) {
            const char *name = arg + 5U;
            while (*name == ' ') {
                ++name;
            }
            if (*name != '\0') {
                snprintf(cfg.sensor_name, sizeof(cfg.sensor_name), "%s", name);
                sanitize_text_field(cfg.sensor_name, sizeof(cfg.sensor_name));
                ok = s_cfg.set_cfg_state(s_cfg.ctx, &cfg);
            }
        } else if (sscanf(arg, "SAMPLE_MS %d", &value) == 1) {
            if (value >= (int)DS_SAMPLE_PERIOD_MIN_MS &&
                value <= (int)DS_SAMPLE_PERIOD_MAX_MS) {
                cfg.sample_period_ms = (uint32_t)value;
                ok = s_cfg.set_cfg_state(s_cfg.ctx, &cfg);
            }
        } else if (sscanf(arg, "MAX_MM %d", &value) == 1 ||
                   sscanf(arg, "MAX_DISTANCE_MM %d", &value) == 1) {
            if (value >= (int)DS_DISTANCE_MIN_MM &&
                value <= (int)DS_DISTANCE_MAX_MM) {
                cfg.max_distance_mm = (uint32_t)value;
                ok = s_cfg.set_cfg_state(s_cfg.ctx, &cfg);
            }
        } else if (sscanf(arg, "FILTER %d", &value) == 1 ||
                   sscanf(arg, "FILTER_WINDOW %d", &value) == 1) {
            if (value >= 0 && value <= UINT8_MAX &&
                filter_window_is_valid((uint8_t)value)) {
                cfg.filter_window = (uint8_t)value;
                ok = s_cfg.set_cfg_state(s_cfg.ctx, &cfg);
            }
        } else {
            ok = false;
        }

        if (ok) {
            ok = format_cfg_snapshot(response_out, response_out_len);
        }
    } else {
        handled = false;
    }

    if (handled && response_out && response_out_len > 0U && response_out[0] == '\0') {
        snprintf(response_out, response_out_len, ok ? "OK" : "ERR");
    }
    if (ok_out) {
        *ok_out = ok;
    }
    return handled;
}

static void send_control_frame_payload(const uint8_t *payload, size_t payload_len)
{
    if (!payload || payload_len == 0U) {
        return;
    }
    uint8_t frame[FRAME_SYNC_LEN + 1U + 80U];
    if (payload_len > sizeof(frame) - FRAME_SYNC_LEN - 1U) {
        payload_len = sizeof(frame) - FRAME_SYNC_LEN - 1U;
    }
    size_t idx = 0U;
    memcpy(&frame[idx], s_frame_sync, FRAME_SYNC_LEN);
    idx += FRAME_SYNC_LEN;
    frame[idx++] = DISTANCE_SENSOR_MODULE_ID;
    memcpy(&frame[idx], payload, payload_len);
    idx += payload_len;
    write_bytes_locked(frame, idx);
}

static void send_module_info_frame(void)
{
    const char *name = FRAME_MODULE_INFO_TEXT;
    size_t name_len = strlen(name);
    if (name_len > 64U) {
        name_len = 64U;
    }
    uint8_t payload[2U + 64U];
    payload[0] = FRAME_MODULE_INFO_PREFIX_BYTE;
    payload[1] = (uint8_t)name_len;
    memcpy(&payload[2], name, name_len);
    send_control_frame_payload(payload, 2U + name_len);
}

static void send_stream_frame_text(uint8_t message_type, uint32_t sequence, const char *text)
{
    const char *safe = text;
    size_t text_len = safe ? strlen(safe) : 0U;
    if (text_len == 0U) {
        safe = " ";
        text_len = 1U;
    }
    if (text_len > FRAME_RX_MAX_LEN) {
        text_len = FRAME_RX_MAX_LEN;
    }

    uint8_t frame[FRAME_SYNC_LEN + 1U + FRAME_RX_MAX_LEN + 1U + FRAME_SEQ_BYTES];
    size_t idx = 0U;
    memcpy(&frame[idx], s_frame_sync, FRAME_SYNC_LEN);
    idx += FRAME_SYNC_LEN;
    frame[idx++] = (uint8_t)text_len;
    memcpy(&frame[idx], safe, text_len);
    idx += text_len;
    frame[idx++] = message_type;
    const uint32_t seq24 = sequence & FRAME_SEQ_MASK;
    frame[idx++] = (uint8_t)((seq24 >> 16U) & 0xFFU);
    frame[idx++] = (uint8_t)((seq24 >> 8U) & 0xFFU);
    frame[idx++] = (uint8_t)(seq24 & 0xFFU);
    write_bytes_locked(frame, idx);
}

static void handle_control_frame(const uint8_t *payload, size_t payload_len)
{
    if (!payload || payload_len < 2U || payload[0] != HOST_MODULE_ID) {
        return;
    }
    if (payload[1] == FRAME_HELLO_BYTE) {
        const uint8_t ack = FRAME_HELLO_ACK_BYTE;
        send_control_frame_payload(&ack, 1U);
    } else if (payload[1] == FRAME_MODULE_QUERY_BYTE) {
        send_module_info_frame();
    }
}

static void handle_stream_frame(const uint8_t *payload, size_t payload_len)
{
    if (!payload || payload_len < 1U + 1U + FRAME_SEQ_BYTES) {
        return;
    }
    const uint8_t message_len = payload[0];
    const size_t expected = 1U + (size_t)message_len + 1U + FRAME_SEQ_BYTES;
    if (message_len == 0U || message_len > FRAME_RX_MAX_LEN || payload_len != expected) {
        return;
    }

    char message[FRAME_RX_MAX_LEN + 1U];
    memcpy(message, &payload[1], message_len);
    message[message_len] = '\0';
    const uint8_t message_type = payload[1U + message_len];
    const size_t seq_offset = 1U + (size_t)message_len + 1U;
    const uint32_t sequence =
        ((uint32_t)payload[seq_offset] << 16U) |
        ((uint32_t)payload[seq_offset + 1U] << 8U) |
        (uint32_t)payload[seq_offset + 2U];

    if (message_type == FRAME_MSG_TYPE_CMD) {
        char response[FRAME_RX_MAX_LEN + 1U] = {0};
        bool ok = false;
        if (try_apply_command(message, response, sizeof(response), &ok)) {
            send_stream_frame_text(message_type, sequence, response);
        } else {
            send_stream_frame_text(message_type, sequence, "I RECIEVED CMD");
        }
        return;
    }
    if (message_type == FRAME_MSG_TYPE_TEST) {
        send_stream_frame_text(message_type, sequence, "I RECIEVED TEST");
        return;
    }
    if (message_type == FRAME_MSG_TYPE_CONTROL) {
        send_stream_frame_text(message_type, sequence, "ERR");
        return;
    }
    if (message_type != FRAME_MSG_TYPE_TELEMETRY) {
        send_stream_frame_text(message_type, sequence, "ERR");
        return;
    }

    if (strncmp(message, "TELEMETRY_START", 15U) == 0) {
        s_stream_telem_enabled = true;
        s_stream_telem_seq = 0U;
        s_stream_last_sample_timestamp_ms = UINT64_MAX;
        send_stream_frame_text(message_type, sequence, "TELEMETRY STARTED");
    } else if (strncmp(message, "TELEMETRY_STOP", 14U) == 0) {
        s_stream_telem_enabled = false;
        send_stream_frame_text(message_type, sequence, "TELEMETRY STOPPED");
    } else if (strncmp(message, "TELEMETRY_SYNC", 14U) == 0) {
        send_stream_frame_text(message_type, sequence, "TELEMETRY SYNCED");
    } else {
        send_stream_frame_text(message_type, sequence, "TELEMETRY");
    }
}

static void handle_framed_payload(const uint8_t *payload, size_t payload_len)
{
    if (!payload || payload_len == 0U) {
        return;
    }
    if (payload[0] == HOST_MODULE_ID && payload_len >= 2U) {
        handle_control_frame(payload, payload_len);
    } else {
        handle_stream_frame(payload, payload_len);
    }
}

static void reset_parser_payload(frame_parser_t *parser)
{
    parser->collecting = false;
    parser->len = 0U;
    parser->expected = 0U;
    parser->last_byte_us = 0;
}

static void feed_rx_byte(frame_parser_t *parser, uint8_t byte)
{
    if (!parser) {
        return;
    }

    const int64_t now_us = esp_timer_get_time();
    if (parser->collecting &&
        parser->last_byte_us > 0 &&
        (now_us - parser->last_byte_us) > FRAME_RX_INTERBYTE_TIMEOUT_US) {
        reset_parser_payload(parser);
        parser->sync_match = 0U;
    }

    if (parser->collecting) {
        parser->last_byte_us = now_us;
        if (parser->len >= sizeof(parser->payload)) {
            reset_parser_payload(parser);
            return;
        }
        parser->payload[parser->len++] = byte;
        if (parser->len == 1U) {
            if (parser->payload[0] == HOST_MODULE_ID) {
                parser->expected = 2U;
            } else if (parser->payload[0] > 0U &&
                       parser->payload[0] <= FRAME_RX_MAX_LEN) {
                parser->expected =
                    1U + (size_t)parser->payload[0] + 1U + FRAME_SEQ_BYTES;
            } else {
                reset_parser_payload(parser);
                return;
            }
            if (parser->expected > sizeof(parser->payload)) {
                reset_parser_payload(parser);
                return;
            }
        }
        if (parser->expected > 0U && parser->len >= parser->expected) {
            handle_framed_payload(parser->payload, parser->len);
            reset_parser_payload(parser);
        }
        return;
    }

    if (byte == s_frame_sync[parser->sync_match]) {
        ++parser->sync_match;
        if (parser->sync_match == FRAME_SYNC_LEN) {
            parser->sync_match = 0U;
            parser->collecting = true;
            parser->len = 0U;
            parser->expected = 0U;
            parser->last_byte_us = now_us;
        }
    } else if (parser->sync_match > 0U) {
        parser->sync_match = byte == s_frame_sync[0] ? 1U : 0U;
    }
}

static void maybe_send_stream_telemetry(void)
{
    if (!s_stream_telem_enabled) {
        return;
    }
    ds_comm_sensor_state_t state = {0};
    if (!get_sensor_snapshot(&state) ||
        state.sample_timestamp_ms == s_stream_last_sample_timestamp_ms) {
        return;
    }

    char message[FRAME_RX_MAX_LEN + 1U] = {0};
    if (!format_sensor_state(&state, message, sizeof(message))) {
        return;
    }
    send_stream_frame_text(FRAME_MSG_TYPE_TELEMETRY, s_stream_telem_seq, message);
    s_stream_telem_seq = (s_stream_telem_seq + 1U) & FRAME_SEQ_MASK;
    s_stream_last_sample_timestamp_ms = state.sample_timestamp_ms;
}

esp_err_t ds_comm_init(const ds_comm_cfg_t *cfg)
{
    if (!cfg || !cfg->get_sensor_state || !cfg->get_cfg_state) {
        return ESP_ERR_INVALID_ARG;
    }
    s_cfg = *cfg;
    if (s_cfg.link_timeout_ms == 0U) {
        s_cfg.link_timeout_ms = DS_COMM_DEFAULT_LINK_TIMEOUT_MS;
    }

    if (!s_tx_mutex) {
        s_tx_mutex = xSemaphoreCreateMutex();
        if (!s_tx_mutex) {
            return ESP_ERR_NO_MEM;
        }
    }

    const uart_config_t uart_cfg = {
        .baud_rate = 512000,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    ESP_ERROR_CHECK(uart_param_config(UART_NUM_0, &uart_cfg));
    ESP_ERROR_CHECK(uart_set_pin(
        UART_NUM_0,
        UART_PIN_NO_CHANGE,
        UART_PIN_NO_CHANGE,
        UART_PIN_NO_CHANGE,
        UART_PIN_NO_CHANGE));

    esp_err_t err = uart_driver_install(UART_NUM_0, 1024, 0, 0, NULL, 0);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        return err;
    }
    uart_vfs_dev_use_driver(UART_NUM_0);

    usb_serial_jtag_driver_config_t usb_cfg = {
        .rx_buffer_size = 1024,
        .tx_buffer_size = 1024,
    };
    err = usb_serial_jtag_driver_install(&usb_cfg);
    if (err == ESP_ERR_INVALID_STATE) {
        usb_serial_jtag_driver_uninstall();
        err = usb_serial_jtag_driver_install(&usb_cfg);
    }
    if (err == ESP_OK) {
        s_usb_jtag_ready = true;
    } else {
        s_usb_jtag_ready = false;
        ESP_LOGW(TAG, "USB Serial/JTAG unavailable (%d)", (int)err);
    }

    s_link_active = false;
    s_last_rx_us = 0;
    s_stream_telem_enabled = false;
    s_stream_telem_seq = 0U;
    s_stream_last_sample_timestamp_ms = UINT64_MAX;
    s_inited = true;
    return ESP_OK;
}

void ds_comm_task(void *arg)
{
    (void)arg;
    frame_parser_t parser = {0};

    while (true) {
        uint8_t byte = 0U;
        int received = 0;
        if (s_usb_jtag_ready) {
            received = usb_serial_jtag_read_bytes(&byte, 1U, 0);
        }
        if (received <= 0) {
            received = uart_read_bytes(UART_NUM_0, &byte, 1U, pdMS_TO_TICKS(20));
        }
        if (received > 0) {
            s_last_rx_us = esp_timer_get_time();
            s_link_active = true;
            feed_rx_byte(&parser, byte);
        }

        maybe_send_stream_telemetry();

        if (s_link_active && s_cfg.link_timeout_ms > 0U) {
            const int64_t now_us = esp_timer_get_time();
            if ((now_us - s_last_rx_us) > (int64_t)s_cfg.link_timeout_ms * 1000LL) {
                s_link_active = false;
                reset_parser_payload(&parser);
                parser.sync_match = 0U;
                if (s_cfg.on_link_timeout) {
                    s_cfg.on_link_timeout(s_cfg.ctx);
                }
                ESP_LOGW(TAG, "serial link timeout");
            }
        }
    }
}
