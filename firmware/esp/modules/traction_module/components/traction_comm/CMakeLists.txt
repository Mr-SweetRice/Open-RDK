idf_component_register(
    SRCS
        "traction_comm.c"
    INCLUDE_DIRS
        "include"
    REQUIRES
        esp_timer
        driver
        vfs
)

option(TRACTION_COMM_ENABLE_LINE_FALLBACK "Enable legacy line-command fallback parser" OFF)
if(TRACTION_COMM_ENABLE_LINE_FALLBACK)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE TRACTION_COMM_ENABLE_LINE_FALLBACK=1)
else()
    target_compile_definitions(${COMPONENT_LIB} PRIVATE TRACTION_COMM_ENABLE_LINE_FALLBACK=0)
endif()
