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
 * Parses only [property] for tensor preparation and scaling pool configuration.
 * [group-N] / ROI sections are not parsed — slices are computed dynamically.
 */

#include "nvsahipreprocess_property_parser.h"

#include <cstring>
#include <iostream>
#include <string>

GST_DEBUG_CATEGORY(NVSAHIPREPROCESS_CFG_PARSER_CAT);

#define PARSE_ERROR(details_fmt, ...)                                    \
  G_STMT_START {                                                         \
    GST_CAT_ERROR(NVSAHIPREPROCESS_CFG_PARSER_CAT,                       \
                  "Failed to parse config file %s: " details_fmt,        \
                  cfg_file_path, ##__VA_ARGS__);                         \
    GST_ELEMENT_ERROR(nvsahi, LIBRARY, SETTINGS,                         \
                      ("Failed to parse config file:%s", cfg_file_path), \
                      (details_fmt, ##__VA_ARGS__));                     \
    goto done;                                                           \
  }                                                                      \
  G_STMT_END

#define CHECK_ERROR(error, group)                                       \
  G_STMT_START {                                                        \
    if (error) {                                                        \
      std::string errvalue = "Error while setting property, in group "; \
      errvalue.append(group);                                           \
      PARSE_ERROR("%s %s", errvalue.c_str(), error->message);           \
    }                                                                   \
  }                                                                     \
  G_STMT_END

static gboolean get_absolute_file_path(const gchar *cfg_file_path,
                                       const gchar *file_path,
                                       char *abs_path_str) {
  gchar abs_cfg_path[_PATH_MAX + 1];
  gchar abs_real_file_path[_PATH_MAX + 1];
  gchar *abs_file_path;
  gchar *delim;

  if (file_path[0] == '/') {
    if (!realpath(file_path, abs_real_file_path)) return FALSE;
    g_strlcpy(abs_path_str, abs_real_file_path, _PATH_MAX);
    return TRUE;
  }

  if (!realpath(cfg_file_path, abs_cfg_path)) return FALSE;

  delim = g_strrstr(abs_cfg_path, "/");
  *(delim + 1) = '\0';

  abs_file_path = g_strconcat(abs_cfg_path, file_path, nullptr);

  if (realpath(abs_file_path, abs_real_file_path) == nullptr) {
    if (errno == ENOENT)
      g_strlcpy(abs_real_file_path, abs_file_path, _PATH_MAX);
    else {
      g_free(abs_file_path);
      return FALSE;
    }
  }
  g_free(abs_file_path);
  g_strlcpy(abs_path_str, abs_real_file_path, _PATH_MAX);
  return TRUE;
}

static gboolean parse_property_group(GstNvSahiPreProcess *nvsahi,
                                     gchar *cfg_file_path, GKeyFile *key_file,
                                     gchar *group) {
  g_autoptr(GError) error = nullptr;
  gboolean ret = FALSE;
  g_auto(GStrv) keys = nullptr;
  GStrv key = nullptr;
  gint *network_shape_list = nullptr;
  gsize network_shape_list_len = 0;
  gint *target_unique_ids_list = nullptr;
  gsize target_unique_ids_list_len = 0;

  gboolean has_processing_width = FALSE, has_processing_height = FALSE;
  gboolean has_network_input_order = FALSE, has_network_input_shape = FALSE;
  gboolean has_network_color_format = FALSE, has_tensor_data_type = FALSE;
  gboolean has_tensor_name = FALSE, has_custom_lib_path = FALSE;
  gboolean has_custom_tensor_function = FALSE;
  gboolean has_scaling_filter = FALSE, has_scaling_pool_memory_type = FALSE;

  keys = g_key_file_get_keys(key_file, group, nullptr, &error);
  CHECK_ERROR(error, group);

  for (key = keys; *key; key++) {
    if (!g_strcmp0(*key, NVSAHIPREPROCESS_PROPERTY_ENABLE)) {
      nvsahi->enable = g_key_file_get_boolean(key_file, group, *key, &error);
      CHECK_ERROR(error, group);
    } else if (!g_strcmp0(*key, NVSAHIPREPROCESS_PROPERTY_UNIQUE_ID)) {
      nvsahi->unique_id = g_key_file_get_integer(key_file, group, *key, &error);
      CHECK_ERROR(error, group);
    } else if (!g_strcmp0(*key, NVSAHIPREPROCESS_PROPERTY_GPU_ID)) {
      nvsahi->gpu_id = g_key_file_get_integer(key_file, group, *key, &error);
      CHECK_ERROR(error, group);
    } else if (!g_strcmp0(*key,
                          NVSAHIPREPROCESS_PROPERTY_MAINTAIN_ASPECT_RATIO)) {
      nvsahi->maintain_aspect_ratio =
          g_key_file_get_boolean(key_file, group, *key, &error);
      CHECK_ERROR(error, group);
    } else if (!g_strcmp0(*key, NVSAHIPREPROCESS_PROPERTY_SYMMETRIC_PADDING)) {
      nvsahi->symmetric_padding =
          g_key_file_get_boolean(key_file, group, *key, &error);
      CHECK_ERROR(error, group);
    } else if (!g_strcmp0(*key, NVSAHIPREPROCESS_PROPERTY_PROCESSING_WIDTH)) {
      nvsahi->processing_width =
          g_key_file_get_integer(key_file, group, *key, &error);
      CHECK_ERROR(error, group);
      has_processing_width = TRUE;
    } else if (!g_strcmp0(*key, NVSAHIPREPROCESS_PROPERTY_PROCESSING_HEIGHT)) {
      nvsahi->processing_height =
          g_key_file_get_integer(key_file, group, *key, &error);
      CHECK_ERROR(error, group);
      has_processing_height = TRUE;
    } else if (!g_strcmp0(*key,
                          NVSAHIPREPROCESS_PROPERTY_SCALING_BUF_POOL_SIZE)) {
      nvsahi->scaling_buf_pool_size =
          g_key_file_get_integer(key_file, group, *key, &error);
      CHECK_ERROR(error, group);
    } else if (!g_strcmp0(*key,
                          NVSAHIPREPROCESS_PROPERTY_TENSOR_BUF_POOL_SIZE)) {
      nvsahi->tensor_buf_pool_size =
          g_key_file_get_integer(key_file, group, *key, &error);
      CHECK_ERROR(error, group);
    } else if (!g_strcmp0(*key, NVSAHIPREPROCESS_PROPERTY_TARGET_IDS)) {
      target_unique_ids_list = g_key_file_get_integer_list(
          key_file, group, *key, &target_unique_ids_list_len, &error);
      if (target_unique_ids_list == nullptr) CHECK_ERROR(error, group);
      nvsahi->target_unique_ids.clear();
      for (gsize i = 0; i < target_unique_ids_list_len; i++)
        nvsahi->target_unique_ids.push_back(target_unique_ids_list[i]);
      g_free(target_unique_ids_list);
      target_unique_ids_list = nullptr;
    } else if (!g_strcmp0(*key,
                          NVSAHIPREPROCESS_PROPERTY_NETWORK_INPUT_ORDER)) {
      guint val = g_key_file_get_integer(key_file, group, *key, &error);
      CHECK_ERROR(error, group);
      nvsahi->tensor_params.network_input_order =
          (NvDsPreProcessNetworkInputOrder)val;
      has_network_input_order = TRUE;
    } else if (!g_strcmp0(*key, NVSAHIPREPROCESS_PROPERTY_NETWORK_SHAPE)) {
      network_shape_list = g_key_file_get_integer_list(
          key_file, group, *key, &network_shape_list_len, &error);
      if (network_shape_list == nullptr) CHECK_ERROR(error, group);
      nvsahi->tensor_params.network_input_shape.clear();
      for (gsize i = 0; i < network_shape_list_len; i++)
        nvsahi->tensor_params.network_input_shape.push_back(
            network_shape_list[i]);
      g_free(network_shape_list);
      network_shape_list = nullptr;
      has_network_input_shape = TRUE;
    } else if (!g_strcmp0(*key,
                          NVSAHIPREPROCESS_PROPERTY_NETWORK_COLOR_FORMAT)) {
      guint val = g_key_file_get_integer(key_file, group, *key, &error);
      CHECK_ERROR(error, group);
      nvsahi->tensor_params.network_color_format = (NvDsPreProcessFormat)val;
      has_network_color_format = TRUE;
    } else if (!g_strcmp0(*key, NVSAHIPREPROCESS_PROPERTY_SCALING_FILTER)) {
      int val = g_key_file_get_integer(key_file, group, *key, &error);
      CHECK_ERROR(error, group);
      nvsahi->scaling_pool_interpolation_filter = (NvBufSurfTransform_Inter)val;
      has_scaling_filter = TRUE;
    } else if (!g_strcmp0(*key, NVSAHIPREPROCESS_PROPERTY_TENSOR_DATA_TYPE)) {
      int val = g_key_file_get_integer(key_file, group, *key, &error);
      CHECK_ERROR(error, group);
      nvsahi->tensor_params.data_type = (NvDsDataType)val;
      has_tensor_data_type = TRUE;
    } else if (!g_strcmp0(*key,
                          NVSAHIPREPROCESS_PROPERTY_SCALING_POOL_MEMORY_TYPE)) {
      int val = g_key_file_get_integer(key_file, group, *key, &error);
      CHECK_ERROR(error, group);
      nvsahi->scaling_pool_memory_type = (NvBufSurfaceMemType)val;
      has_scaling_pool_memory_type = TRUE;
    } else if (!g_strcmp0(*key,
                          NVSAHIPREPROCESS_PROPERTY_SCALING_POOL_COMPUTE_HW)) {
      int val = g_key_file_get_integer(key_file, group, *key, &error);
      CHECK_ERROR(error, group);
      nvsahi->scaling_pool_compute_hw = (NvBufSurfTransform_Compute)val;
    } else if (!g_strcmp0(*key, NVSAHIPREPROCESS_PROPERTY_TENSOR_NAME)) {
      gchar *temp = g_key_file_get_string(key_file, group, *key, &error);
      nvsahi->tensor_params.tensor_name = temp;
      g_free(temp);
      CHECK_ERROR(error, group);
      has_tensor_name = TRUE;
    } else if (!g_strcmp0(*key, NVSAHIPREPROCESS_PROPERTY_CUSTOM_LIB_NAME)) {
      gchar *temp = g_key_file_get_string(key_file, group, *key, &error);
      std::string str = temp;
      g_free(temp);
      nvsahi->custom_lib_path = new gchar[_PATH_MAX];
      if (!get_absolute_file_path(cfg_file_path, str.c_str(),
                                  nvsahi->custom_lib_path)) {
        g_printerr("Error: Could not parse custom lib path\n");
        delete[] nvsahi->custom_lib_path;
        nvsahi->custom_lib_path = NULL;
        goto done;
      }
      has_custom_lib_path = TRUE;
    } else if (!g_strcmp0(
                   *key,
                   NVSAHIPREPROCESS_PROPERTY_TENSOR_PREPARATION_FUNCTION)) {
      gchar *temp = g_key_file_get_string(key_file, group, *key, &error);
      nvsahi->custom_tensor_function_name = temp;
      g_free(temp);
      CHECK_ERROR(error, group);
      has_custom_tensor_function = TRUE;
    }
  }

  if (!(has_processing_width && has_processing_height &&
        has_network_input_order && has_network_input_shape &&
        has_network_color_format && has_tensor_data_type && has_tensor_name &&
        has_custom_lib_path && has_custom_tensor_function &&
        has_scaling_filter && has_scaling_pool_memory_type)) {
    g_printerr(
        "ERROR: Some required config properties not set in [property]\n");
    goto done;
  }

  ret = TRUE;

done:
  return ret;
}

static gboolean parse_user_configs(GstNvSahiPreProcess *nvsahi,
                                   gchar *cfg_file_path, GKeyFile *key_file,
                                   gchar *group) {
  g_autoptr(GError) error = nullptr;
  gboolean ret = FALSE;
  g_auto(GStrv) keys = nullptr;
  GStrv key = nullptr;
  std::unordered_map<std::string, std::string> user_configs;

  keys = g_key_file_get_keys(key_file, group, nullptr, &error);
  CHECK_ERROR(error, group);

  for (key = keys; *key; key++) {
    gchar *temp = g_key_file_get_string(key_file, group, *key, &error);
    std::string val = temp;
    g_free(temp);
    CHECK_ERROR(error, group);
    user_configs.emplace(std::string(*key), val);
  }
  nvsahi->custom_initparams.user_configs = user_configs;
  ret = TRUE;

done:
  return ret;
}

gboolean nvsahipreprocess_parse_config_file(GstNvSahiPreProcess *nvsahi,
                                            gchar *cfg_file_path) {
  g_autoptr(GError) error = nullptr;
  gboolean ret = FALSE;
  g_auto(GStrv) groups = nullptr;
  GStrv group;
  g_autoptr(GKeyFile) cfg_file = g_key_file_new();

  if (!NVSAHIPREPROCESS_CFG_PARSER_CAT) {
    GstDebugLevel level;
    GST_DEBUG_CATEGORY_INIT(NVSAHIPREPROCESS_CFG_PARSER_CAT, "nvsahipreprocess",
                            0, NULL);
    level = gst_debug_category_get_threshold(NVSAHIPREPROCESS_CFG_PARSER_CAT);
    if (level < GST_LEVEL_ERROR)
      gst_debug_category_set_threshold(NVSAHIPREPROCESS_CFG_PARSER_CAT,
                                       GST_LEVEL_ERROR);
  }

  if (!g_key_file_load_from_file(cfg_file, cfg_file_path, G_KEY_FILE_NONE,
                                 &error)) {
    PARSE_ERROR("%s", error->message);
  }

  if (!g_key_file_has_group(cfg_file, NVSAHIPREPROCESS_PROPERTY)) {
    PARSE_ERROR("Group 'property' not specified");
  }

  g_key_file_set_list_separator(cfg_file, ';');

  groups = g_key_file_get_groups(cfg_file, nullptr);

  for (group = groups; *group; group++) {
    if (!strcmp(*group, NVSAHIPREPROCESS_PROPERTY)) {
      ret = parse_property_group(nvsahi, cfg_file_path, cfg_file, *group);
      if (!ret) {
        g_printerr("NVSAHIPREPROCESS: Group '%s' parse failed\n", *group);
        goto done;
      }
    } else if (!strcmp(*group, NVSAHIPREPROCESS_USER_CONFIGS)) {
      ret = parse_user_configs(nvsahi, cfg_file_path, cfg_file, *group);
      if (!ret) {
        g_printerr("NVSAHIPREPROCESS: Group '%s' parse failed\n", *group);
        goto done;
      }
    } else {
      GST_DEBUG("NVSAHIPREPROCESS: Group '%s' ignored\n", *group);
    }
  }

  nvsahi->max_batch_size = nvsahi->tensor_params.network_input_shape[0];

done:
  return ret;
}
