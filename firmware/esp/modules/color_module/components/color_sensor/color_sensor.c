#include "color_sensor.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_check.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define TCS3472_COMMAND_BIT 0x80U
#define TCS3472_ENABLE_REG  0x00U
#define TCS3472_ATIME_REG   0x01U
#define TCS3472_ID_REG      0x12U
#define TCS3472_STATUS_REG  0x13U
#define TCS3472_CDATAL_REG  0x14U
#define TCS3472_CONTROL_REG 0x0FU

#define TCS3472_ENABLE_PON  0x01U
#define TCS3472_ENABLE_AEN  0x02U
#define TCS3472_STATUS_AVALID 0x01U

static const char *TAG = "color_sensor";

struct color_sensor_ctx_t {
    color_sensor_cfg_t cfg;
    portMUX_TYPE mux;
    bool configured;
    bool sensor_ok;
    bool led_on;
    uint8_t sensor_id;
    uint16_t gain;
    uint16_t integration_ms;
    uint32_t error_count;
    char last_error[32];
};

static uint8_t gain_to_reg(uint16_t gain, uint16_t *resolved_gain)
{
    if (gain <= 1U) {
        if (resolved_gain) {
            *resolved_gain = 1U;
        }
        return 0U;
    }
    if (gain <= 4U) {
        if (resolved_gain) {
            *resolved_gain = 4U;
        }
        return 1U;
    }
    if (gain <= 16U) {
        if (resolved_gain) {
            *resolved_gain = 16U;
        }
        return 2U;
    }
    if (resolved_gain) {
        *resolved_gain = 60U;
    }
    return 3U;
}

static uint8_t integration_to_atime(uint16_t integration_ms, uint16_t *resolved_ms)
{
    float safe_ms = (integration_ms < 3U) ? 3.0f : (float)integration_ms;
    float cycles_f = safe_ms / 2.4f;
    if (cycles_f < 1.0f) {
        cycles_f = 1.0f;
    }
    if (cycles_f > 256.0f) {
        cycles_f = 256.0f;
    }
    uint16_t cycles = (uint16_t)lroundf(cycles_f);
    if (cycles == 0U) {
        cycles = 1U;
    }
    if (resolved_ms) {
        *resolved_ms = (uint16_t)lroundf((float)cycles * 2.4f);
    }
    return (uint8_t)(256U - cycles);
}

static void set_last_error(color_sensor_handle_t handle, const char *text, bool sensor_ok)
{
    if (!handle) {
        return;
    }
    portENTER_CRITICAL(&handle->mux);
    handle->sensor_ok = sensor_ok;
    if (!sensor_ok) {
        handle->error_count++;
    }
    snprintf(handle->last_error, sizeof(handle->last_error), "%s", text ? text : "");
    portEXIT_CRITICAL(&handle->mux);
}

static esp_err_t write_reg8(color_sensor_handle_t handle, uint8_t reg, uint8_t value)
{
    uint8_t payload[2] = {TCS3472_COMMAND_BIT | reg, value};
    return i2c_master_write_to_device(handle->cfg.i2c_port,
                                      handle->cfg.i2c_address,
                                      payload,
                                      sizeof(payload),
                                      pdMS_TO_TICKS(30));
}

static esp_err_t read_reg(color_sensor_handle_t handle, uint8_t reg, uint8_t *out_data, size_t len)
{
    uint8_t reg_addr = TCS3472_COMMAND_BIT | reg;
    return i2c_master_write_read_device(handle->cfg.i2c_port,
                                        handle->cfg.i2c_address,
                                        &reg_addr,
                                        1U,
                                        out_data,
                                        len,
                                        pdMS_TO_TICKS(30));
}

static esp_err_t apply_led_state(color_sensor_handle_t handle, bool enabled)
{
    if (!handle) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err = gpio_set_level(handle->cfg.led_pin, enabled ? 1 : 0);
    if (err == ESP_OK) {
        portENTER_CRITICAL(&handle->mux);
        handle->led_on = enabled;
        portEXIT_CRITICAL(&handle->mux);
    }
    return err;
}

esp_err_t color_sensor_set_led_state(color_sensor_handle_t handle, bool enabled)
{
    return apply_led_state(handle, enabled);
}

