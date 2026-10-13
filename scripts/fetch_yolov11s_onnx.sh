#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "${SCRIPT_DIR}/.." && pwd)
MODEL_PATH="${REPO_ROOT}/models/yolo11s.onnx"
MODEL_URL=${YOLO11S_ONNX_URL:-https://huggingface.co/unileon-robotics/YOLO11-ONNX/resolve/main/dynamic/yolo11s.onnx}
YOLO11S_PROXY=${YOLO11S_PROXY:-http://192.168.10.20:6152}
MODEL_SHA256=d98bbe0162cd882d6ad1305ea5ced6413a2819adfcf019de1740aa94c04254d0

fail() { printf 'fetch_yolov11s_onnx: %s\n' "$1" >&2; exit 1; }
command -v curl >/dev/null 2>&1 || fail "curl is required"
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum is required"
mkdir -p "${REPO_ROOT}/models"

if [ -f "${MODEL_PATH}" ] && printf '%s  %s\n' "${MODEL_SHA256}" "${MODEL_PATH}" | sha256sum -c - >/dev/null; then
    printf 'YOLOv11s ONNX already verified: %s\n' "${MODEL_PATH}"
    exit 0
fi

TEMP_PATH="${MODEL_PATH}.download"
rm -f "${TEMP_PATH}"
curl -fL --retry 3 --connect-timeout 20 --max-time 300 \
    --proxy "${YOLO11S_PROXY}" -o "${TEMP_PATH}" "${MODEL_URL}"
printf '%s  %s\n' "${MODEL_SHA256}" "${TEMP_PATH}" | sha256sum -c - >/dev/null \
    || fail "download checksum did not match the pinned YOLOv11s ONNX"
mv -f "${TEMP_PATH}" "${MODEL_PATH}"
printf 'Downloaded and verified YOLOv11s ONNX: %s\n' "${MODEL_PATH}"
