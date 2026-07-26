#ifndef OPENRDKC_OPENRDKC_H
#define OPENRDKC_OPENRDKC_H

#include <stdbool.h>

#include "openrdkc/version.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct ordkc_runtime ordkc_runtime_t;

typedef enum ordkc_result {
    ORDKC_OK = 0,
    ORDKC_ERR_INVALID_ARGUMENT = 1,
    ORDKC_ERR_NO_MEMORY = 2,
    ORDKC_ERR_ALREADY_RUNNING = 3,
    ORDKC_ERR_NOT_RUNNING = 4
} ordkc_result_t;

ordkc_result_t ordkc_runtime_create(ordkc_runtime_t **out_runtime);
ordkc_result_t ordkc_runtime_start(ordkc_runtime_t *runtime);
ordkc_result_t ordkc_runtime_stop(ordkc_runtime_t *runtime);
bool ordkc_runtime_is_running(const ordkc_runtime_t *runtime);
void ordkc_runtime_destroy(ordkc_runtime_t *runtime);

const char *ordkc_version(void);
const char *ordkc_result_name(ordkc_result_t result);

#ifdef __cplusplus
}
#endif

#endif

