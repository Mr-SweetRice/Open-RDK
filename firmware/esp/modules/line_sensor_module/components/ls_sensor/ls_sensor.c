#include "ls_sensor.h"

#include <stdlib.h>

#include "esp_adc/adc_oneshot.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"

struct ls_sensor_ctx_t {
    adc_oneshot_unit_handle_t adc_handle;
    adc_unit_t unit_id;
    adc_channel_t channels[LS_SENSOR_COUNT];
    gpio_num_t gpio_pins[LS_SENSOR_COUNT];
    portMUX_TYPE mux;
    ls_sensor_calibration_t calibration;
    bool calibrating;
    bool calibration_done;
    int64_t calibration_started_us;
    uint32_t calibration_duration_ms;
};

static void calibration_set_runtime_defaults(ls_sensor_calibration_t *calibration)
{
    if (!calibration) {
        return;
    }
    for (size_t i = 0; i < LS_SENSOR_COUNT; ++i) {
        calibration->min_raw[i] = 0U;
        calibration->max_raw[i] = 4095U;
    }
}

static void calibration_set_capture_extremes(ls_sensor_calibration_t *calibration)
{
    if (!calibration) {
        return;
    }
    for (size_t i = 0; i < LS_SENSOR_COUNT; ++i) {
        calibration->min_raw[i] = UINT16_MAX;
        calibration->max_raw[i] = 0U;
    }
}

static void calibration_sanitize(ls_sensor_calibration_t *calibration)
{
    if (!calibration) {
        return;
    }
    for (size_t i = 0; i < LS_SENSOR_COUNT; ++i) {
        if (calibration->max_raw[i] <= calibration->min_raw[i]) {
            calibration->min_raw[i] = 0U;
            calibration->max_raw[i] = 4095U;
        }
    }
}

void ls_sensor_get_default_calibration(ls_sensor_calibration_t *out)
{
    calibration_set_runtime_defaults(out);
}

esp_err_t ls_sensor_init(const ls_sensor_cfg_t *cfg, ls_sensor_handle_t *out_handle)
{
    if (!cfg || !out_handle) {
        return ESP_ERR_INVALID_ARG;
    }

    ls_sensor_handle_t handle = calloc(1, sizeof(*handle));
    if (!handle) {
        return ESP_ERR_NO_MEM;
    }

    handle->mux = (portMUX_TYPE)portMUX_INITIALIZER_UNLOCKED;
    handle->unit_id = ADC_UNIT_1;
    calibration_set_runtime_defaults(&handle->calibration);
    handle->calibration_duration_ms = cfg->default_calibration_time_ms;

    adc_oneshot_unit_init_cfg_t unit_cfg = {
        .unit_id = handle->unit_id,
        .ulp_mode = ADC_ULP_MODE_DISABLE,
    };
    esp_err_t err = adc_oneshot_new_unit(&unit_cfg, &handle->adc_handle);
    if (err != ESP_OK) {
        free(handle);
        return err;
    }

    adc_oneshot_chan_cfg_t chan_cfg = {
        .atten = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_12,
    };

    for (size_t i = 0; i < LS_SENSOR_COUNT; ++i) {
        adc_unit_t unit = ADC_UNIT_1;
        adc_channel_t channel = ADC_CHANNEL_0;
        err = adc_oneshot_io_to_channel((int)cfg->gpio_pins[i], &unit, &channel);
        if (err != ESP_OK || unit != ADC_UNIT_1) {
            adc_oneshot_del_unit(handle->adc_handle);
            free(handle);
            return (err == ESP_OK) ? ESP_ERR_INVALID_ARG : err;
        }
        err = adc_oneshot_config_channel(handle->adc_handle, channel, &chan_cfg);
        if (err != ESP_OK) {
            adc_oneshot_del_unit(handle->adc_handle);
            free(handle);
            return err;
        }
        handle->channels[i] = channel;
        handle->gpio_pins[i] = cfg->gpio_pins[i];
    }

    *out_handle = handle;
    return ESP_OK;
}

