#include <inttypes.h>
#include <math.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "driver/gpio.h"
#include "driver/i2c.h"
#include "driver/ledc.h"
#include "driver/uart.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "nvs.h"
#include "nvs_flash.h"

#define MODULE_ID 0x15U
#define MODULE_NAME "control_hub_module"
#define CFG_VERSION 3U
#define MENU_COUNT 8U
#define CONNECTED_MODULE_COUNT 8U
#define MENU_NAME_B64_LEN 44U
#define MENU_COMMAND_B64_LEN 132U
#define FRAME_MAX 200U
#define SYNC_LEN 4U
#define EXEC_ACK_TIMEOUT_US 5000000LL
#define EXEC_RUN_TIMEOUT_US 35000000LL
#define EXEC_STOP_TIMEOUT_US 5000000LL
#define I2C_PORT I2C_NUM_0
#define I2C_SDA GPIO_NUM_33
#define I2C_SCL GPIO_NUM_32
#define OLED_ADDR 0x3CU
#define MPU_ADDR 0x68U
#define ENCODER_CLK GPIO_NUM_14
#define ENCODER_DT GPIO_NUM_27
#define ENCODER_SW GPIO_NUM_26
#define OLED_WIDTH 128U
#define OLED_HEIGHT 64U
#define IMU_SAMPLE_PERIOD_MS 20U
#define IMU_CALIBRATION_SAMPLES 250U
#define IMU_CALIBRATION_MAGIC 0x494D5531U
#define RAD_TO_DEG 57.29577951308232f
#define COMPLEMENTARY_ALPHA 0.98f

static const char *TAG = "control_hub";
static const uint8_t s_sync[SYNC_LEN] = {0xAA, 0x55, 0xAA, 0x55};
static const gpio_num_t s_servo_pins[6] = {
    GPIO_NUM_13, GPIO_NUM_12, GPIO_NUM_23, GPIO_NUM_5, GPIO_NUM_2, GPIO_NUM_15};
static const gpio_num_t s_control_pins[12] = {
    GPIO_NUM_0, GPIO_NUM_21, GPIO_NUM_16, GPIO_NUM_17, GPIO_NUM_18, GPIO_NUM_19,
    GPIO_NUM_4, GPIO_NUM_22, GPIO_NUM_25, GPIO_NUM_34, GPIO_NUM_35, GPIO_NUM_39};

typedef struct {
    uint8_t enabled;
    uint8_t mode; /* 0 = terminal command, 1 = Python script */
    char name_b64[MENU_NAME_B64_LEN];
    char command_b64[MENU_COMMAND_B64_LEN];
} menu_entry_t;

typedef struct {
    uint32_t version;
    char device_name[32];
    menu_entry_t menu[MENU_COUNT];
    uint16_t servo_us[6];
    uint8_t gpio_mode[12];
    uint8_t gpio_value[12];
} persisted_cfg_t;

typedef struct {
    uint32_t magic;
    float gyro_bias_dps[3];
} imu_calibration_t;

typedef enum {
    OLED_MENU_ROOT = 0,
    OLED_MENU_MODULES,
    OLED_MENU_SERVOS,
    OLED_MENU_SERVO_CONTROL,
    OLED_MENU_EXECUTION,
    OLED_MENU_TRACTION,
    OLED_MENU_TRACTION_CONTROL,
    OLED_MENU_IMU,
} oled_menu_view_t;

typedef struct {
    persisted_cfg_t cfg;
    SemaphoreHandle_t lock;
    SemaphoreHandle_t tx_lock;
    SemaphoreHandle_t i2c_lock;
    int16_t accel[3];
    int16_t gyro[3];
    float accel_g[3];
    float gyro_dps[3];
    float euler_deg[3]; /* roll, pitch, relative yaw */
    float gyro_bias_dps[3];
    float imu_calibration_sum[3];
    uint16_t imu_calibration_samples;
    bool imu_calibrated;
    bool imu_calibrating;
    bool imu_filter_ready;
    int64_t imu_last_sample_us;
    uint8_t selected;
    bool oled_ok;
    bool mpu_ok;
    bool gesture_armed;
    int32_t encoder_position;
    oled_menu_view_t menu_view;
    uint8_t menu_cursor;
    uint8_t connected_module_count;
    char connected_modules[CONNECTED_MODULE_COUNT][MENU_NAME_B64_LEN];
    uint8_t connected_module_kinds[CONNECTED_MODULE_COUNT]; /* 1 = traction */
    uint8_t servo_control_channel;
    uint8_t servo_control_angle;
    uint8_t traction_module_index;
    uint8_t traction_control_action; /* 0 position, 1 RPM, 2 force output */
    int16_t traction_control_value;
    int16_t traction_control_values[CONNECTED_MODULE_COUNT][3];
    bool traction_feedback_visible;
    uint8_t traction_feedback_result; /* 0 sending, 1 done, 2 failed */
    int64_t traction_feedback_until_us;
    uint8_t traction_live_state; /* 0 idle, 1 sending, 2 done, 3 failed */
    bool encoder_pressed;
    bool switch_feedback_visible;
    bool switch_feedback_has_command;
    int64_t switch_feedback_until_us;
    bool execution_active;
    bool execution_stopping;
    uint8_t execution_slot;
    uint8_t execution_mode;
    uint8_t execution_result; /* 0 none, 1 done, 2 failed, 3 stopped */
    int64_t execution_deadline_us;
    uint32_t event_seq;
} app_state_t;

static app_state_t s_app;
static uint8_t s_oled[OLED_WIDTH * OLED_HEIGHT / 8U];

static void defaults(persisted_cfg_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->version = CFG_VERSION;
    snprintf(cfg->device_name, sizeof(cfg->device_name), "Modulo de Controle");
    for (size_t i = 0; i < 6; ++i) cfg->servo_us[i] = 1500U;
}

static bool cfg_valid(const persisted_cfg_t *cfg)
{
    if (!cfg || cfg->version != CFG_VERSION || cfg->device_name[0] == '\0') return false;
    for (size_t i = 0; i < 6; ++i) {
        if (cfg->servo_us[i] < 500U || cfg->servo_us[i] > 2500U) return false;
    }
    for (size_t i = 0; i < MENU_COUNT; ++i) {
        if (cfg->menu[i].mode > 1U ||
            !memchr(cfg->menu[i].name_b64, '\0', sizeof(cfg->menu[i].name_b64)) ||
            !memchr(cfg->menu[i].command_b64, '\0', sizeof(cfg->menu[i].command_b64))) return false;
    }
    return true;
}

static esp_err_t save_cfg(void)
{
    persisted_cfg_t copy;
    xSemaphoreTake(s_app.lock, portMAX_DELAY);
    copy = s_app.cfg;
    xSemaphoreGive(s_app.lock);
    nvs_handle_t nvs;
    esp_err_t err = nvs_open("control_hub", NVS_READWRITE, &nvs);
    if (err != ESP_OK) return err;
    err = nvs_set_blob(nvs, "cfg", &copy, sizeof(copy));
    if (err == ESP_OK) err = nvs_commit(nvs);
    nvs_close(nvs);
    return err;
}

static void load_cfg(void)
{
    defaults(&s_app.cfg);
    nvs_handle_t nvs;
    if (nvs_open("control_hub", NVS_READONLY, &nvs) != ESP_OK) return;
    persisted_cfg_t stored;
    size_t size = sizeof(stored);
    esp_err_t err = nvs_get_blob(nvs, "cfg", &stored, &size);
    nvs_close(nvs);
    if (err == ESP_OK && size == sizeof(stored) && cfg_valid(&stored)) s_app.cfg = stored;
}

static void load_imu_calibration(void)
{
    nvs_handle_t nvs;
    if (nvs_open("control_hub", NVS_READONLY, &nvs) != ESP_OK) return;
    imu_calibration_t stored={0};
    size_t size=sizeof(stored);
    esp_err_t err=nvs_get_blob(nvs,"imu_cal",&stored,&size);
    nvs_close(nvs);
    if (err==ESP_OK && size==sizeof(stored) && stored.magic==IMU_CALIBRATION_MAGIC) {
        for (size_t i=0;i<3;i++) s_app.gyro_bias_dps[i]=stored.gyro_bias_dps[i];
        s_app.imu_calibrated=true;
    }
}

