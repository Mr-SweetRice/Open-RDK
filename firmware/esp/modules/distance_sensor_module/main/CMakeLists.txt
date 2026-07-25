idf_component_register(
    SRCS "main.c"
    INCLUDE_DIRS "."
    REQUIRES ds_comm ds_proc ds_sensor ds_storage
)
