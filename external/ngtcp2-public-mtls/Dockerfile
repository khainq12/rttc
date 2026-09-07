ARG BASE_IMAGE=localhost/fleetrmw/rmw-netem:jazzy
FROM ${BASE_IMAGE}

ARG NGTCP2_COMMIT=a4ba3f20d70d4a4d79674cee1093c55b4c1d78ed

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        libev-dev \
        libhttp-parser-dev \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/ngtcp2/ngtcp2.git /opt/ngtcp2-source \
    && git -C /opt/ngtcp2-source checkout "${NGTCP2_COMMIT}"

COPY external/ngtcp2-public-mtls/public-mtls.patch /tmp/public-mtls.patch
COPY external/ngtcp2-public-mtls/stateful-backend.patch /tmp/stateful-backend.patch
COPY external/ngtcp2-public-mtls/async-backend.patch /tmp/async-backend.patch
COPY external/ngtcp2-public-mtls/identity-fairness.patch /tmp/identity-fairness.patch
COPY external/ngtcp2-public-mtls/active-worker-isolation.patch /tmp/active-worker-isolation.patch
COPY external/ngtcp2-public-mtls/online-crl-refresh.patch /tmp/online-crl-refresh.patch
COPY external/ngtcp2-public-mtls/active-session-revocation.patch /tmp/active-session-revocation.patch
COPY external/ngtcp2-public-mtls/server-certificate-rotation.patch /tmp/server-certificate-rotation.patch
COPY external/ngtcp2-public-mtls/client-server-cert-verification.patch /tmp/client-server-cert-verification.patch

RUN git -C /opt/ngtcp2-source apply /tmp/public-mtls.patch \
    && git -C /opt/ngtcp2-source apply /tmp/stateful-backend.patch \
    && git -C /opt/ngtcp2-source apply /tmp/async-backend.patch \
    && git -C /opt/ngtcp2-source apply /tmp/identity-fairness.patch \
    && git -C /opt/ngtcp2-source apply /tmp/active-worker-isolation.patch \
    && git -C /opt/ngtcp2-source apply /tmp/online-crl-refresh.patch \
    && git -C /opt/ngtcp2-source apply /tmp/active-session-revocation.patch \
    && git -C /opt/ngtcp2-source apply /tmp/server-certificate-rotation.patch \
    && git -C /opt/ngtcp2-source apply /tmp/client-server-cert-verification.patch \
    && c++ -std=c++20 -O2 -Wall -Wextra \
        -DENABLE_EXAMPLE_GNUTLS \
        -DWITH_EXAMPLE_GNUTLS \
        -DHAVE_ARPA_INET_H \
        -DHAVE_ASM_TYPES_H \
        -DHAVE_LINUX_NETLINK_H \
        -DHAVE_LINUX_RTNETLINK_H \
        -DHAVE_NETINET_IN_H \
        -DHAVE_SYS_SOCKET_H \
        -I/opt/ngtcp2-source/examples \
        -I/opt/ngtcp2-source/third-party \
        /opt/ngtcp2-source/examples/server.cc \
        /opt/ngtcp2-source/examples/server_base.cc \
        /opt/ngtcp2-source/examples/debug.cc \
        /opt/ngtcp2-source/examples/util.cc \
        /opt/ngtcp2-source/examples/http.cc \
        /opt/ngtcp2-source/examples/shared.cc \
        /opt/ngtcp2-source/examples/tls_server_context_gnutls.cc \
        /opt/ngtcp2-source/examples/tls_server_session_gnutls.cc \
        /opt/ngtcp2-source/examples/tls_session_base_gnutls.cc \
        /opt/ngtcp2-source/examples/util_gnutls.cc \
        -o /usr/local/bin/fleetqox-public-mtls-server \
        $(pkg-config --cflags --libs \
          libngtcp2_crypto_gnutls libngtcp2 libnghttp3 gnutls) \
        -lev -lhttp_parser

RUN test "$(/usr/local/bin/fleetqox-public-mtls-server --help 2>&1 \
      | grep -c 'FLEETQOX_GNUTLS_CLIENT_CA')" -eq 1

# The distro-packaged gtlsclient (from apt's ngtcp2-client, installed in the
# base image) never verifies the server's certificate at all, so it cannot
# be used to observe a server-certificate-rotation outcome. This client is
# built from the same patched source tree as the server above, adding an
# opt-in --verify-server-cert flag (client-server-cert-verification.patch)
# that gtlsclient does not have; it is a separate binary so every other
# probe that already depends on gtlsclient's default unverified behavior is
# unaffected.
RUN c++ -std=c++20 -O2 -Wall -Wextra \
        -DENABLE_EXAMPLE_GNUTLS \
        -DWITH_EXAMPLE_GNUTLS \
        -DHAVE_ARPA_INET_H \
        -DHAVE_ASM_TYPES_H \
        -DHAVE_LINUX_NETLINK_H \
        -DHAVE_LINUX_RTNETLINK_H \
        -DHAVE_NETINET_IN_H \
        -DHAVE_SYS_SOCKET_H \
        -I/opt/ngtcp2-source/examples \
        -I/opt/ngtcp2-source/third-party \
        /opt/ngtcp2-source/examples/client.cc \
        /opt/ngtcp2-source/examples/client_base.cc \
        /opt/ngtcp2-source/examples/debug.cc \
        /opt/ngtcp2-source/examples/util.cc \
        /opt/ngtcp2-source/examples/shared.cc \
        /opt/ngtcp2-source/examples/tls_client_context_gnutls.cc \
        /opt/ngtcp2-source/examples/tls_client_session_gnutls.cc \
        /opt/ngtcp2-source/examples/tls_session_base_gnutls.cc \
        /opt/ngtcp2-source/examples/util_gnutls.cc \
        -o /usr/local/bin/fleetqox-public-mtls-client \
        $(pkg-config --cflags --libs \
          libngtcp2_crypto_gnutls libngtcp2 libnghttp3 gnutls) \
        -lev -lhttp_parser

RUN test "$(/usr/local/bin/fleetqox-public-mtls-client --help 2>&1 \
      | grep -c 'verify-server-cert')" -eq 1

WORKDIR /work
