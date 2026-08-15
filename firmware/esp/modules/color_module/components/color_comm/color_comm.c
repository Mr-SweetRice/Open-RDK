#include "color_comm.h"

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>

#include "driver/uart.h"
#include "driver/usb_serial_jtag.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_vfs_dev.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "color_proc.h"

static const char *TAG = "color_comm";

static color_comm_cfg_t s_cfg = {0};
static bool s_inited = false;
static bool s_usb_jtag_ready = false;
static SemaphoreHandle_t s_tx_mutex = NULL;
static bool s_link_active = false;
static int64_t s_last_event_us = 0;
static int64_t s_last_link_check_us = 0;
static bool s_stream_telem_enabled = false;
static uint32_t s_stream_telem_seq = 0U;
static int64_t s_stream_telem_last_tx_us = 0;

#define FRAME_SYNC_0                  0xAAU
#define FRAME_SYNC_1                  0x55U
#define FRAME_SYNC_2                  0xAAU
#define FRAME_SYNC_3                  0x55U
#define FRAME_SYNC_LEN                4U
#define FRAME_MODULE_INFO_TEXT        "color_module|legacy_1.0|color-studio|legacy_1.0"
#define FRAME_RX_MAX_LEN              200U
#define FRAME_SEQ_BYTES               3U
#define FRAME_SEQ_MASK                0x00FFFFFFUL

#define HOST_MODULE_ID                0x00U
#define COLOR_MODULE_ID               0x13U
#define FRAME_HELLO_BYTE              0x01U
#define FRAME_HELLO_ACK_BYTE          0x06U
#define FRAME_MODULE_QUERY_BYTE       0x04U
#define FRAME_MODULE_INFO_PREFIX_BYTE 0x05U
#define FRAME_MODULE_NAME             "color_module"

#define FRAME_MSG_TYPE_CMD            0x01U
#define FRAME_MSG_TYPE_TEST           0x02U
#define FRAME_MSG_TYPE_TELEMETRY      0x03U
#define FRAME_MSG_TYPE_CONTROL        0x04U

#define FRAME_TELEMETRY_TX_PERIOD_US  100000LL

#ifndef COLOR_COMM_ENABLE_LINE_FALLBACK
#define COLOR_COMM_ENABLE_LINE_FALLBACK 0
#endif

static const uint8_t s_frame_sync[FRAME_SYNC_LEN] = {
    FRAME_SYNC_0, FRAME_SYNC_1, FRAME_SYNC_2, FRAME_SYNC_3
};

static void set_link_active_state(bool active)
{
    if (s_link_active == active) {
        return;
    }

    s_link_active = active;
    s_last_event_us = esp_timer_get_time();

    if (active) {
        if (s_cfg.on_link_active) {
            s_cfg.on_link_active(s_cfg.ctx);
        }
        return;
    }

    if (s_cfg.on_link_timeout) {
        s_cfg.on_link_timeout(s_cfg.ctx);
    }
}

static int32_t scale_milli(float value, float min_value, float max_value)
{
    float safe = value;
    if (safe < min_value) {
        safe = min_value;
    }
    if (safe > max_value) {
        safe = max_value;
    }
    return (int32_t)lroundf(safe * 1000.0f);
}

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

void color_comm_send_line(const char *fmt, ...)
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

static bool get_sensor_snapshot(color_comm_sensor_state_t *state)
{
    if (!state || !s_cfg.get_sensor_state) {
        return false;
    }
    memset(state, 0, sizeof(*state));
    return s_cfg.get_sensor_state(s_cfg.ctx, state);
}

static bool get_cfg_snapshot(color_comm_cfg_state_t *state)
{
    if (!state || !s_cfg.get_cfg_state) {
        return false;
    }
    memset(state, 0, sizeof(*state));
    return s_cfg.get_cfg_state(s_cfg.ctx, state);
}

static bool get_cal_snapshot(uint8_t palette_mode, color_comm_cal_state_t *state)
{
    if (!state || !s_cfg.get_cal_state) {
        return false;
    }
    memset(state, 0, sizeof(*state));
    return s_cfg.get_cal_state(s_cfg.ctx, palette_mode, state);
}

