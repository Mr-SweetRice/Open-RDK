#include "ds_sensor.h"

#include <stdlib.h>
#include <string.h>

#include "esp_attr.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

static const char *TAG = "ds_sensor";

struct ds_sensor_context {
    gpio_num_t trigger_pin;
    gpio_num_t echo_pin;
    uint32_t max_distance_mm;
    SemaphoreHandle_t measurement_mutex;
    volatile bool armed;
    volatile bool saw_rising_edge;
    volatile bool completed;
    volatile int64_t rising_edge_us;
    volatile uint32_t echo_time_us;
    volatile TaskHandle_t waiting_task;
    int64_t last_trigger_us;
};

static void IRAM_ATTR echo_edge_isr(void *arg)
{
    struct ds_sensor_context *handle = (struct ds_sensor_context *)arg;
    if (!handle || !handle->armed) {
        return;
    }

    const int level = gpio_get_level(handle->echo_pin);
    const int64_t now_us = esp_timer_get_time();
    if (level != 0) {
        if (!handle->saw_rising_edge) {
            handle->rising_edge_us = now_us;
            handle->saw_rising_edge = true;
        }
        return;
    }

    if (!handle->saw_rising_edge || handle->completed) {
        return;
    }

    const int64_t pulse_us = now_us - handle->rising_edge_us;
    handle->echo_time_us = (pulse_us > 0 && pulse_us <= UINT32_MAX)
        ? (uint32_t)pulse_us
        : 0U;
    handle->completed = true;
    handle->armed = false;

    TaskHandle_t task = (TaskHandle_t)handle->waiting_task;
    if (task) {
        BaseType_t wake = pdFALSE;
        vTaskNotifyGiveFromISR(task, &wake);
        if (wake == pdTRUE) {
            portYIELD_FROM_ISR();
        }
    }
}

static uint32_t echo_timeout_us(uint32_t max_distance_mm)
{
    /*
     * Always cover the HC-SR04 physical range so a pulse beyond the configured
     * maximum can be reported as ABOVE_MAX instead of being misclassified as
     * NO_ECHO. The configured maximum is still used for validity.
     */
    (void)max_distance_mm;
    return 30000U;
}

static void wait_for_echo_or_deadline(
    struct ds_sensor_context *handle,
    uint32_t timeout_us)
{
    const int64_t deadline_us = esp_timer_get_time() + (int64_t)timeout_us;
    while (!handle->completed) {
        const int64_t remaining_us = deadline_us - esp_timer_get_time();
        if (remaining_us <= 0) {
            break;
        }
        const uint32_t remaining_ms = (uint32_t)((remaining_us + 999LL) / 1000LL);
        TickType_t wait_ticks = pdMS_TO_TICKS(remaining_ms);
        if (wait_ticks < 1U) {
            wait_ticks = 1U;
        }
        (void)ulTaskNotifyTake(pdTRUE, wait_ticks);
    }
}

static void wait_for_minimum_cycle(struct ds_sensor_context *handle)
{
    if (!handle || handle->last_trigger_us <= 0) {
        return;
    }

    while (true) {
        const int64_t remaining_us =
            DS_SENSOR_MIN_CYCLE_US - (esp_timer_get_time() - handle->last_trigger_us);
        if (remaining_us <= 0) {
            return;
        }
        if (remaining_us >= 1000) {
            const uint32_t delay_ms = (uint32_t)((remaining_us + 999) / 1000);
            vTaskDelay(pdMS_TO_TICKS(delay_ms));
        } else {
            esp_rom_delay_us((uint32_t)remaining_us);
        }
    }
}

esp_err_t ds_sensor_init(const ds_sensor_cfg_t *cfg, ds_sensor_handle_t *out_handle)
{
    if (!cfg || !out_handle ||
        !GPIO_IS_VALID_OUTPUT_GPIO(cfg->trigger_pin) ||
        !GPIO_IS_VALID_GPIO(cfg->echo_pin) ||
        cfg->trigger_pin == cfg->echo_pin ||
        cfg->max_distance_mm < DS_SENSOR_MIN_DISTANCE_MM ||
        cfg->max_distance_mm > DS_SENSOR_MAX_DISTANCE_MM) {
        return ESP_ERR_INVALID_ARG;
    }

    struct ds_sensor_context *handle = calloc(1, sizeof(*handle));
    if (!handle) {
        return ESP_ERR_NO_MEM;
    }

    handle->trigger_pin = cfg->trigger_pin;
    handle->echo_pin = cfg->echo_pin;
    handle->max_distance_mm = cfg->max_distance_mm;
    handle->measurement_mutex = xSemaphoreCreateMutex();
    if (!handle->measurement_mutex) {
        free(handle);
        return ESP_ERR_NO_MEM;
    }

    gpio_config_t trigger_cfg = {
        .pin_bit_mask = 1ULL << (uint32_t)cfg->trigger_pin,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    esp_err_t err = gpio_config(&trigger_cfg);
    if (err != ESP_OK) {
        vSemaphoreDelete(handle->measurement_mutex);
        free(handle);
        return err;
    }
    ESP_ERROR_CHECK_WITHOUT_ABORT(gpio_set_level(cfg->trigger_pin, 0));

    gpio_config_t echo_cfg = {
        .pin_bit_mask = 1ULL << (uint32_t)cfg->echo_pin,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_ENABLE,
        .intr_type = GPIO_INTR_ANYEDGE,
    };
    err = gpio_config(&echo_cfg);
    if (err != ESP_OK) {
        vSemaphoreDelete(handle->measurement_mutex);
        free(handle);
        return err;
    }

    err = gpio_install_isr_service(0);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        vSemaphoreDelete(handle->measurement_mutex);
        free(handle);
        return err;
    }
    err = gpio_isr_handler_add(cfg->echo_pin, echo_edge_isr, handle);
    if (err != ESP_OK) {
        vSemaphoreDelete(handle->measurement_mutex);
        free(handle);
        return err;
    }

    *out_handle = handle;
    ESP_LOGI(TAG, "HC-SR04 ready: trigger=GPIO%d echo=GPIO%d max=%lu mm",
             (int)cfg->trigger_pin,
             (int)cfg->echo_pin,
             (unsigned long)cfg->max_distance_mm);
    return ESP_OK;
}

