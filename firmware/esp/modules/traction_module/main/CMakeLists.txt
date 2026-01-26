idf_component_register(
    SRCS "main.c"
    INCLUDE_DIRS "."
    REQUIRES
        traction_control
        traction_storage
        traction_comm
        traction_hal
)
