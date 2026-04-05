#include "traction_storage.h"

#include "nvs.h"
#include "nvs_flash.h"
#include "esp_log.h"

static const char *TAG = "storage";
static const char *NVS_NAMESPACE = "pid";
static const char *KEY_PID_RPM = "pid";
static const char *KEY_PID_POS = "pid_pos";
static const char *KEY_INVERT = "invert";
static const char *KEY_BRIDGE = "bridge";
static const char *KEY_CURVE = "curve";
static const char *KEY_CONTROLLER_CFG = "cfg";

static bool s_nvs_ready = false;

typedef struct {
    float kp;
    float ki;
    float kd;
    float target_rev;
} traction_pos_pid_store_legacy_t;

esp_err_t traction_storage_init(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        err = nvs_flash_init();
    }
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "nvs init failed (%d)", (int)err);
        s_nvs_ready = false;
        return err;
    }
    s_nvs_ready = true;
    return ESP_OK;
}

bool traction_storage_is_ready(void)
{
    return s_nvs_ready;
}

esp_err_t traction_storage_load_pid(traction_pid_store_t *out)
{
    if (!out) return ESP_ERR_INVALID_ARG;
    if (!s_nvs_ready) return ESP_ERR_INVALID_STATE;

    nvs_handle_t h = 0;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READONLY, &h);
    if (err != ESP_OK) {
        return err;
    }

    size_t len = sizeof(*out);
    err = nvs_get_blob(h, KEY_PID_RPM, out, &len);
    nvs_close(h);

    if (err == ESP_OK && len != sizeof(*out)) {
        return ESP_ERR_INVALID_SIZE;
    }
    return err;
}

esp_err_t traction_storage_save_pid(const traction_pid_store_t *in)
{
    if (!in) return ESP_ERR_INVALID_ARG;
    if (!s_nvs_ready) return ESP_ERR_INVALID_STATE;

    nvs_handle_t h = 0;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        return err;
    }

    err = nvs_set_blob(h, KEY_PID_RPM, in, sizeof(*in));
    if (err == ESP_OK) {
        err = nvs_commit(h);
    }
    nvs_close(h);
    return err;
}

esp_err_t traction_storage_load_pos_pid(traction_pos_pid_store_t *out)
{
    if (!out) return ESP_ERR_INVALID_ARG;
    if (!s_nvs_ready) return ESP_ERR_INVALID_STATE;

    nvs_handle_t h = 0;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READONLY, &h);
    if (err != ESP_OK) {
        return err;
    }

    size_t len = 0;
    err = nvs_get_blob(h, KEY_PID_POS, NULL, &len);
    if (err != ESP_OK) {
        nvs_close(h);
        return err;
    }

    if (len == sizeof(*out)) {
        err = nvs_get_blob(h, KEY_PID_POS, out, &len);
        nvs_close(h);
        if (err != ESP_OK) {
            return err;
        }
        if (out->version != TRACTION_POS_PID_STORE_VERSION) {
            return ESP_ERR_INVALID_VERSION;
        }
        return ESP_OK;
    }

    if (len == sizeof(traction_pos_pid_store_legacy_t)) {
        traction_pos_pid_store_legacy_t legacy = {0};
        err = nvs_get_blob(h, KEY_PID_POS, &legacy, &len);
        nvs_close(h);
        if (err != ESP_OK) {
            return err;
        }
        out->version = TRACTION_POS_PID_STORE_VERSION;
        out->reserved0 = 0U;
        out->kp = legacy.kp;
        out->ki = legacy.ki;
        out->kd = legacy.kd;
        out->target_deg = legacy.target_rev * 360.0f;
        return ESP_OK;
    }
    nvs_close(h);
    return ESP_ERR_INVALID_SIZE;
}

esp_err_t traction_storage_save_pos_pid(const traction_pos_pid_store_t *in)
{
    if (!in) return ESP_ERR_INVALID_ARG;
    if (!s_nvs_ready) return ESP_ERR_INVALID_STATE;

    nvs_handle_t h = 0;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        return err;
    }

    traction_pos_pid_store_t st = *in;
    st.version = TRACTION_POS_PID_STORE_VERSION;
    st.reserved0 = 0U;

    err = nvs_set_blob(h, KEY_PID_POS, &st, sizeof(st));
    if (err == ESP_OK) {
        err = nvs_commit(h);
    }
    nvs_close(h);
    return err;
}

