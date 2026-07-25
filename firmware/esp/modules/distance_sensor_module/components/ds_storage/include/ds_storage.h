#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DS_STORAGE_CFG_VERSION 1U
#define DS_STORAGE_NAME_MAX_LEN 32U

typedef struct {
    uint32_t version;
    uint32_t sample_period_ms;
    uint32_t max_distance_mm;
    uint8_t filter_window;
    uint8_t reserved[3];
    char sensor_name[DS_STORAGE_NAME_MAX_LEN];
} ds_storage_cfg_t;

esp_err_t ds_storage_init(void);
bool ds_storage_is_ready(void);
esp_err_t ds_storage_load_cfg(ds_storage_cfg_t *out_cfg);
esp_err_t ds_storage_save_cfg(const ds_storage_cfg_t *cfg);

#ifdef __cplusplus
}
#endif
