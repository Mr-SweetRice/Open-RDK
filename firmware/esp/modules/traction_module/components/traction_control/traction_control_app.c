#include "traction_control_app.h"

#include <math.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/portmacro.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "traction_control.h"
#include "traction_hal.h"
#include "traction_storage.h"

// Limits
#define MOTOR_MAX_OUTPUT_PCT 100
#define MOTOR_STARTUP_OUTPUT_PCT 0 // Keep motor off after reset unless an explicit command wakes it.

#if (MOTOR_STARTUP_OUTPUT_PCT < 0) || (MOTOR_STARTUP_OUTPUT_PCT > MOTOR_MAX_OUTPUT_PCT)
#error "MOTOR_STARTUP_OUTPUT_PCT must be between 0 and 100"
#endif

// Default output-to-RPM curve seed
#define ENABLE_OUTPUT_LINEARIZATION 1
#define RPM_AT_100_PCT 199.89f
#define LIN_DEADBAND_PCT 40.0f
#define LIN_A 0.5855839f
#define LIN_B 1.42f
#define CURVE_STORE_VERSION 1U
#define CONTROLLER_CFG_STORE_VERSION 1U

// Speed control
#define CONTROL_PERIOD_MS          33
#define PID_KP_DEFAULT             1.0f
#define PID_KI_DEFAULT             2.2f
#define PID_KD_DEFAULT             0.00f
#define PID_D_ALPHA_DEFAULT        0.2f
#define SETPOINT_DEFAULT_RPM       0.0f
#define SETPOINT_MIN_RPM           10.0f
#define SETPOINT_MAX_RPM           150.0f

// Position control
#define POS_KP_DEFAULT             200.0f
#define POS_KI_DEFAULT             0.0f
#define POS_KD_DEFAULT             8.0f
#define POSITION_DEADBAND_DEG      3.6f
#define POSITION_BRAKE_HOLD_MIN_PWM_PCT 1.0f
#define POS_I_WINDUP_LIMIT         12000000.0f
#define POS_MODE_OUTPUT_BOOST      1.10f
#define POS_SINE_AMP_DEFAULT_DEG    90.0f
#define POS_SINE_OFFSET_DEFAULT_DEG 180.0f
#define POS_SINE_PERIOD_DEFAULT_S   8.0f
#define POS_SINE_PERIOD_MIN_S       0.5f
#define POS_SINE_PERIOD_MAX_S       60.0f
#define PI_F                        3.14159265358979323846f
#define PRESERVE_FORCE_OUTPUT_ON_LINK_TIMEOUT 1

static const char *TAG = "ctrl_app";

typedef enum {
    NVS_SAVE_KIND_RPM = 0,
    NVS_SAVE_KIND_POS = 1,
    NVS_SAVE_KIND_INVERT = 2,
    NVS_SAVE_KIND_BRIDGE = 3,
    NVS_SAVE_KIND_CURVE = 4,
    NVS_SAVE_KIND_CONTROLLER_CFG = 5,
} nvs_save_kind_t;

typedef struct {
    nvs_save_kind_t kind;
    traction_pid_store_t rpm;
    traction_pos_pid_store_t pos;
    traction_invert_store_t invert;
    traction_bridge_store_t bridge;
    traction_curve_store_t curve;
    traction_controller_cfg_store_t controller_cfg;
} nvs_save_req_t;

static traction_pid_cfg_t s_pid_cfg;
static traction_pid_cfg_t s_pos_pid_cfg;
static float s_setpoint_rpm = SETPOINT_DEFAULT_RPM;
static float s_target_pos_deg = 0.0f;
static bool s_pos_sine_enabled = false;
static float s_pos_sine_amp_deg = POS_SINE_AMP_DEFAULT_DEG;
static float s_pos_sine_offset_deg = POS_SINE_OFFSET_DEFAULT_DEG;
static float s_pos_sine_period_s = POS_SINE_PERIOD_DEFAULT_S;
static int64_t s_pos_sine_t0_us = 0;
static uint32_t s_pid_version = 0;
static uint32_t s_pos_pid_version = 0;
static portMUX_TYPE s_pid_mux = portMUX_INITIALIZER_UNLOCKED;
static float s_force_out_pct = -1.0f;
static bool s_force_out_is_raw = false;
static bool s_pos_mode_enabled = false;
static bool s_pos_target_explicit = false;
static bool s_motor_invert = false;
static bool s_encoder_invert = false;
static traction_motor_bridge_t s_motor_bridge = TRACTION_MOTOR_BRIDGE_TB6612;
static traction_curve_store_t s_motor_curve = {0};
static traction_controller_cfg_store_t s_controller_cfg = {0};
static QueueHandle_t s_nvs_queue = NULL;
static bool s_telem_req = false;
static bool s_pos_telem_req = false;
static TaskHandle_t s_speed_task = NULL;
static TaskHandle_t s_enc_task = NULL;

static void apply_startup_motor_output(void)
{
    if (MOTOR_STARTUP_OUTPUT_PCT <= 0) {
        return;
    }

    portENTER_CRITICAL(&s_pid_mux);
    s_force_out_pct = MOTOR_STARTUP_OUTPUT_PCT;
    s_force_out_is_raw = false;
    s_setpoint_rpm = 0.0f;
    s_pos_mode_enabled = false;
    s_pos_sine_enabled = false;
    portEXIT_CRITICAL(&s_pid_mux);

    ESP_LOGI(TAG, "startup motor output enabled (%d%%)", MOTOR_STARTUP_OUTPUT_PCT);
}

static void set_control_to_zero(bool clear_force_output)
{
    portENTER_CRITICAL(&s_pid_mux);
    s_setpoint_rpm = 0.0f;
    s_target_pos_deg = 0.0f;
    s_pos_target_explicit = false;
    if (clear_force_output) {
        s_force_out_pct = -1.0f;
        s_force_out_is_raw = false;
    }
    s_pos_mode_enabled = false;
    s_pos_sine_enabled = false;
    portEXIT_CRITICAL(&s_pid_mux);
}

static traction_dir_t apply_motor_invert(traction_dir_t dir, bool motor_invert)
{
    if (!motor_invert) return dir;
    return (dir == TRACTION_DIR_CW) ? TRACTION_DIR_CCW : TRACTION_DIR_CW;
}

static float curve_point_pwm_pct(size_t point_index)
{
    return 10.0f * (float)(point_index + 1U);
}

static float default_curve_rpm_for_pwm(float pwm_pct)
{
    if (pwm_pct <= LIN_DEADBAND_PCT) {
        return 0.0f;
    }
    if (pwm_pct >= 100.0f) {
        return RPM_AT_100_PCT;
    }
    return LIN_A * powf(pwm_pct - LIN_DEADBAND_PCT, LIN_B);
}

static void motor_curve_defaults(void)
{
    memset(&s_motor_curve, 0, sizeof(s_motor_curve));
    s_motor_curve.version = CURVE_STORE_VERSION;
    s_motor_curve.count = TRACTION_MOTOR_CURVE_POINT_COUNT;
    for (size_t i = 0; i < TRACTION_MOTOR_CURVE_POINT_COUNT; i++) {
        s_motor_curve.rpm_points[i] = default_curve_rpm_for_pwm(curve_point_pwm_pct(i));
    }
}

static void controller_cfg_defaults(void)
{
    memset(&s_controller_cfg, 0, sizeof(s_controller_cfg));
    s_controller_cfg.version = CONTROLLER_CFG_STORE_VERSION;
    s_controller_cfg.bridge_type = (uint8_t)TRACTION_MOTOR_BRIDGE_TB6612;
    s_controller_cfg.encoder_mode = (uint8_t)TRACTION_ENCODER_MODE_QUADRATURE_X4;
    s_controller_cfg.motor_invert = 0U;
    s_controller_cfg.encoder_invert = 0U;
    s_controller_cfg.pullup_enabled = 1U;
    s_controller_cfg.pwm_freq_hz = 20000U;
    s_controller_cfg.counts_per_motor_rev = 44;
    s_controller_cfg.gear_ratio = 45;
    s_controller_cfg.rpm_max = RPM_AT_100_PCT;
    snprintf(s_controller_cfg.notes, sizeof(s_controller_cfg.notes),
             "Bench profile / default firmware values");
}

