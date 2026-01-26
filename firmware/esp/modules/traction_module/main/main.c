#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/ledc.h"

//Motor associe
#define driver_sleep_pin      21
#define drive_enable_pin1    0
#define drive_enable_pin2    1
#define motor_enc1_pin   5
#define motor_enc2_pin   6

#define a_clock_wise 1
#define clock_wise 0
#define motor_max_instant_output 70 //percent
#define LEDC_MODE       LEDC_LOW_SPEED_MODE
#define LEDC_TIMER      LEDC_TIMER_0
#define LEDC_CHANNEL1   LEDC_CHANNEL_1
#define LEDC_CHANNEL2   LEDC_CHANNEL_2
#define LEDC_DUTY_RES   LEDC_TIMER_10_BIT   // 0..1023
#define LEDC_FREQUENCY  20000               // 20 kHz

static inline uint32_t max_duty(void) {
    return (1U << LEDC_DUTY_RES) - 1U;
}

// Algumas SuperMini têm LED invertido (active-low). Se ficar “ao contrário”, troque para 0.

static void ledc_init(){
    ledc_timer_config_t timer = {
        .speed_mode       = LEDC_MODE,
        .timer_num        = LEDC_TIMER,
        .duty_resolution  = LEDC_DUTY_RES,
        .freq_hz          = LEDC_FREQUENCY,
        .clk_cfg          = LEDC_AUTO_CLK,
    };
    ledc_timer_config(&timer);

    ledc_channel_config_t ch1 = {
        .gpio_num   = drive_enable_pin1,
        .speed_mode = LEDC_MODE,
        .channel    = LEDC_CHANNEL1,
        .intr_type  = LEDC_INTR_DISABLE,
        .timer_sel  = LEDC_TIMER,
        .duty       = 0,
        .hpoint     = 0,
    };
    ledc_channel_config(&ch1);
    ledc_channel_config_t ch2 = {
        .gpio_num   = drive_enable_pin2,
        .speed_mode = LEDC_MODE,
        .channel    = LEDC_CHANNEL2,
        .intr_type  = LEDC_INTR_DISABLE,
        .timer_sel  = LEDC_TIMER,
        .duty       = 0,
        .hpoint     = 0,
    };
    ledc_channel_config(&ch2);
}

static void motor_control(int percent, int dir){
    if (percent < 0) percent = 0;
    if (percent > 100) percent = 100;

    uint32_t duty = (max_duty() * (uint32_t)percent) / 100U;
    if(dir == 0){
        // sentido 1
        ledc_set_duty(LEDC_MODE, LEDC_CHANNEL1, duty);
        ledc_update_duty(LEDC_MODE, LEDC_CHANNEL1);
        ledc_set_duty(LEDC_MODE, LEDC_CHANNEL2, 0);
        ledc_update_duty(LEDC_MODE, LEDC_CHANNEL2);
    } else {
        // sentido 2
        ledc_set_duty(LEDC_MODE, LEDC_CHANNEL1, 0);
        ledc_update_duty(LEDC_MODE, LEDC_CHANNEL1);
        ledc_set_duty(LEDC_MODE, LEDC_CHANNEL2, duty);
        ledc_update_duty(LEDC_MODE, LEDC_CHANNEL2);
    }
}

void app_main(void)
{

    ledc_init();
    gpio_reset_pin(driver_sleep_pin);                          // opcional, mas recomendado
    gpio_set_direction(driver_sleep_pin, GPIO_MODE_OUTPUT);    // vira saída

    gpio_set_level(driver_sleep_pin, 1);     
    vTaskDelay(pdMS_TO_TICKS(5000));
    while (1) {
        motor_control(0, a_clock_wise);
        vTaskDelay(pdMS_TO_TICKS(2000));
        for(int i = 45; i<=100; i++){
            motor_control(i, a_clock_wise);
            vTaskDelay(pdMS_TO_TICKS(50));
        }

        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}