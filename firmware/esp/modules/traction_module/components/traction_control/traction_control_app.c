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

// Output-to-RPM linearization (tuned curve)
#define ENABLE_OUTPUT_LINEARIZATION 1
#define RPM_AT_100_PCT 199.89f
#define LIN_DEADBAND_PCT 40.0f
#define LIN_A 0.5855839f
#define LIN_B 1.42f

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
#define POSITION_DEADBAND_REV      0.01f
#define POS_I_WINDUP_LIMIT         12000000.0f
#define POS_MODE_OUTPUT_BOOST      1.10f
#define POS_TARGET_MIN_REV         0.0f
#define POS_TARGET_MAX_REV         1.0f
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
} nvs_save_kind_t;

typedef struct {
    nvs_save_kind_t kind;
    traction_pid_store_t rpm;
    traction_pos_pid_store_t pos;
} nvs_save_req_t;

static traction_pid_cfg_t s_pid_cfg;
static traction_pid_cfg_t s_pos_pid_cfg;
static float s_setpoint_rpm = SETPOINT_DEFAULT_RPM;
static float s_target_pos_rev = 0.0f;
static bool s_pos_sine_enabled = false;
static float s_pos_sine_amp_deg = POS_SINE_AMP_DEFAULT_DEG;
static float s_pos_sine_offset_deg = POS_SINE_OFFSET_DEFAULT_DEG;
static float s_pos_sine_period_s = POS_SINE_PERIOD_DEFAULT_S;
static int64_t s_pos_sine_t0_us = 0;
static uint32_t s_pid_version = 0;
static uint32_t s_pos_pid_version = 0;
static portMUX_TYPE s_pid_mux = portMUX_INITIALIZER_UNLOCKED;
static int s_force_out_pct = -1;
static bool s_pos_mode_enabled = false;
static QueueHandle_t s_nvs_queue = NULL;
static bool s_telem_req = false;
static bool s_pos_telem_req = false;
static TaskHandle_t s_speed_task = NULL;
static TaskHandle_t s_enc_task = NULL;

static void set_control_to_zero(bool clear_force_output)
{
    portENTER_CRITICAL(&s_pid_mux);
    s_setpoint_rpm = 0.0f;
    s_target_pos_rev = 0.0f;
    if (clear_force_output) {
        s_force_out_pct = -1;
    }
    s_pos_mode_enabled = false;
    s_pos_sine_enabled = false;
    portEXIT_CRITICAL(&s_pid_mux);
}

static float linearize_output_percent(float cmd_pct)
{
    if (cmd_pct <= 0.0f) return 0.0f;
    if (cmd_pct >= 100.0f) return 100.0f;
    const float desired_rpm = (cmd_pct / 100.0f) * RPM_AT_100_PCT;
    if (desired_rpm <= 0.01f) return 0.0f;
    float out = LIN_DEADBAND_PCT + powf(desired_rpm / LIN_A, 1.0f / LIN_B);
    if (out < 0.0f) out = 0.0f;
    if (out > 100.0f) out = 100.0f;
    return out;
}

static float clamp_setpoint_rpm(float sp)
{
    if (sp > SETPOINT_MAX_RPM) return SETPOINT_MAX_RPM;
    if (sp < -SETPOINT_MAX_RPM) return -SETPOINT_MAX_RPM;
    return sp;
}

