FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG OMNETPP_COMMIT=2cf25223fee7c2386f4c610f6fcb12d9585497ab
ARG INET_COMMIT=dfe270b21f38856874e3ba50ef964bd557e4bb99

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bison \
        build-essential \
        ca-certificates \
        ccache \
        clang \
        diffutils \
        flex \
        gawk \
        git \
        libdw-dev \
        libxml2-dev \
        lld \
        make \
        perl \
        pkg-config \
        python3 \
        python3-dev \
        sed \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

RUN git config --global http.version HTTP/1.1 \
    && git config --global http.postBuffer 1048576000 \
    && git init /opt/omnetpp \
    && git -C /opt/omnetpp remote add origin https://github.com/omnetpp/omnetpp.git \
    && for attempt in 1 2 3 4 5; do \
        git -C /opt/omnetpp fetch --depth 1 origin "${OMNETPP_COMMIT}" && break; \
        echo "omnetpp fetch attempt ${attempt} failed, retrying..."; \
        sleep 5; \
    done \
    && git -C /opt/omnetpp checkout --detach FETCH_HEAD \
    && test "$(git -C /opt/omnetpp rev-parse HEAD)" = "${OMNETPP_COMMIT}"

WORKDIR /opt/omnetpp
RUN cp configure.user.dist configure.user \
    && bash -lc '. ./setenv -q \
        && ./configure \
            WITH_LIBXML=yes \
            WITH_OSG=no \
            WITH_OSGEARTH=no \
            WITH_PARSIM=no \
            WITH_PYTHON=no \
            WITH_QTENV=no \
            WITH_SCAVE_PYTHON_BINDINGS=no \
        && make MODE=release -j"$(nproc)" base'

RUN git init /opt/inet \
    && git -C /opt/inet remote add origin https://github.com/inet-framework/inet.git \
    && for attempt in 1 2 3 4 5; do \
        git -C /opt/inet fetch --depth 1 origin "${INET_COMMIT}" && break; \
        echo "inet fetch attempt ${attempt} failed, retrying..."; \
        sleep 5; \
    done \
    && git -C /opt/inet checkout --detach FETCH_HEAD \
    && test "$(git -C /opt/inet rev-parse HEAD)" = "${INET_COMMIT}"

WORKDIR /opt/inet
RUN bash -lc '. /opt/omnetpp/setenv -q \
        && . ./setenv -q \
        && opp_featuretool disable all \
        && opp_featuretool enable --with-required \
            Ipv4 Loopback Ppp Queueing Udp Ieee80211 Mobility Ethernet \
        && make makefiles \
        && make MODE=release -j"$(nproc)"' \
    && cp -L /opt/inet/src/libINET.so /tmp/libINET.so \
    && rm -rf /opt/omnetpp/.git /opt/omnetpp/out \
        /opt/omnetpp/doc /opt/omnetpp/samples /opt/omnetpp/test /opt/omnetpp/ui \
        /opt/inet/.git /opt/inet/out /opt/inet/doc /opt/inet/examples \
        /opt/inet/showcases /opt/inet/tests /opt/inet/tutorials \
    && rm -f /opt/inet/src/libINET.so \
    && mv /tmp/libINET.so /opt/inet/src/libINET.so \
    && ln -s /opt/omnetpp/bin/opp_nedtool /usr/local/bin/nedtool

ENV OMNETPP_ROOT=/opt/omnetpp
ENV INET_ROOT=/opt/inet
ENV PATH=/opt/inet/bin:/opt/omnetpp/bin:${PATH}
ENV LD_LIBRARY_PATH=/opt/inet/src:/opt/omnetpp/lib
ENV OMNETPP_IMAGE_PATH=/opt/omnetpp/images:/opt/inet/images

WORKDIR /work

LABEL org.fleetqox.omnetpp.version="omnetpp-6.4.0" \
      org.fleetqox.inet.version="inet-4.7.0"

RUN test "$(cat /opt/omnetpp/Version)" = "omnetpp-6.4.0" \
    && test -x /opt/omnetpp/bin/opp_run_release \
    && test -x /opt/omnetpp/bin/opp_nedtool \
    && test -f /opt/inet/src/libINET.so
