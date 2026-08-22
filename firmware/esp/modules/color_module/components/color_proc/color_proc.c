#include "color_proc.h"

#include <math.h>
#include <string.h>

static const char *const s_palette_names_5[5] = {
    "black",
    "white",
    "blue",
    "green",
    "red",
};

static const uint32_t s_palette_rgb_5[5] = {
    0x050505U,
    0xFFFFFFU,
    0x006DFFU,
    0x30FF00U,
    0xFF0000U,
};

static const char *const s_palette_names_8[8] = {
    "black",
    "white",
    "violet",
    "blue",
    "cyan",
    "green",
    "orange",
    "red",
};

static const uint32_t s_palette_rgb_8[8] = {
    0x050505U,
    0xFFFFFFU,
    0x8300FFU,
    0x006DFFU,
    0x00FFD5U,
    0xA5FF00U,
    0xFF9B00U,
    0xFF0000U,
};

static const char *const s_palette_names_16[16] = {
    "black",
    "white",
    "380nm",
    "405nm",
    "429nm",
    "454nm",
    "478nm",
    "503nm",
    "528nm",
    "552nm",
    "577nm",
    "602nm",
    "626nm",
    "651nm",
    "675nm",
    "700nm",
};

static const uint32_t s_palette_rgb_16[16] = {
    0x050505U,
    0xFFFFFFU,
    0x6100FFU,
    0x8300FFU,
    0x004DFFU,
    0x006DFFU,
    0x00B7FFU,
    0x00FFD5U,
    0x30FF00U,
    0xA5FF00U,
    0xFFFF00U,
    0xFF9B00U,
    0xFF3F00U,
    0xFF0000U,
    0xD00000U,
    0x7A0000U,
};

typedef struct {
    int slot;
    float distance;
} class_distance_t;

#define COLOR_PROC_NEUTRAL_AXIS_TOLERANCE_LAB 8.0f
#define COLOR_PROC_NEUTRAL_AXIS_PENALTY_MAX   0.12f

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

static uint8_t palette_count_from_mode(color_palette_mode_t mode)
{
    switch (mode) {
        case COLOR_PALETTE_MODE_5:
            return 5U;
        case COLOR_PALETTE_MODE_8:
            return 8U;
        case COLOR_PALETTE_MODE_16:
            return 16U;
        default:
            return 0U;
    }
}

bool color_proc_mode_is_valid(uint8_t mode)
{
    return (mode == (uint8_t)COLOR_PALETTE_MODE_5) ||
           (mode == (uint8_t)COLOR_PALETTE_MODE_8) ||
           (mode == (uint8_t)COLOR_PALETTE_MODE_16);
}

uint8_t color_proc_palette_class_count(color_palette_mode_t mode)
{
    return palette_count_from_mode(mode);
}

const char *color_proc_palette_name(color_palette_mode_t mode, int slot)
{
    if (slot < 0) {
        if (slot == COLOR_CAL_TARGET_DARK) {
            return "dark";
        }
        if (slot == COLOR_CAL_TARGET_WHITE) {
            return "white";
        }
        return "";
    }

    switch (mode) {
        case COLOR_PALETTE_MODE_5:
            return (slot < 5) ? s_palette_names_5[slot] : "";
        case COLOR_PALETTE_MODE_8:
            return (slot < 8) ? s_palette_names_8[slot] : "";
        case COLOR_PALETTE_MODE_16:
            return (slot < 16) ? s_palette_names_16[slot] : "";
        default:
            return "";
    }
}

uint32_t color_proc_palette_rgb(color_palette_mode_t mode, int slot)
{
    if (slot < 0) {
        return 0x000000U;
    }

    switch (mode) {
        case COLOR_PALETTE_MODE_5:
            return (slot < 5) ? s_palette_rgb_5[slot] : 0x000000U;
        case COLOR_PALETTE_MODE_8:
            return (slot < 8) ? s_palette_rgb_8[slot] : 0x000000U;
        case COLOR_PALETTE_MODE_16:
            return (slot < 16) ? s_palette_rgb_16[slot] : 0x000000U;
        default:
            return 0x000000U;
    }
}

