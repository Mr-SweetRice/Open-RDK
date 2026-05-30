#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define COLOR_PROC_MAX_CLASSES 16U
#define COLOR_PROC_TOP_CANDIDATES 3U
#define COLOR_PROC_CAPTURE_MAX_SAMPLES 48U

typedef enum {
    COLOR_PALETTE_MODE_5 = 5,
    COLOR_PALETTE_MODE_8 = 8,
    COLOR_PALETTE_MODE_16 = 16,
} color_palette_mode_t;

typedef enum {
    COLOR_LED_MODE_OFF = 0,
    COLOR_LED_MODE_ON = 1,
    COLOR_LED_MODE_AUTO = 2,
} color_led_mode_t;

typedef enum {
    COLOR_GAIN_MODE_MANUAL = 0,
    COLOR_GAIN_MODE_AUTO = 1,
} color_gain_mode_t;

typedef enum {
    COLOR_CLASSIFIER_NORM_RGB = 0,
    COLOR_CLASSIFIER_LAB = 1,
} color_classifier_t;

enum {
    COLOR_CAL_TARGET_NONE = -128,
    COLOR_CAL_TARGET_DARK = -2,
    COLOR_CAL_TARGET_WHITE = -1,
};

typedef struct {
    uint16_t r;
    uint16_t g;
    uint16_t b;
    uint16_t c;
} color_proc_raw_ref_t;

typedef struct {
    bool valid;
    uint16_t sample_count;
    float norm_rgb[3];
    float lab[3];
    float luma;
} color_proc_patch_t;

typedef struct {
    color_palette_mode_t mode;
    uint8_t class_count;
    uint16_t valid_mask;
    uint16_t enabled_mask;
    bool dark_valid;
    bool white_valid;
    color_proc_raw_ref_t dark;
    color_proc_raw_ref_t white;
    color_proc_patch_t patches[COLOR_PROC_MAX_CLASSES];
} color_proc_calibration_profile_t;

typedef struct {
    color_palette_mode_t mode;
    color_classifier_t classifier;
    float confidence_threshold;
} color_proc_cfg_t;

typedef struct {
    int slot;
    float confidence;
} color_proc_candidate_t;

typedef struct {
    bool ready;
    color_proc_raw_ref_t raw;
    float norm_rgb[3];
    float lab[3];
    float luma;
    int detected_slot;
    float confidence;
    float best_distance;
    float second_distance;
    bool saturated;
    bool dark_valid;
    bool white_valid;
    color_proc_candidate_t top[COLOR_PROC_TOP_CANDIDATES];
} color_proc_result_t;

typedef struct {
    color_proc_raw_ref_t raw;
    float norm_rgb[3];
    float lab[3];
    float luma;
    bool processed_valid;
} color_proc_capture_sample_t;

typedef struct {
    bool active;
    color_palette_mode_t mode;
    int target_slot;
    uint16_t sample_count;
    color_proc_capture_sample_t samples[COLOR_PROC_CAPTURE_MAX_SAMPLES];
} color_proc_capture_t;

bool color_proc_mode_is_valid(uint8_t mode);
uint8_t color_proc_palette_class_count(color_palette_mode_t mode);
const char *color_proc_palette_name(color_palette_mode_t mode, int slot);
uint32_t color_proc_palette_rgb(color_palette_mode_t mode, int slot);
void color_proc_get_default_cfg(color_proc_cfg_t *out_cfg);
void color_proc_get_default_profile(color_palette_mode_t mode, color_proc_calibration_profile_t *out_profile);
esp_err_t color_proc_process(const color_proc_raw_ref_t *raw,
                             bool saturated,
                             const color_proc_calibration_profile_t *profile,
                             const color_proc_cfg_t *cfg,
                             color_proc_result_t *out_result);
void color_proc_capture_reset(color_proc_capture_t *capture,
                              color_palette_mode_t mode,
                              int target_slot);
bool color_proc_capture_add(color_proc_capture_t *capture,
                            const color_proc_raw_ref_t *raw,
                            const color_proc_result_t *result);
bool color_proc_capture_finalize_reference(const color_proc_capture_t *capture,
                                           color_proc_raw_ref_t *out_ref);
bool color_proc_capture_finalize_patch(const color_proc_capture_t *capture,
                                       color_proc_patch_t *out_patch);

#ifdef __cplusplus
}
#endif
