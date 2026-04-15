#include "traction_bridge.h"

#include "esp_log.h"
#include "esp_check.h"
#include "driver/gpio.h"
#include "driver/ledc.h"

static const char *TAG = "traction_bridge";
static traction_bridge_cfg_t s_cfg;
static bool s_inited = false;
static bool s_tb6612_single_pwm = false;
static gpio_num_t s_tb6612_pwm_gpio = GPIO_NUM_NC;

static inline bool bridge_is_valid(traction_motor_bridge_t bridge)
{
    return (bridge == TRACTION_MOTOR_BRIDGE_TB6612) ||
           (bridge == TRACTION_MOTOR_BRIDGE_DRV8833);
}

static inline traction_motor_bridge_t bridge_or_default(traction_motor_bridge_t bridge)
{
    return bridge_is_valid(bridge) ? bridge : TRACTION_MOTOR_BRIDGE_TB6612;
}

static inline uint32_t max_duty(ledc_timer_bit_t resolution)
{
    return (1U << (uint32_t)resolution) - 1U;
}

static inline void ledc_write_value(ledc_channel_t channel, uint32_t duty)
{
    ledc_set_duty(s_cfg.speed_mode, channel, duty);
    ledc_update_duty(s_cfg.speed_mode, channel);
}

static esp_err_t configure_ledc_channel(ledc_channel_t channel, gpio_num_t gpio)
{
    ledc_channel_config_t ch = {
        .gpio_num   = gpio,
        .speed_mode = s_cfg.speed_mode,
        .channel    = channel,
        .intr_type  = LEDC_INTR_DISABLE,
        .timer_sel  = s_cfg.timer_num,
        .duty       = 0,
        .hpoint     = 0,
    };
    return ledc_channel_config(&ch);
}

static esp_err_t configure_bridge_mode(traction_motor_bridge_t bridge)
{
    const bool has_tb_pwm = GPIO_IS_VALID_OUTPUT_GPIO(s_cfg.tb6612_pwm_gpio) &&
                            (s_cfg.tb6612_pwm_gpio != s_cfg.pwm_gpio_a) &&
                            (s_cfg.tb6612_pwm_gpio != s_cfg.pwm_gpio_b);

    if (bridge == TRACTION_MOTOR_BRIDGE_TB6612 && has_tb_pwm) {
        s_tb6612_single_pwm = true;
        s_tb6612_pwm_gpio = s_cfg.tb6612_pwm_gpio;

        ESP_RETURN_ON_ERROR(configure_ledc_channel(s_cfg.channel_a, s_tb6612_pwm_gpio),
                            TAG, "TB6612 PWM channel config failed");

        gpio_reset_pin(s_cfg.pwm_gpio_a);
        gpio_set_direction(s_cfg.pwm_gpio_a, GPIO_MODE_OUTPUT);
        gpio_set_level(s_cfg.pwm_gpio_a, 0);

        gpio_reset_pin(s_cfg.pwm_gpio_b);
        gpio_set_direction(s_cfg.pwm_gpio_b, GPIO_MODE_OUTPUT);
        gpio_set_level(s_cfg.pwm_gpio_b, 0);

        return ESP_OK;
    }

    s_tb6612_single_pwm = false;
    s_tb6612_pwm_gpio = GPIO_NUM_NC;

    ESP_RETURN_ON_ERROR(configure_ledc_channel(s_cfg.channel_a, s_cfg.pwm_gpio_a),
                        TAG, "channel A config failed");
    ESP_RETURN_ON_ERROR(configure_ledc_channel(s_cfg.channel_b, s_cfg.pwm_gpio_b),
                        TAG, "channel B config failed");
    return ESP_OK;
}

esp_err_t traction_bridge_init(const traction_bridge_cfg_t *cfg)
{
    ESP_RETURN_ON_FALSE(cfg, ESP_ERR_INVALID_ARG, TAG, "cfg null");

    s_cfg = *cfg;
    s_cfg.bridge_type = bridge_or_default(s_cfg.bridge_type);
    s_tb6612_single_pwm = false;
    s_tb6612_pwm_gpio = GPIO_NUM_NC;

    gpio_reset_pin(s_cfg.sleep_gpio);
    gpio_set_direction(s_cfg.sleep_gpio, GPIO_MODE_OUTPUT);
    gpio_set_level(s_cfg.sleep_gpio, 0);

    ledc_timer_config_t timer = {
        .speed_mode       = s_cfg.speed_mode,
        .timer_num        = s_cfg.timer_num,
        .duty_resolution  = s_cfg.duty_resolution,
        .freq_hz          = s_cfg.pwm_freq_hz,
        .clk_cfg          = LEDC_AUTO_CLK,
    };
    ESP_RETURN_ON_ERROR(ledc_timer_config(&timer), TAG, "timer config failed");

    ESP_RETURN_ON_ERROR(configure_bridge_mode(s_cfg.bridge_type), TAG, "bridge mode config failed");

    s_inited = true;
    ESP_RETURN_ON_ERROR(traction_bridge_coast(), TAG, "bridge coast failed");
    ESP_LOGI(TAG, "bridge init ok (bridge=%s, tb_mode=%s, freq=%lu Hz, res=%u-bit-ish)",
             s_cfg.bridge_type == TRACTION_MOTOR_BRIDGE_TB6612 ? "TB6612" : "DRV8833",
             s_tb6612_single_pwm ? "single_pwm" : "legacy_dual_pwm",
             (unsigned long)s_cfg.pwm_freq_hz,
             (unsigned)s_cfg.duty_resolution);
    return ESP_OK;
}

