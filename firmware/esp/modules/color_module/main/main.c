#include <limits.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "color_comm.h"
#include "color_proc.h"
#include "color_sensor.h"
#include "color_storage.h"

#define COLOR_MODULE_DEFAULT_SAMPLE_MS       80U
#define COLOR_MODULE_DEFAULT_GAIN            4U
#define COLOR_MODULE_DEFAULT_INTEGRATION_MS  50U
#define COLOR_MODULE_DEFAULT_TARGET_CLEAR    18000U
#define COLOR_MODULE_DEFAULT_PATCH_SAMPLES   12U
#define COLOR_MODULE_DEFAULT_NAME            "color-sensor-esp"
#define COLOR_MODULE_TYPE                    "color_module"
#define COLOR_MODULE_FIRMWARE_MODULE         "color_module"
#define COLOR_MODULE_ID_VALUE                0x13U

#define COLOR_MODULE_I2C_PORT                I2C_NUM_0
#define COLOR_MODULE_SDA_PIN                 GPIO_NUM_8
#define COLOR_MODULE_SCL_PIN                 GPIO_NUM_9
#define COLOR_MODULE_LED_PIN                 GPIO_NUM_10
#define COLOR_MODULE_I2C_ADDRESS             COLOR_SENSOR_I2C_ADDRESS

#define COLOR_HEALTH_SENSOR_OK      (1U << 0)
#define COLOR_HEALTH_SATURATED      (1U << 1)
#define COLOR_HEALTH_DARK_VALID     (1U << 2)
#define COLOR_HEALTH_WHITE_VALID    (1U << 3)
#define COLOR_HEALTH_CAL_ACTIVE     (1U << 4)
#define COLOR_HEALTH_SELFTEST_OK    (1U << 5)
#define COLOR_HEALTH_AUTO_EXPOSURE  (1U << 6)
#define COLOR_HEALTH_SENSOR_PRESENT (1U << 7)

static const char *TAG = "color_main";

typedef struct {
    uint16_t gain;
    uint16_t integration_ms;
} exposure_step_t;

static const exposure_step_t s_auto_exposure_steps[] = {
    {1U, 24U},
    {1U, 50U},
    {4U, 24U},
    {1U, 101U},
    {4U, 50U},
    {16U, 24U},
    {1U, 154U},
    {4U, 101U},
    {16U, 50U},
    {60U, 24U},
    {4U, 154U},
    {16U, 101U},
    {60U, 50U},
    {16U, 154U},
    {60U, 101U},
    {60U, 154U},
    {60U, 240U},
};

typedef struct {
    color_sensor_handle_t sensor;
    portMUX_TYPE mux;
    color_storage_cfg_t cfg;
    color_storage_cal_t calibration;
    color_sensor_sample_t sensor_sample;
    color_sensor_status_t sensor_status;
    color_proc_result_t result;
    color_proc_capture_t capture;
    bool selftest_ok;
    char selftest_message[48];
    int64_t sample_started_us;
    int64_t sample_ready_us;
} app_state_t;

static app_state_t s_app = {
    .mux = portMUX_INITIALIZER_UNLOCKED,
};

static bool mode_is_valid(uint8_t mode)
{
    return color_proc_mode_is_valid(mode);
}

static void apply_default_cfg(color_storage_cfg_t *cfg)
{
    if (!cfg) {
        return;
    }
    memset(cfg, 0, sizeof(*cfg));
    cfg->version = COLOR_STORAGE_CFG_VERSION;
    cfg->led_mode = COLOR_LED_MODE_AUTO;
    cfg->gain_mode = COLOR_GAIN_MODE_AUTO;
    cfg->classifier = COLOR_CLASSIFIER_LAB;
    cfg->palette_mode = COLOR_PALETTE_MODE_8;
    cfg->gain = COLOR_MODULE_DEFAULT_GAIN;
    cfg->integration_ms = COLOR_MODULE_DEFAULT_INTEGRATION_MS;
    cfg->target_clear = COLOR_MODULE_DEFAULT_TARGET_CLEAR;
    cfg->patch_sample_count = COLOR_MODULE_DEFAULT_PATCH_SAMPLES;
    cfg->sample_period_ms = COLOR_MODULE_DEFAULT_SAMPLE_MS;
    cfg->confidence_threshold = 0.30f;
    snprintf(cfg->sensor_name, sizeof(cfg->sensor_name), COLOR_MODULE_DEFAULT_NAME);
}