static float clamp_pos_target_rev(float target_rev)
{
    if (target_rev > POS_TARGET_MAX_REV) return POS_TARGET_MAX_REV;
    if (target_rev < POS_TARGET_MIN_REV) return POS_TARGET_MIN_REV;
    return target_rev;
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

static void comm_set_force_output(void *ctx, int output_pct)
{
    (void)ctx;
    if (output_pct < 0) output_pct = 0;
    if (output_pct > MOTOR_MAX_OUTPUT_PCT) output_pct = MOTOR_MAX_OUTPUT_PCT;

    portENTER_CRITICAL(&s_pid_mux);
    s_force_out_pct = output_pct;
    portEXIT_CRITICAL(&s_pid_mux);
}

static void comm_clear_force_output(void *ctx)
{
    (void)ctx;
    portENTER_CRITICAL(&s_pid_mux);
    s_force_out_pct = -1;
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
    out_state->target_rev = s_target_pos_rev;
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

static void comm_set_pos_target_rev(void *ctx, float value)
{
    (void)ctx;
    portENTER_CRITICAL(&s_pid_mux);
    s_target_pos_rev = clamp_pos_target_rev(value);
    s_pos_sine_enabled = false;
    portEXIT_CRITICAL(&s_pid_mux);
}

static void comm_set_pos_enabled(void *ctx, bool enabled)
{
    (void)ctx;

    float hold_rev = s_target_pos_rev;
    int64_t count = 0;
    if (enabled && traction_encoder_get_count(&count) == ESP_OK) {
        hold_rev = traction_counts_to_output_rev(count);
    }

    portENTER_CRITICAL(&s_pid_mux);
    s_pos_mode_enabled = enabled;
    s_force_out_pct = -1;
    s_setpoint_rpm = 0.0f;
    if (enabled) {
        s_target_pos_rev = clamp_pos_target_rev(hold_rev);
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
            .target_rev = state->target_rev,
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
        s_force_out_pct = -1;
        s_setpoint_rpm = 0.0f;
        s_pos_sine_t0_us = now_us;
        s_target_pos_rev = clamp_pos_target_rev(s_pos_sine_offset_deg / 360.0f);
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

static void comm_on_link_timeout(void *ctx)
{
    (void)ctx;
    bool keep_forced_output = false;
    int forced_output = -1;

    portENTER_CRITICAL(&s_pid_mux);
    forced_output = s_force_out_pct;
    keep_forced_output = (forced_output >= 0);
    portEXIT_CRITICAL(&s_pid_mux);

#if PRESERVE_FORCE_OUTPUT_ON_LINK_TIMEOUT
    set_control_to_zero(false);
    if (keep_forced_output) {
        ESP_LOGW(TAG, "serial link timeout, preserving forced output=%d%%", forced_output);
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
    s_target_pos_rev = 0.0f;
    s_pos_mode_enabled = false;
    s_pos_sine_enabled = false;
    s_pos_sine_amp_deg = POS_SINE_AMP_DEFAULT_DEG;
    s_pos_sine_offset_deg = POS_SINE_OFFSET_DEFAULT_DEG;
    s_pos_sine_period_s = POS_SINE_PERIOD_DEFAULT_S;
    s_pos_sine_t0_us = 0;
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
        s_target_pos_rev = clamp_pos_target_rev(pos.target_rev);
        ESP_LOGI(TAG, "POS PID loaded from NVS");
    } else {
        ESP_LOGW(TAG, "POS PID not found in NVS, using defaults");
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

static esp_err_t pos_pid_save_to_nvs(float kp, float ki, float kd, float target_rev)
{
    const traction_pos_pid_store_t st = {
        .kp = kp,
        .ki = ki,
        .kd = kd,
        .target_rev = clamp_pos_target_rev(target_rev),
    };
    esp_err_t err = traction_storage_save_pos_pid(&st);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "POS PID saved to NVS");
    } else {
        ESP_LOGW(TAG, "pos nvs save failed (%d)", (int)err);
    }
    return err;
}

esp_err_t traction_control_app_init(void)
{
    pid_defaults();
    pid_load_from_nvs();
    set_control_to_zero(true);

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
    out_cfg->clear_force_output = comm_clear_force_output;
    out_cfg->enqueue_rpm_save = comm_enqueue_rpm_save;
    out_cfg->get_pos_state = comm_get_pos_state;
    out_cfg->set_pos_kp = comm_set_pos_kp;
    out_cfg->set_pos_ki = comm_set_pos_ki;
    out_cfg->set_pos_kd = comm_set_pos_kd;
    out_cfg->set_pos_target_rev = comm_set_pos_target_rev;
    out_cfg->set_pos_enabled = comm_set_pos_enabled;
    out_cfg->enqueue_pos_save = comm_enqueue_pos_save;
    out_cfg->get_pos_sine_state = comm_get_pos_sine_state;
    out_cfg->set_pos_sine_amp_deg = comm_set_pos_sine_amp_deg;
    out_cfg->set_pos_sine_offset_deg = comm_set_pos_sine_offset_deg;
    out_cfg->set_pos_sine_period_s = comm_set_pos_sine_period_s;
    out_cfg->set_pos_sine_enabled = comm_set_pos_sine_enabled;
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
        } else {
            ESP_LOGI(TAG, "save POS req: KP=%.3f KI=%.3f KD=%.3f TARGET=%.4f",
                     (double)req.pos.kp, (double)req.pos.ki,
                     (double)req.pos.kd, (double)req.pos.target_rev);
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
            werr = pos_pid_save_to_nvs(req.pos.kp, req.pos.ki, req.pos.kd, req.pos.target_rev);
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

    traction_motor_sleep(false);

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

        int64_t c = 0;
        ESP_ERROR_CHECK(traction_encoder_get_count(&c));

        const int64_t now_us = esp_timer_get_time();
        const float dt = (now_us - last_t_us) / 1e6f;
        const int64_t dc = c - last_count;

        float rpm = 0.0f;
        if (dt > 0.0f) {
            rpm = traction_delta_counts_to_output_rpm(dc, dt);
        }
        if (rpm > -5.0f && rpm < 5.0f) {
            rpm = 0.0f;
        }

        const float pos_rev = traction_counts_to_output_rev(c);
        float target_rpm = 0.0f;
        float target_pos = 0.0f;
        bool pos_mode = false;
        bool pos_sine_enabled = false;
        float pos_sine_amp_deg = 0.0f;
        float pos_sine_offset_deg = 0.0f;
        float pos_sine_period_s = POS_SINE_PERIOD_DEFAULT_S;
        int64_t pos_sine_t0_us = 0;
        int force_out = -1;
        bool send_rpm_telem = false;
        bool send_pos_telem = false;

        portENTER_CRITICAL(&s_pid_mux);
        cfg_rpm = s_pid_cfg;
        cfg_pos = s_pos_pid_cfg;
        target_rpm = s_setpoint_rpm;
        target_pos = s_target_pos_rev;
        pos_mode = s_pos_mode_enabled;
        pos_sine_enabled = s_pos_sine_enabled;
        pos_sine_amp_deg = s_pos_sine_amp_deg;
        pos_sine_offset_deg = s_pos_sine_offset_deg;
        pos_sine_period_s = s_pos_sine_period_s;
        pos_sine_t0_us = s_pos_sine_t0_us;
        force_out = s_force_out_pct;

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

        if (pos_mode && pos_sine_enabled) {
            const float elapsed_s = (float)(now_us - pos_sine_t0_us) / 1e6f;
            const float phase = (2.0f * PI_F * elapsed_s) / clamp_pos_sine_period_s(pos_sine_period_s);
            const float target_deg = clamp_angle_deg(pos_sine_offset_deg + (pos_sine_amp_deg * sinf(phase)));
            target_pos = clamp_pos_target_rev(target_deg / 360.0f);
            portENTER_CRITICAL(&s_pid_mux);
            s_target_pos_rev = target_pos;
            portEXIT_CRITICAL(&s_pid_mux);
        }

        traction_dir_t dir = TRACTION_DIR_CW;
        const float target_abs = fabsf(target_rpm);
        const float rpm_abs = fabsf(rpm);
        float cmd_raw = 0.0f;
        float cmd_pwm_mag = 0.0f;
        float cmd_pwm_signed = 0.0f;

        if (force_out >= 0) {
            dir = (target_rpm >= 0.0f) ? TRACTION_DIR_CW : TRACTION_DIR_CCW;
            cmd_raw = (float)force_out;
#if ENABLE_OUTPUT_LINEARIZATION
            cmd_pwm_mag = linearize_output_percent(cmd_raw);
#else
            cmd_pwm_mag = cmd_raw;
#endif
            cmd_pwm_signed = (dir == TRACTION_DIR_CW) ? cmd_pwm_mag : -cmd_pwm_mag;
            traction_motor_set((int)cmd_pwm_mag, dir);
        } else if (pos_mode) {
            const float pos_err = target_pos - pos_rev;
            if (fabsf(pos_err) <= POSITION_DEADBAND_REV) {
                traction_motor_brake();
                traction_pid_reset(&pid_pos);
                cmd_raw = 0.0f;
                cmd_pwm_signed = 0.0f;
            } else {
                cmd_raw = traction_pid_update(&pid_pos, target_pos, pos_rev, dt);
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
                cmd_pwm_signed = (cmd_raw >= 0.0f) ? cmd_pwm_mag : -cmd_pwm_mag;
                traction_motor_set((int)cmd_pwm_mag, dir);
            }
        } else if (target_abs <= SETPOINT_MIN_RPM) {
            traction_motor_brake();
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
            cmd_pwm_signed = (dir == TRACTION_DIR_CW) ? cmd_pwm_mag : -cmd_pwm_mag;
            traction_motor_set((int)cmd_pwm_mag, dir);
        }

        if (send_rpm_telem) {
            traction_comm_send_line("T,%.2f,%.2f,%.2f,%.2f",
                                    (double)target_rpm, (double)rpm,
                                    (double)cmd_pwm_signed, (double)cmd_raw);
        }
        if (send_pos_telem) {
            traction_comm_send_line("TP,%.4f,%.4f,%.2f,%.2f",
                                    (double)target_pos, (double)pos_rev,
                                    (double)cmd_pwm_signed, (double)cmd_raw);
        }

        last_count = c;
        last_t_us = now_us;
    }
}
