#!/usr/bin/env python3
"""DCS-authorized hardware STONITH agent, fencing via a real Redfish BMC call.

Structurally this is fleetqox_postgres_fence_agent.py -- same DCS-lease
authorization check, same mTLS client-identity binding on the /fence
endpoint -- with exactly one thing swapped: instead of calling the Docker
socket API to SIGKILL a container, it makes a real DMTF Redfish HTTPS call
(`POST .../Actions/ComputerSystem.Reset` with `ResetType: ForceOff`) to the
target node's BMC, then polls the same BMC's `PowerState` to confirm the
power action actually took effect before reporting the fence as confirmed.

See scripts/fleetqox_redfish_bmc_simulator.py for what this has actually
been verified against: a minimal but protocol-conformant Redfish server
implementing the same two endpoints. No physical server or real vendor BMC
is available in this environment, so this code is untested against real
hardware firmware -- that is a real, stated limitation of this evidence,
not something the simulator can close.
"""

from __future__ import annotations

import argparse
import base64
import json
import ssl
import time
from typing import Any
from urllib import error, request
from urllib.parse import quote

from fleetqox.postgres_failover_dcs import EtcdQuorumLease


SCHEMA_VERSION = "fleetrmw.hardware_stonith_agent.v1"


def _basic_auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


def redfish_get(
    *, bmc_url: str, path: str, username: str, password: str,
    ca_file: str, timeout_s: float,
) -> tuple[int, dict[str, Any] | None]:
    call = request.Request(bmc_url.rstrip("/") + path, method="GET")
    call.add_header("Authorization", _basic_auth_header(username, password))
    context = ssl.create_default_context(cafile=ca_file)
    try:
        with request.urlopen(call, timeout=timeout_s, context=context) as response:
            raw = response.read()
            document = json.loads(raw.decode()) if raw else None
            return response.status, document if isinstance(document, dict) else None
    except error.HTTPError as exc:
        return exc.code, None
    except (error.URLError, TimeoutError, ssl.SSLError, json.JSONDecodeError, OSError):
        return -1, None


