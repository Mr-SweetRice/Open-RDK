#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "traction_hal.h"
#include "traction_comm.h"
#include "traction_control_app.h"

// Pins
#define DRIVER_SLEEP_PIN     GPIO_NUM_21
#define DRIVE_ENABLE_PIN1    GPIO_NUM_0
#define DRIVE_ENABLE_PIN2    GPIO_NUM_1

// PWM config
#define PWM_FREQ_HZ          20000
#define PWM_RES              LEDC_TIMER_10_BIT

#define APP_SERIAL_LINK_TIMEOUT_MS 1200

#define ENABLE_ENCODER_LOG   0

#if ENABLE_ENCODER_LOG
static const char *TAG = "enc";
static TaskHandle_t s_enc_task = NULL;

static void encoder_monitor_task(void *arg)
{
    (void)arg;

    int64_t last_count = 0;
    int64_t last_t_us = esp_timer_get_time();

    while (1) {
        vTaskDelay(pdMS_TO_TICKS(100));

        int64_t c = 0;
        ESP_ERROR_CHECK(traction_encoder_get_count(&c));

        const int64_t now_us = esp_timer_get_time();
        const float dt = (now_us - last_t_us) / 1e6f;
        const int64_t dc = c - last_count;

        const float pos_rev = traction_counts_to_output_rev(c);
        const float vel_rpm = traction_delta_counts_to_output_rpm(dc, dt);

        ESP_LOGI(TAG, "count=%lld  pos=%.4f rev  vel=%.2f rpm",
                 (long long)c, (double)pos_rev, (double)vel_rpm);

        last_count = c;
        last_t_us = now_us;
    }
}
#endif

void app_main(void)
{
    esp_log_level_set("*", ESP_LOG_WARN);
    esp_log_level_set("main", ESP_LOG_INFO);
    esp_log_level_set("storage", ESP_LOG_INFO);

    traction_motor_cfg_t motor_cfg = {
        .sleep_gpio = DRIVER_SLEEP_PIN,
        .pwm_gpio_a = DRIVE_ENABLE_PIN1,
        .pwm_gpio_b = DRIVE_ENABLE_PIN2,
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .timer_num = LEDC_TIMER_0,
        .channel_a = LEDC_CHANNEL_1,
        .channel_b = LEDC_CHANNEL_2,
        .duty_resolution = PWM_RES,
        .pwm_freq_hz = PWM_FREQ_HZ,
    };
    ESP_ERROR_CHECK(traction_motor_init(&motor_cfg));

    traction_encoder_cfg_t enc_cfg = {
        .pin_a = GPIO_NUM_5,
        .pin_b = GPIO_NUM_6,
        .pullup = true,
        .invert_direction = false,
        .counts_per_motor_rev = 44,
        .gear_ratio = 45,
    };
    ESP_ERROR_CHECK(traction_encoder_init(&enc_cfg));

    ESP_ERROR_CHECK(traction_control_app_init());

    traction_comm_cfg_t comm_cfg = {0};
    traction_control_app_make_comm_cfg(&comm_cfg, APP_SERIAL_LINK_TIMEOUT_MS);
    ESP_ERROR_CHECK(traction_comm_init(&comm_cfg));

    xTaskCreate(traction_control_app_nvs_save_task, "nvs_save", 8192, NULL, 5, NULL);
    xTaskCreate(traction_control_app_speed_task, "speed_ctrl", 4096, NULL, 8, NULL);
    xTaskCreate(traction_comm_task, "serial_cmd", 4096, NULL, 9, NULL);

#if ENABLE_ENCODER_LOG
    xTaskCreate(encoder_monitor_task, "enc_mon", 4096, NULL, 6, &s_enc_task);
    traction_control_app_set_encoder_monitor_task(s_enc_task);
#endif
}
