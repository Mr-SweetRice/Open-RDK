idf_component_register(SRCS "ls_comm.c"
                    INCLUDE_DIRS "include"
                    REQUIRES driver esp_timer usb vfs ls_sensor)

option(LS_COMM_ENABLE_LINE_FALLBACK "Enable legacy line-command fallback parser" OFF)
if(LS_COMM_ENABLE_LINE_FALLBACK)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE LS_COMM_ENABLE_LINE_FALLBACK=1)
else()
    target_compile_definitions(${COMPONENT_LIB} PRIVATE LS_COMM_ENABLE_LINE_FALLBACK=0)
endif()