esp_err_t traction_storage_load_invert(traction_invert_store_t *out)
{
    if (!out) return ESP_ERR_INVALID_ARG;
    if (!s_nvs_ready) return ESP_ERR_INVALID_STATE;

    nvs_handle_t h = 0;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READONLY, &h);
    if (err != ESP_OK) {
        return err;
    }

    size_t len = sizeof(*out);
    err = nvs_get_blob(h, KEY_INVERT, out, &len);
    nvs_close(h);

    if (err == ESP_OK && len != sizeof(*out)) {
        return ESP_ERR_INVALID_SIZE;
    }
    return err;
}

esp_err_t traction_storage_save_invert(const traction_invert_store_t *in)
{
    if (!in) return ESP_ERR_INVALID_ARG;
    if (!s_nvs_ready) return ESP_ERR_INVALID_STATE;

    nvs_handle_t h = 0;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        return err;
    }

    err = nvs_set_blob(h, KEY_INVERT, in, sizeof(*in));
    if (err == ESP_OK) {
        err = nvs_commit(h);
    }
    nvs_close(h);
    return err;
}

esp_err_t traction_storage_load_bridge(traction_bridge_store_t *out)
{
    if (!out) return ESP_ERR_INVALID_ARG;
    if (!s_nvs_ready) return ESP_ERR_INVALID_STATE;

    nvs_handle_t h = 0;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READONLY, &h);
    if (err != ESP_OK) {
        return err;
    }

    size_t len = sizeof(*out);
    err = nvs_get_blob(h, KEY_BRIDGE, out, &len);
    nvs_close(h);

    if (err == ESP_OK && len != sizeof(*out)) {
        return ESP_ERR_INVALID_SIZE;
    }
    return err;
}

esp_err_t traction_storage_save_bridge(const traction_bridge_store_t *in)
{
    if (!in) return ESP_ERR_INVALID_ARG;
    if (!s_nvs_ready) return ESP_ERR_INVALID_STATE;

    nvs_handle_t h = 0;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        return err;
    }

    err = nvs_set_blob(h, KEY_BRIDGE, in, sizeof(*in));
    if (err == ESP_OK) {
        err = nvs_commit(h);
    }
    nvs_close(h);
    return err;
}

esp_err_t traction_storage_load_curve(traction_curve_store_t *out)
{
    if (!out) return ESP_ERR_INVALID_ARG;
    if (!s_nvs_ready) return ESP_ERR_INVALID_STATE;

    nvs_handle_t h = 0;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READONLY, &h);
    if (err != ESP_OK) {
        return err;
    }

    size_t len = sizeof(*out);
    err = nvs_get_blob(h, KEY_CURVE, out, &len);
    nvs_close(h);

    if (err == ESP_OK && len != sizeof(*out)) {
        return ESP_ERR_INVALID_SIZE;
    }
    return err;
}

esp_err_t traction_storage_save_curve(const traction_curve_store_t *in)
{
    if (!in) return ESP_ERR_INVALID_ARG;
    if (!s_nvs_ready) return ESP_ERR_INVALID_STATE;

    nvs_handle_t h = 0;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        return err;
    }

    err = nvs_set_blob(h, KEY_CURVE, in, sizeof(*in));
    if (err == ESP_OK) {
        err = nvs_commit(h);
    }
    nvs_close(h);
    return err;
}

esp_err_t traction_storage_load_controller_cfg(traction_controller_cfg_store_t *out)
{
    if (!out) return ESP_ERR_INVALID_ARG;
    if (!s_nvs_ready) return ESP_ERR_INVALID_STATE;

    nvs_handle_t h = 0;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READONLY, &h);
    if (err != ESP_OK) {
        return err;
    }

    size_t len = sizeof(*out);
    err = nvs_get_blob(h, KEY_CONTROLLER_CFG, out, &len);
    nvs_close(h);

    if (err == ESP_OK && len != sizeof(*out)) {
        return ESP_ERR_INVALID_SIZE;
    }
    return err;
}

esp_err_t traction_storage_save_controller_cfg(const traction_controller_cfg_store_t *in)
{
    if (!in) return ESP_ERR_INVALID_ARG;
    if (!s_nvs_ready) return ESP_ERR_INVALID_STATE;

    nvs_handle_t h = 0;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        return err;
    }

    err = nvs_set_blob(h, KEY_CONTROLLER_CFG, in, sizeof(*in));
    if (err == ESP_OK) {
        err = nvs_commit(h);
    }
    nvs_close(h);
    return err;
}
