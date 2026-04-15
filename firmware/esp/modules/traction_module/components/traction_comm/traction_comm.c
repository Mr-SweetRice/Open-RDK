#include "traction_comm.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "driver/uart.h"
#include "esp_vfs_dev.h"
#include "driver/usb_serial_jtag.h"

/*
 * Serial command reference (one command per line, "\n" or "\r\n"):
 *
 * PID namespace (speed loop):
 *
 * 1) GET PID RPM
 *    - Function: returns current PID gains and setpoint.
 *    - Response: P,<kp>,<ki>,<kd>,<sp>
 *    - Example:  GET PID RPM
 *
 * 2) GET TELEM
 *    - Function: requests one RPM telemetry sample.
 *    - Response: T,<target_rpm>,<measured_rpm>,<output_pwm_pct>,<output_raw_pct>
 *    - Example:  GET TELEM
 *
 * 3) SET PID RPM KP <value>
 *    - Function: updates proportional gain.
 *    - Response: OK + updated P line.
 *    - Example:  SET PID RPM KP 1.50
 *
 * 4) SET PID RPM KI <value>
 *    - Function: updates integral gain.
 *    - Response: OK + updated P line.
 *    - Example:  SET PID RPM KI 2.20
 *
 * 5) SET PID RPM KD <value>
 *    - Function: updates derivative gain.
 *    - Response: OK + updated P line.
 *    - Example:  SET PID RPM KD 0.05
 *
 * 6) SET PID RPM SP <value>
 *    - Function: updates speed setpoint in RPM.
 *    - Response: OK + updated P line.
 *    - Example:  SET PID RPM SP 120
 *
 * 7) SET OUT <value>
 *    - Function: forces motor output percent before the firmware normalization curve.
 *    - Response: OK
 *    - Example:  SET OUT 35
 *
 * 8) SET OUT RAW <value>
 *    - Function: forces the real PWM percent sent to the H-bridge, bypassing normalization.
 *    - Response: OK
 *    - Example:  SET OUT RAW 55
 *
 * 9) CLR OUT
 *    - Function: clears manual output and returns control to PID.
 *    - Response: OK
 *    - Example:  CLR OUT
 *
 * 10) SAVE PID RPM
 *    - Function: enqueues save of PID/setpoint to NVS.
 *    - Immediate response: S,ENQ + OK
 *    - Async responses: S,START -> S,OK (or S,ERR,<code>) -> S,END
 *    - Example:  SAVE PID RPM
 *
 * 11) GET PID POS
 *    - Function: returns current position PID gains/target angle and mode.
 *    - Response: PP,<kp>,<ki>,<kd>,<target_deg>,<enabled>,<integral_window_deg>
 *    - Example:  GET PID POS
 *
 * 12) SET PID POS KP|KI|KD <value>
 *    - Function: updates position PID gains.
 *    - Response: OK + updated PP line.
 *    - Example:  SET PID POS KP 2.0
 *
 * 13) SET PID POS ANGLE <value>
 *    - Function: updates position target in output shaft degrees.
 *    - Response: OK + updated PP line.
 *    - Example:  SET PID POS ANGLE 180
 *
 * 14) START PID POS / STOP PID POS
 *    - Function: enables or disables position mode.
 *    - Response: OK + updated PP line.
 *    - Example:  START PID POS
 *
 * 15) SAVE PID POS
 *    - Function: enqueues save of position PID/target to NVS.
 *    - Immediate response: S,ENQ + OK
 *    - Async responses: S,START -> S,OK (or S,ERR,<code>) -> S,END
 *    - Example:  SAVE PID POS
 *
 * 16) GET TELEM POS
 *    - Function: requests one position telemetry sample.
 *    - Response: TP,<target_deg>,<position_deg>,<output_pwm_pct>,<output_raw_pct>,<i_term>
 *    - Example:  GET TELEM POS
 *
 * 17) GET PID POS SINE
 *    - Function: returns firmware sine generator configuration for position target.
 *    - Response: PS,<amp_deg>,<offset_deg>,<period_s>,<enabled>
 *    - Example:  GET PID POS SINE
 *
 * 18) SET PID POS SINE AMP|OFFSET|PERIOD <value>
 *    - Function: updates sine generator parameters.
 *    - Response: OK + updated PS line.
 *    - Example:  SET PID POS SINE AMP 90
 *
 * 19) START PID POS SINE / STOP PID POS SINE
 *    - Function: enables or disables firmware sine target generator.
 *    - Response: OK + updated PS line.
 *    - Example:  START PID POS SINE
 *
 * 20) GET INVERT
 *    - Function: returns inversion config.
 *    - Response: INV,<motor_invert>,<encoder_invert>
 *    - Example:  GET INVERT
 *
 * 21) SET MOTOR INV <0|1>
 *    - Function: updates motor direction inversion and persists to NVS.
 *    - Response: OK + updated INV line.
 *    - Example:  SET MOTOR INV 1
 *
 * 22) SET ENCODER INV <0|1>
 *    - Function: updates encoder direction inversion and persists to NVS.
 *    - Response: OK + updated INV line.
 *    - Example:  SET ENCODER INV 1
 *
 * 23) GET BRIDGE
 *    - Function: returns selected motor bridge.
 *    - Response: BRG,<bridge_type> (0=TB6612, 1=DRV8833)
 *    - Example:  GET BRIDGE
 *
 * 24) GET CURVE
 *    - Function: returns the fixed 10-point motor curve as RPM values for PWM 10..100%.
 *    - Response: CV,<rpm@10>,<rpm@20>,...,<rpm@100>
 *    - Example:  GET CURVE
 *
 * 25) SET CURVE <10|20|...|100> <rpm>
 *    - Function: updates one fixed motor-curve point in RAM.
 *    - Response: OK + updated CV line.
 *    - Example:  SET CURVE 40 52.5
 *
 * 26) SAVE CURVE
 *    - Function: enqueues save of the motor curve to NVS.
 *    - Immediate response: S,ENQ + OK
 *    - Async responses: S,START -> S,OK (or S,ERR,<code>) -> S,END
 *    - Example:  SAVE CURVE
 *
 * 27) GET CFG
 *    - Function: returns controller configuration stored in NVS/runtime.
 *    - Responses:
 *      CFG,<bridge>,<encoder_mode>,<motor_inv>,<encoder_inv>,<pullup>,<pwm_freq>,<counts>,<gear>,<rpm_max>
 *      CFGN,<notes>
 *    - Example:  GET CFG
 *
 * 28) SET CFG <field> <value>
 *    - Fields: BRIDGE, ENCODER_MODE, MOTOR_INV, ENCODER_INV, PULLUP, PWM_FREQ, COUNTS_PER_REV, GEAR_RATIO, RPM_MAX, NOTES
 *    - Response: OK + updated CFG/CFGN lines
 *
 * 29) SAVE CFG
 *    - Function: enqueues save of controller configuration to NVS.
 *    - Immediate response: S,ENQ + OK
 *    - Async responses: S,START -> S,OK (or S,ERR,<code>) -> S,END
 *
 * 30) SET BRIDGE <0|1>
 *    - Function: updates motor bridge and persists to NVS.
 *    - Response: OK + updated BRG line.
 *    - Example:  SET BRIDGE 0
 *
 * 31) Unknown/invalid command
 *    - Response: ERR
 *
 * Backward compatibility (temporary):
 * - GET, SAVE, SET KP/KI/KD/SP are still accepted.
 */

static const char *TAG = "traction_comm";

static traction_comm_cfg_t s_cfg = {0};
static bool s_inited = false;
static bool s_usb_jtag_ready = false;
static SemaphoreHandle_t s_tx_mutex = NULL;
static bool s_stream_telem_enabled = false;
static uint32_t s_stream_telem_seq = 0U;
static int64_t s_stream_telem_last_tx_us = 0;

#define FRAME_SYNC_0                     0xAAU
#define FRAME_SYNC_1                     0x55U
#define FRAME_SYNC_2                     0xAAU
#define FRAME_SYNC_3                     0x55U
#define FRAME_SYNC_LEN                   4U
#define FRAME_RX_MAX_LEN                 200U
#define FRAME_SEQ_BYTES                  3U
#define FRAME_SEQ_MASK                   0x00FFFFFFUL

#define HOST_MODULE_ID                   0x00U
#define TRACTION_MODULE_ID                   0x11U
#define FRAME_HELLO_BYTE                 0x01U
#define FRAME_HELLO_ACK_BYTE             0x06U
#define FRAME_MODULE_QUERY_BYTE          0x04U
#define FRAME_MODULE_INFO_PREFIX_BYTE    0x05U
#define FRAME_MODULE_NAME                "traction_module"

#define FRAME_MSG_TYPE_CMD               0x01U
#define FRAME_MSG_TYPE_TEST              0x02U
#define FRAME_MSG_TYPE_TELEMETRY         0x03U
#define FRAME_MSG_TYPE_TRACTION_OUT      0x04U

