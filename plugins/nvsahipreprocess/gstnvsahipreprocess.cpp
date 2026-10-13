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
 * Dynamically computes SAHI slices per frame, crops + scales each slice to
 * network resolution via NvBufSurfTransform, then delegates tensor preparation
 * to the same custom library used by nvdspreprocess.
 *
 * Produces GstNvDsPreProcessBatchMeta identical to nvdspreprocess so nvinfer
 * can consume tensors and perform coordinate remapping transparently.
 *
 * Pipeline position:
 *   nvstreammux → nvsahipreprocess → nvinfer (input-tensor-meta=1)
 */

#include "gstnvsahipreprocess.h"

#include <dlfcn.h>
#include <stdint.h>
#include <string.h>
#include <sys/time.h>

#include <fstream>
#include <functional>
#include <iostream>
#include <sstream>
#include <string>

#include "gst-nvdscustomevent.h"
#include "gstnvsahipreprocess_allocator.h"
#include "nvsahipreprocess_property_parser.h"

GST_DEBUG_CATEGORY_STATIC(gst_nvsahipreprocess_debug);
#define GST_CAT_DEFAULT gst_nvsahipreprocess_debug

enum {
  PROP_0,
  PROP_UNIQUE_ID,
  PROP_ENABLE,
  PROP_GPU_DEVICE_ID,
  PROP_CONFIG_FILE,
  PROP_SLICE_WIDTH,
  PROP_SLICE_HEIGHT,
  PROP_OVERLAP_WIDTH_RATIO,
  PROP_OVERLAP_HEIGHT_RATIO,
  PROP_ENABLE_FULL_FRAME,
  PROP_TARGET_UNIQUE_IDS,
};

#define DEFAULT_UNIQUE_ID 15
#define DEFAULT_GPU_ID 0
#define DEFAULT_BATCH_SIZE 1
#define DEFAULT_PROCESSING_WIDTH 640
#define DEFAULT_PROCESSING_HEIGHT 640
#define DEFAULT_SCALING_BUF_POOL_SIZE 6
#define DEFAULT_TENSOR_BUF_POOL_SIZE 6
#define DEFAULT_SLICE_WIDTH 640
#define DEFAULT_SLICE_HEIGHT 640
#define DEFAULT_OVERLAP_WIDTH_RATIO 0.2f
#define DEFAULT_OVERLAP_HEIGHT_RATIO 0.2f
#define DEFAULT_ENABLE_FULL_FRAME TRUE
#define DEFAULT_CONFIG_FILE_PATH ""
#define DEFAULT_TARGET_UNIQUE_IDS ""

#define NVTX_TEAL_COLOR 0xFF008080

template <class T>
T *dlsym_ptr(void *handle, char const *name) {
  return reinterpret_cast<T *>(dlsym(handle, name));
}

#define GST_CAPS_FEATURE_MEMORY_NVMM "memory:NVMM"
static GstStaticPadTemplate gst_nvsahipreprocess_sink_template =
    GST_STATIC_PAD_TEMPLATE(
        "sink", GST_PAD_SINK, GST_PAD_ALWAYS,
        GST_STATIC_CAPS(GST_VIDEO_CAPS_MAKE_WITH_FEATURES(
            GST_CAPS_FEATURE_MEMORY_NVMM, "{ NV12, RGBA, I420 }")));

static GstStaticPadTemplate gst_nvsahipreprocess_src_template =
    GST_STATIC_PAD_TEMPLATE(
        "src", GST_PAD_SRC, GST_PAD_ALWAYS,
        GST_STATIC_CAPS(GST_VIDEO_CAPS_MAKE_WITH_FEATURES(
            GST_CAPS_FEATURE_MEMORY_NVMM, "{ NV12, RGBA, I420 }")));

#define gst_nvsahipreprocess_parent_class parent_class
G_DEFINE_TYPE(GstNvSahiPreProcess, gst_nvsahipreprocess,
              GST_TYPE_BASE_TRANSFORM);

static void gst_nvsahipreprocess_set_property(GObject *, guint, const GValue *,
                                              GParamSpec *);
static void gst_nvsahipreprocess_get_property(GObject *, guint, GValue *,
                                              GParamSpec *);
static gboolean gst_nvsahipreprocess_set_caps(GstBaseTransform *, GstCaps *,
                                              GstCaps *);
static gboolean gst_nvsahipreprocess_start(GstBaseTransform *);
static gboolean gst_nvsahipreprocess_stop(GstBaseTransform *);
static GstFlowReturn gst_nvsahipreprocess_submit_input_buffer(
    GstBaseTransform *, gboolean, GstBuffer *);
static GstFlowReturn gst_nvsahipreprocess_generate_output(GstBaseTransform *,
                                                          GstBuffer **);
static gpointer gst_nvsahipreprocess_output_loop(gpointer);
static gboolean gst_nvsahipreprocess_sink_event(GstBaseTransform *, GstEvent *);
static void gst_nvsahipreprocess_finalize(GObject *);

/* ═══════════════════════════════════════════════════════════════════════════
 * SAHI Slice Computation (ported from sahi/slicing.py get_slice_bboxes)
 * ═══════════════════════════════════════════════════════════════════════════
 */

struct SliceBBox {
  guint left, top, width, height;
};

