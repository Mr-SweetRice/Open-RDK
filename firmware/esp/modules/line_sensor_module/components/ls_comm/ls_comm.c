#include "ls_comm.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "driver/uart.h"
#include "driver/usb_serial_jtag.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_timer.h"
#include "esp_vfs_dev.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static const char *TAG = "ls_comm";

static ls_comm_cfg_t s_cfg = {0};
static bool s_inited = false;
static bool s_usb_jtag_ready = false;
static SemaphoreHandle_t s_tx_mutex = NULL;
static bool s_link_active = false;
static int64_t s_last_event_us = 0;
static int64_t s_last_link_check_us = 0;
static bool s_stream_telem_enabled = false;
static uint32_t s_stream_telem_seq = 0U;
static int64_t s_stream_telem_last_tx_us = 0;

#define FRAME_SYNC_0                     0xAAU
#define FRAME_SYNC_1                     0x55U
#define FRAME_SYNC_2                     0xAAU
#define FRAME_SYNC_3                     0x55U
#define FRAME_SYNC_LEN                   4U
#define FRAME_RX_MAX_LEN                 200U
#define FRAME_SEQ_BYTES                  3U
#define FRAME_SEQ_MASK                   0x00FFFFFFUL

#define HOST_MODULE_ID                   0x00U
#define LINE_SENSOR_MODULE_ID            0x12U
#define FRAME_HELLO_BYTE                 0x01U
#define FRAME_HELLO_ACK_BYTE             0x06U
#define FRAME_MODULE_QUERY_BYTE          0x04U
#define FRAME_MODULE_INFO_PREFIX_BYTE    0x05U
#define FRAME_MODULE_NAME                "line_sensor_module"

#define FRAME_MSG_TYPE_CMD               0x01U
#define FRAME_MSG_TYPE_TEST              0x02U
#define FRAME_MSG_TYPE_TELEMETRY         0x03U
#define FRAME_MSG_TYPE_TRACTION_OUT      0x04U

#define FRAME_TELEMETRY_TX_PERIOD_US     100000LL

#ifndef LS_COMM_ENABLE_LINE_FALLBACK
#define LS_COMM_ENABLE_LINE_FALLBACK 0
#endif

static const uint8_t s_frame_sync[FRAME_SYNC_LEN] = {
    FRAME_SYNC_0, FRAME_SYNC_1, FRAME_SYNC_2, FRAME_SYNC_3
};

static void write_bytes_locked(const uint8_t *data, size_t len)
{
    if (!s_inited || !data || len == 0U) {
        return;
    }

    bool locked = false;
    if (s_tx_mutex) {
        locked = (xSemaphoreTake(s_tx_mutex, pdMS_TO_TICKS(20)) == pdTRUE);
    }

    uart_write_bytes(UART_NUM_0, data, len);
    if (s_usb_jtag_ready) {
        usb_serial_jtag_write_bytes(data, len, 0);
    }

    if (locked) {
        xSemaphoreGive(s_tx_mutex);
    }
}

static bool feed_char_cmd(char ch, char *buf, size_t *len, size_t max_len)
{
    if (ch == '\r' || ch == '\n') {
        if (*len == 0U) {
            return false;
        }
        buf[*len] = '\0';
        *len = 0U;
        return true;
    }
    if (*len < (max_len - 1U)) {
        buf[(*len)++] = ch;
    } else {
        *len = 0U;
    }
    return false;
}

void ls_comm_send_line(const char *fmt, ...)
{
    if (!s_inited || !fmt) {
        return;
    }

    char buf[196];
    va_list ap;
    va_start(ap, fmt);
    int len = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (len <= 0) {
        return;
    }
    if (len > (int)(sizeof(buf) - 2)) {
        len = (int)(sizeof(buf) - 2);
    }
    buf[len++] = '\n';
    write_bytes_locked((const uint8_t *)buf, (size_t)len);
}

static bool get_sensor_snapshot(ls_comm_sensor_state_t *state)
{
    if (!state || !s_cfg.get_sensor_state) {
        return false;
    }
    memset(state, 0, sizeof(*state));
    return s_cfg.get_sensor_state(s_cfg.ctx, state);
}

