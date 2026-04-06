#pragma once

#include "driver/gpio.h"
#include "driver/ledc.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    TRACTION_DIR_CW  = 0,
    TRACTION_DIR_CCW = 1,
} traction_dir_t;

typedef enum {
    TRACTION_MOTOR_BRIDGE_TB6612 = 0,
    TRACTION_MOTOR_BRIDGE_DRV8833 = 1,
} traction_motor_bridge_t;

#ifdef __cplusplus
}
#endif
