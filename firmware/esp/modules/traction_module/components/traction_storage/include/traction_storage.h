#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define TRACTION_MOTOR_CURVE_POINT_COUNT 10U
#define TRACTION_CONTROLLER_NOTES_MAX_LEN 64U
#define TRACTION_POS_PID_STORE_VERSION 1U

typedef struct {
    float kp;
    float ki;
    float kd;
    float setpoint_rpm;
} traction_pid_store_t;

typedef struct {
    uint16_t version;
    uint16_t reserved0;
    float kp;
    float ki;
    float kd;
    float target_deg;
} traction_pos_pid_store_t;

typedef struct {
    uint8_t motor_invert;
    uint8_t encoder_invert;
    uint8_t reserved0;
    uint8_t reserved1;
} traction_invert_store_t;

typedef struct {
    uint8_t bridge_type;
    uint8_t reserved0;
    uint8_t reserved1;
    uint8_t reserved2;
} traction_bridge_store_t;

typedef struct {
    uint16_t version;
    uint16_t count;
    float rpm_points[TRACTION_MOTOR_CURVE_POINT_COUNT];
} traction_curve_store_t;

typedef struct {
    uint16_t version;
    uint8_t bridge_type;
    uint8_t encoder_mode;
    uint8_t motor_invert;
    uint8_t encoder_invert;
    uint8_t pullup_enabled;
    uint8_t reserved0;
    uint8_t reserved1;
    uint32_t pwm_freq_hz;
    int32_t counts_per_motor_rev;
    int32_t gear_ratio;
    float rpm_max;
    char notes[TRACTION_CONTROLLER_NOTES_MAX_LEN];
} traction_controller_cfg_store_t;

esp_err_t traction_storage_init(void);
bool traction_storage_is_ready(void);

esp_err_t traction_storage_load_pid(traction_pid_store_t *out);
esp_err_t traction_storage_save_pid(const traction_pid_store_t *in);
esp_err_t traction_storage_load_pos_pid(traction_pos_pid_store_t *out);
esp_err_t traction_storage_save_pos_pid(const traction_pos_pid_store_t *in);
esp_err_t traction_storage_load_invert(traction_invert_store_t *out);
esp_err_t traction_storage_save_invert(const traction_invert_store_t *in);
esp_err_t traction_storage_load_bridge(traction_bridge_store_t *out);
esp_err_t traction_storage_save_bridge(const traction_bridge_store_t *in);
esp_err_t traction_storage_load_curve(traction_curve_store_t *out);
esp_err_t traction_storage_save_curve(const traction_curve_store_t *in);
esp_err_t traction_storage_load_controller_cfg(traction_controller_cfg_store_t *out);
esp_err_t traction_storage_save_controller_cfg(const traction_controller_cfg_store_t *in);

#ifdef __cplusplus
}
#endif
