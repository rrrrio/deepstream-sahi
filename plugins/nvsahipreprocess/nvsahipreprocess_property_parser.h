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
 * Simplified config file parser for nvsahipreprocess.
 * Only parses [property] section (tensor preparation, scaling pool, custom
 * lib). ROI/group definitions are replaced by dynamic slice computation.
 */

#ifndef NVSAHIPREPROCESS_PROPERTY_PARSER_H_
#define NVSAHIPREPROCESS_PROPERTY_PARSER_H_

#include <gst/gst.h>

#include "gstnvsahipreprocess.h"

#define _PATH_MAX 4096

#define NVSAHIPREPROCESS_PROPERTY "property"
#define NVSAHIPREPROCESS_PROPERTY_TARGET_IDS "target-unique-ids"
#define NVSAHIPREPROCESS_PROPERTY_ENABLE "enable"
#define NVSAHIPREPROCESS_PROPERTY_UNIQUE_ID "unique-id"
#define NVSAHIPREPROCESS_PROPERTY_GPU_ID "gpu-id"
#define NVSAHIPREPROCESS_PROPERTY_PROCESSING_WIDTH "processing-width"
#define NVSAHIPREPROCESS_PROPERTY_PROCESSING_HEIGHT "processing-height"
#define NVSAHIPREPROCESS_PROPERTY_MAINTAIN_ASPECT_RATIO "maintain-aspect-ratio"
#define NVSAHIPREPROCESS_PROPERTY_SYMMETRIC_PADDING "symmetric-padding"
#define NVSAHIPREPROCESS_PROPERTY_TENSOR_BUF_POOL_SIZE "tensor-buf-pool-size"
#define NVSAHIPREPROCESS_PROPERTY_SCALING_BUF_POOL_SIZE "scaling-buf-pool-size"
#define NVSAHIPREPROCESS_PROPERTY_SCALING_FILTER "scaling-filter"
#define NVSAHIPREPROCESS_PROPERTY_SCALING_POOL_COMPUTE_HW \
  "scaling-pool-compute-hw"
#define NVSAHIPREPROCESS_PROPERTY_SCALING_POOL_MEMORY_TYPE \
  "scaling-pool-memory-type"
#define NVSAHIPREPROCESS_PROPERTY_NETWORK_INPUT_ORDER "network-input-order"
#define NVSAHIPREPROCESS_PROPERTY_NETWORK_SHAPE "network-input-shape"
#define NVSAHIPREPROCESS_PROPERTY_NETWORK_COLOR_FORMAT "network-color-format"
#define NVSAHIPREPROCESS_PROPERTY_TENSOR_DATA_TYPE "tensor-data-type"
#define NVSAHIPREPROCESS_PROPERTY_TENSOR_NAME "tensor-name"
#define NVSAHIPREPROCESS_PROPERTY_CUSTOM_LIB_NAME "custom-lib-path"
#define NVSAHIPREPROCESS_PROPERTY_TENSOR_PREPARATION_FUNCTION \
  "custom-tensor-preparation-function"
#define NVSAHIPREPROCESS_USER_CONFIGS "user-configs"

gboolean nvsahipreprocess_parse_config_file(
    GstNvSahiPreProcess *nvsahipreprocess, gchar *cfg_file_path);

#endif /* NVSAHIPREPROCESS_PROPERTY_PARSER_H_ */
