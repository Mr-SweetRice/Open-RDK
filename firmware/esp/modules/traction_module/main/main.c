#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/portmacro.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "driver/uart.h"
#include "esp_vfs_dev.h"
#include "driver/usb_serial_jtag.h"
#include "traction_hal.h"
#include "traction_control.h"
#include <math.h>
#include <string.h>
#include <stdio.h>
#include <stdarg.h>

// Pinos (como você definiu)
#define DRIVER_SLEEP_PIN     GPIO_NUM_21
#define DRIVE_ENABLE_PIN1    GPIO_NUM_0
#define DRIVE_ENABLE_PIN2    GPIO_NUM_1

// PWM config
#define PWM_FREQ_HZ          20000
#define PWM_RES              LEDC_TIMER_10_BIT

// Limites
#define MOTOR_MAX_OUTPUT_PCT 100

// Linearizacao saida->rpm (curva ajustada)
#define ENABLE_OUTPUT_LINEARIZATION 1
#define RPM_AT_100_PCT 199.89f
#define LIN_DEADBAND_PCT 40.0f
#define LIN_A 0.5855839f
#define LIN_B 1.42f

// Controle de velocidade (ajuste conforme necessidade)
#define CONTROL_PERIOD_MS    33
#define PID_KP_DEFAULT       1.0f
#define PID_KI_DEFAULT       2.2f
#define PID_KD_DEFAULT       0.00f
#define PID_D_ALPHA_DEFAULT  0.2f
#define SETPOINT_DEFAULT_RPM 50.0f

#define ENABLE_ENCODER_LOG   0
#define NVS_NAMESPACE        "pid"

static const char *TAG = "enc";
static const char *TAGM = "main";

typedef struct {
    float kp;
    float ki;
    float kd;
    float setpoint_rpm;
} pid_store_t;

static traction_pid_cfg_t s_pid_cfg;
static float s_setpoint_rpm = SETPOINT_DEFAULT_RPM;
static uint32_t s_pid_version = 0;
static portMUX_TYPE s_pid_mux = portMUX_INITIALIZER_UNLOCKED;
static int s_force_out_pct = -1;
static bool s_usb_jtag_ready = false;
static QueueHandle_t s_nvs_queue = NULL;
static bool s_nvs_ready = false;

static float linearize_output_percent(float cmd_pct)
{
    if (cmd_pct <= 0.0f) return 0.0f;
    if (cmd_pct >= 100.0f) return 100.0f;
    float desired_rpm = (cmd_pct / 100.0f) * RPM_AT_100_PCT;
    if (desired_rpm <= 0.01f) return 0.0f;
    float out = LIN_DEADBAND_PCT + powf(desired_rpm / LIN_A, 1.0f / LIN_B);
    if (out < 0.0f) out = 0.0f;
    if (out > 100.0f) out = 100.0f;
    return out;
}

static bool feed_char_cmd(char ch, char *buf, size_t *len, size_t max_len)
{
    if (ch == '\r' || ch == '\n') {
        if (*len == 0) return false;
        buf[*len] = '\0';
        *len = 0;
        return true;
    }
    if (*len < (max_len - 1)) {
        buf[(*len)++] = ch;
    } else {
        *len = 0;
    }
    return false;
}

static void serial_send_line(const char *fmt, ...)
{
    char buf[128];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n <= 0) return;
    if (n > (int)(sizeof(buf) - 1)) n = (int)(sizeof(buf) - 1);
    buf[n++] = '\n';

    uart_write_bytes(UART_NUM_0, buf, n);
    if (s_usb_jtag_ready) {
        usb_serial_jtag_write_bytes((const uint8_t *)buf, n, 0);
    }
}

