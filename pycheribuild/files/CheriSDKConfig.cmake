
get_filename_component(_cherisdk_rootdir ${CMAKE_CURRENT_LIST_DIR}/../../../ REALPATH)

set(CheriSDK_TOOLCHAIN_DIR "${_cherisdk_rootdir}/bin")
set(CheriSDK_SYSROOT_DIR "${_cherisdk_rootdir}/sysroot")

set(CheriSDK_CC "${CheriSDK_TOOLCHAIN_DIR}/clang")
set(CheriSDK_CXX "${CheriSDK_TOOLCHAIN_DIR}/clang++")

if(NOT EXISTS ${CheriSDK_CC})
    message(FATAL_ERROR "CHERI clang is missing! Expected it to be at ${CheriSDK_CC}")
endif()
