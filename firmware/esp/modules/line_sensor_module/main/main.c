#include <stdio.h>
#include <string.h>

#include "esp_err.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "ls_comm.h"
#include "ls_proc.h"
#include "ls_sensor.h"
#include "ls_storage.h"

#define LINE_SENSOR_SAMPLE_PERIOD_MS 20U
#define LINE_SENSOR_DEFAULT_CAL_TIME_MS 3000U
#define LINE_SENSOR_DEFAULT_NAME "line-sensor-esp"
#define LINE_SENSOR_MODULE_TYPE "line_sensor_module"
#define LINE_SENSOR_FIRMWARE_MODULE "line_sensor_module"
#define LINE_SENSOR_FIRMWARE_VERSION "1.0"
#define LINE_SENSOR_EXPECTED_PAGE "line-sensor"
#define LINE_SENSOR_EXPECTED_PAGE_VERSION "1.0"
#define LINE_SENSOR_MODULE_ID 0x12U

static const char *TAG = "line_sensor_main";

typedef struct {
    ls_sensor_handle_t sensor;
    portMUX_TYPE mux;
    ls_storage_cfg_t cfg;
    ls_sensor_calibration_t calibration;
    ls_sensor_calibration_state_t cal_state;
    ls_proc_result_t result;
} app_state_t;

static app_state_t s_app = {
    .mux = portMUX_INITIALIZER_UNLOCKED,
};

static void apply_default_cfg(ls_storage_cfg_t *cfg)
{
    if (!cfg) {
        return;
    }
    memset(cfg, 0, sizeof(*cfg));
    cfg->version = LS_STORAGE_CFG_VERSION;
    cfg->track_type = LS_TRACK_DARK;
    cfg->digital_threshold = 0.45f;
    cfg->detect_threshold = 0.20f;
    cfg->calibration_time_ms = LINE_SENSOR_DEFAULT_CAL_TIME_MS;
    snprintf(cfg->sensor_name, sizeof(cfg->sensor_name), LINE_SENSOR_DEFAULT_NAME);
}

static void copy_calibration_to_store(const ls_sensor_calibration_t *source, ls_storage_cal_t *dest)
{
    memset(dest, 0, sizeof(*dest));
    dest->version = LS_STORAGE_CAL_VERSION;
    dest->count = LS_SENSOR_COUNT;
    for (size_t i = 0; i < LS_SENSOR_COUNT; ++i) {
        dest->min_raw[i] = source->min_raw[i];
        dest->max_raw[i] = source->max_raw[i];
    }
}

static void copy_store_to_calibration(const ls_storage_cal_t *source, ls_sensor_calibration_t *dest)
{
    for (size_t i = 0; i < LS_SENSOR_COUNT; ++i) {
        dest->min_raw[i] = source->min_raw[i];
        dest->max_raw[i] = source->max_raw[i];
    }
}

static bool persist_current_calibration(void)
{
    ls_sensor_calibration_t calibration = {0};
    ls_storage_cal_t stored = {0};

    ls_sensor_get_calibration(s_app.sensor, &calibration);

    portENTER_CRITICAL(&s_app.mux);
    s_app.calibration = calibration;
    portEXIT_CRITICAL(&s_app.mux);

    copy_calibration_to_store(&calibration, &stored);
    return (ls_storage_save_cal(&stored) == ESP_OK);
}

static bool comm_get_sensor_state(void *ctx, ls_comm_sensor_state_t *out_state)
{
    (void)ctx;
    if (!out_state) {
        return false;
    }

    portENTER_CRITICAL(&s_app.mux);
    for (size_t i = 0; i < LS_SENSOR_COUNT; ++i) {
        out_state->raw[i] = s_app.result.raw[i];
        out_state->value[i] = s_app.result.line_value[i];
        out_state->digital[i] = s_app.result.digital[i];
    }
    out_state->position = s_app.result.position;
    out_state->strength = s_app.result.strength;
    out_state->line_detected = s_app.result.line_detected;
    out_state->calibrating = s_app.cal_state.active;
    out_state->calibration_remaining_ms = s_app.cal_state.remaining_ms;
    portEXIT_CRITICAL(&s_app.mux);
    return true;
}

static bool comm_get_cfg_state(void *ctx, ls_comm_cfg_state_t *out_state)
{
    (void)ctx;
    if (!out_state) {
        return false;
    }

    portENTER_CRITICAL(&s_app.mux);
    out_state->track_type = s_app.cfg.track_type;
    out_state->digital_threshold = s_app.cfg.digital_threshold;
    out_state->detect_threshold = s_app.cfg.detect_threshold;
    out_state->calibration_time_ms = s_app.cfg.calibration_time_ms;
    memcpy(out_state->sensor_name, s_app.cfg.sensor_name, sizeof(out_state->sensor_name));
    out_state->sensor_name[sizeof(out_state->sensor_name) - 1U] = '\0';
    portEXIT_CRITICAL(&s_app.mux);
    return true;
}