#define FRAME_TELEMETRY_TX_PERIOD_US     100000LL

#ifndef TRACTION_COMM_ENABLE_LINE_FALLBACK
#define TRACTION_COMM_ENABLE_LINE_FALLBACK 0
#endif

static const uint8_t s_frame_sync[FRAME_SYNC_LEN] = {
    FRAME_SYNC_0, FRAME_SYNC_1, FRAME_SYNC_2, FRAME_SYNC_3
};

static void write_bytes_locked(const uint8_t *data, size_t len)
{
    if (!s_inited || !data || len == 0U) return;

    bool locked = false;
    if (s_tx_mutex) {
        locked = (xSemaphoreTake(s_tx_mutex, pdMS_TO_TICKS(20)) == pdTRUE);
    }

    uart_write_bytes(UART_NUM_0, data, len);
    if (s_usb_jtag_ready) {
        usb_serial_jtag_write_bytes(data, len, 0);
    }

    if (locked) {
        xSemaphoreGive(s_tx_mutex);
    }
}

static bool feed_char_cmd(char ch, char *buf, size_t *len, size_t max_len)
{
    if (ch == '\r' || ch == '\n') {
        if (*len == 0) return false;
        buf[*len] = '\0';
        *len = 0;
        return true;
    }
    if (*len < (max_len - 1)) {
        buf[(*len)++] = ch;
    } else {
        *len = 0;
    }
    return false;
}

void traction_comm_send_line(const char *fmt, ...)
{
    if (!s_inited || !fmt) return;

    char buf[128];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n <= 0) return;
    if (n > (int)(sizeof(buf) - 1)) n = (int)(sizeof(buf) - 1);
    buf[n++] = '\n';
    write_bytes_locked((const uint8_t *)buf, (size_t)n);
}

static bool get_rpm_snapshot(traction_comm_pid_rpm_state_t *st)
{
    if (!st || !s_cfg.get_rpm_state) return false;
    memset(st, 0, sizeof(*st));
    return s_cfg.get_rpm_state(s_cfg.ctx, st);
}

static void send_rpm_snapshot(void)
{
    traction_comm_pid_rpm_state_t st = {0};
    if (!get_rpm_snapshot(&st)) {
        traction_comm_send_line("ERR");
        return;
    }
    traction_comm_send_line("P,%.4f,%.4f,%.4f,%.2f",
                            (double)st.kp, (double)st.ki, (double)st.kd, (double)st.setpoint_rpm);
}

static bool format_rpm_snapshot(char *out, size_t out_len)
{
    traction_comm_pid_rpm_state_t st = {0};
    if (!out || out_len == 0U) {
        return false;
    }
    if (!get_rpm_snapshot(&st)) {
        out[0] = '\0';
        return false;
    }

    int n = snprintf(
        out,
        out_len,
        "P,%.4f,%.4f,%.4f,%.2f",
        (double)st.kp,
        (double)st.ki,
        (double)st.kd,
        (double)st.setpoint_rpm
    );
    return n > 0 && (size_t)n < out_len;
}

static bool get_rpm_telem_snapshot(traction_comm_rpm_telem_state_t *st)
{
    if (!st || !s_cfg.get_rpm_telem_state) {
        return false;
    }
    memset(st, 0, sizeof(*st));
    return s_cfg.get_rpm_telem_state(s_cfg.ctx, st);
}

static bool format_rpm_telem_snapshot(char *out, size_t out_len)
{
    traction_comm_rpm_telem_state_t st = {0};
    if (!out || out_len == 0U) {
        return false;
    }
    if (!get_rpm_telem_snapshot(&st)) {
        out[0] = '\0';
        return false;
    }

    int n = snprintf(
        out,
        out_len,
        "T,%.2f,%.2f,%.2f,%.2f",
        (double)st.target_rpm,
        (double)st.measured_rpm,
        (double)st.cmd_pwm_signed,
        (double)st.cmd_raw
    );
    return n > 0 && (size_t)n < out_len;
}

static bool try_apply_rpm_pid_command(
    const char *line,
    char *response_out,
    size_t response_out_len,
    bool *ok_out
)
{
    float val = 0.0f;
    bool ok = false;
    bool handled = false;

    if (response_out && response_out_len > 0U) {
        response_out[0] = '\0';
    }
    if (!line) {
        if (ok_out) {
            *ok_out = false;
        }
        return false;
    }

    if ((strcmp(line, "GET PID RPM") == 0) || (strcmp(line, "GET") == 0)) {
        handled = true;
        ok = format_rpm_snapshot(response_out, response_out_len);
        if (!ok && response_out && response_out_len > 0U) {
            snprintf(response_out, response_out_len, "ERR");
        }
    } else if (strcmp(line, "GET TELEM") == 0) {
        handled = true;
        ok = format_rpm_telem_snapshot(response_out, response_out_len);
        if (!ok && response_out && response_out_len > 0U) {
            snprintf(response_out, response_out_len, "ERR");
        }
    } else if ((sscanf(line, "SET PID RPM KP %f", &val) == 1) ||
               (sscanf(line, "SET KP %f", &val) == 1)) {
        handled = true;
        if (s_cfg.set_rpm_kp) {
            s_cfg.set_rpm_kp(s_cfg.ctx, val);
            ok = true;
        }
    } else if ((sscanf(line, "SET PID RPM KI %f", &val) == 1) ||
               (sscanf(line, "SET KI %f", &val) == 1)) {
        handled = true;
        if (s_cfg.set_rpm_ki) {
            s_cfg.set_rpm_ki(s_cfg.ctx, val);
            ok = true;
        }
    } else if ((sscanf(line, "SET PID RPM KD %f", &val) == 1) ||
               (sscanf(line, "SET KD %f", &val) == 1)) {
        handled = true;
        if (s_cfg.set_rpm_kd) {
            s_cfg.set_rpm_kd(s_cfg.ctx, val);
            ok = true;
        }
    } else if ((sscanf(line, "SET PID RPM SP %f", &val) == 1) ||
               (sscanf(line, "SET SP %f", &val) == 1)) {
        handled = true;
        if (s_cfg.set_rpm_setpoint) {
            s_cfg.set_rpm_setpoint(s_cfg.ctx, val);
            ok = true;
        }
    } else if ((strcmp(line, "SAVE PID RPM") == 0) || (strcmp(line, "SAVE") == 0)) {
        traction_comm_pid_rpm_state_t st = {0};
        handled = true;
        if (!get_rpm_snapshot(&st) || !s_cfg.enqueue_rpm_save) {
            ok = false;
            if (response_out && response_out_len > 0U) {
                snprintf(response_out, response_out_len, "ERR");
            }
        } else if (s_cfg.enqueue_rpm_save(s_cfg.ctx, &st)) {
            ok = true;
            if (response_out && response_out_len > 0U) {
                snprintf(response_out, response_out_len, "S,ENQ");
            }
        } else {
            ok = false;
            if (response_out && response_out_len > 0U) {
                snprintf(response_out, response_out_len, "ERR");
            }
        }
    } else {
        handled = false;
    }

    if (handled && response_out && response_out_len > 0U && response_out[0] == '\0') {
        snprintf(response_out, response_out_len, ok ? "OK" : "ERR");
    }
    if (ok_out) {
        *ok_out = ok;
    }
    return handled;
}

static bool get_pos_snapshot(traction_comm_pid_pos_state_t *st)
{
    if (!st || !s_cfg.get_pos_state) return false;
    memset(st, 0, sizeof(*st));
    return s_cfg.get_pos_state(s_cfg.ctx, st);
}

static void send_pos_snapshot(void)
{
    traction_comm_pid_pos_state_t st = {0};
    if (!get_pos_snapshot(&st)) {
        traction_comm_send_line("ERR");
        return;
    }
    traction_comm_send_line("PP,%.4f,%.4f,%.4f,%.4f,%d,%.2f",
                            (double)st.kp, (double)st.ki, (double)st.kd,
                            (double)st.target_deg, st.enabled ? 1 : 0,
                            (double)st.integral_window_deg);
}

static bool format_pos_snapshot(char *out, size_t out_len)
{
    traction_comm_pid_pos_state_t st = {0};
    if (!out || out_len == 0U) {
        return false;
    }
    if (!get_pos_snapshot(&st)) {
        out[0] = '\0';
        return false;
    }

    int n = snprintf(
        out,
        out_len,
        "PP,%.4f,%.4f,%.4f,%.4f,%d,%.2f",
        (double)st.kp,
        (double)st.ki,
        (double)st.kd,
        (double)st.target_deg,
        st.enabled ? 1 : 0,
        (double)st.integral_window_deg
    );
    return n > 0 && (size_t)n < out_len;
}

static bool get_pos_telem_snapshot(traction_comm_pos_telem_state_t *st)
{
    if (!st || !s_cfg.get_pos_telem_state) {
        return false;
    }
    memset(st, 0, sizeof(*st));
    return s_cfg.get_pos_telem_state(s_cfg.ctx, st);
}