static bool get_cfg_snapshot(ls_comm_cfg_state_t *state)
{
    if (!state || !s_cfg.get_cfg_state) {
        return false;
    }
    memset(state, 0, sizeof(*state));
    return s_cfg.get_cfg_state(s_cfg.ctx, state);
}

static bool get_cal_snapshot(ls_comm_cal_state_t *state)
{
    if (!state || !s_cfg.get_cal_state) {
        return false;
    }
    memset(state, 0, sizeof(*state));
    return s_cfg.get_cal_state(s_cfg.ctx, state);
}

static bool get_info_snapshot(ls_comm_info_state_t *state)
{
    if (!state || !s_cfg.get_info_state) {
        return false;
    }
    memset(state, 0, sizeof(*state));
    return s_cfg.get_info_state(s_cfg.ctx, state);
}

static void sanitize_text_field(char *text, size_t len)
{
    if (!text || len == 0U) {
        return;
    }
    text[len - 1U] = '\0';
    for (size_t i = 0; i < len && text[i] != '\0'; ++i) {
        if (text[i] == ',') {
            text[i] = '-';
        }
    }
}

static void format_hms_from_us(int64_t time_us, char *out, size_t out_len)
{
    if (!out || out_len == 0U) {
        return;
    }
    if (time_us < 0) {
        time_us = 0;
    }

    const uint32_t total_sec = (uint32_t)(time_us / 1000000LL);
    const uint32_t hh = (total_sec / 3600U) % 100U;
    const uint32_t mm = (total_sec / 60U) % 60U;
    const uint32_t ss = total_sec % 60U;
    snprintf(out, out_len, "%02lu:%02lu:%02lu",
             (unsigned long)hh,
             (unsigned long)mm,
             (unsigned long)ss);
}

static float calc_sensor_input_lag_ms(const ls_comm_sensor_state_t *state)
{
    if (!state || state->sample_started_us <= 0 || state->sample_ready_us <= 0) {
        return 0.0f;
    }

    const int64_t lag_us = state->sample_ready_us - state->sample_started_us;
    if (lag_us <= 0) {
        return 0.0f;
    }
    return (float)lag_us / 1000.0f;
}

static bool format_sensor_snapshot(char *out, size_t out_len)
{
    ls_comm_sensor_state_t state = {0};
    if (!out || out_len == 0U) {
        return false;
    }
    out[0] = '\0';

    if (!get_sensor_snapshot(&state)) {
        return false;
    }

    const float input_lag_ms = calc_sensor_input_lag_ms(&state);
    snprintf(
        out,
        out_len,
        "LS,%u,%u,%u,%u,%u,%.4f,%.4f,%.4f,%.4f,%.4f,%u,%u,%u,%u,%u,%.4f,%.4f,%u,%u,%lu,%.3f",
        (unsigned)state.raw[0], (unsigned)state.raw[1], (unsigned)state.raw[2],
        (unsigned)state.raw[3], (unsigned)state.raw[4],
        (double)state.value[0], (double)state.value[1], (double)state.value[2],
        (double)state.value[3], (double)state.value[4],
        (unsigned)state.digital[0], (unsigned)state.digital[1], (unsigned)state.digital[2],
        (unsigned)state.digital[3], (unsigned)state.digital[4],
        (double)state.position, (double)state.strength,
        state.line_detected ? 1U : 0U,
        state.calibrating ? 1U : 0U,
        (unsigned long)state.calibration_remaining_ms,
        (double)input_lag_ms);
    return true;
}

static bool format_cfg_snapshot(char *out, size_t out_len)
{
    ls_comm_cfg_state_t state = {0};
    if (!out || out_len == 0U) {
        return false;
    }
    out[0] = '\0';

    if (!get_cfg_snapshot(&state)) {
        return false;
    }
    sanitize_text_field(state.sensor_name, sizeof(state.sensor_name));
    snprintf(out, out_len, "CFG,%u,%.4f,%.4f,%lu,%s",
             (unsigned)state.track_type,
             (double)state.digital_threshold,
             (double)state.detect_threshold,
             (unsigned long)state.calibration_time_ms,
             state.sensor_name);
    return true;
}

