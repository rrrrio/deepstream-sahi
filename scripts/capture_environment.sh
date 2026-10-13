#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "${SCRIPT_DIR}/.." && pwd)
STATE_DIR="${REPO_ROOT}/.state"
mkdir -p "${STATE_DIR}"

output_file="${STATE_DIR}/environment-$(date -u +%Y%m%dT%H%M%SZ).txt"
{
    printf 'captured_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'repo_root=%s\n' "${REPO_ROOT}"
    printf 'kernel=%s\n' "$(uname -a)"
    printf 'architecture=%s\n' "$(uname -m)"
    printf 'user=%s\n' "$(id -u):$(id -g)"
    for command_name in git python3 nvcc gst-launch-1.0 cmake colcon; do
        if command -v "${command_name}" >/dev/null 2>&1; then
            printf '%s_path=%s\n' "${command_name}" "$(command -v "${command_name}")"
            version_line=$("${command_name}" --version 2>&1 | head -n 1 || true)
            printf '%s_version=%s\n' "${command_name}" "${version_line:-unknown}"
        else
            printf '%s_path=missing\n' "${command_name}"
        fi
    done
    if [ -f /etc/os-release ]; then
        sed 's/^/os_/' /etc/os-release
    fi
    if command -v git >/dev/null 2>&1 && git -C "${REPO_ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        printf 'git_commit=%s\n' "$(git -C "${REPO_ROOT}" rev-parse HEAD 2>/dev/null || true)"
        printf 'git_status_begin\n'
        git -C "${REPO_ROOT}" status --short
        printf 'git_status_end\n'
    fi
    printf 'cuda_home=%s\n' "${CUDA_HOME:-${CUDA_PATH:-/usr/local/cuda}}"
    printf 'deepstream_dir=%s\n' "${DEEPSTREAM_DIR:-/opt/nvidia/deepstream/deepstream}"
} >"${output_file}"

ln -sfn "$(basename -- "${output_file}")" "${STATE_DIR}/environment-latest.txt"
printf 'Environment snapshot: %s\n' "${output_file}"