static bool format_pos_telem_snapshot(char *out, size_t out_len)
{
    traction_comm_pos_telem_state_t st = {0};
    if (!out || out_len == 0U) {
        return false;
    }
    if (!get_pos_telem_snapshot(&st)) {
        out[0] = '\0';
        return false;
    }

    int n = snprintf(
        out,
        out_len,
        "TP,%.4f,%.4f,%.2f,%.2f,%.4f",
        (double)st.target_deg,
        (double)st.position_deg,
        (double)st.cmd_pwm_signed,
        (double)st.cmd_raw,
        (double)st.i_term
    );
    return n > 0 && (size_t)n < out_len;
}

static bool try_apply_pos_pid_command(
    const char *line,
    char *response_out,
    size_t response_out_len,
    bool *ok_out
)
{
    float val = 0.0f;
    bool ok = false;
    bool handled = false;

    if (response_out && response_out_len > 0U) {
        response_out[0] = '\0';
    }
    if (!line) {
        if (ok_out) {
            *ok_out = false;
        }
        return false;
    }

    if (strcmp(line, "GET PID POS") == 0) {
        handled = true;
        ok = format_pos_snapshot(response_out, response_out_len);
        if (!ok && response_out && response_out_len > 0U) {
            snprintf(response_out, response_out_len, "ERR");
        }
    } else if (strcmp(line, "GET TELEM POS") == 0) {
        handled = true;
        ok = format_pos_telem_snapshot(response_out, response_out_len);
        if (!ok && response_out && response_out_len > 0U) {
            snprintf(response_out, response_out_len, "ERR");
        }
    } else if (strcmp(line, "GET PID POS SINE") == 0) {
        traction_comm_pid_pos_sine_state_t sine_st = {0};
        handled = true;
        if (s_cfg.get_pos_sine_state && s_cfg.get_pos_sine_state(s_cfg.ctx, &sine_st)) {
            ok = true;
            if (response_out && response_out_len > 0U) {
                snprintf(
                    response_out,
                    response_out_len,
                    "PS,%.2f,%.2f,%.2f,%d",
                    (double)sine_st.amp_deg,
                    (double)sine_st.offset_deg,
                    (double)sine_st.period_s,
                    sine_st.enabled ? 1 : 0
                );
            }
        } else if (response_out && response_out_len > 0U) {
            snprintf(response_out, response_out_len, "ERR");
        }
    } else if (sscanf(line, "SET PID POS KP %f", &val) == 1) {
        handled = true;
        if (s_cfg.set_pos_kp) {
            s_cfg.set_pos_kp(s_cfg.ctx, val);
            ok = true;
        }
    } else if (sscanf(line, "SET PID POS KI %f", &val) == 1) {
        handled = true;
        if (s_cfg.set_pos_ki) {
            s_cfg.set_pos_ki(s_cfg.ctx, val);
            ok = true;
        }
    } else if (sscanf(line, "SET PID POS KD %f", &val) == 1) {
        handled = true;
        if (s_cfg.set_pos_kd) {
            s_cfg.set_pos_kd(s_cfg.ctx, val);
            ok = true;
        }
    } else if (sscanf(line, "SET PID POS IWIN %f", &val) == 1) {
        handled = true;
        if (s_cfg.set_pos_integral_window_deg) {
            s_cfg.set_pos_integral_window_deg(s_cfg.ctx, val);
            ok = true;
        }
    } else if ((sscanf(line, "SET PID POS ANGLE %f", &val) == 1) ||
               (sscanf(line, "SET PID POS TARGET %f", &val) == 1)) {
        handled = true;
        if (s_cfg.set_pos_target_deg) {
            s_cfg.set_pos_target_deg(s_cfg.ctx, val);
            ok = true;
        }
    } else if (sscanf(line, "SET PID POS SINE AMP %f", &val) == 1) {
        handled = true;
        if (s_cfg.set_pos_sine_amp_deg) {
            s_cfg.set_pos_sine_amp_deg(s_cfg.ctx, val);
            ok = true;
        }
    } else if (sscanf(line, "SET PID POS SINE OFFSET %f", &val) == 1) {
        handled = true;
        if (s_cfg.set_pos_sine_offset_deg) {
            s_cfg.set_pos_sine_offset_deg(s_cfg.ctx, val);
            ok = true;
        }
    } else if (sscanf(line, "SET PID POS SINE PERIOD %f", &val) == 1) {
        handled = true;
        if (s_cfg.set_pos_sine_period_s) {
            s_cfg.set_pos_sine_period_s(s_cfg.ctx, val);
            ok = true;
        }
    } else if (strcmp(line, "START PID POS") == 0) {
        handled = true;
        if (s_cfg.set_pos_enabled) {
            s_cfg.set_pos_enabled(s_cfg.ctx, true);
            ok = true;
        }
    } else if (strcmp(line, "START PID POS SINE") == 0) {
        handled = true;
        if (s_cfg.set_pos_sine_enabled) {
            s_cfg.set_pos_sine_enabled(s_cfg.ctx, true);
            ok = true;
        }
    } else if (strcmp(line, "STOP PID POS SINE") == 0) {
        handled = true;
        if (s_cfg.set_pos_sine_enabled) {
            s_cfg.set_pos_sine_enabled(s_cfg.ctx, false);
            ok = true;
        }
    } else if (strcmp(line, "STOP PID POS") == 0) {
        handled = true;
        if (s_cfg.set_pos_enabled) {
            s_cfg.set_pos_enabled(s_cfg.ctx, false);
            ok = true;
        }
    } else if (strcmp(line, "SAVE PID POS") == 0) {
        traction_comm_pid_pos_state_t st = {0};
        handled = true;
        if (!get_pos_snapshot(&st) || !s_cfg.enqueue_pos_save) {
            ok = false;
            if (response_out && response_out_len > 0U) {
                snprintf(response_out, response_out_len, "ERR");
            }
        } else if (s_cfg.enqueue_pos_save(s_cfg.ctx, &st)) {
            ok = true;
            if (response_out && response_out_len > 0U) {
                snprintf(response_out, response_out_len, "S,ENQ");
            }
        } else {
            ok = false;
            if (response_out && response_out_len > 0U) {
                snprintf(response_out, response_out_len, "ERR");
            }
        }
    } else {
        handled = false;
    }

    if (handled && response_out && response_out_len > 0U && response_out[0] == '\0') {
        snprintf(response_out, response_out_len, ok ? "OK" : "ERR");
    }
    if (ok_out) {
        *ok_out = ok;
    }
    return handled;
}

static bool get_pos_sine_snapshot(traction_comm_pid_pos_sine_state_t *st)
{
    if (!st || !s_cfg.get_pos_sine_state) return false;
    memset(st, 0, sizeof(*st));
    return s_cfg.get_pos_sine_state(s_cfg.ctx, st);
}

static void send_pos_sine_snapshot(void)
{
    traction_comm_pid_pos_sine_state_t st = {0};
    if (!get_pos_sine_snapshot(&st)) {
        traction_comm_send_line("ERR");
        return;
    }
    traction_comm_send_line("PS,%.2f,%.2f,%.2f,%d",
                            (double)st.amp_deg, (double)st.offset_deg,
                            (double)st.period_s, st.enabled ? 1 : 0);
}

static bool get_invert_snapshot(traction_comm_invert_state_t *st)
{
    if (!st || !s_cfg.get_invert_state) return false;
    memset(st, 0, sizeof(*st));
    return s_cfg.get_invert_state(s_cfg.ctx, st);
}

static void send_invert_snapshot(void)
{
    traction_comm_invert_state_t st = {0};
    if (!get_invert_snapshot(&st)) {
        traction_comm_send_line("ERR");
        return;
    }
    traction_comm_send_line("INV,%d,%d", st.motor_invert ? 1 : 0, st.encoder_invert ? 1 : 0);
}

static bool get_bridge_snapshot(traction_comm_bridge_state_t *st)
{
    if (!st || !s_cfg.get_bridge_state) return false;
    memset(st, 0, sizeof(*st));
    return s_cfg.get_bridge_state(s_cfg.ctx, st);
}

static void send_bridge_snapshot(void)
{
    traction_comm_bridge_state_t st = {0};
    if (!get_bridge_snapshot(&st)) {
        traction_comm_send_line("ERR");
        return;
    }
    traction_comm_send_line("BRG,%u", (unsigned)st.bridge_type);
}

static bool format_invert_snapshot(char *out, size_t out_len)
{
    traction_comm_invert_state_t st = {0};
    if (!out || out_len == 0U) {
        return false;
    }
    if (!get_invert_snapshot(&st)) {
        out[0] = '\0';
        return false;
    }

    int n = snprintf(
        out,
        out_len,
        "INV,%d,%d",
        st.motor_invert ? 1 : 0,
        st.encoder_invert ? 1 : 0
    );
    return n > 0 && (size_t)n < out_len;
}

