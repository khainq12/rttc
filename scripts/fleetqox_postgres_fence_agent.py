#!/usr/bin/env python3
"""DCS-authorized Docker STONITH agent for the PostgreSQL HA probe."""

from __future__ import annotations

import argparse
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import ssl
import time
from typing import Any
from urllib.parse import quote

from fleetqox.postgres_failover_dcs import EtcdQuorumLease


SCHEMA_VERSION = "fleetrmw.postgresql_fence_agent.v1"


class UnixHttpConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str) -> None:
        super().__init__("localhost")
        self.socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.socket_path)


def docker_connection(target: str) -> http.client.HTTPConnection:
    # `target` is either a Unix socket path (default
    # /var/run/docker.sock, the original and still-default behavior for
    # same-host fencing) or a tcp://host:port URL for a remote Docker
    # Engine API -- e.g. the primary being fenced runs on a different
    # host/VM than this fence agent, so the local Docker socket cannot
    # reach it. Same authorization/DCS-lease checks either way; only the
    # transport to the Docker daemon differs.
    if target.startswith("tcp://"):
        host, _, port = target[len("tcp://"):].partition(":")
        return http.client.HTTPConnection(host, int(port) if port else 2375)
    return UnixHttpConnection(target)


def docker_call(
    socket_path: str, method: str, path: str
) -> tuple[int, dict[str, Any]]:
    connection = docker_connection(socket_path)
    try:
        connection.request(method, path)
        response = connection.getresponse()
        raw = response.read()
        document = json.loads(raw.decode()) if raw else {}
        return response.status, document if isinstance(document, dict) else {}
    finally:
        connection.close()


def container_running(socket_path: str, container: str) -> bool | None:
    status, document = docker_call(
        socket_path, "GET", f"/containers/{quote(container, safe='')}/json"
    )
    if status != 200:
        return None
    return bool(document.get("State", {}).get("Running"))


class FenceServer(ThreadingHTTPServer):
    dcs: EtcdQuorumLease
    lease_key: str
    target_container: str
    docker_socket: str


class FenceHandler(BaseHTTPRequestHandler):
    server: FenceServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _reply(self, status: int, document: dict[str, Any]) -> None:
        body = json.dumps(document, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/fence":
            self._reply(404, {"status": "not_found"})
            return
        try:
            length = int(self.headers.get("content-length", "0"))
            document = json.loads(self.rfile.read(length).decode())
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            self._reply(400, {"status": "invalid_request"})
            return
        controller_id = document.get("controller_id")
        lease_id = str(document.get("lease_id", ""))
        if not isinstance(controller_id, str) or not controller_id or not lease_id:
            self._reply(400, {"status": "missing_lease_identity"})
            return
        peer = self.connection.getpeercert()
        peer_common_name = next(
            (
                value
                for relative_name in peer.get("subject", ())
                for key, value in relative_name
                if key == "commonName"
            ),
            "",
        )
        if peer_common_name != controller_id:
            self._reply(403, {"status": "client_identity_not_bound"})
            return
        try:
            leader = self.server.dcs.get(key=self.server.lease_key)
        except RuntimeError:
            self._reply(503, {"status": "dcs_unavailable"})
            return
        authorized = (
            leader is not None
            and leader.value == controller_id
            and leader.lease_id == lease_id
        )
        if not authorized:
            self._reply(403, {"status": "dcs_lease_not_authorized"})
            return
        running_before = container_running(
            self.server.docker_socket, self.server.target_container
        )
        kill_status = 304
        if running_before is True:
            kill_status, _ = docker_call(
                self.server.docker_socket,
                "POST",
                f"/containers/{quote(self.server.target_container, safe='')}/kill"
                "?signal=SIGKILL",
            )
        deadline = time.monotonic() + 5.0
        running_after = running_before
        while time.monotonic() < deadline:
            running_after = container_running(
                self.server.docker_socket, self.server.target_container
            )
            if running_after is False:
                break
            time.sleep(0.05)
        fenced = running_before is True and kill_status == 204 and running_after is False
        result = {
            "schema_version": SCHEMA_VERSION,
            "status": "fenced" if fenced else "fence_failed",
            "dcs_lease_authorized": authorized,
            "mtls_client_authenticated": True,
            "peer_common_name": peer_common_name,
            "controller_id": controller_id,
            "lease_id": lease_id,
            "target_container": self.server.target_container,
            "running_before": running_before,
            "docker_kill_status": kill_status,
            "running_after": running_after,
            "hard_fence_confirmed": fenced,
            "fence_confirmed_unix_ns": time.time_ns() if fenced else None,
        }
        print(json.dumps(result, sort_keys=True), flush=True)
        self._reply(200 if fenced else 503, result)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=4510)
    parser.add_argument("--target-container", required=True)
    parser.add_argument(
        "--docker-socket", default="/var/run/docker.sock",
        help="Unix socket path (same-host fencing, default) or a "
             "tcp://host:port Docker Engine API URL (cross-host fencing)",
    )
    parser.add_argument("--tls-ca", required=True)
    parser.add_argument("--tls-cert", required=True)
    parser.add_argument("--tls-key", required=True)
    parser.add_argument("--etcd-endpoints", required=True)
    parser.add_argument("--etcd-ca", required=True)
    parser.add_argument("--etcd-cert", required=True)
    parser.add_argument("--etcd-key", required=True)
    parser.add_argument("--lease-key", default="/fleetqox/postgresql/failover")
    args = parser.parse_args()
    server = FenceServer((args.host, args.port), FenceHandler)
    server.dcs = EtcdQuorumLease(
        tuple(part for part in args.etcd_endpoints.split(",") if part),
        timeout_s=0.75,
        ca_file=args.etcd_ca,
        cert_file=args.etcd_cert,
        key_file=args.etcd_key,
    )
    server.lease_key = args.lease_key
    server.target_container = args.target_container
    server.docker_socket = args.docker_socket
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(certfile=args.tls_cert, keyfile=args.tls_key)
    tls.load_verify_locations(cafile=args.tls_ca)
    tls.verify_mode = ssl.CERT_REQUIRED
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    print(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "status": "ready",
                "target_container": args.target_container,
                "dcs_endpoint_count": len(server.dcs.endpoints),
                "mutual_tls": True,
                "minimum_tls_version": "TLSv1.2",
                "client_certificate_required": True,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