static bool comm_set_cfg_state(void *ctx, const ls_comm_cfg_state_t *state)
{
    (void)ctx;
    if (!state) {
        return false;
    }

    portENTER_CRITICAL(&s_app.mux);
    s_app.cfg.track_type = (state->track_type == LS_TRACK_LIGHT) ? LS_TRACK_LIGHT : LS_TRACK_DARK;
    s_app.cfg.digital_threshold = state->digital_threshold;
    s_app.cfg.detect_threshold = state->detect_threshold;
    s_app.cfg.calibration_time_ms = state->calibration_time_ms;
    memcpy(s_app.cfg.sensor_name, state->sensor_name, sizeof(s_app.cfg.sensor_name));
    s_app.cfg.sensor_name[sizeof(s_app.cfg.sensor_name) - 1U] = '\0';
    if (s_app.cfg.digital_threshold < 0.05f) {
        s_app.cfg.digital_threshold = 0.05f;
    } else if (s_app.cfg.digital_threshold > 0.95f) {
        s_app.cfg.digital_threshold = 0.95f;
    }
    if (s_app.cfg.detect_threshold < 0.05f) {
        s_app.cfg.detect_threshold = 0.05f;
    } else if (s_app.cfg.detect_threshold > 0.95f) {
        s_app.cfg.detect_threshold = 0.95f;
    }
    if (s_app.cfg.calibration_time_ms < 100U) {
        s_app.cfg.calibration_time_ms = 100U;
    }
    if (s_app.cfg.sensor_name[0] == '\0') {
        snprintf(s_app.cfg.sensor_name, sizeof(s_app.cfg.sensor_name), LINE_SENSOR_DEFAULT_NAME);
    }
    portEXIT_CRITICAL(&s_app.mux);
    return true;
}

static bool comm_save_cfg(void *ctx)
{
    (void)ctx;
    ls_storage_cfg_t cfg = {0};
    portENTER_CRITICAL(&s_app.mux);
    cfg = s_app.cfg;
    portEXIT_CRITICAL(&s_app.mux);
    return (ls_storage_save_cfg(&cfg) == ESP_OK);
}

static bool comm_get_cal_state(void *ctx, ls_comm_cal_state_t *out_state)
{
    (void)ctx;
    if (!out_state) {
        return false;
    }

    portENTER_CRITICAL(&s_app.mux);
    for (size_t i = 0; i < LS_SENSOR_COUNT; ++i) {
        out_state->min_raw[i] = s_app.calibration.min_raw[i];
        out_state->max_raw[i] = s_app.calibration.max_raw[i];
    }
    portEXIT_CRITICAL(&s_app.mux);
    return true;
}

static bool comm_set_cal_state(void *ctx, const ls_comm_cal_state_t *state)
{
    (void)ctx;
    if (!state) {
        return false;
    }

    ls_sensor_calibration_t calibration = {0};
    for (size_t i = 0; i < LS_SENSOR_COUNT; ++i) {
        calibration.min_raw[i] = state->min_raw[i];
        calibration.max_raw[i] = state->max_raw[i];
    }
    ls_sensor_set_calibration(s_app.sensor, &calibration);
    ls_sensor_get_calibration(s_app.sensor, &calibration);

    portENTER_CRITICAL(&s_app.mux);
    s_app.calibration = calibration;
    portEXIT_CRITICAL(&s_app.mux);
    return true;
}

static bool comm_get_info_state(void *ctx, ls_comm_info_state_t *out_state)
{
    (void)ctx;
    if (!out_state) {
        return false;
    }

    memset(out_state, 0, sizeof(*out_state));
    portENTER_CRITICAL(&s_app.mux);
    memcpy(out_state->name, s_app.cfg.sensor_name, sizeof(out_state->name));
    portEXIT_CRITICAL(&s_app.mux);
    out_state->name[sizeof(out_state->name) - 1U] = '\0';
    snprintf(out_state->module_type, sizeof(out_state->module_type), LINE_SENSOR_MODULE_TYPE);
    snprintf(out_state->firmware_module, sizeof(out_state->firmware_module), LINE_SENSOR_FIRMWARE_MODULE);
    snprintf(out_state->firmware_version, sizeof(out_state->firmware_version), LINE_SENSOR_FIRMWARE_VERSION);
    snprintf(out_state->expected_page, sizeof(out_state->expected_page), LINE_SENSOR_EXPECTED_PAGE);
    snprintf(out_state->expected_page_version, sizeof(out_state->expected_page_version), LINE_SENSOR_EXPECTED_PAGE_VERSION);
    out_state->module_id = LINE_SENSOR_MODULE_ID;
    return true;
}