static void rgb_to_lab(const float rgb[3], float lab[3]);

void color_proc_get_default_cfg(color_proc_cfg_t *out_cfg)
{
    if (!out_cfg) {
        return;
    }
    memset(out_cfg, 0, sizeof(*out_cfg));
    out_cfg->mode = COLOR_PALETTE_MODE_8;
    out_cfg->classifier = COLOR_CLASSIFIER_LAB;
    out_cfg->confidence_threshold = 0.30f;
    out_cfg->black_threshold = 0.12f;
    out_cfg->bright_threshold = 0.88f;
}

void color_proc_get_default_profile(color_palette_mode_t mode, color_proc_calibration_profile_t *out_profile)
{
    if (!out_profile) {
        return;
    }

    memset(out_profile, 0, sizeof(*out_profile));
    out_profile->mode = mode;
    out_profile->class_count = palette_count_from_mode(mode);
    if (out_profile->class_count >= 16U) {
        out_profile->enabled_mask = 0xFFFFU;
    } else if (out_profile->class_count > 0U) {
        out_profile->enabled_mask = (uint16_t)((1UL << out_profile->class_count) - 1UL);
    }

    for (uint8_t slot = 0U; slot < out_profile->class_count; ++slot) {
        const uint32_t rgb = color_proc_palette_rgb(mode, (int)slot);
        color_proc_patch_t *patch = &out_profile->patches[slot];
        const float r = (float)((rgb >> 16U) & 0xFFU) / 255.0f;
        const float g = (float)((rgb >> 8U) & 0xFFU) / 255.0f;
        const float b = (float)(rgb & 0xFFU) / 255.0f;

        patch->valid = true;
        patch->sample_count = 1U;
        patch->norm_rgb[0] = r;
        patch->norm_rgb[1] = g;
        patch->norm_rgb[2] = b;
        patch->luma = clampf((0.2126f * r) + (0.7152f * g) + (0.0722f * b), 0.0f, 1.0f);
        rgb_to_lab(patch->norm_rgb, patch->lab);
        out_profile->valid_mask |= (uint16_t)(1U << slot);
    }
}

static void rgb_to_lab(const float rgb[3], float lab[3])
{
    const float peak = fmaxf(1.0f, fmaxf(rgb[0], fmaxf(rgb[1], rgb[2])));
    const float r = clampf(rgb[0] / peak, 0.0f, 1.0f);
    const float g = clampf(rgb[1] / peak, 0.0f, 1.0f);
    const float b = clampf(rgb[2] / peak, 0.0f, 1.0f);

    const float x = (0.4124564f * r) + (0.3575761f * g) + (0.1804375f * b);
    const float y = (0.2126729f * r) + (0.7151522f * g) + (0.0721750f * b);
    const float z = (0.0193339f * r) + (0.1191920f * g) + (0.9503041f * b);

    const float xr = x / 0.95047f;
    const float yr = y / 1.00000f;
    const float zr = z / 1.08883f;

    const float epsilon = 216.0f / 24389.0f;
    const float kappa = 24389.0f / 27.0f;

    const float fx = (xr > epsilon) ? cbrtf(xr) : ((kappa * xr) + 16.0f) / 116.0f;
    const float fy = (yr > epsilon) ? cbrtf(yr) : ((kappa * yr) + 16.0f) / 116.0f;
    const float fz = (zr > epsilon) ? cbrtf(zr) : ((kappa * zr) + 16.0f) / 116.0f;

    lab[0] = clampf((116.0f * fy) - 16.0f, 0.0f, 100.0f);
    lab[1] = clampf(500.0f * (fx - fy), -128.0f, 127.0f);
    lab[2] = clampf(200.0f * (fy - fz), -128.0f, 127.0f);
}

