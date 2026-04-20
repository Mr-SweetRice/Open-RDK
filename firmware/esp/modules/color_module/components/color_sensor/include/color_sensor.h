#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "driver/gpio.h"
#include "driver/i2c.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define COLOR_SENSOR_I2C_ADDRESS 0x29U
#define COLOR_SENSOR_DEFAULT_I2C_FREQ_HZ 400000U

typedef struct {
    i2c_port_t i2c_port;
    gpio_num_t sda_pin;
    gpio_num_t scl_pin;
    gpio_num_t led_pin;
    uint8_t i2c_address;
    uint32_t i2c_freq_hz;
    uint16_t default_integration_ms;
    uint16_t default_gain;
} color_sensor_cfg_t;

typedef struct {
    uint16_t r;
    uint16_t g;
    uint16_t b;
    uint16_t c;
    bool saturated;
    bool led_on;
    bool sensor_ok;
    uint8_t sensor_id;
    uint16_t gain;
    uint16_t integration_ms;
    int64_t sample_timestamp_us;
} color_sensor_sample_t;

typedef struct {
    uint8_t sensor_id;
    bool sensor_ok;
    bool led_on;
    uint16_t gain;
    uint16_t integration_ms;
    uint32_t error_count;
    char last_error[32];
} color_sensor_status_t;

typedef struct {
    bool ok;
    uint8_t sensor_id;
    char message[32];
} color_sensor_selftest_result_t;

typedef struct color_sensor_ctx_t *color_sensor_handle_t;

esp_err_t color_sensor_init(const color_sensor_cfg_t *cfg, color_sensor_handle_t *out_handle);
esp_err_t color_sensor_set_exposure(color_sensor_handle_t handle, uint16_t gain, uint16_t integration_ms);
esp_err_t color_sensor_read_sample(color_sensor_handle_t handle,
                                   uint8_t led_mode,
                                   color_sensor_sample_t *out_sample);
void color_sensor_get_status(color_sensor_handle_t handle, color_sensor_status_t *out_status);
esp_err_t color_sensor_run_selftest(color_sensor_handle_t handle,
                                    color_sensor_selftest_result_t *out_result);

#ifdef __cplusplus
}
#endif
