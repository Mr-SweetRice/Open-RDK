#include "color_storage.h"

#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "nvs.h"
#include "nvs_flash.h"

static const char *TAG = "color_storage";
static const char *NVS_NAMESPACE = "color";
static const char *KEY_CFG = "cfg";
static const char *KEY_CAL = "cal";

static bool s_ready = false;

static void cfg_set_default_name(color_storage_cfg_t *cfg)
{
    if (!cfg) {
        return;
    }
    snprintf(cfg->sensor_name, sizeof(cfg->sensor_name), "color-sensor-esp");
}

esp_err_t color_storage_init(void)
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

bool color_storage_is_ready(void)
{
    return s_ready;
}

esp_err_t color_storage_load_cfg(color_storage_cfg_t *out_cfg)
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
    if (out_cfg->version != COLOR_STORAGE_CFG_VERSION) {
        return ESP_ERR_INVALID_VERSION;
    }
    out_cfg->sensor_name[sizeof(out_cfg->sensor_name) - 1U] = '\0';
    if (out_cfg->sensor_name[0] == '\0') {
        cfg_set_default_name(out_cfg);
    }
    return ESP_OK;
}

esp_err_t color_storage_save_cfg(const color_storage_cfg_t *cfg)
{
    if (!cfg) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_ready) {
        return ESP_ERR_INVALID_STATE;
    }

    color_storage_cfg_t stored = *cfg;
    stored.version = COLOR_STORAGE_CFG_VERSION;
    stored.sensor_name[sizeof(stored.sensor_name) - 1U] = '\0';
    if (stored.sensor_name[0] == '\0') {
        cfg_set_default_name(&stored);
    }

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

esp_err_t color_storage_load_cal(color_storage_cal_t *out_cal)
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

    if (err != ESP_OK) {
        return err;
    }
    if (len != sizeof(*out_cal)) {
        return ESP_ERR_INVALID_SIZE;
    }
    if (out_cal->version != COLOR_STORAGE_CAL_VERSION) {
        return ESP_ERR_INVALID_VERSION;
    }
    return ESP_OK;
}

esp_err_t color_storage_save_cal(const color_storage_cal_t *cal)
{
    if (!cal) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_ready) {
        return ESP_ERR_INVALID_STATE;
    }

    color_storage_cal_t stored = *cal;
    stored.version = COLOR_STORAGE_CAL_VERSION;

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
