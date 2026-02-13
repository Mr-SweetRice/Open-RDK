#include "traction_hal.h"

#include "esp_log.h"
#include "esp_check.h"

static const char *TAG = "traction_hal_motor";

static traction_motor_cfg_t s_cfg;
static bool s_inited = false;

static inline uint32_t max_duty(ledc_timer_bit_t res)
{
    // LEDC_TIMER_10_BIT -> 10, etc.
    return (1U << (uint32_t)res) - 1U;
}

esp_err_t traction_motor_init(const traction_motor_cfg_t *cfg)
{
    ESP_RETURN_ON_FALSE(cfg, ESP_ERR_INVALID_ARG, TAG, "cfg null");

    s_cfg = *cfg;

    // SLEEP/EN
    gpio_reset_pin(s_cfg.sleep_gpio);
    gpio_set_direction(s_cfg.sleep_gpio, GPIO_MODE_OUTPUT);
    gpio_set_level(s_cfg.sleep_gpio, 1); // acorda

    // Timer LEDC
    ledc_timer_config_t timer = {
        .speed_mode       = s_cfg.speed_mode,
        .timer_num        = s_cfg.timer_num,
        .duty_resolution  = s_cfg.duty_resolution,
        .freq_hz          = s_cfg.pwm_freq_hz,
        .clk_cfg          = LEDC_AUTO_CLK,
    };
    ESP_RETURN_ON_ERROR(ledc_timer_config(&timer), TAG, "timer config failed");

    // Canal A (IN1)
    ledc_channel_config_t ch_a = {
        .gpio_num   = s_cfg.pwm_gpio_a,
        .speed_mode = s_cfg.speed_mode,
        .channel    = s_cfg.channel_a,
        .intr_type  = LEDC_INTR_DISABLE,
        .timer_sel  = s_cfg.timer_num,
        .duty       = 0,
        .hpoint     = 0,
    };
    ESP_RETURN_ON_ERROR(ledc_channel_config(&ch_a), TAG, "channel A config failed");

    // Canal B (IN2)
    ledc_channel_config_t ch_b = {
        .gpio_num   = s_cfg.pwm_gpio_b,
        .speed_mode = s_cfg.speed_mode,
        .channel    = s_cfg.channel_b,
        .intr_type  = LEDC_INTR_DISABLE,
        .timer_sel  = s_cfg.timer_num,
        .duty       = 0,
        .hpoint     = 0,
    };
    ESP_RETURN_ON_ERROR(ledc_channel_config(&ch_b), TAG, "channel B config failed");

    s_inited = true;
    ESP_LOGI(TAG, "motor init ok (freq=%lu Hz, res=%u-bit-ish)", (unsigned long)s_cfg.pwm_freq_hz, (unsigned)s_cfg.duty_resolution);
    return ESP_OK;
}

esp_err_t traction_motor_sleep(bool enable_sleep)
{
    ESP_RETURN_ON_FALSE(s_inited, ESP_ERR_INVALID_STATE, TAG, "not inited");
    gpio_set_level(s_cfg.sleep_gpio, enable_sleep ? 0 : 1);
    return ESP_OK;
}

esp_err_t traction_motor_coast(void)
{
    ESP_RETURN_ON_FALSE(s_inited, ESP_ERR_INVALID_STATE, TAG, "not inited");

    ledc_set_duty(s_cfg.speed_mode, s_cfg.channel_a, 0);
    ledc_update_duty(s_cfg.speed_mode, s_cfg.channel_a);

    ledc_set_duty(s_cfg.speed_mode, s_cfg.channel_b, 0);
    ledc_update_duty(s_cfg.speed_mode, s_cfg.channel_b);

    return ESP_OK;
}

esp_err_t traction_motor_brake(void)
{
    ESP_RETURN_ON_FALSE(s_inited, ESP_ERR_INVALID_STATE, TAG, "not inited");

    // DRV8833: IN1=1 e IN2=1 aplica freio ativo (short brake)
    uint32_t duty = max_duty(s_cfg.duty_resolution);

    ledc_set_duty(s_cfg.speed_mode, s_cfg.channel_a, duty);
    ledc_update_duty(s_cfg.speed_mode, s_cfg.channel_a);

    ledc_set_duty(s_cfg.speed_mode, s_cfg.channel_b, duty);
    ledc_update_duty(s_cfg.speed_mode, s_cfg.channel_b);

    return ESP_OK;
}

esp_err_t traction_motor_set(int percent, traction_dir_t dir)
{
    ESP_RETURN_ON_FALSE(s_inited, ESP_ERR_INVALID_STATE, TAG, "not inited");

    if (percent < 0) percent = 0;
    if (percent > 100) percent = 100;

    uint32_t duty = (max_duty(s_cfg.duty_resolution) * (uint32_t)percent) / 100U;

    if (percent == 0) {
        return traction_motor_coast();
    }

    if (dir == TRACTION_DIR_CW) {
        // sentido 1: A=PWM, B=0
        ledc_set_duty(s_cfg.speed_mode, s_cfg.channel_a, duty);
        ledc_update_duty(s_cfg.speed_mode, s_cfg.channel_a);

        ledc_set_duty(s_cfg.speed_mode, s_cfg.channel_b, 0);
        ledc_update_duty(s_cfg.speed_mode, s_cfg.channel_b);
    } else {
        // sentido 2: A=0, B=PWM
        ledc_set_duty(s_cfg.speed_mode, s_cfg.channel_a, 0);
        ledc_update_duty(s_cfg.speed_mode, s_cfg.channel_a);

        ledc_set_duty(s_cfg.speed_mode, s_cfg.channel_b, duty);
        ledc_update_duty(s_cfg.speed_mode, s_cfg.channel_b);
    }

    return ESP_OK;
}
