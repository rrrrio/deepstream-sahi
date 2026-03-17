cmake_minimum_required(VERSION 3.14)
project(nvsahi_plugins LANGUAGES C CXX)

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# ─── DeepStream / CUDA paths ─────────────────────────────────────────
set(DEEPSTREAM_DIR "/opt/nvidia/deepstream/deepstream" CACHE PATH "DeepStream SDK root")
set(CUDA_DIR "/usr/local/cuda" CACHE PATH "CUDA toolkit root")

# ─── GStreamer via pkg-config ─────────────────────────────────────────
find_package(PkgConfig REQUIRED)
pkg_check_modules(GST REQUIRED
    gstreamer-1.0
    gstreamer-base-1.0
    gstreamer-video-1.0
)

include_directories(
    ${GST_INCLUDE_DIRS}
    ${DEEPSTREAM_DIR}/sources/includes
    ${CUDA_DIR}/include
    ${CMAKE_SOURCE_DIR}/common
)

link_directories(
    ${GST_LIBRARY_DIRS}
    ${DEEPSTREAM_DIR}/lib
    ${CUDA_DIR}/lib64
)

# ─── nvsahipreprocess (C) ────────────────────────────────────────────
add_library(nvsahipreprocess SHARED
    nvsahipreprocess/gstnvsahipreprocess.c
)
target_link_libraries(nvsahipreprocess
    ${GST_LIBRARIES}
    nvdsgst_meta nvds_meta nvbufsurface
)

# ─── nvsahipostprocess (C++) ─────────────────────────────────────────
add_library(nvsahipostprocess SHARED
    nvsahipostprocess/gstnvsahipostprocess.cpp
)
target_link_libraries(nvsahipostprocess
    ${GST_LIBRARIES}
    nvdsgst_meta nvds_meta
)

# ─── Install ─────────────────────────────────────────────────────────
set(GST_PLUGIN_INSTALL_DIR "${DEEPSTREAM_DIR}/lib/gst-plugins"
    CACHE PATH "GStreamer plugin install directory")

install(TARGETS nvsahipreprocess nvsahipostprocess
    LIBRARY DESTINATION ${GST_PLUGIN_INSTALL_DIR}
)
