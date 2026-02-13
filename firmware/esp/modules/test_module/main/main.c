#include <stdint.h>
#include <stdio.h>
#include <stdbool.h>

#include "driver/usb_serial_jtag.h"
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define HELLO_BYTE ((uint8_t)0x01)
#define ACK_BYTE   ((uint8_t)0x06)
#define PING_BYTE  ((uint8_t)0x02)
#define PONG_BYTE  ((uint8_t)0x03)
#define MODULE_QUERY_BYTE      ((uint8_t)0x04)
#define MODULE_INFO_PREFIX_BYTE ((uint8_t)0x05)
#define HOST_MODULE_ID ((uint8_t)0x00)
#define TEST_MODULE_ID ((uint8_t)0x11)
#define MODULE_NAME "test_module"
#define MODULE_NAME_LEN ((uint8_t)(sizeof(MODULE_NAME) - 1U))
static const uint8_t FRAME_SYNC_BYTES[4] = { 0xAA, 0x55, 0xAA, 0x55 };

static void send_framed_byte(uint8_t module_id, uint8_t payload)
{
    uint8_t frame[6] = {
        FRAME_SYNC_BYTES[0],
        FRAME_SYNC_BYTES[1],
        FRAME_SYNC_BYTES[2],
        FRAME_SYNC_BYTES[3],
        module_id,
        payload
    };
    (void)usb_serial_jtag_write_bytes(frame, sizeof(frame), pdMS_TO_TICKS(50));
}

static void send_module_info_frame(void)
{
    uint8_t header[7] = {
        FRAME_SYNC_BYTES[0],
        FRAME_SYNC_BYTES[1],
        FRAME_SYNC_BYTES[2],
        FRAME_SYNC_BYTES[3],
        TEST_MODULE_ID,
        MODULE_INFO_PREFIX_BYTE,
        MODULE_NAME_LEN
    };
    (void)usb_serial_jtag_write_bytes(header, sizeof(header), pdMS_TO_TICKS(50));
    (void)usb_serial_jtag_write_bytes(
        (const uint8_t *)MODULE_NAME,
        MODULE_NAME_LEN,
        pdMS_TO_TICKS(50)
    );
}

void app_main(void)
{
    usb_serial_jtag_driver_config_t usb_cfg = {
        .rx_buffer_size = 256,
        .tx_buffer_size = 256,
    };

    esp_err_t err = usb_serial_jtag_driver_install(&usb_cfg);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        printf("usb_serial_jtag_driver_install failed: %d\n", (int)err);
    } else {
        printf(
            "test_module ready (sync AA55AA55, module_id=0x%02X): "
            "HELLO=0x%02X ACK=0x%02X PING=0x%02X PONG=0x%02X QUERY=0x%02X\n",
            TEST_MODULE_ID,
            HELLO_BYTE,
            ACK_BYTE,
            PING_BYTE,
            PONG_BYTE,
            MODULE_QUERY_BYTE
        );
    }

    uint8_t sync_index = 0;
    uint8_t source_module_id = HOST_MODULE_ID;
    bool have_module_id = false;
    while (1) {
        uint8_t rx = 0;
        int n = usb_serial_jtag_read_bytes(&rx, 1, pdMS_TO_TICKS(100));
        if (n <= 0) {
            continue;
        }

        if (sync_index < 4) {
            if (rx == FRAME_SYNC_BYTES[sync_index]) {
                sync_index++;
            } else if (rx == FRAME_SYNC_BYTES[0]) {
                sync_index = 1;
            } else {
                sync_index = 0;
            }
            continue;
        }

        if (!have_module_id) {
            source_module_id = rx;
            have_module_id = true;
            continue;
        }

        uint8_t command = rx;
        sync_index = 0;
        have_module_id = false;

        if (command == HELLO_BYTE) {
            send_framed_byte(TEST_MODULE_ID, ACK_BYTE);
            printf(
                "HELLO frame received from module_id=0x%02X (0x%02X), "
                "ACK frame sent with module_id=0x%02X (0x%02X)\n",
                source_module_id,
                HELLO_BYTE,
                TEST_MODULE_ID,
                ACK_BYTE
            );
        } else if (command == PING_BYTE) {
            send_framed_byte(TEST_MODULE_ID, PONG_BYTE);
            printf(
                "PING frame received from module_id=0x%02X (0x%02X), "
                "PONG frame sent with module_id=0x%02X (0x%02X)\n",
                source_module_id,
                PING_BYTE,
                TEST_MODULE_ID,
                PONG_BYTE
            );
        } else if (command == MODULE_QUERY_BYTE) {
            send_module_info_frame();
            printf(
                "MODULE query frame received from module_id=0x%02X (0x%02X), "
                "sent module_id=0x%02X name=%s\n",
                source_module_id,
                MODULE_QUERY_BYTE,
                TEST_MODULE_ID,
                MODULE_NAME
            );
        } else {
            printf(
                "Unexpected framed command byte from module_id=0x%02X: 0x%02X\n",
                source_module_id,
                command
            );
        }
    }
}
