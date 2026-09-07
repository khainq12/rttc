# FleetQoX public-API ngtcp2/GnuTLS server

This image rebuilds the official ngtcp2 `v0.12.1` GnuTLS HTTP/3 server
example at the exact source commit used by the Ubuntu 24.04 development
packages. The small auditable patch replaces the example's request-only
client-certificate behavior with:

- `GNUTLS_CERT_REQUIRE`;
- a mandatory PEM trust store loaded with
  `gnutls_certificate_set_x509_trust_file`;
- an optional CRL loaded with `gnutls_certificate_set_x509_crl_file`;
- handshake-time chain/revocation verification through
  `gnutls_certificate_set_verify_function` and
  `gnutls_certificate_verify_peers3`;
- client-auth EKU and optional exact URI-SAN enforcement.
- disabled TLS/QUIC early data at the server edge.

All TLS and QUIC operations use public GnuTLS/ngtcp2/nghttp3 APIs. The optional
`FLEETQOX_STATE_BACKEND_SOCKET` path buffers a bounded request body and passes
method, URI, the verified certificate identity, and body to
`fleetqox.public_quic_gateway_backend`. That service uses the same
`FleetQoxGatewayState` engine as the aioquic compatibility gateway. The local
protocol uses fixed magic/version bytes, network-order lengths, 1 MiB request
and 4 MiB response limits, three-second socket timeouts, and fail-closed HTTP
502 behavior.

Build from the repository root:

```bash
docker build \
  -f external/ngtcp2-public-mtls/Dockerfile \
  -t localhost/fleetrmw/ngtcp2-public-mtls:0.12.1 \
  .
```

Run the canonical five-round Docker/netem proof:

```bash
python3 scripts/run_rmw_docker_ngtcp2_public_mtls_server_probe.py \
  --iterations 5
```

The summary is written to
`results_rmw_socket/docker_ngtcp2_public_mtls_server_summary.json`.

Run the stateful public-server proof:

```bash
python3 scripts/run_rmw_docker_ngtcp2_public_stateful_gateway_probe.py \
  --iterations 5
```

This second artifact proves history, deduplication, independent consumer
cursors, publisher identity binding, invalid-frame status propagation, and
multi-stream session reuse under Docker/netem. Native public path metrics and
nonblocking backend I/O remain outside its claim.

The image also builds a second binary, `fleetqox-public-mtls-client`, from
the same patched source tree
(`client-server-cert-verification.patch`). The distro-packaged `gtlsclient`
used by every other probe in this family never verifies the server's
certificate at all, so it cannot observe a server-certificate rotation;
this client adds an opt-in `--verify-server-cert` flag that actually does,
without changing `gtlsclient`'s own default (unverified) behavior for
anything else. Run the online-rotation proofs:

```bash
python3 scripts/run_rmw_docker_ngtcp2_public_online_client_ca_rotation_probe.py \
  --iterations 5
python3 scripts/run_rmw_docker_ngtcp2_public_online_server_certificate_rotation_probe.py \
  --iterations 5
```
