#pragma once

#include <stdint.h>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "traction_comm.h"

#ifdef __cplusplus
extern "C" {
#endif

esp_err_t traction_control_app_init(void);
void traction_control_app_make_comm_cfg(traction_comm_cfg_t *out_cfg, uint32_t link_timeout_ms);
void traction_control_app_speed_task(void *arg);
void traction_control_app_nvs_save_task(void *arg);
void traction_control_app_set_encoder_monitor_task(TaskHandle_t task);

#ifdef __cplusplus
}
#endif
