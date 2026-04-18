#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "ls_sensor.h"

#ifdef __cplusplus
extern "C" {
#endif

#define LS_STORAGE_CFG_VERSION 2U
#define LS_STORAGE_CAL_VERSION 1U
#define LS_SENSOR_NAME_MAX_LEN 32U

typedef struct {
    uint16_t version;
    uint8_t track_type;
    uint8_t reserved0;
    uint16_t reserved1;
    float digital_threshold;
    float detect_threshold;
    uint32_t calibration_time_ms;
    char sensor_name[LS_SENSOR_NAME_MAX_LEN];
} ls_storage_cfg_t;

typedef struct {
    uint16_t version;
    uint16_t count;
    uint16_t min_raw[LS_SENSOR_COUNT];
    uint16_t max_raw[LS_SENSOR_COUNT];
} ls_storage_cal_t;

esp_err_t ls_storage_init(void);
bool ls_storage_is_ready(void);
esp_err_t ls_storage_load_cfg(ls_storage_cfg_t *out_cfg);
esp_err_t ls_storage_save_cfg(const ls_storage_cfg_t *cfg);
esp_err_t ls_storage_load_cal(ls_storage_cal_t *out_cal);
esp_err_t ls_storage_save_cal(const ls_storage_cal_t *cal);

#ifdef __cplusplus
}
#endif
