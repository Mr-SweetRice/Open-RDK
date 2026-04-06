#include "traction_control.h"

#include <string.h>

static float clampf(float v, float vmin, float vmax)
{
    if (v < vmin) return vmin;
    if (v > vmax) return vmax;
    return v;
}

void traction_pid_init(traction_pid_t *pid, const traction_pid_cfg_t *cfg)
{
    if (!pid || !cfg) return;
    memset(pid, 0, sizeof(*pid));

    pid->kp = cfg->kp;
    pid->ki = cfg->ki;
    pid->kd = cfg->kd;
    pid->out_min = cfg->out_min;
    pid->out_max = cfg->out_max;
    pid->i_min = cfg->i_min;
    pid->i_max = cfg->i_max;

    if (cfg->d_alpha < 0.0f) {
        pid->d_alpha = 0.0f;
    } else if (cfg->d_alpha > 1.0f) {
        pid->d_alpha = 1.0f;
    } else {
        pid->d_alpha = cfg->d_alpha;
    }

    pid->i_term = 0.0f;
    pid->prev_meas = 0.0f;
    pid->d_filt = 0.0f;
    pid->inited = false;
}

void traction_pid_reset(traction_pid_t *pid)
{
    if (!pid) return;
    pid->i_term = 0.0f;
    pid->prev_meas = 0.0f;
    pid->d_filt = 0.0f;
    pid->inited = false;
}

float traction_pid_update_ex(traction_pid_t *pid,
                             float setpoint,
                             float measurement,
                             float dt_s,
                             bool integral_enabled,
                             bool anti_windup_enabled,
                             bool reset_integral_when_disabled)
{
    if (!pid || dt_s <= 0.0f) return 0.0f;

    float err = setpoint - measurement;
    float p = pid->kp * err;

    // Derivative on measurement reduces setpoint kick.
    float d_meas = 0.0f;
    if (pid->inited) {
        d_meas = (measurement - pid->prev_meas) / dt_s;
    }
    float d_term = -pid->kd * d_meas;

    if (!pid->inited) {
        pid->d_filt = d_term;
        pid->inited = true;
    } else {
        pid->d_filt += pid->d_alpha * (d_term - pid->d_filt);
    }

    float i_term_new = pid->i_term;
    if (integral_enabled) {
        i_term_new += (pid->ki * err * dt_s);
        if (anti_windup_enabled) {
            i_term_new = clampf(i_term_new, pid->i_min, pid->i_max);
        }
    } else if (reset_integral_when_disabled) {
        i_term_new = 0.0f;
    }

    float i_term_applied = integral_enabled ? i_term_new : 0.0f;
    float out_unclamped = p + i_term_applied + pid->d_filt;
    float out = clampf(out_unclamped, pid->out_min, pid->out_max);

    if (anti_windup_enabled && out != out_unclamped) {
        if ((out == pid->out_max && err > 0.0f) ||
            (out == pid->out_min && err < 0.0f)) {
            i_term_new = pid->i_term;
        }
    }

    pid->i_term = i_term_new;
    pid->prev_meas = measurement;

    return out;
}

float traction_pid_update(traction_pid_t *pid, float setpoint, float measurement, float dt_s)
{
    return traction_pid_update_ex(pid, setpoint, measurement, dt_s, true, true, false);
}
