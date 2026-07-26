cmake_minimum_required(VERSION 3.20)
project(openrdkC VERSION 0.1.0 LANGUAGES C)

include(GNUInstallDirs)
find_package(Python3 REQUIRED COMPONENTS Interpreter Development.Module)

option(ORDKC_BUILD_TESTS "Build native C tests" OFF)
option(ORDKC_ENABLE_ASAN "Enable AddressSanitizer" OFF)
option(ORDKC_ENABLE_UBSAN "Enable UndefinedBehaviorSanitizer" OFF)

add_library(openrdkc_core STATIC
    src/core/runtime.c
    src/core/framing.c
)
set_target_properties(openrdkc_core PROPERTIES
    POSITION_INDEPENDENT_CODE ON
    C_STANDARD 11
    C_STANDARD_REQUIRED ON
    C_EXTENSIONS OFF
)
target_include_directories(openrdkc_core
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>
)
target_compile_options(openrdkc_core PRIVATE
    -Wall
    -Wextra
    -Wpedantic
    -Werror
)
if(ORDKC_ENABLE_ASAN)
    target_compile_options(openrdkc_core PRIVATE -fsanitize=address -fno-omit-frame-pointer)
    target_link_options(openrdkc_core PRIVATE -fsanitize=address)
endif()
if(ORDKC_ENABLE_UBSAN)
    target_compile_options(openrdkc_core PRIVATE -fsanitize=undefined -fno-omit-frame-pointer)
    target_link_options(openrdkc_core PRIVATE -fsanitize=undefined)
endif()

Python3_add_library(_openrdkC MODULE
    src/python/module.c
    WITH_SOABI
)
target_link_libraries(_openrdkC PRIVATE openrdkc_core Python3::Module)
target_include_directories(_openrdkC PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_compile_features(_openrdkC PRIVATE c_std_11)
target_compile_options(_openrdkC PRIVATE
    -Wall
    -Wextra
    -Werror
)
if(ORDKC_ENABLE_ASAN)
    target_compile_options(_openrdkC PRIVATE -fsanitize=address -fno-omit-frame-pointer)
    target_link_options(_openrdkC PRIVATE -fsanitize=address)
endif()
if(ORDKC_ENABLE_UBSAN)
    target_compile_options(_openrdkC PRIVATE -fsanitize=undefined -fno-omit-frame-pointer)
    target_link_options(_openrdkC PRIVATE -fsanitize=undefined)
endif()

if(ORDKC_BUILD_TESTS)
    enable_testing()

    add_executable(test_framing tests/c/test_framing.c)
    target_link_libraries(test_framing PRIVATE openrdkc_core)
    target_compile_options(test_framing PRIVATE -Wall -Wextra -Wpedantic -Werror)
    add_test(NAME framing COMMAND test_framing)

    add_executable(fuzz_framing_smoke tests/c/fuzz_framing.c)
    target_link_libraries(fuzz_framing_smoke PRIVATE openrdkc_core)
    target_compile_options(fuzz_framing_smoke PRIVATE -Wall -Wextra -Wpedantic -Werror)
    add_test(NAME framing_fuzz_smoke COMMAND fuzz_framing_smoke)

    if(ORDKC_ENABLE_ASAN)
        foreach(test_target test_framing fuzz_framing_smoke)
            target_compile_options(${test_target} PRIVATE
                -fsanitize=address
                -fno-omit-frame-pointer
                -fno-pie
            )
            target_link_options(${test_target} PRIVATE
                -fsanitize=address
                -no-pie
            )
        endforeach()
    endif()
    if(ORDKC_ENABLE_UBSAN)
        foreach(test_target test_framing fuzz_framing_smoke)
            target_compile_options(${test_target} PRIVATE
                -fsanitize=undefined
                -fno-omit-frame-pointer
            )
            target_link_options(${test_target} PRIVATE -fsanitize=undefined)
        endforeach()
    endif()
endif()

install(TARGETS _openrdkC
    LIBRARY DESTINATION openrdkC
    RUNTIME DESTINATION openrdkC
)

install(TARGETS openrdkc_core
    ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
)
install(DIRECTORY include/openrdkc
    DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}
)