static void encoder_monitor_task(void *arg)
{
    (void)arg;

    int64_t last_count = 0;
    int64_t last_t_us  = esp_timer_get_time();

    while (1) {
        vTaskDelay(pdMS_TO_TICKS(100));

        int64_t c = 0;
        ESP_ERROR_CHECK(traction_encoder_get_count(&c));

        int64_t now_us = esp_timer_get_time();
        float dt = (now_us - last_t_us) / 1e6f;

        int64_t dc = c - last_count;

        float pos_rev = traction_counts_to_output_rev(c);
        float vel_rpm = traction_delta_counts_to_output_rpm(dc, dt);

        ESP_LOGI(TAG, "count=%lld  pos=%.4f rev  vel=%.2f rpm",
                 (long long)c, (double)pos_rev, (double)vel_rpm);

        last_count = c;
        last_t_us = now_us;
    }
}

static void pid_defaults(void)
{
    s_pid_cfg.kp = PID_KP_DEFAULT;
    s_pid_cfg.ki = PID_KI_DEFAULT;
    s_pid_cfg.kd = PID_KD_DEFAULT;
    s_pid_cfg.d_alpha = PID_D_ALPHA_DEFAULT;
    s_pid_cfg.out_min = 0.0f;
    s_pid_cfg.out_max = (float)MOTOR_MAX_OUTPUT_PCT;
    s_pid_cfg.i_min = -500.0f;
    s_pid_cfg.i_max = 500.0f;
    s_setpoint_rpm = SETPOINT_DEFAULT_RPM;
}

static void pid_load_from_nvs(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        err = nvs_flash_init();
    }
    if (err != ESP_OK) {
        ESP_LOGW(TAGM, "nvs init failed (%d), usando defaults", (int)err);
        s_nvs_ready = false;
        return;
    }
    s_nvs_ready = true;

    nvs_handle_t h = 0;
    err = nvs_open(NVS_NAMESPACE, NVS_READONLY, &h);
    if (err != ESP_OK) {
        ESP_LOGW(TAGM, "nvs open failed (%d), usando defaults", (int)err);
        return;
    }

    pid_store_t st = {0};
    size_t len = sizeof(st);
    err = nvs_get_blob(h, "pid", &st, &len);
    nvs_close(h);

    if (err == ESP_OK && len == sizeof(st)) {
        s_pid_cfg.kp = st.kp;
        s_pid_cfg.ki = st.ki;
        s_pid_cfg.kd = st.kd;
        s_pid_cfg.d_alpha = PID_D_ALPHA_DEFAULT;
        s_pid_cfg.out_min = 0.0f;
        s_pid_cfg.out_max = (float)MOTOR_MAX_OUTPUT_PCT;
        s_pid_cfg.i_min = -500.0f;
        s_pid_cfg.i_max = 500.0f;
        s_setpoint_rpm = st.setpoint_rpm;
        ESP_LOGI(TAGM, "pid carregado do NVS");
    } else {
        ESP_LOGW(TAGM, "pid nao encontrado no NVS, usando defaults");
    }
}

static esp_err_t pid_save_to_nvs(float kp, float ki, float kd, float setpoint)
{
    nvs_handle_t h = 0;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        ESP_LOGW(TAGM, "nvs open failed (%d), nao salvou", (int)err);
        return err;
    }

    pid_store_t st = {
        .kp = kp,
        .ki = ki,
        .kd = kd,
        .setpoint_rpm = setpoint,
    };
    err = nvs_set_blob(h, "pid", &st, sizeof(st));
    if (err == ESP_OK) {
        err = nvs_commit(h);
        ESP_LOGI(TAGM, "pid salvo no NVS");
    } else {
        ESP_LOGW(TAGM, "nvs set failed (%d)", (int)err);
    }
    nvs_close(h);
    return err;
}