static void apply_default_cal(color_storage_cal_t *cal)
{
    if (!cal) {
        return;
    }
    memset(cal, 0, sizeof(*cal));
    cal->version = COLOR_STORAGE_CAL_VERSION;
    color_proc_get_default_profile(COLOR_PALETTE_MODE_4, &cal->profile4);
    color_proc_get_default_profile(COLOR_PALETTE_MODE_8, &cal->profile8);
    color_proc_get_default_profile(COLOR_PALETTE_MODE_16, &cal->profile16);
}

static void sanitize_cfg(color_storage_cfg_t *cfg)
{
    if (!cfg) {
        return;
    }
    cfg->version = COLOR_STORAGE_CFG_VERSION;
    if (cfg->led_mode > COLOR_LED_MODE_AUTO) {
        cfg->led_mode = COLOR_LED_MODE_AUTO;
    }
    if (cfg->gain_mode > COLOR_GAIN_MODE_AUTO) {
        cfg->gain_mode = COLOR_GAIN_MODE_AUTO;
    }
    if (cfg->classifier > COLOR_CLASSIFIER_LAB) {
        cfg->classifier = COLOR_CLASSIFIER_LAB;
    }
    if (!mode_is_valid(cfg->palette_mode)) {
        cfg->palette_mode = COLOR_PALETTE_MODE_8;
    }
    if (cfg->sample_period_ms < 20U) {
        cfg->sample_period_ms = 20U;
    } else if (cfg->sample_period_ms > 1000U) {
        cfg->sample_period_ms = 1000U;
    }
    if (cfg->patch_sample_count < 3U) {
        cfg->patch_sample_count = 3U;
    } else if (cfg->patch_sample_count > COLOR_PROC_CAPTURE_MAX_SAMPLES) {
        cfg->patch_sample_count = COLOR_PROC_CAPTURE_MAX_SAMPLES;
    }
    if (cfg->target_clear < 1000U) {
        cfg->target_clear = 1000U;
    } else if (cfg->target_clear > 60000U) {
        cfg->target_clear = 60000U;
    }
    if (cfg->confidence_threshold < 0.05f) {
        cfg->confidence_threshold = 0.05f;
    } else if (cfg->confidence_threshold > 1.0f) {
        cfg->confidence_threshold = 1.0f;
    }
    if (cfg->gain == 0U) {
        cfg->gain = COLOR_MODULE_DEFAULT_GAIN;
    }
    if (cfg->integration_ms < 3U) {
        cfg->integration_ms = COLOR_MODULE_DEFAULT_INTEGRATION_MS;
    }
    cfg->sensor_name[sizeof(cfg->sensor_name) - 1U] = '\0';
    if (cfg->sensor_name[0] == '\0') {
        snprintf(cfg->sensor_name, sizeof(cfg->sensor_name), COLOR_MODULE_DEFAULT_NAME);
    }
}

static color_proc_calibration_profile_t *profile_mut_for_mode(uint8_t mode)
{
    switch (mode) {
        case COLOR_PALETTE_MODE_4:
            return &s_app.calibration.profile4;
        case COLOR_PALETTE_MODE_8:
            return &s_app.calibration.profile8;
        case COLOR_PALETTE_MODE_16:
            return &s_app.calibration.profile16;
        default:
            return NULL;
    }
}

static const color_proc_calibration_profile_t *profile_for_mode(uint8_t mode)
{
    return profile_mut_for_mode(mode);
}

static bool persist_cfg_snapshot(const color_storage_cfg_t *cfg)
{
    return cfg && (color_storage_save_cfg(cfg) == ESP_OK);
}

static bool persist_cal_snapshot(const color_storage_cal_t *cal)
{
    return cal && (color_storage_save_cal(cal) == ESP_OK);
}

static int find_exposure_index(uint16_t gain, uint16_t integration_ms)
{
    int best_index = 0;
    uint32_t best_score = UINT32_MAX;
    for (size_t i = 0; i < (sizeof(s_auto_exposure_steps) / sizeof(s_auto_exposure_steps[0])); ++i) {
        const uint32_t dg = (s_auto_exposure_steps[i].gain > gain)
                                ? (uint32_t)(s_auto_exposure_steps[i].gain - gain)
                                : (uint32_t)(gain - s_auto_exposure_steps[i].gain);
        const uint32_t di = (s_auto_exposure_steps[i].integration_ms > integration_ms)
                                ? (uint32_t)(s_auto_exposure_steps[i].integration_ms - integration_ms)
                                : (uint32_t)(integration_ms - s_auto_exposure_steps[i].integration_ms);
        const uint32_t score = (dg * 1000U) + di;
        if (score < best_score) {
            best_score = score;
            best_index = (int)i;
        }
    }
    return best_index;
}