static bool get_cal_patch_snapshot(uint8_t palette_mode, int slot, color_comm_cal_patch_state_t *state)
{
    if (!state || !s_cfg.get_cal_patch_state) {
        return false;
    }
    memset(state, 0, sizeof(*state));
    return s_cfg.get_cal_patch_state(s_cfg.ctx, palette_mode, slot, state);
}

static bool get_info_snapshot(color_comm_info_state_t *state)
{
    if (!state || !s_cfg.get_info_state) {
        return false;
    }
    memset(state, 0, sizeof(*state));
    return s_cfg.get_info_state(s_cfg.ctx, state);
}

static bool get_selftest_snapshot(color_comm_selftest_state_t *state)
{
    if (!state || !s_cfg.run_selftest) {
        return false;
    }
    memset(state, 0, sizeof(*state));
    return s_cfg.run_selftest(s_cfg.ctx, state);
}

static bool format_sensor_snapshot(const char *prefix, char *out, size_t out_len)
{
    color_comm_sensor_state_t st = {0};
    if (!out || out_len == 0U) {
        return false;
    }
    out[0] = '\0';

    if (!get_sensor_snapshot(&st)) {
        return false;
    }

    snprintf(out,
             out_len,
             "%s,%u,%d,%u,%d,%u,%d,%u,%d,%u,%u,%u,%u,%u,%u,%u,%u,%d,%d,%d,%u,%u,%u,%u,%u,%u,%u,%d,%u,%lu",
             prefix ? prefix : "DATA",
             (unsigned)st.palette_mode,
             (int)st.detected_slot,
             (unsigned)st.confidence_milli,
             (int)st.top_slot[0],
             (unsigned)st.top_confidence_milli[0],
             (int)st.top_slot[1],
             (unsigned)st.top_confidence_milli[1],
             (int)st.top_slot[2],
             (unsigned)st.top_confidence_milli[2],
             (unsigned)st.raw_r,
             (unsigned)st.raw_g,
             (unsigned)st.raw_b,
             (unsigned)st.raw_c,
             (unsigned)st.norm_r_milli,
             (unsigned)st.norm_g_milli,
             (unsigned)st.norm_b_milli,
             (int)st.lab_l_centi,
             (int)st.lab_a_centi,
             (int)st.lab_b_centi,
             (unsigned)st.luma_milli,
             (unsigned)st.gain,
             (unsigned)st.integration_ms,
             (unsigned)st.led_mode,
             (unsigned)st.led_active,
             (unsigned)st.health_flags,
             (unsigned)st.classifier,
             (int)st.calibration_target_slot,
             (unsigned)st.calibration_samples,
             (unsigned long)st.sample_timestamp_ms);
    return true;
}

static bool format_cfg_snapshot(char *out, size_t out_len)
{
    color_comm_cfg_state_t st = {0};
    if (!out || out_len == 0U) {
        return false;
    }
    out[0] = '\0';

    if (!get_cfg_snapshot(&st)) {
        return false;
    }

    sanitize_text_field(st.sensor_name, sizeof(st.sensor_name));
    snprintf(out,
             out_len,
             "CFG,%s,%lu,%u,%u,%u,%u,%u,%u,%u,%u,%u",
             st.sensor_name,
             (unsigned long)st.sample_period_ms,
             (unsigned)st.led_mode,
             (unsigned)st.gain_mode,
             (unsigned)st.gain,
             (unsigned)st.integration_ms,
             (unsigned)st.classifier,
             (unsigned)scale_milli(st.confidence_threshold, 0.0f, 2.0f),
             (unsigned)st.target_clear,
             (unsigned)st.palette_mode,
             (unsigned)st.patch_sample_count);
    return true;
}

static bool format_cal_snapshot(uint8_t palette_mode, char *out, size_t out_len)
{
    color_comm_cal_state_t st = {0};
    if (!out || out_len == 0U) {
        return false;
    }
    out[0] = '\0';

    if (!get_cal_snapshot(palette_mode, &st)) {
        return false;
    }

    snprintf(out,
             out_len,
             "CAL,%u,%u,%lu,%lu,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u",
             (unsigned)st.palette_mode,
             (unsigned)st.class_count,
             (unsigned long)st.valid_mask,
             (unsigned long)st.enabled_mask,
             st.dark_valid ? 1U : 0U,
             st.white_valid ? 1U : 0U,
             (unsigned)st.dark_r,
             (unsigned)st.dark_g,
             (unsigned)st.dark_b,
             (unsigned)st.dark_c,
             (unsigned)st.white_r,
             (unsigned)st.white_g,
             (unsigned)st.white_b,
             (unsigned)st.white_c);
    return true;
}

