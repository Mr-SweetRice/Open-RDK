#include "ds_storage.h"

#include <string.h>

#include "esp_log.h"
#include "nvs.h"
#include "nvs_flash.h"

static const char *TAG = "ds_storage";
static const char *NVS_NAMESPACE = "distance";
static const char *KEY_CFG = "cfg";
static bool s_ready = false;

esp_err_t ds_storage_init(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "NVS init failed (%d)", (int)err);
        s_ready = false;
        return err;
    }
    s_ready = true;
    return ESP_OK;
}

bool ds_storage_is_ready(void)
{
    return s_ready;
}

esp_err_t ds_storage_load_cfg(ds_storage_cfg_t *out_cfg)
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
    if (err != ESP_OK) {
        return err;
    }
    if (len != sizeof(*out_cfg)) {
        return ESP_ERR_INVALID_SIZE;
    }
    if (out_cfg->version != DS_STORAGE_CFG_VERSION) {
        return ESP_ERR_INVALID_VERSION;
    }
    out_cfg->sensor_name[sizeof(out_cfg->sensor_name) - 1U] = '\0';
    return ESP_OK;
}

esp_err_t ds_storage_save_cfg(const ds_storage_cfg_t *cfg)
{
    if (!cfg) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_ready) {
        return ESP_ERR_INVALID_STATE;
    }

    ds_storage_cfg_t stored = *cfg;
    stored.version = DS_STORAGE_CFG_VERSION;
    stored.sensor_name[sizeof(stored.sensor_name) - 1U] = '\0';

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
