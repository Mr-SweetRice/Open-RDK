#include "ds_proc.h"

#include <stdbool.h>
#include <string.h>

bool ds_proc_filter_window_is_valid(uint8_t window)
{
    return window == 1U || window == 3U || window == 5U || window == 7U;
}

esp_err_t ds_proc_init(ds_proc_state_t *state, uint8_t filter_window)
{
    if (!state || !ds_proc_filter_window_is_valid(filter_window)) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(state, 0, sizeof(*state));
    state->window = filter_window;
    return ESP_OK;
}

esp_err_t ds_proc_set_filter_window(ds_proc_state_t *state, uint8_t filter_window)
{
    if (!state || !ds_proc_filter_window_is_valid(filter_window)) {
        return ESP_ERR_INVALID_ARG;
    }
    if (state->window != filter_window) {
        memset(state->values, 0, sizeof(state->values));
        state->window = filter_window;
        state->count = 0U;
        state->write_index = 0U;
    }
    return ESP_OK;
}

static int32_t median_of_values(const int32_t *values, uint8_t count)
{
    int32_t sorted[DS_PROC_MAX_FILTER_WINDOW] = {0};
    for (uint8_t i = 0U; i < count; ++i) {
        sorted[i] = values[i];
    }
    for (uint8_t i = 1U; i < count; ++i) {
        const int32_t candidate = sorted[i];
        uint8_t j = i;
        while (j > 0U && sorted[j - 1U] > candidate) {
            sorted[j] = sorted[j - 1U];
            --j;
        }
        sorted[j] = candidate;
    }
    return sorted[count / 2U];
}

esp_err_t ds_proc_process(
    ds_proc_state_t *state,
    const ds_sensor_sample_t *sample,
    ds_proc_result_t *out_result)
{
    if (!state || !sample || !out_result ||
        !ds_proc_filter_window_is_valid(state->window)) {
        return ESP_ERR_INVALID_ARG;
    }

    memset(out_result, 0, sizeof(*out_result));
    out_result->filtered_distance_mm = -1;
    out_result->raw_distance_mm = sample->raw_distance_mm;
    out_result->echo_time_us = sample->echo_time_us;
    out_result->health_flags = sample->health_flags;
    out_result->timestamp_ms = sample->timestamp_ms;

    if (state->window > 1U) {
        out_result->health_flags |= DS_HEALTH_FILTER_ACTIVE;
    }

    if ((sample->health_flags & DS_HEALTH_VALID) == 0U ||
        sample->raw_distance_mm < 0) {
        return ESP_OK;
    }

    state->values[state->write_index] = sample->raw_distance_mm;
    state->write_index = (uint8_t)((state->write_index + 1U) % state->window);
    if (state->count < state->window) {
        ++state->count;
    }
    out_result->filtered_distance_mm = median_of_values(state->values, state->count);
    return ESP_OK;
}