static bool format_cal_patch_snapshot(uint8_t palette_mode, int slot, char *out, size_t out_len)
{
    color_comm_cal_patch_state_t st = {0};
    if (!out || out_len == 0U) {
        return false;
    }
    out[0] = '\0';

    if (!get_cal_patch_snapshot(palette_mode, slot, &st)) {
        return false;
    }

    sanitize_text_field(st.name, sizeof(st.name));
    snprintf(out,
             out_len,
             "PATCH,%u,%d,%u,%u,%u,%s,%u,%u,%u,%d,%d,%d,%u",
             (unsigned)st.palette_mode,
             (int)st.slot,
             st.enabled ? 1U : 0U,
             st.valid ? 1U : 0U,
             (unsigned)st.sample_count,
             st.name,
             (unsigned)st.norm_r_milli,
             (unsigned)st.norm_g_milli,
             (unsigned)st.norm_b_milli,
             (int)st.lab_l_centi,
             (int)st.lab_a_centi,
             (int)st.lab_b_centi,
             (unsigned)st.luma_milli);
    return true;
}

static bool format_info_snapshot(char *out, size_t out_len)
{
    color_comm_info_state_t st = {0};
    if (!out || out_len == 0U) {
        return false;
    }
    out[0] = '\0';

    if (!get_info_snapshot(&st)) {
        return false;
    }

    sanitize_text_field(st.name, sizeof(st.name));
    sanitize_text_field(st.module_type, sizeof(st.module_type));
    sanitize_text_field(st.firmware_module, sizeof(st.firmware_module));
    sanitize_text_field(st.firmware_version, sizeof(st.firmware_version));
    sanitize_text_field(st.expected_page, sizeof(st.expected_page));
    sanitize_text_field(st.expected_page_version, sizeof(st.expected_page_version));
    snprintf(out,
             out_len,
             "INFO,%s,%s,%s,%lu,%u,%u,%u,%u,%u,%u,%s,%s,%s",
             st.name,
             st.module_type,
             st.firmware_module,
             (unsigned long)st.module_id,
             (unsigned)st.sensor_id,
             (unsigned)st.health_flags,
             (unsigned)st.i2c_address,
             (unsigned)st.sda_pin,
             (unsigned)st.scl_pin,
             (unsigned)st.led_pin,
             st.firmware_version,
             st.expected_page,
             st.expected_page_version);
    return true;
}

static bool format_selftest_snapshot(char *out, size_t out_len)
{
    color_comm_selftest_state_t st = {0};
    if (!out || out_len == 0U) {
        return false;
    }
    out[0] = '\0';

    if (!get_selftest_snapshot(&st)) {
        return false;
    }

    sanitize_text_field(st.message, sizeof(st.message));
    snprintf(out,
             out_len,
             "SELFTEST,%u,%u,%s",
             st.ok ? 1U : 0U,
             (unsigned)st.sensor_id,
             st.message);
    return true;
}

static bool parse_palette_mode_token(const char *token, uint8_t *out_mode)
{
    if (!token || !out_mode) {
        return false;
    }
    int value = 0;
    if (sscanf(token, "%d", &value) != 1) {
        return false;
    }
    if (!color_proc_mode_is_valid((uint8_t)value)) {
        return false;
    }
    *out_mode = (uint8_t)value;
    return true;
}

static bool parse_led_mode_token(const char *token, uint8_t *out_mode)
{
    if (!token || !out_mode) {
        return false;
    }
    if (strcasecmp(token, "OFF") == 0 || strcmp(token, "0") == 0) {
        *out_mode = 0U;
        return true;
    }
    if (strcasecmp(token, "ON") == 0 || strcmp(token, "1") == 0) {
        *out_mode = 1U;
        return true;
    }
    if (strcasecmp(token, "AUTO") == 0 || strcmp(token, "2") == 0) {
        *out_mode = 2U;
        return true;
    }
    return false;
}

