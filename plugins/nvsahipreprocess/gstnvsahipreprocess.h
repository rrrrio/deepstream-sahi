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
 * DeepStream SAHI Pre-Process Plugin
 *
 * Fork of nvdspreprocess that replaces static ROI groups with dynamic SAHI
 * slice computation. Reuses the same custom library interface for tensor
 * preparation and produces identical GstNvDsPreProcessBatchMeta for nvinfer.
 */

#ifndef __GST_NVSAHIPREPROCESS_H__
#define __GST_NVSAHIPREPROCESS_H__

#include <cuda.h>
#include <cuda_runtime.h>
#include <gst/base/gstbasetransform.h>
#include <gst/video/video.h>

#include <condition_variable>
#include <functional>
#include <mutex>
#include <thread>
#include <unordered_map>

#include "gst-nvquery.h"
#include "gstnvsahipreprocess_allocator.h"
#include "nvbufsurface.h"
#include "nvbufsurftransform.h"
#include "nvdspreprocess_interface.h"
#include "nvdspreprocess_meta.h"
#include "nvtx3/nvToolsExt.h"

#define PACKAGE "nvsahipreprocess"
#define VERSION "1.0"
#define LICENSE "Proprietary"
#define DESCRIPTION "DeepStream SAHI dynamic slice preprocessor"
#define BINARY_PACKAGE "DeepStream SAHI Preprocessing"
#define URL "https://github.com/rrrrio/deepstream-sahi"

G_BEGIN_DECLS

typedef struct _GstNvSahiPreProcess GstNvSahiPreProcess;
typedef struct _GstNvSahiPreProcessClass GstNvSahiPreProcessClass;

#define GST_TYPE_NVSAHIPREPROCESS (gst_nvsahipreprocess_get_type())
#define GST_NVSAHIPREPROCESS(obj)                               \
  (G_TYPE_CHECK_INSTANCE_CAST((obj), GST_TYPE_NVSAHIPREPROCESS, \
                              GstNvSahiPreProcess))
#define GST_NVSAHIPREPROCESS_CLASS(klass)                      \
  (G_TYPE_CHECK_CLASS_CAST((klass), GST_TYPE_NVSAHIPREPROCESS, \
                           GstNvSahiPreProcessClass))
#define GST_IS_NVSAHIPREPROCESS(obj) \
  (G_TYPE_CHECK_INSTANCE_TYPE((obj), GST_TYPE_NVSAHIPREPROCESS))
#define GST_IS_NVSAHIPREPROCESS_CLASS(klass) \
  (G_TYPE_CHECK_CLASS_TYPE((klass), GST_TYPE_NVSAHIPREPROCESS))

struct NvSahiPreProcessCustomBufImpl : public NvDsPreProcessCustomBuf {
  GstBuffer *gstbuf;
  GstNvSahiPreProcessMemory *memory;
};

class NvSahiPreProcessAcquirerImpl : public NvDsPreProcessAcquirer {
 public:
  NvSahiPreProcessAcquirerImpl(GstBufferPool *pool);
  NvDsPreProcessCustomBuf *acquire() override;
  gboolean release(NvDsPreProcessCustomBuf *) override;

 private:
  GstBufferPool *m_gstpool = nullptr;
};

struct _GstNvSahiPreProcess {
  GstBaseTransform base_trans;

  /* ── SAHI slice parameters (GStreamer properties) ──────────────────────── */
  guint slice_width;
  guint slice_height;
  gfloat overlap_width_ratio;
  gfloat overlap_height_ratio;
  gboolean enable_full_frame;

  /* ── Target GIE IDs ────────────────────────────────────────────────────── */
  std::vector<guint64> target_unique_ids;

  /* ── Custom lib ────────────────────────────────────────────────────────── */
  CustomCtx *custom_lib_ctx;
  CustomInitParams custom_initparams;
  void *custom_lib_handle;
  gchar *custom_lib_path;
  std::string custom_tensor_function_name;
  std::function<NvDsPreProcessStatus(
      CustomCtx *, NvDsPreProcessBatch *, NvDsPreProcessCustomBuf *&,
      CustomTensorParams &, NvDsPreProcessAcquirer *)>
      custom_tensor_function;

  /* ── Scaling pool ──────────────────────────────────────────────────────── */
  GstBufferPool *scaling_pool;
  NvDsPreProcessFormat scaling_pool_format;
  NvBufSurfaceMemType scaling_pool_memory_type;
  NvBufSurfTransform_Compute scaling_pool_compute_hw;
  NvBufSurfTransform_Inter scaling_pool_interpolation_filter;
  guint scaling_buf_pool_size;

  /* ── Tensor pool ───────────────────────────────────────────────────────── */
  guint meta_id;
  GstBufferPool *tensor_pool;
  guint tensor_buf_pool_size;
  std::unique_ptr<NvSahiPreProcessAcquirerImpl> acquire_impl;
  NvDsPreProcessCustomBuf *tensor_buf;
  NvDsPreProcessTensorParams tensor_params;

  /* ── Processing dimensions ─────────────────────────────────────────────── */
  gint processing_width;
  gint processing_height;
  cudaStream_t convert_stream;
  gboolean maintain_aspect_ratio;
  gboolean symmetric_padding;

  /* ── Processing queue (two-thread architecture) ────────────────────────── */
  GMutex preprocess_lock;
  GQueue *preprocess_queue;
  GCond preprocess_cond;
  GThread *output_thread;
  gboolean stop;

  /* ── Misc ──────────────────────────────────────────────────────────────── */
  guint unique_id;
  guint64 frame_num;
  NvBufSurface batch_insurf;
  NvBufSurface batch_outsurf;
  guint max_batch_size;
  guint gpu_id;
  gboolean enable;
  gchar *config_file_path;
  gboolean config_file_parse_successful;
  gulong current_batch_num;
  GstFlowReturn last_flow_ret;
  NvBufSurfTransformConfigParams transform_config_params;
  NvBufSurfTransformParams transform_params;
  nvtxDomainHandle_t nvtx_domain;
};

struct _GstNvSahiPreProcessClass {
  GstBaseTransformClass parent_class;
};

GType gst_nvsahipreprocess_get_type(void);

G_END_DECLS
#endif /* __GST_NVSAHIPREPROCESS_H__ */
