#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define COLOR_COMM_DEFAULT_LINK_TIMEOUT_MS 5000U
#define COLOR_COMM_SENSOR_NAME_MAX_LEN 32U
#define COLOR_COMM_TEXT_MAX_LEN 32U
#define COLOR_COMM_PATCH_NAME_MAX_LEN 16U

typedef struct {
    uint16_t raw_r;
    uint16_t raw_g;
    uint16_t raw_b;
    uint16_t raw_c;
    uint16_t norm_r_milli;
    uint16_t norm_g_milli;
    uint16_t norm_b_milli;
    int16_t lab_l_centi;
    int16_t lab_a_centi;
    int16_t lab_b_centi;
    uint16_t luma_milli;
    int8_t detected_slot;
    uint16_t confidence_milli;
    int8_t top_slot[3];
    uint16_t top_confidence_milli[3];
    uint8_t palette_mode;
    uint8_t classifier;
    uint8_t led_mode;
    uint8_t led_active;
    uint8_t gain_mode;
    uint8_t sensor_id;
    uint8_t health_flags;
    bool calibrating;
    int8_t calibration_target_slot;
    uint16_t calibration_samples;
    uint16_t gain;
    uint16_t integration_ms;
    uint16_t target_clear;
    uint32_t sample_timestamp_ms;
} color_comm_sensor_state_t;

typedef struct {
    char sensor_name[COLOR_COMM_SENSOR_NAME_MAX_LEN];
    uint32_t sample_period_ms;
    uint16_t gain;
    uint16_t integration_ms;
    uint16_t target_clear;
    uint16_t patch_sample_count;
    float confidence_threshold;
    float black_threshold;
    float bright_threshold;
    uint8_t led_mode;
    uint8_t gain_mode;
    uint8_t classifier;
    uint8_t palette_mode;
    uint8_t telemetry_delivery_mode;
    uint16_t display_hysteresis_milli;
} color_comm_cfg_state_t;

typedef struct {
    uint8_t palette_mode;
    uint8_t class_count;
    uint16_t valid_mask;
    uint16_t enabled_mask;
    bool dark_valid;
    bool white_valid;
    uint16_t dark_r;
    uint16_t dark_g;
    uint16_t dark_b;
    uint16_t dark_c;
    uint16_t white_r;
    uint16_t white_g;
    uint16_t white_b;
    uint16_t white_c;
    uint16_t patch_sample_count;
} color_comm_cal_state_t;

typedef struct {
    uint8_t palette_mode;
    int8_t slot;
    bool enabled;
    bool valid;
    uint16_t sample_count;
    char name[COLOR_COMM_PATCH_NAME_MAX_LEN];
    uint16_t norm_r_milli;
    uint16_t norm_g_milli;
    uint16_t norm_b_milli;
    int16_t lab_l_centi;
    int16_t lab_a_centi;
    int16_t lab_b_centi;
    uint16_t luma_milli;
} color_comm_cal_patch_state_t;

typedef struct {
    char name[COLOR_COMM_SENSOR_NAME_MAX_LEN];
    char module_type[COLOR_COMM_TEXT_MAX_LEN];
    char firmware_module[COLOR_COMM_TEXT_MAX_LEN];
    char firmware_version[COLOR_COMM_TEXT_MAX_LEN];
    char expected_page[COLOR_COMM_TEXT_MAX_LEN];
    char expected_page_version[COLOR_COMM_TEXT_MAX_LEN];
    uint32_t module_id;
    uint8_t sensor_id;
    uint8_t health_flags;
    uint8_t i2c_address;
    uint8_t sda_pin;
    uint8_t scl_pin;
    uint8_t led_pin;
} color_comm_info_state_t;

typedef struct {
    bool ok;
    uint8_t sensor_id;
    uint8_t health_flags;
    char message[48];
} color_comm_selftest_state_t;

typedef struct {
    void *ctx;
    uint32_t link_timeout_ms;
    void (*on_link_active)(void *ctx);
    void (*on_link_timeout)(void *ctx);
    bool (*get_sensor_state)(void *ctx, color_comm_sensor_state_t *out_state);
    bool (*get_cfg_state)(void *ctx, color_comm_cfg_state_t *out_state);
    bool (*set_cfg_state)(void *ctx, const color_comm_cfg_state_t *state);
    bool (*save_cfg)(void *ctx);
    bool (*reset_cfg)(void *ctx);
    bool (*get_cal_state)(void *ctx, uint8_t palette_mode, color_comm_cal_state_t *out_state);
    bool (*get_cal_patch_state)(void *ctx,
                                uint8_t palette_mode,
                                int slot,
                                color_comm_cal_patch_state_t *out_state);
    bool (*save_cal)(void *ctx);
    bool (*reset_cal)(void *ctx, uint8_t palette_mode);
    void (*start_calibration)(void *ctx);
    void (*stop_calibration)(void *ctx);
    bool (*set_cal_target)(void *ctx, int slot);
    bool (*commit_cal_target)(void *ctx, int slot);
    bool (*set_cal_reference)(void *ctx,
                              uint8_t palette_mode,
                              int slot,
                              uint16_t r,
                              uint16_t g,
                              uint16_t b,
                              uint16_t c);
    bool (*set_cal_patch_data)(void *ctx,
                               uint8_t palette_mode,
                               int slot,
                               uint16_t norm_r_milli,
                               uint16_t norm_g_milli,
                               uint16_t norm_b_milli,
                               uint16_t luma_milli,
                               int16_t lab_l_centi,
                               int16_t lab_a_centi,
                               int16_t lab_b_centi,
                               uint16_t sample_count);
    bool (*get_info_state)(void *ctx, color_comm_info_state_t *out_state);
    bool (*run_selftest)(void *ctx, color_comm_selftest_state_t *out_state);
} color_comm_cfg_t;

esp_err_t color_comm_init(const color_comm_cfg_t *cfg);
void color_comm_task(void *arg);
void color_comm_send_line(const char *fmt, ...);

#ifdef __cplusplus
}
#endif
