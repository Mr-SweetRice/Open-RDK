#pragma once

#include <stdint.h>
#include <stdbool.h>
#include "driver/ledc.h"
#include "driver/gpio.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    TRACTION_DIR_CW  = 0,
    TRACTION_DIR_CCW = 1,
} traction_dir_t;

typedef struct {
    gpio_num_t pin_a;
    gpio_num_t pin_b;
    bool pullup;                 // true para pull-up interno
    bool invert_direction;       // inverte sinal (+/-) caso o sentido fique ao contrário
    int32_t counts_per_motor_rev; // ex.: 44 (x4)
    int32_t gear_ratio;           // ex.: 45
} traction_encoder_cfg_t;

esp_err_t traction_encoder_init(const traction_encoder_cfg_t *cfg);
void      traction_encoder_reset(int64_t value);
esp_err_t traction_encoder_get_count(int64_t *out_count);
esp_err_t traction_encoder_set_irq_enabled(bool enabled);

int32_t   traction_encoder_counts_per_motor_rev(void);
int32_t   traction_encoder_counts_per_output_rev(void);

// Helpers para posição/velocidade (usam a config carregada)
static inline float traction_counts_to_output_rev(int64_t counts) {
    return (float)counts / (float)traction_encoder_counts_per_output_rev();
}

static inline float traction_counts_to_output_rad(int64_t counts) {
    const float two_pi = 6.28318530718f;
    return traction_counts_to_output_rev(counts) * two_pi;
}

static inline float traction_delta_counts_to_output_rpm(int64_t delta_counts, float dt_s) {
    // rpm = (counts/s) * (60 / counts_per_rev)
    return ((float)delta_counts / dt_s) * (60.0f / (float)traction_encoder_counts_per_output_rev());
}

typedef struct {
    gpio_num_t sleep_gpio;     // pino SLEEP/EN do driver (ex.: DRV8833)
    gpio_num_t pwm_gpio_a;     // IN1 (ou AIN1)
    gpio_num_t pwm_gpio_b;     // IN2 (ou AIN2)

    ledc_mode_t       speed_mode;
    ledc_timer_t      timer_num;
    ledc_channel_t    channel_a;
    ledc_channel_t    channel_b;
    ledc_timer_bit_t  duty_resolution;
    uint32_t          pwm_freq_hz;
} traction_motor_cfg_t;

esp_err_t traction_motor_init(const traction_motor_cfg_t *cfg);
esp_err_t traction_motor_sleep(bool enable_sleep);                 // true = dorme, false = acorda
esp_err_t traction_motor_set(int percent, traction_dir_t dir);     // 0..100%
esp_err_t traction_motor_coast(void);                              // roda livre (IN1=0, IN2=0)
esp_err_t traction_motor_brake(void);                              // freio ativo (IN1=1, IN2=1)

#ifdef __cplusplus
}
#endif