static bool parse_gain_mode_token(const char *token, uint8_t *out_mode)
{
    if (!token || !out_mode) {
        return false;
    }
    if (strcasecmp(token, "MANUAL") == 0 || strcmp(token, "0") == 0) {
        *out_mode = 0U;
        return true;
    }
    if (strcasecmp(token, "AUTO") == 0 || strcmp(token, "1") == 0) {
        *out_mode = 1U;
        return true;
    }
    return false;
}

static bool parse_classifier_token(const char *token, uint8_t *out_classifier)
{
    if (!token || !out_classifier) {
        return false;
    }
    if (strcasecmp(token, "NORM_RGB") == 0 || strcmp(token, "0") == 0) {
        *out_classifier = 0U;
        return true;
    }
    if (strcasecmp(token, "LAB") == 0 || strcmp(token, "1") == 0) {
        *out_classifier = 1U;
        return true;
    }
    return false;
}

static int current_palette_mode(void)
{
    color_comm_cfg_state_t cfg = {0};
    if (!get_cfg_snapshot(&cfg)) {
        return (int)COLOR_PALETTE_MODE_8;
    }
    return (int)cfg.palette_mode;
}

static bool parse_target_token(const char *token, uint8_t palette_mode, int *out_slot)
{
    if (!token || !out_slot) {
        return false;
    }
    if (strcasecmp(token, "DARK") == 0) {
        *out_slot = COLOR_CAL_TARGET_DARK;
        return true;
    }
    if (strcasecmp(token, "WHITE") == 0) {
        *out_slot = COLOR_CAL_TARGET_WHITE;
        return true;
    }

    int value = -1;
    if (sscanf(token, "%d", &value) == 1) {
        if (value >= 0 && value < (int)color_proc_palette_class_count((color_palette_mode_t)palette_mode)) {
            *out_slot = value;
            return true;
        }
    }

    for (int slot = 0; slot < (int)color_proc_palette_class_count((color_palette_mode_t)palette_mode); ++slot) {
        const char *name = color_proc_palette_name((color_palette_mode_t)palette_mode, slot);
        if (name && strcasecmp(name, token) == 0) {
            *out_slot = slot;
            return true;
        }
    }
    return false;
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

static bool try_apply_color_command(const char *line, char *response_out, size_t response_out_len, bool *ok_out)
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
        ok = format_sensor_snapshot("DATA", response_out, response_out_len);
    } else if (strcmp(line, "GET CFG") == 0) {
        ok = format_cfg_snapshot(response_out, response_out_len);
    } else if (strcmp(line, "GET INFO") == 0) {
        ok = format_info_snapshot(response_out, response_out_len);
    } else if (strcmp(line, "RUN SELFTEST") == 0) {
        ok = format_selftest_snapshot(response_out, response_out_len);
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
    } else if (strcmp(line, "RESET CFG") == 0) {
        ok = (s_cfg.reset_cfg && s_cfg.reset_cfg(s_cfg.ctx) &&
              format_cfg_snapshot(response_out, response_out_len));
    } else if (strncmp(line, "RESET CAL", 9) == 0) {
        uint8_t palette_mode = (uint8_t)current_palette_mode();
        const char *arg = line + 9;
        while (*arg == ' ') {
            ++arg;
        }
        if (*arg != '\0' && strcasecmp(arg, "ALL") != 0) {
            if (!parse_palette_mode_token(arg, &palette_mode)) {
                ok = false;
            } else {
                ok = (s_cfg.reset_cal && s_cfg.reset_cal(s_cfg.ctx, palette_mode));
            }
        } else if (s_cfg.reset_cal) {
            ok = s_cfg.reset_cal(s_cfg.ctx, 0U);
        }
    } else if (strncmp(line, "GET CAL PATCH ", 14) == 0) {
        uint8_t palette_mode = (uint8_t)current_palette_mode();
        int slot = -1;
        int parsed_mode = 0;
        int parsed_slot = 0;
        const char *args = line + 14;
        char token0[24] = {0};
        char token1[24] = {0};

        if (sscanf(args, "%23s %23s", token0, token1) >= 1) {
            if (sscanf(args, "%d %d", &parsed_mode, &parsed_slot) == 2 &&
                color_proc_mode_is_valid((uint8_t)parsed_mode)) {
                palette_mode = (uint8_t)parsed_mode;
                slot = parsed_slot;
            } else if (parse_target_token(token0, palette_mode, &slot)) {
                if (token1[0] != '\0' && color_proc_mode_is_valid((uint8_t)atoi(token1))) {
                    palette_mode = (uint8_t)atoi(token1);
                }
            }
        }
        if (slot < 0) {
            ok = false;
        } else {
            ok = format_cal_patch_snapshot(palette_mode, slot, response_out, response_out_len);
        }
    } else if (strncmp(line, "GET CAL", 7) == 0) {
        uint8_t palette_mode = (uint8_t)current_palette_mode();
        const char *arg = line + 7;
        while (*arg == ' ') {
            ++arg;
        }
        if (*arg != '\0') {
            ok = parse_palette_mode_token(arg, &palette_mode);
        } else {
            ok = true;
        }
        if (ok) {
            ok = format_cal_snapshot(palette_mode, response_out, response_out_len);
        }
    } else if (strcasecmp(line, "LED ON") == 0 ||
               strcasecmp(line, "LED OFF") == 0 ||
               strcasecmp(line, "LED AUTO") == 0) {
        color_comm_cfg_state_t cfg = {0};
        uint8_t led_mode = 0U;
        ok = get_cfg_snapshot(&cfg) &&
             parse_led_mode_token(line + 4, &led_mode);
        if (ok) {
            cfg.led_mode = led_mode;
            ok = s_cfg.set_cfg_state && s_cfg.set_cfg_state(s_cfg.ctx, &cfg);
        }
        if (ok) {
            ok = format_cfg_snapshot(response_out, response_out_len);
        }
    } else if (strncmp(line, "SET CFG ", 8) == 0) {
        color_comm_cfg_state_t cfg = {0};
        const char *cfg_line = line + 8;
        float fvalue = 0.0f;
        int ivalue = 0;
        char token[32] = {0};

        if (!get_cfg_snapshot(&cfg) || !s_cfg.set_cfg_state) {
            ok = false;
        } else if (strncmp(cfg_line, "NAME ", 5) == 0) {
            const char *name = cfg_line + 5;
            while (*name == ' ') {
                ++name;
            }
            snprintf(cfg.sensor_name, sizeof(cfg.sensor_name), "%s", name);
            ok = s_cfg.set_cfg_state(s_cfg.ctx, &cfg);
        } else if (sscanf(cfg_line, "SAMPLE_MS %d", &ivalue) == 1) {
            cfg.sample_period_ms = (uint32_t)ivalue;
            ok = s_cfg.set_cfg_state(s_cfg.ctx, &cfg);
        } else if (sscanf(cfg_line, "GAIN %d", &ivalue) == 1) {
            cfg.gain = (uint16_t)ivalue;
            ok = s_cfg.set_cfg_state(s_cfg.ctx, &cfg);
        } else if (sscanf(cfg_line, "INTEGRATION_MS %d", &ivalue) == 1) {
            cfg.integration_ms = (uint16_t)ivalue;
            ok = s_cfg.set_cfg_state(s_cfg.ctx, &cfg);
        } else if (sscanf(cfg_line, "CONF_TH %f", &fvalue) == 1) {
            cfg.confidence_threshold = fvalue;
            ok = s_cfg.set_cfg_state(s_cfg.ctx, &cfg);
        } else if (sscanf(cfg_line, "TARGET_CLEAR %d", &ivalue) == 1) {
            cfg.target_clear = (uint16_t)ivalue;
            ok = s_cfg.set_cfg_state(s_cfg.ctx, &cfg);
        } else if (sscanf(cfg_line, "PALETTE_MODE %d", &ivalue) == 1) {
            cfg.palette_mode = (uint8_t)ivalue;
            ok = s_cfg.set_cfg_state(s_cfg.ctx, &cfg);
        } else if (sscanf(cfg_line, "PATCH_SAMPLES %d", &ivalue) == 1) {
            cfg.patch_sample_count = (uint16_t)ivalue;
            ok = s_cfg.set_cfg_state(s_cfg.ctx, &cfg);
        } else if (sscanf(cfg_line, "LED %31s", token) == 1) {
            ok = parse_led_mode_token(token, &cfg.led_mode) &&
                 s_cfg.set_cfg_state(s_cfg.ctx, &cfg);
        } else if (sscanf(cfg_line, "GAIN_MODE %31s", token) == 1) {
            ok = parse_gain_mode_token(token, &cfg.gain_mode) &&
                 s_cfg.set_cfg_state(s_cfg.ctx, &cfg);
        } else if (sscanf(cfg_line, "CLASSIFIER %31s", token) == 1) {
            ok = parse_classifier_token(token, &cfg.classifier) &&
                 s_cfg.set_cfg_state(s_cfg.ctx, &cfg);
        } else {
            ok = false;
        }

        if (ok) {
            ok = format_cfg_snapshot(response_out, response_out_len);
        }
    } else if (strncmp(line, "SET CAL PATCH ", 14) == 0) {
        uint8_t palette_mode = (uint8_t)current_palette_mode();
        int slot = -1;
        const char *token = line + 14;
        while (*token == ' ') {
            ++token;
        }
        ok = parse_target_token(token, palette_mode, &slot) &&
             s_cfg.set_cal_target &&
             s_cfg.set_cal_target(s_cfg.ctx, slot);
    } else if (strncmp(line, "COMMIT CAL PATCH ", 17) == 0) {
        uint8_t palette_mode = (uint8_t)current_palette_mode();
        int slot = -1;
        const char *token = line + 17;
        while (*token == ' ') {
            ++token;
        }
        ok = parse_target_token(token, palette_mode, &slot) &&
             s_cfg.commit_cal_target &&
             s_cfg.commit_cal_target(s_cfg.ctx, slot);
        if (ok) {
            ok = format_cal_snapshot(palette_mode, response_out, response_out_len);
        }
    } else {
        int mode = 0;
        int slot = 0;
        int r = 0;
        int g = 0;
        int b = 0;
        int c = 0;
        int nr = 0;
        int ng = 0;
        int nb = 0;
        int luma = 0;
        int ll = 0;
        int la = 0;
        int lb = 0;
        int samples = 0;

        if (sscanf(line, "SET CAL DARK %d %d %d %d %d", &mode, &r, &g, &b, &c) == 5) {
            ok = color_proc_mode_is_valid((uint8_t)mode) &&
                 s_cfg.set_cal_reference &&
                 s_cfg.set_cal_reference(s_cfg.ctx,
                                         (uint8_t)mode,
                                         COLOR_CAL_TARGET_DARK,
                                         (uint16_t)r,
                                         (uint16_t)g,
                                         (uint16_t)b,
                                         (uint16_t)c);
        } else if (sscanf(line, "SET CAL WHITE %d %d %d %d %d", &mode, &r, &g, &b, &c) == 5) {
            ok = color_proc_mode_is_valid((uint8_t)mode) &&
                 s_cfg.set_cal_reference &&
                 s_cfg.set_cal_reference(s_cfg.ctx,
                                         (uint8_t)mode,
                                         COLOR_CAL_TARGET_WHITE,
                                         (uint16_t)r,
                                         (uint16_t)g,
                                         (uint16_t)b,
                                         (uint16_t)c);
        } else if (sscanf(line,
                          "SET CAL PROTO %d %d %d %d %d %d %d %d %d %d",
                          &mode,
                          &slot,
                          &nr,
                          &ng,
                          &nb,
                          &luma,
                          &ll,
                          &la,
                          &lb,
                          &samples) == 10) {
            ok = color_proc_mode_is_valid((uint8_t)mode) &&
                 s_cfg.set_cal_patch_data &&
                 s_cfg.set_cal_patch_data(s_cfg.ctx,
                                          (uint8_t)mode,
                                          slot,
                                          (uint16_t)nr,
                                          (uint16_t)ng,
                                          (uint16_t)nb,
                                          (uint16_t)luma,
                                          (int16_t)ll,
                                          (int16_t)la,
                                          (int16_t)lb,
                                          (uint16_t)samples);
        } else {
            handled = false;
        }
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
    if (payload_len > (sizeof(frame) - FRAME_SYNC_LEN - 1U)) {
        payload_len = sizeof(frame) - FRAME_SYNC_LEN - 1U;
    }

    size_t idx = 0U;
    memcpy(&frame[idx], s_frame_sync, FRAME_SYNC_LEN);
    idx += FRAME_SYNC_LEN;
    frame[idx++] = COLOR_MODULE_ID;
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

    char msg_text[FRAME_RX_MAX_LEN + 1U];
    memcpy(msg_text, &frame_payload[1], msg_len);
    msg_text[msg_len] = '\0';

    const uint8_t msg_type = frame_payload[1U + msg_len];
    const size_t seq_offset = 1U + (size_t)msg_len + 1U;
    uint32_t seq = ((uint32_t)frame_payload[seq_offset] << 16U) |
                   ((uint32_t)frame_payload[seq_offset + 1U] << 8U) |
                   ((uint32_t)frame_payload[seq_offset + 2U]);

    if (msg_type == FRAME_MSG_TYPE_CMD) {
        char response[FRAME_RX_MAX_LEN + 1U];
        bool ok = false;
        bool handled = try_apply_color_command(msg_text, response, sizeof(response), &ok);
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

    if (msg_type == FRAME_MSG_TYPE_CONTROL) {
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
    if (!format_sensor_snapshot("TEL", msg, sizeof(msg))) {
        snprintf(msg, sizeof(msg), "TELEMETRY");
    }

    send_stream_frame_text(FRAME_MSG_TYPE_TELEMETRY, s_stream_telem_seq, msg);
    s_stream_telem_seq = (s_stream_telem_seq + 1U) & FRAME_SEQ_MASK;
}

static void handle_cmd_line(const char *line)
{
    char response[196];
    bool ok = false;
    bool handled = try_apply_color_command(line, response, sizeof(response), &ok);

    if (!handled) {
        color_comm_send_line("ERR");
        return;
    }

    if (response[0] != '\0') {
        color_comm_send_line("%s", response);
    } else {
        color_comm_send_line(ok ? "OK" : "ERR");
    }
}

esp_err_t color_comm_init(const color_comm_cfg_t *cfg)
{
    if (!cfg) {
        return ESP_ERR_INVALID_ARG;
    }

    s_cfg = *cfg;
    if (s_cfg.link_timeout_ms == 0U) {
        s_cfg.link_timeout_ms = COLOR_COMM_DEFAULT_LINK_TIMEOUT_MS;
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
    ESP_ERROR_CHECK(uart_set_pin(UART_NUM_0,
                                 UART_PIN_NO_CHANGE,
                                 UART_PIN_NO_CHANGE,
                                 UART_PIN_NO_CHANGE,
                                 UART_PIN_NO_CHANGE));

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

    ESP_LOGI(TAG,
             "line fallback %s",
             (COLOR_COMM_ENABLE_LINE_FALLBACK != 0) ? "ENABLED" : "DISABLED");

    s_stream_telem_enabled = false;
    s_stream_telem_seq = 0U;
    s_stream_telem_last_tx_us = 0;
    s_link_active = false;
    s_last_event_us = 0;
    s_last_link_check_us = 0;
    s_inited = true;
    return ESP_OK;
}

void color_comm_task(void *arg)
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
            s_last_link_check_us = last_rx_us;
            set_link_active_state(true);
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

            if (feed_line && (COLOR_COMM_ENABLE_LINE_FALLBACK != 0)) {
                got_line = feed_char_cmd((char)ch, line, &line_len, sizeof(line));
            }
        } else {
            n = uart_read_bytes(UART_NUM_0, &ch, 1, pdMS_TO_TICKS(20));
            if (n > 0) {
                last_rx_us = esp_timer_get_time();
                s_last_link_check_us = last_rx_us;
                set_link_active_state(true);
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

                if (feed_line && (COLOR_COMM_ENABLE_LINE_FALLBACK != 0)) {
                    got_line = feed_char_cmd((char)ch, line, &line_len, sizeof(line));
                }
            }
        }

        maybe_send_stream_telemetry();

        if (s_link_active && s_cfg.link_timeout_ms > 0U) {
            const int64_t now_us = esp_timer_get_time();
            s_last_link_check_us = now_us;
            if ((now_us - last_rx_us) > ((int64_t)s_cfg.link_timeout_ms * 1000LL)) {
                set_link_active_state(false);
                ESP_LOGW(TAG, "serial link timeout");
            }
        }

        if (!got_line) {
            continue;
        }

        if (COLOR_COMM_ENABLE_LINE_FALLBACK != 0) {
            handle_cmd_line(line);
        }
    }
}
