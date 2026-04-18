#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "driver/gpio.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define LS_SENSOR_COUNT 5U

typedef struct {
    uint16_t min_raw[LS_SENSOR_COUNT];
    uint16_t max_raw[LS_SENSOR_COUNT];
} ls_sensor_calibration_t;

typedef struct {
    bool active;
    uint32_t remaining_ms;
    uint32_t duration_ms;
} ls_sensor_calibration_state_t;

typedef struct {
    gpio_num_t gpio_pins[LS_SENSOR_COUNT];
    uint32_t default_calibration_time_ms;
} ls_sensor_cfg_t;

typedef struct ls_sensor_ctx_t *ls_sensor_handle_t;

void ls_sensor_get_default_calibration(ls_sensor_calibration_t *out);
esp_err_t ls_sensor_init(const ls_sensor_cfg_t *cfg, ls_sensor_handle_t *out_handle);
esp_err_t ls_sensor_sample(ls_sensor_handle_t handle, uint16_t raw_out[LS_SENSOR_COUNT]);
void ls_sensor_set_calibration(ls_sensor_handle_t handle, const ls_sensor_calibration_t *calibration);
void ls_sensor_get_calibration(ls_sensor_handle_t handle, ls_sensor_calibration_t *out_calibration);
void ls_sensor_start_calibration(ls_sensor_handle_t handle, uint32_t duration_ms);
void ls_sensor_stop_calibration(ls_sensor_handle_t handle, bool keep_data);
void ls_sensor_get_calibration_state(ls_sensor_handle_t handle, ls_sensor_calibration_state_t *out_state);
bool ls_sensor_consume_calibration_done(ls_sensor_handle_t handle);

#ifdef __cplusplus
}
#endif
