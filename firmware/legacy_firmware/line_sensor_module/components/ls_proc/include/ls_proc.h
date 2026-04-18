#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "ls_sensor.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    LS_TRACK_DARK = 0,
    LS_TRACK_LIGHT = 1,
} ls_track_type_t;

typedef struct {
    ls_track_type_t track_type;
    float digital_threshold;
    float detect_threshold;
} ls_proc_cfg_t;

typedef struct {
    uint16_t raw[LS_SENSOR_COUNT];
    float reflectance[LS_SENSOR_COUNT];
    float line_value[LS_SENSOR_COUNT];
    uint8_t digital[LS_SENSOR_COUNT];
    float position;
    float strength;
    bool line_detected;
} ls_proc_result_t;

void ls_proc_get_default_cfg(ls_proc_cfg_t *out_cfg);
esp_err_t ls_proc_process(const uint16_t raw[LS_SENSOR_COUNT],
                          const ls_sensor_calibration_t *calibration,
                          const ls_proc_cfg_t *cfg,
                          ls_proc_result_t *out_result);

#ifdef __cplusplus
}
#endif
