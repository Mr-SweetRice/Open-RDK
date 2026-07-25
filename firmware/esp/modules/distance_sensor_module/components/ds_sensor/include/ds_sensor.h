#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "driver/gpio.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DS_SENSOR_MIN_DISTANCE_MM 20U
#define DS_SENSOR_MAX_DISTANCE_MM 4000U
#define DS_SENSOR_MIN_CYCLE_US 60000LL

#define DS_HEALTH_VALID          (1U << 0)
#define DS_HEALTH_NO_ECHO        (1U << 1)
#define DS_HEALTH_ECHO_STUCK     (1U << 2)
#define DS_HEALTH_BELOW_MIN      (1U << 3)
#define DS_HEALTH_ABOVE_MAX      (1U << 4)
#define DS_HEALTH_FILTER_ACTIVE  (1U << 5)
#define DS_HEALTH_CONFIG_LOADED  (1U << 6)

typedef struct ds_sensor_context *ds_sensor_handle_t;

typedef struct {
    gpio_num_t trigger_pin;
    gpio_num_t echo_pin;
    uint32_t max_distance_mm;
} ds_sensor_cfg_t;

typedef struct {
    int32_t raw_distance_mm;
    uint32_t echo_time_us;
    uint8_t health_flags;
    uint64_t timestamp_ms;
} ds_sensor_sample_t;

esp_err_t ds_sensor_init(const ds_sensor_cfg_t *cfg, ds_sensor_handle_t *out_handle);
esp_err_t ds_sensor_set_max_distance(ds_sensor_handle_t handle, uint32_t max_distance_mm);
esp_err_t ds_sensor_measure(ds_sensor_handle_t handle, ds_sensor_sample_t *out_sample);

#ifdef __cplusplus
}
#endif
