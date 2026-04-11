#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "traction_types.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    TRACTION_ENCODER_MODE_QUADRATURE_X4 = 0,
    TRACTION_ENCODER_MODE_QUADRATURE_X2 = 1,
    TRACTION_ENCODER_MODE_SINGLE_CHANNEL = 2,
} traction_encoder_mode_t;

typedef struct {
    gpio_num_t pin_a;
    gpio_num_t pin_b;
    bool pullup;
    bool invert_direction;
    traction_encoder_mode_t mode;
    int32_t counts_per_motor_rev;
    float gear_ratio;
} traction_encoder_cfg_t;

esp_err_t traction_encoder_init(const traction_encoder_cfg_t *cfg);
esp_err_t traction_encoder_set_runtime_config(bool pullup,
                                              bool invert_direction,
                                              traction_encoder_mode_t mode,
                                              int32_t counts_per_motor_rev,
                                              float gear_ratio);
esp_err_t traction_encoder_get_runtime_config(traction_encoder_cfg_t *out_cfg);
void      traction_encoder_reset(int64_t value);
esp_err_t traction_encoder_get_count(int64_t *out_count);
esp_err_t traction_encoder_set_irq_enabled(bool enabled);

int32_t traction_encoder_counts_per_motor_rev(void);
float traction_encoder_counts_per_output_rev(void);

static inline float traction_counts_to_output_rev(int64_t counts) {
    return (float)counts / traction_encoder_counts_per_output_rev();
}

static inline float traction_counts_to_output_deg(int64_t counts) {
    return traction_counts_to_output_rev(counts) * 360.0f;
}

static inline float traction_counts_to_output_rad(int64_t counts) {
    const float two_pi = 6.28318530718f;
    return traction_counts_to_output_rev(counts) * two_pi;
}

static inline float traction_delta_counts_to_output_rpm(int64_t delta_counts, float dt_s) {
    return ((float)delta_counts / dt_s) * (60.0f / traction_encoder_counts_per_output_rev());
}

typedef struct {
    gpio_num_t sleep_gpio;
    gpio_num_t pwm_gpio_a;
    gpio_num_t pwm_gpio_b;
    gpio_num_t tb6612_pwm_gpio;
    traction_motor_bridge_t bridge_type;
    ledc_mode_t speed_mode;
    ledc_timer_t timer_num;
    ledc_channel_t channel_a;
    ledc_channel_t channel_b;
    ledc_timer_bit_t duty_resolution;
    uint32_t pwm_freq_hz;
} traction_motor_cfg_t;

esp_err_t traction_motor_init(const traction_motor_cfg_t *cfg);
esp_err_t traction_motor_sleep(bool enable_sleep);
esp_err_t traction_motor_set(int percent, traction_dir_t dir);
esp_err_t traction_motor_coast(void);
esp_err_t traction_motor_brake(void);
esp_err_t traction_motor_set_pwm_freq(uint32_t pwm_freq_hz);
esp_err_t traction_motor_get_pwm_freq(uint32_t *out_pwm_freq_hz);
esp_err_t traction_motor_set_bridge(traction_motor_bridge_t bridge);
esp_err_t traction_motor_get_bridge(traction_motor_bridge_t *out_bridge);

#ifdef __cplusplus
}
#endif
