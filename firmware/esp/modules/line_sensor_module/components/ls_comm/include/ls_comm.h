#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "ls_sensor.h"

#ifdef __cplusplus
extern "C" {
#endif

#define LS_COMM_DEFAULT_LINK_TIMEOUT_MS 1200U
#define LS_COMM_SENSOR_NAME_MAX_LEN 32U
#define LS_COMM_TEXT_MAX_LEN 32U

typedef struct {
    uint16_t raw[LS_SENSOR_COUNT];
    float value[LS_SENSOR_COUNT];
    uint8_t digital[LS_SENSOR_COUNT];
    float position;
    float strength;
    int64_t sample_started_us;
    int64_t sample_ready_us;
    bool line_detected;
    bool calibrating;
    uint32_t calibration_remaining_ms;
} ls_comm_sensor_state_t;

typedef struct {
    uint8_t track_type;
    float digital_threshold;
    float detect_threshold;
    uint32_t calibration_time_ms;
    char sensor_name[LS_COMM_SENSOR_NAME_MAX_LEN];
} ls_comm_cfg_state_t;

typedef struct {
    uint16_t min_raw[LS_SENSOR_COUNT];
    uint16_t max_raw[LS_SENSOR_COUNT];
} ls_comm_cal_state_t;

typedef struct {
    char name[LS_COMM_SENSOR_NAME_MAX_LEN];
    char module_type[LS_COMM_TEXT_MAX_LEN];
    char firmware_module[LS_COMM_TEXT_MAX_LEN];
    uint32_t module_id;
} ls_comm_info_state_t;

typedef struct {
    void *ctx;
    uint32_t link_timeout_ms;
    void (*on_link_timeout)(void *ctx);
    bool (*get_sensor_state)(void *ctx, ls_comm_sensor_state_t *out_state);
    bool (*get_cfg_state)(void *ctx, ls_comm_cfg_state_t *out_state);
    bool (*set_cfg_state)(void *ctx, const ls_comm_cfg_state_t *state);
    bool (*save_cfg)(void *ctx);
    bool (*get_cal_state)(void *ctx, ls_comm_cal_state_t *out_state);
    bool (*get_info_state)(void *ctx, ls_comm_info_state_t *out_state);
    bool (*save_cal)(void *ctx);
    void (*start_calibration)(void *ctx);
    void (*stop_calibration)(void *ctx);
} ls_comm_cfg_t;

esp_err_t ls_comm_init(const ls_comm_cfg_t *cfg);
void ls_comm_task(void *arg);
void ls_comm_send_line(const char *fmt, ...);

#ifdef __cplusplus
}
#endif
