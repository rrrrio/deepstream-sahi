FROM nvcr.io/nvidia/deepstream:7.1-samples-multiarch

ARG DEBIAN_FRONTEND=noninteractive
ARG L4T_RELEASE=r36.4
ARG ROS_DISTRO=humble
ARG ROS_APT_SOURCE_VERSION=1.3.0
ARG PYDS_WHEEL_FILE=pyds-1.2.0-cp310-cp310-linux_aarch64.whl

ENV DEEPSTREAM_DIR=/opt/nvidia/deepstream/deepstream \
    ROS_DISTRO=${ROS_DISTRO} \
    NVIDIA_DRIVER_CAPABILITIES=all

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

COPY ${PYDS_WHEEL_FILE} /tmp/pyds.whl

# The base image carries the DeepStream runtime but not the project build
# headers, ROS 2, or the Jetson Python binding used by deepstream.node.
RUN set -eux; \
    sed -i '/l4t-repo\.nvidia\.com\/common/d' /etc/apt/sources.list; \
    apt-get -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 -o Acquire::Retries=3 update; \
    apt-get -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 -o Acquire::Retries=3 install -y --no-install-recommends ca-certificates curl gnupg; \
    install -d -m 0755 /etc/apt/keyrings; \
    curl --connect-timeout 15 --max-time 120 --retry 3 --retry-delay 2 -fsSL https://repo.download.nvidia.com/jetson/jetson-ota-public.asc \
      | gpg --dearmor -o /etc/apt/keyrings/nvidia-jetson.gpg; \
    printf '%s\n' \
      "deb [arch=arm64 signed-by=/etc/apt/keyrings/nvidia-jetson.gpg] https://repo.download.nvidia.com/jetson/common ${L4T_RELEASE} main" \
      > /etc/apt/sources.list.d/nvidia-jetson.list; \
    curl --connect-timeout 15 --max-time 120 --retry 3 --retry-delay 2 -fsSL \
      "http://repo.ros2.org/ubuntu/main/pool/main/r/ros-apt-source/ros2-apt-source_${ROS_APT_SOURCE_VERSION}~jammy_all.deb" \
      -o /tmp/ros2-apt-source.deb; \
    dpkg -i /tmp/ros2-apt-source.deb; \
    rm -f /tmp/ros2-apt-source.deb; \
    apt-get -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 -o Acquire::Retries=3 update; \
    apt-get -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 -o Acquire::Retries=3 install -y --no-install-recommends \
      build-essential \
      cmake \
      pkg-config \
      git-lfs \
      v4l-utils \
      cuda-cudart-dev-12-6 \
      cuda-crt-12-6 \
      libglib2.0-dev \
      libgstreamer1.0-dev \
      libgstreamer-plugins-base1.0-dev \
      gstreamer1.0-tools \
      libnvinfer-dev \
      libnvonnxparsers-dev \
      python3-dev \
      python3-gst-1.0 \
      python3-numpy \
      python3-onnx \
      python3-pip \
      python3-pytest \
      python3-venv \
      python3-colcon-common-extensions \
      ros-${ROS_DISTRO}-ros-base \
      ros-${ROS_DISTRO}-ros2cli \
      ros-${ROS_DISTRO}-ros2launch \
      ros-${ROS_DISTRO}-launch-ros \
      ros-${ROS_DISTRO}-vision-msgs; \
    python3 -m pip install --no-cache-dir 'numpy<2' /tmp/pyds.whl; \
    rm -rf /var/lib/apt/lists/* /root/.cache/pip /tmp/pyds.whl

COPY deepstream-env.sh /usr/local/bin/deepstream-sahi-env
RUN chmod 0755 /usr/local/bin/deepstream-sahi-env

WORKDIR /workspace/deepstream-sahi
CMD ["bash"]
