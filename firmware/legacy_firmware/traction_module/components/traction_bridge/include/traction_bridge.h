#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "traction_types.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    gpio_num_t              sleep_gpio;
    gpio_num_t              pwm_gpio_a;
    gpio_num_t              pwm_gpio_b;
    gpio_num_t              tb6612_pwm_gpio;
    traction_motor_bridge_t bridge_type;
    ledc_mode_t             speed_mode;
    ledc_timer_t            timer_num;
    ledc_channel_t          channel_a;
    ledc_channel_t          channel_b;
    ledc_timer_bit_t        duty_resolution;
    uint32_t                pwm_freq_hz;
} traction_bridge_cfg_t;

esp_err_t traction_bridge_init(const traction_bridge_cfg_t *cfg);
esp_err_t traction_bridge_set_sleep(bool enable_sleep);
esp_err_t traction_bridge_coast(void);
esp_err_t traction_bridge_brake(void);
esp_err_t traction_bridge_set(traction_dir_t dir, uint32_t duty);
esp_err_t traction_bridge_set_pwm_freq(uint32_t pwm_freq_hz);
esp_err_t traction_bridge_get_pwm_freq(uint32_t *out_pwm_freq_hz);
esp_err_t traction_bridge_set_bridge(traction_motor_bridge_t bridge);
esp_err_t traction_bridge_get_bridge(traction_motor_bridge_t *out_bridge);

#ifdef __cplusplus
}
#endif