static bool comm_save_cal(void *ctx)
{
    (void)ctx;
    return persist_current_calibration();
}

static void comm_start_calibration(void *ctx)
{
    (void)ctx;
    uint32_t duration_ms = LINE_SENSOR_DEFAULT_CAL_TIME_MS;
    portENTER_CRITICAL(&s_app.mux);
    duration_ms = s_app.cfg.calibration_time_ms;
    portEXIT_CRITICAL(&s_app.mux);
    ls_sensor_start_calibration(s_app.sensor, duration_ms);
}

static void comm_stop_calibration(void *ctx)
{
    (void)ctx;
    ls_sensor_stop_calibration(s_app.sensor, true);
    if (!persist_current_calibration()) {
        ESP_LOGW(TAG, "calibration save failed after stop");
    }
}

static void line_sensor_task(void *arg)
{
    (void)arg;

    while (true) {
        uint16_t raw[LS_SENSOR_COUNT] = {0};
        if (ls_sensor_sample(s_app.sensor, raw) == ESP_OK) {
            ls_sensor_calibration_t calibration = {0};
            ls_sensor_calibration_state_t cal_state = {0};
            ls_storage_cfg_t cfg_snapshot = {0};
            ls_proc_cfg_t proc_cfg = {0};
            ls_proc_result_t result = {0};

            ls_sensor_get_calibration(s_app.sensor, &calibration);
            ls_sensor_get_calibration_state(s_app.sensor, &cal_state);

            portENTER_CRITICAL(&s_app.mux);
            cfg_snapshot = s_app.cfg;
            portEXIT_CRITICAL(&s_app.mux);

            proc_cfg.track_type = (cfg_snapshot.track_type == LS_TRACK_LIGHT) ? LS_TRACK_LIGHT : LS_TRACK_DARK;
            proc_cfg.digital_threshold = cfg_snapshot.digital_threshold;
            proc_cfg.detect_threshold = cfg_snapshot.detect_threshold;
            ls_proc_process(raw, &calibration, &proc_cfg, &result);

            portENTER_CRITICAL(&s_app.mux);
            s_app.calibration = calibration;
            s_app.cal_state = cal_state;
            s_app.result = result;
            portEXIT_CRITICAL(&s_app.mux);

            if (ls_sensor_consume_calibration_done(s_app.sensor)) {
                if (persist_current_calibration()) {
                    ESP_LOGI(TAG, "calibration saved");
                } else {
                    ESP_LOGW(TAG, "calibration save failed");
                }
            }
        }

        vTaskDelay(pdMS_TO_TICKS(LINE_SENSOR_SAMPLE_PERIOD_MS));
    }
}

void app_main(void)
{
    ESP_ERROR_CHECK(ls_storage_init());

    apply_default_cfg(&s_app.cfg);
    ls_sensor_get_default_calibration(&s_app.calibration);

    ls_storage_cfg_t stored_cfg = {0};
    if (ls_storage_load_cfg(&stored_cfg) == ESP_OK) {
        s_app.cfg = stored_cfg;
    }

    ls_storage_cal_t stored_cal = {0};
    if (ls_storage_load_cal(&stored_cal) == ESP_OK) {
        copy_store_to_calibration(&stored_cal, &s_app.calibration);
    }

    const ls_sensor_cfg_t sensor_cfg = {
        .gpio_pins = {GPIO_NUM_4, GPIO_NUM_3, GPIO_NUM_2, GPIO_NUM_1, GPIO_NUM_0},
        .default_calibration_time_ms = s_app.cfg.calibration_time_ms,
    };

    ESP_ERROR_CHECK(ls_sensor_init(&sensor_cfg, &s_app.sensor));
    ls_sensor_set_calibration(s_app.sensor, &s_app.calibration);

    const ls_comm_cfg_t comm_cfg = {
        .ctx = NULL,
        .link_timeout_ms = LS_COMM_DEFAULT_LINK_TIMEOUT_MS,
        .get_sensor_state = comm_get_sensor_state,
        .get_cfg_state = comm_get_cfg_state,
        .set_cfg_state = comm_set_cfg_state,
        .save_cfg = comm_save_cfg,
        .get_cal_state = comm_get_cal_state,
        .set_cal_state = comm_set_cal_state,
        .get_info_state = comm_get_info_state,
        .save_cal = comm_save_cal,
        .start_calibration = comm_start_calibration,
        .stop_calibration = comm_stop_calibration,
    };

    ESP_ERROR_CHECK(ls_comm_init(&comm_cfg));

    xTaskCreate(ls_comm_task, "ls_comm", 4096, NULL, 5, NULL);
    xTaskCreate(line_sensor_task, "ls_task", 4096, NULL, 5, NULL);

    ESP_LOGI(TAG, "line_sensor_module line sensor firmware ready");
}
