idf_component_register(SRCS "ls_comm.c"
                    INCLUDE_DIRS "include"
                    REQUIRES driver esp_timer usb ls_sensor)