static esp_err_t save_imu_calibration(void)
{
    imu_calibration_t stored={.magic=IMU_CALIBRATION_MAGIC};
    xSemaphoreTake(s_app.lock,portMAX_DELAY);
    for (size_t i=0;i<3;i++) stored.gyro_bias_dps[i]=s_app.gyro_bias_dps[i];
    xSemaphoreGive(s_app.lock);
    nvs_handle_t nvs;
    esp_err_t err=nvs_open("control_hub",NVS_READWRITE,&nvs);
    if (err!=ESP_OK) return err;
    err=nvs_set_blob(nvs,"imu_cal",&stored,sizeof(stored));
    if (err==ESP_OK) err=nvs_commit(nvs);
    nvs_close(nvs);
    return err;
}

static float wrap_degrees(float angle)
{
    while (angle>180.0f) angle-=360.0f;
    while (angle<-180.0f) angle+=360.0f;
    return angle;
}

static esp_err_t i2c_write(uint8_t address, const uint8_t *data, size_t len)
{
    if (!s_app.i2c_lock || !data || !len) return ESP_ERR_INVALID_ARG;
    if (xSemaphoreTake(s_app.i2c_lock,pdMS_TO_TICKS(100))!=pdTRUE) return ESP_ERR_TIMEOUT;
    i2c_cmd_handle_t cmd = i2c_cmd_link_create();
    if (!cmd) { xSemaphoreGive(s_app.i2c_lock); return ESP_ERR_NO_MEM; }
    i2c_master_start(cmd);
    i2c_master_write_byte(cmd, (address << 1U) | I2C_MASTER_WRITE, true);
    i2c_master_write(cmd, data, len, true);
    i2c_master_stop(cmd);
    esp_err_t err = i2c_master_cmd_begin(I2C_PORT, cmd, pdMS_TO_TICKS(80));
    i2c_cmd_link_delete(cmd);
    xSemaphoreGive(s_app.i2c_lock);
    return err;
}

static esp_err_t i2c_read_reg(uint8_t address, uint8_t reg, uint8_t *data, size_t len)
{
    if (!s_app.i2c_lock || !data || !len) return ESP_ERR_INVALID_ARG;
    if (xSemaphoreTake(s_app.i2c_lock,pdMS_TO_TICKS(100))!=pdTRUE) return ESP_ERR_TIMEOUT;
    i2c_cmd_handle_t cmd = i2c_cmd_link_create();
    if (!cmd) { xSemaphoreGive(s_app.i2c_lock); return ESP_ERR_NO_MEM; }
    i2c_master_start(cmd);
    i2c_master_write_byte(cmd, (address << 1U) | I2C_MASTER_WRITE, true);
    i2c_master_write_byte(cmd, reg, true);
    i2c_master_start(cmd);
    i2c_master_write_byte(cmd, (address << 1U) | I2C_MASTER_READ, true);
    if (len > 1U) i2c_master_read(cmd, data, len - 1U, I2C_MASTER_ACK);
    i2c_master_read_byte(cmd, data + len - 1U, I2C_MASTER_NACK);
    i2c_master_stop(cmd);
    esp_err_t err = i2c_master_cmd_begin(I2C_PORT, cmd, pdMS_TO_TICKS(80));
    i2c_cmd_link_delete(cmd);
    xSemaphoreGive(s_app.i2c_lock);
    return err;
}

static esp_err_t oled_cmds(const uint8_t *commands, size_t count)
{
    uint8_t data[32];
    if (count + 1U > sizeof(data)) return ESP_ERR_INVALID_SIZE;
    data[0] = 0x00;
    memcpy(data + 1, commands, count);
    return i2c_write(OLED_ADDR, data, count + 1U);
}

static void oled_init(void)
{
    const uint8_t init[] = {0xAE,0x20,0x00,0xB0,0xC8,0x00,0x10,0x40,0x81,0x7F,
        0xA1,0xA6,0xA8,0x3F,0xA4,0xD3,0x00,0xD5,0x80,0xD9,0xF1,0xDA,0x12,
        0xDB,0x40,0x8D,0x14,0xAF};
    s_app.oled_ok = oled_cmds(init, sizeof(init)) == ESP_OK;
}

/* Compact 5x7 glyphs: digits, uppercase letters and a few menu symbols. */
static void glyph(char ch, uint8_t out[5])
{
    static const uint8_t digits[10][5] = {
        {0x3E,0x51,0x49,0x45,0x3E},{0x00,0x42,0x7F,0x40,0x00},{0x42,0x61,0x51,0x49,0x46},
        {0x21,0x41,0x45,0x4B,0x31},{0x18,0x14,0x12,0x7F,0x10},{0x27,0x45,0x45,0x45,0x39},
        {0x3C,0x4A,0x49,0x49,0x30},{0x01,0x71,0x09,0x05,0x03},{0x36,0x49,0x49,0x49,0x36},
        {0x06,0x49,0x49,0x29,0x1E}};
    static const uint8_t letters[26][5] = {
        {0x7E,0x11,0x11,0x11,0x7E},{0x7F,0x49,0x49,0x49,0x36},{0x3E,0x41,0x41,0x41,0x22},
        {0x7F,0x41,0x41,0x22,0x1C},{0x7F,0x49,0x49,0x49,0x41},{0x7F,0x09,0x09,0x09,0x01},
        {0x3E,0x41,0x49,0x49,0x7A},{0x7F,0x08,0x08,0x08,0x7F},{0x00,0x41,0x7F,0x41,0x00},
        {0x20,0x40,0x41,0x3F,0x01},{0x7F,0x08,0x14,0x22,0x41},{0x7F,0x40,0x40,0x40,0x40},
        {0x7F,0x02,0x0C,0x02,0x7F},{0x7F,0x04,0x08,0x10,0x7F},{0x3E,0x41,0x41,0x41,0x3E},
        {0x7F,0x09,0x09,0x09,0x06},{0x3E,0x41,0x51,0x21,0x5E},{0x7F,0x09,0x19,0x29,0x46},
        {0x46,0x49,0x49,0x49,0x31},{0x01,0x01,0x7F,0x01,0x01},{0x3F,0x40,0x40,0x40,0x3F},
        {0x1F,0x20,0x40,0x20,0x1F},{0x3F,0x40,0x38,0x40,0x3F},{0x63,0x14,0x08,0x14,0x63},
        {0x07,0x08,0x70,0x08,0x07},{0x61,0x51,0x49,0x45,0x43}};
    memset(out, 0, 5);
    if (ch >= 'a' && ch <= 'z') ch -= 32;
    if (ch >= '0' && ch <= '9') memcpy(out, digits[ch - '0'], 5);
    else if (ch >= 'A' && ch <= 'Z') memcpy(out, letters[ch - 'A'], 5);
    else if (ch == '>') { uint8_t g[5]={0x00,0x41,0x22,0x14,0x08}; memcpy(out,g,5); }
    else if (ch == '-') { out[1]=out[2]=out[3]=0x08; }
    else if (ch == '_') { memset(out,0x40,5); }
    else if (ch == '.') { out[2]=0x60; }
    else if (ch == ':') { out[2]=0x36; }
}

static void oled_text(uint8_t x, uint8_t page, const char *text)
{
    if (!text || page >= 8U) return;
    while (*text && x + 5U < OLED_WIDTH) {
        uint8_t g[5]; glyph(*text++, g);
        for (size_t i=0;i<5;i++) s_oled[page*OLED_WIDTH+x++]=g[i];
        s_oled[page*OLED_WIDTH+x++]=0;
    }
}