static void nvs_save_task(void *arg)
{
    (void)arg;

    pid_store_t st = {0};
    while (1) {
        if (xQueueReceive(s_nvs_queue, &st, portMAX_DELAY) == pdTRUE) {
            if (!s_nvs_ready) {
                serial_send_line("S,ERR,%d", (int)ESP_ERR_INVALID_STATE);
                continue;
            }
            serial_send_line("S,START");
            esp_err_t werr = pid_save_to_nvs(st.kp, st.ki, st.kd, st.setpoint_rpm);
            if (werr == ESP_OK) {
                serial_send_line("S,OK");
            } else {
                serial_send_line("S,ERR,%d", (int)werr);
            }
            serial_send_line("S,END");
        }
    }
}

static void speed_control_task(void *arg)
{
    (void)arg;

    traction_pid_t pid;
    traction_pid_cfg_t cfg = {0};
    uint32_t last_version = 0;

    traction_motor_sleep(false);

    int64_t last_count = 0;
    int64_t last_t_us  = esp_timer_get_time();

    portENTER_CRITICAL(&s_pid_mux);
    cfg = s_pid_cfg;
    last_version = s_pid_version;
    portEXIT_CRITICAL(&s_pid_mux);
    traction_pid_init(&pid, &cfg);

    while (1) {
        vTaskDelay(pdMS_TO_TICKS(CONTROL_PERIOD_MS));

        int64_t c = 0;
        ESP_ERROR_CHECK(traction_encoder_get_count(&c));

        int64_t now_us = esp_timer_get_time();
        float dt = (now_us - last_t_us) / 1e6f;
        int64_t dc = c - last_count;

        float rpm = 0.0f;
        if (dt > 0.0f) {
            rpm = traction_delta_counts_to_output_rpm(dc, dt);
        }
        if (rpm > -5.0f && rpm < 5.0f) {
            rpm = 0.0f;
        }

        float target = 0.0f;
        portENTER_CRITICAL(&s_pid_mux);
        cfg = s_pid_cfg;
        target = s_setpoint_rpm;
        if (s_pid_version != last_version) {
            last_version = s_pid_version;
            traction_pid_init(&pid, &cfg);
        }
        portEXIT_CRITICAL(&s_pid_mux);

        traction_dir_t dir = (target >= 0.0f) ? TRACTION_DIR_CW : TRACTION_DIR_CCW;
        float target_abs = fabsf(target);
        float rpm_abs = fabsf(rpm);

        int force_out = -1;
        portENTER_CRITICAL(&s_pid_mux);
        force_out = s_force_out_pct;
        portEXIT_CRITICAL(&s_pid_mux);

        float cmd = 0.0f;
        float cmd_pwm = 0.0f;
        if (force_out >= 0) {
            cmd = (float)force_out;
#if ENABLE_OUTPUT_LINEARIZATION
            cmd_pwm = linearize_output_percent(cmd);
#else
            cmd_pwm = cmd;
#endif
            traction_motor_set((int)cmd_pwm, dir);
        } else if (target_abs <= 0.0f) {
            traction_motor_coast();
            traction_pid_reset(&pid);
        } else {
            cmd = traction_pid_update(&pid, target_abs, rpm_abs, dt);
#if ENABLE_OUTPUT_LINEARIZATION
            cmd_pwm = linearize_output_percent(cmd);
#else
            cmd_pwm = cmd;
#endif
            traction_motor_set((int)cmd_pwm, dir);
        }

        serial_send_line("T,%.2f,%.2f,%.2f", (double)target, (double)rpm, (double)cmd_pwm);

        last_count = c;
        last_t_us = now_us;
    }
}