static float calc_lab_distance(const color_proc_result_t *result, const color_proc_patch_t *patch)
{
    const float chroma = sqrtf((result->lab[1] - patch->lab[1]) * (result->lab[1] - patch->lab[1]) +
                               (result->lab[2] - patch->lab[2]) * (result->lab[2] - patch->lab[2]));
    const float luminance = fabsf(result->lab[0] - patch->lab[0]);
    return (0.70f * (chroma / 180.0f)) + (0.30f * (luminance / 100.0f));
}

static float calc_norm_rgb_distance(const color_proc_result_t *result, const color_proc_patch_t *patch)
{
    const float rs = result->norm_rgb[0] + result->norm_rgb[1] + result->norm_rgb[2];
    const float ps = patch->norm_rgb[0] + patch->norm_rgb[1] + patch->norm_rgb[2];
    const float rr = (rs > 1e-6f) ? (result->norm_rgb[0] / rs) : 0.0f;
    const float rg = (rs > 1e-6f) ? (result->norm_rgb[1] / rs) : 0.0f;
    const float rb = (rs > 1e-6f) ? (result->norm_rgb[2] / rs) : 0.0f;
    const float pr = (ps > 1e-6f) ? (patch->norm_rgb[0] / ps) : 0.0f;
    const float pg = (ps > 1e-6f) ? (patch->norm_rgb[1] / ps) : 0.0f;
    const float pb = (ps > 1e-6f) ? (patch->norm_rgb[2] / ps) : 0.0f;

    const float chroma = sqrtf((rr - pr) * (rr - pr) +
                               (rg - pg) * (rg - pg) +
                               (rb - pb) * (rb - pb));
    const float luminance = fabsf(result->luma - patch->luma);
    return (0.75f * (chroma / 1.7321f)) + (0.25f * luminance);
}

static bool classify_neutral_axis(const color_proc_result_t *result,
                                  const color_proc_calibration_profile_t *profile,
                                  float *out_position,
                                  float *out_distance)
{
    const color_proc_patch_t *black = &profile->patches[0];
    const color_proc_patch_t *bright = &profile->patches[1];
    if (!black->valid || !bright->valid ||
        ((profile->valid_mask & 0x0003U) != 0x0003U)) {
        return false;
    }

    const float l_span = bright->lab[0] - black->lab[0];
    if (fabsf(l_span) < 1.0f) {
        return false;
    }
    const float position = clampf((result->lab[0] - black->lab[0]) / l_span, 0.0f, 1.0f);
    const float neutral_a = black->lab[1] + position * (bright->lab[1] - black->lab[1]);
    const float neutral_b = black->lab[2] + position * (bright->lab[2] - black->lab[2]);
    const float da = result->lab[1] - neutral_a;
    const float db = result->lab[2] - neutral_b;
    const float distance = sqrtf(da * da + db * db);

    if (out_position) {
        *out_position = position;
    }
    if (out_distance) {
        *out_distance = distance;
    }
    return distance <= COLOR_PROC_NEUTRAL_AXIS_TOLERANCE_LAB;
}

static void sort_distances(class_distance_t *items, size_t count)
{
    if (!items) {
        return;
    }
    for (size_t i = 1; i < count; ++i) {
        class_distance_t key = items[i];
        size_t j = i;
        while (j > 0U && items[j - 1U].distance > key.distance) {
            items[j] = items[j - 1U];
            --j;
        }
        items[j] = key;
    }
}

