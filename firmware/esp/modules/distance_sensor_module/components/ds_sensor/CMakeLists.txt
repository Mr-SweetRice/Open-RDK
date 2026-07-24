idf_component_register(
    SRCS "ds_sensor.c"
    INCLUDE_DIRS "include"
    REQUIRES driver esp_timer freertos
)