static int b64_value(char c)
{
    if (c >= 'A' && c <= 'Z') return c - 'A';
    if (c >= 'a' && c <= 'z') return c - 'a' + 26;
    if (c >= '0' && c <= '9') return c - '0' + 52;
    if (c == '-' || c == '+') return 62;
    if (c == '_' || c == '/') return 63;
    return -1;
}

static void decode_name(const char *in, char *out, size_t out_len)
{
    uint32_t acc=0; int bits=0; size_t used=0;
    if (!out_len) return;
    while (in && *in && used+1<out_len) {
        int v=b64_value(*in++); if (v<0) break; acc=(acc<<6)|(uint32_t)v; bits+=6;
        if (bits>=8) { bits-=8; out[used++]=(char)((acc>>bits)&0xFFU); }
    }
    out[used]='\0';
}

static void oled_render(void)
{
    if (!s_app.oled_ok) return;
    static persisted_cfg_t copy;
    uint8_t selected; bool feedback_visible; bool feedback_has_command;
    bool execution_active; bool execution_stopping; uint8_t execution_slot;
    uint8_t execution_mode; uint8_t execution_result;
    oled_menu_view_t menu_view; uint8_t menu_cursor; uint8_t connected_count;
    uint8_t servo_channel; uint8_t servo_angle;
    uint8_t traction_action; int16_t traction_value; uint8_t traction_live_state;
    bool traction_feedback_visible; uint8_t traction_feedback_result;
    float euler_deg[3]; bool imu_calibrated; bool imu_calibrating; uint16_t imu_cal_samples;
    char connected_modules[CONNECTED_MODULE_COUNT][MENU_NAME_B64_LEN];
    xSemaphoreTake(s_app.lock, portMAX_DELAY);
    copy=s_app.cfg; selected=s_app.selected;
    feedback_visible=s_app.switch_feedback_visible;
    feedback_has_command=s_app.switch_feedback_has_command;
    execution_active=s_app.execution_active;
    execution_stopping=s_app.execution_stopping;
    execution_slot=s_app.execution_slot;
    execution_mode=s_app.execution_mode;
    execution_result=s_app.execution_result;
    menu_view=s_app.menu_view; menu_cursor=s_app.menu_cursor;
    connected_count=s_app.connected_module_count;
    memcpy(connected_modules,s_app.connected_modules,sizeof(connected_modules));
    servo_channel=s_app.servo_control_channel;
    servo_angle=s_app.servo_control_angle;
    traction_action=s_app.traction_control_action;
    traction_value=s_app.traction_control_value;
    traction_live_state=s_app.traction_live_state;
    traction_feedback_visible=s_app.traction_feedback_visible;
    traction_feedback_result=s_app.traction_feedback_result;
    memcpy(euler_deg,s_app.euler_deg,sizeof(euler_deg));
    imu_calibrated=s_app.imu_calibrated;
    imu_calibrating=s_app.imu_calibrating;
    imu_cal_samples=s_app.imu_calibration_samples;
    xSemaphoreGive(s_app.lock);
    memset(s_oled,0,sizeof(s_oled));
    oled_text(0,0,copy.device_name);
    if (execution_active) {
        char name[40]; decode_name(copy.menu[execution_slot].name_b64,name,sizeof(name));
        oled_text(0,2,execution_mode==1U?"CODIGO PYTHON":"COMANDO");
        oled_text(0,3,execution_stopping?"PARANDO...":"RODANDO...");
        oled_text(0,5,name);
        oled_text(0,7,"> PARAR EXECUCAO");
    } else if (traction_feedback_visible) {
        char line[32];
        const char *action=traction_action==0U?"POSICAO":(traction_action==1U?"VELOCIDADE":"FORCE OUTPUT");
        oled_text(0,2,"MODULO DE TRACAO");
        oled_text(0,3,action);
        if (traction_feedback_result==1U) oled_text(0,5,"COMANDO APLICADO");
        else if (traction_feedback_result==2U) oled_text(0,5,"FALHA NO COMANDO");
        else oled_text(0,5,"ENVIANDO...");
        snprintf(line,sizeof(line),"VALOR: %d",traction_value); oled_text(0,7,line);
    } else if (feedback_visible) {
        char name[40]; decode_name(copy.menu[selected].name_b64,name,sizeof(name));
        if (!copy.menu[selected].enabled) snprintf(name,sizeof(name),"SLOT %u",selected+1U);
        oled_text(0,2,execution_result?"EXECUCAO":"KY-040 SWITCH");
        if (execution_result==1U) oled_text(0,3,"CONCLUIDO");
        else if (execution_result==2U) oled_text(0,3,"FALHOU");
        else if (execution_result==3U) oled_text(0,3,"INTERROMPIDO");
        else oled_text(0,3,"SWITCH OK");
        oled_text(0,5,name);
        if (!execution_result) oled_text(0,7,feedback_has_command?"EXECUTANDO":"ITEM VAZIO");
    } else if (menu_view==OLED_MENU_ROOT) {
        oled_text(0,1,"MENU PRINCIPAL");
        const char *items[]={"MODULOS","SERVOS","IMU","EXECUCAO"};
        for (uint8_t i=0;i<4U;i++) {
            uint8_t row=(uint8_t)(2U+i);
            oled_text(0,row,i==menu_cursor?">":" "); oled_text(8,row,items[i]);
        }
    } else if (menu_view==OLED_MENU_IMU) {
        char line[32];
        oled_text(0,1,"IMU - ANGULOS EULER");
        snprintf(line,sizeof(line),"ROLL : %7.2f",euler_deg[0]); oled_text(0,2,line);
        snprintf(line,sizeof(line),"PITCH: %7.2f",euler_deg[1]); oled_text(0,3,line);
        snprintf(line,sizeof(line),"YAW  : %7.2f",euler_deg[2]); oled_text(0,4,line);
        if (imu_calibrating) {
            unsigned progress=(unsigned)((imu_cal_samples*100U)/IMU_CALIBRATION_SAMPLES);
            snprintf(line,sizeof(line),"CALIBRANDO %u%%",progress); oled_text(0,6,line);
        } else oled_text(0,6,imu_calibrated?"CALIBRADO":"NAO CALIBRADO");
        oled_text(0,7,"PRESS=VOLTAR");
    } else if (menu_view==OLED_MENU_SERVO_CONTROL) {
        char line[24];
        oled_text(0,2,"CONTROLE DE SERVO");
        snprintf(line,sizeof(line),"SERVO %u",servo_channel+1U); oled_text(0,4,line);
        snprintf(line,sizeof(line),"ANGULO: %u",servo_angle); oled_text(0,5,line);
        oled_text(0,7,"GIRAR  PRESS=VOLTAR");
    } else if (menu_view==OLED_MENU_TRACTION_CONTROL) {
        char line[32];
        const char *action=traction_action==0U?"POSICAO":(traction_action==1U?"VELOCIDADE":"FORCE OUTPUT");
        oled_text(0,2,"CONTROLE DE TRACAO");
        oled_text(0,4,action);
        if (traction_action==0U) snprintf(line,sizeof(line),"ALVO: %d GRAUS",traction_value);
        else if (traction_action==1U) snprintf(line,sizeof(line),"ALVO: %d RPM",traction_value);
        else snprintf(line,sizeof(line),"SAIDA: %d%%",traction_value);
        oled_text(0,5,line);
        if (traction_live_state==1U) oled_text(0,6,"ENVIANDO...");
        else if (traction_live_state==2U) oled_text(0,6,"APLICADO");
        else if (traction_live_state==3U) oled_text(0,6,"FALHA");
        oled_text(0,7,"GIRAR PRESS=VOLTAR");
    } else {
        uint8_t item_count=0U;
        const char *title="";
        if (menu_view==OLED_MENU_MODULES) { item_count=(uint8_t)(connected_count+1U); title="MODULOS RDK"; }
        else if (menu_view==OLED_MENU_SERVOS) { item_count=7U; title="SERVOS"; }
        else if (menu_view==OLED_MENU_TRACTION) { item_count=4U; title="MODULO DE TRACAO"; }
        else { item_count=(uint8_t)(MENU_COUNT+1U); title="EXECUCAO"; }
        oled_text(0,1,title);
        int start=(menu_cursor>2U)?(int)menu_cursor-2:0;
        if (start>(int)item_count-6) start=(int)item_count-6;
        if (start<0) start=0;
        for (int row=0;row<6;row++) {
            int item=start+row; if (item>=(int)item_count) break;
            char label[40];
            if (item==0) snprintf(label,sizeof(label),"< VOLTAR");
            else if (menu_view==OLED_MENU_MODULES) decode_name(connected_modules[item-1],label,sizeof(label));
            else if (menu_view==OLED_MENU_SERVOS) {
                unsigned angle=(unsigned)(((uint32_t)(copy.servo_us[item-1]-500U)*180U)/2000U);
                snprintf(label,sizeof(label),"SERVO %d  %u",item,angle);
            } else if (menu_view==OLED_MENU_TRACTION) {
                const char *items[]={"< VOLTAR","POSICAO","VELOCIDADE","FORCE OUTPUT"};
                snprintf(label,sizeof(label),"%s",items[item]);
            } else {
                decode_name(copy.menu[item-1].name_b64,label,sizeof(label));
                if (!copy.menu[item-1].enabled) snprintf(label,sizeof(label),"SLOT %d VAZIO",item);
            }
            oled_text(0,(uint8_t)(row+2),item==menu_cursor?">":" ");
            oled_text(8,(uint8_t)(row+2),label);
        }
    }
    const uint8_t range[]={0x21,0,127,0x22,0,7};
    if (oled_cmds(range,sizeof(range))!=ESP_OK) { s_app.oled_ok=false; return; }
    for (size_t off=0; off<sizeof(s_oled); off+=16) {
        uint8_t packet[17]; packet[0]=0x40; memcpy(packet+1,s_oled+off,16);
        if (i2c_write(OLED_ADDR,packet,sizeof(packet))!=ESP_OK) { s_app.oled_ok=false; return; }
    }
}

