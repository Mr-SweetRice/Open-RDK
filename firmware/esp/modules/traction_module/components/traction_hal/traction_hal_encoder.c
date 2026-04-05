#include "traction_hal.h"

#include "freertos/FreeRTOS.h"
#include "freertos/portmacro.h"
#include "esp_err.h"
#include "esp_attr.h"

static traction_encoder_cfg_t s_enc_cfg;
static volatile int64_t s_count = 0;
static volatile uint8_t s_prev_state = 0;
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
static bool s_inited = false;

// Tabela padrão de quadratura (Gray code) para delta
// idx = (prev<<2) | curr, prev/curr em [0..3] (bits: A<<1 | B)
DRAM_ATTR static const int8_t s_quad_table[16] = {
     0, -1, +1,  0,
    +1,  0,  0, -1,
    -1,  0,  0, +1,
     0, +1, -1,  0
};

static inline uint8_t IRAM_ATTR read_state(void)
{
    // A como bit1, B como bit0
    uint8_t a = (uint8_t)gpio_get_level(s_enc_cfg.pin_a);
    uint8_t b = (uint8_t)gpio_get_level(s_enc_cfg.pin_b);
    return (uint8_t)((a << 1) | b);
}

static inline traction_encoder_mode_t IRAM_ATTR encoder_mode_or_default(traction_encoder_mode_t mode)
{
    if (mode == TRACTION_ENCODER_MODE_QUADRATURE_X2 ||
        mode == TRACTION_ENCODER_MODE_SINGLE_CHANNEL) {
        return mode;
    }
    return TRACTION_ENCODER_MODE_QUADRATURE_X4;
}

static void IRAM_ATTR encoder_isr(void *arg)
{
    (void)arg;
    uint8_t curr = read_state();
    uint8_t idx  = (uint8_t)((s_prev_state << 2) | curr);
    int8_t delta = s_quad_table[idx];

    const bool prev_a = ((s_prev_state >> 1) & 0x1U) != 0U;
    const bool curr_a = ((curr >> 1) & 0x1U) != 0U;
    const traction_encoder_mode_t mode = encoder_mode_or_default(s_enc_cfg.mode);
    if (mode == TRACTION_ENCODER_MODE_QUADRATURE_X2) {
        if (prev_a == curr_a) {
            delta = 0;
        }
    } else if (mode == TRACTION_ENCODER_MODE_SINGLE_CHANNEL) {
        if (prev_a || !curr_a) {
            delta = 0;
        }
    }

    if (s_enc_cfg.invert_direction) {
        delta = (int8_t)-delta;
    }

    portENTER_CRITICAL_ISR(&s_mux);
    s_count += (int64_t)delta;
    s_prev_state = curr;
    portEXIT_CRITICAL_ISR(&s_mux);
}

esp_err_t traction_encoder_init(const traction_encoder_cfg_t *cfg)
{
    if (!cfg) return ESP_ERR_INVALID_ARG;

    s_enc_cfg = *cfg;
    s_enc_cfg.mode = encoder_mode_or_default(s_enc_cfg.mode);

    // Config GPIOs como entrada com interrupção em qualquer borda
    gpio_config_t io = {
        .pin_bit_mask = (1ULL << s_enc_cfg.pin_a) | (1ULL << s_enc_cfg.pin_b),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = s_enc_cfg.pullup ? GPIO_PULLUP_ENABLE : GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_ANYEDGE,
    };
    esp_err_t err = gpio_config(&io);
    if (err != ESP_OK) return err;

    // Estado inicial
    s_prev_state = read_state();

    // ISR service (se já estiver instalado, ignore)
    err = gpio_install_isr_service(ESP_INTR_FLAG_IRAM);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) return err;

    err = gpio_isr_handler_add(s_enc_cfg.pin_a, encoder_isr, NULL);
    if (err != ESP_OK) return err;

    err = gpio_isr_handler_add(s_enc_cfg.pin_b, encoder_isr, NULL);
    if (err != ESP_OK) return err;

    s_inited = true;
    return ESP_OK;
}

esp_err_t traction_encoder_set_runtime_config(bool pullup,
                                              bool invert_direction,
                                              traction_encoder_mode_t mode,
                                              int32_t counts_per_motor_rev,
                                              int32_t gear_ratio)
{
    if (!s_inited) return ESP_ERR_INVALID_STATE;
    if (counts_per_motor_rev <= 0 || gear_ratio <= 0) return ESP_ERR_INVALID_ARG;

    gpio_pull_mode_t pull_mode = pullup ? GPIO_PULLUP_ONLY : GPIO_FLOATING;
    esp_err_t err = gpio_set_pull_mode(s_enc_cfg.pin_a, pull_mode);
    if (err != ESP_OK) return err;
    err = gpio_set_pull_mode(s_enc_cfg.pin_b, pull_mode);
    if (err != ESP_OK) return err;

    portENTER_CRITICAL(&s_mux);
    s_enc_cfg.pullup = pullup;
    s_enc_cfg.invert_direction = invert_direction;
    s_enc_cfg.mode = encoder_mode_or_default(mode);
    s_enc_cfg.counts_per_motor_rev = counts_per_motor_rev;
    s_enc_cfg.gear_ratio = gear_ratio;
    portEXIT_CRITICAL(&s_mux);
    return ESP_OK;
}

esp_err_t traction_encoder_get_runtime_config(traction_encoder_cfg_t *out_cfg)
{
    if (!s_inited) return ESP_ERR_INVALID_STATE;
    if (!out_cfg) return ESP_ERR_INVALID_ARG;

    portENTER_CRITICAL(&s_mux);
    *out_cfg = s_enc_cfg;
    portEXIT_CRITICAL(&s_mux);
    return ESP_OK;
}

void traction_encoder_reset(int64_t value)
{
    portENTER_CRITICAL(&s_mux);
    s_count = value;
    s_prev_state = read_state();
    portEXIT_CRITICAL(&s_mux);
}

esp_err_t traction_encoder_get_count(int64_t *out_count)
{
    if (!s_inited) return ESP_ERR_INVALID_STATE;
    if (!out_count) return ESP_ERR_INVALID_ARG;

    portENTER_CRITICAL(&s_mux);
    *out_count = s_count;
    portEXIT_CRITICAL(&s_mux);

    return ESP_OK;
}

esp_err_t traction_encoder_set_irq_enabled(bool enabled)
{
    if (!s_inited) return ESP_ERR_INVALID_STATE;

    esp_err_t err = enabled ? gpio_intr_enable(s_enc_cfg.pin_a) : gpio_intr_disable(s_enc_cfg.pin_a);
    if (err != ESP_OK) return err;

    err = enabled ? gpio_intr_enable(s_enc_cfg.pin_b) : gpio_intr_disable(s_enc_cfg.pin_b);
    return err;
}

int32_t traction_encoder_counts_per_motor_rev(void)
{
    return s_enc_cfg.counts_per_motor_rev;
}

int32_t traction_encoder_counts_per_output_rev(void)
{
    // Ex.: 44 * 45 = 1980
    return s_enc_cfg.counts_per_motor_rev * s_enc_cfg.gear_ratio;
}