static float linearize_output_percent(float cmd_pct)
{
    if (cmd_pct <= 0.0f) return 0.0f;
    if (cmd_pct >= 100.0f) return 100.0f;

    float rpm_points[TRACTION_MOTOR_CURVE_POINT_COUNT] = {0};
    portENTER_CRITICAL(&s_pid_mux);
    for (size_t i = 0; i < TRACTION_MOTOR_CURVE_POINT_COUNT; i++) {
        rpm_points[i] = s_motor_curve.rpm_points[i];
    }
    portEXIT_CRITICAL(&s_pid_mux);

    float max_rpm = 0.0f;
    for (size_t i = 0; i < TRACTION_MOTOR_CURVE_POINT_COUNT; i++) {
        if (rpm_points[i] > max_rpm) {
            max_rpm = rpm_points[i];
        }
    }
    if (max_rpm <= 0.01f) {
        return cmd_pct;
    }

    const float desired_rpm = (cmd_pct / 100.0f) * max_rpm;
    float prev_pwm = 0.0f;
    float prev_rpm = 0.0f;

    for (size_t i = 0; i < TRACTION_MOTOR_CURVE_POINT_COUNT; i++) {
        const float curr_pwm = curve_point_pwm_pct(i);
        const float curr_rpm = (rpm_points[i] > prev_rpm) ? rpm_points[i] : prev_rpm;
        if (desired_rpm <= curr_rpm) {
            const float rpm_span = curr_rpm - prev_rpm;
            if (rpm_span <= 0.01f) {
                return curr_pwm;
            }
            const float t = (desired_rpm - prev_rpm) / rpm_span;
            return prev_pwm + (t * (curr_pwm - prev_pwm));
        }
        prev_pwm = curr_pwm;
        prev_rpm = curr_rpm;
    }

    return 100.0f;
}

static float clamp_setpoint_rpm(float sp)
{
    if (sp > SETPOINT_MAX_RPM) return SETPOINT_MAX_RPM;
    if (sp < -SETPOINT_MAX_RPM) return -SETPOINT_MAX_RPM;
    return sp;
}

static float deg_to_pos_rev(float deg)
{
    return deg / 360.0f;
}

static float sanitize_pos_target_deg(float target_deg)
{
    if (!isfinite(target_deg)) {
        return 0.0f;
    }
    return target_deg;
}

static float clamp_angle_deg(float deg)
{
    if (deg > 360.0f) return 360.0f;
    if (deg < 0.0f) return 0.0f;
    return deg;
}

static float clamp_pos_sine_amp_deg(float deg)
{
    if (deg > 180.0f) return 180.0f;
    if (deg < 0.0f) return 0.0f;
    return deg;
}

static float clamp_pos_sine_period_s(float period_s)
{
    if (period_s > POS_SINE_PERIOD_MAX_S) return POS_SINE_PERIOD_MAX_S;
    if (period_s < POS_SINE_PERIOD_MIN_S) return POS_SINE_PERIOD_MIN_S;
    return period_s;
}

static bool bridge_type_is_valid_u8(uint8_t bridge_type)
{
    return (bridge_type == (uint8_t)TRACTION_MOTOR_BRIDGE_TB6612) ||
           (bridge_type == (uint8_t)TRACTION_MOTOR_BRIDGE_DRV8833);
}

static bool encoder_mode_is_valid_u8(uint8_t encoder_mode)
{
    return encoder_mode <= (uint8_t)TRACTION_ENCODER_MODE_SINGLE_CHANNEL;
}

static traction_motor_bridge_t bridge_from_u8_or_default(uint8_t bridge_type)
{
    if (bridge_type == (uint8_t)TRACTION_MOTOR_BRIDGE_DRV8833) {
        return TRACTION_MOTOR_BRIDGE_DRV8833;
    }
    return TRACTION_MOTOR_BRIDGE_TB6612;
}

static const char *bridge_name(traction_motor_bridge_t bridge)
{
    return (bridge == TRACTION_MOTOR_BRIDGE_DRV8833) ? "DRV8833" : "TB6612";
}

static void controller_cfg_sync_runtime_fields_locked(void)
{
    s_motor_bridge = bridge_from_u8_or_default(s_controller_cfg.bridge_type);
    s_motor_invert = (s_controller_cfg.motor_invert != 0U);
    s_encoder_invert = (s_controller_cfg.encoder_invert != 0U);
}

static void controller_cfg_hold_current_position_locked(void)
{
    int64_t raw_count = 0;
    if (!s_pos_mode_enabled) {
        return;
    }
    if (traction_encoder_get_count(&raw_count) != ESP_OK) {
        return;
    }
    if (s_encoder_invert) {
        raw_count = -raw_count;
    }
    s_target_pos_deg = sanitize_pos_target_deg(traction_counts_to_output_deg(raw_count));
}

static esp_err_t controller_cfg_apply_runtime(void)
{
    esp_err_t err = traction_motor_set_pwm_freq(s_controller_cfg.pwm_freq_hz);
    if (err != ESP_OK) {
        return err;
    }

    err = traction_motor_set_bridge(bridge_from_u8_or_default(s_controller_cfg.bridge_type));
    if (err != ESP_OK) {
        return err;
    }

    err = traction_encoder_set_runtime_config(s_controller_cfg.pullup_enabled != 0U,
                                              false,
                                              (traction_encoder_mode_t)s_controller_cfg.encoder_mode,
                                              s_controller_cfg.counts_per_motor_rev,
                                              s_controller_cfg.gear_ratio);
    if (err != ESP_OK) {
        return err;
    }

    portENTER_CRITICAL(&s_pid_mux);
    controller_cfg_sync_runtime_fields_locked();
    controller_cfg_hold_current_position_locked();
    s_pid_version++;
    s_pos_pid_version++;
    portEXIT_CRITICAL(&s_pid_mux);

    return ESP_OK;
}

static bool comm_get_rpm_state(void *ctx, traction_comm_pid_rpm_state_t *out_state)
{
    (void)ctx;
    if (!out_state) return false;

    portENTER_CRITICAL(&s_pid_mux);
    out_state->kp = s_pid_cfg.kp;
    out_state->ki = s_pid_cfg.ki;
    out_state->kd = s_pid_cfg.kd;
    out_state->setpoint_rpm = s_setpoint_rpm;
    portEXIT_CRITICAL(&s_pid_mux);

    return true;
}

static void comm_set_rpm_kp(void *ctx, float value)
{
    (void)ctx;
    portENTER_CRITICAL(&s_pid_mux);
    s_pid_cfg.kp = value;
    s_pid_version++;
    portEXIT_CRITICAL(&s_pid_mux);
}

static void comm_set_rpm_ki(void *ctx, float value)
{
    (void)ctx;
    portENTER_CRITICAL(&s_pid_mux);
    s_pid_cfg.ki = value;
    s_pid_version++;
    portEXIT_CRITICAL(&s_pid_mux);
}

static void comm_set_rpm_kd(void *ctx, float value)
{
    (void)ctx;
    portENTER_CRITICAL(&s_pid_mux);
    s_pid_cfg.kd = value;
    s_pid_version++;
    portEXIT_CRITICAL(&s_pid_mux);
}

static void comm_set_rpm_setpoint(void *ctx, float value)
{
    (void)ctx;
    portENTER_CRITICAL(&s_pid_mux);
    s_setpoint_rpm = clamp_setpoint_rpm(value);
    s_pos_mode_enabled = false;
    portEXIT_CRITICAL(&s_pid_mux);
}

static void comm_set_force_output(void *ctx, float output_pct)
{
    (void)ctx;
    if (output_pct < 0.0f) output_pct = 0.0f;
    if (output_pct > MOTOR_MAX_OUTPUT_PCT) output_pct = MOTOR_MAX_OUTPUT_PCT;

    portENTER_CRITICAL(&s_pid_mux);
    s_force_out_pct = output_pct;
    s_force_out_is_raw = false;
    portEXIT_CRITICAL(&s_pid_mux);
}