esp_err_t ls_sensor_sample(ls_sensor_handle_t handle, uint16_t raw_out[LS_SENSOR_COUNT])
{
    if (!handle || !raw_out) {
        return ESP_ERR_INVALID_ARG;
    }

    for (size_t i = 0; i < LS_SENSOR_COUNT; ++i) {
        int raw = 0;
        esp_err_t err = adc_oneshot_read(handle->adc_handle, handle->channels[i], &raw);
        if (err != ESP_OK) {
            return err;
        }
        if (raw < 0) {
            raw = 0;
        } else if (raw > 4095) {
            raw = 4095;
        }
        raw_out[i] = (uint16_t)raw;
    }

    const int64_t now_us = esp_timer_get_time();

    portENTER_CRITICAL(&handle->mux);
    if (handle->calibrating) {
        for (size_t i = 0; i < LS_SENSOR_COUNT; ++i) {
            if (raw_out[i] < handle->calibration.min_raw[i]) {
                handle->calibration.min_raw[i] = raw_out[i];
            }
            if (raw_out[i] > handle->calibration.max_raw[i]) {
                handle->calibration.max_raw[i] = raw_out[i];
            }
        }

        const int64_t elapsed_us = now_us - handle->calibration_started_us;
        if (elapsed_us >= ((int64_t)handle->calibration_duration_ms * 1000LL)) {
            calibration_sanitize(&handle->calibration);
            handle->calibrating = false;
            handle->calibration_done = true;
        }
    }
    portEXIT_CRITICAL(&handle->mux);

    return ESP_OK;
}

void ls_sensor_set_calibration(ls_sensor_handle_t handle, const ls_sensor_calibration_t *calibration)
{
    if (!handle || !calibration) {
        return;
    }

    portENTER_CRITICAL(&handle->mux);
    handle->calibration = *calibration;
    calibration_sanitize(&handle->calibration);
    portEXIT_CRITICAL(&handle->mux);
}

void ls_sensor_get_calibration(ls_sensor_handle_t handle, ls_sensor_calibration_t *out_calibration)
{
    if (!handle || !out_calibration) {
        return;
    }

    portENTER_CRITICAL(&handle->mux);
    *out_calibration = handle->calibration;
    portEXIT_CRITICAL(&handle->mux);
}

void ls_sensor_start_calibration(ls_sensor_handle_t handle, uint32_t duration_ms)
{
    if (!handle) {
        return;
    }

    if (duration_ms == 0U) {
        duration_ms = handle->calibration_duration_ms ? handle->calibration_duration_ms : 3000U;
    }

    portENTER_CRITICAL(&handle->mux);
    calibration_set_capture_extremes(&handle->calibration);
    handle->calibrating = true;
    handle->calibration_done = false;
    handle->calibration_started_us = esp_timer_get_time();
    handle->calibration_duration_ms = duration_ms;
    portEXIT_CRITICAL(&handle->mux);
}

void ls_sensor_stop_calibration(ls_sensor_handle_t handle, bool keep_data)
{
    if (!handle) {
        return;
    }

    portENTER_CRITICAL(&handle->mux);
    if (!keep_data) {
        calibration_set_runtime_defaults(&handle->calibration);
    } else {
        calibration_sanitize(&handle->calibration);
        handle->calibration_done = true;
    }
    handle->calibrating = false;
    portEXIT_CRITICAL(&handle->mux);
}

void ls_sensor_get_calibration_state(ls_sensor_handle_t handle, ls_sensor_calibration_state_t *out_state)
{
    if (!handle || !out_state) {
        return;
    }

    portENTER_CRITICAL(&handle->mux);
    out_state->active = handle->calibrating;
    out_state->duration_ms = handle->calibration_duration_ms;
    if (!handle->calibrating) {
        out_state->remaining_ms = 0U;
    } else {
        const int64_t elapsed_ms = (esp_timer_get_time() - handle->calibration_started_us) / 1000LL;
        out_state->remaining_ms = (elapsed_ms >= handle->calibration_duration_ms)
                                      ? 0U
                                      : (handle->calibration_duration_ms - (uint32_t)elapsed_ms);
    }
    portEXIT_CRITICAL(&handle->mux);
}

bool ls_sensor_consume_calibration_done(ls_sensor_handle_t handle)
{
    if (!handle) {
        return false;
    }

    bool done = false;
    portENTER_CRITICAL(&handle->mux);
    done = handle->calibration_done;
    handle->calibration_done = false;
    portEXIT_CRITICAL(&handle->mux);
    return done;
}