static esp_err_t probe_and_configure(color_sensor_handle_t handle)
{
    if (!handle) {
        return ESP_ERR_INVALID_ARG;
    }

    uint8_t sensor_id = 0U;
    esp_err_t err = read_reg(handle, TCS3472_ID_REG, &sensor_id, 1U);
    if (err != ESP_OK) {
        set_last_error(handle, "read-id", false);
        return err;
    }

    uint16_t resolved_gain = 1U;
    uint16_t resolved_ms = 24U;
    const uint8_t gain_reg = gain_to_reg(handle->gain, &resolved_gain);
    const uint8_t atime = integration_to_atime(handle->integration_ms, &resolved_ms);

    err = write_reg8(handle, TCS3472_ENABLE_REG, TCS3472_ENABLE_PON);
    if (err != ESP_OK) {
        set_last_error(handle, "enable-pon", false);
        return err;
    }
    vTaskDelay(pdMS_TO_TICKS(3));

    err = write_reg8(handle, TCS3472_ATIME_REG, atime);
    if (err != ESP_OK) {
        set_last_error(handle, "write-atime", false);
        return err;
    }

    err = write_reg8(handle, TCS3472_CONTROL_REG, gain_reg);
    if (err != ESP_OK) {
        set_last_error(handle, "write-gain", false);
        return err;
    }

    err = write_reg8(handle, TCS3472_ENABLE_REG, (uint8_t)(TCS3472_ENABLE_PON | TCS3472_ENABLE_AEN));
    if (err != ESP_OK) {
        set_last_error(handle, "enable-aen", false);
        return err;
    }

    portENTER_CRITICAL(&handle->mux);
    handle->sensor_id = sensor_id;
    handle->gain = resolved_gain;
    handle->integration_ms = resolved_ms;
    handle->configured = true;
    handle->sensor_ok = true;
    snprintf(handle->last_error, sizeof(handle->last_error), "ok");
    portEXIT_CRITICAL(&handle->mux);
    return ESP_OK;
}

esp_err_t color_sensor_init(const color_sensor_cfg_t *cfg, color_sensor_handle_t *out_handle)
{
    if (!cfg || !out_handle) {
        return ESP_ERR_INVALID_ARG;
    }

    color_sensor_handle_t handle = calloc(1, sizeof(*handle));
    if (!handle) {
        return ESP_ERR_NO_MEM;
    }

    handle->cfg = *cfg;
    handle->mux = (portMUX_TYPE)portMUX_INITIALIZER_UNLOCKED;
    handle->gain = cfg->default_gain ? cfg->default_gain : 4U;
    handle->integration_ms = cfg->default_integration_ms ? cfg->default_integration_ms : 50U;
    snprintf(handle->last_error, sizeof(handle->last_error), "boot");

    const i2c_config_t i2c_cfg = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = cfg->sda_pin,
        .scl_io_num = cfg->scl_pin,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = cfg->i2c_freq_hz ? cfg->i2c_freq_hz : COLOR_SENSOR_DEFAULT_I2C_FREQ_HZ,
        .clk_flags = 0,
    };

    esp_err_t err = i2c_param_config(cfg->i2c_port, &i2c_cfg);
    if (err != ESP_OK) {
        free(handle);
        return err;
    }
    err = i2c_driver_install(cfg->i2c_port, I2C_MODE_MASTER, 0, 0, 0);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        free(handle);
        return err;
    }

    gpio_config_t led_cfg = {
        .pin_bit_mask = (1ULL << cfg->led_pin),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&led_cfg));
    ESP_ERROR_CHECK(gpio_set_level(cfg->led_pin, 0));

    err = probe_and_configure(handle);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "initial probe failed (%d)", (int)err);
    }

    *out_handle = handle;
    return ESP_OK;
}

esp_err_t color_sensor_set_exposure(color_sensor_handle_t handle, uint16_t gain, uint16_t integration_ms)
{
    if (!handle) {
        return ESP_ERR_INVALID_ARG;
    }

    uint16_t resolved_gain = gain;
    uint16_t resolved_ms = integration_ms;
    (void)gain_to_reg(gain, &resolved_gain);
    (void)integration_to_atime(integration_ms, &resolved_ms);

    portENTER_CRITICAL(&handle->mux);
    handle->gain = resolved_gain;
    handle->integration_ms = resolved_ms;
    handle->configured = false;
    portEXIT_CRITICAL(&handle->mux);

    return probe_and_configure(handle);
}