static void comm_set_force_output_raw(void *ctx, float output_pct)
{
    (void)ctx;
    if (output_pct < 0.0f) output_pct = 0.0f;
    if (output_pct > MOTOR_MAX_OUTPUT_PCT) output_pct = MOTOR_MAX_OUTPUT_PCT;

    portENTER_CRITICAL(&s_pid_mux);
    s_force_out_pct = output_pct;
    s_force_out_is_raw = true;
    portEXIT_CRITICAL(&s_pid_mux);
}

static void comm_clear_force_output(void *ctx)
{
    (void)ctx;
    portENTER_CRITICAL(&s_pid_mux);
    s_force_out_pct = -1.0f;
    s_force_out_is_raw = false;
    portEXIT_CRITICAL(&s_pid_mux);
}

static void comm_request_rpm_telem(void *ctx)
{
    (void)ctx;
    portENTER_CRITICAL(&s_pid_mux);
    s_telem_req = true;
    portEXIT_CRITICAL(&s_pid_mux);
}

static bool comm_enqueue_rpm_save(void *ctx, const traction_comm_pid_rpm_state_t *state)
{
    (void)ctx;
    if (!s_nvs_queue || !state) return false;

    const nvs_save_req_t req = {
        .kind = NVS_SAVE_KIND_RPM,
        .rpm = {
            .kp = state->kp,
            .ki = state->ki,
            .kd = state->kd,
            .setpoint_rpm = state->setpoint_rpm,
        },
    };
    return (xQueueOverwrite(s_nvs_queue, &req) == pdPASS);
}

static bool comm_get_pos_state(void *ctx, traction_comm_pid_pos_state_t *out_state)
{
    (void)ctx;
    if (!out_state) return false;

    portENTER_CRITICAL(&s_pid_mux);
    out_state->kp = s_pos_pid_cfg.kp;
    out_state->ki = s_pos_pid_cfg.ki;
    out_state->kd = s_pos_pid_cfg.kd;
    out_state->target_deg = s_target_pos_deg;
    out_state->enabled = s_pos_mode_enabled;
    portEXIT_CRITICAL(&s_pid_mux);

    return true;
}

static void comm_set_pos_kp(void *ctx, float value)
{
    (void)ctx;
    portENTER_CRITICAL(&s_pid_mux);
    s_pos_pid_cfg.kp = value;
    s_pos_pid_version++;
    portEXIT_CRITICAL(&s_pid_mux);
}

static void comm_set_pos_ki(void *ctx, float value)
{
    (void)ctx;
    portENTER_CRITICAL(&s_pid_mux);
    s_pos_pid_cfg.ki = value;
    s_pos_pid_version++;
    portEXIT_CRITICAL(&s_pid_mux);
}

static void comm_set_pos_kd(void *ctx, float value)
{
    (void)ctx;
    portENTER_CRITICAL(&s_pid_mux);
    s_pos_pid_cfg.kd = value;
    s_pos_pid_version++;
    portEXIT_CRITICAL(&s_pid_mux);
}

static void comm_set_pos_target_deg(void *ctx, float value)
{
    (void)ctx;
    portENTER_CRITICAL(&s_pid_mux);
    s_target_pos_deg = sanitize_pos_target_deg(value);
    s_pos_target_explicit = true;
    s_pos_sine_enabled = false;
    portEXIT_CRITICAL(&s_pid_mux);
}

static void comm_set_pos_enabled(void *ctx, bool enabled)
{
    (void)ctx;

    float hold_deg = s_target_pos_deg;
    int64_t count = 0;
    bool encoder_invert = false;
    bool keep_existing_target = false;

    portENTER_CRITICAL(&s_pid_mux);
    encoder_invert = s_encoder_invert;
    keep_existing_target = s_pos_target_explicit;
    portEXIT_CRITICAL(&s_pid_mux);

    if (enabled && !keep_existing_target && traction_encoder_get_count(&count) == ESP_OK) {
        if (encoder_invert) {
            count = -count;
        }
        hold_deg = traction_counts_to_output_deg(count);
    }

    portENTER_CRITICAL(&s_pid_mux);
    s_pos_mode_enabled = enabled;
    s_force_out_pct = -1.0f;
    s_force_out_is_raw = false;
    s_setpoint_rpm = 0.0f;
    if (enabled) {
        s_target_pos_deg = sanitize_pos_target_deg(hold_deg);
    } else {
        s_pos_sine_enabled = false;
    }
    portEXIT_CRITICAL(&s_pid_mux);
}

static bool comm_enqueue_pos_save(void *ctx, const traction_comm_pid_pos_state_t *state)
{
    (void)ctx;
    if (!s_nvs_queue || !state) return false;

    const nvs_save_req_t req = {
        .kind = NVS_SAVE_KIND_POS,
        .pos = {
            .kp = state->kp,
            .ki = state->ki,
            .kd = state->kd,
            .target_deg = state->target_deg,
        },
    };
    return (xQueueOverwrite(s_nvs_queue, &req) == pdPASS);
}

static bool comm_get_pos_sine_state(void *ctx, traction_comm_pid_pos_sine_state_t *out_state)
{
    (void)ctx;
    if (!out_state) return false;

    portENTER_CRITICAL(&s_pid_mux);
    out_state->amp_deg = s_pos_sine_amp_deg;
    out_state->offset_deg = s_pos_sine_offset_deg;
    out_state->period_s = s_pos_sine_period_s;
    out_state->enabled = s_pos_sine_enabled;
    portEXIT_CRITICAL(&s_pid_mux);
    return true;
}

static void comm_set_pos_sine_amp_deg(void *ctx, float value)
{
    (void)ctx;
    portENTER_CRITICAL(&s_pid_mux);
    s_pos_sine_amp_deg = clamp_pos_sine_amp_deg(value);
    portEXIT_CRITICAL(&s_pid_mux);
}

static void comm_set_pos_sine_offset_deg(void *ctx, float value)
{
    (void)ctx;
    portENTER_CRITICAL(&s_pid_mux);
    s_pos_sine_offset_deg = clamp_angle_deg(value);
    portEXIT_CRITICAL(&s_pid_mux);
}

static void comm_set_pos_sine_period_s(void *ctx, float value)
{
    (void)ctx;
    portENTER_CRITICAL(&s_pid_mux);
    s_pos_sine_period_s = clamp_pos_sine_period_s(value);
    portEXIT_CRITICAL(&s_pid_mux);
}

static void comm_set_pos_sine_enabled(void *ctx, bool enabled)
{
    (void)ctx;
    const int64_t now_us = esp_timer_get_time();

    portENTER_CRITICAL(&s_pid_mux);
    s_pos_sine_enabled = enabled;
    if (enabled) {
        s_pos_mode_enabled = true;
        s_pos_target_explicit = true;
        s_force_out_pct = -1.0f;
        s_force_out_is_raw = false;
        s_setpoint_rpm = 0.0f;
        s_pos_sine_t0_us = now_us;
        s_target_pos_deg = sanitize_pos_target_deg(s_pos_sine_offset_deg);
    }
    portEXIT_CRITICAL(&s_pid_mux);
}

static void comm_request_pos_telem(void *ctx)
{
    (void)ctx;
    portENTER_CRITICAL(&s_pid_mux);
    s_pos_telem_req = true;
    portEXIT_CRITICAL(&s_pid_mux);
}

static bool enqueue_invert_save(bool motor_invert, bool encoder_invert)
{
    if (!s_nvs_queue) return false;
    const nvs_save_req_t req = {
        .kind = NVS_SAVE_KIND_INVERT,
        .invert = {
            .motor_invert = motor_invert ? 1U : 0U,
            .encoder_invert = encoder_invert ? 1U : 0U,
            .reserved0 = 0U,
            .reserved1 = 0U,
        },
    };
    return (xQueueOverwrite(s_nvs_queue, &req) == pdPASS);
}

