#include "ls_proc.h"

#include <math.h>
#include <string.h>

static float clampf(float value, float min_value, float max_value)
{
    if (value < min_value) {
        return min_value;
    }
    if (value > max_value) {
        return max_value;
    }
    return value;
}

static float bezier_x(float x0, float x1, float x2, float t)
{
    const float omt = 1.0f - t;
    return (omt * omt * x0) + (2.0f * omt * t * x1) + (t * t * x2);
}

void ls_proc_get_default_cfg(ls_proc_cfg_t *out_cfg)
{
    if (!out_cfg) {
        return;
    }
    out_cfg->track_type = LS_TRACK_LIGHT;
    out_cfg->digital_threshold = 0.45f;
    out_cfg->detect_threshold = 0.20f;
}

esp_err_t ls_proc_process(const uint16_t raw[LS_SENSOR_COUNT],
                          const ls_sensor_calibration_t *calibration,
                          const ls_proc_cfg_t *cfg,
                          ls_proc_result_t *out_result)
{
    if (!raw || !calibration || !cfg || !out_result) {
        return ESP_ERR_INVALID_ARG;
    }

    static const float sensor_x[LS_SENSOR_COUNT] = {-1.0f, -0.5f, 0.0f, 0.5f, 1.0f};

    memset(out_result, 0, sizeof(*out_result));

    float peak_value = 0.0f;
    size_t peak_index = 0U;
    float weighted_sum = 0.0f;
    float total_weight = 0.0f;

    for (size_t i = 0; i < LS_SENSOR_COUNT; ++i) {
        out_result->raw[i] = raw[i];
        float reflectance = 0.0f;
        if (calibration->max_raw[i] > calibration->min_raw[i]) {
            reflectance = (float)((int)raw[i] - (int)calibration->min_raw[i]) /
                          (float)((int)calibration->max_raw[i] - (int)calibration->min_raw[i]);
        }
        reflectance = clampf(reflectance, 0.0f, 1.0f);
        out_result->reflectance[i] = reflectance;

        const float line_value = (cfg->track_type == LS_TRACK_DARK) ? (1.0f - reflectance) : reflectance;
        out_result->line_value[i] = clampf(line_value, 0.0f, 1.0f);
        out_result->digital[i] = (out_result->line_value[i] >= cfg->digital_threshold) ? 1U : 0U;

        if (out_result->line_value[i] > peak_value) {
            peak_value = out_result->line_value[i];
            peak_index = i;
        }

        weighted_sum += sensor_x[i] * out_result->line_value[i];
        total_weight += out_result->line_value[i];
    }

    out_result->strength = peak_value;
    out_result->line_detected = (peak_value >= cfg->detect_threshold) ||
                                (total_weight >= (cfg->detect_threshold * 1.5f));

    if (!out_result->line_detected) {
        out_result->position = 0.0f;
        return ESP_OK;
    }

    if (peak_index == 0U || peak_index == (LS_SENSOR_COUNT - 1U)) {
        out_result->position = sensor_x[peak_index];
        return ESP_OK;
    }

    const float y0 = out_result->line_value[peak_index - 1U];
    const float y1 = out_result->line_value[peak_index];
    const float y2 = out_result->line_value[peak_index + 1U];
    const float denom = y0 - (2.0f * y1) + y2;

    if (fabsf(denom) <= 1e-5f) {
        out_result->position = (total_weight > 1e-5f) ? clampf(weighted_sum / total_weight, -1.0f, 1.0f) : 0.0f;
        return ESP_OK;
    }

    const float t = clampf((y0 - y1) / denom, 0.0f, 1.0f);
    out_result->position = clampf(bezier_x(sensor_x[peak_index - 1U],
                                           sensor_x[peak_index],
                                           sensor_x[peak_index + 1U],
                                           t),
                                  -1.0f,
                                  1.0f);
    return ESP_OK;
}