esp_err_t traction_bridge_set_sleep(bool enable_sleep)
{
    ESP_RETURN_ON_FALSE(s_inited, ESP_ERR_INVALID_STATE, TAG, "not inited");
    gpio_set_level(s_cfg.sleep_gpio, enable_sleep ? 0 : 1);
    return ESP_OK;
}

esp_err_t traction_bridge_coast(void)
{
    ESP_RETURN_ON_FALSE(s_inited, ESP_ERR_INVALID_STATE, TAG, "not inited");

    if (s_tb6612_single_pwm) {
        ledc_write_value(s_cfg.channel_a, 0);
        gpio_set_level(s_cfg.pwm_gpio_a, 0);
        gpio_set_level(s_cfg.pwm_gpio_b, 0);
    } else {
        ledc_write_value(s_cfg.channel_a, 0);
        ledc_write_value(s_cfg.channel_b, 0);
    }

    return ESP_OK;
}

esp_err_t traction_bridge_brake(void)
{
    ESP_RETURN_ON_FALSE(s_inited, ESP_ERR_INVALID_STATE, TAG, "not inited");

    uint32_t duty = max_duty(s_cfg.duty_resolution);
    if (s_tb6612_single_pwm) {
        ledc_write_value(s_cfg.channel_a, duty);
        gpio_set_level(s_cfg.pwm_gpio_a, 1);
        gpio_set_level(s_cfg.pwm_gpio_b, 1);
    } else {
        ledc_write_value(s_cfg.channel_a, duty);
        ledc_write_value(s_cfg.channel_b, duty);
    }
    return ESP_OK;
}

esp_err_t traction_bridge_set(traction_dir_t dir, uint32_t duty)
{
    ESP_RETURN_ON_FALSE(s_inited, ESP_ERR_INVALID_STATE, TAG, "not inited");

    if (duty == 0) {
        return traction_bridge_coast();
    }

    if (s_tb6612_single_pwm) {
        ledc_write_value(s_cfg.channel_a, duty);
        if (dir == TRACTION_DIR_CW) {
            gpio_set_level(s_cfg.pwm_gpio_a, 1);
            gpio_set_level(s_cfg.pwm_gpio_b, 0);
        } else {
            gpio_set_level(s_cfg.pwm_gpio_a, 0);
            gpio_set_level(s_cfg.pwm_gpio_b, 1);
        }
    } else {
        if (dir == TRACTION_DIR_CW) {
            ledc_write_value(s_cfg.channel_a, duty);
            ledc_write_value(s_cfg.channel_b, 0);
        } else {
            ledc_write_value(s_cfg.channel_a, 0);
            ledc_write_value(s_cfg.channel_b, duty);
        }
    }
    return ESP_OK;
}

esp_err_t traction_bridge_set_pwm_freq(uint32_t pwm_freq_hz)
{
    ESP_RETURN_ON_FALSE(s_inited, ESP_ERR_INVALID_STATE, TAG, "not inited");
    ESP_RETURN_ON_FALSE(pwm_freq_hz > 0, ESP_ERR_INVALID_ARG, TAG, "invalid pwm freq");

    ledc_timer_config_t timer = {
        .speed_mode = s_cfg.speed_mode,
        .timer_num = s_cfg.timer_num,
        .duty_resolution = s_cfg.duty_resolution,
        .freq_hz = pwm_freq_hz,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ESP_RETURN_ON_ERROR(ledc_timer_config(&timer), TAG, "timer reconfig failed");
    s_cfg.pwm_freq_hz = pwm_freq_hz;
    return ESP_OK;
}

esp_err_t traction_bridge_get_pwm_freq(uint32_t *out_pwm_freq_hz)
{
    ESP_RETURN_ON_FALSE(s_inited, ESP_ERR_INVALID_STATE, TAG, "not inited");
    ESP_RETURN_ON_FALSE(out_pwm_freq_hz, ESP_ERR_INVALID_ARG, TAG, "out null");
    *out_pwm_freq_hz = s_cfg.pwm_freq_hz;
    return ESP_OK;
}

esp_err_t traction_bridge_set_bridge(traction_motor_bridge_t bridge)
{
    ESP_RETURN_ON_FALSE(bridge_is_valid(bridge), ESP_ERR_INVALID_ARG, TAG, "invalid bridge");
    ESP_RETURN_ON_FALSE(s_inited, ESP_ERR_INVALID_STATE, TAG, "not inited");

    if (s_cfg.bridge_type == bridge) {
        return ESP_OK;
    }

    ESP_RETURN_ON_ERROR(configure_bridge_mode(bridge), TAG, "bridge mode switch failed");
    s_cfg.bridge_type = bridge;
    return traction_bridge_coast();
}

esp_err_t traction_bridge_get_bridge(traction_motor_bridge_t *out_bridge)
{
    ESP_RETURN_ON_FALSE(s_inited, ESP_ERR_INVALID_STATE, TAG, "not inited");
    ESP_RETURN_ON_FALSE(out_bridge, ESP_ERR_INVALID_ARG, TAG, "out_bridge null");
    *out_bridge = s_cfg.bridge_type;
    return ESP_OK;
}