static bool enqueue_bridge_save(traction_motor_bridge_t bridge)
{
    if (!s_nvs_queue) return false;
    const nvs_save_req_t req = {
        .kind = NVS_SAVE_KIND_BRIDGE,
        .bridge = {
            .bridge_type = (uint8_t)bridge,
            .reserved0 = 0U,
            .reserved1 = 0U,
            .reserved2 = 0U,
        },
    };
    return (xQueueOverwrite(s_nvs_queue, &req) == pdPASS);
}

static bool comm_get_invert_state(void *ctx, traction_comm_invert_state_t *out_state)
{
    (void)ctx;
    if (!out_state) return false;

    portENTER_CRITICAL(&s_pid_mux);
    out_state->motor_invert = s_motor_invert;
    out_state->encoder_invert = s_encoder_invert;
    portEXIT_CRITICAL(&s_pid_mux);
    return true;
}

static void comm_set_motor_invert(void *ctx, bool enabled)
{
    (void)ctx;
    bool encoder_invert = false;

    portENTER_CRITICAL(&s_pid_mux);
    s_motor_invert = enabled;
    s_controller_cfg.motor_invert = enabled ? 1U : 0U;
    encoder_invert = s_encoder_invert;
    controller_cfg_hold_current_position_locked();
    portEXIT_CRITICAL(&s_pid_mux);

    if (!enqueue_invert_save(enabled, encoder_invert)) {
        ESP_LOGW(TAG, "invert save enqueue failed (motor=%d, encoder=%d)",
                 enabled ? 1 : 0, encoder_invert ? 1 : 0);
    }
}

static void comm_set_encoder_invert(void *ctx, bool enabled)
{
    (void)ctx;
    bool motor_invert = false;

    portENTER_CRITICAL(&s_pid_mux);
    s_encoder_invert = enabled;
    s_controller_cfg.encoder_invert = enabled ? 1U : 0U;
    controller_cfg_hold_current_position_locked();
    motor_invert = s_motor_invert;
    portEXIT_CRITICAL(&s_pid_mux);

    if (!enqueue_invert_save(motor_invert, enabled)) {
        ESP_LOGW(TAG, "invert save enqueue failed (motor=%d, encoder=%d)",
                 motor_invert ? 1 : 0, enabled ? 1 : 0);
    }
}

static bool comm_get_bridge_state(void *ctx, traction_comm_bridge_state_t *out_state)
{
    (void)ctx;
    if (!out_state) return false;

    portENTER_CRITICAL(&s_pid_mux);
    out_state->bridge_type = (uint8_t)s_motor_bridge;
    portEXIT_CRITICAL(&s_pid_mux);
    return true;
}

static void comm_set_bridge_type(void *ctx, uint8_t bridge_type)
{
    (void)ctx;
    if (!bridge_type_is_valid_u8(bridge_type)) {
        ESP_LOGW(TAG, "bridge set ignored: invalid type=%u", (unsigned)bridge_type);
        return;
    }

    const traction_motor_bridge_t bridge = bridge_from_u8_or_default(bridge_type);
    portENTER_CRITICAL(&s_pid_mux);
    s_motor_bridge = bridge;
    s_controller_cfg.bridge_type = (uint8_t)bridge;
    portEXIT_CRITICAL(&s_pid_mux);

    esp_err_t err = traction_motor_set_bridge(bridge);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "bridge apply failed (%d)", (int)err);
    }

    if (!enqueue_bridge_save(bridge)) {
        ESP_LOGW(TAG, "bridge save enqueue failed (bridge=%s)", bridge_name(bridge));
    }
}

static bool comm_get_curve_state(void *ctx, traction_comm_curve_state_t *out_state)
{
    (void)ctx;
    if (!out_state) return false;

    portENTER_CRITICAL(&s_pid_mux);
    for (size_t i = 0; i < TRACTION_MOTOR_CURVE_POINT_COUNT; i++) {
        out_state->rpm_points[i] = s_motor_curve.rpm_points[i];
    }
    portEXIT_CRITICAL(&s_pid_mux);
    return true;
}

static void comm_set_curve_point(void *ctx, uint8_t point_index, float rpm_value)
{
    (void)ctx;
    if (point_index >= TRACTION_MOTOR_CURVE_POINT_COUNT) {
        return;
    }
    if (rpm_value < 0.0f) {
        rpm_value = 0.0f;
    }

    portENTER_CRITICAL(&s_pid_mux);
    s_motor_curve.version = CURVE_STORE_VERSION;
    s_motor_curve.count = TRACTION_MOTOR_CURVE_POINT_COUNT;
    s_motor_curve.rpm_points[point_index] = rpm_value;
    s_controller_cfg.rpm_max = s_motor_curve.rpm_points[TRACTION_MOTOR_CURVE_POINT_COUNT - 1U];
    portEXIT_CRITICAL(&s_pid_mux);
}

static bool comm_enqueue_curve_save(void *ctx, const traction_comm_curve_state_t *state)
{
    (void)ctx;
    if (!s_nvs_queue || !state) return false;

    nvs_save_req_t req = {0};
    req.kind = NVS_SAVE_KIND_CURVE;
    req.curve.version = CURVE_STORE_VERSION;
    req.curve.count = TRACTION_MOTOR_CURVE_POINT_COUNT;
    for (size_t i = 0; i < TRACTION_MOTOR_CURVE_POINT_COUNT; i++) {
        req.curve.rpm_points[i] = state->rpm_points[i];
    }
    return (xQueueOverwrite(s_nvs_queue, &req) == pdPASS);
}

static bool comm_get_controller_cfg_state(void *ctx, traction_comm_controller_cfg_state_t *out_state)
{
    (void)ctx;
    if (!out_state) return false;

    portENTER_CRITICAL(&s_pid_mux);
    out_state->bridge_type = s_controller_cfg.bridge_type;
    out_state->encoder_mode = s_controller_cfg.encoder_mode;
    out_state->motor_invert = s_controller_cfg.motor_invert;
    out_state->encoder_invert = s_controller_cfg.encoder_invert;
    out_state->pullup_enabled = s_controller_cfg.pullup_enabled;
    out_state->pwm_freq_hz = s_controller_cfg.pwm_freq_hz;
    out_state->counts_per_motor_rev = s_controller_cfg.counts_per_motor_rev;
    out_state->gear_ratio = s_controller_cfg.gear_ratio;
    out_state->rpm_max = s_controller_cfg.rpm_max;
    memcpy(out_state->notes, s_controller_cfg.notes, sizeof(out_state->notes));
    portEXIT_CRITICAL(&s_pid_mux);
    out_state->notes[sizeof(out_state->notes) - 1U] = '\0';
    return true;
}

static bool controller_cfg_state_is_valid(const traction_comm_controller_cfg_state_t *state)
{
    return state &&
           bridge_type_is_valid_u8(state->bridge_type) &&
           encoder_mode_is_valid_u8(state->encoder_mode) &&
           state->pwm_freq_hz > 0 &&
           state->counts_per_motor_rev > 0 &&
           state->gear_ratio > 0 &&
           state->rpm_max >= 0.0f;
}

static bool comm_set_controller_cfg_state(void *ctx, const traction_comm_controller_cfg_state_t *state)
{
    (void)ctx;
    if (!controller_cfg_state_is_valid(state)) {
        return false;
    }

    portENTER_CRITICAL(&s_pid_mux);
    s_controller_cfg.version = CONTROLLER_CFG_STORE_VERSION;
    s_controller_cfg.bridge_type = state->bridge_type;
    s_controller_cfg.encoder_mode = state->encoder_mode;
    s_controller_cfg.motor_invert = state->motor_invert ? 1U : 0U;
    s_controller_cfg.encoder_invert = state->encoder_invert ? 1U : 0U;
    s_controller_cfg.pullup_enabled = state->pullup_enabled ? 1U : 0U;
    s_controller_cfg.pwm_freq_hz = state->pwm_freq_hz;
    s_controller_cfg.counts_per_motor_rev = state->counts_per_motor_rev;
    s_controller_cfg.gear_ratio = state->gear_ratio;
    s_controller_cfg.rpm_max = state->rpm_max;
    s_motor_curve.version = CURVE_STORE_VERSION;
    s_motor_curve.count = TRACTION_MOTOR_CURVE_POINT_COUNT;
    s_motor_curve.rpm_points[TRACTION_MOTOR_CURVE_POINT_COUNT - 1U] = state->rpm_max;
    memcpy(s_controller_cfg.notes, state->notes, sizeof(s_controller_cfg.notes));
    s_controller_cfg.notes[sizeof(s_controller_cfg.notes) - 1U] = '\0';
    portEXIT_CRITICAL(&s_pid_mux);

    return (controller_cfg_apply_runtime() == ESP_OK);
}

