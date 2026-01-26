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
esp_err_t traction_motor_coast(void);                              // “solto” (IN1=0, IN2=0)

#ifdef __cplusplus
}
#endif