static void serial_cmd_task(void *arg)
{
    (void)arg;

    uart_config_t uart_cfg = {
        .baud_rate = 115200,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    uart_param_config(UART_NUM_0, &uart_cfg);
    uart_set_pin(UART_NUM_0, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    esp_err_t uerr = uart_driver_install(UART_NUM_0, 1024, 0, 0, NULL, 0);
    if (uerr != ESP_OK && uerr != ESP_ERR_INVALID_STATE) {
        ESP_LOGW(TAGM, "uart install failed (%d)", (int)uerr);
    }
    esp_vfs_dev_uart_use_driver(UART_NUM_0);

    usb_serial_jtag_driver_config_t usb_cfg = {
        .rx_buffer_size = 1024,
        .tx_buffer_size = 1024,
    };
    esp_err_t jerr = usb_serial_jtag_driver_install(&usb_cfg);
    if (jerr == ESP_OK || jerr == ESP_ERR_INVALID_STATE) {
        s_usb_jtag_ready = true;
    } else {
        ESP_LOGW(TAGM, "usb serial jtag install failed (%d)", (int)jerr);
    }

    char line[128];
    size_t line_len = 0;

    while (1) {
        bool got_line = false;

        uint8_t ch = 0;
        int n = usb_serial_jtag_read_bytes(&ch, 1, 0);
        if (n > 0) {
            got_line = feed_char_cmd((char)ch, line, &line_len, sizeof(line));
        } else {
            n = uart_read_bytes(UART_NUM_0, &ch, 1, pdMS_TO_TICKS(20));
            if (n > 0) {
                got_line = feed_char_cmd((char)ch, line, &line_len, sizeof(line));
            }
        }

        if (!got_line) {
            continue;
        }

        float val = 0.0f;
        if (strncmp(line, "GET", 3) == 0) {
            portENTER_CRITICAL(&s_pid_mux);
            float kp = s_pid_cfg.kp;
            float ki = s_pid_cfg.ki;
            float kd = s_pid_cfg.kd;
            float sp = s_setpoint_rpm;
            portEXIT_CRITICAL(&s_pid_mux);
            serial_send_line("P,%.4f,%.4f,%.4f,%.2f", (double)kp, (double)ki, (double)kd, (double)sp);
            continue;
        }

        if (sscanf(line, "SET KP %f", &val) == 1) {
            portENTER_CRITICAL(&s_pid_mux);
            s_pid_cfg.kp = val;
            s_pid_version++;
            float kp = s_pid_cfg.kp;
            float ki = s_pid_cfg.ki;
            float kd = s_pid_cfg.kd;
            float sp = s_setpoint_rpm;
            portEXIT_CRITICAL(&s_pid_mux);
            serial_send_line("OK");
            serial_send_line("P,%.4f,%.4f,%.4f,%.2f", (double)kp, (double)ki, (double)kd, (double)sp);
            continue;
        }
        if (sscanf(line, "SET KI %f", &val) == 1) {
            portENTER_CRITICAL(&s_pid_mux);
            s_pid_cfg.ki = val;
            s_pid_version++;
            float kp = s_pid_cfg.kp;
            float ki = s_pid_cfg.ki;
            float kd = s_pid_cfg.kd;
            float sp = s_setpoint_rpm;
            portEXIT_CRITICAL(&s_pid_mux);
            serial_send_line("OK");
            serial_send_line("P,%.4f,%.4f,%.4f,%.2f", (double)kp, (double)ki, (double)kd, (double)sp);
            continue;
        }
        if (sscanf(line, "SET KD %f", &val) == 1) {
            portENTER_CRITICAL(&s_pid_mux);
            s_pid_cfg.kd = val;
            s_pid_version++;
            float kp = s_pid_cfg.kp;
            float ki = s_pid_cfg.ki;
            float kd = s_pid_cfg.kd;
            float sp = s_setpoint_rpm;
            portEXIT_CRITICAL(&s_pid_mux);
            serial_send_line("OK");
            serial_send_line("P,%.4f,%.4f,%.4f,%.2f", (double)kp, (double)ki, (double)kd, (double)sp);
            continue;
        }
        if (sscanf(line, "SET SP %f", &val) == 1) {
            portENTER_CRITICAL(&s_pid_mux);
            s_setpoint_rpm = val;
            float kp = s_pid_cfg.kp;
            float ki = s_pid_cfg.ki;
            float kd = s_pid_cfg.kd;
            float sp = s_setpoint_rpm;
            portEXIT_CRITICAL(&s_pid_mux);
            serial_send_line("OK");
            serial_send_line("P,%.4f,%.4f,%.4f,%.2f", (double)kp, (double)ki, (double)kd, (double)sp);
            continue;
        }
        if (sscanf(line, "SET OUT %f", &val) == 1) {
            int out = (int)val;
            if (out < 0) out = 0;
            if (out > MOTOR_MAX_OUTPUT_PCT) out = MOTOR_MAX_OUTPUT_PCT;
            portENTER_CRITICAL(&s_pid_mux);
            s_force_out_pct = out;
            portEXIT_CRITICAL(&s_pid_mux);
            serial_send_line("OK");
            continue;
        }
        if (strncmp(line, "CLR OUT", 7) == 0) {
            portENTER_CRITICAL(&s_pid_mux);
            s_force_out_pct = -1;
            portEXIT_CRITICAL(&s_pid_mux);
            serial_send_line("OK");
            continue;
        }
        if (strncmp(line, "SAVE", 4) == 0) {
            portENTER_CRITICAL(&s_pid_mux);
            float kp = s_pid_cfg.kp;
            float ki = s_pid_cfg.ki;
            float kd = s_pid_cfg.kd;
            float sp = s_setpoint_rpm;
            portEXIT_CRITICAL(&s_pid_mux);
            pid_store_t st = {
                .kp = kp,
                .ki = ki,
                .kd = kd,
                .setpoint_rpm = sp,
            };
            if (s_nvs_queue) {
                xQueueOverwrite(s_nvs_queue, &st);
                serial_send_line("S,ENQ");
                serial_send_line("OK");
            } else {
                serial_send_line("ERR");
                serial_send_line("E,%d", (int)ESP_ERR_INVALID_STATE);
            }
            continue;
        }

        serial_send_line("ERR");
    }
}

void app_main(void)
{
    pid_defaults();
    pid_load_from_nvs();

    esp_log_level_set("*", ESP_LOG_WARN);

    traction_motor_cfg_t cfg = {
        .sleep_gpio      = DRIVER_SLEEP_PIN,
        .pwm_gpio_a      = DRIVE_ENABLE_PIN1,
        .pwm_gpio_b      = DRIVE_ENABLE_PIN2,

        .speed_mode      = LEDC_LOW_SPEED_MODE,
        .timer_num       = LEDC_TIMER_0,
        .channel_a       = LEDC_CHANNEL_1,
        .channel_b       = LEDC_CHANNEL_2,
        .duty_resolution = PWM_RES,
        .pwm_freq_hz     = PWM_FREQ_HZ,
    };

    ESP_ERROR_CHECK(traction_motor_init(&cfg));

    s_nvs_queue = xQueueCreate(1, sizeof(pid_store_t));
    xTaskCreate(nvs_save_task, "nvs_save", 8192, NULL, 5, NULL);

    // Task de controle PID de velocidade
    xTaskCreate(speed_control_task, "speed_ctrl", 4096, NULL, 8, NULL);
    xTaskCreate(serial_cmd_task, "serial_cmd", 4096, NULL, 9, NULL);

    traction_encoder_cfg_t enc = {
        .pin_a = GPIO_NUM_5,
        .pin_b = GPIO_NUM_6,
        .pullup = true,               // ajuste conforme seu encoder
        .invert_direction = false,    // se o sinal ficar invertido, troque para true
        .counts_per_motor_rev = 44,   // 11 * 4
        .gear_ratio = 45,             // 45:1
    };

    ESP_ERROR_CHECK(traction_encoder_init(&enc));
#if ENABLE_ENCODER_LOG
    xTaskCreate(encoder_monitor_task, "enc_mon", 4096, NULL, 6, NULL);
#endif
}