static esp_err_t wait_for_sample_ready(color_sensor_handle_t handle, uint16_t integration_ms)
{
    const TickType_t start = xTaskGetTickCount();
    const TickType_t timeout = pdMS_TO_TICKS((integration_ms * 2U) + 40U);
    uint8_t status = 0U;

    while ((xTaskGetTickCount() - start) <= timeout) {
        esp_err_t err = read_reg(handle, TCS3472_STATUS_REG, &status, 1U);
        if (err != ESP_OK) {
            return err;
        }
        if ((status & TCS3472_STATUS_AVALID) != 0U) {
            return ESP_OK;
        }
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    return ESP_ERR_TIMEOUT;
}

esp_err_t color_sensor_read_sample(color_sensor_handle_t handle,
                                   uint8_t led_mode,
                                   color_sensor_sample_t *out_sample)
{
    if (!handle || !out_sample) {
        return ESP_ERR_INVALID_ARG;
    }
    (void)led_mode;

    memset(out_sample, 0, sizeof(*out_sample));

    bool configured = false;
    uint16_t integration_ms = 0U;
    uint16_t gain = 0U;
    uint8_t sensor_id = 0U;
    bool led_on = false;
    portENTER_CRITICAL(&handle->mux);
    configured = handle->configured;
    integration_ms = handle->integration_ms;
    gain = handle->gain;
    sensor_id = handle->sensor_id;
    led_on = handle->led_on;
    portEXIT_CRITICAL(&handle->mux);

    if (!configured) {
        ESP_RETURN_ON_ERROR(probe_and_configure(handle), TAG, "probe failed");
        portENTER_CRITICAL(&handle->mux);
        integration_ms = handle->integration_ms;
        gain = handle->gain;
        sensor_id = handle->sensor_id;
        led_on = handle->led_on;
        portEXIT_CRITICAL(&handle->mux);
    }

    esp_err_t err = wait_for_sample_ready(handle, integration_ms);
    if (err != ESP_OK) {
        set_last_error(handle, "sample-timeout", false);
        return err;
    }

    uint8_t raw_bytes[8] = {0};
    err = read_reg(handle, TCS3472_CDATAL_REG, raw_bytes, sizeof(raw_bytes));
    if (err != ESP_OK) {
        set_last_error(handle, "read-rgbc", false);
        return err;
    }

    out_sample->c = (uint16_t)(raw_bytes[0] | (raw_bytes[1] << 8));
    out_sample->r = (uint16_t)(raw_bytes[2] | (raw_bytes[3] << 8));
    out_sample->g = (uint16_t)(raw_bytes[4] | (raw_bytes[5] << 8));
    out_sample->b = (uint16_t)(raw_bytes[6] | (raw_bytes[7] << 8));
    out_sample->saturated = (out_sample->c >= 65000U) ||
                            (out_sample->r >= 65000U) ||
                            (out_sample->g >= 65000U) ||
                            (out_sample->b >= 65000U);
    out_sample->sensor_ok = true;
    out_sample->led_on = led_on;
    out_sample->sensor_id = sensor_id;
    out_sample->gain = gain;
    out_sample->integration_ms = integration_ms;
    out_sample->sample_timestamp_us = esp_timer_get_time();

    portENTER_CRITICAL(&handle->mux);
    handle->sensor_ok = true;
    snprintf(handle->last_error, sizeof(handle->last_error), "ok");
    portEXIT_CRITICAL(&handle->mux);
    return ESP_OK;
}

void color_sensor_get_status(color_sensor_handle_t handle, color_sensor_status_t *out_status)
{
    if (!handle || !out_status) {
        return;
    }

    memset(out_status, 0, sizeof(*out_status));
    portENTER_CRITICAL(&handle->mux);
    out_status->sensor_id = handle->sensor_id;
    out_status->sensor_ok = handle->sensor_ok;
    out_status->led_on = handle->led_on;
    out_status->gain = handle->gain;
    out_status->integration_ms = handle->integration_ms;
    out_status->error_count = handle->error_count;
    memcpy(out_status->last_error, handle->last_error, sizeof(out_status->last_error));
    portEXIT_CRITICAL(&handle->mux);
    out_status->last_error[sizeof(out_status->last_error) - 1U] = '\0';
}

esp_err_t color_sensor_run_selftest(color_sensor_handle_t handle,
                                    color_sensor_selftest_result_t *out_result)
{
    if (!handle || !out_result) {
        return ESP_ERR_INVALID_ARG;
    }

    memset(out_result, 0, sizeof(*out_result));
    color_sensor_sample_t sample = {0};
    esp_err_t err = color_sensor_read_sample(handle, 0U, &sample);
    if (err != ESP_OK) {
        snprintf(out_result->message, sizeof(out_result->message), "sample-failed");
        color_sensor_status_t status = {0};
        color_sensor_get_status(handle, &status);
        out_result->sensor_id = status.sensor_id;
        out_result->ok = false;
        return err;
    }

    out_result->ok = (sample.sensor_ok && sample.sensor_id != 0U);
    out_result->sensor_id = sample.sensor_id;
    snprintf(out_result->message,
             sizeof(out_result->message),
             out_result->ok ? "ok" : "sensor-missing");
    return out_result->ok ? ESP_OK : ESP_FAIL;
}