static void send_stream(uint8_t type, uint32_t seq, const char *text)
{
    size_t len=strlen(text); if (!len) return; if (len>FRAME_MAX) len=FRAME_MAX;
    uint8_t frame[SYNC_LEN+1+FRAME_MAX+1+3]; size_t i=0;
    memcpy(frame,s_sync,SYNC_LEN); i+=SYNC_LEN; frame[i++]=(uint8_t)len;
    memcpy(frame+i,text,len); i+=len; frame[i++]=type;
    frame[i++]=(seq>>16)&0xFF; frame[i++]=(seq>>8)&0xFF; frame[i++]=seq&0xFF;
    xSemaphoreTake(s_app.tx_lock,portMAX_DELAY); uart_write_bytes(UART_NUM_0,frame,i); xSemaphoreGive(s_app.tx_lock);
}

static void send_control(const uint8_t *payload,size_t len)
{
    uint8_t frame[SYNC_LEN+70]; if (len>69) return;
    memcpy(frame,s_sync,SYNC_LEN); frame[SYNC_LEN]=MODULE_ID; memcpy(frame+SYNC_LEN+1,payload,len);
    xSemaphoreTake(s_app.tx_lock,portMAX_DELAY); uart_write_bytes(UART_NUM_0,frame,SYNC_LEN+1+len); xSemaphoreGive(s_app.tx_lock);
}

static void execute_selected(void)
{
    menu_entry_t entry; uint8_t selected;
    xSemaphoreTake(s_app.lock,portMAX_DELAY); selected=s_app.selected; entry=s_app.cfg.menu[selected]; xSemaphoreGive(s_app.lock);
    if (!entry.enabled) return;
    xSemaphoreTake(s_app.lock,portMAX_DELAY);
    s_app.execution_active=true;
    s_app.execution_stopping=false;
    s_app.execution_slot=selected;
    s_app.execution_mode=entry.mode;
    s_app.execution_result=0U;
    s_app.execution_deadline_us=esp_timer_get_time()+EXEC_ACK_TIMEOUT_US;
    s_app.switch_feedback_visible=false;
    xSemaphoreGive(s_app.lock);
    oled_render();
    char event[FRAME_MAX+1]; snprintf(event,sizeof(event),"EXEC,%u,%u,%s",selected,entry.mode,entry.command_b64);
    send_stream(0x04,s_app.event_seq++,event);
}

static void stop_selected_execution(void)
{
    uint8_t slot;
    xSemaphoreTake(s_app.lock,portMAX_DELAY);
    if (!s_app.execution_active || s_app.execution_stopping) {
        xSemaphoreGive(s_app.lock);
        return;
    }
    s_app.execution_stopping=true;
    s_app.execution_deadline_us=esp_timer_get_time()+EXEC_STOP_TIMEOUT_US;
    slot=s_app.execution_slot;
    xSemaphoreGive(s_app.lock);
    oled_render();
    char event[32]; snprintf(event,sizeof(event),"STOP,%u",slot);
    send_stream(0x04,s_app.event_seq++,event);
}

static void send_traction_request(uint8_t module_index,uint8_t action,int16_t value,bool show_feedback)
{
    const char *name=action==0U?"POS":(action==1U?"RPM":(action==2U?"OUT":"CLEAR"));
    xSemaphoreTake(s_app.lock,portMAX_DELAY);
    if (action<3U && module_index<CONNECTED_MODULE_COUNT) {
        s_app.traction_module_index=module_index;
        s_app.traction_control_action=action;
        s_app.traction_control_value=value;
        s_app.traction_control_values[module_index][action]=value;
        s_app.traction_live_state=1U;
    } else if (module_index<CONNECTED_MODULE_COUNT) {
        s_app.traction_control_values[module_index][2]=0;
    }
    if (show_feedback) {
        s_app.traction_feedback_visible=true;
        s_app.traction_feedback_result=0U;
        s_app.traction_feedback_until_us=esp_timer_get_time()+3000000LL;
    } else s_app.traction_feedback_visible=false;
    xSemaphoreGive(s_app.lock);
    char event[48];
    snprintf(event,sizeof(event),"TRACT,%u,%s,%d",module_index,name,value);
    send_stream(0x04,s_app.event_seq++,event);
}

static void set_servo_us(size_t channel,uint16_t pulse_us)
{
    if (channel>=6 || pulse_us<500 || pulse_us>2500) return;
    uint32_t duty=(uint32_t)(((uint64_t)pulse_us*65535U)/20000U);
    ledc_set_duty(LEDC_HIGH_SPEED_MODE,(ledc_channel_t)channel,duty);
    ledc_update_duty(LEDC_HIGH_SPEED_MODE,(ledc_channel_t)channel);
}