static bool format_bridge_snapshot(char *out, size_t out_len)
{
    traction_comm_bridge_state_t st = {0};
    if (!out || out_len == 0U) {
        return false;
    }
    if (!get_bridge_snapshot(&st)) {
        out[0] = '\0';
        return false;
    }

    int n = snprintf(out, out_len, "BRG,%u", (unsigned)st.bridge_type);
    return n > 0 && (size_t)n < out_len;
}

static bool try_apply_invert_bridge_command(
    const char *line,
    char *response_out,
    size_t response_out_len,
    bool *ok_out
)
{
    int ivalue = 0;
    bool ok = false;
    bool handled = false;

    if (response_out && response_out_len > 0U) {
        response_out[0] = '\0';
    }
    if (!line) {
        if (ok_out) {
            *ok_out = false;
        }
        return false;
    }

    if (strcmp(line, "GET INVERT") == 0) {
        handled = true;
        ok = format_invert_snapshot(response_out, response_out_len);
        if (!ok && response_out && response_out_len > 0U) {
            snprintf(response_out, response_out_len, "ERR");
        }
    } else if (sscanf(line, "SET MOTOR INV %d", &ivalue) == 1) {
        handled = true;
        if ((ivalue == 0 || ivalue == 1) && s_cfg.set_motor_invert) {
            s_cfg.set_motor_invert(s_cfg.ctx, ivalue != 0);
            ok = true;
        }
    } else if (sscanf(line, "SET ENCODER INV %d", &ivalue) == 1) {
        handled = true;
        if ((ivalue == 0 || ivalue == 1) && s_cfg.set_encoder_invert) {
            s_cfg.set_encoder_invert(s_cfg.ctx, ivalue != 0);
            ok = true;
        }
    } else if (strcmp(line, "GET BRIDGE") == 0) {
        handled = true;
        ok = format_bridge_snapshot(response_out, response_out_len);
        if (!ok && response_out && response_out_len > 0U) {
            snprintf(response_out, response_out_len, "ERR");
        }
    } else if (sscanf(line, "SET BRIDGE %d", &ivalue) == 1) {
        handled = true;
        if ((ivalue == 0 || ivalue == 1) && s_cfg.set_bridge_type) {
            s_cfg.set_bridge_type(s_cfg.ctx, (uint8_t)ivalue);
            ok = true;
        }
    } else {
        handled = false;
    }

    if (handled && response_out && response_out_len > 0U && response_out[0] == '\0') {
        snprintf(response_out, response_out_len, ok ? "OK" : "ERR");
    }
    if (ok_out) {
        *ok_out = ok;
    }
    return handled;
}

static bool get_curve_snapshot(traction_comm_curve_state_t *st)
{
    if (!st || !s_cfg.get_curve_state) return false;
    memset(st, 0, sizeof(*st));
    return s_cfg.get_curve_state(s_cfg.ctx, st);
}

static void send_curve_snapshot(void)
{
    traction_comm_curve_state_t st = {0};
    if (!get_curve_snapshot(&st)) {
        traction_comm_send_line("ERR");
        return;
    }
    traction_comm_send_line("CV,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f",
                            (double)st.rpm_points[0], (double)st.rpm_points[1],
                            (double)st.rpm_points[2], (double)st.rpm_points[3],
                            (double)st.rpm_points[4], (double)st.rpm_points[5],
                            (double)st.rpm_points[6], (double)st.rpm_points[7],
                            (double)st.rpm_points[8], (double)st.rpm_points[9]);
}

static bool format_curve_snapshot(char *out, size_t out_len)
{
    traction_comm_curve_state_t st = {0};
    if (!out || out_len == 0U) {
        return false;
    }
    if (!get_curve_snapshot(&st)) {
        out[0] = '\0';
        return false;
    }

    int n = snprintf(
        out,
        out_len,
        "CV,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f",
        (double)st.rpm_points[0],
        (double)st.rpm_points[1],
        (double)st.rpm_points[2],
        (double)st.rpm_points[3],
        (double)st.rpm_points[4],
        (double)st.rpm_points[5],
        (double)st.rpm_points[6],
        (double)st.rpm_points[7],
        (double)st.rpm_points[8],
        (double)st.rpm_points[9]
    );
    return n > 0 && (size_t)n < out_len;
}

static bool try_apply_curve_command(
    const char *line,
    char *response_out,
    size_t response_out_len,
    bool *ok_out
)
{
    int curve_pwm_pct = 0;
    float val = 0.0f;
    bool ok = false;
    bool handled = false;

    if (response_out && response_out_len > 0U) {
        response_out[0] = '\0';
    }
    if (!line) {
        if (ok_out) {
            *ok_out = false;
        }
        return false;
    }

    if (strcmp(line, "GET CURVE") == 0) {
        handled = true;
        ok = format_curve_snapshot(response_out, response_out_len);
        if (!ok && response_out && response_out_len > 0U) {
            snprintf(response_out, response_out_len, "ERR");
        }
    } else if (sscanf(line, "SET CURVE %d %f", &curve_pwm_pct, &val) == 2) {
        handled = true;
        if (s_cfg.set_curve_point &&
            curve_pwm_pct >= 10 && curve_pwm_pct <= 100 &&
            (curve_pwm_pct % 10) == 0 &&
            val >= 0.0f) {
            s_cfg.set_curve_point(s_cfg.ctx, (uint8_t)((curve_pwm_pct / 10) - 1), val);
            ok = true;
        }
    } else if (strcmp(line, "SAVE CURVE") == 0) {
        traction_comm_curve_state_t st = {0};
        handled = true;
        if (!get_curve_snapshot(&st) || !s_cfg.enqueue_curve_save) {
            ok = false;
            if (response_out && response_out_len > 0U) {
                snprintf(response_out, response_out_len, "ERR");
            }
        } else if (s_cfg.enqueue_curve_save(s_cfg.ctx, &st)) {
            ok = true;
            if (response_out && response_out_len > 0U) {
                snprintf(response_out, response_out_len, "S,ENQ");
            }
        } else {
            ok = false;
            if (response_out && response_out_len > 0U) {
                snprintf(response_out, response_out_len, "ERR");
            }
        }
    } else {
        handled = false;
    }

    if (handled && response_out && response_out_len > 0U && response_out[0] == '\0') {
        snprintf(response_out, response_out_len, ok ? "OK" : "ERR");
    }
    if (ok_out) {
        *ok_out = ok;
    }
    return handled;
}

static bool get_controller_cfg_snapshot(traction_comm_controller_cfg_state_t *st)
{
    if (!st || !s_cfg.get_controller_cfg_state) return false;
    memset(st, 0, sizeof(*st));
    return s_cfg.get_controller_cfg_state(s_cfg.ctx, st);
}

static void send_controller_cfg_snapshot(void)
{
    traction_comm_controller_cfg_state_t st = {0};
    if (!get_controller_cfg_snapshot(&st)) {
        traction_comm_send_line("ERR");
        return;
    }
    st.notes[sizeof(st.notes) - 1U] = '\0';
    traction_comm_send_line("CFG,%u,%u,%u,%u,%u,%lu,%ld,%ld,%.2f",
                            (unsigned)st.bridge_type,
                            (unsigned)st.encoder_mode,
                            (unsigned)st.motor_invert,
                            (unsigned)st.encoder_invert,
                            (unsigned)st.pullup_enabled,
                            (unsigned long)st.pwm_freq_hz,
                            (long)st.counts_per_motor_rev,
                            (long)st.gear_ratio,
                            (double)st.rpm_max);
    traction_comm_send_line("CFGN,%s", st.notes);
}

static bool format_controller_cfg_snapshot(char *out, size_t out_len)
{
    traction_comm_controller_cfg_state_t st = {0};
    if (!out || out_len == 0U) {
        return false;
    }
    if (!get_controller_cfg_snapshot(&st)) {
        out[0] = '\0';
        return false;
    }

    st.notes[sizeof(st.notes) - 1U] = '\0';
    int n = snprintf(
        out,
        out_len,
        "CFG,%u,%u,%u,%u,%u,%lu,%ld,%ld,%.2f|CFGN,%s",
        (unsigned)st.bridge_type,
        (unsigned)st.encoder_mode,
        (unsigned)st.motor_invert,
        (unsigned)st.encoder_invert,
        (unsigned)st.pullup_enabled,
        (unsigned long)st.pwm_freq_hz,
        (long)st.counts_per_motor_rev,
        (long)st.gear_ratio,
        (double)st.rpm_max,
        st.notes
    );
    return n > 0 && (size_t)n < out_len;
}

