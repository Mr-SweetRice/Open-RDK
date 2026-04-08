#include "ls_comm.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "driver/uart.h"
#include "driver/usb_serial_jtag.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_vfs_dev.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static const char *TAG = "ls_comm";

static ls_comm_cfg_t s_cfg = {0};
static bool s_inited = false;
static bool s_usb_jtag_ready = false;
static SemaphoreHandle_t s_tx_mutex = NULL;

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

    bool locked = false;
    if (s_tx_mutex) {
        locked = (xSemaphoreTake(s_tx_mutex, pdMS_TO_TICKS(20)) == pdTRUE);
    }
    uart_write_bytes(UART_NUM_0, buf, len);
    if (s_usb_jtag_ready) {
        usb_serial_jtag_write_bytes((const uint8_t *)buf, len, 0);
    }
    if (locked) {
        xSemaphoreGive(s_tx_mutex);
    }
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

static void send_sensor_snapshot(void)
{
    ls_comm_sensor_state_t state = {0};
    if (!get_sensor_snapshot(&state)) {
        ls_comm_send_line("ERR");
        return;
    }

    ls_comm_send_line(
        "LS,%u,%u,%u,%u,%u,%.4f,%.4f,%.4f,%.4f,%.4f,%u,%u,%u,%u,%u,%.4f,%.4f,%u,%u,%lu",
        (unsigned)state.raw[0], (unsigned)state.raw[1], (unsigned)state.raw[2],
        (unsigned)state.raw[3], (unsigned)state.raw[4],
        (double)state.value[0], (double)state.value[1], (double)state.value[2],
        (double)state.value[3], (double)state.value[4],
        (unsigned)state.digital[0], (unsigned)state.digital[1], (unsigned)state.digital[2],
        (unsigned)state.digital[3], (unsigned)state.digital[4],
        (double)state.position, (double)state.strength,
        state.line_detected ? 1U : 0U,
        state.calibrating ? 1U : 0U,
        (unsigned long)state.calibration_remaining_ms);
}

static void send_cfg_snapshot(void)
{
    ls_comm_cfg_state_t state = {0};
    if (!get_cfg_snapshot(&state)) {
        ls_comm_send_line("ERR");
        return;
    }
    ls_comm_send_line("CFG,%u,%.4f,%.4f,%lu",
                      (unsigned)state.track_type,
                      (double)state.digital_threshold,
                      (double)state.detect_threshold,
                      (unsigned long)state.calibration_time_ms);
}

static void send_cal_snapshot(void)
{
    ls_comm_cal_state_t state = {0};
    if (!get_cal_snapshot(&state)) {
        ls_comm_send_line("ERR");
        return;
    }
    ls_comm_send_line("CAL,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u",
                      (unsigned)state.min_raw[0], (unsigned)state.min_raw[1],
                      (unsigned)state.min_raw[2], (unsigned)state.min_raw[3],
                      (unsigned)state.min_raw[4], (unsigned)state.max_raw[0],
                      (unsigned)state.max_raw[1], (unsigned)state.max_raw[2],
                      (unsigned)state.max_raw[3], (unsigned)state.max_raw[4]);
}