static bool comm_enqueue_controller_cfg_save(void *ctx, const traction_comm_controller_cfg_state_t *state)
{
    (void)ctx;
    if (!s_nvs_queue || !controller_cfg_state_is_valid(state)) return false;

    nvs_save_req_t req = {0};
    req.kind = NVS_SAVE_KIND_CONTROLLER_CFG;
    req.controller_cfg.version = CONTROLLER_CFG_STORE_VERSION;
    req.controller_cfg.bridge_type = state->bridge_type;
    req.controller_cfg.encoder_mode = state->encoder_mode;
    req.controller_cfg.motor_invert = state->motor_invert ? 1U : 0U;
    req.controller_cfg.encoder_invert = state->encoder_invert ? 1U : 0U;
    req.controller_cfg.pullup_enabled = state->pullup_enabled ? 1U : 0U;
    req.controller_cfg.pwm_freq_hz = state->pwm_freq_hz;
    req.controller_cfg.counts_per_motor_rev = state->counts_per_motor_rev;
    req.controller_cfg.gear_ratio = state->gear_ratio;
    req.controller_cfg.rpm_max = state->rpm_max;
    memcpy(req.controller_cfg.notes, state->notes, sizeof(req.controller_cfg.notes));
    req.controller_cfg.notes[sizeof(req.controller_cfg.notes) - 1U] = '\0';

    portENTER_CRITICAL(&s_pid_mux);
    req.curve = s_motor_curve;
    req.curve.version = CURVE_STORE_VERSION;
    req.curve.count = TRACTION_MOTOR_CURVE_POINT_COUNT;
    req.curve.rpm_points[TRACTION_MOTOR_CURVE_POINT_COUNT - 1U] = state->rpm_max;
    portEXIT_CRITICAL(&s_pid_mux);
    return (xQueueOverwrite(s_nvs_queue, &req) == pdPASS);
}

static void comm_on_link_timeout(void *ctx)
{
    (void)ctx;
    bool keep_forced_output = false;
    float forced_output = -1.0f;
    bool force_output_is_raw = false;

    portENTER_CRITICAL(&s_pid_mux);
    forced_output = s_force_out_pct;
    force_output_is_raw = s_force_out_is_raw;
    keep_forced_output = (forced_output >= 0.0f);
    portEXIT_CRITICAL(&s_pid_mux);

#if PRESERVE_FORCE_OUTPUT_ON_LINK_TIMEOUT
    set_control_to_zero(false);
    if (keep_forced_output) {
        ESP_LOGW(TAG, "serial link timeout, preserving forced output=%.2f%% (%s)",
                 (double)forced_output,
                 force_output_is_raw ? "raw" : "normalized");
    } else {
        ESP_LOGW(TAG, "serial link timeout, forcing SP=0");
    }
#else
    set_control_to_zero(true);
    ESP_LOGW(TAG, "serial link timeout, forcing SP=0");
#endif
}

static void pid_defaults(void)
{
    controller_cfg_defaults();
    s_pid_cfg.kp = PID_KP_DEFAULT;
    s_pid_cfg.ki = PID_KI_DEFAULT;
    s_pid_cfg.kd = PID_KD_DEFAULT;
    s_pid_cfg.d_alpha = PID_D_ALPHA_DEFAULT;
    s_pid_cfg.out_min = 0.0f;
    s_pid_cfg.out_max = (float)MOTOR_MAX_OUTPUT_PCT;
    s_pid_cfg.i_min = -500.0f;
    s_pid_cfg.i_max = 500.0f;
    s_setpoint_rpm = SETPOINT_DEFAULT_RPM;

    s_pos_pid_cfg.kp = POS_KP_DEFAULT;
    s_pos_pid_cfg.ki = POS_KI_DEFAULT;
    s_pos_pid_cfg.kd = POS_KD_DEFAULT;
    s_pos_pid_cfg.d_alpha = PID_D_ALPHA_DEFAULT;
    s_pos_pid_cfg.out_min = -(float)MOTOR_MAX_OUTPUT_PCT;
    s_pos_pid_cfg.out_max = (float)MOTOR_MAX_OUTPUT_PCT;
    s_pos_pid_cfg.i_min = -POS_I_WINDUP_LIMIT;
    s_pos_pid_cfg.i_max = POS_I_WINDUP_LIMIT;
    s_target_pos_deg = 0.0f;
    s_pos_target_explicit = false;
    s_pos_mode_enabled = false;
    s_pos_sine_enabled = false;
    s_pos_sine_amp_deg = POS_SINE_AMP_DEFAULT_DEG;
    s_pos_sine_offset_deg = POS_SINE_OFFSET_DEFAULT_DEG;
    s_pos_sine_period_s = POS_SINE_PERIOD_DEFAULT_S;
    s_pos_sine_t0_us = 0;
    controller_cfg_sync_runtime_fields_locked();
    motor_curve_defaults();
}