static bool try_apply_controller_cfg_command(
    const char *line,
    char *response_out,
    size_t response_out_len,
    bool *ok_out
)
{
    int ivalue = 0;
    float val = 0.0f;
    bool ok = false;
    bool handled = false;

    if (response_out && response_out_len > 0U) {
        response_out[0] = '\0';
    }
    if (!line) {
        if (ok_out) {
            *ok_out = false;
        }
        return false;
    }

    if (strcmp(line, "GET CFG") == 0) {
        handled = true;
        ok = format_controller_cfg_snapshot(response_out, response_out_len);
        if (!ok && response_out && response_out_len > 0U) {
            snprintf(response_out, response_out_len, "ERR");
        }
    } else if (strncmp(line, "SET CFG ", 8) == 0) {
        traction_comm_controller_cfg_state_t st = {0};
        const char *cfg_line = line + 8;
        handled = true;
        if (!get_controller_cfg_snapshot(&st) || !s_cfg.set_controller_cfg_state) {
            ok = false;
        } else if (sscanf(cfg_line, "BRIDGE %d", &ivalue) == 1) {
            st.bridge_type = (uint8_t)ivalue;
            ok = s_cfg.set_controller_cfg_state(s_cfg.ctx, &st);
        } else if (sscanf(cfg_line, "ENCODER_MODE %d", &ivalue) == 1) {
            st.encoder_mode = (uint8_t)ivalue;
            ok = s_cfg.set_controller_cfg_state(s_cfg.ctx, &st);
        } else if (sscanf(cfg_line, "MOTOR_INV %d", &ivalue) == 1) {
            st.motor_invert = (uint8_t)(ivalue != 0);
            ok = s_cfg.set_controller_cfg_state(s_cfg.ctx, &st);
        } else if (sscanf(cfg_line, "ENCODER_INV %d", &ivalue) == 1) {
            st.encoder_invert = (uint8_t)(ivalue != 0);
            ok = s_cfg.set_controller_cfg_state(s_cfg.ctx, &st);
        } else if (sscanf(cfg_line, "PULLUP %d", &ivalue) == 1) {
            st.pullup_enabled = (uint8_t)(ivalue != 0);
            ok = s_cfg.set_controller_cfg_state(s_cfg.ctx, &st);
        } else if (sscanf(cfg_line, "PWM_FREQ %d", &ivalue) == 1) {
            st.pwm_freq_hz = (uint32_t)ivalue;
            ok = s_cfg.set_controller_cfg_state(s_cfg.ctx, &st);
        } else if (sscanf(cfg_line, "COUNTS_PER_REV %d", &ivalue) == 1) {
            st.counts_per_motor_rev = ivalue;
            ok = s_cfg.set_controller_cfg_state(s_cfg.ctx, &st);
        } else if (sscanf(cfg_line, "GEAR_RATIO %d", &ivalue) == 1) {
            st.gear_ratio = ivalue;
            ok = s_cfg.set_controller_cfg_state(s_cfg.ctx, &st);
        } else if (sscanf(cfg_line, "RPM_MAX %f", &val) == 1) {
            st.rpm_max = val;
            ok = s_cfg.set_controller_cfg_state(s_cfg.ctx, &st);
        } else if (strncmp(cfg_line, "NOTES ", 6) == 0) {
            snprintf(st.notes, sizeof(st.notes), "%s", cfg_line + 6);
            ok = s_cfg.set_controller_cfg_state(s_cfg.ctx, &st);
        } else {
            ok = false;
        }
    } else if (strcmp(line, "SAVE CFG") == 0) {
        traction_comm_controller_cfg_state_t st = {0};
        handled = true;
        if (!get_controller_cfg_snapshot(&st) || !s_cfg.enqueue_controller_cfg_save) {
            ok = false;
            if (response_out && response_out_len > 0U) {
                snprintf(response_out, response_out_len, "ERR");
            }
        } else if (s_cfg.enqueue_controller_cfg_save(s_cfg.ctx, &st)) {
            ok = true;
            if (response_out && response_out_len > 0U) {
                snprintf(response_out, response_out_len, "S,ENQ");
            }
        } else {
            ok = false;
            if (response_out && response_out_len > 0U) {
                snprintf(response_out, response_out_len, "ERR");
            }
        }
    } else {
        handled = false;
    }

    if (handled && response_out && response_out_len > 0U && response_out[0] == '\0') {
        snprintf(response_out, response_out_len, ok ? "OK" : "ERR");
    }
    if (ok_out) {
        *ok_out = ok;
    }
    return handled;
}

static void send_control_frame_payload(const uint8_t *payload, size_t payload_len)
{
    if (!payload || payload_len == 0U) return;

    uint8_t frame[FRAME_SYNC_LEN + 1U + 80U];
    if (payload_len > (sizeof(frame) - FRAME_SYNC_LEN - 1U)) {
        payload_len = sizeof(frame) - FRAME_SYNC_LEN - 1U;
    }

    size_t idx = 0U;
    memcpy(&frame[idx], s_frame_sync, FRAME_SYNC_LEN);
    idx += FRAME_SYNC_LEN;
    frame[idx++] = TRACTION_MODULE_ID;
    memcpy(&frame[idx], payload, payload_len);
    idx += payload_len;
    write_bytes_locked(frame, idx);
}

static void send_module_info_frame(void)
{
    const char *name = FRAME_MODULE_NAME;
    size_t name_len = strlen(name);
    if (name_len > 64U) {
        name_len = 64U;
    }

    uint8_t payload[2U + 64U];
    payload[0] = FRAME_MODULE_INFO_PREFIX_BYTE;
    payload[1] = (uint8_t)name_len;
    if (name_len > 0U) {
        memcpy(&payload[2], name, name_len);
    }
    send_control_frame_payload(payload, 2U + name_len);
}

static void send_stream_frame_text(uint8_t msg_type, uint32_t seq, const char *text)
{
    const char *safe = text;
    size_t msg_len = (safe != NULL) ? strlen(safe) : 0U;
    if (msg_len == 0U) {
        safe = " ";
        msg_len = 1U;
    }
    if (msg_len > FRAME_RX_MAX_LEN) {
        msg_len = FRAME_RX_MAX_LEN;
    }

    uint8_t frame[FRAME_SYNC_LEN + 1U + FRAME_RX_MAX_LEN + 1U + FRAME_SEQ_BYTES];
    size_t idx = 0U;
    memcpy(&frame[idx], s_frame_sync, FRAME_SYNC_LEN);
    idx += FRAME_SYNC_LEN;
    frame[idx++] = (uint8_t)msg_len;
    memcpy(&frame[idx], safe, msg_len);
    idx += msg_len;
    frame[idx++] = msg_type;

    uint32_t seq24 = (seq & FRAME_SEQ_MASK);
    frame[idx++] = (uint8_t)((seq24 >> 16U) & 0xFFU);
    frame[idx++] = (uint8_t)((seq24 >> 8U) & 0xFFU);
    frame[idx++] = (uint8_t)(seq24 & 0xFFU);

    write_bytes_locked(frame, idx);
}

static bool try_apply_traction_out_command(const char *line, bool *ok_out)
{
    float val = 0.0f;
    bool ok = false;

    if (!line) {
        if (ok_out) {
            *ok_out = false;
        }
        return false;
    }

    if (sscanf(line, "SET OUT RAW %f", &val) == 1) {
        if (s_cfg.set_force_output_raw) {
            s_cfg.set_force_output_raw(s_cfg.ctx, val);
            ok = true;
        }
        if (ok_out) {
            *ok_out = ok;
        }
        return true;
    }

    if (sscanf(line, "SET OUT %f", &val) == 1) {
        if (s_cfg.set_force_output) {
            s_cfg.set_force_output(s_cfg.ctx, val);
            ok = true;
        }
        if (ok_out) {
            *ok_out = ok;
        }
        return true;
    }

    if (strncmp(line, "CLR OUT", 7) == 0) {
        if (s_cfg.clear_force_output) {
            s_cfg.clear_force_output(s_cfg.ctx);
            ok = true;
        }
        if (ok_out) {
            *ok_out = ok;
        }
        return true;
    }

    if (ok_out) {
        *ok_out = false;
    }
    return false;
}

static void handle_control_frame(const uint8_t *frame_payload, size_t payload_len)
{
    if (!frame_payload || payload_len < 2U) {
        return;
    }

    const uint8_t module_id = frame_payload[0];
    const uint8_t cmd = frame_payload[1];
    if (module_id != HOST_MODULE_ID) {
        return;
    }

    if (cmd == FRAME_HELLO_BYTE) {
        const uint8_t ack = FRAME_HELLO_ACK_BYTE;
        send_control_frame_payload(&ack, 1U);
        return;
    }

    if (cmd == FRAME_MODULE_QUERY_BYTE) {
        send_module_info_frame();
        return;
    }
}