static int choose_auto_exposure_step(const color_sensor_sample_t *sample, uint16_t target_clear)
{
    if (!sample) {
        return 0;
    }

    const int current_index = find_exposure_index(sample->gain, sample->integration_ms);
    const uint32_t clear_value = sample->c;

    if (sample->saturated || clear_value > (uint32_t)(target_clear + (target_clear / 3U))) {
        if (current_index > 0) {
            return current_index - 1;
        }
        return current_index;
    }
    if (clear_value < (uint32_t)(target_clear - (target_clear / 3U))) {
        const int max_index = (int)((sizeof(s_auto_exposure_steps) / sizeof(s_auto_exposure_steps[0])) - 1U);
        if (current_index < max_index) {
            return current_index + 1;
        }
    }
    return current_index;
}

static uint8_t build_health_flags_locked(void)
{
    uint8_t flags = 0U;
    const color_proc_calibration_profile_t *profile = profile_for_mode(s_app.cfg.palette_mode);

    if (s_app.sensor_status.sensor_ok) {
        flags |= COLOR_HEALTH_SENSOR_OK;
    }
    if (s_app.sensor_status.sensor_id != 0U) {
        flags |= COLOR_HEALTH_SENSOR_PRESENT;
    }
    if (s_app.sensor_sample.saturated) {
        flags |= COLOR_HEALTH_SATURATED;
    }
    if (profile && profile->dark_valid) {
        flags |= COLOR_HEALTH_DARK_VALID;
    }
    if (profile && profile->white_valid) {
        flags |= COLOR_HEALTH_WHITE_VALID;
    }
    if (s_app.capture.active) {
        flags |= COLOR_HEALTH_CAL_ACTIVE;
    }
    if (s_app.selftest_ok) {
        flags |= COLOR_HEALTH_SELFTEST_OK;
    }
    if (s_app.cfg.gain_mode == COLOR_GAIN_MODE_AUTO) {
        flags |= COLOR_HEALTH_AUTO_EXPOSURE;
    }
    return flags;
}

static bool apply_sensor_exposure(uint16_t gain, uint16_t integration_ms)
{
    return (color_sensor_set_exposure(s_app.sensor, gain, integration_ms) == ESP_OK);
}

static bool comm_get_sensor_state(void *ctx, color_comm_sensor_state_t *out_state)
{
    (void)ctx;
    if (!out_state) {
        return false;
    }

    portENTER_CRITICAL(&s_app.mux);
    memset(out_state, 0, sizeof(*out_state));
    out_state->raw_r = s_app.result.raw.r;
    out_state->raw_g = s_app.result.raw.g;
    out_state->raw_b = s_app.result.raw.b;
    out_state->raw_c = s_app.result.raw.c;
    out_state->norm_r_milli = (uint16_t)lroundf(s_app.result.norm_rgb[0] * 1000.0f);
    out_state->norm_g_milli = (uint16_t)lroundf(s_app.result.norm_rgb[1] * 1000.0f);
    out_state->norm_b_milli = (uint16_t)lroundf(s_app.result.norm_rgb[2] * 1000.0f);
    out_state->lab_l_centi = (int16_t)lroundf(s_app.result.lab[0] * 100.0f);
    out_state->lab_a_centi = (int16_t)lroundf(s_app.result.lab[1] * 100.0f);
    out_state->lab_b_centi = (int16_t)lroundf(s_app.result.lab[2] * 100.0f);
    out_state->luma_milli = (uint16_t)lroundf(s_app.result.luma * 1000.0f);
    out_state->detected_slot = (int8_t)s_app.result.detected_slot;
    out_state->confidence_milli = (uint16_t)lroundf(s_app.result.confidence * 1000.0f);
    for (size_t i = 0; i < 3U; ++i) {
        out_state->top_slot[i] = (int8_t)s_app.result.top[i].slot;
        out_state->top_confidence_milli[i] = (uint16_t)lroundf(s_app.result.top[i].confidence * 1000.0f);
    }
    out_state->palette_mode = s_app.cfg.palette_mode;
    out_state->classifier = s_app.cfg.classifier;
    out_state->led_mode = s_app.cfg.led_mode;
    out_state->led_active = s_app.sensor_status.led_on ? 1U : 0U;
    out_state->gain_mode = s_app.cfg.gain_mode;
    out_state->sensor_id = s_app.sensor_status.sensor_id;
    out_state->health_flags = build_health_flags_locked();
    out_state->calibrating = s_app.capture.active;
    out_state->calibration_target_slot = (int8_t)s_app.capture.target_slot;
    out_state->calibration_samples = s_app.capture.sample_count;
    out_state->gain = s_app.sensor_status.gain;
    out_state->integration_ms = s_app.sensor_status.integration_ms;
    out_state->target_clear = s_app.cfg.target_clear;
    out_state->sample_timestamp_ms = (uint32_t)(s_app.sensor_sample.sample_timestamp_us / 1000LL);
    portEXIT_CRITICAL(&s_app.mux);
    return true;
}