static bool apply_command(char *line,char *out,size_t out_len)
{
    unsigned a,b; int value;
    if (!strcmp(line,"GET INFO")) {
        snprintf(out,out_len,"INFO,%s,control_hub_module,control_hub_module,21,SSD1306,MPU6050,33,32,6,12,14,27,26",s_app.cfg.device_name); return true;
    }
    if (!strcmp(line,"GET CFG")) {
        snprintf(out,out_len,"HUB,%s,%u,%u,%u",s_app.cfg.device_name,s_app.selected,s_app.oled_ok?1:0,s_app.mpu_ok?1:0); return true;
    }
    if (!strcmp(line,"GET IMU")) {
        float euler[3],gyro[3]; bool calibrated,calibrating; uint16_t samples;
        xSemaphoreTake(s_app.lock,portMAX_DELAY);
        memcpy(euler,s_app.euler_deg,sizeof(euler));
        memcpy(gyro,s_app.gyro_dps,sizeof(gyro));
        calibrated=s_app.imu_calibrated; calibrating=s_app.imu_calibrating;
        samples=s_app.imu_calibration_samples;
        xSemaphoreGive(s_app.lock);
        unsigned progress=calibrating?(unsigned)((samples*100U)/IMU_CALIBRATION_SAMPLES):(calibrated?100U:0U);
        if (progress>100U) progress=100U;
        snprintf(out,out_len,"IMU,%.2f,%.2f,%.2f,%.3f,%.3f,%.3f,%u,%u,%u",
            euler[0],euler[1],euler[2],gyro[0],gyro[1],gyro[2],
            calibrated?1U:0U,calibrating?1U:0U,progress); return true;
    }
    if (!strcmp(line,"GET IMU RAW")) {
        int16_t accel[3],gyro[3];
        xSemaphoreTake(s_app.lock,portMAX_DELAY);
        memcpy(accel,s_app.accel,sizeof(accel)); memcpy(gyro,s_app.gyro,sizeof(gyro));
        xSemaphoreGive(s_app.lock);
        snprintf(out,out_len,"IMU_RAW,%d,%d,%d,%d,%d,%d",accel[0],accel[1],accel[2],gyro[0],gyro[1],gyro[2]); return true;
    }
    if (!strcmp(line,"CALIBRATE IMU")) {
        xSemaphoreTake(s_app.lock,portMAX_DELAY);
        bool available=s_app.mpu_ok;
        if (available && !s_app.imu_calibrating) {
            memset(s_app.imu_calibration_sum,0,sizeof(s_app.imu_calibration_sum));
            s_app.imu_calibration_samples=0U;
            s_app.imu_calibrating=true;
            s_app.imu_calibrated=false;
        }
        xSemaphoreGive(s_app.lock);
        snprintf(out,out_len,available?"OK":"ERR"); return true;
    }
    if (!strcmp(line,"GET ENCODER")) {
        snprintf(out,out_len,"ENCODER,%" PRId32 ",%u,%u",s_app.encoder_position,s_app.encoder_pressed?1U:0U,s_app.selected); return true;
    }
    if (!strcmp(line,"GET RUN")) {
        snprintf(out,out_len,"RUN,%u,%u,%u,%u",s_app.execution_active?1U:0U,s_app.execution_stopping?1U:0U,s_app.execution_slot,s_app.execution_mode); return true;
    }
    if (!strcmp(line,"GET MODULES")) {
        snprintf(out,out_len,"MODULES,%u",s_app.connected_module_count); return true;
    }
    if (sscanf(line,"GET MODULE %u",&a)==1 && a<s_app.connected_module_count) {
        snprintf(out,out_len,"MODULE,%u,%u,%s",a,s_app.connected_module_kinds[a],s_app.connected_modules[a]); return true;
    }
    if (sscanf(line,"GET MENU %u",&a)==1 && a<MENU_COUNT) {
        menu_entry_t *e=&s_app.cfg.menu[a]; snprintf(out,out_len,"MENU,%u,%u,%u,%s,%s",a,e->enabled,e->mode,e->name_b64,e->command_b64); return true;
    }
    if (sscanf(line,"SET SERVO %u %d",&a,&value)==2 && a<6 && value>=0 && value<=180) {
        uint16_t us=(uint16_t)(500+(value*2000)/180); set_servo_us(a,us); s_app.cfg.servo_us[a]=us; snprintf(out,out_len,"OK"); return true;
    }
    if (sscanf(line,"SET SERVO_US %u %u",&a,&b)==2 && a<6 && b>=500 && b<=2500) {
        set_servo_us(a,(uint16_t)b); s_app.cfg.servo_us[a]=(uint16_t)b; snprintf(out,out_len,"OK"); return true;
    }
    if (sscanf(line,"SET GPIO %u %d",&a,&value)==2 && a<9 && (value==0||value==1)) {
        gpio_set_direction(s_control_pins[a],GPIO_MODE_OUTPUT); gpio_set_level(s_control_pins[a],value);
        s_app.cfg.gpio_mode[a]=1; s_app.cfg.gpio_value[a]=(uint8_t)value; snprintf(out,out_len,"OK"); return true;
    }
    if (sscanf(line,"GET GPIO %u",&a)==1 && a<12) {
        snprintf(out,out_len,"GPIO,%u,%d,%u,%u",a,(int)s_control_pins[a],s_app.cfg.gpio_mode[a],gpio_get_level(s_control_pins[a])); return true;
    }
    if (sscanf(line,"CLEAR MENU %u",&a)==1 && a<MENU_COUNT) {
        memset(&s_app.cfg.menu[a],0,sizeof(s_app.cfg.menu[a])); snprintf(out,out_len,"OK"); oled_render(); return true;
    }
    if (!strncmp(line,"SET MENU ",9)) {
        char name[MENU_NAME_B64_LEN],command[MENU_COMMAND_B64_LEN];
        unsigned mode=0U;
        if (sscanf(line+9,"%u %u %43s %131s",&a,&mode,name,command)==4 && a<MENU_COUNT && mode<=1U) {
            menu_entry_t *e=&s_app.cfg.menu[a]; memset(e,0,sizeof(*e)); e->enabled=1;
            e->mode=(uint8_t)mode;
            snprintf(e->name_b64,sizeof(e->name_b64),"%s",name); snprintf(e->command_b64,sizeof(e->command_b64),"%s",command);
            snprintf(out,out_len,"OK"); oled_render(); return true;
        }
        snprintf(out,out_len,"ERR"); return true;
    }
    if (!strcmp(line,"CLEAR MODULES")) {
        xSemaphoreTake(s_app.lock,portMAX_DELAY);
        s_app.connected_module_count=0U;
        memset(s_app.connected_modules,0,sizeof(s_app.connected_modules));
        memset(s_app.connected_module_kinds,0,sizeof(s_app.connected_module_kinds));
        if (s_app.menu_view==OLED_MENU_MODULES) s_app.menu_cursor=0U;
        xSemaphoreGive(s_app.lock);
        oled_render(); snprintf(out,out_len,"OK"); return true;
    }
    if (!strncmp(line,"SET MODULE ",11) && strncmp(line,"SET MODULE COUNT ",17)) {
        char name[MENU_NAME_B64_LEN];
        unsigned kind=0U;
        if (sscanf(line+11,"%u %u %43s",&a,&kind,name)==3 && a<CONNECTED_MODULE_COUNT && kind<=1U) {
            xSemaphoreTake(s_app.lock,portMAX_DELAY);
            snprintf(s_app.connected_modules[a],sizeof(s_app.connected_modules[a]),"%s",name);
            s_app.connected_module_kinds[a]=(uint8_t)kind;
            if (s_app.connected_module_count<=a) s_app.connected_module_count=(uint8_t)(a+1U);
            xSemaphoreGive(s_app.lock);
            snprintf(out,out_len,"OK"); return true;
        }
        snprintf(out,out_len,"ERR"); return true;
    }
    if (sscanf(line,"SET MODULE COUNT %u",&a)==1 && a<=CONNECTED_MODULE_COUNT) {
        xSemaphoreTake(s_app.lock,portMAX_DELAY);
        s_app.connected_module_count=(uint8_t)a;
        for (uint8_t i=(uint8_t)a;i<CONNECTED_MODULE_COUNT;i++) {
            s_app.connected_modules[i][0]='\0';
            s_app.connected_module_kinds[i]=0U;
        }
        if (s_app.menu_view==OLED_MENU_MODULES && s_app.menu_cursor>a) s_app.menu_cursor=(uint8_t)a;
        xSemaphoreGive(s_app.lock);
        oled_render(); snprintf(out,out_len,"OK"); return true;
    }
    if (!strncmp(line,"TRACT STATE ",12)) {
        char action[8]={0},state[8]={0};
        if (sscanf(line+12,"%u %7s %7s",&a,action,state)==3 && a<CONNECTED_MODULE_COUNT) {
            uint8_t action_id=!strcmp(action,"POS")?0U:(!strcmp(action,"RPM")?1U:2U);
            uint8_t result=!strcmp(state,"DONE")?1U:2U;
            bool is_clear=!strcmp(action,"CLEAR");
            xSemaphoreTake(s_app.lock,portMAX_DELAY);
            if (!is_clear && s_app.menu_view==OLED_MENU_TRACTION_CONTROL &&
                    s_app.traction_module_index==(uint8_t)a &&
                    s_app.traction_control_action==action_id) {
                s_app.traction_live_state=result==1U?2U:3U;
                s_app.traction_feedback_visible=false;
            } else if (is_clear) {
                s_app.traction_control_values[a][2]=0;
                s_app.traction_feedback_visible=false;
            }
            xSemaphoreGive(s_app.lock);
            snprintf(out,out_len,"OK"); return true;
        }
        snprintf(out,out_len,"ERR"); return true;
    }
    if (!strncmp(line,"SET CFG NAME ",13)) {
        const char *name=line+13; if (!*name) { snprintf(out,out_len,"ERR"); return true; }
        snprintf(s_app.cfg.device_name,sizeof(s_app.cfg.device_name),"%s",name); snprintf(out,out_len,"OK"); oled_render(); return true;
    }
    if (!strncmp(line,"RUN STATE ",10)) {
        char state[16]={0};
        if (sscanf(line+10,"%u %15s",&a,state)==2 && a<MENU_COUNT) {
            xSemaphoreTake(s_app.lock,portMAX_DELAY);
            s_app.execution_slot=(uint8_t)a;
            if (!strcmp(state,"RUNNING")) {
                s_app.execution_active=true; s_app.execution_stopping=false; s_app.execution_result=0U;
                s_app.execution_deadline_us=esp_timer_get_time()+EXEC_RUN_TIMEOUT_US;
            } else {
                s_app.execution_active=false; s_app.execution_stopping=false;
                s_app.execution_deadline_us=0;
                s_app.execution_result=!strcmp(state,"DONE")?1U:(!strcmp(state,"STOPPED")?3U:2U);
                s_app.switch_feedback_visible=true;
                s_app.switch_feedback_until_us=esp_timer_get_time()+1500000LL;
            }
            xSemaphoreGive(s_app.lock);
            oled_render(); snprintf(out,out_len,"OK"); return true;
        }
        snprintf(out,out_len,"ERR"); return true;
    }
    if (!strcmp(line,"SAVE CFG")) { snprintf(out,out_len,save_cfg()==ESP_OK?"OK":"ERR"); return true; }
    if (!strcmp(line,"RESET CFG")) { defaults(&s_app.cfg); for(size_t i=0;i<6;i++)set_servo_us(i,1500); oled_render(); snprintf(out,out_len,"OK"); return true; }
    return false;
}