static void handle_stream_frame(const uint8_t *frame_payload, size_t payload_len)
{
    if (!frame_payload || payload_len < (1U + 1U + FRAME_SEQ_BYTES)) {
        return;
    }

    const uint8_t msg_len = frame_payload[0];
    const size_t expected = 1U + (size_t)msg_len + 1U + FRAME_SEQ_BYTES;
    if (msg_len == 0U || msg_len > FRAME_RX_MAX_LEN || payload_len != expected) {
        return;
    }

    const char *msg_ptr = (const char *)&frame_payload[1];
    char msg_text[FRAME_RX_MAX_LEN + 1U];
    memcpy(msg_text, msg_ptr, msg_len);
    msg_text[msg_len] = '\0';

    const uint8_t msg_type = frame_payload[1U + msg_len];
    const size_t seq_offset = 1U + (size_t)msg_len + 1U;
    uint32_t seq = ((uint32_t)frame_payload[seq_offset] << 16U) |
                   ((uint32_t)frame_payload[seq_offset + 1U] << 8U) |
                   ((uint32_t)frame_payload[seq_offset + 2U]);

    if (msg_type == FRAME_MSG_TYPE_CMD) {
        char cmd_response[FRAME_RX_MAX_LEN + 1U];
        bool cmd_ok = false;
        bool handled = try_apply_rpm_pid_command(
            msg_text,
            cmd_response,
            sizeof(cmd_response),
            &cmd_ok
        );
        if (!handled) {
            handled = try_apply_pos_pid_command(
                msg_text,
                cmd_response,
                sizeof(cmd_response),
                &cmd_ok
            );
        }
        if (!handled) {
            handled = try_apply_invert_bridge_command(
                msg_text,
                cmd_response,
                sizeof(cmd_response),
                &cmd_ok
            );
        }
        if (!handled) {
            handled = try_apply_curve_command(
                msg_text,
                cmd_response,
                sizeof(cmd_response),
                &cmd_ok
            );
        }
        if (!handled) {
            handled = try_apply_controller_cfg_command(
                msg_text,
                cmd_response,
                sizeof(cmd_response),
                &cmd_ok
            );
        }
        if (handled) {
            send_stream_frame_text(msg_type, seq, cmd_response);
        } else {
            send_stream_frame_text(msg_type, seq, "I RECIEVED CMD");
        }
        return;
    }

    if (msg_type == FRAME_MSG_TYPE_TEST) {
        send_stream_frame_text(msg_type, seq, "I RECIEVED TEST");
        return;
    }

    if (msg_type == FRAME_MSG_TYPE_TRACTION_OUT) {
        bool traction_cmd_ok = false;
        bool handled = try_apply_traction_out_command(msg_text, &traction_cmd_ok);
        if (handled && traction_cmd_ok) {
            send_stream_frame_text(msg_type, seq, "OK");
        } else {
            send_stream_frame_text(msg_type, seq, "ERR");
        }
        return;
    }

    if (msg_type != FRAME_MSG_TYPE_TELEMETRY) {
        send_stream_frame_text(msg_type, seq, "ERR");
        return;
    }

    if (strncmp(msg_text, "TELEMETRY_START", 15) == 0) {
        s_stream_telem_enabled = true;
        s_stream_telem_last_tx_us = esp_timer_get_time();
        s_stream_telem_seq = 0U;
        send_stream_frame_text(msg_type, seq, "TELEMETRY STARTED");
        return;
    }

    if (strncmp(msg_text, "TELEMETRY_STOP", 14) == 0) {
        s_stream_telem_enabled = false;
        send_stream_frame_text(msg_type, seq, "TELEMETRY STOPPED");
        return;
    }

    if (strncmp(msg_text, "TELEMETRY_SYNC", 14) == 0) {
        send_stream_frame_text(msg_type, seq, "TELEMETRY SYNCED");
        return;
    }

    send_stream_frame_text(msg_type, seq, "TELEMETRY");
}

static void handle_framed_payload(const uint8_t *frame_payload, size_t payload_len)
{
    if (!frame_payload || payload_len == 0U) {
        return;
    }

    if (frame_payload[0] == HOST_MODULE_ID && payload_len >= 2U) {
        handle_control_frame(frame_payload, payload_len);
        return;
    }

    handle_stream_frame(frame_payload, payload_len);
}

static void maybe_send_stream_telemetry(void)
{
    if (!s_stream_telem_enabled) {
        return;
    }

    const int64_t now_us = esp_timer_get_time();
    if ((now_us - s_stream_telem_last_tx_us) < FRAME_TELEMETRY_TX_PERIOD_US) {
        return;
    }
    s_stream_telem_last_tx_us = now_us;

    char msg[64];
    const int64_t now_ms = now_us / 1000LL;
    int n = snprintf(msg, sizeof(msg), "TELEMETRY DEVICE %lld", (long long)now_ms);
    if (n <= 0) {
        return;
    }
    send_stream_frame_text(FRAME_MSG_TYPE_TELEMETRY, s_stream_telem_seq, msg);
    s_stream_telem_seq = (s_stream_telem_seq + 1U) & FRAME_SEQ_MASK;
}