static bool comm_get_cfg_state(void *ctx, color_comm_cfg_state_t *out_state)
{
    (void)ctx;
    if (!out_state) {
        return false;
    }

    portENTER_CRITICAL(&s_app.mux);
    memset(out_state, 0, sizeof(*out_state));
    memcpy(out_state->sensor_name, s_app.cfg.sensor_name, sizeof(out_state->sensor_name));
    out_state->sample_period_ms = s_app.cfg.sample_period_ms;
    out_state->gain = s_app.cfg.gain;
    out_state->integration_ms = s_app.cfg.integration_ms;
    out_state->target_clear = s_app.cfg.target_clear;
    out_state->patch_sample_count = s_app.cfg.patch_sample_count;
    out_state->confidence_threshold = s_app.cfg.confidence_threshold;
    out_state->led_mode = s_app.cfg.led_mode;
    out_state->gain_mode = s_app.cfg.gain_mode;
    out_state->classifier = s_app.cfg.classifier;
    out_state->palette_mode = s_app.cfg.palette_mode;
    portEXIT_CRITICAL(&s_app.mux);
    out_state->sensor_name[sizeof(out_state->sensor_name) - 1U] = '\0';
    return true;
}

static bool comm_set_cfg_state(void *ctx, const color_comm_cfg_state_t *state)
{
    (void)ctx;
    if (!state) {
        return false;
    }

    color_storage_cfg_t next_cfg = {0};
    portENTER_CRITICAL(&s_app.mux);
    next_cfg = s_app.cfg;
    portEXIT_CRITICAL(&s_app.mux);

    next_cfg.sample_period_ms = state->sample_period_ms;
    next_cfg.gain = state->gain;
    next_cfg.integration_ms = state->integration_ms;
    next_cfg.target_clear = state->target_clear;
    next_cfg.patch_sample_count = state->patch_sample_count;
    next_cfg.confidence_threshold = state->confidence_threshold;
    next_cfg.led_mode = state->led_mode;
    next_cfg.gain_mode = state->gain_mode;
    next_cfg.classifier = state->classifier;
    next_cfg.palette_mode = state->palette_mode;
    memcpy(next_cfg.sensor_name, state->sensor_name, sizeof(next_cfg.sensor_name));
    sanitize_cfg(&next_cfg);

    (void)apply_sensor_exposure(next_cfg.gain, next_cfg.integration_ms);

    portENTER_CRITICAL(&s_app.mux);
    s_app.cfg = next_cfg;
    portEXIT_CRITICAL(&s_app.mux);
    return true;
}

static bool comm_save_cfg(void *ctx)
{
    (void)ctx;
    color_storage_cfg_t cfg = {0};
    portENTER_CRITICAL(&s_app.mux);
    cfg = s_app.cfg;
    portEXIT_CRITICAL(&s_app.mux);
    return persist_cfg_snapshot(&cfg);
}

static bool comm_reset_cfg(void *ctx)
{
    (void)ctx;
    color_storage_cfg_t cfg = {0};
    apply_default_cfg(&cfg);
    portENTER_CRITICAL(&s_app.mux);
    s_app.cfg = cfg;
    portEXIT_CRITICAL(&s_app.mux);
    (void)apply_sensor_exposure(cfg.gain, cfg.integration_ms);
    return true;
}

