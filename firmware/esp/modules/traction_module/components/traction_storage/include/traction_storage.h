#pragma once

#include <stdbool.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    float kp;
    float ki;
    float kd;
    float setpoint_rpm;
} traction_pid_store_t;

typedef struct {
    float kp;
    float ki;
    float kd;
    float target_rev;
} traction_pos_pid_store_t;

esp_err_t traction_storage_init(void);
bool traction_storage_is_ready(void);

esp_err_t traction_storage_load_pid(traction_pid_store_t *out);
esp_err_t traction_storage_save_pid(const traction_pid_store_t *in);
esp_err_t traction_storage_load_pos_pid(traction_pos_pid_store_t *out);
esp_err_t traction_storage_save_pos_pid(const traction_pos_pid_store_t *in);

#ifdef __cplusplus
}
#endif