esp_err_t color_proc_process(const color_proc_raw_ref_t *raw,
                             bool saturated,
                             const color_proc_calibration_profile_t *profile,
                             const color_proc_cfg_t *cfg,
                             color_proc_result_t *out_result)
{
    if (!raw || !profile || !cfg || !out_result) {
        return ESP_ERR_INVALID_ARG;
    }

    memset(out_result, 0, sizeof(*out_result));
    out_result->raw = *raw;
    out_result->saturated = saturated;
    out_result->dark_valid = profile->dark_valid;
    out_result->white_valid = profile->white_valid;
    out_result->detected_slot = -1;
    out_result->best_distance = 1.0f;
    out_result->second_distance = 1.0f;
    for (size_t i = 0; i < COLOR_PROC_TOP_CANDIDATES; ++i) {
        out_result->top[i].slot = -1;
        out_result->top[i].confidence = 0.0f;
    }

    float corrected[3] = {(float)raw->r, (float)raw->g, (float)raw->b};
    float white_range[3] = {
        (float)raw->r,
        (float)raw->g,
        (float)raw->b,
    };
    float clear_corr = (float)raw->c;

    if (profile->dark_valid) {
        corrected[0] = fmaxf(0.0f, (float)raw->r - (float)profile->dark.r);
        corrected[1] = fmaxf(0.0f, (float)raw->g - (float)profile->dark.g);
        corrected[2] = fmaxf(0.0f, (float)raw->b - (float)profile->dark.b);
        clear_corr = fmaxf(1.0f, (float)raw->c - (float)profile->dark.c);
    }

    if (profile->dark_valid && profile->white_valid) {
        white_range[0] = fmaxf(1.0f, (float)profile->white.r - (float)profile->dark.r);
        white_range[1] = fmaxf(1.0f, (float)profile->white.g - (float)profile->dark.g);
        white_range[2] = fmaxf(1.0f, (float)profile->white.b - (float)profile->dark.b);
        const float white_clear = fmaxf(1.0f, (float)profile->white.c - (float)profile->dark.c);
        out_result->luma = clampf(clear_corr / white_clear, 0.0f, 2.0f);
    } else {
        const float white_clear = fmaxf(1.0f, (float)raw->c);
        out_result->luma = clampf(clear_corr / white_clear, 0.0f, 2.0f);
    }

    for (size_t i = 0; i < 3U; ++i) {
        out_result->norm_rgb[i] = clampf(corrected[i] / fmaxf(1.0f, white_range[i]), 0.0f, 32.0f);
    }
    rgb_to_lab(out_result->norm_rgb, out_result->lab);

    const uint8_t class_count = palette_count_from_mode(profile->mode);
    if (!profile->dark_valid || !profile->white_valid || class_count == 0U) {
        return ESP_OK;
    }

    class_distance_t distances[COLOR_PROC_MAX_CLASSES];
    size_t dist_count = 0U;
    float neutral_position = 0.0f;
    float neutral_distance = 0.0f;
    const bool neutral_axis_available = profile->patches[0].valid && profile->patches[1].valid &&
                                        ((profile->valid_mask & 0x0003U) == 0x0003U);
    (void)classify_neutral_axis(out_result, profile, &neutral_position, &neutral_distance);
    (void)neutral_position;
    for (uint8_t slot = 0U; slot < class_count; ++slot) {
        if (((profile->enabled_mask >> slot) & 0x1U) == 0U) {
            continue;
        }
        if (((profile->valid_mask >> slot) & 0x1U) == 0U) {
            continue;
        }

        const color_proc_patch_t *patch = &profile->patches[slot];
        if (!patch->valid) {
            continue;
        }

        float distance = 1.0f;
        if (cfg->classifier == COLOR_CLASSIFIER_LAB) {
            distance = calc_lab_distance(out_result, patch);
        } else {
            distance = calc_norm_rgb_distance(out_result, patch);
        }
        if (neutral_axis_available && slot < 2U) {
            const float axis_ratio = clampf(
                neutral_distance / COLOR_PROC_NEUTRAL_AXIS_TOLERANCE_LAB,
                0.0f,
                1.0f);
            distance += COLOR_PROC_NEUTRAL_AXIS_PENALTY_MAX * axis_ratio;
            if (slot == 1U && out_result->luma <= cfg->black_threshold) {
                distance += COLOR_PROC_NEUTRAL_AXIS_PENALTY_MAX;
            }
            if (slot == 0U && out_result->luma >= cfg->bright_threshold) {
                distance += COLOR_PROC_NEUTRAL_AXIS_PENALTY_MAX;
            }
        }
        distances[dist_count].slot = (int)slot;
        distances[dist_count].distance = distance;
        ++dist_count;
    }

    if (dist_count == 0U) {
        return ESP_OK;
    }

    sort_distances(distances, dist_count);
    out_result->best_distance = distances[0].distance;
    out_result->second_distance = (dist_count > 1U) ? distances[1].distance : distances[0].distance;

    const float conf_threshold = fmaxf(cfg->confidence_threshold, 0.05f);
    const float threshold_score = clampf(1.0f - (out_result->best_distance / conf_threshold), 0.0f, 1.0f);
    out_result->confidence = threshold_score;

    for (size_t i = 0; i < COLOR_PROC_TOP_CANDIDATES && i < dist_count; ++i) {
        const float distance = distances[i].distance;
        const float score = clampf(1.0f - (distance / conf_threshold), 0.0f, 1.0f);
        out_result->top[i].slot = distances[i].slot;
        out_result->top[i].confidence = score;
    }

    out_result->ready = true;
    if (out_result->best_distance <= conf_threshold) {
        out_result->detected_slot = distances[0].slot;
    }
    return ESP_OK;
}