static void handle_stream(const uint8_t *data,size_t len)
{
    uint8_t msg_len=data[0]; if (!msg_len || len!=1U+msg_len+4U) return;
    char line[FRAME_MAX+1]; memcpy(line,data+1,msg_len); line[msg_len]='\0';
    uint8_t type=data[1+msg_len]; size_t q=2+msg_len;
    uint32_t seq=((uint32_t)data[q]<<16)|((uint32_t)data[q+1]<<8)|data[q+2];
    char out[FRAME_MAX+1]="";
    if (type==0x01) { if (!apply_command(line,out,sizeof(out))) snprintf(out,sizeof(out),"I RECIEVED CMD"); }
    else if (type==0x02) snprintf(out,sizeof(out),"I RECIEVED TEST");
    else if (type==0x03) {
        if (!strncmp(line,"TELEMETRY_START",15)) snprintf(out,sizeof(out),"TELEMETRY STARTED");
        else if (!strncmp(line,"TELEMETRY_SYNC",14)) snprintf(out,sizeof(out),"TELEMETRY SYNCED");
        else if (!strncmp(line,"TELEMETRY_STOP",14)) snprintf(out,sizeof(out),"TELEMETRY STOPPED");
        else snprintf(out,sizeof(out),"TELEMETRY");
    } else if (type==0x04) { if (!apply_command(line,out,sizeof(out))) snprintf(out,sizeof(out),"ERR"); }
    else snprintf(out,sizeof(out),"ERR");
    send_stream(type,seq,out);
}

static void comm_task(void *arg)
{
    (void)arg; size_t sync=0,used=0,expected=0; bool collecting=false;
    uint8_t data[1+FRAME_MAX+4];
    while (true) {
        uint8_t byte; int n=uart_read_bytes(UART_NUM_0,&byte,1,pdMS_TO_TICKS(20)); if (n<=0) continue;
        if (!collecting) {
            if (byte==s_sync[sync]) { if (++sync==SYNC_LEN) { sync=0; collecting=true; used=expected=0; } }
            else sync=(byte==s_sync[0])?1:0;
            continue;
        }
        if (used>=sizeof(data)) { collecting=false; continue; }
        data[used++]=byte;
        if (used==1) expected=(byte==0)?2U:((byte>0&&byte<=FRAME_MAX)?1U+byte+4U:0U);
        if (!expected) { collecting=false; continue; }
        if (used==expected) {
            if (data[0]==0 && data[1]==0x01) { uint8_t ack=0x06; send_control(&ack,1); }
            else if (data[0]==0 && data[1]==0x04) { uint8_t info[66]; size_t l=strlen(MODULE_NAME); info[0]=0x05; info[1]=(uint8_t)l; memcpy(info+2,MODULE_NAME,l); send_control(info,l+2); }
            else handle_stream(data,used);
            collecting=false; used=expected=0;
        }
    }
}

static void imu_menu_task(void *arg)
{
    (void)arg; uint8_t raw[14]; int64_t last_oled_refresh_us=0;
    while (true) {
        if (i2c_read_reg(MPU_ADDR,0x3B,raw,sizeof(raw))==ESP_OK) {
            int16_t accel_raw[3],gyro_raw[3];
            float accel_g[3],gyro_unbiased[3];
            for(int i=0;i<3;i++) {
                accel_raw[i]=(int16_t)((raw[i*2]<<8)|raw[i*2+1]);
                gyro_raw[i]=(int16_t)((raw[8+i*2]<<8)|raw[9+i*2]);
                accel_g[i]=(float)accel_raw[i]/16384.0f;
                gyro_unbiased[i]=(float)gyro_raw[i]/131.0f;
            }
            float roll_acc=atan2f(accel_g[1],accel_g[2])*RAD_TO_DEG;
            float pitch_acc=atan2f(-accel_g[0],sqrtf(accel_g[1]*accel_g[1]+accel_g[2]*accel_g[2]))*RAD_TO_DEG;
            int64_t now_us=esp_timer_get_time(); bool save_calibration=false; bool refresh_oled=false;
            xSemaphoreTake(s_app.lock,portMAX_DELAY);
            s_app.mpu_ok=true;
            memcpy(s_app.accel,accel_raw,sizeof(accel_raw));
            memcpy(s_app.gyro,gyro_raw,sizeof(gyro_raw));
            memcpy(s_app.accel_g,accel_g,sizeof(accel_g));
            float dt=(s_app.imu_last_sample_us>0)?(float)(now_us-s_app.imu_last_sample_us)/1000000.0f:0.02f;
            if (dt<0.001f || dt>0.1f) dt=0.02f;
            s_app.imu_last_sample_us=now_us;
            if (s_app.imu_calibrating) {
                for (size_t i=0;i<3;i++) s_app.imu_calibration_sum[i]+=gyro_unbiased[i];
                s_app.imu_calibration_samples++;
                if (s_app.imu_calibration_samples>=IMU_CALIBRATION_SAMPLES) {
                    for (size_t i=0;i<3;i++) {
                        s_app.gyro_bias_dps[i]=s_app.imu_calibration_sum[i]/(float)IMU_CALIBRATION_SAMPLES;
                    }
                    s_app.imu_calibrating=false;
                    s_app.imu_calibrated=true;
                    s_app.euler_deg[0]=roll_acc;
                    s_app.euler_deg[1]=pitch_acc;
                    s_app.euler_deg[2]=0.0f;
                    s_app.imu_filter_ready=true;
                    save_calibration=true;
                }
            }
            for (size_t i=0;i<3;i++) s_app.gyro_dps[i]=gyro_unbiased[i]-s_app.gyro_bias_dps[i];
            if (!s_app.imu_filter_ready) {
                s_app.euler_deg[0]=roll_acc;
                s_app.euler_deg[1]=pitch_acc;
                s_app.euler_deg[2]=0.0f;
                s_app.imu_filter_ready=true;
            } else if (!s_app.imu_calibrating) {
                s_app.euler_deg[0]=COMPLEMENTARY_ALPHA*(s_app.euler_deg[0]+s_app.gyro_dps[0]*dt)
                    +(1.0f-COMPLEMENTARY_ALPHA)*roll_acc;
                s_app.euler_deg[1]=COMPLEMENTARY_ALPHA*(s_app.euler_deg[1]+s_app.gyro_dps[1]*dt)
                    +(1.0f-COMPLEMENTARY_ALPHA)*pitch_acc;
                s_app.euler_deg[2]=wrap_degrees(s_app.euler_deg[2]+s_app.gyro_dps[2]*dt);
            }
            refresh_oled=s_app.menu_view==OLED_MENU_IMU && now_us-last_oled_refresh_us>=200000LL;
            xSemaphoreGive(s_app.lock);
            if (save_calibration) (void)save_imu_calibration();
            if (refresh_oled) { last_oled_refresh_us=now_us; oled_render(); }
        } else {
            xSemaphoreTake(s_app.lock,portMAX_DELAY); s_app.mpu_ok=false; xSemaphoreGive(s_app.lock);
        }
        vTaskDelay(pdMS_TO_TICKS(IMU_SAMPLE_PERIOD_MS));
    }
}

