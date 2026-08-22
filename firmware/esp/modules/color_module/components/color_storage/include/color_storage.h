#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "color_proc.h"

#ifdef __cplusplus
extern "C" {
#endif

#define COLOR_STORAGE_CFG_VERSION 3U
#define COLOR_STORAGE_CAL_VERSION 4U
#define COLOR_STORAGE_SENSOR_NAME_MAX_LEN 32U

typedef struct {
    uint16_t version;
    uint8_t led_mode;
    uint8_t gain_mode;
    uint8_t classifier;
    uint8_t palette_mode;
    uint8_t telemetry_delivery_mode;
    uint8_t reserved0;
    uint16_t display_hysteresis_milli;
    uint16_t gain;
    uint16_t integration_ms;
    uint16_t target_clear;
    uint16_t patch_sample_count;
    uint32_t sample_period_ms;
    float confidence_threshold;
    float black_threshold;
    float bright_threshold;
    char sensor_name[COLOR_STORAGE_SENSOR_NAME_MAX_LEN];
} color_storage_cfg_t;

typedef struct {
    uint16_t version;
    uint16_t reserved0;
    color_proc_calibration_profile_t profile5;
    color_proc_calibration_profile_t profile8;
    color_proc_calibration_profile_t profile16;
} color_storage_cal_t;

esp_err_t color_storage_init(void);
bool color_storage_is_ready(void);
esp_err_t color_storage_load_cfg(color_storage_cfg_t *out_cfg);
esp_err_t color_storage_save_cfg(const color_storage_cfg_t *cfg);
esp_err_t color_storage_load_cal(color_storage_cal_t *out_cal);
esp_err_t color_storage_save_cal(const color_storage_cal_t *cal);

#ifdef __cplusplus
}
#endif
