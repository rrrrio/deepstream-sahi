#include <cmath>
#include <cstdlib>
#include <iostream>
#include <vector>

#include "nvdsinfer_custom_impl.h"

extern "C" bool NvDsInferParseYolo11(
    std::vector<NvDsInferLayerInfo> const &outputLayersInfo,
    NvDsInferNetworkInfo const &networkInfo,
    NvDsInferParseDetectionParams const &detectionParams,
    std::vector<NvDsInferObjectDetectionInfo> &objectList);

namespace {

void require(bool value, const char *message) {
  if (!value) {
    std::cerr << "FAILED: " << message << std::endl;
    std::exit(EXIT_FAILURE);
  }
}

bool same(float actual, float expected) {
  return std::fabs(actual - expected) < 0.0001F;
}

NvDsInferLayerInfo layer_for(std::vector<float> &values, unsigned int dim0,
                              unsigned int dim1) {
  NvDsInferLayerInfo layer{};
  layer.buffer = values.data();
  layer.inferDims.numDims = 2;
  layer.inferDims.d[0] = dim0;
  layer.inferDims.d[1] = dim1;
  layer.inferDims.numElements = dim0 * dim1;
  return layer;
}

NvDsInferParseDetectionParams params() {
  NvDsInferParseDetectionParams detection_params{};
  detection_params.numClassesConfigured = 2;
  detection_params.perClassPreclusterThreshold = {0.5F, 0.7F};
  return detection_params;
}

void check_channel_major_and_clipping() {
  // [channels, candidates], where each channel is contiguous.
  std::vector<float> values = {
      320.0F, 10.0F,  // cx
      240.0F, 20.0F,  // cy
      100.0F, 30.0F,  // width
      80.0F, 40.0F,   // height
      0.90F, 0.60F,   // class 0
      0.20F, 0.75F,   // class 1
  };
  NvDsInferNetworkInfo network{640U, 480U, 3U};
  std::vector<NvDsInferObjectDetectionInfo> detections;
  const bool ok = NvDsInferParseYolo11({layer_for(values, 6U, 2U)}, network,
                                       params(), detections);

  require(ok, "channel-major parser result");
  require(detections.size() == 2U, "channel-major detection count");
  require(detections[0].classId == 0U, "first class ID");
  require(same(detections[0].detectionConfidence, 0.90F), "first confidence");
  require(same(detections[0].left, 270.0F), "first left");
  require(same(detections[0].top, 200.0F), "first top");
  require(same(detections[0].width, 100.0F), "first width");
  require(same(detections[0].height, 80.0F), "first height");
  require(detections[1].classId == 1U, "second class ID");
  require(same(detections[1].left, 0.0F), "clipped left");
  require(same(detections[1].top, 0.0F), "clipped top");
  require(same(detections[1].width, 25.0F), "clipped width");
  require(same(detections[1].height, 40.0F), "clipped height");
}

void check_transposed_and_threshold() {
  // [candidates, channels], each candidate is contiguous.
  std::vector<float> values = {
      600.0F, 450.0F, 100.0F, 100.0F, 0.50F, 0.70F,
      100.0F, 100.0F, 20.0F, 20.0F, 0.49F, 0.69F,
  };
  NvDsInferNetworkInfo network{640U, 480U, 3U};
  std::vector<NvDsInferObjectDetectionInfo> detections;
  const bool ok = NvDsInferParseYolo11({layer_for(values, 2U, 6U)}, network,
                                       params(), detections);

  require(ok, "transposed parser result");
  require(detections.size() == 1U, "threshold filters second candidate");
  require(detections[0].classId == 1U, "transposed class ID");
  require(same(detections[0].left, 550.0F), "transposed left");
  require(same(detections[0].top, 400.0F), "transposed top");
  require(same(detections[0].width, 90.0F), "right boundary clipping");
  require(same(detections[0].height, 80.0F), "bottom boundary clipping");
}

void check_invalid_layer_rejected() {
  NvDsInferNetworkInfo network{640U, 480U, 3U};
  std::vector<NvDsInferObjectDetectionInfo> detections;
  require(!NvDsInferParseYolo11({}, network, params(), detections),
          "empty output layers must fail");
}

}  // namespace

int main() {
  check_channel_major_and_clipping();
  check_transposed_and_threshold();
  check_invalid_layer_rejected();
  std::cout << "NvDsInferParseYolo11 harness passed" << std::endl;
  return EXIT_SUCCESS;
}