static bool comm_get_cal_state(void *ctx, uint8_t palette_mode, color_comm_cal_state_t *out_state)
{
    (void)ctx;
    if (!out_state || !mode_is_valid(palette_mode)) {
        return false;
    }

    const color_proc_calibration_profile_t *profile = NULL;
    portENTER_CRITICAL(&s_app.mux);
    profile = profile_for_mode(palette_mode);
    if (!profile) {
        portEXIT_CRITICAL(&s_app.mux);
        return false;
    }
    memset(out_state, 0, sizeof(*out_state));
    out_state->palette_mode = palette_mode;
    out_state->class_count = profile->class_count;
    out_state->valid_mask = profile->valid_mask;
    out_state->enabled_mask = profile->enabled_mask;
    out_state->dark_valid = profile->dark_valid;
    out_state->white_valid = profile->white_valid;
    out_state->dark_r = profile->dark.r;
    out_state->dark_g = profile->dark.g;
    out_state->dark_b = profile->dark.b;
    out_state->dark_c = profile->dark.c;
    out_state->white_r = profile->white.r;
    out_state->white_g = profile->white.g;
    out_state->white_b = profile->white.b;
    out_state->white_c = profile->white.c;
    out_state->patch_sample_count = s_app.cfg.patch_sample_count;
    portEXIT_CRITICAL(&s_app.mux);
    return true;
}

static bool comm_get_cal_patch_state(void *ctx,
                                     uint8_t palette_mode,
                                     int slot,
                                     color_comm_cal_patch_state_t *out_state)
{
    (void)ctx;
    if (!out_state || !mode_is_valid(palette_mode) || slot < 0 ||
        slot >= (int)color_proc_palette_class_count((color_palette_mode_t)palette_mode)) {
        return false;
    }

    const color_proc_calibration_profile_t *profile = NULL;
    portENTER_CRITICAL(&s_app.mux);
    profile = profile_for_mode(palette_mode);
    if (!profile) {
        portEXIT_CRITICAL(&s_app.mux);
        return false;
    }
    const color_proc_patch_t patch = profile->patches[slot];
    memset(out_state, 0, sizeof(*out_state));
    out_state->palette_mode = palette_mode;
    out_state->slot = (int8_t)slot;
    out_state->enabled = (((profile->enabled_mask >> slot) & 0x1U) != 0U);
    out_state->valid = patch.valid && (((profile->valid_mask >> slot) & 0x1U) != 0U);
    out_state->sample_count = patch.sample_count;
    snprintf(out_state->name,
             sizeof(out_state->name),
             "%s",
             color_proc_palette_name((color_palette_mode_t)palette_mode, slot));
    out_state->norm_r_milli = (uint16_t)lroundf(patch.norm_rgb[0] * 1000.0f);
    out_state->norm_g_milli = (uint16_t)lroundf(patch.norm_rgb[1] * 1000.0f);
    out_state->norm_b_milli = (uint16_t)lroundf(patch.norm_rgb[2] * 1000.0f);
    out_state->lab_l_centi = (int16_t)lroundf(patch.lab[0] * 100.0f);
    out_state->lab_a_centi = (int16_t)lroundf(patch.lab[1] * 100.0f);
    out_state->lab_b_centi = (int16_t)lroundf(patch.lab[2] * 100.0f);
    out_state->luma_milli = (uint16_t)lroundf(patch.luma * 1000.0f);
    portEXIT_CRITICAL(&s_app.mux);
    out_state->name[sizeof(out_state->name) - 1U] = '\0';
    return true;
}

static bool comm_save_cal(void *ctx)
{
    (void)ctx;
    color_storage_cal_t cal = {0};
    portENTER_CRITICAL(&s_app.mux);
    cal = s_app.calibration;
    portEXIT_CRITICAL(&s_app.mux);
    return persist_cal_snapshot(&cal);
}

static bool comm_reset_cal(void *ctx, uint8_t palette_mode)
{
    (void)ctx;
    portENTER_CRITICAL(&s_app.mux);
    if (palette_mode == 0U) {
        apply_default_cal(&s_app.calibration);
    } else {
        color_proc_calibration_profile_t *profile = profile_mut_for_mode(palette_mode);
        if (!profile) {
            portEXIT_CRITICAL(&s_app.mux);
            return false;
        }
        color_proc_get_default_profile((color_palette_mode_t)palette_mode, profile);
    }
    portEXIT_CRITICAL(&s_app.mux);
    return true;
}

static void comm_start_calibration(void *ctx)
{
    (void)ctx;
    portENTER_CRITICAL(&s_app.mux);
    color_proc_capture_reset(&s_app.capture, (color_palette_mode_t)s_app.cfg.palette_mode, COLOR_CAL_TARGET_NONE);
    portEXIT_CRITICAL(&s_app.mux);
}

static void comm_stop_calibration(void *ctx)
{
    (void)ctx;
    portENTER_CRITICAL(&s_app.mux);
    memset(&s_app.capture, 0, sizeof(s_app.capture));
    portEXIT_CRITICAL(&s_app.mux);
}

