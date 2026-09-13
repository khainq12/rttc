ARG ROS_IMAGE=ros:jazzy-ros-base
FROM ${ROS_IMAGE}

ARG AIOQUIC_DEB_VERSION=0.9.25-3build2
ARG PSYCOPG_DEB_VERSION=3.1.17-2

# Variant of ../rmw-netem/Dockerfile that ALSO builds the 5G-LENA "nr"
# contrib module, for the paper's "5G" network profile (Bảng V) --
# ns-3's own module list has no LTE/NR support at all (confirmed via
# `pkg-config --list-all | grep ns3` on the base :jazzy image: no
# ns3-lte, no ns3-nr), so this has to be added as an out-of-tree contrib
# module before ns-3's own build, not something addable to an
# already-built install.
#
# Built as a SEPARATE image tag (see build_5g_lena_image.sh) rather than
# replacing rmw-netem/Dockerfile's output, so every already-validated
# wifi-based experiment in this investigation keeps using the exact
# image it was measured against -- this variant is additive, not a
# replacement.
ARG NS3_VERSION=ns-3.41
# NR-v3.0 is the release RELEASE_NOTES.md itself states is "compatible
# with ns-3.41" (confirmed by inspecting a fresh clone of the nr repo's
# RELEASE_NOTES.md directly -- other releases target ns-3.39/3.40/3.42).
ARG NR_VERSION=5g-lena-v3.0.y

SHELL ["/bin/bash", "-lc"]

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        iproute2 \
        build-essential \
        cmake \
        ninja-build \
        git \
        libgnutls28-dev \
        libngtcp2-dev \
        libngtcp2-crypto-gnutls-dev \
        libnghttp3-dev \
        ngtcp2-client \
        ngtcp2-server \
        openssl \
        pkg-config \
        libgsl-dev \
        libxml2-dev \
        libsqlite3-dev \
        libssl-dev \
        python3-aioquic=${AIOQUIC_DEB_VERSION} \
        python3-psycopg=${PSYCOPG_DEB_VERSION} \
        python3-colcon-common-extensions \
        ros-jazzy-geometry-msgs \
        ros-jazzy-nav-msgs \
        ros-jazzy-nav2-behaviors \
        ros-jazzy-nav2-behavior-tree \
        ros-jazzy-nav2-bt-navigator \
        ros-jazzy-nav2-controller \
        ros-jazzy-nav2-dwb-controller \
        ros-jazzy-nav2-lifecycle-manager \
        ros-jazzy-nav2-msgs \
        ros-jazzy-nav2-navfn-planner \
        ros-jazzy-nav2-planner \
        ros-jazzy-rmf-fleet-msgs \
        ros-jazzy-rmf-task-msgs \
        ros-jazzy-rmf-traffic-msgs \
        ros-jazzy-rmw-cyclonedds-cpp \
        ros-jazzy-rosidl-default-generators \
        ros-jazzy-rosidl-default-runtime \
        ros-jazzy-sensor-msgs \
        ros-jazzy-std-msgs \
        ros-jazzy-std-srvs \
        ros-jazzy-tf2-msgs \
        ros-jazzy-tinyxml2-vendor \
    && rm -rf /var/lib/apt/lists/*

ARG NS3_BUILD_JOBS=4
COPY external/ns3/patches/0001-tap-bridge-disable-use-local-address-autolearn-ns3.41.patch /tmp/ns3-tap-bridge-fix.patch
COPY external/ns3/patches/0002-wifi-phy-state-helper-missing-algorithm-header-ns3.41.patch /tmp/ns3-missing-header-fix.patch
RUN git clone --branch "${NS3_VERSION}" --depth 1 https://gitlab.com/nsnam/ns-3-dev.git /tmp/ns3-src \
    && cd /tmp/ns3-src \
    && git apply /tmp/ns3-tap-bridge-fix.patch /tmp/ns3-missing-header-fix.patch \
    && git clone --branch "${NR_VERSION}" --depth 1 https://gitlab.com/cttc-lena/nr.git contrib/nr \
    && cmake -S . -B build -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr \
        -DNS3_EXAMPLES=OFF \
        -DNS3_TESTS=OFF \
        -DNS3_ENABLED_MODULES='applications;bridge;core;csma;internet;mobility;network;point-to-point;tap-bridge;wifi;nr' \
        -DCMAKE_CXX_FLAGS='-Wno-error=array-bounds -Wno-error=stringop-overflow' \
    && cmake --build build -j "${NS3_BUILD_JOBS}" \
    && cmake --install build \
    && ldconfig \
    && cd / && rm -rf /tmp/ns3-src /tmp/ns3-tap-bridge-fix.patch /tmp/ns3-missing-header-fix.patch

RUN apt-get update \
    && (apt-get install -y --no-install-recommends ros-jazzy-rmw-zenoh-cpp || true) \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work
ENV PYTHONPATH=/work

ENTRYPOINT ["/bin/bash", "-lc"]