static void pid_load_from_nvs(void)
{
    esp_err_t err = traction_storage_init();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "nvs init failed (%d), using defaults", (int)err);
        return;
    }

    traction_pid_store_t st = {0};
    err = traction_storage_load_pid(&st);
    if (err == ESP_OK) {
        s_pid_cfg.kp = st.kp;
        s_pid_cfg.ki = st.ki;
        s_pid_cfg.kd = st.kd;
        s_pid_cfg.d_alpha = PID_D_ALPHA_DEFAULT;
        s_pid_cfg.out_min = 0.0f;
        s_pid_cfg.out_max = (float)MOTOR_MAX_OUTPUT_PCT;
        s_pid_cfg.i_min = -500.0f;
        s_pid_cfg.i_max = 500.0f;
        s_setpoint_rpm = clamp_setpoint_rpm(st.setpoint_rpm);
        ESP_LOGI(TAG, "PID loaded from NVS");
    } else {
        ESP_LOGW(TAG, "PID not found in NVS, using defaults");
    }

    traction_pos_pid_store_t pos = {0};
    err = traction_storage_load_pos_pid(&pos);
    if (err == ESP_OK) {
        s_pos_pid_cfg.kp = pos.kp;
        s_pos_pid_cfg.ki = pos.ki;
        s_pos_pid_cfg.kd = pos.kd;
        s_pos_pid_cfg.d_alpha = PID_D_ALPHA_DEFAULT;
        s_pos_pid_cfg.out_min = -(float)MOTOR_MAX_OUTPUT_PCT;
        s_pos_pid_cfg.out_max = (float)MOTOR_MAX_OUTPUT_PCT;
        s_pos_pid_cfg.i_min = -POS_I_WINDUP_LIMIT;
        s_pos_pid_cfg.i_max = POS_I_WINDUP_LIMIT;
        s_target_pos_deg = sanitize_pos_target_deg(pos.target_deg);
        s_pos_target_explicit = true;
        ESP_LOGI(TAG, "POS PID loaded from NVS");
    } else {
        ESP_LOGW(TAG, "POS PID not found in NVS, using defaults");
    }

    traction_invert_store_t inv = {0};
    err = traction_storage_load_invert(&inv);
    if (err == ESP_OK) {
        s_motor_invert = (inv.motor_invert != 0U);
        s_encoder_invert = (inv.encoder_invert != 0U);
        ESP_LOGI(TAG, "invert loaded from NVS (motor=%d encoder=%d)",
                 s_motor_invert ? 1 : 0, s_encoder_invert ? 1 : 0);
    } else {
        ESP_LOGW(TAG, "invert not found in NVS, using defaults");
    }

    traction_bridge_store_t bridge = {0};
    err = traction_storage_load_bridge(&bridge);
    if (err == ESP_OK && bridge_type_is_valid_u8(bridge.bridge_type)) {
        s_motor_bridge = bridge_from_u8_or_default(bridge.bridge_type);
        ESP_LOGI(TAG, "bridge loaded from NVS (%s)", bridge_name(s_motor_bridge));
    } else if (err == ESP_OK) {
        s_motor_bridge = TRACTION_MOTOR_BRIDGE_TB6612;
        ESP_LOGW(TAG, "bridge in NVS invalid (%u), defaulting to %s",
                 (unsigned)bridge.bridge_type, bridge_name(s_motor_bridge));
    } else {
        ESP_LOGW(TAG, "bridge not found in NVS, using default (%s)", bridge_name(s_motor_bridge));
    }

    err = traction_motor_set_bridge(s_motor_bridge);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "bridge apply failed (%d), using runtime default", (int)err);
    }

    traction_curve_store_t curve = {0};
    err = traction_storage_load_curve(&curve);
    if (err == ESP_OK &&
        curve.version == CURVE_STORE_VERSION &&
        curve.count == TRACTION_MOTOR_CURVE_POINT_COUNT) {
        s_motor_curve = curve;
        ESP_LOGI(TAG, "motor curve loaded from NVS");
    } else if (err == ESP_OK) {
        motor_curve_defaults();
        ESP_LOGW(TAG, "motor curve in NVS invalid, using defaults");
    } else {
        motor_curve_defaults();
        ESP_LOGW(TAG, "motor curve not found in NVS, using defaults");
    }

    traction_controller_cfg_store_t cfg = {0};
    err = traction_storage_load_controller_cfg(&cfg);
    if (err == ESP_OK &&
        cfg.version == CONTROLLER_CFG_STORE_VERSION &&
        bridge_type_is_valid_u8(cfg.bridge_type) &&
        encoder_mode_is_valid_u8(cfg.encoder_mode) &&
        cfg.pwm_freq_hz > 0 &&
        cfg.counts_per_motor_rev > 0 &&
        cfg.gear_ratio > 0) {
        cfg.notes[sizeof(cfg.notes) - 1U] = '\0';
        s_controller_cfg = cfg;
        ESP_LOGI(TAG, "controller config loaded from NVS");
    } else {
        controller_cfg_defaults();
        s_controller_cfg.bridge_type = (uint8_t)s_motor_bridge;
        s_controller_cfg.motor_invert = s_motor_invert ? 1U : 0U;
        s_controller_cfg.encoder_invert = s_encoder_invert ? 1U : 0U;
        ESP_LOGW(TAG, "controller config not found in NVS, using runtime defaults");
    }

    s_controller_cfg.rpm_max = s_motor_curve.rpm_points[TRACTION_MOTOR_CURVE_POINT_COUNT - 1U];
    err = controller_cfg_apply_runtime();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "controller config apply failed (%d), keeping initialized runtime", (int)err);
    }
}

static esp_err_t pid_save_to_nvs(float kp, float ki, float kd, float setpoint)
{
    const traction_pid_store_t st = {
        .kp = kp,
        .ki = ki,
        .kd = kd,
        .setpoint_rpm = clamp_setpoint_rpm(setpoint),
    };
    esp_err_t err = traction_storage_save_pid(&st);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "PID saved to NVS");
    } else {
        ESP_LOGW(TAG, "nvs save failed (%d)", (int)err);
    }
    return err;
}

static esp_err_t pos_pid_save_to_nvs(float kp, float ki, float kd, float target_deg)
{
    const traction_pos_pid_store_t st = {
        .version = TRACTION_POS_PID_STORE_VERSION,
        .reserved0 = 0U,
        .kp = kp,
        .ki = ki,
        .kd = kd,
        .target_deg = sanitize_pos_target_deg(target_deg),
    };
    esp_err_t err = traction_storage_save_pos_pid(&st);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "POS PID saved to NVS");
    } else {
        ESP_LOGW(TAG, "pos nvs save failed (%d)", (int)err);
    }
    return err;
}

static esp_err_t invert_save_to_nvs(bool motor_invert, bool encoder_invert)
{
    const traction_invert_store_t st = {
        .motor_invert = motor_invert ? 1U : 0U,
        .encoder_invert = encoder_invert ? 1U : 0U,
        .reserved0 = 0U,
        .reserved1 = 0U,
    };
    esp_err_t err = traction_storage_save_invert(&st);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "invert saved to NVS (motor=%d encoder=%d)",
                 motor_invert ? 1 : 0, encoder_invert ? 1 : 0);
    } else {
        ESP_LOGW(TAG, "invert nvs save failed (%d)", (int)err);
    }
    return err;
}

static esp_err_t bridge_save_to_nvs(traction_motor_bridge_t bridge)
{
    const traction_bridge_store_t st = {
        .bridge_type = (uint8_t)bridge,
        .reserved0 = 0U,
        .reserved1 = 0U,
        .reserved2 = 0U,
    };
    esp_err_t err = traction_storage_save_bridge(&st);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "bridge saved to NVS (%s)", bridge_name(bridge));
    } else {
        ESP_LOGW(TAG, "bridge nvs save failed (%d)", (int)err);
    }
    return err;
}

static esp_err_t curve_save_to_nvs(const traction_curve_store_t *curve)
{
    if (!curve) {
        return ESP_ERR_INVALID_ARG;
    }

    traction_curve_store_t st = *curve;
    st.version = CURVE_STORE_VERSION;
    st.count = TRACTION_MOTOR_CURVE_POINT_COUNT;
    esp_err_t err = traction_storage_save_curve(&st);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "motor curve saved to NVS");
    } else {
        ESP_LOGW(TAG, "motor curve nvs save failed (%d)", (int)err);
    }
    return err;
}

static esp_err_t controller_cfg_save_to_nvs(const traction_controller_cfg_store_t *cfg)
{
    if (!cfg) {
        return ESP_ERR_INVALID_ARG;
    }

    traction_controller_cfg_store_t st = *cfg;
    st.version = CONTROLLER_CFG_STORE_VERSION;
    st.notes[sizeof(st.notes) - 1U] = '\0';
    esp_err_t err = traction_storage_save_controller_cfg(&st);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "controller config saved to NVS");
    } else {
        ESP_LOGW(TAG, "controller config nvs save failed (%d)", (int)err);
    }
    return err;
}