static bool comm_set_cal_target(void *ctx, int slot)
{
    (void)ctx;
    portENTER_CRITICAL(&s_app.mux);
    color_proc_capture_reset(&s_app.capture, (color_palette_mode_t)s_app.cfg.palette_mode, slot);
    portEXIT_CRITICAL(&s_app.mux);
    return true;
}

static bool comm_commit_cal_target(void *ctx, int slot)
{
    (void)ctx;
    bool ok = false;

    portENTER_CRITICAL(&s_app.mux);
    color_proc_capture_t capture = s_app.capture;
    const uint8_t palette_mode = s_app.cfg.palette_mode;
    portEXIT_CRITICAL(&s_app.mux);

    if (!capture.active || capture.target_slot != slot || capture.sample_count == 0U) {
        return false;
    }

    color_proc_calibration_profile_t *profile = profile_mut_for_mode(palette_mode);
    if (!profile) {
        return false;
    }

    if (slot == COLOR_CAL_TARGET_DARK || slot == COLOR_CAL_TARGET_WHITE) {
        color_proc_raw_ref_t ref = {0};
        ok = color_proc_capture_finalize_reference(&capture, &ref);
        if (ok) {
            portENTER_CRITICAL(&s_app.mux);
            profile = profile_mut_for_mode(palette_mode);
            if (!profile) {
                memset(&s_app.capture, 0, sizeof(s_app.capture));
                portEXIT_CRITICAL(&s_app.mux);
                return false;
            }
            if (slot == COLOR_CAL_TARGET_DARK) {
                profile->dark = ref;
                profile->dark_valid = true;
            } else {
                profile->white = ref;
                profile->white_valid = true;
            }
            memset(&s_app.capture, 0, sizeof(s_app.capture));
            portEXIT_CRITICAL(&s_app.mux);
        }
        return ok;
    }

    if (slot < 0 || slot >= (int)profile->class_count) {
        return false;
    }

    color_proc_patch_t patch = {0};
    ok = color_proc_capture_finalize_patch(&capture, &patch);
    if (!ok) {
        return false;
    }

    portENTER_CRITICAL(&s_app.mux);
    profile = profile_mut_for_mode(palette_mode);
    profile->patches[slot] = patch;
    profile->valid_mask |= (uint16_t)(1U << slot);
    memset(&s_app.capture, 0, sizeof(s_app.capture));
    portEXIT_CRITICAL(&s_app.mux);
    return true;
}

static bool comm_set_cal_reference(void *ctx,
                                   uint8_t palette_mode,
                                   int slot,
                                   uint16_t r,
                                   uint16_t g,
                                   uint16_t b,
                                   uint16_t c)
{
    (void)ctx;
    if (!mode_is_valid(palette_mode) || (slot != COLOR_CAL_TARGET_DARK && slot != COLOR_CAL_TARGET_WHITE)) {
        return false;
    }

    portENTER_CRITICAL(&s_app.mux);
    color_proc_calibration_profile_t *profile = profile_mut_for_mode(palette_mode);
    color_proc_raw_ref_t ref = {.r = r, .g = g, .b = b, .c = c};
    if (slot == COLOR_CAL_TARGET_DARK) {
        profile->dark = ref;
        profile->dark_valid = true;
    } else {
        profile->white = ref;
        profile->white_valid = true;
    }
    portEXIT_CRITICAL(&s_app.mux);
    return true;
}

static bool comm_set_cal_patch_data(void *ctx,
                                    uint8_t palette_mode,
                                    int slot,
                                    uint16_t norm_r_milli,
                                    uint16_t norm_g_milli,
                                    uint16_t norm_b_milli,
                                    uint16_t luma_milli,
                                    int16_t lab_l_centi,
                                    int16_t lab_a_centi,
                                    int16_t lab_b_centi,
                                    uint16_t sample_count)
{
    (void)ctx;
    if (!mode_is_valid(palette_mode) ||
        slot < 0 ||
        slot >= (int)color_proc_palette_class_count((color_palette_mode_t)palette_mode)) {
        return false;
    }

    portENTER_CRITICAL(&s_app.mux);
    color_proc_calibration_profile_t *profile = profile_mut_for_mode(palette_mode);
    profile->patches[slot].valid = true;
    profile->patches[slot].sample_count = sample_count;
    profile->patches[slot].norm_rgb[0] = (float)norm_r_milli / 1000.0f;
    profile->patches[slot].norm_rgb[1] = (float)norm_g_milli / 1000.0f;
    profile->patches[slot].norm_rgb[2] = (float)norm_b_milli / 1000.0f;
    profile->patches[slot].lab[0] = (float)lab_l_centi / 100.0f;
    profile->patches[slot].lab[1] = (float)lab_a_centi / 100.0f;
    profile->patches[slot].lab[2] = (float)lab_b_centi / 100.0f;
    profile->patches[slot].luma = (float)luma_milli / 1000.0f;
    profile->valid_mask |= (uint16_t)(1U << slot);
    portEXIT_CRITICAL(&s_app.mux);
    return true;
}

