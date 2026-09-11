ARG ROS_IMAGE=ros:jazzy-ros-base
FROM ${ROS_IMAGE}

ARG AIOQUIC_DEB_VERSION=0.9.25-3build2
ARG PSYCOPG_DEB_VERSION=3.1.17-2

# Pin the exact ns-3 release built from source below (replaces the
# apt-installed `ns3`/`libns3-dev` 3.41-1.1build1 packages -- see
# docs/AUDIT_ACCEPTANCE_TRACKING.md's "GIẢI QUYẾT: build ns-3 từ
# source..." section). apt's ns-3 3.41 has a real TapBridge bug
# (Mode=UseLocal's one-shot address auto-learn races against spurious
# background traffic -- e.g. IPv6 neighbor discovery, or another bridged
# station's multicast -- and whichever wins gets permanently locked in,
# unrelated to the actual station on that tap) that silently breaks any
# unicast relay through a TapBridge-connected AP; confirmed root cause
# via a custom debug build with NS_LOG, fixed by
# external/ns3/patches/0001-tap-bridge-disable-use-local-address-autolearn-ns3.41.patch.
ARG NS3_VERSION=ns-3.41

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

# Build ns-3 ${NS3_VERSION} from source with the TapBridge fix (see the
# comment above NS3_VERSION). NS3_BUILD_JOBS defaults conservatively --
# ns-3's heavier translation units can use well over 1GB each, and a
# naive -j "$(nproc)" OOM-killed cc1plus mid-build during development on
# a host whose Docker VM only exposed ~3.7GB despite the host having
# 15GB; raise it via --build-arg if the actual build host has more
# memory per core to spare.
ARG NS3_BUILD_JOBS=4
COPY external/ns3/patches/0001-tap-bridge-disable-use-local-address-autolearn-ns3.41.patch /tmp/ns3-tap-bridge-fix.patch
# ns-3 3.41 was written against an older compiler than this image's GCC
# 15 -- wifi-phy-state-helper.h uses std::transform without including
# <algorithm>, which older libstdc++ pulled in transitively but this one
# doesn't; unrelated to the TapBridge fix, just a real compile error
# under a newer toolchain than ns-3 3.41 originally targeted.
COPY external/ns3/patches/0002-wifi-phy-state-helper-missing-algorithm-header-ns3.41.patch /tmp/ns3-missing-header-fix.patch
RUN git clone --branch "${NS3_VERSION}" --depth 1 https://gitlab.com/nsnam/ns-3-dev.git /tmp/ns3-src \
    && cd /tmp/ns3-src \
    && git apply /tmp/ns3-tap-bridge-fix.patch /tmp/ns3-missing-header-fix.patch \
    && cmake -S . -B build -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr \
        -DNS3_EXAMPLES=OFF \
        -DNS3_TESTS=OFF \
        -DNS3_ENABLED_MODULES='applications;bridge;core;csma;internet;mobility;network;point-to-point;tap-bridge;wifi' \
    && cmake --build build -j "${NS3_BUILD_JOBS}" \
    && cmake --install build \
    && ldconfig \
    && cd / && rm -rf /tmp/ns3-src /tmp/ns3-tap-bridge-fix.patch /tmp/ns3-missing-header-fix.patch

# rmw_zenoh may not be available in every ROS apt snapshot. Keep the image
# build usable and let baseline runners report unavailable RMWs at execution.
RUN apt-get update \
    && (apt-get install -y --no-install-recommends ros-jazzy-rmw-zenoh-cpp || true) \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work
ENV PYTHONPATH=/work

ENTRYPOINT ["/bin/bash", "-lc"]
