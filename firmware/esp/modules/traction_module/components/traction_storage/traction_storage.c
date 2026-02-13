#include "traction_storage.h"

#include "nvs.h"
#include "nvs_flash.h"
#include "esp_log.h"

static const char *TAG = "storage";
static const char *NVS_NAMESPACE = "pid";
static const char *KEY_PID_RPM = "pid";
static const char *KEY_PID_POS = "pid_pos";

static bool s_nvs_ready = false;

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

    size_t len = sizeof(*out);
    err = nvs_get_blob(h, KEY_PID_POS, out, &len);
    nvs_close(h);

    if (err == ESP_OK && len != sizeof(*out)) {
        return ESP_ERR_INVALID_SIZE;
    }
    return err;
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

    err = nvs_set_blob(h, KEY_PID_POS, in, sizeof(*in));
    if (err == ESP_OK) {
        err = nvs_commit(h);
    }
    nvs_close(h);
    return err;
}