static void handle_cmd_line(const char *line)
{
    float val = 0.0f;
    int ivalue = 0;
    int curve_pwm_pct = 0;

    if (strcmp(line, "GET TELEM POS") == 0) {
        if (s_cfg.request_pos_telem) {
            s_cfg.request_pos_telem(s_cfg.ctx);
        }
        return;
    }

    if (strncmp(line, "GET TELEM", 9) == 0) {
        if (s_cfg.request_rpm_telem) {
            s_cfg.request_rpm_telem(s_cfg.ctx);
        }
        return;
    }

    if (strcmp(line, "GET PID POS") == 0) {
        send_pos_snapshot();
        return;
    }

    if (strcmp(line, "GET PID POS SINE") == 0) {
        send_pos_sine_snapshot();
        return;
    }

    if ((strcmp(line, "GET PID RPM") == 0) || (strcmp(line, "GET") == 0)) {
        send_rpm_snapshot();
        return;
    }

    if (strcmp(line, "GET INVERT") == 0) {
        send_invert_snapshot();
        return;
    }

    if (strcmp(line, "GET BRIDGE") == 0) {
        send_bridge_snapshot();
        return;
    }

    if (strcmp(line, "GET CURVE") == 0) {
        send_curve_snapshot();
        return;
    }

    if (strcmp(line, "GET CFG") == 0) {
        send_controller_cfg_snapshot();
        return;
    }

    if (sscanf(line, "SET MOTOR INV %d", &ivalue) == 1) {
        if (!s_cfg.set_motor_invert) {
            traction_comm_send_line("ERR");
            return;
        }
        s_cfg.set_motor_invert(s_cfg.ctx, ivalue != 0);
        traction_comm_send_line("OK");
        send_invert_snapshot();
        return;
    }

    if (sscanf(line, "SET BRIDGE %d", &ivalue) == 1) {
        if (!s_cfg.set_bridge_type || ivalue < 0 || ivalue > 1) {
            traction_comm_send_line("ERR");
            return;
        }
        s_cfg.set_bridge_type(s_cfg.ctx, (uint8_t)ivalue);
        traction_comm_send_line("OK");
        send_bridge_snapshot();
        return;
    }

    if (sscanf(line, "SET ENCODER INV %d", &ivalue) == 1) {
        if (!s_cfg.set_encoder_invert) {
            traction_comm_send_line("ERR");
            return;
        }
        s_cfg.set_encoder_invert(s_cfg.ctx, ivalue != 0);
        traction_comm_send_line("OK");
        send_invert_snapshot();
        return;
    }

    if (sscanf(line, "SET CURVE %d %f", &curve_pwm_pct, &val) == 2) {
        if (!s_cfg.set_curve_point ||
            curve_pwm_pct < 10 || curve_pwm_pct > 100 || (curve_pwm_pct % 10) != 0 ||
            val < 0.0f) {
            traction_comm_send_line("ERR");
            return;
        }
        s_cfg.set_curve_point(s_cfg.ctx, (uint8_t)((curve_pwm_pct / 10) - 1), val);
        traction_comm_send_line("OK");
        send_curve_snapshot();
        return;
    }

    if (strncmp(line, "SET CFG ", 8) == 0) {
        traction_comm_controller_cfg_state_t st = {0};
        if (!get_controller_cfg_snapshot(&st) || !s_cfg.set_controller_cfg_state) {
            traction_comm_send_line("ERR");
            return;
        }

        const char *cfg_line = line + 8;
        if (sscanf(cfg_line, "BRIDGE %d", &ivalue) == 1) {
            st.bridge_type = (uint8_t)ivalue;
        } else if (sscanf(cfg_line, "ENCODER_MODE %d", &ivalue) == 1) {
            st.encoder_mode = (uint8_t)ivalue;
        } else if (sscanf(cfg_line, "MOTOR_INV %d", &ivalue) == 1) {
            st.motor_invert = (uint8_t)(ivalue != 0);
        } else if (sscanf(cfg_line, "ENCODER_INV %d", &ivalue) == 1) {
            st.encoder_invert = (uint8_t)(ivalue != 0);
        } else if (sscanf(cfg_line, "PULLUP %d", &ivalue) == 1) {
            st.pullup_enabled = (uint8_t)(ivalue != 0);
        } else if (sscanf(cfg_line, "PWM_FREQ %d", &ivalue) == 1) {
            st.pwm_freq_hz = (uint32_t)ivalue;
        } else if (sscanf(cfg_line, "COUNTS_PER_REV %d", &ivalue) == 1) {
            st.counts_per_motor_rev = ivalue;
        } else if (sscanf(cfg_line, "GEAR_RATIO %d", &ivalue) == 1) {
            st.gear_ratio = ivalue;
        } else if (sscanf(cfg_line, "RPM_MAX %f", &val) == 1) {
            st.rpm_max = val;
        } else if (strncmp(cfg_line, "NOTES ", 6) == 0) {
            snprintf(st.notes, sizeof(st.notes), "%s", cfg_line + 6);
        } else {
            traction_comm_send_line("ERR");
            return;
        }

        if (!s_cfg.set_controller_cfg_state(s_cfg.ctx, &st)) {
            traction_comm_send_line("ERR");
            return;
        }
        traction_comm_send_line("OK");
        send_controller_cfg_snapshot();
        return;
    }

    if ((sscanf(line, "SET PID RPM KP %f", &val) == 1) ||
        (sscanf(line, "SET KP %f", &val) == 1)) {
        if (!s_cfg.set_rpm_kp) {
            traction_comm_send_line("ERR");
            return;
        }
        s_cfg.set_rpm_kp(s_cfg.ctx, val);
        traction_comm_send_line("OK");
        send_rpm_snapshot();
        return;
    }

    if ((sscanf(line, "SET PID RPM KI %f", &val) == 1) ||
        (sscanf(line, "SET KI %f", &val) == 1)) {
        if (!s_cfg.set_rpm_ki) {
            traction_comm_send_line("ERR");
            return;
        }
        s_cfg.set_rpm_ki(s_cfg.ctx, val);
        traction_comm_send_line("OK");
        send_rpm_snapshot();
        return;
    }

    if ((sscanf(line, "SET PID RPM KD %f", &val) == 1) ||
        (sscanf(line, "SET KD %f", &val) == 1)) {
        if (!s_cfg.set_rpm_kd) {
            traction_comm_send_line("ERR");
            return;
        }
        s_cfg.set_rpm_kd(s_cfg.ctx, val);
        traction_comm_send_line("OK");
        send_rpm_snapshot();
        return;
    }

    if ((sscanf(line, "SET PID RPM SP %f", &val) == 1) ||
        (sscanf(line, "SET SP %f", &val) == 1)) {
        if (!s_cfg.set_rpm_setpoint) {
            traction_comm_send_line("ERR");
            return;
        }
        s_cfg.set_rpm_setpoint(s_cfg.ctx, val);
        traction_comm_send_line("OK");
        send_rpm_snapshot();
        return;
    }

    if (sscanf(line, "SET PID POS KP %f", &val) == 1) {
        if (!s_cfg.set_pos_kp) {
            traction_comm_send_line("ERR");
            return;
        }
        s_cfg.set_pos_kp(s_cfg.ctx, val);
        traction_comm_send_line("OK");
        send_pos_snapshot();
        return;
    }

    if (sscanf(line, "SET PID POS KI %f", &val) == 1) {
        if (!s_cfg.set_pos_ki) {
            traction_comm_send_line("ERR");
            return;
        }
        s_cfg.set_pos_ki(s_cfg.ctx, val);
        traction_comm_send_line("OK");
        send_pos_snapshot();
        return;
    }

    if (sscanf(line, "SET PID POS KD %f", &val) == 1) {
        if (!s_cfg.set_pos_kd) {
            traction_comm_send_line("ERR");
            return;
        }
        s_cfg.set_pos_kd(s_cfg.ctx, val);
        traction_comm_send_line("OK");
        send_pos_snapshot();
        return;
    }

    if (sscanf(line, "SET PID POS IWIN %f", &val) == 1) {
        if (!s_cfg.set_pos_integral_window_deg) {
            traction_comm_send_line("ERR");
            return;
        }
        s_cfg.set_pos_integral_window_deg(s_cfg.ctx, val);
        traction_comm_send_line("OK");
        send_pos_snapshot();
        return;
    }

    if (sscanf(line, "SET PID POS ANGLE %f", &val) == 1 ||
        sscanf(line, "SET PID POS TARGET %f", &val) == 1) {
        if (!s_cfg.set_pos_target_deg) {
            traction_comm_send_line("ERR");
            return;
        }
        s_cfg.set_pos_target_deg(s_cfg.ctx, val);
        traction_comm_send_line("OK");
        send_pos_snapshot();
        return;
    }

    if (strcmp(line, "START PID POS") == 0) {
        if (!s_cfg.set_pos_enabled) {
            traction_comm_send_line("ERR");
            return;
        }
        s_cfg.set_pos_enabled(s_cfg.ctx, true);
        traction_comm_send_line("OK");
        send_pos_snapshot();
        return;
    }

    if (sscanf(line, "SET PID POS SINE AMP %f", &val) == 1) {
        if (!s_cfg.set_pos_sine_amp_deg) {
            traction_comm_send_line("ERR");
            return;
        }
        s_cfg.set_pos_sine_amp_deg(s_cfg.ctx, val);
        traction_comm_send_line("OK");
        send_pos_sine_snapshot();
        return;
    }

    if (sscanf(line, "SET PID POS SINE OFFSET %f", &val) == 1) {
        if (!s_cfg.set_pos_sine_offset_deg) {
            traction_comm_send_line("ERR");
            return;
        }
        s_cfg.set_pos_sine_offset_deg(s_cfg.ctx, val);
        traction_comm_send_line("OK");
        send_pos_sine_snapshot();
        return;
    }

    if (sscanf(line, "SET PID POS SINE PERIOD %f", &val) == 1) {
        if (!s_cfg.set_pos_sine_period_s) {
            traction_comm_send_line("ERR");
            return;
        }
        s_cfg.set_pos_sine_period_s(s_cfg.ctx, val);
        traction_comm_send_line("OK");
        send_pos_sine_snapshot();
        return;
    }

    if (strcmp(line, "START PID POS SINE") == 0) {
        if (!s_cfg.set_pos_sine_enabled) {
            traction_comm_send_line("ERR");
            return;
        }
        s_cfg.set_pos_sine_enabled(s_cfg.ctx, true);
        traction_comm_send_line("OK");
        send_pos_sine_snapshot();
        return;
    }

    if (strcmp(line, "STOP PID POS SINE") == 0) {
        if (!s_cfg.set_pos_sine_enabled) {
            traction_comm_send_line("ERR");
            return;
        }
        s_cfg.set_pos_sine_enabled(s_cfg.ctx, false);
        traction_comm_send_line("OK");
        send_pos_sine_snapshot();
        return;
    }

    if (strcmp(line, "STOP PID POS") == 0) {
        if (!s_cfg.set_pos_enabled) {
            traction_comm_send_line("ERR");
            return;
        }
        s_cfg.set_pos_enabled(s_cfg.ctx, false);
        traction_comm_send_line("OK");
        send_pos_snapshot();
        return;
    }

    if (strcmp(line, "SAVE PID POS") == 0) {
        traction_comm_pid_pos_state_t st = {0};
        if (!get_pos_snapshot(&st) || !s_cfg.enqueue_pos_save) {
            traction_comm_send_line("ERR");
            traction_comm_send_line("E,%d", (int)ESP_ERR_INVALID_STATE);
            return;
        }
        if (s_cfg.enqueue_pos_save(s_cfg.ctx, &st)) {
            traction_comm_send_line("S,ENQ");
            traction_comm_send_line("OK");
        } else {
            traction_comm_send_line("ERR");
            traction_comm_send_line("E,%d", (int)ESP_ERR_INVALID_STATE);
        }
        return;
    }

    bool traction_cmd_ok = false;
    if (try_apply_traction_out_command(line, &traction_cmd_ok)) {
        traction_comm_send_line(traction_cmd_ok ? "OK" : "ERR");
        return;
    }

    if ((strcmp(line, "SAVE PID RPM") == 0) || (strcmp(line, "SAVE") == 0)) {
        traction_comm_pid_rpm_state_t st = {0};
        if (!get_rpm_snapshot(&st) || !s_cfg.enqueue_rpm_save) {
            traction_comm_send_line("ERR");
            traction_comm_send_line("E,%d", (int)ESP_ERR_INVALID_STATE);
            return;
        }
        if (s_cfg.enqueue_rpm_save(s_cfg.ctx, &st)) {
            traction_comm_send_line("S,ENQ");
            traction_comm_send_line("OK");
        } else {
            traction_comm_send_line("ERR");
            traction_comm_send_line("E,%d", (int)ESP_ERR_INVALID_STATE);
        }
        return;
    }

    if (strcmp(line, "SAVE CURVE") == 0) {
        traction_comm_curve_state_t st = {0};
        if (!get_curve_snapshot(&st) || !s_cfg.enqueue_curve_save) {
            traction_comm_send_line("ERR");
            traction_comm_send_line("E,%d", (int)ESP_ERR_INVALID_STATE);
            return;
        }
        if (s_cfg.enqueue_curve_save(s_cfg.ctx, &st)) {
            traction_comm_send_line("S,ENQ");
            traction_comm_send_line("OK");
        } else {
            traction_comm_send_line("ERR");
            traction_comm_send_line("E,%d", (int)ESP_ERR_INVALID_STATE);
        }
        return;
    }

    if (strcmp(line, "SAVE CFG") == 0) {
        traction_comm_controller_cfg_state_t st = {0};
        if (!get_controller_cfg_snapshot(&st) || !s_cfg.enqueue_controller_cfg_save) {
            traction_comm_send_line("ERR");
            traction_comm_send_line("E,%d", (int)ESP_ERR_INVALID_STATE);
            return;
        }
        if (s_cfg.enqueue_controller_cfg_save(s_cfg.ctx, &st)) {
            traction_comm_send_line("S,ENQ");
            traction_comm_send_line("OK");
        } else {
            traction_comm_send_line("ERR");
            traction_comm_send_line("E,%d", (int)ESP_ERR_INVALID_STATE);
        }
        return;
    }

    traction_comm_send_line("ERR");
}

