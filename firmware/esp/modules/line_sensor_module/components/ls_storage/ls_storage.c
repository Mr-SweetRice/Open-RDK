#include "ls_storage.h"

#include "esp_log.h"
#include "nvs.h"
#include "nvs_flash.h"

static const char *TAG = "ls_storage";
static const char *NVS_NAMESPACE = "ls";
static const char *KEY_CFG = "cfg";
static const char *KEY_CAL = "cal";

static bool s_ready = false;

esp_err_t ls_storage_init(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "nvs init failed (%d)", (int)err);
        s_ready = false;
        return err;
    }
    s_ready = true;
    return ESP_OK;
}

bool ls_storage_is_ready(void)
{
    return s_ready;
}

esp_err_t ls_storage_load_cfg(ls_storage_cfg_t *out_cfg)
{
    if (!out_cfg) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_ready) {
        return ESP_ERR_INVALID_STATE;
    }

    nvs_handle_t handle = 0;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READONLY, &handle);
    if (err != ESP_OK) {
        return err;
    }

    size_t len = sizeof(*out_cfg);
    err = nvs_get_blob(handle, KEY_CFG, out_cfg, &len);
    nvs_close(handle);

    if (err == ESP_OK) {
        if (len != sizeof(*out_cfg)) {
            return ESP_ERR_INVALID_SIZE;
        }
        if (out_cfg->version != LS_STORAGE_CFG_VERSION) {
            return ESP_ERR_INVALID_VERSION;
        }
    }
    return err;
}

esp_err_t ls_storage_save_cfg(const ls_storage_cfg_t *cfg)
{
    if (!cfg) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_ready) {
        return ESP_ERR_INVALID_STATE;
    }

    ls_storage_cfg_t stored = *cfg;
    stored.version = LS_STORAGE_CFG_VERSION;
    stored.reserved0 = 0U;
    stored.reserved1 = 0U;

    nvs_handle_t handle = 0;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle);
    if (err != ESP_OK) {
        return err;
    }
    err = nvs_set_blob(handle, KEY_CFG, &stored, sizeof(stored));
    if (err == ESP_OK) {
        err = nvs_commit(handle);
    }
    nvs_close(handle);
    return err;
}

esp_err_t ls_storage_load_cal(ls_storage_cal_t *out_cal)
{
    if (!out_cal) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_ready) {
        return ESP_ERR_INVALID_STATE;
    }

    nvs_handle_t handle = 0;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READONLY, &handle);
    if (err != ESP_OK) {
        return err;
    }

    size_t len = sizeof(*out_cal);
    err = nvs_get_blob(handle, KEY_CAL, out_cal, &len);
    nvs_close(handle);

    if (err == ESP_OK) {
        if (len != sizeof(*out_cal)) {
            return ESP_ERR_INVALID_SIZE;
        }
        if (out_cal->version != LS_STORAGE_CAL_VERSION || out_cal->count != LS_SENSOR_COUNT) {
            return ESP_ERR_INVALID_VERSION;
        }
    }
    return err;
}

esp_err_t ls_storage_save_cal(const ls_storage_cal_t *cal)
{
    if (!cal) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_ready) {
        return ESP_ERR_INVALID_STATE;
    }

    ls_storage_cal_t stored = *cal;
    stored.version = LS_STORAGE_CAL_VERSION;
    stored.count = LS_SENSOR_COUNT;

    nvs_handle_t handle = 0;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle);
    if (err != ESP_OK) {
        return err;
    }
    err = nvs_set_blob(handle, KEY_CAL, &stored, sizeof(stored));
    if (err == ESP_OK) {
        err = nvs_commit(handle);
    }
    nvs_close(handle);
    return err;
}