def redfish_post(
    *, bmc_url: str, path: str, username: str, password: str,
    ca_file: str, timeout_s: float, payload: dict[str, Any],
) -> int:
    body = json.dumps(payload).encode()
    call = request.Request(
        bmc_url.rstrip("/") + path, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    call.add_header("Authorization", _basic_auth_header(username, password))
    context = ssl.create_default_context(cafile=ca_file)
    try:
        with request.urlopen(call, timeout=timeout_s, context=context) as response:
            return response.status
    except error.HTTPError as exc:
        return exc.code
    except (error.URLError, TimeoutError, ssl.SSLError, OSError):
        return -1


def redfish_power_state(
    *, bmc_url: str, system_id: str, username: str, password: str,
    ca_file: str, timeout_s: float,
) -> str | None:
    status, document = redfish_get(
        bmc_url=bmc_url, path=f"/redfish/v1/Systems/{quote(system_id, safe='')}",
        username=username, password=password, ca_file=ca_file, timeout_s=timeout_s,
    )
    if status != 200 or document is None:
        return None
    state = document.get("PowerState")
    return state if isinstance(state, str) else None


def redfish_force_off(
    *, bmc_url: str, system_id: str, username: str, password: str,
    ca_file: str, timeout_s: float,
) -> int:
    return redfish_post(
        bmc_url=bmc_url,
        path=(
            f"/redfish/v1/Systems/{quote(system_id, safe='')}"
            "/Actions/ComputerSystem.Reset"
        ),
        username=username, password=password, ca_file=ca_file, timeout_s=timeout_s,
        payload={"ResetType": "ForceOff"},
    )


def build_server(args: argparse.Namespace) -> Any:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class HardwareFenceServer(ThreadingHTTPServer):
        dcs: EtcdQuorumLease
        lease_key: str

    class HardwareFenceHandler(BaseHTTPRequestHandler):
        server: HardwareFenceServer

        def log_message(self, format: str, *log_args: Any) -> None:
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
            power_state_before = redfish_power_state(
                bmc_url=args.bmc_url, system_id=args.system_id,
                username=args.bmc_username, password=args.bmc_password,
                ca_file=args.bmc_ca, timeout_s=args.bmc_timeout_s,
            )
            reset_status = -1
            if power_state_before == "On":
                reset_status = redfish_force_off(
                    bmc_url=args.bmc_url, system_id=args.system_id,
                    username=args.bmc_username, password=args.bmc_password,
                    ca_file=args.bmc_ca, timeout_s=args.bmc_timeout_s,
                )
            deadline = time.monotonic() + args.bmc_timeout_s + 2.0
            power_state_after = power_state_before
            while time.monotonic() < deadline:
                power_state_after = redfish_power_state(
                    bmc_url=args.bmc_url, system_id=args.system_id,
                    username=args.bmc_username, password=args.bmc_password,
                    ca_file=args.bmc_ca, timeout_s=args.bmc_timeout_s,
                )
                if power_state_after == "Off":
                    break
                time.sleep(0.05)
            fenced = (
                power_state_before == "On"
                and reset_status == 204
                and power_state_after == "Off"
            )
            result = {
                "schema_version": SCHEMA_VERSION,
                "status": "fenced" if fenced else "fence_failed",
                "dcs_lease_authorized": authorized,
                "mtls_client_authenticated": True,
                "peer_common_name": peer_common_name,
                "controller_id": controller_id,
                "lease_id": lease_id,
                "fence_mechanism": "redfish_computer_system_reset",
                "bmc_url": args.bmc_url,
                "system_id": args.system_id,
                "power_state_before": power_state_before,
                "redfish_reset_status": reset_status,
                "power_state_after": power_state_after,
                "hard_fence_confirmed": fenced,
                "fence_confirmed_unix_ns": time.time_ns() if fenced else None,
            }
            print(json.dumps(result, sort_keys=True), flush=True)
            self._reply(200 if fenced else 503, result)

    server = HardwareFenceServer((args.host, args.port), HardwareFenceHandler)
    server.dcs = EtcdQuorumLease(
        tuple(part for part in args.etcd_endpoints.split(",") if part),
        timeout_s=0.75,
        ca_file=args.etcd_ca,
        cert_file=args.etcd_cert,
        key_file=args.etcd_key,
    )
    server.lease_key = args.lease_key
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(certfile=args.tls_cert, keyfile=args.tls_key)
    tls.load_verify_locations(cafile=args.tls_ca)
    tls.verify_mode = ssl.CERT_REQUIRED
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    return server


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=4513)
    parser.add_argument("--bmc-url", required=True)
    parser.add_argument("--system-id", default="1")
    parser.add_argument("--bmc-username", required=True)
    parser.add_argument("--bmc-password", required=True)
    parser.add_argument("--bmc-ca", required=True)
    parser.add_argument("--bmc-timeout-s", type=float, default=5.0)
    parser.add_argument("--tls-ca", required=True)
    parser.add_argument("--tls-cert", required=True)
    parser.add_argument("--tls-key", required=True)
    parser.add_argument("--etcd-endpoints", required=True)
    parser.add_argument("--etcd-ca", required=True)
    parser.add_argument("--etcd-cert", required=True)
    parser.add_argument("--etcd-key", required=True)
    parser.add_argument("--lease-key", default="/fleetqox/hardware/failover")
    args = parser.parse_args()
    if args.bmc_timeout_s <= 0:
        parser.error("--bmc-timeout-s must be positive")
    server = build_server(args)
    print(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "status": "ready",
                "bmc_url": args.bmc_url,
                "system_id": args.system_id,
                "fence_mechanism": "redfish_computer_system_reset",
                "dcs_endpoint_count": len(server.dcs.endpoints),
                "mutual_tls": True,
                "minimum_tls_version": "TLSv1.2",
                "client_certificate_required": True,
                "hardware_validated": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
