idf_component_register(SRCS "color_comm.c"
                    INCLUDE_DIRS "include"
                    REQUIRES driver esp_timer usb vfs color_proc)

option(COLOR_COMM_ENABLE_LINE_FALLBACK "Enable legacy line-command fallback parser" OFF)
if(COLOR_COMM_ENABLE_LINE_FALLBACK)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE COLOR_COMM_ENABLE_LINE_FALLBACK=1)
else()
    target_compile_definitions(${COMPONENT_LIB} PRIVATE COLOR_COMM_ENABLE_LINE_FALLBACK=0)
endif()