static bool format_cal_snapshot(char *out, size_t out_len)
{
    ls_comm_cal_state_t state = {0};
    if (!out || out_len == 0U) {
        return false;
    }
    out[0] = '\0';

    if (!get_cal_snapshot(&state)) {
        return false;
    }

    snprintf(out, out_len, "CAL,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u",
             (unsigned)state.min_raw[0], (unsigned)state.min_raw[1],
             (unsigned)state.min_raw[2], (unsigned)state.min_raw[3],
             (unsigned)state.min_raw[4], (unsigned)state.max_raw[0],
             (unsigned)state.max_raw[1], (unsigned)state.max_raw[2],
             (unsigned)state.max_raw[3], (unsigned)state.max_raw[4]);
    return true;
}

static bool format_info_snapshot(char *out, size_t out_len)
{
    ls_comm_info_state_t state = {0};
    if (!out || out_len == 0U) {
        return false;
    }
    out[0] = '\0';

    if (!get_info_snapshot(&state)) {
        return false;
    }

    uint8_t mac[6] = {0};
    if (esp_read_mac(mac, ESP_MAC_WIFI_STA) != ESP_OK) {
        memset(mac, 0, sizeof(mac));
    }

    char serial_number[18];
    char module_id_hex[11];
    char last_event_at[16];
    char last_link_check_at[16];
    const char *link_status = s_link_active ? "live" : "idle";
    const char *status = s_link_active ? "online connected" : "online idle";

    snprintf(serial_number, sizeof(serial_number), "%02X:%02X:%02X:%02X:%02X:%02X",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    snprintf(module_id_hex, sizeof(module_id_hex), "0x%02lX", (unsigned long)(state.module_id & 0xFFU));
    format_hms_from_us(s_last_event_us, last_event_at, sizeof(last_event_at));
    format_hms_from_us(s_last_link_check_us, last_link_check_at, sizeof(last_link_check_at));

    sanitize_text_field(state.name, sizeof(state.name));
    sanitize_text_field(state.module_type, sizeof(state.module_type));
    sanitize_text_field(state.firmware_module, sizeof(state.firmware_module));

    snprintf(out, out_len, "INFO,%s,%s,%s,%s,%s,%s,%lu,%s,%u,%s,%s,%s",
             serial_number,
             state.name,
             status,
             "n/a",
             state.module_type,
             state.firmware_module,
             (unsigned long)state.module_id,
             module_id_hex,
             s_link_active ? 1U : 0U,
             link_status,
             last_event_at,
             last_link_check_at);
    return true;
}

static bool try_apply_line_sensor_command(const char *line, char *response_out, size_t response_out_len, bool *ok_out)
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
    } else if (strcmp(line, "GET CAL") == 0) {
        ok = format_cal_snapshot(response_out, response_out_len);
    } else if (strcmp(line, "GET INFO") == 0) {
        ok = format_info_snapshot(response_out, response_out_len);
    } else if (strcmp(line, "START CAL") == 0) {
        if (s_cfg.start_calibration) {
            s_cfg.start_calibration(s_cfg.ctx);
            ok = true;
        }
    } else if (strcmp(line, "STOP CAL") == 0) {
        if (s_cfg.stop_calibration) {
            s_cfg.stop_calibration(s_cfg.ctx);
            ok = true;
        }
    } else if (strcmp(line, "SAVE CFG") == 0) {
        ok = (s_cfg.save_cfg && s_cfg.save_cfg(s_cfg.ctx));
    } else if (strcmp(line, "SAVE CAL") == 0) {
        ok = (s_cfg.save_cal && s_cfg.save_cal(s_cfg.ctx));
    } else if (strncmp(line, "SET CFG ", 8) == 0) {
        ls_comm_cfg_state_t state = {0};
        if (!get_cfg_snapshot(&state) || !s_cfg.set_cfg_state) {
            ok = false;
        } else {
            const char *cfg_line = line + 8;
            float fvalue = 0.0f;
            int ivalue = 0;

            if (sscanf(cfg_line, "TRACK %d", &ivalue) == 1) {
                if (ivalue < 0 || ivalue > 1) {
                    ok = false;
                } else {
                    state.track_type = (uint8_t)ivalue;
                    ok = s_cfg.set_cfg_state(s_cfg.ctx, &state);
                }
            } else if (strncmp(cfg_line, "NAME ", 5) == 0) {
                const char *name = cfg_line + 5;
                while (*name == ' ') {
                    ++name;
                }
                snprintf(state.sensor_name, sizeof(state.sensor_name), "%s", name);
                sanitize_text_field(state.sensor_name, sizeof(state.sensor_name));
                ok = s_cfg.set_cfg_state(s_cfg.ctx, &state);
            } else if (sscanf(cfg_line, "DIGITAL_TH %f", &fvalue) == 1) {
                state.digital_threshold = fvalue;
                ok = s_cfg.set_cfg_state(s_cfg.ctx, &state);
            } else if (sscanf(cfg_line, "DETECT_TH %f", &fvalue) == 1) {
                state.detect_threshold = fvalue;
                ok = s_cfg.set_cfg_state(s_cfg.ctx, &state);
            } else if (sscanf(cfg_line, "CAL_TIME_MS %d", &ivalue) == 1) {
                if (ivalue < 100) {
                    ok = false;
                } else {
                    state.calibration_time_ms = (uint32_t)ivalue;
                    ok = s_cfg.set_cfg_state(s_cfg.ctx, &state);
                }
            } else {
                ok = false;
            }
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

static bool try_apply_traction_out_command(const char *line, bool *ok_out)
{
    float val = 0.0f;

    if (!line) {
        if (ok_out) {
            *ok_out = false;
        }
        return false;
    }

    if (sscanf(line, "SET OUT RAW %f", &val) == 1) {
        if (ok_out) {
            *ok_out = true;
        }
        return true;
    }

    if (sscanf(line, "SET OUT %f", &val) == 1) {
        if (ok_out) {
            *ok_out = true;
        }
        return true;
    }

    if (strncmp(line, "CLR OUT", 7) == 0) {
        if (ok_out) {
            *ok_out = true;
        }
        return true;
    }

    if (ok_out) {
        *ok_out = false;
    }
    return false;
}

static void send_control_frame_payload(const uint8_t *payload, size_t payload_len)
{
    if (!payload || payload_len == 0U) {
        return;
    }

    uint8_t frame[FRAME_SYNC_LEN + 1U + 80U];
    if (payload_len > (sizeof(frame) - FRAME_SYNC_LEN - 1U)) {
        payload_len = sizeof(frame) - FRAME_SYNC_LEN - 1U;
    }

    size_t idx = 0U;
    memcpy(&frame[idx], s_frame_sync, FRAME_SYNC_LEN);
    idx += FRAME_SYNC_LEN;
    frame[idx++] = LINE_SENSOR_MODULE_ID;
    memcpy(&frame[idx], payload, payload_len);
    idx += payload_len;
    write_bytes_locked(frame, idx);
}

static void send_module_info_frame(void)
{
    const char *name = FRAME_MODULE_NAME;
    size_t name_len = strlen(name);
    if (name_len > 64U) {
        name_len = 64U;
    }

    uint8_t payload[2U + 64U];
    payload[0] = FRAME_MODULE_INFO_PREFIX_BYTE;
    payload[1] = (uint8_t)name_len;
    if (name_len > 0U) {
        memcpy(&payload[2], name, name_len);
    }
    send_control_frame_payload(payload, 2U + name_len);
}

static void send_stream_frame_text(uint8_t msg_type, uint32_t seq, const char *text)
{
    const char *safe = text;
    size_t msg_len = (safe != NULL) ? strlen(safe) : 0U;
    if (msg_len == 0U) {
        safe = " ";
        msg_len = 1U;
    }
    if (msg_len > FRAME_RX_MAX_LEN) {
        msg_len = FRAME_RX_MAX_LEN;
    }

    uint8_t frame[FRAME_SYNC_LEN + 1U + FRAME_RX_MAX_LEN + 1U + FRAME_SEQ_BYTES];
    size_t idx = 0U;
    memcpy(&frame[idx], s_frame_sync, FRAME_SYNC_LEN);
    idx += FRAME_SYNC_LEN;
    frame[idx++] = (uint8_t)msg_len;
    memcpy(&frame[idx], safe, msg_len);
    idx += msg_len;
    frame[idx++] = msg_type;

    uint32_t seq24 = (seq & FRAME_SEQ_MASK);
    frame[idx++] = (uint8_t)((seq24 >> 16U) & 0xFFU);
    frame[idx++] = (uint8_t)((seq24 >> 8U) & 0xFFU);
    frame[idx++] = (uint8_t)(seq24 & 0xFFU);

    write_bytes_locked(frame, idx);
}

static void handle_control_frame(const uint8_t *frame_payload, size_t payload_len)
{
    if (!frame_payload || payload_len < 2U) {
        return;
    }

    const uint8_t module_id = frame_payload[0];
    const uint8_t cmd = frame_payload[1];
    if (module_id != HOST_MODULE_ID) {
        return;
    }

    if (cmd == FRAME_HELLO_BYTE) {
        const uint8_t ack = FRAME_HELLO_ACK_BYTE;
        send_control_frame_payload(&ack, 1U);
        return;
    }

    if (cmd == FRAME_MODULE_QUERY_BYTE) {
        send_module_info_frame();
        return;
    }
}

static void handle_stream_frame(const uint8_t *frame_payload, size_t payload_len)
{
    if (!frame_payload || payload_len < (1U + 1U + FRAME_SEQ_BYTES)) {
        return;
    }

    const uint8_t msg_len = frame_payload[0];
    const size_t expected = 1U + (size_t)msg_len + 1U + FRAME_SEQ_BYTES;
    if (msg_len == 0U || msg_len > FRAME_RX_MAX_LEN || payload_len != expected) {
        return;
    }

    const char *msg_ptr = (const char *)&frame_payload[1];
    char msg_text[FRAME_RX_MAX_LEN + 1U];
    memcpy(msg_text, msg_ptr, msg_len);
    msg_text[msg_len] = '\0';

    const uint8_t msg_type = frame_payload[1U + msg_len];
    const size_t seq_offset = 1U + (size_t)msg_len + 1U;
    uint32_t seq = ((uint32_t)frame_payload[seq_offset] << 16U) |
                   ((uint32_t)frame_payload[seq_offset + 1U] << 8U) |
                   ((uint32_t)frame_payload[seq_offset + 2U]);

    if (msg_type == FRAME_MSG_TYPE_CMD) {
        char response[FRAME_RX_MAX_LEN + 1U];
        bool ok = false;
        bool handled = try_apply_line_sensor_command(msg_text, response, sizeof(response), &ok);
        if (handled) {
            send_stream_frame_text(msg_type, seq, response);
        } else {
            send_stream_frame_text(msg_type, seq, "I RECIEVED CMD");
        }
        return;
    }

    if (msg_type == FRAME_MSG_TYPE_TEST) {
        send_stream_frame_text(msg_type, seq, "I RECIEVED TEST");
        return;
    }

    if (msg_type == FRAME_MSG_TYPE_TRACTION_OUT) {
        bool ok = false;
        bool handled = try_apply_traction_out_command(msg_text, &ok);
        if (handled && ok) {
            send_stream_frame_text(msg_type, seq, "OK");
        } else {
            send_stream_frame_text(msg_type, seq, "ERR");
        }
        return;
    }

    if (msg_type != FRAME_MSG_TYPE_TELEMETRY) {
        send_stream_frame_text(msg_type, seq, "ERR");
        return;
    }

    if (strncmp(msg_text, "TELEMETRY_START", 15) == 0) {
        s_stream_telem_enabled = true;
        s_stream_telem_last_tx_us = esp_timer_get_time();
        s_stream_telem_seq = 0U;
        send_stream_frame_text(msg_type, seq, "TELEMETRY STARTED");
        return;
    }

    if (strncmp(msg_text, "TELEMETRY_STOP", 14) == 0) {
        s_stream_telem_enabled = false;
        send_stream_frame_text(msg_type, seq, "TELEMETRY STOPPED");
        return;
    }

    if (strncmp(msg_text, "TELEMETRY_SYNC", 14) == 0) {
        send_stream_frame_text(msg_type, seq, "TELEMETRY SYNCED");
        return;
    }

    send_stream_frame_text(msg_type, seq, "TELEMETRY");
}

static void handle_framed_payload(const uint8_t *frame_payload, size_t payload_len)
{
    if (!frame_payload || payload_len == 0U) {
        return;
    }

    if (frame_payload[0] == HOST_MODULE_ID && payload_len >= 2U) {
        handle_control_frame(frame_payload, payload_len);
        return;
    }

    handle_stream_frame(frame_payload, payload_len);
}

static void maybe_send_stream_telemetry(void)
{
    if (!s_stream_telem_enabled) {
        return;
    }

    const int64_t now_us = esp_timer_get_time();
    if ((now_us - s_stream_telem_last_tx_us) < FRAME_TELEMETRY_TX_PERIOD_US) {
        return;
    }
    s_stream_telem_last_tx_us = now_us;

    char msg[FRAME_RX_MAX_LEN + 1U];
    if (!format_sensor_snapshot(msg, sizeof(msg))) {
        snprintf(msg, sizeof(msg), "TELEMETRY");
    }

    send_stream_frame_text(FRAME_MSG_TYPE_TELEMETRY, s_stream_telem_seq, msg);
    s_stream_telem_seq = (s_stream_telem_seq + 1U) & FRAME_SEQ_MASK;
}

static void handle_cmd_line(const char *line)
{
    char response[196];
    bool ok = false;
    bool handled = try_apply_line_sensor_command(line, response, sizeof(response), &ok);

    if (!handled) {
        ls_comm_send_line("ERR");
        return;
    }

    if (response[0] != '\0') {
        ls_comm_send_line("%s", response);
    } else {
        ls_comm_send_line(ok ? "OK" : "ERR");
    }
}

esp_err_t ls_comm_init(const ls_comm_cfg_t *cfg)
{
    if (!cfg) {
        return ESP_ERR_INVALID_ARG;
    }

    s_cfg = *cfg;
    if (s_cfg.link_timeout_ms == 0U) {
        s_cfg.link_timeout_ms = LS_COMM_DEFAULT_LINK_TIMEOUT_MS;
    }

    if (!s_tx_mutex) {
        s_tx_mutex = xSemaphoreCreateMutex();
        if (!s_tx_mutex) {
            return ESP_ERR_NO_MEM;
        }
    }

    uart_config_t uart_cfg = {
        .baud_rate = 115200,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    ESP_ERROR_CHECK(uart_param_config(UART_NUM_0, &uart_cfg));
    ESP_ERROR_CHECK(uart_set_pin(UART_NUM_0, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));

    esp_err_t err = uart_driver_install(UART_NUM_0, 1024, 0, 0, NULL, 0);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        return err;
    }
    esp_vfs_dev_uart_use_driver(UART_NUM_0);

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
        ESP_LOGW(TAG, "usb serial jtag unavailable (%d)", (int)err);
    }

    ESP_LOGI(TAG, "line fallback %s",
             (LS_COMM_ENABLE_LINE_FALLBACK != 0) ? "ENABLED" : "DISABLED");

    s_stream_telem_enabled = false;
    s_stream_telem_seq = 0U;
    s_stream_telem_last_tx_us = 0;
    s_link_active = false;
    s_last_event_us = 0;
    s_last_link_check_us = 0;
    s_inited = true;
    return ESP_OK;
}

void ls_comm_task(void *arg)
{
    (void)arg;

    char line[128];
    size_t line_len = 0U;
    uint8_t framed_buf[1U + FRAME_RX_MAX_LEN + 1U + FRAME_SEQ_BYTES];
    size_t framed_len = 0U;
    size_t framed_expected = 0U;
    size_t sync_match = 0U;
    bool collecting_framed = false;
    int64_t last_rx_us = 0;

    while (true) {
        bool got_line = false;

        uint8_t ch = 0U;
        int n = 0;
        if (s_usb_jtag_ready) {
            n = usb_serial_jtag_read_bytes(&ch, 1, 0);
        }
        if (n > 0) {
            last_rx_us = esp_timer_get_time();
            s_last_event_us = last_rx_us;
            s_last_link_check_us = last_rx_us;
            s_link_active = true;
            bool feed_line = true;

            if (collecting_framed) {
                feed_line = false;
                if (framed_len < sizeof(framed_buf)) {
                    framed_buf[framed_len++] = ch;
                } else {
                    collecting_framed = false;
                    framed_len = 0U;
                    framed_expected = 0U;
                }

                if (collecting_framed && framed_len == 1U) {
                    if (framed_buf[0] == HOST_MODULE_ID) {
                        framed_expected = 2U;
                    } else if (framed_buf[0] > 0U && framed_buf[0] <= FRAME_RX_MAX_LEN) {
                        framed_expected = 1U + (size_t)framed_buf[0] + 1U + FRAME_SEQ_BYTES;
                    } else {
                        collecting_framed = false;
                        framed_len = 0U;
                        framed_expected = 0U;
                    }
                    if (framed_expected > sizeof(framed_buf)) {
                        collecting_framed = false;
                        framed_len = 0U;
                        framed_expected = 0U;
                    }
                }

                if (collecting_framed && framed_expected > 0U && framed_len >= framed_expected) {
                    handle_framed_payload(framed_buf, framed_len);
                    collecting_framed = false;
                    framed_len = 0U;
                    framed_expected = 0U;
                }
            } else if (ch == s_frame_sync[sync_match]) {
                feed_line = false;
                sync_match++;
                if (sync_match >= FRAME_SYNC_LEN) {
                    sync_match = 0U;
                    collecting_framed = true;
                    framed_len = 0U;
                    framed_expected = 0U;
                }
            } else if (sync_match > 0U) {
                feed_line = false;
                sync_match = (ch == s_frame_sync[0]) ? 1U : 0U;
            }

            if (feed_line && (LS_COMM_ENABLE_LINE_FALLBACK != 0)) {
                got_line = feed_char_cmd((char)ch, line, &line_len, sizeof(line));
            }
        } else {
            n = uart_read_bytes(UART_NUM_0, &ch, 1, pdMS_TO_TICKS(20));
            if (n > 0) {
                last_rx_us = esp_timer_get_time();
                s_last_event_us = last_rx_us;
                s_last_link_check_us = last_rx_us;
                s_link_active = true;
                bool feed_line = true;

                if (collecting_framed) {
                    feed_line = false;
                    if (framed_len < sizeof(framed_buf)) {
                        framed_buf[framed_len++] = ch;
                    } else {
                        collecting_framed = false;
                        framed_len = 0U;
                        framed_expected = 0U;
                    }

                    if (collecting_framed && framed_len == 1U) {
                        if (framed_buf[0] == HOST_MODULE_ID) {
                            framed_expected = 2U;
                        } else if (framed_buf[0] > 0U && framed_buf[0] <= FRAME_RX_MAX_LEN) {
                            framed_expected = 1U + (size_t)framed_buf[0] + 1U + FRAME_SEQ_BYTES;
                        } else {
                            collecting_framed = false;
                            framed_len = 0U;
                            framed_expected = 0U;
                        }
                        if (framed_expected > sizeof(framed_buf)) {
                            collecting_framed = false;
                            framed_len = 0U;
                            framed_expected = 0U;
                        }
                    }

                    if (collecting_framed && framed_expected > 0U && framed_len >= framed_expected) {
                        handle_framed_payload(framed_buf, framed_len);
                        collecting_framed = false;
                        framed_len = 0U;
                        framed_expected = 0U;
                    }
                } else if (ch == s_frame_sync[sync_match]) {
                    feed_line = false;
                    sync_match++;
                    if (sync_match >= FRAME_SYNC_LEN) {
                        sync_match = 0U;
                        collecting_framed = true;
                        framed_len = 0U;
                        framed_expected = 0U;
                    }
                } else if (sync_match > 0U) {
                    feed_line = false;
                    sync_match = (ch == s_frame_sync[0]) ? 1U : 0U;
                }

                if (feed_line && (LS_COMM_ENABLE_LINE_FALLBACK != 0)) {
                    got_line = feed_char_cmd((char)ch, line, &line_len, sizeof(line));
                }
            }
        }

        maybe_send_stream_telemetry();

        if (s_link_active && s_cfg.link_timeout_ms > 0U) {
            const int64_t now_us = esp_timer_get_time();
            s_last_link_check_us = now_us;
            if ((now_us - last_rx_us) > ((int64_t)s_cfg.link_timeout_ms * 1000LL)) {
                s_link_active = false;
                s_last_event_us = now_us;
                if (s_cfg.on_link_timeout) {
                    s_cfg.on_link_timeout(s_cfg.ctx);
                }
                ESP_LOGW(TAG, "serial link timeout");
            }
        }

        if (!got_line) {
            continue;
        }

        if (LS_COMM_ENABLE_LINE_FALLBACK != 0) {
            handle_cmd_line(line);
        }
    }
}
