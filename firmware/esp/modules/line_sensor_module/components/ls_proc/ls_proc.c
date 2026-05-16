#include "ls_proc.h"

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

void ls_proc_get_default_cfg(ls_proc_cfg_t *out_cfg)
{
    if (!out_cfg) {
        return;
    }
    out_cfg->track_type = LS_TRACK_DARK;
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

    out_result->position = (total_weight > 1e-5f) ? clampf(weighted_sum / total_weight, -1.0f, 1.0f) : 0.0f;
    return ESP_OK;
}
