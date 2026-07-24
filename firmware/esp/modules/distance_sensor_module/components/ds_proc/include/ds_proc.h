#pragma once

#include <stdint.h>

#include "esp_err.h"
#include "ds_sensor.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DS_PROC_MAX_FILTER_WINDOW 7U

typedef struct {
    int32_t values[DS_PROC_MAX_FILTER_WINDOW];
    uint8_t window;
    uint8_t count;
    uint8_t write_index;
} ds_proc_state_t;

typedef struct {
    int32_t filtered_distance_mm;
    int32_t raw_distance_mm;
    uint32_t echo_time_us;
    uint8_t health_flags;
    uint64_t timestamp_ms;
} ds_proc_result_t;

bool ds_proc_filter_window_is_valid(uint8_t window);
esp_err_t ds_proc_init(ds_proc_state_t *state, uint8_t filter_window);
esp_err_t ds_proc_set_filter_window(ds_proc_state_t *state, uint8_t filter_window);
esp_err_t ds_proc_process(
    ds_proc_state_t *state,
    const ds_sensor_sample_t *sample,
    ds_proc_result_t *out_result
);

#ifdef __cplusplus
}
#endif