static bool comm_get_info_state(void *ctx, color_comm_info_state_t *out_state)
{
    (void)ctx;
    if (!out_state) {
        return false;
    }

    memset(out_state, 0, sizeof(*out_state));
    portENTER_CRITICAL(&s_app.mux);
    memcpy(out_state->name, s_app.cfg.sensor_name, sizeof(out_state->name));
    out_state->sensor_id = s_app.sensor_status.sensor_id;
    out_state->health_flags = build_health_flags_locked();
    portEXIT_CRITICAL(&s_app.mux);
    snprintf(out_state->module_type, sizeof(out_state->module_type), COLOR_MODULE_TYPE);
    snprintf(out_state->firmware_module, sizeof(out_state->firmware_module), COLOR_MODULE_FIRMWARE_MODULE);
    out_state->module_id = COLOR_MODULE_ID_VALUE;
    out_state->i2c_address = COLOR_MODULE_I2C_ADDRESS;
    out_state->sda_pin = COLOR_MODULE_SDA_PIN;
    out_state->scl_pin = COLOR_MODULE_SCL_PIN;
    out_state->led_pin = COLOR_MODULE_LED_PIN;
    return true;
}

static bool comm_run_selftest(void *ctx, color_comm_selftest_state_t *out_state)
{
    (void)ctx;
    if (!out_state) {
        return false;
    }

    color_sensor_selftest_result_t result = {0};
    esp_err_t err = color_sensor_run_selftest(s_app.sensor, &result);
    portENTER_CRITICAL(&s_app.mux);
    s_app.selftest_ok = (err == ESP_OK && result.ok);
    snprintf(s_app.selftest_message, sizeof(s_app.selftest_message), "%s", result.message);
    out_state->ok = s_app.selftest_ok;
    out_state->sensor_id = result.sensor_id;
    out_state->health_flags = build_health_flags_locked();
    snprintf(out_state->message, sizeof(out_state->message), "%s", result.message);
    portEXIT_CRITICAL(&s_app.mux);
    return true;
}

static void color_sampling_task(void *arg)
{
    (void)arg;

    while (true) {
        color_storage_cfg_t cfg_snapshot = {0};
        color_proc_calibration_profile_t profile_snapshot = {0};

        portENTER_CRITICAL(&s_app.mux);
        cfg_snapshot = s_app.cfg;
        const color_proc_calibration_profile_t *profile = profile_for_mode(cfg_snapshot.palette_mode);
        if (profile) {
            profile_snapshot = *profile;
        }
        portEXIT_CRITICAL(&s_app.mux);

        const int64_t sample_started_us = esp_timer_get_time();
        color_sensor_sample_t sample = {0};
        color_sensor_status_t sensor_status = {0};
        color_proc_result_t result = {0};

        esp_err_t err = color_sensor_read_sample(s_app.sensor, cfg_snapshot.led_mode, &sample);
        color_sensor_get_status(s_app.sensor, &sensor_status);

        if (err == ESP_OK) {
            const color_proc_cfg_t proc_cfg = {
                .mode = (color_palette_mode_t)cfg_snapshot.palette_mode,
                .classifier = (color_classifier_t)cfg_snapshot.classifier,
                .confidence_threshold = cfg_snapshot.confidence_threshold,
            };
            color_proc_raw_ref_t raw = {
                .r = sample.r,
                .g = sample.g,
                .b = sample.b,
                .c = sample.c,
            };
            color_proc_process(&raw, sample.saturated, &profile_snapshot, &proc_cfg, &result);

            portENTER_CRITICAL(&s_app.mux);
            if (s_app.capture.active && s_app.capture.target_slot != COLOR_CAL_TARGET_NONE) {
                color_proc_capture_add(&s_app.capture, &raw, &result);
            }
            portEXIT_CRITICAL(&s_app.mux);

            if (cfg_snapshot.gain_mode == COLOR_GAIN_MODE_AUTO) {
                const int next_index = choose_auto_exposure_step(&sample, cfg_snapshot.target_clear);
                const exposure_step_t next_step = s_auto_exposure_steps[next_index];
                if (next_step.gain != sample.gain || next_step.integration_ms != sample.integration_ms) {
                    if (apply_sensor_exposure(next_step.gain, next_step.integration_ms)) {
                        sensor_status.gain = next_step.gain;
                        sensor_status.integration_ms = next_step.integration_ms;
                        cfg_snapshot.gain = next_step.gain;
                        cfg_snapshot.integration_ms = next_step.integration_ms;
                    }
                } else {
                    cfg_snapshot.gain = sample.gain;
                    cfg_snapshot.integration_ms = sample.integration_ms;
                }
            }
        } else {
            result.raw.r = sample.r;
            result.raw.g = sample.g;
            result.raw.b = sample.b;
            result.raw.c = sample.c;
        }

        const int64_t sample_ready_us = esp_timer_get_time();
        portENTER_CRITICAL(&s_app.mux);
        s_app.cfg.gain = cfg_snapshot.gain;
        s_app.cfg.integration_ms = cfg_snapshot.integration_ms;
        s_app.sensor_sample = sample;
        s_app.sensor_status = sensor_status;
        s_app.result = result;
        s_app.sample_started_us = sample_started_us;
        s_app.sample_ready_us = sample_ready_us;
        portEXIT_CRITICAL(&s_app.mux);

        vTaskDelay(pdMS_TO_TICKS(cfg_snapshot.sample_period_ms));
    }
}

