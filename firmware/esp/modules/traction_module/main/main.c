#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"

#include "traction_hal.h"

// Pinos (como você definiu)
#define DRIVER_SLEEP_PIN     GPIO_NUM_21
#define DRIVE_ENABLE_PIN1    GPIO_NUM_0
#define DRIVE_ENABLE_PIN2    GPIO_NUM_1

// PWM config
#define PWM_FREQ_HZ          20000
#define PWM_RES              LEDC_TIMER_10_BIT

// Limites
#define MOTOR_MAX_OUTPUT_PCT 100

static const char *TAG = "main";

static void motor_test_task(void *arg)
{
    (void)arg;

    // garante acordado
    traction_motor_sleep(false);

    while (1) {
        ESP_LOGI(TAG, "coast");
        traction_motor_coast();
        vTaskDelay(pdMS_TO_TICKS(2000));

        ESP_LOGI(TAG, "ramp CW 45..%d", MOTOR_MAX_OUTPUT_PCT);
        for (int i = 45; i <= MOTOR_MAX_OUTPUT_PCT; i++) {
            traction_motor_set(i, TRACTION_DIR_CW);
            vTaskDelay(pdMS_TO_TICKS(50));
        }

        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}

void app_main(void)
{
    traction_motor_cfg_t cfg = {
        .sleep_gpio      = DRIVER_SLEEP_PIN,
        .pwm_gpio_a      = DRIVE_ENABLE_PIN1,
        .pwm_gpio_b      = DRIVE_ENABLE_PIN2,

        .speed_mode      = LEDC_LOW_SPEED_MODE,
        .timer_num       = LEDC_TIMER_0,
        .channel_a       = LEDC_CHANNEL_1,
        .channel_b       = LEDC_CHANNEL_2,
        .duty_resolution = PWM_RES,
        .pwm_freq_hz     = PWM_FREQ_HZ,
    };

    ESP_ERROR_CHECK(traction_motor_init(&cfg));

    // Task de teste (depois isso vira traction_control_task)
    xTaskCreate(motor_test_task, "motor_test", 4096, NULL, 8, NULL);
}