static void encoder_task(void *arg)
{
    (void)arg;
    static const int8_t transitions[16] = {
        0,-1,1,0, 1,0,0,-1, -1,0,0,1, 0,1,-1,0};
    uint8_t previous = (uint8_t)((gpio_get_level(ENCODER_CLK) << 1) |
                                 gpio_get_level(ENCODER_DT));
    int8_t accumulator = 0;
    bool previous_switch = gpio_get_level(ENCODER_SW) != 0;
    int64_t last_press_us = 0;
    while (true) {
        uint8_t current = (uint8_t)((gpio_get_level(ENCODER_CLK) << 1) |
                                    gpio_get_level(ENCODER_DT));
        if (current != previous) {
            accumulator += transitions[(previous << 2) | current];
            previous = current;
            if (accumulator >= 4 || accumulator <= -4) {
                int direction = accumulator > 0 ? 1 : -1;
                accumulator = 0;
                bool servo_changed=false; uint8_t servo_channel=0U; uint16_t servo_us=1500U;
                bool traction_changed=false; uint8_t traction_module=0U,traction_action=0U;
                int16_t traction_value=0;
                xSemaphoreTake(s_app.lock, portMAX_DELAY);
                if (!s_app.execution_active) {
                    s_app.encoder_position += direction;
                    int count=1;
                    if (s_app.menu_view==OLED_MENU_ROOT) count=4;
                    else if (s_app.menu_view==OLED_MENU_MODULES) count=(int)s_app.connected_module_count+1;
                    else if (s_app.menu_view==OLED_MENU_SERVOS) count=7;
                    else if (s_app.menu_view==OLED_MENU_EXECUTION) count=(int)MENU_COUNT+1;
                    else if (s_app.menu_view==OLED_MENU_TRACTION) count=4;
                    if (s_app.menu_view==OLED_MENU_SERVO_CONTROL) {
                        int angle=(int)s_app.servo_control_angle+direction*5;
                        if (angle<0) angle=0;
                        if (angle>180) angle=180;
                        s_app.servo_control_angle=(uint8_t)angle;
                        servo_channel=s_app.servo_control_channel;
                        servo_us=(uint16_t)(500+(angle*2000)/180);
                        s_app.cfg.servo_us[servo_channel]=servo_us;
                        servo_changed=true;
                    } else if (s_app.menu_view==OLED_MENU_TRACTION_CONTROL) {
                        int step=s_app.traction_control_action==0U?10:5;
                        int minimum=s_app.traction_control_action==0U?-3600:(s_app.traction_control_action==1U?-150:-100);
                        int maximum=s_app.traction_control_action==0U?3600:(s_app.traction_control_action==1U?150:100);
                        int next=(int)s_app.traction_control_value+direction*step;
                        if (next<minimum) next=minimum;
                        if (next>maximum) next=maximum;
                        s_app.traction_control_value=(int16_t)next;
                        traction_module=s_app.traction_module_index;
                        traction_action=s_app.traction_control_action;
                        traction_value=s_app.traction_control_value;
                        if (traction_module<CONNECTED_MODULE_COUNT && traction_action<3U) {
                            s_app.traction_control_values[traction_module][traction_action]=traction_value;
                        }
                        traction_changed=true;
                    } else {
                        int cursor=(int)s_app.menu_cursor+direction;
                        if (cursor<0) cursor=count-1; else if (cursor>=count) cursor=0;
                        s_app.menu_cursor=(uint8_t)cursor;
                    }
                }
                xSemaphoreGive(s_app.lock);
                if (servo_changed) set_servo_us(servo_channel,servo_us);
                if (traction_changed) {
                    send_traction_request(traction_module,traction_action,traction_value,false);
                }
                oled_render();
            }
        }
        bool switch_high = gpio_get_level(ENCODER_SW) != 0;
        s_app.encoder_pressed = !switch_high;
        int64_t now_us = esp_timer_get_time();
        if (previous_switch && !switch_high && now_us - last_press_us > 250000) {
            last_press_us = now_us;
            bool execute_item=false; bool persist_servo=false; bool traction_send=false;
            uint8_t traction_module=0U,traction_action=0U; int16_t traction_value=0;
            xSemaphoreTake(s_app.lock, portMAX_DELAY);
            bool execution_active = s_app.execution_active;
            if (!execution_active) {
                if (s_app.menu_view==OLED_MENU_ROOT) {
                    if (s_app.menu_cursor==0U) s_app.menu_view=OLED_MENU_MODULES;
                    else if (s_app.menu_cursor==1U) s_app.menu_view=OLED_MENU_SERVOS;
                    else if (s_app.menu_cursor==2U) s_app.menu_view=OLED_MENU_IMU;
                    else s_app.menu_view=OLED_MENU_EXECUTION;
                    s_app.menu_cursor=0U;
                } else if (s_app.menu_view==OLED_MENU_MODULES) {
                    if (s_app.menu_cursor==0U) { s_app.menu_view=OLED_MENU_ROOT; s_app.menu_cursor=0U; }
                    else {
                        uint8_t index=(uint8_t)(s_app.menu_cursor-1U);
                        if (index<s_app.connected_module_count && s_app.connected_module_kinds[index]==1U) {
                            s_app.traction_module_index=index;
                            s_app.menu_view=OLED_MENU_TRACTION;
                            s_app.menu_cursor=0U;
                        }
                    }
                } else if (s_app.menu_view==OLED_MENU_SERVOS) {
                    if (s_app.menu_cursor==0U) { s_app.menu_view=OLED_MENU_ROOT; s_app.menu_cursor=1U; }
                    else {
                        s_app.servo_control_channel=(uint8_t)(s_app.menu_cursor-1U);
                        uint16_t us=s_app.cfg.servo_us[s_app.servo_control_channel];
                        s_app.servo_control_angle=(uint8_t)(((uint32_t)(us-500U)*180U)/2000U);
                        s_app.menu_view=OLED_MENU_SERVO_CONTROL;
                    }
                } else if (s_app.menu_view==OLED_MENU_SERVO_CONTROL) {
                    s_app.menu_view=OLED_MENU_SERVOS;
                    s_app.menu_cursor=(uint8_t)(s_app.servo_control_channel+1U);
                    persist_servo=true;
                } else if (s_app.menu_view==OLED_MENU_IMU) {
                    s_app.menu_view=OLED_MENU_ROOT;
                    s_app.menu_cursor=2U;
                } else if (s_app.menu_view==OLED_MENU_TRACTION) {
                    if (s_app.menu_cursor==0U) {
                        s_app.menu_view=OLED_MENU_MODULES;
                        s_app.menu_cursor=(uint8_t)(s_app.traction_module_index+1U);
                    } else if (s_app.menu_cursor<=3U) {
                        s_app.traction_control_action=(uint8_t)(s_app.menu_cursor-1U);
                        s_app.traction_control_value=s_app.traction_control_values
                            [s_app.traction_module_index][s_app.traction_control_action];
                        s_app.traction_live_state=0U;
                        s_app.traction_feedback_visible=false;
                        s_app.menu_view=OLED_MENU_TRACTION_CONTROL;
                    }
                } else if (s_app.menu_view==OLED_MENU_TRACTION_CONTROL) {
                    if (s_app.traction_control_action==1U) {
                        traction_send=true;
                        traction_module=s_app.traction_module_index;
                        traction_action=3U;
                        traction_value=0;
                        s_app.traction_control_values[traction_module][2]=0;
                    }
                    s_app.menu_view=OLED_MENU_TRACTION;
                    s_app.menu_cursor=(uint8_t)(s_app.traction_control_action+1U);
                } else if (s_app.menu_cursor==0U) {
                    s_app.menu_view=OLED_MENU_ROOT; s_app.menu_cursor=3U;
                } else {
                    s_app.selected=(uint8_t)(s_app.menu_cursor-1U);
                    s_app.switch_feedback_visible=true;
                    s_app.switch_feedback_has_command=s_app.cfg.menu[s_app.selected].enabled!=0U;
                    s_app.switch_feedback_until_us=now_us+1000000LL;
                    execute_item=true;
                }
            }
            xSemaphoreGive(s_app.lock);
            if (execution_active) stop_selected_execution();
            else {
                if (persist_servo) (void)save_cfg();
                if (execute_item) execute_selected();
                if (traction_send) send_traction_request(
                    traction_module,traction_action,traction_value,false);
                oled_render();
            }
        }
        bool feedback_expired = false;
        bool execution_timed_out = false;
        xSemaphoreTake(s_app.lock, portMAX_DELAY);
        if (s_app.execution_active && s_app.execution_deadline_us > 0
                && now_us >= s_app.execution_deadline_us) {
            s_app.execution_active=false;
            s_app.execution_stopping=false;
            s_app.execution_result=2U;
            s_app.execution_deadline_us=0;
            s_app.switch_feedback_visible=true;
            s_app.switch_feedback_until_us=now_us+1500000LL;
            execution_timed_out=true;
        }
        if (s_app.switch_feedback_visible && now_us >= s_app.switch_feedback_until_us) {
            s_app.switch_feedback_visible = false;
            feedback_expired = true;
        }
        if (s_app.traction_feedback_visible && now_us >= s_app.traction_feedback_until_us) {
            s_app.traction_feedback_visible=false;
            feedback_expired=true;
        }
        xSemaphoreGive(s_app.lock);
        if (feedback_expired || execution_timed_out) oled_render();
        previous_switch = switch_high;
        vTaskDelay(pdMS_TO_TICKS(2));
    }
}