void color_proc_capture_reset(color_proc_capture_t *capture,
                              color_palette_mode_t mode,
                              int target_slot)
{
    if (!capture) {
        return;
    }
    memset(capture, 0, sizeof(*capture));
    capture->active = true;
    capture->mode = mode;
    capture->target_slot = target_slot;
}

bool color_proc_capture_add(color_proc_capture_t *capture,
                            const color_proc_raw_ref_t *raw,
                            const color_proc_result_t *result)
{
    if (!capture || !capture->active || !raw || !result) {
        return false;
    }

    size_t index = capture->sample_count;
    if (index >= COLOR_PROC_CAPTURE_MAX_SAMPLES) {
        memmove(&capture->samples[0],
                &capture->samples[1],
                sizeof(capture->samples[0]) * (COLOR_PROC_CAPTURE_MAX_SAMPLES - 1U));
        index = COLOR_PROC_CAPTURE_MAX_SAMPLES - 1U;
        capture->sample_count = COLOR_PROC_CAPTURE_MAX_SAMPLES - 1U;
    }

    capture->samples[index].raw = *raw;
    capture->samples[index].processed_valid = result->dark_valid && result->white_valid;
    capture->samples[index].luma = result->luma;
    for (size_t i = 0; i < 3U; ++i) {
        capture->samples[index].norm_rgb[i] = result->norm_rgb[i];
        capture->samples[index].lab[i] = result->lab[i];
    }
    capture->sample_count++;
    return true;
}

static void sort_u16(uint16_t *values, size_t count)
{
    if (!values) {
        return;
    }
    for (size_t i = 1; i < count; ++i) {
        uint16_t key = values[i];
        size_t j = i;
        while (j > 0U && values[j - 1U] > key) {
            values[j] = values[j - 1U];
            --j;
        }
        values[j] = key;
    }
}

static void sort_f32(float *values, size_t count)
{
    if (!values) {
        return;
    }
    for (size_t i = 1; i < count; ++i) {
        float key = values[i];
        size_t j = i;
        while (j > 0U && values[j - 1U] > key) {
            values[j] = values[j - 1U];
            --j;
        }
        values[j] = key;
    }
}

static uint16_t trimmed_mean_u16(const uint16_t *values, size_t count)
{
    uint16_t sorted[COLOR_PROC_CAPTURE_MAX_SAMPLES];
    if (!values || count == 0U) {
        return 0U;
    }
    memcpy(sorted, values, sizeof(uint16_t) * count);
    sort_u16(sorted, count);

    size_t trim = count / 5U;
    if ((count - (trim * 2U)) == 0U) {
        trim = 0U;
    }

    uint32_t sum = 0U;
    size_t used = 0U;
    for (size_t i = trim; i < (count - trim); ++i) {
        sum += sorted[i];
        ++used;
    }
    if (used == 0U) {
        return sorted[count / 2U];
    }
    return (uint16_t)(sum / used);
}

