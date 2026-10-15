#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
    cat <<'EOF'
Usage: verify_v4l2_stability.sh [options]

Run the real three-camera V4L2 profile headless and verify all detection topics
for a sustained period. The default duration is 1800 seconds (30 minutes).

Options:
  --duration SECONDS          test duration (default: 1800)
  --window SECONDS            rate window (default: 10)
  --min-rate HZ               minimum rate per topic (default: 8)
  --max-relative-jitter VALUE max (max-min)/median (default: 0.25)
  --max-topic-spread HZ       max cross-camera spread per window (default: 1)
  --warmup-windows COUNT      windows excluded from rate checks (default: 1)
  --params-file PATH          ROS parameter profile (default: config/v4l2_3cam.yaml)
  -h, --help                  show this help
EOF
}

duration=1800
window=10
min_rate=8
max_relative_jitter=0.25
max_topic_spread=1
warmup_windows=1
params_file=config/v4l2_3cam.yaml

while [ "$#" -gt 0 ]; do
    case "$1" in
        --duration) duration=$2; shift 2 ;;
        --window) window=$2; shift 2 ;;
        --min-rate) min_rate=$2; shift 2 ;;
        --max-relative-jitter) max_relative_jitter=$2; shift 2 ;;
        --max-topic-spread) max_topic_spread=$2; shift 2 ;;
        --warmup-windows) warmup_windows=$2; shift 2 ;;
        --params-file) params_file=$2; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'verify_v4l2_stability: unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "${SCRIPT_DIR}/.." && pwd)
cd "${REPO_ROOT}"

"${SCRIPT_DIR}/check_runtime.sh" --require-gpu
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/env.sh"

if [ ! -f "${REPO_ROOT}/.colcon/install/setup.bash" ]; then
    printf '%s\n' 'verify_v4l2_stability: ROS package is not built.' >&2
    exit 1
fi
if [ ! -f "${params_file}" ]; then
    printf 'verify_v4l2_stability: profile does not exist: %s\n' "${params_file}" >&2
    exit 1
fi

run_dir="${STABILITY_LOG_DIR:-${REPO_ROOT}/.state/stability}/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "${run_dir}"
launch_log="${run_dir}/launch.log"
rate_log="${run_dir}/topic-rates.log"

launch_pid=""
cleanup() {
    if [ -z "${launch_pid}" ] || ! kill -0 "${launch_pid}" 2>/dev/null; then
        return
    fi

    kill -INT -- "-${launch_pid}" 2>/dev/null || kill -INT "${launch_pid}" 2>/dev/null || true
    for _ in {1..50}; do
        kill -0 "${launch_pid}" 2>/dev/null || break
        sleep 0.1
    done

    if kill -0 "${launch_pid}" 2>/dev/null; then
        kill -TERM -- "-${launch_pid}" 2>/dev/null || kill -TERM "${launch_pid}" 2>/dev/null || true
        for _ in {1..20}; do
            kill -0 "${launch_pid}" 2>/dev/null || break
            sleep 0.1
        done
    fi

    if kill -0 "${launch_pid}" 2>/dev/null; then
        kill -KILL -- "-${launch_pid}" 2>/dev/null || kill -KILL "${launch_pid}" 2>/dev/null || true
    fi
    wait "${launch_pid}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

printf 'Stability logs: %s\n' "${run_dir}"
setsid env EGL_PLATFORM=surfaceless ros2 launch deepstream deepstream.launch.py \
    params_file:="${params_file}" \
    display:=false publish_detections:=true publish_annotated:=false \
    >"${launch_log}" 2>&1 &
launch_pid=$!

set +e
python3 -u "${SCRIPT_DIR}/monitor_detection_topics.py" \
    --duration "${duration}" \
    --window "${window}" \
    --launch-pid "${launch_pid}" \
    --min-rate "${min_rate}" \
    --max-relative-jitter "${max_relative_jitter}" \
    --max-topic-spread "${max_topic_spread}" \
    --warmup-windows "${warmup_windows}" \
    >"${rate_log}" 2>&1
monitor_rc=$?
launch_alive=false
if kill -0 "${launch_pid}" 2>/dev/null; then
    launch_alive=true
fi
set -e

if [ "${monitor_rc}" -ne 0 ] || [ "${launch_alive}" != true ]; then
    printf '%s\n' 'verify_v4l2_stability: FAILED' >&2
    printf 'monitor_rc=%s launch_alive=%s\n' "${monitor_rc}" "${launch_alive}" >&2
    tail -80 "${launch_log}" >&2 || true
    tail -80 "${rate_log}" >&2 || true
    exit 1
fi

cleanup
if grep -Eqi \
    'GStreamer error|process has died|NVDSINFER_TENSORRT_ERROR|Failed in mem copy|CUDA Runtime error|illegal memory access|cudaErrorIllegalAddress|Cuda Stream Synchronization failed|Signal lost|Device .*busy' \
    "${launch_log}"; then
    printf '%s\n' 'verify_v4l2_stability: FAILED because the launch log contains a runtime error.' >&2
    exit 1
fi

printf '%s\n' 'verify_v4l2_stability: PASSED'
printf 'topic rates: %s\n' "${rate_log}"
printf 'launch log: %s\n' "${launch_log}"
