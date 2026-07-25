#include <stdio.h>
#include <string.h>

#include "driver/gpio.h"
#include "ds_comm.h"
#include "ds_proc.h"
#include "ds_sensor.h"
#include "ds_storage.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define DISTANCE_TRIGGER_PIN GPIO_NUM_3
#define DISTANCE_ECHO_PIN GPIO_NUM_10

#define DISTANCE_DEFAULT_NAME "distance-sensor-esp"
#define DISTANCE_DEFAULT_SAMPLE_PERIOD_MS 100U
#define DISTANCE_DEFAULT_MAX_MM 4000U
#define DISTANCE_DEFAULT_FILTER_WINDOW 3U
#define DISTANCE_SAMPLE_PERIOD_MIN_MS 60U
#define DISTANCE_SAMPLE_PERIOD_MAX_MS 2000U

static const char *TAG = "distance_main";

typedef struct {
    ds_sensor_handle_t sensor;
    portMUX_TYPE mux;
    ds_storage_cfg_t cfg;
    ds_proc_state_t proc;
    ds_proc_result_t result;
    uint8_t applied_filter_window;
    uint32_t applied_max_distance_mm;
    bool config_loaded;
} app_state_t;

static app_state_t s_app = {
    .mux = portMUX_INITIALIZER_UNLOCKED,
};

static void apply_default_cfg(ds_storage_cfg_t *cfg)
{
    if (!cfg) {
        return;
    }
    memset(cfg, 0, sizeof(*cfg));
    cfg->version = DS_STORAGE_CFG_VERSION;
    cfg->sample_period_ms = DISTANCE_DEFAULT_SAMPLE_PERIOD_MS;
    cfg->max_distance_mm = DISTANCE_DEFAULT_MAX_MM;
    cfg->filter_window = DISTANCE_DEFAULT_FILTER_WINDOW;
    snprintf(cfg->sensor_name, sizeof(cfg->sensor_name), DISTANCE_DEFAULT_NAME);
}

static bool cfg_is_valid(const ds_storage_cfg_t *cfg)
{
    return cfg &&
        cfg->sample_period_ms >= DISTANCE_SAMPLE_PERIOD_MIN_MS &&
        cfg->sample_period_ms <= DISTANCE_SAMPLE_PERIOD_MAX_MS &&
        cfg->max_distance_mm >= DS_SENSOR_MIN_DISTANCE_MM &&
        cfg->max_distance_mm <= DS_SENSOR_MAX_DISTANCE_MM &&
        ds_proc_filter_window_is_valid(cfg->filter_window) &&
        cfg->sensor_name[0] != '\0';
}

static void sanitize_name(char *name, size_t len)
{
    if (!name || len == 0U) {
        return;
    }
    name[len - 1U] = '\0';
    for (size_t i = 0U; i < len && name[i] != '\0'; ++i) {
        if (name[i] == ',' || name[i] == '\r' || name[i] == '\n') {
            name[i] = '-';
        }
    }
}

static bool comm_get_sensor_state(void *ctx, ds_comm_sensor_state_t *out_state)
{
    (void)ctx;
    if (!out_state) {
        return false;
    }

    portENTER_CRITICAL(&s_app.mux);
    out_state->filtered_distance_mm = s_app.result.filtered_distance_mm;
    out_state->raw_distance_mm = s_app.result.raw_distance_mm;
    out_state->echo_time_us = s_app.result.echo_time_us;
    out_state->health_flags = s_app.result.health_flags;
    out_state->sample_timestamp_ms = s_app.result.timestamp_ms;
    if (s_app.config_loaded) {
        out_state->health_flags |= DS_HEALTH_CONFIG_LOADED;
    }
    portEXIT_CRITICAL(&s_app.mux);

    out_state->valid = (out_state->health_flags & DS_HEALTH_VALID) != 0U;
    if (!out_state->valid) {
        out_state->filtered_distance_mm = -1;
    }
    return true;
}

static bool comm_get_cfg_state(void *ctx, ds_comm_cfg_state_t *out_state)
{
    (void)ctx;
    if (!out_state) {
        return false;
    }

    portENTER_CRITICAL(&s_app.mux);
    out_state->sample_period_ms = s_app.cfg.sample_period_ms;
    out_state->max_distance_mm = s_app.cfg.max_distance_mm;
    out_state->filter_window = s_app.cfg.filter_window;
    memcpy(out_state->sensor_name, s_app.cfg.sensor_name, sizeof(out_state->sensor_name));
    portEXIT_CRITICAL(&s_app.mux);
    out_state->sensor_name[sizeof(out_state->sensor_name) - 1U] = '\0';
    return true;
}