static float trimmed_mean_f32(const float *values, size_t count)
{
    float sorted[COLOR_PROC_CAPTURE_MAX_SAMPLES];
    if (!values || count == 0U) {
        return 0.0f;
    }
    memcpy(sorted, values, sizeof(float) * count);
    sort_f32(sorted, count);

    size_t trim = count / 5U;
    if ((count - (trim * 2U)) == 0U) {
        trim = 0U;
    }

    float sum = 0.0f;
    size_t used = 0U;
    for (size_t i = trim; i < (count - trim); ++i) {
        sum += sorted[i];
        ++used;
    }
    if (used == 0U) {
        return sorted[count / 2U];
    }
    return sum / (float)used;
}

bool color_proc_capture_finalize_reference(const color_proc_capture_t *capture,
                                           color_proc_raw_ref_t *out_ref)
{
    if (!capture || !out_ref || capture->sample_count == 0U) {
        return false;
    }

    uint16_t r[COLOR_PROC_CAPTURE_MAX_SAMPLES];
    uint16_t g[COLOR_PROC_CAPTURE_MAX_SAMPLES];
    uint16_t b[COLOR_PROC_CAPTURE_MAX_SAMPLES];
    uint16_t c[COLOR_PROC_CAPTURE_MAX_SAMPLES];
    const size_t count = capture->sample_count;

    for (size_t i = 0; i < count; ++i) {
        r[i] = capture->samples[i].raw.r;
        g[i] = capture->samples[i].raw.g;
        b[i] = capture->samples[i].raw.b;
        c[i] = capture->samples[i].raw.c;
    }

    out_ref->r = trimmed_mean_u16(r, count);
    out_ref->g = trimmed_mean_u16(g, count);
    out_ref->b = trimmed_mean_u16(b, count);
    out_ref->c = trimmed_mean_u16(c, count);
    return true;
}

bool color_proc_capture_finalize_patch(const color_proc_capture_t *capture,
                                       color_proc_patch_t *out_patch)
{
    if (!capture || !out_patch || capture->sample_count == 0U) {
        return false;
    }

    float nr[COLOR_PROC_CAPTURE_MAX_SAMPLES];
    float ng[COLOR_PROC_CAPTURE_MAX_SAMPLES];
    float nb[COLOR_PROC_CAPTURE_MAX_SAMPLES];
    float ll[COLOR_PROC_CAPTURE_MAX_SAMPLES];
    float la[COLOR_PROC_CAPTURE_MAX_SAMPLES];
    float lb[COLOR_PROC_CAPTURE_MAX_SAMPLES];
    float luma[COLOR_PROC_CAPTURE_MAX_SAMPLES];
    size_t count = 0U;

    for (size_t i = 0; i < capture->sample_count; ++i) {
        if (!capture->samples[i].processed_valid) {
            continue;
        }
        nr[count] = capture->samples[i].norm_rgb[0];
        ng[count] = capture->samples[i].norm_rgb[1];
        nb[count] = capture->samples[i].norm_rgb[2];
        ll[count] = capture->samples[i].lab[0];
        la[count] = capture->samples[i].lab[1];
        lb[count] = capture->samples[i].lab[2];
        luma[count] = capture->samples[i].luma;
        ++count;
    }

    if (count == 0U) {
        return false;
    }

    memset(out_patch, 0, sizeof(*out_patch));
    out_patch->valid = true;
    out_patch->sample_count = (uint16_t)count;
    out_patch->norm_rgb[0] = clampf(trimmed_mean_f32(nr, count), 0.0f, 2.0f);
    out_patch->norm_rgb[1] = clampf(trimmed_mean_f32(ng, count), 0.0f, 2.0f);
    out_patch->norm_rgb[2] = clampf(trimmed_mean_f32(nb, count), 0.0f, 2.0f);
    out_patch->lab[0] = clampf(trimmed_mean_f32(ll, count), 0.0f, 100.0f);
    out_patch->lab[1] = clampf(trimmed_mean_f32(la, count), -128.0f, 127.0f);
    out_patch->lab[2] = clampf(trimmed_mean_f32(lb, count), -128.0f, 127.0f);
    out_patch->luma = clampf(trimmed_mean_f32(luma, count), 0.0f, 2.0f);
    return true;
}