void app_main(void)
{
    s_app.lock=xSemaphoreCreateMutex();
    s_app.tx_lock=xSemaphoreCreateMutex();
    s_app.i2c_lock=xSemaphoreCreateMutex();
    ESP_ERROR_CHECK((s_app.lock && s_app.tx_lock && s_app.i2c_lock)?ESP_OK:ESP_ERR_NO_MEM);
    s_app.gesture_armed=true;
    s_app.event_seq = 0x800000U;
    ESP_ERROR_CHECK(nvs_flash_init()); load_cfg(); load_imu_calibration();
    i2c_config_t icfg={.mode=I2C_MODE_MASTER,.sda_io_num=I2C_SDA,.scl_io_num=I2C_SCL,.sda_pullup_en=GPIO_PULLUP_ENABLE,.scl_pullup_en=GPIO_PULLUP_ENABLE,.master.clk_speed=400000};
    ESP_ERROR_CHECK(i2c_param_config(I2C_PORT,&icfg)); ESP_ERROR_CHECK(i2c_driver_install(I2C_PORT,I2C_MODE_MASTER,0,0,0));
    uint8_t wake[2]={0x6B,0x00}; s_app.mpu_ok=i2c_write(MPU_ADDR,wake,sizeof(wake))==ESP_OK;
    const uint8_t dlpf[2]={0x1A,0x03}; (void)i2c_write(MPU_ADDR,dlpf,sizeof(dlpf));
    const uint8_t sample_rate[2]={0x19,0x09}; (void)i2c_write(MPU_ADDR,sample_rate,sizeof(sample_rate));
    const uint8_t gyro_range[2]={0x1B,0x00}; (void)i2c_write(MPU_ADDR,gyro_range,sizeof(gyro_range));
    const uint8_t accel_range[2]={0x1C,0x00}; (void)i2c_write(MPU_ADDR,accel_range,sizeof(accel_range));
    oled_init();
    ledc_timer_config_t timer={.speed_mode=LEDC_HIGH_SPEED_MODE,.duty_resolution=LEDC_TIMER_16_BIT,.timer_num=LEDC_TIMER_0,.freq_hz=50,.clk_cfg=LEDC_AUTO_CLK}; ESP_ERROR_CHECK(ledc_timer_config(&timer));
    for(size_t i=0;i<6;i++) { ledc_channel_config_t ch={.gpio_num=s_servo_pins[i],.speed_mode=LEDC_HIGH_SPEED_MODE,.channel=(ledc_channel_t)i,.intr_type=LEDC_INTR_DISABLE,.timer_sel=LEDC_TIMER_0,.duty=0,.hpoint=0}; ESP_ERROR_CHECK(ledc_channel_config(&ch)); set_servo_us(i,s_app.cfg.servo_us[i]); }
    for(size_t i=0;i<12;i++) {
        bool output_capable = i < 9U;
        bool output = output_capable && s_app.cfg.gpio_mode[i];
        gpio_set_direction(s_control_pins[i],output?GPIO_MODE_OUTPUT:GPIO_MODE_INPUT);
        if(output) gpio_set_level(s_control_pins[i],s_app.cfg.gpio_value[i]);
        if(!output_capable) s_app.cfg.gpio_mode[i]=0;
    }
    gpio_config_t encoder_cfg = {
        .pin_bit_mask = (1ULL << ENCODER_CLK) | (1ULL << ENCODER_DT) | (1ULL << ENCODER_SW),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&encoder_cfg));
    uart_config_t uart={.baud_rate=512000,.data_bits=UART_DATA_8_BITS,.parity=UART_PARITY_DISABLE,.stop_bits=UART_STOP_BITS_1,.flow_ctrl=UART_HW_FLOWCTRL_DISABLE,.source_clk=UART_SCLK_DEFAULT};
    ESP_ERROR_CHECK(uart_param_config(UART_NUM_0,&uart)); ESP_ERROR_CHECK(uart_set_pin(UART_NUM_0,UART_PIN_NO_CHANGE,UART_PIN_NO_CHANGE,UART_PIN_NO_CHANGE,UART_PIN_NO_CHANGE)); ESP_ERROR_CHECK(uart_driver_install(UART_NUM_0,2048,0,0,NULL,0));
    oled_render();
    ESP_ERROR_CHECK(xTaskCreate(comm_task,"hub_comm",6144,NULL,6,NULL)==pdPASS?ESP_OK:ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(xTaskCreate(imu_menu_task,"hub_menu",4096,NULL,5,NULL)==pdPASS?ESP_OK:ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(xTaskCreate(encoder_task,"hub_encoder",6144,NULL,5,NULL)==pdPASS?ESP_OK:ESP_ERR_NO_MEM);
    ESP_LOGI(TAG,"ready id=0x15 OLED/MPU SDA=33 SCL=32");
}