static bool comm_set_cfg_state(void *ctx, const ds_comm_cfg_state_t *state)
{
    (void)ctx;
    if (!state) {
        return false;
    }

    ds_storage_cfg_t next = {0};
    next.version = DS_STORAGE_CFG_VERSION;
    next.sample_period_ms = state->sample_period_ms;
    next.max_distance_mm = state->max_distance_mm;
    next.filter_window = state->filter_window;
    memcpy(next.sensor_name, state->sensor_name, sizeof(next.sensor_name));
    next.sensor_name[sizeof(next.sensor_name) - 1U] = '\0';
    sanitize_name(next.sensor_name, sizeof(next.sensor_name));
    if (!cfg_is_valid(&next)) {
        return false;
    }

    if (ds_sensor_set_max_distance(s_app.sensor, next.max_distance_mm) != ESP_OK) {
        return false;
    }
    portENTER_CRITICAL(&s_app.mux);
    s_app.cfg = next;
    portEXIT_CRITICAL(&s_app.mux);
    return true;
}

static bool comm_save_cfg(void *ctx)
{
    (void)ctx;
    ds_storage_cfg_t snapshot = {0};
    portENTER_CRITICAL(&s_app.mux);
    snapshot = s_app.cfg;
    portEXIT_CRITICAL(&s_app.mux);

    const bool saved = ds_storage_save_cfg(&snapshot) == ESP_OK;
    if (saved) {
        portENTER_CRITICAL(&s_app.mux);
        s_app.config_loaded = true;
        portEXIT_CRITICAL(&s_app.mux);
    }
    return saved;
}

static bool comm_reset_cfg(void *ctx)
{
    (void)ctx;
    ds_storage_cfg_t defaults = {0};
    apply_default_cfg(&defaults);
    if (ds_sensor_set_max_distance(s_app.sensor, defaults.max_distance_mm) != ESP_OK) {
        return false;
    }

    portENTER_CRITICAL(&s_app.mux);
    s_app.cfg = defaults;
    s_app.config_loaded = false;
    portEXIT_CRITICAL(&s_app.mux);
    return true;
}

static bool comm_run_selftest(void *ctx, ds_comm_selftest_state_t *out_state)
{
    (void)ctx;
    if (!out_state) {
        return false;
    }

    ds_storage_cfg_t cfg_snapshot = {0};
    portENTER_CRITICAL(&s_app.mux);
    cfg_snapshot = s_app.cfg;
    portEXIT_CRITICAL(&s_app.mux);
    (void)ds_sensor_set_max_distance(s_app.sensor, cfg_snapshot.max_distance_mm);

    ds_sensor_sample_t sample = {0};
    const esp_err_t err = ds_sensor_measure(s_app.sensor, &sample);
    if (err != ESP_OK) {
        sample.raw_distance_mm = -1;
        sample.health_flags = DS_HEALTH_ECHO_STUCK;
    }
    if (cfg_snapshot.filter_window > 1U) {
        sample.health_flags |= DS_HEALTH_FILTER_ACTIVE;
    }
    portENTER_CRITICAL(&s_app.mux);
    if (s_app.config_loaded) {
        sample.health_flags |= DS_HEALTH_CONFIG_LOADED;
    }
    portEXIT_CRITICAL(&s_app.mux);

    out_state->health_flags = sample.health_flags;
    out_state->ok = (sample.health_flags & DS_HEALTH_VALID) != 0U;
    out_state->distance_mm = out_state->ok ? sample.raw_distance_mm : -1;
    return true;
}

