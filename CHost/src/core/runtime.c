#include "openrdkc/openrdkc.h"

#include <stdlib.h>

struct ordkc_runtime {
    bool running;
};

ordkc_result_t ordkc_runtime_create(ordkc_runtime_t **out_runtime)
{
    if (out_runtime == NULL) {
        return ORDKC_ERR_INVALID_ARGUMENT;
    }
    *out_runtime = calloc(1U, sizeof(**out_runtime));
    if (*out_runtime == NULL) {
        return ORDKC_ERR_NO_MEMORY;
    }
    return ORDKC_OK;
}

ordkc_result_t ordkc_runtime_start(ordkc_runtime_t *runtime)
{
    if (runtime == NULL) {
        return ORDKC_ERR_INVALID_ARGUMENT;
    }
    if (runtime->running) {
        return ORDKC_ERR_ALREADY_RUNNING;
    }
    runtime->running = true;
    return ORDKC_OK;
}

ordkc_result_t ordkc_runtime_stop(ordkc_runtime_t *runtime)
{
    if (runtime == NULL) {
        return ORDKC_ERR_INVALID_ARGUMENT;
    }
    if (!runtime->running) {
        return ORDKC_ERR_NOT_RUNNING;
    }
    runtime->running = false;
    return ORDKC_OK;
}

bool ordkc_runtime_is_running(const ordkc_runtime_t *runtime)
{
    return runtime != NULL && runtime->running;
}

void ordkc_runtime_destroy(ordkc_runtime_t *runtime)
{
    free(runtime);
}

const char *ordkc_version(void)
{
    return ORDKC_VERSION_STRING;
}

const char *ordkc_result_name(ordkc_result_t result)
{
    switch (result) {
    case ORDKC_OK:
        return "ok";
    case ORDKC_ERR_INVALID_ARGUMENT:
        return "invalid_argument";
    case ORDKC_ERR_NO_MEMORY:
        return "no_memory";
    case ORDKC_ERR_ALREADY_RUNNING:
        return "already_running";
    case ORDKC_ERR_NOT_RUNNING:
        return "not_running";
    default:
        return "unknown";
    }
}

