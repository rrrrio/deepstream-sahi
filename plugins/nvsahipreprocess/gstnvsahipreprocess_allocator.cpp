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
 * Allocates NvBufSurface batches on GPU for scaling pool and
 * raw CUDA memory for tensor pool.
 */

#include "gstnvsahipreprocess_allocator.h"

#include "cuda_runtime.h"

#define GST_TYPE_NVSAHIPREPROCESS_ALLOCATOR \
  (gst_nvsahipreprocess_allocator_get_type())
#define GST_NVSAHIPREPROCESS_ALLOCATOR(obj)                               \
  (G_TYPE_CHECK_INSTANCE_CAST((obj), GST_TYPE_NVSAHIPREPROCESS_ALLOCATOR, \
                              GstNvSahiPreProcessAllocator))
#define GST_IS_NVSAHIPREPROCESS_ALLOCATOR(obj) \
  (G_TYPE_CHECK_INSTANCE_TYPE((obj), GST_TYPE_NVSAHIPREPROCESS_ALLOCATOR))

typedef struct _GstNvSahiPreProcessAllocator GstNvSahiPreProcessAllocator;
typedef struct _GstNvSahiPreProcessAllocatorClass
    GstNvSahiPreProcessAllocatorClass;

G_GNUC_INTERNAL GType gst_nvsahipreprocess_allocator_get_type(void);

GST_DEBUG_CATEGORY_STATIC(gst_nvsahipreprocess_allocator_debug);
#define GST_CAT_DEFAULT gst_nvsahipreprocess_allocator_debug

struct _GstNvSahiPreProcessAllocator {
  GstAllocator allocator;
  guint gpu_id;
  GstNvSahiPreProcessVideoBufferAllocatorInfo *info;
  size_t raw_buf_size;
  gboolean debug_tensor;
};

struct _GstNvSahiPreProcessAllocatorClass {
  GstAllocatorClass parent_class;
};

#define _do_init                                                \
  GST_DEBUG_CATEGORY_INIT(gst_nvsahipreprocess_allocator_debug, \
                          "nvsahipreprocessallocator", 0,       \
                          "nvsahipreprocess allocator");
#define gst_nvsahipreprocess_allocator_parent_class parent_class
G_DEFINE_TYPE_WITH_CODE(GstNvSahiPreProcessAllocator,
                        gst_nvsahipreprocess_allocator, GST_TYPE_ALLOCATOR,
                        _do_init);

#define GST_NVSAHIPREPROCESS_MEMORY_TYPE "nvsahipreprocess"

typedef struct {
  GstMemory mem;
  GstNvSahiPreProcessMemory mem_preprocess;
} GstNvSahiPreProcessMem;

static GstMemory *gst_nvsahipreprocess_allocator_alloc(
    GstAllocator *allocator, gsize size, GstAllocationParams *params) {
  GstNvSahiPreProcessAllocator *sahi_alloc =
      GST_NVSAHIPREPROCESS_ALLOCATOR(allocator);
  GstNvSahiPreProcessMem *nvmem = new GstNvSahiPreProcessMem;
  GstNvSahiPreProcessMemory *tmem = &nvmem->mem_preprocess;
  NvBufSurfaceCreateParams create_params = {0};
  cudaError_t cudaReturn = cudaSuccess;

  if (sahi_alloc->info == NULL) {
    if (sahi_alloc->debug_tensor) {
      cudaReturn =
          cudaMallocHost(&tmem->dev_memory_ptr, sahi_alloc->raw_buf_size);
    } else {
      cudaReturn = cudaMalloc(&tmem->dev_memory_ptr, sahi_alloc->raw_buf_size);
    }
    if (cudaReturn != cudaSuccess) {
      GST_ERROR("failed to allocate cuda malloc for tensor: %s",
                cudaGetErrorName(cudaReturn));
      delete nvmem;
      return nullptr;
    }
    gst_memory_init((GstMemory *)nvmem, (GstMemoryFlags)0, allocator, nullptr,
                    size, params->align, 0, size);
    return (GstMemory *)nvmem;
  }

  create_params.gpuId = sahi_alloc->gpu_id;
  create_params.width = sahi_alloc->info->width;
  create_params.height = sahi_alloc->info->height;
  create_params.size = 0;
  create_params.isContiguous = 1;
  create_params.colorFormat = sahi_alloc->info->color_format;
  create_params.layout = NVBUF_LAYOUT_PITCH;
  create_params.memType = sahi_alloc->info->memory_type;

  if (NvBufSurfaceCreate(&tmem->surf, sahi_alloc->info->batch_size,
                         &create_params) != 0) {
    GST_ERROR("Could not allocate internal buffer pool for nvsahipreprocess");
    delete nvmem;
    return nullptr;
  }

  if (tmem->surf->memType == NVBUF_MEM_SURFACE_ARRAY) {
    if (NvBufSurfaceMapEglImage(tmem->surf, -1) != 0) {
      GST_ERROR("Could not map EglImage from NvBufSurface");
      delete nvmem;
      return nullptr;
    }
    tmem->egl_frames.resize(sahi_alloc->info->batch_size);
    tmem->cuda_resources.resize(sahi_alloc->info->batch_size);
  }

  tmem->frame_memory_ptrs.assign(sahi_alloc->info->batch_size, nullptr);

  for (guint i = 0; i < sahi_alloc->info->batch_size; i++) {
#if defined(__aarch64__)
    if (tmem->surf->memType == NVBUF_MEM_SURFACE_ARRAY) {
      if (cuGraphicsEGLRegisterImage(
              &tmem->cuda_resources[i],
              tmem->surf->surfaceList[i].mappedAddr.eglImage,
              CU_GRAPHICS_MAP_RESOURCE_FLAGS_NONE) != CUDA_SUCCESS) {
        delete nvmem;
        return nullptr;
      }
      if (cuGraphicsResourceGetMappedEglFrame(&tmem->egl_frames[i],
                                              tmem->cuda_resources[i], 0,
                                              0) != CUDA_SUCCESS) {
        delete nvmem;
        return nullptr;
      }
      tmem->frame_memory_ptrs[i] = (char *)tmem->egl_frames[i].frame.pPitch[0];
    } else
#endif
    {
      tmem->frame_memory_ptrs[i] = (char *)tmem->surf->surfaceList[i].dataPtr;
    }
  }

  gst_memory_init((GstMemory *)nvmem, (GstMemoryFlags)0, allocator, nullptr,
                  size, params->align, 0, size);
  return (GstMemory *)nvmem;
}

