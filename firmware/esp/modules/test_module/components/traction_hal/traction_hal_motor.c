#include "traction_bridge.h"
#include "traction_hal.h"

#include "esp_log.h"
#include "esp_check.h"

static const char *TAG = "traction_hal_motor";

static traction_motor_cfg_t s_cfg;
static bool s_inited = false;

static traction_motor_bridge_t bridge_or_default(traction_motor_bridge_t bridge)
{
    return (bridge == TRACTION_MOTOR_BRIDGE_TB6612 ||
            bridge == TRACTION_MOTOR_BRIDGE_DRV8833)
           ? bridge
           : TRACTION_MOTOR_BRIDGE_TB6612;
}

static const char *bridge_name(traction_motor_bridge_t bridge)
{
    switch (bridge) {
    case TRACTION_MOTOR_BRIDGE_TB6612:
        return "TB6612";
    case TRACTION_MOTOR_BRIDGE_DRV8833:
        return "DRV8833";
    default:
        return "UNKNOWN";
    }
}

static inline uint32_t max_duty(ledc_timer_bit_t res)
{
    return (1U << (uint32_t)res) - 1U;
}

esp_err_t traction_motor_init(const traction_motor_cfg_t *cfg)
{
    ESP_RETURN_ON_FALSE(cfg, ESP_ERR_INVALID_ARG, TAG, "cfg null");

    s_cfg = *cfg;
    s_cfg.bridge_type = bridge_or_default(s_cfg.bridge_type);

    traction_bridge_cfg_t bridge_cfg = {
        .sleep_gpio      = s_cfg.sleep_gpio,
        .pwm_gpio_a      = s_cfg.pwm_gpio_a,
        .pwm_gpio_b      = s_cfg.pwm_gpio_b,
        .tb6612_pwm_gpio = s_cfg.tb6612_pwm_gpio,
        .bridge_type     = s_cfg.bridge_type,
        .speed_mode      = s_cfg.speed_mode,
        .timer_num       = s_cfg.timer_num,
        .channel_a       = s_cfg.channel_a,
        .channel_b       = s_cfg.channel_b,
        .duty_resolution = s_cfg.duty_resolution,
        .pwm_freq_hz     = s_cfg.pwm_freq_hz,
    };

    ESP_RETURN_ON_ERROR(traction_bridge_init(&bridge_cfg), TAG, "bridge init failed");

    s_inited = true;
    ESP_LOGI(TAG, "motor init ok (bridge=%s, freq=%lu Hz, res=%u-bit-ish)",
             bridge_name(s_cfg.bridge_type),
             (unsigned long)s_cfg.pwm_freq_hz,
             (unsigned)s_cfg.duty_resolution);
    return ESP_OK;
}

esp_err_t traction_motor_sleep(bool enable_sleep)
{
    ESP_RETURN_ON_FALSE(s_inited, ESP_ERR_INVALID_STATE, TAG, "not inited");
    return traction_bridge_set_sleep(enable_sleep);
}

esp_err_t traction_motor_coast(void)
{
    ESP_RETURN_ON_FALSE(s_inited, ESP_ERR_INVALID_STATE, TAG, "not inited");
    return traction_bridge_coast();
}

esp_err_t traction_motor_brake(void)
{
    ESP_RETURN_ON_FALSE(s_inited, ESP_ERR_INVALID_STATE, TAG, "not inited");
    return traction_bridge_brake();
}

esp_err_t traction_motor_set(int percent, traction_dir_t dir)
{
    ESP_RETURN_ON_FALSE(s_inited, ESP_ERR_INVALID_STATE, TAG, "not inited");

    if (percent < 0) percent = 0;
    if (percent > 100) percent = 100;

    uint32_t duty = (max_duty(s_cfg.duty_resolution) * (uint32_t)percent) / 100U;
    return traction_bridge_set(dir, duty);
}

esp_err_t traction_motor_set_pwm_freq(uint32_t pwm_freq_hz)
{
    ESP_RETURN_ON_FALSE(s_inited, ESP_ERR_INVALID_STATE, TAG, "not inited");
    ESP_RETURN_ON_ERROR(traction_bridge_set_pwm_freq(pwm_freq_hz), TAG, "bridge pwm freq failed");
    s_cfg.pwm_freq_hz = pwm_freq_hz;
    ESP_LOGI(TAG, "pwm frequency set to %lu Hz", (unsigned long)s_cfg.pwm_freq_hz);
    return ESP_OK;
}

esp_err_t traction_motor_get_pwm_freq(uint32_t *out_pwm_freq_hz)
{
    ESP_RETURN_ON_FALSE(s_inited, ESP_ERR_INVALID_STATE, TAG, "not inited");
    ESP_RETURN_ON_FALSE(out_pwm_freq_hz, ESP_ERR_INVALID_ARG, TAG, "out null");
    *out_pwm_freq_hz = s_cfg.pwm_freq_hz;
    return ESP_OK;
}

esp_err_t traction_motor_set_bridge(traction_motor_bridge_t bridge)
{
    ESP_RETURN_ON_FALSE(bridge == TRACTION_MOTOR_BRIDGE_TB6612 ||
                        bridge == TRACTION_MOTOR_BRIDGE_DRV8833,
                        ESP_ERR_INVALID_ARG, TAG, "invalid bridge");
    ESP_RETURN_ON_FALSE(s_inited, ESP_ERR_INVALID_STATE, TAG, "not inited");

    if (s_cfg.bridge_type == bridge) {
        return ESP_OK;
    }

    ESP_RETURN_ON_ERROR(traction_bridge_set_bridge(bridge), TAG, "bridge mode switch failed");
    s_cfg.bridge_type = bridge;
    ESP_LOGI(TAG, "bridge set to %s", bridge_name(s_cfg.bridge_type));
    return ESP_OK;
}

esp_err_t traction_motor_get_bridge(traction_motor_bridge_t *out_bridge)
{
    ESP_RETURN_ON_FALSE(out_bridge, ESP_ERR_INVALID_ARG, TAG, "out null");
    *out_bridge = bridge_or_default(s_cfg.bridge_type);
    return ESP_OK;
}