static void compute_sahi_slices(guint image_width, guint image_height,
                                guint slice_width, guint slice_height,
                                gfloat overlap_w_ratio, gfloat overlap_h_ratio,
                                gboolean add_full_frame,
                                std::vector<SliceBBox> &out_slices) {
  out_slices.clear();

  guint w_overlap = (guint)(slice_width * overlap_w_ratio);
  guint h_overlap = (guint)(slice_height * overlap_h_ratio);
  guint w_step = (slice_width > w_overlap) ? (slice_width - w_overlap) : 1;
  guint h_step = (slice_height > h_overlap) ? (slice_height - h_overlap) : 1;

  for (guint y = 0; y < image_height; y += h_step) {
    guint y_end = MIN(y + slice_height, image_height);
    guint y_start = y;

    if (y_end == image_height && y_start > 0 &&
        (y_end - y_start) < slice_height) {
      y_start =
          (image_height > slice_height) ? (image_height - slice_height) : 0;
    }

    for (guint x = 0; x < image_width; x += w_step) {
      guint x_end = MIN(x + slice_width, image_width);
      guint x_start = x;

      if (x_end == image_width && x_start > 0 &&
          (x_end - x_start) < slice_width) {
        x_start = (image_width > slice_width) ? (image_width - slice_width) : 0;
      }

      out_slices.push_back(
          {x_start, y_start, x_end - x_start, y_end - y_start});

      if (x_end >= image_width) break;
    }

    if (y_end >= image_height) break;
  }

  if (add_full_frame) {
    out_slices.push_back({0, 0, image_width, image_height});
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Scale and fill batch (from nvdspreprocess, simplified — no group logic)
 * ═══════════════════════════════════════════════════════════════════════════
 */

static GstFlowReturn scale_and_fill_data(
    GstNvSahiPreProcess *self, NvBufSurfaceParams *src_frame,
    NvOSD_RectParams *crop_rect, gdouble &ratio_x, gdouble &ratio_y,
    guint &offset_left, guint &offset_top, NvBufSurface *dest_surf,
    NvBufSurfaceParams *dest_frame, void *destCudaPtr) {
  if (crop_rect->width == 0 || crop_rect->height == 0) {
    GST_ELEMENT_ERROR(self, STREAM, FAILED, ("crop_rect dimensions are zero"),
                      (NULL));
    return GST_FLOW_ERROR;
  }

  if (crop_rect->left + crop_rect->width > src_frame->width)
    crop_rect->width = src_frame->width - crop_rect->left;
  if (crop_rect->top + crop_rect->height > src_frame->height)
    crop_rect->height = src_frame->height - crop_rect->top;

  gint src_left = GST_ROUND_UP_2((unsigned int)crop_rect->left);
  gint src_top = GST_ROUND_UP_2((unsigned int)crop_rect->top);
  gint src_width = GST_ROUND_DOWN_2((unsigned int)crop_rect->width);
  gint src_height = GST_ROUND_DOWN_2((unsigned int)crop_rect->height);

  guint dest_width, dest_height;
  offset_left = 0;
  offset_top = 0;

  if (self->maintain_aspect_ratio) {
    double hdest = dest_frame->width * src_height / (double)src_width;
    double wdest = dest_frame->height * src_width / (double)src_height;
    int pixel_size;
    cudaError_t cudaReturn;

    if (hdest <= dest_frame->height) {
      dest_width = dest_frame->width;
      dest_height = (guint)hdest;
    } else {
      dest_width = (guint)wdest;
      dest_height = dest_frame->height;
    }

    switch (dest_frame->colorFormat) {
      case NVBUF_COLOR_FORMAT_RGBA:
        pixel_size = 4;
        break;
      case NVBUF_COLOR_FORMAT_RGB:
        pixel_size = 3;
        break;
      case NVBUF_COLOR_FORMAT_GRAY8:
      case NVBUF_COLOR_FORMAT_NV12:
        pixel_size = 1;
        break;
      default:
        g_assert_not_reached();
        break;
    }

    if (!self->symmetric_padding) {
      guint offset_right = dest_frame->width - dest_width;
      cudaReturn = cudaMemset2DAsync(
          (uint8_t *)destCudaPtr + pixel_size * dest_width,
          dest_frame->planeParams.pitch[0], 0, pixel_size * offset_right,
          dest_frame->height, self->convert_stream);
      if (cudaReturn != cudaSuccess) return GST_FLOW_ERROR;

      guint offset_bottom = dest_frame->height - dest_height;
      cudaReturn = cudaMemset2DAsync(
          (uint8_t *)destCudaPtr +
              dest_frame->planeParams.pitch[0] * dest_height,
          dest_frame->planeParams.pitch[0], 0, pixel_size * dest_width,
          offset_bottom, self->convert_stream);
      if (cudaReturn != cudaSuccess) return GST_FLOW_ERROR;
    } else {
      offset_left = (dest_frame->width - dest_width) / 2;
      cudaReturn = cudaMemset2DAsync(
          (uint8_t *)destCudaPtr, dest_frame->planeParams.pitch[0], 0,
          pixel_size * offset_left, dest_frame->height, self->convert_stream);
      if (cudaReturn != cudaSuccess) return GST_FLOW_ERROR;

      guint offset_right = dest_frame->width - dest_width - offset_left;
      cudaReturn = cudaMemset2DAsync(
          (uint8_t *)destCudaPtr + pixel_size * (dest_width + offset_left),
          dest_frame->planeParams.pitch[0], 0, pixel_size * offset_right,
          dest_frame->height, self->convert_stream);
      if (cudaReturn != cudaSuccess) return GST_FLOW_ERROR;

      offset_top = (dest_frame->height - dest_height) / 2;
      cudaReturn = cudaMemset2DAsync(
          (uint8_t *)destCudaPtr, dest_frame->planeParams.pitch[0], 0,
          pixel_size * dest_width, offset_top, self->convert_stream);
      if (cudaReturn != cudaSuccess) return GST_FLOW_ERROR;

      guint offset_bottom = dest_frame->height - dest_height - offset_top;
      cudaReturn = cudaMemset2DAsync(
          (uint8_t *)destCudaPtr +
              dest_frame->planeParams.pitch[0] * (dest_height + offset_top),
          dest_frame->planeParams.pitch[0], 0, pixel_size * dest_width,
          offset_bottom, self->convert_stream);
      if (cudaReturn != cudaSuccess) return GST_FLOW_ERROR;
    }
  } else {
    dest_width = self->processing_width;
    dest_height = self->processing_height;
  }

  ratio_x = (double)dest_width / src_width;
  ratio_y = (double)dest_height / src_height;

  self->batch_insurf.surfaceList[self->batch_insurf.numFilled] = *src_frame;
  self->batch_outsurf.surfaceList[self->batch_outsurf.numFilled] = *dest_frame;

  self->transform_params.src_rect[self->batch_insurf.numFilled] = {
      (guint)src_top, (guint)src_left, (guint)src_width, (guint)src_height};
  self->transform_params.dst_rect[self->batch_outsurf.numFilled] = {
      offset_top, offset_left, dest_width, dest_height};

  self->batch_insurf.numFilled++;
  self->batch_outsurf.numFilled++;
  self->batch_insurf.batchSize = self->batch_insurf.numFilled;
  self->batch_outsurf.batchSize = self->batch_outsurf.numFilled;

  return GST_FLOW_OK;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Batched transform helper
 * ═══════════════════════════════════════════════════════════════════════════
 */

static gboolean batch_transformation(GstNvSahiPreProcess *self,
                                     NvBufSurfTransformSyncObj_t *sync_obj) {
  NvBufSurfTransform_Error err;

  err = NvBufSurfTransformSetSessionParams(&self->transform_config_params);
  if (err != NvBufSurfTransformError_Success) {
    GST_ERROR("NvBufSurfTransformSetSessionParams failed: %d", err);
    return FALSE;
  }

  err = NvBufSurfTransformAsync(&self->batch_insurf, &self->batch_outsurf,
                                &self->transform_params, sync_obj);
  if (err != NvBufSurfTransformError_Success) {
    GST_ERROR("NvBufSurfTransformAsync failed: %d", err);
    return FALSE;
  }

  return TRUE;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Release / attach user meta at batch level (identical to nvdspreprocess)
 * ═══════════════════════════════════════════════════════════════════════════
 */

static void release_user_meta_at_batch_level(gpointer data,
                                             gpointer user_data) {
  NvDsUserMeta *user_meta = (NvDsUserMeta *)data;
  GstNvDsPreProcessBatchMeta *pp_meta =
      (GstNvDsPreProcessBatchMeta *)user_meta->user_meta_data;

  if (pp_meta->tensor_meta != nullptr) {
    auto *private_data_pair =
        (std::pair<GstNvSahiPreProcess *, NvDsPreProcessCustomBuf *> *)
            pp_meta->tensor_meta->private_data;

    GstNvSahiPreProcess *self = private_data_pair->first;
    NvDsPreProcessCustomBuf *buf = private_data_pair->second;

    NvSahiPreProcessAcquirerImpl *acq =
        (NvSahiPreProcessAcquirerImpl *)self->acquire_impl.get();
    if (acq != nullptr) acq->release(buf);
    delete private_data_pair;
    delete pp_meta->tensor_meta;
  }

  gst_buffer_unref((GstBuffer *)pp_meta->private_data);

  for (auto &roi_meta : pp_meta->roi_vector) {
    if (roi_meta.classifier_meta_list) {
      g_list_free(roi_meta.classifier_meta_list);
      roi_meta.classifier_meta_list = nullptr;
    }
    if (roi_meta.roi_user_meta_list) {
      g_list_free(roi_meta.roi_user_meta_list);
      roi_meta.roi_user_meta_list = nullptr;
    }
  }

  delete pp_meta;
}

static void attach_user_meta_at_batch_level(
    GstNvSahiPreProcess *self, NvDsPreProcessBatch *batch,
    CustomTensorParams custom_tensor_params, NvDsPreProcessStatus status) {
  GstNvDsPreProcessBatchMeta *pp_meta = new GstNvDsPreProcessBatchMeta;

  if (status == NVDSPREPROCESS_SUCCESS) {
    pp_meta->roi_vector.clear();
    pp_meta->roi_vector = custom_tensor_params.seq_params.roi_vector;

    pp_meta->tensor_meta = new NvDsPreProcessTensorMeta;
    pp_meta->tensor_meta->gpu_id = self->gpu_id;
    pp_meta->tensor_meta->private_data = new std::pair(self, self->tensor_buf);
    pp_meta->tensor_meta->meta_id = self->meta_id;
    self->meta_id++;
    pp_meta->tensor_meta->maintain_aspect_ratio = self->maintain_aspect_ratio;
    pp_meta->tensor_meta->raw_tensor_buffer =
        ((NvSahiPreProcessCustomBufImpl *)self->tensor_buf)
            ->memory->dev_memory_ptr;
    pp_meta->tensor_meta->tensor_shape =
        custom_tensor_params.params.network_input_shape;
    pp_meta->tensor_meta->buffer_size = custom_tensor_params.params.buffer_size;
    pp_meta->tensor_meta->data_type = custom_tensor_params.params.data_type;
    pp_meta->tensor_meta->tensor_name = custom_tensor_params.params.tensor_name;
  } else {
    pp_meta->roi_vector.clear();
    for (guint i = 0; i < batch->units.size(); i++)
      pp_meta->roi_vector.push_back(batch->units[i].roi_meta);
    pp_meta->tensor_meta = nullptr;
  }

  pp_meta->private_data = batch->converted_buf;
  pp_meta->target_unique_ids = self->target_unique_ids;

  NvDsBatchMeta *batch_meta = batch->batch_meta;
  NvDsUserMeta *user_meta = nvds_acquire_user_meta_from_pool(batch_meta);

  user_meta->user_meta_data = pp_meta;
  user_meta->base_meta.meta_type = (NvDsMetaType)NVDS_PREPROCESS_BATCH_META;
  user_meta->base_meta.copy_func = NULL;
  user_meta->base_meta.release_func = release_user_meta_at_batch_level;
  user_meta->base_meta.batch_meta = batch_meta;

  nvds_add_user_meta_to_batch(batch_meta, user_meta);
}

/* ═══════════════════════════════════════════════════════════════════════════
 * submit_input_buffer — SAHI dynamic slicing
 * ═══════════════════════════════════════════════════════════════════════════
 */

static GstFlowReturn gst_nvsahipreprocess_submit_input_buffer(
    GstBaseTransform *btrans, gboolean discont, GstBuffer *inbuf) {
  GstNvSahiPreProcess *self = GST_NVSAHIPREPROCESS(btrans);
  GstMapInfo in_map_info;
  NvBufSurface *in_surf;
  GstFlowReturn flow_ret = GST_FLOW_ERROR;
  std::unique_ptr<NvDsPreProcessBatch> batch = nullptr;
  GstNvSahiPreProcessMemory *memory = nullptr;
  GstBuffer *conv_gst_buf = nullptr;
  NvDsBatchMeta *batch_meta = NULL;
  gdouble scale_ratio_x, scale_ratio_y;
  guint offset_left, offset_top;
  std::vector<SliceBBox> slices;

  self->current_batch_num++;

  cudaError_t cudaReturn = cudaSetDevice(self->gpu_id);
  if (cudaReturn != cudaSuccess) {
    GST_ELEMENT_ERROR(
        self, RESOURCE, FAILED,
        ("cudaSetDevice failed: %s", cudaGetErrorName(cudaReturn)), (NULL));
    return GST_FLOW_ERROR;
  }

  if (!self->config_file_parse_successful) {
    GST_ELEMENT_ERROR(self, LIBRARY, SETTINGS,
                      ("Configuration file parsing failed"), (NULL));
    return GST_FLOW_ERROR;
  }

  if (!self->enable) {
    flow_ret = gst_pad_push(GST_BASE_TRANSFORM_SRC_PAD(self), inbuf);
    return flow_ret;
  }

  memset(&in_map_info, 0, sizeof(in_map_info));
  if (!gst_buffer_map(inbuf, &in_map_info, GST_MAP_READ)) {
    GST_ELEMENT_ERROR(self, STREAM, FAILED, ("gst_buffer_map failed"), (NULL));
    return GST_FLOW_ERROR;
  }
  in_surf = (NvBufSurface *)in_map_info.data;

  nvds_set_input_system_timestamp(inbuf, GST_ELEMENT_NAME(self));

  batch_meta = gst_buffer_get_nvds_batch_meta(inbuf);
  if (!batch_meta) {
    GST_ELEMENT_ERROR(self, STREAM, FAILED, ("NvDsBatchMeta not found"),
                      (NULL));
    gst_buffer_unmap(inbuf, &in_map_info);
    return GST_FLOW_ERROR;
  }

  for (NvDsMetaList *l_frame = batch_meta->frame_meta_list; l_frame != NULL;
       l_frame = l_frame->next) {
    NvDsFrameMeta *frame_meta = (NvDsFrameMeta *)l_frame->data;
    gint batch_index = frame_meta->batch_id;
    guint frame_width = in_surf->surfaceList[batch_index].width;
    guint frame_height = in_surf->surfaceList[batch_index].height;

    compute_sahi_slices(frame_width, frame_height, self->slice_width,
                        self->slice_height, self->overlap_width_ratio,
                        self->overlap_height_ratio, self->enable_full_frame,
                        slices);

    GST_DEBUG_OBJECT(self, "Source %d: %ux%u → %zu slices",
                     frame_meta->source_id, frame_width, frame_height,
                     slices.size());

    for (guint s = 0; s < slices.size(); s++) {
      SliceBBox &sl = slices[s];

      if (batch == nullptr) {
        batch.reset(new NvDsPreProcessBatch);
        batch->push_buffer = FALSE;
        batch->event_marker = FALSE;
        batch->inbuf = inbuf;
        batch->inbuf_batch_num = self->current_batch_num;
        batch->batch_meta = batch_meta;
        batch->scaling_pool_format = self->scaling_pool_format;

        flow_ret = gst_buffer_pool_acquire_buffer(self->scaling_pool,
                                                  &conv_gst_buf, nullptr);
        if (flow_ret != GST_FLOW_OK) {
          gst_buffer_unmap(inbuf, &in_map_info);
          return flow_ret;
        }
        memory = gst_nvsahipreprocess_buffer_get_memory(conv_gst_buf);
        if (!memory) {
          gst_buffer_unmap(inbuf, &in_map_info);
          return GST_FLOW_ERROR;
        }
        batch->converted_buf = conv_gst_buf;
        batch->pitch = memory->surf->surfaceList[0].planeParams.pitch[0];
      }

      gint idx = batch->units.size();

      NvOSD_RectParams rect_params;
      rect_params.left = (gfloat)sl.left;
      rect_params.top = (gfloat)sl.top;
      rect_params.width = (gfloat)sl.width;
      rect_params.height = (gfloat)sl.height;

      if (scale_and_fill_data(self, in_surf->surfaceList + batch_index,
                              &rect_params, scale_ratio_x, scale_ratio_y,
                              offset_left, offset_top, memory->surf,
                              memory->surf->surfaceList + idx,
                              memory->frame_memory_ptrs[idx]) != GST_FLOW_OK) {
        gst_buffer_unmap(inbuf, &in_map_info);
        return GST_FLOW_ERROR;
      }

      self->batch_insurf.memType = in_surf->memType;
      self->batch_outsurf.memType = memory->surf->memType;

      NvDsRoiMeta roi_meta = {{0}};
      roi_meta.roi = rect_params;
      roi_meta.converted_buffer =
          (NvBufSurfaceParams *)memory->surf->surfaceList + idx;
      roi_meta.scale_ratio_x = scale_ratio_x;
      roi_meta.scale_ratio_y = scale_ratio_y;
      roi_meta.offset_left = offset_left;
      roi_meta.offset_top = offset_top;
      roi_meta.frame_meta = frame_meta;
      roi_meta.object_meta = NULL;

      NvDsPreProcessUnit unit;
      unit.converted_frame_ptr = memory->frame_memory_ptrs[idx];
      unit.obj_meta = nullptr;
      unit.frame_meta = frame_meta;
      unit.frame_num = frame_meta->frame_num;
      unit.batch_index = batch_index;
      unit.input_surf_params = in_surf->surfaceList + batch_index;
      unit.roi_meta = roi_meta;
      unit.roi_meta.classifier_meta_list = NULL;
      unit.roi_meta.roi_user_meta_list = NULL;

      batch->units.push_back(unit);

      if (batch->units.size() == self->max_batch_size) {
        NvBufSurfTransformSyncObj_t sync_obj = NULL;
        if (!batch_transformation(self, &sync_obj)) {
          gst_buffer_unmap(inbuf, &in_map_info);
          return GST_FLOW_ERROR;
        }
        self->batch_insurf.numFilled = 0;
        self->batch_outsurf.numFilled = 0;

        if (sync_obj) batch->sync_objects.push_back(sync_obj);

        g_mutex_lock(&self->preprocess_lock);
        g_queue_push_tail(self->preprocess_queue, batch.get());
        g_cond_broadcast(&self->preprocess_cond);
        g_mutex_unlock(&self->preprocess_lock);

        conv_gst_buf = nullptr;
        batch.release();
      }
    }
  }

  if (self->batch_insurf.numFilled > 0 && batch != nullptr) {
    NvBufSurfTransformSyncObj_t sync_obj = NULL;
    if (!batch_transformation(self, &sync_obj)) {
      gst_buffer_unmap(inbuf, &in_map_info);
      return GST_FLOW_ERROR;
    }
    self->batch_insurf.numFilled = 0;
    self->batch_outsurf.numFilled = 0;

    if (sync_obj) batch->sync_objects.push_back(sync_obj);
  }

  if (batch != nullptr) {
    g_mutex_lock(&self->preprocess_lock);
    g_queue_push_tail(self->preprocess_queue, batch.get());
    g_cond_broadcast(&self->preprocess_cond);
    g_mutex_unlock(&self->preprocess_lock);
    conv_gst_buf = nullptr;
    batch.release();
  }

  NvDsPreProcessBatch *buf_push_batch = new NvDsPreProcessBatch;
  buf_push_batch->inbuf = inbuf;
  buf_push_batch->push_buffer = TRUE;
  buf_push_batch->nvtx_complete_buf_range = 0;

  g_mutex_lock(&self->preprocess_lock);
  g_queue_push_tail(self->preprocess_queue, buf_push_batch);
  g_cond_broadcast(&self->preprocess_cond);
  g_mutex_unlock(&self->preprocess_lock);

  flow_ret = GST_FLOW_OK;
  gst_buffer_unmap(inbuf, &in_map_info);
  return flow_ret;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Output loop — dequeue batches, call custom tensor prep, attach meta
 * ═══════════════════════════════════════════════════════════════════════════
 */

static gpointer gst_nvsahipreprocess_output_loop(gpointer data) {
  GstNvSahiPreProcess *self = GST_NVSAHIPREPROCESS(data);
  NvDsPreProcessStatus status = NVDSPREPROCESS_TENSOR_NOT_READY;

  cudaSetDevice(self->gpu_id);

  g_mutex_lock(&self->preprocess_lock);

  while (!self->stop) {
    std::unique_ptr<NvDsPreProcessBatch> batch = nullptr;

    if (g_queue_is_empty(self->preprocess_queue)) {
      g_cond_wait(&self->preprocess_cond, &self->preprocess_lock);
      continue;
    }

    batch.reset(
        (NvDsPreProcessBatch *)g_queue_pop_head(self->preprocess_queue));
    g_cond_broadcast(&self->preprocess_cond);

    if (batch->event_marker) continue;

    g_mutex_unlock(&self->preprocess_lock);

    if (batch->push_buffer) {
      nvds_set_output_system_timestamp(batch->inbuf, GST_ELEMENT_NAME(self));

      GstFlowReturn flow_ret =
          gst_pad_push(GST_BASE_TRANSFORM_SRC_PAD(self), batch->inbuf);
      if (self->last_flow_ret != flow_ret) {
        switch (flow_ret) {
          case GST_FLOW_ERROR:
          case GST_FLOW_NOT_LINKED:
          case GST_FLOW_NOT_NEGOTIATED:
            GST_ELEMENT_ERROR(self, STREAM, FAILED,
                              ("Internal data stream error."),
                              ("streaming stopped, reason %s (%d)",
                               gst_flow_get_name(flow_ret), flow_ret));
            break;
          default:
            break;
        }
      }
      self->last_flow_ret = flow_ret;
      self->meta_id = 0;
      g_mutex_lock(&self->preprocess_lock);
      continue;
    }

    for (auto sync_object : batch->sync_objects) {
      NvBufSurfTransformSyncObjWait(sync_object, -1);
      NvBufSurfTransformSyncObjDestroy(&sync_object);
    }

    CustomTensorParams custom_tensor_params;
    if (self->custom_lib_path && self->custom_lib_handle &&
        self->custom_tensor_function) {
      custom_tensor_params.params = self->tensor_params;
      custom_tensor_params.params.buffer_size =
          custom_tensor_params.params.buffer_size * batch->units.size() /
          self->tensor_params.network_input_shape[0];
      custom_tensor_params.seq_params.roi_vector.clear();

      for (guint i = 0; i < batch->units.size(); i++)
        custom_tensor_params.seq_params.roi_vector.push_back(
            batch->units[i].roi_meta);

      status = self->custom_tensor_function(
          self->custom_lib_ctx, batch.get(), self->tensor_buf,
          custom_tensor_params, self->acquire_impl.get());
    }

    attach_user_meta_at_batch_level(self, batch.get(), custom_tensor_params,
                                    status);

    g_mutex_lock(&self->preprocess_lock);
  }

  g_mutex_unlock(&self->preprocess_lock);
  return nullptr;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * generate_output
 * ═══════════════════════════════════════════════════════════════════════════
 */

static GstFlowReturn gst_nvsahipreprocess_generate_output(
    GstBaseTransform *btrans, GstBuffer **outbuf) {
  GstNvSahiPreProcess *self = GST_NVSAHIPREPROCESS(btrans);
  return self->last_flow_ret;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Acquirer implementation
 * ═══════════════════════════════════════════════════════════════════════════
 */

NvSahiPreProcessAcquirerImpl::NvSahiPreProcessAcquirerImpl(
    GstBufferPool *pool) {
  m_gstpool = pool;
}

NvDsPreProcessCustomBuf *NvSahiPreProcessAcquirerImpl::acquire() {
  GstBuffer *gstbuf;
  GstNvSahiPreProcessMemory *memory;
  GstFlowReturn flow_ret;

  flow_ret = gst_buffer_pool_acquire_buffer(m_gstpool, &gstbuf, nullptr);
  if (flow_ret != GST_FLOW_OK) {
    GST_ERROR("error acquiring buffer from tensor pool");
    return nullptr;
  }

  memory = gst_nvsahipreprocess_buffer_get_memory(gstbuf);
  if (!memory) {
    GST_ERROR("error getting memory from tensor pool");
    return nullptr;
  }

  return new NvSahiPreProcessCustomBufImpl{
      {memory->dev_memory_ptr}, gstbuf, memory};
}

gboolean NvSahiPreProcessAcquirerImpl::release(NvDsPreProcessCustomBuf *buf) {
  NvSahiPreProcessCustomBufImpl *impl = (NvSahiPreProcessCustomBufImpl *)buf;
  gst_buffer_unref(impl->gstbuf);
  delete impl;
  return TRUE;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * start / stop / set_caps / sink_event / finalize
 * ═══════════════════════════════════════════════════════════════════════════
 */

static gboolean gst_nvsahipreprocess_start(GstBaseTransform *btrans) {
  GstNvSahiPreProcess *self = GST_NVSAHIPREPROCESS(btrans);
  NvBufSurfaceColorFormat color_format;
  cudaError_t cudaReturn;
  GstStructure *scaling_pool_config, *tensor_pool_config;
  GstAllocator *scaling_pool_allocator, *tensor_pool_allocator;
  GstAllocationParams alloc_params, tensor_alloc_params;

  if (!self->config_file_path || strlen(self->config_file_path) == 0) {
    GST_ELEMENT_ERROR(self, LIBRARY, SETTINGS,
                      ("Configuration file not provided"), (nullptr));
    return FALSE;
  }

  if (!self->config_file_parse_successful) {
    GST_ELEMENT_ERROR(self, LIBRARY, SETTINGS,
                      ("Configuration file parsing failed"), (nullptr));
    return FALSE;
  }

  self->custom_initparams.tensor_params = self->tensor_params;
  self->custom_initparams.unique_id = self->unique_id;
  self->custom_initparams.config_file_path = self->config_file_path;

  if (self->custom_lib_path) {
    if (self->custom_lib_handle) {
      GST_DEBUG_OBJECT(self, "Custom library already loaded");
      return TRUE;
    }
    self->custom_lib_handle = dlopen(self->custom_lib_path, RTLD_NOW);
    if (self->custom_lib_handle) {
      auto initLib = dlsym_ptr<CustomCtx *(CustomInitParams)>(
          self->custom_lib_handle, "initLib");
      self->custom_lib_ctx = initLib(self->custom_initparams);
      if (!self->custom_lib_ctx) {
        GST_ELEMENT_ERROR(self, STREAM, FAILED, ("initLib failed"), (NULL));
        return FALSE;
      }
      if (!self->custom_tensor_function_name.empty()) {
        self->custom_tensor_function = dlsym_ptr<NvDsPreProcessStatus(
            CustomCtx *, NvDsPreProcessBatch *, NvDsPreProcessCustomBuf *&,
            CustomTensorParams &, NvDsPreProcessAcquirer *)>(
            self->custom_lib_handle, self->custom_tensor_function_name.c_str());
        if (!self->custom_tensor_function) {
          GST_ELEMENT_ERROR(self, STREAM, FAILED,
                            ("Custom tensor function not found"), (NULL));
          return FALSE;
        }
      }
    } else {
      GST_ELEMENT_ERROR(self, STREAM, FAILED,
                        ("Could not open custom library: %s", dlerror()),
                        (NULL));
      return FALSE;
    }
  }

  self->batch_insurf.surfaceList = new NvBufSurfaceParams[self->max_batch_size];
  self->batch_insurf.batchSize = self->max_batch_size;
  self->batch_insurf.gpuId = self->gpu_id;
  self->batch_outsurf.surfaceList =
      new NvBufSurfaceParams[self->max_batch_size];
  self->batch_outsurf.batchSize = self->max_batch_size;
  self->batch_outsurf.gpuId = self->gpu_id;

  cudaReturn = cudaSetDevice(self->gpu_id);
  if (cudaReturn != cudaSuccess) goto error;

  cudaReturn =
      cudaStreamCreateWithFlags(&self->convert_stream, cudaStreamNonBlocking);
  if (cudaReturn != cudaSuccess) goto error;

  self->transform_config_params.gpu_id = self->gpu_id;
  self->transform_config_params.cuda_stream = self->convert_stream;
  self->transform_config_params.compute_mode = self->scaling_pool_compute_hw;

  self->transform_params.src_rect =
      new NvBufSurfTransformRect[self->max_batch_size];
  self->transform_params.dst_rect =
      new NvBufSurfTransformRect[self->max_batch_size];
  self->transform_params.transform_flag = NVBUFSURF_TRANSFORM_FILTER |
                                          NVBUFSURF_TRANSFORM_CROP_SRC |
                                          NVBUFSURF_TRANSFORM_CROP_DST;
  self->transform_params.transform_flip = NvBufSurfTransform_None;
  self->transform_params.transform_filter =
      self->scaling_pool_interpolation_filter;

  /* Scaling pool */
  self->scaling_pool = gst_buffer_pool_new();
  scaling_pool_config = gst_buffer_pool_get_config(self->scaling_pool);
  gst_buffer_pool_config_set_params(
      scaling_pool_config, nullptr, sizeof(GstNvSahiPreProcessMemory),
      self->scaling_buf_pool_size, self->scaling_buf_pool_size);

  switch (self->tensor_params.network_color_format) {
    case NvDsPreProcessFormat_RGB:
    case NvDsPreProcessFormat_BGR:
      color_format = NVBUF_COLOR_FORMAT_RGBA;
      self->scaling_pool_format = NvDsPreProcessFormat_RGBA;
      break;
    case NvDsPreProcessFormat_GRAY:
      color_format = NVBUF_COLOR_FORMAT_GRAY8;
      self->scaling_pool_format = NvDsPreProcessFormat_GRAY;
      break;
    default:
      GST_ELEMENT_ERROR(self, LIBRARY, SETTINGS,
                        ("Unsupported network color format: %d",
                         self->tensor_params.network_color_format),
                        (nullptr));
      goto error;
  }

  {
    GstNvSahiPreProcessVideoBufferAllocatorInfo alloc_info;
    alloc_info.width = self->processing_width;
    alloc_info.height = self->processing_height;
    alloc_info.color_format = color_format;
    alloc_info.batch_size = self->max_batch_size;
    alloc_info.memory_type = self->scaling_pool_memory_type;

    scaling_pool_allocator =
        gst_nvsahipreprocess_allocator_new(&alloc_info, 1, self->gpu_id, FALSE);
  }

  memset(&alloc_params, 0, sizeof(alloc_params));
  gst_buffer_pool_config_set_allocator(scaling_pool_config,
                                       scaling_pool_allocator, &alloc_params);

  if (!gst_buffer_pool_set_config(self->scaling_pool, scaling_pool_config))
    goto error;
  if (!gst_buffer_pool_set_active(self->scaling_pool, TRUE)) goto error;

  /* Tensor pool */
  self->tensor_pool = gst_buffer_pool_new();
  tensor_pool_config = gst_buffer_pool_get_config(self->tensor_pool);
  gst_buffer_pool_config_set_params(
      tensor_pool_config, nullptr, sizeof(GstNvSahiPreProcessMemory),
      self->tensor_buf_pool_size, self->tensor_buf_pool_size);

  self->tensor_params.buffer_size = 1;
  for (auto &p : self->tensor_params.network_input_shape)
    self->tensor_params.buffer_size *= p;

  switch (self->tensor_params.data_type) {
    case NvDsDataType_FP32:
    case NvDsDataType_UINT32:
    case NvDsDataType_INT32:
      self->tensor_params.buffer_size *= 4;
      break;
    case NvDsDataType_UINT8:
    case NvDsDataType_INT8:
      self->tensor_params.buffer_size *= 1;
      break;
    case NvDsDataType_FP16:
      self->tensor_params.buffer_size *= 2;
      break;
    default:
      GST_ELEMENT_ERROR(self, LIBRARY, SETTINGS,
                        ("Unsupported tensor data type: %d",
                         (int)self->tensor_params.data_type),
                        (nullptr));
      goto error;
  }

  tensor_pool_allocator = gst_nvsahipreprocess_allocator_new(
      NULL, self->tensor_params.buffer_size, self->gpu_id, FALSE);

  memset(&tensor_alloc_params, 0, sizeof(tensor_alloc_params));
  gst_buffer_pool_config_set_allocator(
      tensor_pool_config, tensor_pool_allocator, &tensor_alloc_params);

  if (!gst_buffer_pool_set_config(self->tensor_pool, tensor_pool_config))
    goto error;
  if (!gst_buffer_pool_set_active(self->tensor_pool, TRUE)) goto error;

  self->acquire_impl =
      std::make_unique<NvSahiPreProcessAcquirerImpl>(self->tensor_pool);

  self->preprocess_queue = g_queue_new();
  self->output_thread = g_thread_new("nvsahipreprocess-thread",
                                     gst_nvsahipreprocess_output_loop, self);

  return TRUE;

error:
  delete[] self->transform_params.src_rect;
  delete[] self->transform_params.dst_rect;
  delete[] self->batch_insurf.surfaceList;
  delete[] self->batch_outsurf.surfaceList;
  if (self->convert_stream) {
    cudaStreamDestroy(self->convert_stream);
    self->convert_stream = NULL;
  }
  return FALSE;
}

static gboolean gst_nvsahipreprocess_stop(GstBaseTransform *btrans) {
  GstNvSahiPreProcess *self = GST_NVSAHIPREPROCESS(btrans);

  g_mutex_lock(&self->preprocess_lock);
  while (!g_queue_is_empty(self->preprocess_queue))
    g_cond_wait(&self->preprocess_cond, &self->preprocess_lock);
  self->stop = TRUE;
  g_cond_broadcast(&self->preprocess_cond);
  g_mutex_unlock(&self->preprocess_lock);

  g_thread_join(self->output_thread);

  cudaSetDevice(self->gpu_id);
  if (self->convert_stream) cudaStreamDestroy(self->convert_stream);
  self->convert_stream = NULL;

  delete[] self->transform_params.src_rect;
  delete[] self->transform_params.dst_rect;
  delete[] self->batch_insurf.surfaceList;
  delete[] self->batch_outsurf.surfaceList;

  g_queue_free(self->preprocess_queue);

  if (self->config_file_path) {
    g_free(self->config_file_path);
    self->config_file_path = NULL;
  }

  gst_object_unref(self->scaling_pool);
  gst_object_unref(self->tensor_pool);

  return TRUE;
}

static gboolean gst_nvsahipreprocess_set_caps(GstBaseTransform *btrans,
                                              GstCaps *incaps,
                                              GstCaps *outcaps) {
  GstNvSahiPreProcess *self = GST_NVSAHIPREPROCESS(btrans);
  cudaSetDevice(self->gpu_id);
  return TRUE;
}

static gboolean gst_nvsahipreprocess_sink_event(GstBaseTransform *trans,
                                                GstEvent *event) {
  GstNvSahiPreProcess *self = GST_NVSAHIPREPROCESS(trans);

  if (GST_EVENT_IS_SERIALIZED(event)) {
    NvDsPreProcessBatch *batch = new NvDsPreProcessBatch;
    batch->event_marker = TRUE;

    g_mutex_lock(&self->preprocess_lock);
    g_queue_push_tail(self->preprocess_queue, batch);
    g_cond_broadcast(&self->preprocess_cond);

    while (!g_queue_is_empty(self->preprocess_queue))
      g_cond_wait(&self->preprocess_cond, &self->preprocess_lock);
    g_mutex_unlock(&self->preprocess_lock);
  }

  return GST_BASE_TRANSFORM_CLASS(parent_class)->sink_event(trans, event);
}

static void gst_nvsahipreprocess_finalize(GObject *object) {
  GstNvSahiPreProcess *self = GST_NVSAHIPREPROCESS(object);

  if (self->custom_lib_path) {
    if (self->custom_lib_handle) {
      auto deInitLib =
          dlsym_ptr<void(void *)>(self->custom_lib_handle, "deInitLib");
      deInitLib(self->custom_lib_ctx);
      dlclose(self->custom_lib_handle);
      self->custom_lib_handle = NULL;
    }
    delete[] self->custom_lib_path;
    self->custom_lib_path = NULL;
  }
  self->acquire_impl.reset();

  G_OBJECT_CLASS(parent_class)->finalize(object);
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Class / instance init
 * ═══════════════════════════════════════════════════════════════════════════
 */

static void gst_nvsahipreprocess_class_init(GstNvSahiPreProcessClass *klass) {
  GObjectClass *gobject_class = (GObjectClass *)klass;
  GstElementClass *gstelement_class = (GstElementClass *)klass;
  GstBaseTransformClass *btrans_class = (GstBaseTransformClass *)klass;

  g_setenv("DS_NEW_BUFAPI", "1", TRUE);

  gobject_class->finalize = GST_DEBUG_FUNCPTR(gst_nvsahipreprocess_finalize);
  gobject_class->set_property =
      GST_DEBUG_FUNCPTR(gst_nvsahipreprocess_set_property);
  gobject_class->get_property =
      GST_DEBUG_FUNCPTR(gst_nvsahipreprocess_get_property);

  btrans_class->set_caps = GST_DEBUG_FUNCPTR(gst_nvsahipreprocess_set_caps);
  btrans_class->start = GST_DEBUG_FUNCPTR(gst_nvsahipreprocess_start);
  btrans_class->stop = GST_DEBUG_FUNCPTR(gst_nvsahipreprocess_stop);
  btrans_class->submit_input_buffer =
      GST_DEBUG_FUNCPTR(gst_nvsahipreprocess_submit_input_buffer);
  btrans_class->generate_output =
      GST_DEBUG_FUNCPTR(gst_nvsahipreprocess_generate_output);
  btrans_class->sink_event = GST_DEBUG_FUNCPTR(gst_nvsahipreprocess_sink_event);

  g_object_class_install_property(
      gobject_class, PROP_UNIQUE_ID,
      g_param_spec_uint(
          "unique-id", "Unique ID", "Unique ID for the element", 0, G_MAXUINT,
          DEFAULT_UNIQUE_ID,
          (GParamFlags)(G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS)));

  g_object_class_install_property(
      gobject_class, PROP_ENABLE,
      g_param_spec_boolean(
          "enable", "Enable", "Enable plugin, or set passthrough mode", TRUE,
          (GParamFlags)(G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS |
                        GST_PARAM_MUTABLE_READY)));

  g_object_class_install_property(
      gobject_class, PROP_GPU_DEVICE_ID,
      g_param_spec_uint(
          "gpu-id", "GPU Device ID", "GPU Device ID", 0, G_MAXUINT,
          DEFAULT_GPU_ID,
          (GParamFlags)(G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS |
                        GST_PARAM_MUTABLE_READY)));

  g_object_class_install_property(
      gobject_class, PROP_CONFIG_FILE,
      g_param_spec_string(
          "config-file", "Config File",
          "Path to config file for tensor preparation parameters",
          DEFAULT_CONFIG_FILE_PATH,
          (GParamFlags)(G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS)));

  g_object_class_install_property(
      gobject_class, PROP_SLICE_WIDTH,
      g_param_spec_uint(
          "slice-width", "Slice Width", "Width of each SAHI slice in pixels", 1,
          G_MAXUINT, DEFAULT_SLICE_WIDTH,
          (GParamFlags)(G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS)));

  g_object_class_install_property(
      gobject_class, PROP_SLICE_HEIGHT,
      g_param_spec_uint(
          "slice-height", "Slice Height", "Height of each SAHI slice in pixels",
          1, G_MAXUINT, DEFAULT_SLICE_HEIGHT,
          (GParamFlags)(G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS)));

  g_object_class_install_property(
      gobject_class, PROP_OVERLAP_WIDTH_RATIO,
      g_param_spec_float(
          "overlap-width-ratio", "Overlap Width Ratio",
          "Horizontal overlap between slices as a fraction of slice-width",
          0.0f, 0.99f, DEFAULT_OVERLAP_WIDTH_RATIO,
          (GParamFlags)(G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS)));

  g_object_class_install_property(
      gobject_class, PROP_OVERLAP_HEIGHT_RATIO,
      g_param_spec_float(
          "overlap-height-ratio", "Overlap Height Ratio",
          "Vertical overlap between slices as a fraction of slice-height", 0.0f,
          0.99f, DEFAULT_OVERLAP_HEIGHT_RATIO,
          (GParamFlags)(G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS)));

  g_object_class_install_property(
      gobject_class, PROP_ENABLE_FULL_FRAME,
      g_param_spec_boolean(
          "enable-full-frame", "Enable Full Frame",
          "Include the full frame as an extra slice so large objects that span "
          "multiple slices are still detected (SAHI standard behaviour). "
          "Default: TRUE — disable only for debugging.",
          DEFAULT_ENABLE_FULL_FRAME,
          (GParamFlags)(G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS)));

  g_object_class_install_property(
      gobject_class, PROP_TARGET_UNIQUE_IDS,
      g_param_spec_string(
          "target-unique-ids", "Target Unique Ids",
          "Semicolon-separated GIE unique-ids for which tensor is prepared. "
          "e.g. \"3;4;5\"",
          DEFAULT_TARGET_UNIQUE_IDS,
          (GParamFlags)(G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS |
                        GST_PARAM_MUTABLE_READY)));

  gst_element_class_add_pad_template(
      gstelement_class,
      gst_static_pad_template_get(&gst_nvsahipreprocess_src_template));
  gst_element_class_add_pad_template(
      gstelement_class,
      gst_static_pad_template_get(&gst_nvsahipreprocess_sink_template));

  gst_element_class_set_details_simple(gstelement_class,
                                       "SAHI Pre-Process (Dynamic Slicing)",
                                       "Filter/Preprocessing",
                                       "Dynamically computes SAHI slices per "
                                       "frame and prepares tensors for nvinfer",
                                       "Yuhao Ding <d346863528@163.com>");
}

static void gst_nvsahipreprocess_init(GstNvSahiPreProcess *self) {
  GstBaseTransform *btrans = GST_BASE_TRANSFORM(self);
  gst_base_transform_set_in_place(btrans, TRUE);
  gst_base_transform_set_passthrough(btrans, TRUE);

  self->unique_id = DEFAULT_UNIQUE_ID;
  self->enable = TRUE;
  self->gpu_id = DEFAULT_GPU_ID;
  self->max_batch_size = DEFAULT_BATCH_SIZE;
  self->processing_width = DEFAULT_PROCESSING_WIDTH;
  self->processing_height = DEFAULT_PROCESSING_HEIGHT;
  self->scaling_buf_pool_size = DEFAULT_SCALING_BUF_POOL_SIZE;
  self->tensor_buf_pool_size = DEFAULT_TENSOR_BUF_POOL_SIZE;
  self->scaling_pool_compute_hw = NvBufSurfTransformCompute_Default;
  self->config_file_path = g_strdup(DEFAULT_CONFIG_FILE_PATH);
  self->config_file_parse_successful = FALSE;

  self->slice_width = DEFAULT_SLICE_WIDTH;
  self->slice_height = DEFAULT_SLICE_HEIGHT;
  self->overlap_width_ratio = DEFAULT_OVERLAP_WIDTH_RATIO;
  self->overlap_height_ratio = DEFAULT_OVERLAP_HEIGHT_RATIO;
  self->enable_full_frame = DEFAULT_ENABLE_FULL_FRAME;

  self->transform_config_params.compute_mode =
      NvBufSurfTransformCompute_Default;
  self->transform_params.transform_filter = NvBufSurfTransformInter_Default;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Property accessors
 * ═══════════════════════════════════════════════════════════════════════════
 */

static void gst_nvsahipreprocess_set_property(GObject *object, guint prop_id,
                                              const GValue *value,
                                              GParamSpec *pspec) {
  GstNvSahiPreProcess *self = GST_NVSAHIPREPROCESS(object);
  switch (prop_id) {
    case PROP_UNIQUE_ID:
      self->unique_id = g_value_get_uint(value);
      break;
    case PROP_ENABLE:
      self->enable = g_value_get_boolean(value);
      break;
    case PROP_GPU_DEVICE_ID:
      self->gpu_id = g_value_get_uint(value);
      break;
    case PROP_CONFIG_FILE: {
      g_mutex_lock(&self->preprocess_lock);
      g_free(self->config_file_path);
      self->config_file_path = g_value_dup_string(value);
      self->config_file_parse_successful =
          nvsahipreprocess_parse_config_file(self, self->config_file_path);
      if (self->config_file_parse_successful)
        GST_DEBUG_OBJECT(self, "Successfully parsed config file");
      g_mutex_unlock(&self->preprocess_lock);
      break;
    }
    case PROP_SLICE_WIDTH:
      self->slice_width = g_value_get_uint(value);
      break;
    case PROP_SLICE_HEIGHT:
      self->slice_height = g_value_get_uint(value);
      break;
    case PROP_OVERLAP_WIDTH_RATIO:
      self->overlap_width_ratio = g_value_get_float(value);
      break;
    case PROP_OVERLAP_HEIGHT_RATIO:
      self->overlap_height_ratio = g_value_get_float(value);
      break;
    case PROP_ENABLE_FULL_FRAME:
      self->enable_full_frame = g_value_get_boolean(value);
      break;
    case PROP_TARGET_UNIQUE_IDS: {
      std::stringstream str(
          g_value_get_string(value) ? g_value_get_string(value) : "");
      self->target_unique_ids.clear();
      while (str.peek() != EOF) {
        gint gie_id;
        str >> gie_id;
        self->target_unique_ids.push_back(gie_id);
        str.get();
      }
      break;
    }
    default:
      G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec);
      break;
  }
}

static void gst_nvsahipreprocess_get_property(GObject *object, guint prop_id,
                                              GValue *value,
                                              GParamSpec *pspec) {
  GstNvSahiPreProcess *self = GST_NVSAHIPREPROCESS(object);
  switch (prop_id) {
    case PROP_UNIQUE_ID:
      g_value_set_uint(value, self->unique_id);
      break;
    case PROP_ENABLE:
      g_value_set_boolean(value, self->enable);
      break;
    case PROP_GPU_DEVICE_ID:
      g_value_set_uint(value, self->gpu_id);
      break;
    case PROP_CONFIG_FILE:
      g_value_set_string(value, self->config_file_path);
      break;
    case PROP_SLICE_WIDTH:
      g_value_set_uint(value, self->slice_width);
      break;
    case PROP_SLICE_HEIGHT:
      g_value_set_uint(value, self->slice_height);
      break;
    case PROP_OVERLAP_WIDTH_RATIO:
      g_value_set_float(value, self->overlap_width_ratio);
      break;
    case PROP_OVERLAP_HEIGHT_RATIO:
      g_value_set_float(value, self->overlap_height_ratio);
      break;
    case PROP_ENABLE_FULL_FRAME:
      g_value_set_boolean(value, self->enable_full_frame);
      break;
    case PROP_TARGET_UNIQUE_IDS: {
      std::stringstream str;
      for (const auto id : self->target_unique_ids) str << id << ";";
      g_value_set_string(value, str.str().c_str());
      break;
    }
    default:
      G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec);
      break;
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Plugin registration
 * ═══════════════════════════════════════════════════════════════════════════
 */

static gboolean nvsahipreprocess_plugin_init(GstPlugin *plugin) {
  GST_DEBUG_CATEGORY_INIT(gst_nvsahipreprocess_debug, "nvsahipreprocess", 0,
                          "SAHI dynamic slice preprocessor");

  return gst_element_register(plugin, "nvsahipreprocess", GST_RANK_PRIMARY,
                              GST_TYPE_NVSAHIPREPROCESS);
}

GST_PLUGIN_DEFINE(GST_VERSION_MAJOR, GST_VERSION_MINOR, nvdsgst_sahipreprocess,
                  DESCRIPTION, nvsahipreprocess_plugin_init, "1.0", LICENSE,
                  BINARY_PACKAGE, URL)
