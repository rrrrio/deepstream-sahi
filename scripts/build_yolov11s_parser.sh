#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "${SCRIPT_DIR}/.." && pwd)
DEEPSTREAM_DIR=${DEEPSTREAM_DIR:-/opt/nvidia/deepstream/deepstream}
CUDA_DIR=${CUDA_DIR:-${CUDA_HOME:-/usr/local/cuda}}
TENSORRT_INCLUDE_DIR=${TENSORRT_INCLUDE_DIR:-/usr/include/aarch64-linux-gnu}
PARSER_DIR="${REPO_ROOT}/plugins/yolov11s"
INSTALL_DIR="${REPO_ROOT}/.local/lib"

fail() { printf 'build_yolov11s_parser: %s\n' "$1" >&2; exit 1; }
[ -f "${DEEPSTREAM_DIR}/sources/includes/nvdsinfer_custom_impl.h" ] || fail "DeepStream parser headers are missing"
[ -f "${CUDA_DIR}/include/cuda.h" ] || fail "CUDA driver headers are missing"
[ -f "${TENSORRT_INCLUDE_DIR}/NvInfer.h" ] || fail "TensorRT development headers are missing: install libnvinfer-dev"
[ -f "${TENSORRT_INCLUDE_DIR}/NvOnnxParser.h" ] || fail "TensorRT ONNX parser headers are missing: install libnvonnxparsers-dev"
command -v g++ >/dev/null 2>&1 || fail "g++ is required"

make -C "${PARSER_DIR}" \
  DEEPSTREAM_DIR="${DEEPSTREAM_DIR}" \
  CUDA_DIR="${CUDA_DIR}" \
  TENSORRT_INCLUDE_DIR="${TENSORRT_INCLUDE_DIR}" \
  test
make -C "${PARSER_DIR}" \
  DEEPSTREAM_DIR="${DEEPSTREAM_DIR}" \
  CUDA_DIR="${CUDA_DIR}" \
  TENSORRT_INCLUDE_DIR="${TENSORRT_INCLUDE_DIR}" \
  INSTALL_DIR="${INSTALL_DIR}" \
  install
nm -D "${INSTALL_DIR}/libnvdsinfer_yolov11s.so" | grep -q NvDsInferParseYolo11 \
  || fail "NvDsInferParseYolo11 was not exported"
printf 'Built YOLOv11s parser: %s/libnvdsinfer_yolov11s.so\n' "${INSTALL_DIR}"
