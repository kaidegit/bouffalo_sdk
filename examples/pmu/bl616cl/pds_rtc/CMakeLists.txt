cmake_minimum_required(VERSION 3.15)


find_package(bouffalo_sdk REQUIRED HINTS $ENV{BL_SDK_BASE})

if(CONFIG_PSRAM)
sdk_add_compile_definitions(-DPM_PDS_LDO18IO_POWER_DOWN=0)
target_sources(app PRIVATE psram_test.c)
else()
sdk_add_compile_definitions(-DPM_PDS_LDO18IO_POWER_DOWN=1)
endif()

sdk_set_main_file(main.c)

project(pds_rtc)