void app_main(void)
{
    ESP_ERROR_CHECK(color_storage_init());

    apply_default_cfg(&s_app.cfg);
    apply_default_cal(&s_app.calibration);
    snprintf(s_app.selftest_message, sizeof(s_app.selftest_message), "not-run");

    color_storage_cfg_t stored_cfg = {0};
    if (color_storage_load_cfg(&stored_cfg) == ESP_OK) {
        sanitize_cfg(&stored_cfg);
        s_app.cfg = stored_cfg;
    }

    color_storage_cal_t stored_cal = {0};
    if (color_storage_load_cal(&stored_cal) == ESP_OK) {
        s_app.calibration = stored_cal;
    }

    const color_sensor_cfg_t sensor_cfg = {
        .i2c_port = COLOR_MODULE_I2C_PORT,
        .sda_pin = COLOR_MODULE_SDA_PIN,
        .scl_pin = COLOR_MODULE_SCL_PIN,
        .led_pin = COLOR_MODULE_LED_PIN,
        .i2c_address = COLOR_MODULE_I2C_ADDRESS,
        .i2c_freq_hz = COLOR_SENSOR_DEFAULT_I2C_FREQ_HZ,
        .default_integration_ms = s_app.cfg.integration_ms,
        .default_gain = s_app.cfg.gain,
    };

    ESP_ERROR_CHECK(color_sensor_init(&sensor_cfg, &s_app.sensor));
    color_sensor_get_status(s_app.sensor, &s_app.sensor_status);

    const color_comm_cfg_t comm_cfg = {
        .ctx = NULL,
        .link_timeout_ms = COLOR_COMM_DEFAULT_LINK_TIMEOUT_MS,
        .get_sensor_state = comm_get_sensor_state,
        .get_cfg_state = comm_get_cfg_state,
        .set_cfg_state = comm_set_cfg_state,
        .save_cfg = comm_save_cfg,
        .reset_cfg = comm_reset_cfg,
        .get_cal_state = comm_get_cal_state,
        .get_cal_patch_state = comm_get_cal_patch_state,
        .save_cal = comm_save_cal,
        .reset_cal = comm_reset_cal,
        .start_calibration = comm_start_calibration,
        .stop_calibration = comm_stop_calibration,
        .set_cal_target = comm_set_cal_target,
        .commit_cal_target = comm_commit_cal_target,
        .set_cal_reference = comm_set_cal_reference,
        .set_cal_patch_data = comm_set_cal_patch_data,
        .get_info_state = comm_get_info_state,
        .run_selftest = comm_run_selftest,
    };

    ESP_ERROR_CHECK(color_comm_init(&comm_cfg));

    xTaskCreate(color_comm_task, "color_comm", 6144, NULL, 5, NULL);
    xTaskCreate(color_sampling_task, "color_task", 6144, NULL, 5, NULL);

    ESP_LOGI(TAG, "color_module firmware ready");
}
