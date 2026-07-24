#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DS_COMM_DEFAULT_LINK_TIMEOUT_MS 1200U
#define DS_COMM_SENSOR_NAME_MAX_LEN 32U

typedef struct {
    int32_t filtered_distance_mm;
    int32_t raw_distance_mm;
    uint32_t echo_time_us;
    bool valid;
    uint8_t health_flags;
    uint64_t sample_timestamp_ms;
} ds_comm_sensor_state_t;

typedef struct {
    char sensor_name[DS_COMM_SENSOR_NAME_MAX_LEN];
    uint32_t sample_period_ms;
    uint32_t max_distance_mm;
    uint8_t filter_window;
} ds_comm_cfg_state_t;

typedef struct {
    bool ok;
    uint8_t health_flags;
    int32_t distance_mm;
} ds_comm_selftest_state_t;

typedef struct {
    void *ctx;
    uint32_t link_timeout_ms;
    void (*on_link_timeout)(void *ctx);
    bool (*get_sensor_state)(void *ctx, ds_comm_sensor_state_t *out_state);
    bool (*get_cfg_state)(void *ctx, ds_comm_cfg_state_t *out_state);
    bool (*set_cfg_state)(void *ctx, const ds_comm_cfg_state_t *state);
    bool (*save_cfg)(void *ctx);
    bool (*reset_cfg)(void *ctx);
    bool (*run_selftest)(void *ctx, ds_comm_selftest_state_t *out_state);
} ds_comm_cfg_t;

esp_err_t ds_comm_init(const ds_comm_cfg_t *cfg);
void ds_comm_task(void *arg);

#ifdef __cplusplus
}
#endif