esp_err_t traction_comm_init(const traction_comm_cfg_t *cfg)
{
    if (!cfg) return ESP_ERR_INVALID_ARG;

    s_cfg = *cfg;
    if (s_cfg.link_timeout_ms == 0) {
        s_cfg.link_timeout_ms = TRACTION_COMM_DEFAULT_LINK_TIMEOUT_MS;
    }

    if (!s_tx_mutex) {
        s_tx_mutex = xSemaphoreCreateMutex();
        if (!s_tx_mutex) {
            return ESP_ERR_NO_MEM;
        }
    }

    uart_config_t uart_cfg = {
        .baud_rate = 115200,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    ESP_ERROR_CHECK(uart_param_config(UART_NUM_0, &uart_cfg));
    ESP_ERROR_CHECK(uart_set_pin(UART_NUM_0, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));

    esp_err_t uerr = uart_driver_install(UART_NUM_0, 1024, 0, 0, NULL, 0);
    if (uerr != ESP_OK && uerr != ESP_ERR_INVALID_STATE) {
        ESP_LOGW(TAG, "uart install failed (%d)", (int)uerr);
        return uerr;
    }
    esp_vfs_dev_uart_use_driver(UART_NUM_0);

    usb_serial_jtag_driver_config_t usb_cfg = {
        .rx_buffer_size = 1024,
        .tx_buffer_size = 1024,
    };
    esp_err_t jerr = usb_serial_jtag_driver_install(&usb_cfg);
    if (jerr == ESP_ERR_INVALID_STATE) {
        /*
         * Console configuration can pre-install USB JTAG in a TX-only path.
         * Reinstall with explicit RX/TX buffers so host->device commands work.
         */
        usb_serial_jtag_driver_uninstall();
        jerr = usb_serial_jtag_driver_install(&usb_cfg);
    }
    if (jerr == ESP_OK) {
        s_usb_jtag_ready = true;
    } else {
        s_usb_jtag_ready = false;
        ESP_LOGW(TAG, "usb serial jtag install failed (%d)", (int)jerr);
    }

    ESP_LOGI(TAG, "line fallback %s",
             (TRACTION_COMM_ENABLE_LINE_FALLBACK != 0) ? "ENABLED" : "DISABLED");

    s_inited = true;
    return ESP_OK;
}

void traction_comm_task(void *arg)
{
    (void)arg;

    char line[128];
    size_t line_len = 0;
    uint8_t framed_buf[1U + FRAME_RX_MAX_LEN + 1U + FRAME_SEQ_BYTES];
    size_t framed_len = 0U;
    size_t framed_expected = 0U;
    size_t sync_match = 0U;
    bool collecting_framed = false;
    int64_t last_rx_us = 0;
    bool serial_link_active = false;

    while (1) {
        bool got_line = false;

        uint8_t ch = 0;
        int n = 0;
        if (s_usb_jtag_ready) {
            n = usb_serial_jtag_read_bytes(&ch, 1, 0);
        }
        if (n > 0) {
            last_rx_us = esp_timer_get_time();
            serial_link_active = true;
            bool feed_line = true;

            if (collecting_framed) {
                feed_line = false;
                if (framed_len < sizeof(framed_buf)) {
                    framed_buf[framed_len++] = ch;
                } else {
                    collecting_framed = false;
                    framed_len = 0U;
                    framed_expected = 0U;
                }

                if (collecting_framed && framed_len == 1U) {
                    if (framed_buf[0] == HOST_MODULE_ID) {
                        framed_expected = 2U;
                    } else if (framed_buf[0] > 0U && framed_buf[0] <= FRAME_RX_MAX_LEN) {
                        framed_expected = 1U + (size_t)framed_buf[0] + 1U + FRAME_SEQ_BYTES;
                    } else {
                        collecting_framed = false;
                        framed_len = 0U;
                        framed_expected = 0U;
                    }
                    if (framed_expected > sizeof(framed_buf)) {
                        collecting_framed = false;
                        framed_len = 0U;
                        framed_expected = 0U;
                    }
                }

                if (collecting_framed && framed_expected > 0U && framed_len >= framed_expected) {
                    handle_framed_payload(framed_buf, framed_len);
                    collecting_framed = false;
                    framed_len = 0U;
                    framed_expected = 0U;
                }
            } else if (ch == s_frame_sync[sync_match]) {
                feed_line = false;
                sync_match++;
                if (sync_match >= FRAME_SYNC_LEN) {
                    sync_match = 0U;
                    collecting_framed = true;
                    framed_len = 0U;
                    framed_expected = 0U;
                }
            } else if (sync_match > 0U) {
                feed_line = false;
                sync_match = (ch == s_frame_sync[0]) ? 1U : 0U;
            }

            if (feed_line && (TRACTION_COMM_ENABLE_LINE_FALLBACK != 0)) {
                got_line = feed_char_cmd((char)ch, line, &line_len, sizeof(line));
            }
        } else {
            n = uart_read_bytes(UART_NUM_0, &ch, 1, pdMS_TO_TICKS(20));
            if (n > 0) {
                last_rx_us = esp_timer_get_time();
                serial_link_active = true;
                bool feed_line = true;

                if (collecting_framed) {
                    feed_line = false;
                    if (framed_len < sizeof(framed_buf)) {
                        framed_buf[framed_len++] = ch;
                    } else {
                        collecting_framed = false;
                        framed_len = 0U;
                        framed_expected = 0U;
                    }

                    if (collecting_framed && framed_len == 1U) {
                        if (framed_buf[0] == HOST_MODULE_ID) {
                            framed_expected = 2U;
                        } else if (framed_buf[0] > 0U && framed_buf[0] <= FRAME_RX_MAX_LEN) {
                            framed_expected = 1U + (size_t)framed_buf[0] + 1U + FRAME_SEQ_BYTES;
                        } else {
                            collecting_framed = false;
                            framed_len = 0U;
                            framed_expected = 0U;
                        }
                        if (framed_expected > sizeof(framed_buf)) {
                            collecting_framed = false;
                            framed_len = 0U;
                            framed_expected = 0U;
                        }
                    }

                    if (collecting_framed && framed_expected > 0U && framed_len >= framed_expected) {
                        handle_framed_payload(framed_buf, framed_len);
                        collecting_framed = false;
                        framed_len = 0U;
                        framed_expected = 0U;
                    }
                } else if (ch == s_frame_sync[sync_match]) {
                    feed_line = false;
                    sync_match++;
                    if (sync_match >= FRAME_SYNC_LEN) {
                        sync_match = 0U;
                        collecting_framed = true;
                        framed_len = 0U;
                        framed_expected = 0U;
                    }
                } else if (sync_match > 0U) {
                    feed_line = false;
                    sync_match = (ch == s_frame_sync[0]) ? 1U : 0U;
                }

                if (feed_line && (TRACTION_COMM_ENABLE_LINE_FALLBACK != 0)) {
                    got_line = feed_char_cmd((char)ch, line, &line_len, sizeof(line));
                }
            }
        }

        maybe_send_stream_telemetry();

        if (serial_link_active && s_cfg.link_timeout_ms > 0) {
            int64_t now_us = esp_timer_get_time();
            if ((now_us - last_rx_us) > ((int64_t)s_cfg.link_timeout_ms * 1000LL)) {
                serial_link_active = false;
                if (s_cfg.on_link_timeout) {
                    s_cfg.on_link_timeout(s_cfg.ctx);
                }
                ESP_LOGW(TAG, "serial link timeout");
            }
        }

        if (!got_line) {
            continue;
        }

        if (TRACTION_COMM_ENABLE_LINE_FALLBACK != 0) {
            handle_cmd_line(line);
        }
    }
}
