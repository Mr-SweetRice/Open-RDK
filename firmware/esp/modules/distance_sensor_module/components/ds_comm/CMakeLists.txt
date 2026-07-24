idf_component_register(
    SRCS "ds_comm.c"
    INCLUDE_DIRS "include"
    REQUIRES driver esp_timer usb vfs
)
