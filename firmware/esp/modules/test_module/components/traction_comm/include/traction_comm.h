#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define TRACTION_COMM_DEFAULT_LINK_TIMEOUT_MS 1200U

typedef struct {
    float kp;
    float ki;
    float kd;
    float setpoint_rpm;
} traction_comm_pid_rpm_state_t;

typedef struct {
    float target_rpm;
    float measured_rpm;
    float cmd_pwm_signed;
    float cmd_raw;
} traction_comm_rpm_telem_state_t;

typedef struct {
    float target_deg;
    float position_deg;
    float cmd_pwm_signed;
    float cmd_raw;
    float i_term;
} traction_comm_pos_telem_state_t;

typedef struct {
    float kp;
    float ki;
    float kd;
    float target_deg;
    float integral_window_deg;
    bool enabled;
} traction_comm_pid_pos_state_t;

typedef struct {
    float amp_deg;
    float offset_deg;
    float period_s;
    bool enabled;
} traction_comm_pid_pos_sine_state_t;

typedef struct {
    bool motor_invert;
    bool encoder_invert;
} traction_comm_invert_state_t;

typedef struct {
    uint8_t bridge_type;
} traction_comm_bridge_state_t;

typedef struct {
    float rpm_points[10];
} traction_comm_curve_state_t;

typedef struct {
    uint8_t bridge_type;
    uint8_t encoder_mode;
    uint8_t motor_invert;
    uint8_t encoder_invert;
    uint8_t pullup_enabled;
    uint32_t pwm_freq_hz;
    int32_t counts_per_motor_rev;
    int32_t gear_ratio;
    float rpm_max;
    char notes[64];
} traction_comm_controller_cfg_state_t;

typedef struct {
    void *ctx;
    uint32_t link_timeout_ms;

    void (*on_link_timeout)(void *ctx);
    void (*request_rpm_telem)(void *ctx);
    void (*request_pos_telem)(void *ctx);
    bool (*get_rpm_telem_state)(void *ctx, traction_comm_rpm_telem_state_t *out_state);
    bool (*get_pos_telem_state)(void *ctx, traction_comm_pos_telem_state_t *out_state);

    bool (*get_rpm_state)(void *ctx, traction_comm_pid_rpm_state_t *out_state);
    void (*set_rpm_kp)(void *ctx, float value);
    void (*set_rpm_ki)(void *ctx, float value);
    void (*set_rpm_kd)(void *ctx, float value);
    void (*set_rpm_setpoint)(void *ctx, float value);
    void (*set_force_output)(void *ctx, float output_pct);
    void (*set_force_output_raw)(void *ctx, float output_pct);
    void (*clear_force_output)(void *ctx);
    bool (*enqueue_rpm_save)(void *ctx, const traction_comm_pid_rpm_state_t *state);

    bool (*get_pos_state)(void *ctx, traction_comm_pid_pos_state_t *out_state);
    void (*set_pos_kp)(void *ctx, float value);
    void (*set_pos_ki)(void *ctx, float value);
    void (*set_pos_kd)(void *ctx, float value);
    void (*set_pos_target_deg)(void *ctx, float value);
    void (*set_pos_integral_window_deg)(void *ctx, float value);
    void (*set_pos_enabled)(void *ctx, bool enabled);
    bool (*enqueue_pos_save)(void *ctx, const traction_comm_pid_pos_state_t *state);
    bool (*get_pos_sine_state)(void *ctx, traction_comm_pid_pos_sine_state_t *out_state);
    void (*set_pos_sine_amp_deg)(void *ctx, float value);
    void (*set_pos_sine_offset_deg)(void *ctx, float value);
    void (*set_pos_sine_period_s)(void *ctx, float value);
    void (*set_pos_sine_enabled)(void *ctx, bool enabled);

    bool (*get_invert_state)(void *ctx, traction_comm_invert_state_t *out_state);
    void (*set_motor_invert)(void *ctx, bool enabled);
    void (*set_encoder_invert)(void *ctx, bool enabled);
    bool (*get_bridge_state)(void *ctx, traction_comm_bridge_state_t *out_state);
    void (*set_bridge_type)(void *ctx, uint8_t bridge_type);
    bool (*get_curve_state)(void *ctx, traction_comm_curve_state_t *out_state);
    void (*set_curve_point)(void *ctx, uint8_t point_index, float rpm_value);
    bool (*enqueue_curve_save)(void *ctx, const traction_comm_curve_state_t *state);
    bool (*get_controller_cfg_state)(void *ctx, traction_comm_controller_cfg_state_t *out_state);
    bool (*set_controller_cfg_state)(void *ctx, const traction_comm_controller_cfg_state_t *state);
    bool (*enqueue_controller_cfg_save)(void *ctx, const traction_comm_controller_cfg_state_t *state);
} traction_comm_cfg_t;

esp_err_t traction_comm_init(const traction_comm_cfg_t *cfg);
void traction_comm_task(void *arg);
void traction_comm_send_line(const char *fmt, ...);

#ifdef __cplusplus
}
#endif
