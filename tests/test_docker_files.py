"""Static contracts for the environment-only Docker workflow."""

import pathlib
import subprocess


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_dockerfile_uses_requested_base_and_does_not_copy_repository_code():
    dockerfile = (REPO_ROOT / "docker" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "FROM nvcr.io/nvidia/deepstream:7.1-samples-multiarch" in dockerfile
    assert "COPY ." not in dockerfile
    assert "cuda-cudart-dev-12-6" in dockerfile
    assert "libnvinfer-dev" in dockerfile
    assert "ros-${ROS_DISTRO}-ros-base" in dockerfile
    assert (
        "ros2-apt-source_${ROS_APT_SOURCE_VERSION}~jammy_all.deb" in dockerfile
    )
    assert "ros.key" not in dockerfile
    assert (
        "ARG PYDS_WHEEL_FILE=pyds-1.2.0-cp310-cp310-linux_aarch64.whl"
        in dockerfile
    )
    assert "COPY ${PYDS_WHEEL_FILE} /tmp/pyds.whl" in dockerfile
    assert (
        "python3 -m pip install --no-cache-dir 'numpy<2' /tmp/pyds.whl"
        in dockerfile
    )

    assert (
        REPO_ROOT / "docker" / "pyds-1.2.0-cp310-cp310-linux_aarch64.whl"
    ).is_file()


def test_docker_launcher_exposes_headless_and_display_boundaries():
    launcher = (REPO_ROOT / "docker" / "run_docker.sh").read_text(
        encoding="utf-8"
    )

    assert "EGL_PLATFORM_VALUE=${EGL_PLATFORM:-surfaceless}" in launcher
    assert "--runtime nvidia" in launcher
    assert "--network=host" in launcher
    assert "-v /dev:/dev" in launcher
    assert "MOUNT_ARGUS_SOCKET=${MOUNT_ARGUS_SOCKET:-0}" in launcher
    assert '"${X11_SOCKET_DIR}/X${display_number}"' in launcher


def test_docker_launcher_help_is_available_without_docker():
    result = subprocess.run(
        [str(REPO_ROOT / "docker" / "run_docker.sh"), "--help"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "EGL_PLATFORM" in result.stdout