static void handle_cmd_line(const char *line)
{
    if (!line) {
        return;
    }

    if (strcmp(line, "GET DATA") == 0 || strcmp(line, "GET TELEM") == 0) {
        send_sensor_snapshot();
        return;
    }

    if (strcmp(line, "GET CFG") == 0) {
        send_cfg_snapshot();
        return;
    }

    if (strcmp(line, "GET CAL") == 0) {
        send_cal_snapshot();
        return;
    }

    if (strcmp(line, "START CAL") == 0) {
        if (!s_cfg.start_calibration) {
            ls_comm_send_line("ERR");
            return;
        }
        s_cfg.start_calibration(s_cfg.ctx);
        ls_comm_send_line("OK");
        send_cal_snapshot();
        return;
    }

    if (strcmp(line, "STOP CAL") == 0) {
        if (!s_cfg.stop_calibration) {
            ls_comm_send_line("ERR");
            return;
        }
        s_cfg.stop_calibration(s_cfg.ctx);
        ls_comm_send_line("OK");
        send_cal_snapshot();
        return;
    }

    if (strcmp(line, "SAVE CFG") == 0) {
        ls_comm_send_line((s_cfg.save_cfg && s_cfg.save_cfg(s_cfg.ctx)) ? "OK" : "ERR");
        return;
    }

    if (strcmp(line, "SAVE CAL") == 0) {
        const bool ok = (s_cfg.save_cal && s_cfg.save_cal(s_cfg.ctx));
        ls_comm_send_line(ok ? "OK" : "ERR");
        if (ok) {
            send_cal_snapshot();
        }
        return;
    }

    if (strncmp(line, "SET CFG ", 8) == 0) {
        ls_comm_cfg_state_t state = {0};
        if (!get_cfg_snapshot(&state) || !s_cfg.set_cfg_state) {
            ls_comm_send_line("ERR");
            return;
        }

        const char *cfg_line = line + 8;
        float fvalue = 0.0f;
        int ivalue = 0;

        if (sscanf(cfg_line, "TRACK %d", &ivalue) == 1) {
            if (ivalue < 0 || ivalue > 1) {
                ls_comm_send_line("ERR");
                return;
            }
            state.track_type = (uint8_t)ivalue;
        } else if (sscanf(cfg_line, "DIGITAL_TH %f", &fvalue) == 1) {
            state.digital_threshold = fvalue;
        } else if (sscanf(cfg_line, "DETECT_TH %f", &fvalue) == 1) {
            state.detect_threshold = fvalue;
        } else if (sscanf(cfg_line, "CAL_TIME_MS %d", &ivalue) == 1) {
            if (ivalue < 100) {
                ls_comm_send_line("ERR");
                return;
            }
            state.calibration_time_ms = (uint32_t)ivalue;
        } else {
            ls_comm_send_line("ERR");
            return;
        }

        if (!s_cfg.set_cfg_state(s_cfg.ctx, &state)) {
            ls_comm_send_line("ERR");
            return;
        }
        ls_comm_send_line("OK");
        send_cfg_snapshot();
        return;
    }

    ls_comm_send_line("ERR");
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
    if (err == ESP_OK || err == ESP_ERR_INVALID_STATE) {
        s_usb_jtag_ready = true;
    } else {
        ESP_LOGW(TAG, "usb serial jtag unavailable (%d)", (int)err);
        s_usb_jtag_ready = false;
    }

    s_inited = true;
    return ESP_OK;
}

void ls_comm_task(void *arg)
{
    (void)arg;

    char line[128];
    size_t line_len = 0U;
    int64_t last_rx_us = 0;
    bool link_active = false;

    while (true) {
        bool got_line = false;
        uint8_t ch = 0U;
        int n = 0;

        if (s_usb_jtag_ready) {
            n = usb_serial_jtag_read_bytes(&ch, 1, 0);
        }
        if (n <= 0) {
            n = uart_read_bytes(UART_NUM_0, &ch, 1, pdMS_TO_TICKS(20));
        }
        if (n > 0) {
            last_rx_us = esp_timer_get_time();
            link_active = true;
            got_line = feed_char_cmd((char)ch, line, &line_len, sizeof(line));
        }

        if (link_active && s_cfg.link_timeout_ms > 0U) {
            const int64_t now_us = esp_timer_get_time();
            if ((now_us - last_rx_us) > ((int64_t)s_cfg.link_timeout_ms * 1000LL)) {
                link_active = false;
                if (s_cfg.on_link_timeout) {
                    s_cfg.on_link_timeout(s_cfg.ctx);
                }
            }
        }

        if (got_line) {
            handle_cmd_line(line);
        }
    }
}