static void distance_sampling_task(void *arg)
{
    (void)arg;
    while (true) {
        const int64_t cycle_started_us = esp_timer_get_time();
        ds_storage_cfg_t cfg_snapshot = {0};
        portENTER_CRITICAL(&s_app.mux);
        cfg_snapshot = s_app.cfg;
        portEXIT_CRITICAL(&s_app.mux);

        if (s_app.applied_max_distance_mm != cfg_snapshot.max_distance_mm) {
            if (ds_sensor_set_max_distance(s_app.sensor, cfg_snapshot.max_distance_mm) == ESP_OK) {
                s_app.applied_max_distance_mm = cfg_snapshot.max_distance_mm;
            }
        }
        if (s_app.applied_filter_window != cfg_snapshot.filter_window) {
            if (ds_proc_set_filter_window(&s_app.proc, cfg_snapshot.filter_window) == ESP_OK) {
                s_app.applied_filter_window = cfg_snapshot.filter_window;
            }
        }

        ds_sensor_sample_t sample = {0};
        esp_err_t err = ds_sensor_measure(s_app.sensor, &sample);
        if (err != ESP_OK) {
            sample.raw_distance_mm = -1;
            sample.health_flags = DS_HEALTH_ECHO_STUCK;
            sample.timestamp_ms = (uint64_t)(esp_timer_get_time() / 1000LL);
        }

        ds_proc_result_t result = {0};
        if (ds_proc_process(&s_app.proc, &sample, &result) != ESP_OK) {
            memset(&result, 0, sizeof(result));
            result.filtered_distance_mm = -1;
            result.raw_distance_mm = -1;
            result.health_flags = DS_HEALTH_ECHO_STUCK;
            result.timestamp_ms = (uint64_t)(esp_timer_get_time() / 1000LL);
        }

        portENTER_CRITICAL(&s_app.mux);
        s_app.result = result;
        portEXIT_CRITICAL(&s_app.mux);

        const int64_t elapsed_us = esp_timer_get_time() - cycle_started_us;
        const int64_t remaining_us =
            (int64_t)cfg_snapshot.sample_period_ms * 1000LL - elapsed_us;
        if (remaining_us > 0) {
            const uint32_t remaining_ms =
                (uint32_t)((remaining_us + 999LL) / 1000LL);
            TickType_t delay_ticks = pdMS_TO_TICKS(remaining_ms);
            if (delay_ticks < 1U) {
                delay_ticks = 1U;
            }
            vTaskDelay(delay_ticks);
        } else {
            taskYIELD();
        }
    }
}

void app_main(void)
{
    ESP_ERROR_CHECK(ds_storage_init());

    apply_default_cfg(&s_app.cfg);
    ds_storage_cfg_t stored_cfg = {0};
    if (ds_storage_load_cfg(&stored_cfg) == ESP_OK && cfg_is_valid(&stored_cfg)) {
        sanitize_name(stored_cfg.sensor_name, sizeof(stored_cfg.sensor_name));
        s_app.cfg = stored_cfg;
        s_app.config_loaded = true;
    }

    ESP_ERROR_CHECK(ds_proc_init(&s_app.proc, s_app.cfg.filter_window));
    s_app.applied_filter_window = s_app.cfg.filter_window;
    s_app.result.filtered_distance_mm = -1;
    s_app.result.raw_distance_mm = -1;
    s_app.result.health_flags = DS_HEALTH_NO_ECHO;
    if (s_app.cfg.filter_window > 1U) {
        s_app.result.health_flags |= DS_HEALTH_FILTER_ACTIVE;
    }
    s_app.result.timestamp_ms = (uint64_t)(esp_timer_get_time() / 1000LL);

    const ds_sensor_cfg_t sensor_cfg = {
        .trigger_pin = DISTANCE_TRIGGER_PIN,
        .echo_pin = DISTANCE_ECHO_PIN,
        .max_distance_mm = s_app.cfg.max_distance_mm,
    };
    ESP_ERROR_CHECK(ds_sensor_init(&sensor_cfg, &s_app.sensor));
    s_app.applied_max_distance_mm = s_app.cfg.max_distance_mm;

    const ds_comm_cfg_t comm_cfg = {
        .ctx = NULL,
        .link_timeout_ms = DS_COMM_DEFAULT_LINK_TIMEOUT_MS,
        .on_link_timeout = NULL,
        .get_sensor_state = comm_get_sensor_state,
        .get_cfg_state = comm_get_cfg_state,
        .set_cfg_state = comm_set_cfg_state,
        .save_cfg = comm_save_cfg,
        .reset_cfg = comm_reset_cfg,
        .run_selftest = comm_run_selftest,
    };
    ESP_ERROR_CHECK(ds_comm_init(&comm_cfg));

    const BaseType_t comm_task_created =
        xTaskCreate(ds_comm_task, "ds_comm", 4096, NULL, 5, NULL);
    ESP_ERROR_CHECK(comm_task_created == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
    const BaseType_t sample_task_created =
        xTaskCreate(distance_sampling_task, "ds_sample", 4096, NULL, 5, NULL);
    ESP_ERROR_CHECK(sample_task_created == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
    ESP_LOGI(
        TAG,
        "distance_sensor_module ready (id=0x14, trigger=GPIO3, echo=GPIO10)");
}
