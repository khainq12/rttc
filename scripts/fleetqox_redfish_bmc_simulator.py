#!/usr/bin/env python3
"""Minimal DMTF Redfish-conformant BMC simulator for hardware STONITH tests.

There is no real server hardware anywhere in this environment (no BMC, no
IPMI/Redfish-capable chassis) -- but the actual wire protocol real hardware
fencing depends on is a well-documented, standard HTTPS/JSON API (DMTF
Redfish), not something vendor-specific. This implements just the two
endpoints a power-fencing client actually needs -- reading `PowerState` on
a ComputerSystem resource and invoking `ComputerSystem.Reset` on it -- with
the real Redfish request/response shapes, real HTTP Basic authentication,
and real TLS, so a genuine Redfish HTTP client (scripts/fleetqox_hardware_stonith_agent.py)
can be exercised end to end without inventing a fake protocol of its own.

This is the same pattern OpenStack Ironic/Metal3 use for testing
bare-metal power management in CI (their "sushy-tools" Redfish emulator)
-- and the same pattern this project already uses elsewhere (a real
etcd/PostgreSQL container standing in for a specific real dependency,
rather than a hand-rolled fake of the whole thing). What it does NOT prove
is that this exact code also works against a specific vendor's real BMC
firmware, which can have quirks/bugs no simulator captures -- that
residual gap is inherent to testing without physical hardware, not a
shortcut taken here.
"""

from __future__ import annotations

import argparse
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import ssl
import threading
from typing import Any


SCHEMA_VERSION = "fleetrmw.redfish_bmc_simulator.v1"
VALID_RESET_TYPES = ("On", "ForceOff", "GracefulShutdown", "ForceRestart")


class BmcServer(ThreadingHTTPServer):
    username: str
    password: str
    system_id: str
    lock: threading.Lock
    power_state: str
    reset_actions_received: int
    unauthorized_requests: int


class BmcHandler(BaseHTTPRequestHandler):
    server: BmcServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _reply(self, status: int, document: dict[str, Any] | None) -> None:
        body = json.dumps(document, sort_keys=True).encode() if document is not None else b""
        self.send_response(status)
        if document is not None:
            self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _authenticated(self) -> bool:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[len("Basic "):]).decode()
        except (ValueError, UnicodeDecodeError):
            return False
        username, _, password = decoded.partition(":")
        return username == self.server.username and password == self.server.password

    def _require_auth(self) -> bool:
        if self._authenticated():
            return True
        with self.server.lock:
            self.server.unauthorized_requests += 1
        self.send_response(401)
        self.send_header(
            "WWW-Authenticate", 'Basic realm="Redfish BMC Simulator"',
        )
        self.send_header("content-length", "0")
        self.end_headers()
        return False

    def do_GET(self) -> None:
        if self.path == "/redfish/v1/":
            self._reply(200, {
                "@odata.id": "/redfish/v1/",
                "@odata.type": "#ServiceRoot.v1_5_0.ServiceRoot",
                "Id": "RootService",
                "RedfishVersion": "1.9.0",
                "Systems": {"@odata.id": "/redfish/v1/Systems"},
            })
            return
        if self.path == f"/redfish/v1/Systems/{self.server.system_id}":
            if not self._require_auth():
                return
            with self.server.lock:
                power_state = self.server.power_state
            self._reply(200, {
                "@odata.id": f"/redfish/v1/Systems/{self.server.system_id}",
                "@odata.type": "#ComputerSystem.v1_13_0.ComputerSystem",
                "Id": self.server.system_id,
                "PowerState": power_state,
                "Actions": {
                    "#ComputerSystem.Reset": {
                        "target": (
                            f"/redfish/v1/Systems/{self.server.system_id}"
                            "/Actions/ComputerSystem.Reset"
                        ),
                        "ResetType@Redfish.AllowableValues": list(VALID_RESET_TYPES),
                    }
                },
            })
            return
        self._reply(404, {"error": {"message": "resource not found"}})

    def do_POST(self) -> None:
        expected_path = (
            f"/redfish/v1/Systems/{self.server.system_id}"
            "/Actions/ComputerSystem.Reset"
        )
        if self.path != expected_path:
            self._reply(404, {"error": {"message": "resource not found"}})
            return
        if not self._require_auth():
            return
        try:
            length = int(self.headers.get("content-length", "0"))
            document = json.loads(self.rfile.read(length).decode())
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            self._reply(400, {"error": {"message": "malformed request body"}})
            return
        reset_type = document.get("ResetType") if isinstance(document, dict) else None
        if reset_type not in VALID_RESET_TYPES:
            self._reply(400, {
                "error": {
                    "message": f"ResetType must be one of {VALID_RESET_TYPES}",
                }
            })
            return
        with self.server.lock:
            self.server.reset_actions_received += 1
            if reset_type in ("ForceOff", "GracefulShutdown"):
                self.server.power_state = "Off"
            else:
                self.server.power_state = "On"
        # Real Redfish services return 204 for a simple action with no
        # response body (as opposed to an async Task, which this minimal
        # simulator does not model).
        self._reply(204, None)

    def do_DELETE(self) -> None:
        self._reply(404, {"error": {"message": "resource not found"}})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=4512)
    parser.add_argument("--system-id", default="1")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--tls-cert", required=True)
    parser.add_argument("--tls-key", required=True)
    parser.add_argument(
        "--initial-power-state", default="On", choices=("On", "Off"),
    )
    args = parser.parse_args()
    server = BmcServer((args.host, args.port), BmcHandler)
    server.username = args.username
    server.password = args.password
    server.system_id = args.system_id
    server.lock = threading.Lock()
    server.power_state = args.initial_power_state
    server.reset_actions_received = 0
    server.unauthorized_requests = 0
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(certfile=args.tls_cert, keyfile=args.tls_key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    print(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "status": "ready",
                "system_id": args.system_id,
                "initial_power_state": args.initial_power_state,
                "redfish_version": "1.9.0",
            },
            sort_keys=True,
        ),
        flush=True,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
