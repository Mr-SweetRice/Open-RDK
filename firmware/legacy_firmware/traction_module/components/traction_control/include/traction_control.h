#pragma once

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    float kp;
    float ki;
    float kd;
    float out_min;
    float out_max;
    float i_min;
    float i_max;
    float d_alpha; // 0..1 (0 = sem filtro, 1 = muito suave)
} traction_pid_cfg_t;

typedef struct {
    float kp;
    float ki;
    float kd;
    float out_min;
    float out_max;
    float i_min;
    float i_max;
    float d_alpha;
    float i_term;
    float prev_meas;
    float d_filt;
    bool  inited;
} traction_pid_t;

void  traction_pid_init(traction_pid_t *pid, const traction_pid_cfg_t *cfg);
float traction_pid_update(traction_pid_t *pid, float setpoint, float measurement, float dt_s);
float traction_pid_update_ex(traction_pid_t *pid,
                             float setpoint,
                             float measurement,
                             float dt_s,
                             bool integral_enabled,
                             bool anti_windup_enabled,
                             bool reset_integral_when_disabled);
void  traction_pid_reset(traction_pid_t *pid);

#ifdef __cplusplus
}
#endif