static void gst_nvsahipreprocess_allocator_free(GstAllocator *allocator,
                                                GstMemory *memory) {
  GstNvSahiPreProcessAllocator *sahi_alloc =
      GST_NVSAHIPREPROCESS_ALLOCATOR(allocator);
  GstNvSahiPreProcessMem *nvmem = (GstNvSahiPreProcessMem *)memory;
  GstNvSahiPreProcessMemory *tmem = &nvmem->mem_preprocess;

  if (sahi_alloc->info == NULL) {
    cudaFree(tmem->dev_memory_ptr);
    delete nvmem;
    return;
  }

  if (tmem->surf->memType == NVBUF_MEM_SURFACE_ARRAY) {
    for (size_t i = 0; i < sahi_alloc->info->batch_size; i++) {
      cuGraphicsUnregisterResource(tmem->cuda_resources[i]);
    }
  }

  NvBufSurfaceUnMapEglImage(tmem->surf, -1);
  NvBufSurfaceDestroy(tmem->surf);
  delete nvmem;
}

static gpointer gst_nvsahipreprocess_memory_map(GstMemory *mem, gsize maxsize,
                                                GstMapFlags flags) {
  GstNvSahiPreProcessMem *nvmem = (GstNvSahiPreProcessMem *)mem;
  return (gpointer)&nvmem->mem_preprocess;
}

static void gst_nvsahipreprocess_memory_unmap(GstMemory *mem) {}

static void gst_nvsahipreprocess_allocator_class_init(
    GstNvSahiPreProcessAllocatorClass *klass) {
  GstAllocatorClass *allocator_class = GST_ALLOCATOR_CLASS(klass);
  allocator_class->alloc =
      GST_DEBUG_FUNCPTR(gst_nvsahipreprocess_allocator_alloc);
  allocator_class->free =
      GST_DEBUG_FUNCPTR(gst_nvsahipreprocess_allocator_free);
}

static void gst_nvsahipreprocess_allocator_init(
    GstNvSahiPreProcessAllocator *allocator) {
  GstAllocator *parent = GST_ALLOCATOR_CAST(allocator);
  parent->mem_type = GST_NVSAHIPREPROCESS_MEMORY_TYPE;
  parent->mem_map = gst_nvsahipreprocess_memory_map;
  parent->mem_unmap = gst_nvsahipreprocess_memory_unmap;
}

GstAllocator *gst_nvsahipreprocess_allocator_new(
    GstNvSahiPreProcessVideoBufferAllocatorInfo *info, size_t raw_buf_size,
    guint gpu_id, gboolean debug_tensor) {
  GstNvSahiPreProcessAllocator *allocator =
      (GstNvSahiPreProcessAllocator *)g_object_new(
          GST_TYPE_NVSAHIPREPROCESS_ALLOCATOR, nullptr);

  if (info != NULL) {
    allocator->info = new GstNvSahiPreProcessVideoBufferAllocatorInfo;
    allocator->info->width = info->width;
    allocator->info->height = info->height;
    allocator->info->batch_size = info->batch_size;
    allocator->info->color_format = info->color_format;
    allocator->info->memory_type = info->memory_type;
  }

  allocator->gpu_id = gpu_id;
  allocator->raw_buf_size = raw_buf_size;
  allocator->debug_tensor = debug_tensor;
  return (GstAllocator *)allocator;
}

GstNvSahiPreProcessMemory *gst_nvsahipreprocess_buffer_get_memory(
    GstBuffer *buffer) {
  GstMemory *mem = gst_buffer_peek_memory(buffer, 0);
  if (!mem || !gst_memory_is_type(mem, GST_NVSAHIPREPROCESS_MEMORY_TYPE))
    return nullptr;
  return &(((GstNvSahiPreProcessMem *)mem)->mem_preprocess);
}
