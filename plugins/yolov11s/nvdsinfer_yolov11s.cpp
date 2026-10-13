/*
 * DeepStream parser for Ultralytics YOLOv11 detection ONNX exports.
 *
 * The supported graph exposes output0 as [4 + class_count, candidate_count]
 * per batch item. The first four channels are center-x, center-y, width, and
 * height in network pixels; remaining channels are per-class confidences.
 */

#include <algorithm>
#include <cstdlib>
#include <cmath>
#include <iostream>
#include <vector>

#include "nvdsinfer_custom_impl.h"

namespace {

float clamp(float value, float lower, float upper) {
  return std::max(lower, std::min(value, upper));
}

}  // namespace

extern "C" bool NvDsInferParseYolo11(
    std::vector<NvDsInferLayerInfo> const &outputLayersInfo,
    NvDsInferNetworkInfo const &networkInfo,
    NvDsInferParseDetectionParams const &detectionParams,
    std::vector<NvDsInferObjectDetectionInfo> &objectList) {
  if (outputLayersInfo.size() != 1 || !outputLayersInfo[0].buffer) {
    std::cerr << "NvDsInferParseYolo11 expects one non-empty output layer."
              << std::endl;
    return false;
  }

  const NvDsInferLayerInfo &layer = outputLayersInfo[0];
  const NvDsInferDims &dims = layer.inferDims;
  if (dims.numDims != 2) {
    std::cerr << "NvDsInferParseYolo11 expects [channels,candidates] output, got "
              << dims.numDims << " dimensions." << std::endl;
    return false;
  }

  const unsigned int configured_classes =
      static_cast<unsigned int>(detectionParams.perClassPreclusterThreshold.size());
  if (configured_classes == 0U || networkInfo.width == 0U ||
      networkInfo.height == 0U) {
    std::cerr << "NvDsInferParseYolo11 requires configured classes and non-zero "
                 "network dimensions."
              << std::endl;
    return false;
  }
  const unsigned int dim0 = static_cast<unsigned int>(dims.d[0]);
  const unsigned int dim1 = static_cast<unsigned int>(dims.d[1]);

  unsigned int channels = 0;
  unsigned int candidates = 0;
  bool channel_major = false;
  if (dim0 == configured_classes + 4U) {
    channels = dim0;
    candidates = dim1;
    channel_major = true;
  } else if (dim1 == configured_classes + 4U) {
    channels = dim1;
    candidates = dim0;
  } else {
    std::cerr << "NvDsInferParseYolo11 output dimensions do not match "
              << configured_classes << " configured classes: [" << dim0 << ","
              << dim1 << "]." << std::endl;
    return false;
  }

  const float *data = static_cast<const float *>(layer.buffer);
  const bool debug_enabled = std::getenv("DEEPSTREAM_SAHI_PARSER_DEBUG") != nullptr;
  float highest_score = 0.0F;
  unsigned int accepted = 0;
  const auto value_at = [data, candidates, channels, channel_major](
                            unsigned int channel, unsigned int candidate) {
    return channel_major ? data[channel * candidates + candidate]
                         : data[candidate * channels + channel];
  };

  for (unsigned int candidate = 0; candidate < candidates; ++candidate) {
    unsigned int class_id = 0;
    float confidence = 0.0F;
    for (unsigned int current_class = 0; current_class < configured_classes;
         ++current_class) {
      const float score = value_at(current_class + 4U, candidate);
      if (score > confidence) {
        confidence = score;
        class_id = current_class;
      }
    }
    highest_score = std::max(highest_score, confidence);
    if (confidence < detectionParams.perClassPreclusterThreshold[class_id]) {
      continue;
    }

    const float center_x = value_at(0, candidate);
    const float center_y = value_at(1, candidate);
    const float width = value_at(2, candidate);
    const float height = value_at(3, candidate);
    if (width <= 0.0F || height <= 0.0F) {
      continue;
    }

    NvDsInferObjectDetectionInfo detection;
    const float left = clamp(center_x - width / 2.0F, 0.0F,
                             static_cast<float>(networkInfo.width));
    const float top = clamp(center_y - height / 2.0F, 0.0F,
                            static_cast<float>(networkInfo.height));
    const float right = clamp(center_x + width / 2.0F, 0.0F,
                              static_cast<float>(networkInfo.width));
    const float bottom = clamp(center_y + height / 2.0F, 0.0F,
                               static_cast<float>(networkInfo.height));

    detection.classId = class_id;
    detection.detectionConfidence = confidence;
    detection.left = left;
    detection.top = top;
    detection.width = right - left;
    detection.height = bottom - top;
    if (detection.width >= 1.0F && detection.height >= 1.0F) {
      objectList.push_back(detection);
      ++accepted;
    }
  }
  if (debug_enabled) {
    std::cerr << "NvDsInferParseYolo11: dims=[" << dim0 << "," << dim1
              << "], candidates=" << candidates << ", max_score="
              << highest_score << ", accepted=" << accepted << std::endl;
  }
  return true;
}

CHECK_CUSTOM_PARSE_FUNC_PROTOTYPE(NvDsInferParseYolo11);
