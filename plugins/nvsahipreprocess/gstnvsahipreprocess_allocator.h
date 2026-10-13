/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 Yuhao Ding <d346863528@163.com>
 * SPDX-FileCopyrightText: Copyright (c) 2021-2025 NVIDIA CORPORATION &
 * AFFILIATES. All rights reserved. SPDX-License-Identifier:
 * LicenseRef-NvidiaProprietary
 *
 * This software contains source code provided by NVIDIA Corporation.
 * Original source: NVIDIA DeepStream SDK (gst-nvdspreprocess).
 *
 * NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
 * property and proprietary rights in and to this material, related
 * documentation and any modifications thereto. Any use, reproduction,
 * disclosure or distribution of this material and related documentation
 * without an express license agreement from NVIDIA CORPORATION or
 * its affiliates is strictly prohibited.
 *
 * Custom memory allocator for SAHI PreProcess plugin.
 * Identical logic to nvdspreprocess allocator.
 */

#ifndef __GSTNVSAHIPREPROCESSALLOCATOR_H__
#define __GSTNVSAHIPREPROCESSALLOCATOR_H__

#include <cuda_runtime_api.h>
#include <gst/gst.h>

#include <vector>

#include "cudaEGL.h"
#include "nvbufsurface.h"

typedef struct {
  NvBufSurface *surf;
  std::vector<CUgraphicsResource> cuda_resources;
  std::vector<CUeglFrame> egl_frames;
  void *dev_memory_ptr;
  std::vector<void *> frame_memory_ptrs;
} GstNvSahiPreProcessMemory;

GstNvSahiPreProcessMemory *gst_nvsahipreprocess_buffer_get_memory(
    GstBuffer *buffer);

typedef struct {
  guint width;
  guint height;
  NvBufSurfaceColorFormat color_format;
  guint batch_size;
  NvBufSurfaceMemType memory_type;
} GstNvSahiPreProcessVideoBufferAllocatorInfo;

GstAllocator *gst_nvsahipreprocess_allocator_new(
    GstNvSahiPreProcessVideoBufferAllocatorInfo *info, size_t raw_buf_size,
    guint gpu_id, gboolean debug_tensor);

#endif