esp_err_t ds_sensor_set_max_distance(ds_sensor_handle_t handle, uint32_t max_distance_mm)
{
    if (!handle ||
        max_distance_mm < DS_SENSOR_MIN_DISTANCE_MM ||
        max_distance_mm > DS_SENSOR_MAX_DISTANCE_MM) {
        return ESP_ERR_INVALID_ARG;
    }
    if (xSemaphoreTake(handle->measurement_mutex, pdMS_TO_TICKS(100)) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }
    handle->max_distance_mm = max_distance_mm;
    xSemaphoreGive(handle->measurement_mutex);
    return ESP_OK;
}

esp_err_t ds_sensor_measure(ds_sensor_handle_t handle, ds_sensor_sample_t *out_sample)
{
    if (!handle || !out_sample) {
        return ESP_ERR_INVALID_ARG;
    }

    memset(out_sample, 0, sizeof(*out_sample));
    out_sample->raw_distance_mm = -1;
    out_sample->timestamp_ms = (uint64_t)(esp_timer_get_time() / 1000LL);

    if (xSemaphoreTake(handle->measurement_mutex, pdMS_TO_TICKS(100)) != pdTRUE) {
        out_sample->health_flags = DS_HEALTH_ECHO_STUCK;
        return ESP_ERR_TIMEOUT;
    }

    wait_for_minimum_cycle(handle);
    (void)ulTaskNotifyTake(pdTRUE, 0);

    gpio_intr_disable(handle->echo_pin);
    handle->armed = false;
    handle->saw_rising_edge = false;
    handle->completed = false;
    handle->rising_edge_us = 0;
    handle->echo_time_us = 0U;
    handle->waiting_task = xTaskGetCurrentTaskHandle();

    if (gpio_get_level(handle->echo_pin) != 0) {
        handle->waiting_task = NULL;
        gpio_intr_enable(handle->echo_pin);
        out_sample->health_flags = DS_HEALTH_ECHO_STUCK;
        xSemaphoreGive(handle->measurement_mutex);
        return ESP_OK;
    }

    handle->armed = true;
    gpio_intr_enable(handle->echo_pin);

    gpio_set_level(handle->trigger_pin, 1);
    esp_rom_delay_us(10U);
    gpio_set_level(handle->trigger_pin, 0);
    handle->last_trigger_us = esp_timer_get_time();

    const uint32_t timeout_us = echo_timeout_us(handle->max_distance_mm);
    wait_for_echo_or_deadline(handle, timeout_us);

    gpio_intr_disable(handle->echo_pin);
    const bool completed = handle->completed;
    const bool saw_rising_edge = handle->saw_rising_edge;
    const uint32_t pulse_us = handle->echo_time_us;
    const bool echo_high = gpio_get_level(handle->echo_pin) != 0;
    handle->armed = false;
    handle->waiting_task = NULL;
    gpio_intr_enable(handle->echo_pin);

    out_sample->timestamp_ms = (uint64_t)(esp_timer_get_time() / 1000LL);
    if (!completed || pulse_us == 0U) {
        out_sample->health_flags =
            (saw_rising_edge || echo_high) ? DS_HEALTH_ECHO_STUCK : DS_HEALTH_NO_ECHO;
        xSemaphoreGive(handle->measurement_mutex);
        return ESP_OK;
    }

    out_sample->echo_time_us = pulse_us;
    const uint64_t distance_mm = (((uint64_t)pulse_us * 343ULL) + 1000ULL) / 2000ULL;
    out_sample->raw_distance_mm =
        (distance_mm <= INT32_MAX) ? (int32_t)distance_mm : INT32_MAX;

    if (distance_mm < DS_SENSOR_MIN_DISTANCE_MM) {
        out_sample->health_flags = DS_HEALTH_BELOW_MIN;
    } else if (distance_mm > handle->max_distance_mm) {
        out_sample->health_flags = DS_HEALTH_ABOVE_MAX;
    } else {
        out_sample->health_flags = DS_HEALTH_VALID;
    }

    xSemaphoreGive(handle->measurement_mutex);
    return ESP_OK;
}