esp_err_t traction_control_app_init(void)
{
    pid_defaults();
    pid_load_from_nvs();
    set_control_to_zero(true);
    apply_startup_motor_output();

    s_nvs_queue = xQueueCreate(1, sizeof(nvs_save_req_t));
    if (!s_nvs_queue) {
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

void traction_control_app_make_comm_cfg(traction_comm_cfg_t *out_cfg, uint32_t link_timeout_ms)
{
    if (!out_cfg) return;
    memset(out_cfg, 0, sizeof(*out_cfg));

    out_cfg->ctx = NULL;
    out_cfg->link_timeout_ms = link_timeout_ms;
    out_cfg->on_link_timeout = comm_on_link_timeout;
    out_cfg->request_rpm_telem = comm_request_rpm_telem;
    out_cfg->request_pos_telem = comm_request_pos_telem;
    out_cfg->get_rpm_state = comm_get_rpm_state;
    out_cfg->set_rpm_kp = comm_set_rpm_kp;
    out_cfg->set_rpm_ki = comm_set_rpm_ki;
    out_cfg->set_rpm_kd = comm_set_rpm_kd;
    out_cfg->set_rpm_setpoint = comm_set_rpm_setpoint;
    out_cfg->set_force_output = comm_set_force_output;
    out_cfg->set_force_output_raw = comm_set_force_output_raw;
    out_cfg->clear_force_output = comm_clear_force_output;
    out_cfg->enqueue_rpm_save = comm_enqueue_rpm_save;
    out_cfg->get_pos_state = comm_get_pos_state;
    out_cfg->set_pos_kp = comm_set_pos_kp;
    out_cfg->set_pos_ki = comm_set_pos_ki;
    out_cfg->set_pos_kd = comm_set_pos_kd;
    out_cfg->set_pos_target_deg = comm_set_pos_target_deg;
    out_cfg->set_pos_enabled = comm_set_pos_enabled;
    out_cfg->enqueue_pos_save = comm_enqueue_pos_save;
    out_cfg->get_pos_sine_state = comm_get_pos_sine_state;
    out_cfg->set_pos_sine_amp_deg = comm_set_pos_sine_amp_deg;
    out_cfg->set_pos_sine_offset_deg = comm_set_pos_sine_offset_deg;
    out_cfg->set_pos_sine_period_s = comm_set_pos_sine_period_s;
    out_cfg->set_pos_sine_enabled = comm_set_pos_sine_enabled;
    out_cfg->get_invert_state = comm_get_invert_state;
    out_cfg->set_motor_invert = comm_set_motor_invert;
    out_cfg->set_encoder_invert = comm_set_encoder_invert;
    out_cfg->get_bridge_state = comm_get_bridge_state;
    out_cfg->set_bridge_type = comm_set_bridge_type;
    out_cfg->get_curve_state = comm_get_curve_state;
    out_cfg->set_curve_point = comm_set_curve_point;
    out_cfg->enqueue_curve_save = comm_enqueue_curve_save;
    out_cfg->get_controller_cfg_state = comm_get_controller_cfg_state;
    out_cfg->set_controller_cfg_state = comm_set_controller_cfg_state;
    out_cfg->enqueue_controller_cfg_save = comm_enqueue_controller_cfg_save;
}

void traction_control_app_set_encoder_monitor_task(TaskHandle_t task)
{
    s_enc_task = task;
}

void traction_control_app_nvs_save_task(void *arg)
{
    (void)arg;

    nvs_save_req_t req = {0};
    while (1) {
        if (xQueueReceive(s_nvs_queue, &req, portMAX_DELAY) != pdTRUE) {
            continue;
        }

        if (req.kind == NVS_SAVE_KIND_RPM) {
            ESP_LOGI(TAG, "save RPM req: KP=%.3f KI=%.3f KD=%.3f SP=%.2f",
                     (double)req.rpm.kp, (double)req.rpm.ki,
                     (double)req.rpm.kd, (double)req.rpm.setpoint_rpm);
        } else if (req.kind == NVS_SAVE_KIND_POS) {
            ESP_LOGI(TAG, "save POS req: KP=%.3f KI=%.3f KD=%.3f TARGET=%.2f deg",
                     (double)req.pos.kp, (double)req.pos.ki,
                     (double)req.pos.kd, (double)req.pos.target_deg);
        } else if (req.kind == NVS_SAVE_KIND_INVERT) {
            ESP_LOGI(TAG, "save invert req: motor=%d encoder=%d",
                     req.invert.motor_invert ? 1 : 0,
                     req.invert.encoder_invert ? 1 : 0);
        } else if (req.kind == NVS_SAVE_KIND_BRIDGE) {
            ESP_LOGI(TAG, "save bridge req: bridge=%s",
                     bridge_name(bridge_from_u8_or_default(req.bridge.bridge_type)));
        } else if (req.kind == NVS_SAVE_KIND_CURVE) {
            ESP_LOGI(TAG, "save curve req: rpm10=%.2f rpm100=%.2f",
                     (double)req.curve.rpm_points[0],
                     (double)req.curve.rpm_points[TRACTION_MOTOR_CURVE_POINT_COUNT - 1U]);
        } else if (req.kind == NVS_SAVE_KIND_CONTROLLER_CFG) {
            ESP_LOGI(TAG, "save cfg req: bridge=%u mode=%u pwm=%lu counts=%ld gear=%ld",
                     (unsigned)req.controller_cfg.bridge_type,
                     (unsigned)req.controller_cfg.encoder_mode,
                     (unsigned long)req.controller_cfg.pwm_freq_hz,
                     (long)req.controller_cfg.counts_per_motor_rev,
                     (long)req.controller_cfg.gear_ratio);
        }

        if (!traction_storage_is_ready()) {
            traction_comm_send_line("S,ERR,%d", (int)ESP_ERR_INVALID_STATE);
            ESP_LOGW(TAG, "save abort: storage not ready");
            continue;
        }

        esp_err_t irq_err = traction_encoder_set_irq_enabled(false);
        if (irq_err != ESP_OK && irq_err != ESP_ERR_INVALID_STATE) {
            ESP_LOGW(TAG, "encoder irq disable failed (%d)", (int)irq_err);
        }

        if (s_speed_task) vTaskSuspend(s_speed_task);
        if (s_enc_task) vTaskSuspend(s_enc_task);

        traction_comm_send_line("S,START");
        ESP_LOGI(TAG, "save start");

        esp_err_t werr = ESP_ERR_INVALID_ARG;
        if (req.kind == NVS_SAVE_KIND_RPM) {
            werr = pid_save_to_nvs(req.rpm.kp, req.rpm.ki, req.rpm.kd, req.rpm.setpoint_rpm);
        } else if (req.kind == NVS_SAVE_KIND_POS) {
            werr = pos_pid_save_to_nvs(req.pos.kp, req.pos.ki, req.pos.kd, req.pos.target_deg);
        } else if (req.kind == NVS_SAVE_KIND_INVERT) {
            werr = invert_save_to_nvs(req.invert.motor_invert != 0U, req.invert.encoder_invert != 0U);
        } else if (req.kind == NVS_SAVE_KIND_BRIDGE) {
            werr = bridge_save_to_nvs(bridge_from_u8_or_default(req.bridge.bridge_type));
        } else if (req.kind == NVS_SAVE_KIND_CURVE) {
            werr = curve_save_to_nvs(&req.curve);
        } else if (req.kind == NVS_SAVE_KIND_CONTROLLER_CFG) {
            werr = curve_save_to_nvs(&req.curve);
            if (werr == ESP_OK) {
                werr = controller_cfg_save_to_nvs(&req.controller_cfg);
            }
        }

        if (werr == ESP_OK) {
            traction_comm_send_line("S,OK");
        } else {
            traction_comm_send_line("S,ERR,%d", (int)werr);
        }
        traction_comm_send_line("S,END");

        if (s_enc_task) vTaskResume(s_enc_task);
        if (s_speed_task) vTaskResume(s_speed_task);

        irq_err = traction_encoder_set_irq_enabled(true);
        if (irq_err != ESP_OK && irq_err != ESP_ERR_INVALID_STATE) {
            ESP_LOGW(TAG, "encoder irq enable failed (%d)", (int)irq_err);
        }
        ESP_LOGI(TAG, "save end (err=%d)", (int)werr);
    }
}

void traction_control_app_speed_task(void *arg)
{
    (void)arg;
    s_speed_task = xTaskGetCurrentTaskHandle();

    traction_pid_t pid_rpm;
    traction_pid_t pid_pos;
    traction_pid_cfg_t cfg_rpm = {0};
    traction_pid_cfg_t cfg_pos = {0};
    uint32_t last_rpm_version = 0;
    uint32_t last_pos_version = 0;

    bool motor_awake = false;

    traction_motor_coast();
    traction_motor_sleep(true);

    int64_t last_count = 0;
    int64_t last_t_us = esp_timer_get_time();

    portENTER_CRITICAL(&s_pid_mux);
    cfg_rpm = s_pid_cfg;
    cfg_pos = s_pos_pid_cfg;
    last_rpm_version = s_pid_version;
    last_pos_version = s_pos_pid_version;
    portEXIT_CRITICAL(&s_pid_mux);
    traction_pid_init(&pid_rpm, &cfg_rpm);
    traction_pid_init(&pid_pos, &cfg_pos);

    while (1) {
        vTaskDelay(pdMS_TO_TICKS(CONTROL_PERIOD_MS));

        int64_t raw_count = 0;
        ESP_ERROR_CHECK(traction_encoder_get_count(&raw_count));

        const int64_t now_us = esp_timer_get_time();
        const float dt = (now_us - last_t_us) / 1e6f;
        float target_rpm = 0.0f;
        float target_pos_deg = 0.0f;
        bool pos_mode = false;
        bool pos_sine_enabled = false;
        bool motor_invert = false;
        bool encoder_invert = false;
        float pos_sine_amp_deg = 0.0f;
        float pos_sine_offset_deg = 0.0f;
        float pos_sine_period_s = POS_SINE_PERIOD_DEFAULT_S;
        int64_t pos_sine_t0_us = 0;
        float force_out = -1.0f;
        bool force_out_is_raw = false;
        bool send_rpm_telem = false;
        bool send_pos_telem = false;

        portENTER_CRITICAL(&s_pid_mux);
        cfg_rpm = s_pid_cfg;
        cfg_pos = s_pos_pid_cfg;
        target_rpm = s_setpoint_rpm;
        target_pos_deg = s_target_pos_deg;
        pos_mode = s_pos_mode_enabled;
        pos_sine_enabled = s_pos_sine_enabled;
        pos_sine_amp_deg = s_pos_sine_amp_deg;
        pos_sine_offset_deg = s_pos_sine_offset_deg;
        pos_sine_period_s = s_pos_sine_period_s;
        pos_sine_t0_us = s_pos_sine_t0_us;
        force_out = s_force_out_pct;
        force_out_is_raw = s_force_out_is_raw;
        motor_invert = s_motor_invert;
        encoder_invert = s_encoder_invert;

        if (s_pid_version != last_rpm_version) {
            last_rpm_version = s_pid_version;
            traction_pid_init(&pid_rpm, &cfg_rpm);
        }
        if (s_pos_pid_version != last_pos_version) {
            last_pos_version = s_pos_pid_version;
            traction_pid_init(&pid_pos, &cfg_pos);
        }

        if (s_telem_req) {
            s_telem_req = false;
            send_rpm_telem = true;
        }
        if (s_pos_telem_req) {
            s_pos_telem_req = false;
            send_pos_telem = true;
        }
        portEXIT_CRITICAL(&s_pid_mux);

        const int64_t c = encoder_invert ? -raw_count : raw_count;
        const int64_t dc = c - last_count;

        float rpm = 0.0f;
        if (dt > 0.0f) {
            rpm = traction_delta_counts_to_output_rpm(dc, dt);
        }
        if (rpm > -5.0f && rpm < 5.0f) {
            rpm = 0.0f;
        }

        const float pos_deg = traction_counts_to_output_deg(c);

        if (pos_mode && pos_sine_enabled) {
            const float elapsed_s = (float)(now_us - pos_sine_t0_us) / 1e6f;
            const float phase = (2.0f * PI_F * elapsed_s) / clamp_pos_sine_period_s(pos_sine_period_s);
            const float target_deg = clamp_angle_deg(pos_sine_offset_deg + (pos_sine_amp_deg * sinf(phase)));
            target_pos_deg = sanitize_pos_target_deg(target_deg);
            portENTER_CRITICAL(&s_pid_mux);
            s_target_pos_deg = target_pos_deg;
            portEXIT_CRITICAL(&s_pid_mux);
        }

        traction_dir_t dir = TRACTION_DIR_CW;
        const float target_abs = fabsf(target_rpm);
        const float rpm_abs = fabsf(rpm);
        float cmd_raw = 0.0f;
        float cmd_pwm_mag = 0.0f;
        float cmd_pwm_signed = 0.0f;

        if (force_out >= 0.0f) {
            dir = (target_rpm >= 0.0f) ? TRACTION_DIR_CW : TRACTION_DIR_CCW;
            cmd_raw = force_out;
            if (force_out_is_raw) {
                cmd_pwm_mag = force_out;
            } else {
#if ENABLE_OUTPUT_LINEARIZATION
                cmd_pwm_mag = linearize_output_percent(cmd_raw);
#else
                cmd_pwm_mag = cmd_raw;
#endif
            }
            if (cmd_pwm_mag > 0.0f && !motor_awake) {
                traction_motor_sleep(false);
                motor_awake = true;
            }
            const traction_dir_t applied_dir = apply_motor_invert(dir, motor_invert);
            cmd_pwm_signed = (applied_dir == TRACTION_DIR_CW) ? cmd_pwm_mag : -cmd_pwm_mag;
            traction_motor_set((int)cmd_pwm_mag, applied_dir);
        } else if (pos_mode) {
            const float pos_err_deg = target_pos_deg - pos_deg;
            if (fabsf(pos_err_deg) <= POSITION_DEADBAND_DEG) {
                if (!motor_awake) {
                    traction_motor_sleep(false);
                    motor_awake = true;
                }
                traction_motor_brake();
                traction_pid_reset(&pid_pos);
                cmd_raw = 0.0f;
                cmd_pwm_signed = 0.0f;
            } else {
                cmd_raw = traction_pid_update(&pid_pos,
                                              deg_to_pos_rev(target_pos_deg),
                                              deg_to_pos_rev(pos_deg),
                                              dt);
                dir = (cmd_raw >= 0.0f) ? TRACTION_DIR_CW : TRACTION_DIR_CCW;
                const float cmd_abs = fabsf(cmd_raw);
#if ENABLE_OUTPUT_LINEARIZATION
                cmd_pwm_mag = linearize_output_percent(cmd_abs);
#else
                cmd_pwm_mag = cmd_abs;
#endif
                cmd_pwm_mag *= POS_MODE_OUTPUT_BOOST;
                if (cmd_pwm_mag > (float)MOTOR_MAX_OUTPUT_PCT) {
                    cmd_pwm_mag = (float)MOTOR_MAX_OUTPUT_PCT;
                }
                if (cmd_pwm_mag >= POSITION_BRAKE_HOLD_MIN_PWM_PCT && !motor_awake) {
                    traction_motor_sleep(false);
                    motor_awake = true;
                }
                if (cmd_pwm_mag < POSITION_BRAKE_HOLD_MIN_PWM_PCT) {
                    if (!motor_awake) {
                        traction_motor_sleep(false);
                        motor_awake = true;
                    }
                    traction_motor_brake();
                    cmd_pwm_mag = 0.0f;
                    cmd_pwm_signed = 0.0f;
                } else {
                    const traction_dir_t applied_dir = apply_motor_invert(dir, motor_invert);
                    cmd_pwm_signed = (applied_dir == TRACTION_DIR_CW) ? cmd_pwm_mag : -cmd_pwm_mag;
                    traction_motor_set((int)cmd_pwm_mag, applied_dir);
                }
            }
        } else if (target_abs <= SETPOINT_MIN_RPM) {
            if (motor_awake) {
                traction_motor_coast();
                traction_motor_sleep(true);
                motor_awake = false;
            }
            traction_pid_reset(&pid_rpm);
            cmd_raw = 0.0f;
            cmd_pwm_signed = 0.0f;
        } else {
            dir = (target_rpm >= 0.0f) ? TRACTION_DIR_CW : TRACTION_DIR_CCW;
            cmd_raw = traction_pid_update(&pid_rpm, target_abs, rpm_abs, dt);
#if ENABLE_OUTPUT_LINEARIZATION
            cmd_pwm_mag = linearize_output_percent(cmd_raw);
#else
            cmd_pwm_mag = cmd_raw;
#endif
            if (cmd_pwm_mag > 0.0f && !motor_awake) {
                traction_motor_sleep(false);
                motor_awake = true;
            }
            const traction_dir_t applied_dir = apply_motor_invert(dir, motor_invert);
            cmd_pwm_signed = (applied_dir == TRACTION_DIR_CW) ? cmd_pwm_mag : -cmd_pwm_mag;
            traction_motor_set((int)cmd_pwm_mag, applied_dir);
        }

        if (send_rpm_telem) {
            traction_comm_send_line("T,%.2f,%.2f,%.2f,%.2f",
                                    (double)target_rpm, (double)rpm,
                                    (double)cmd_pwm_signed, (double)cmd_raw);
        }
        if (send_pos_telem) {
            traction_comm_send_line("TP,%.4f,%.4f,%.2f,%.2f",
                                    (double)target_pos_deg, (double)pos_deg,
                                    (double)cmd_pwm_signed, (double)cmd_raw);
        }

        last_count = c;
        last_t_us = now_us;
    }
}
