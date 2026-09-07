#!/usr/bin/env python3
"""Prove online server-certificate rotation for the public ngtcp2 gateway.

Unlike client-CA rotation (see
run_rmw_docker_ngtcp2_public_online_client_ca_rotation_probe.py), the
server's own certificate/key were never re-read after process startup by
any existing reload path: TLSServerContext::init() loads them once into a
long-lived credentials object that every future connection reuses
unchanged. Rotating them needed a genuinely new hook, not reuse of the
existing one -- the server sends its own certificate to the client before
the point in the handshake where the client-CA/CRL reload runs, so that
mechanism cannot affect it. server-certificate-rotation.patch adds a
second, independent reload in TLSServerSession::init() (which runs once
per new connection, before any handshake message is sent) that rebuilds
credentials from FLEETQOX_GNUTLS_SERVER_CERT/_KEY plus the client CA/CRL
this object must also carry.

This probe proves it end to end: a client that trusts CA-A connects while
the server presents a CA-A-signed certificate (succeeds), a client that
trusts CA-B is rejected (untrusted issuer), the server's certificate files
are swapped to a CA-B-signed pair, and now the CA-A truster is rejected
while the CA-B truster succeeds -- a genuine rotation of what the server
presents, not just an additional trust relationship -- then the original
certificate is restored and the CA-A truster succeeds again.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.fleetqox_public_quic_backend_delay_proxy import (
    SCHEMA_VERSION as PROXY_SCHEMA_VERSION,
)
from scripts.run_rmw_docker_ngtcp2_public_async_backend_probe import (
    DEFAULT_BASE_IMAGE,
    DEFAULT_SERVER_IMAGE,
    load_json,
    response_has_status,
    run,
    stop_server,
    wait_for_log,
)
from scripts.run_rmw_docker_ngtcp2_public_identity_fairness_probe import (
    start_server,
    wait_client_ready,
)
from scripts.run_rmw_docker_ngtcp2_public_stateful_gateway_probe import (
    BACKEND_SCHEMA_VERSION,
    stateful_certificate_command,
)


SCHEMA_VERSION = "fleetrmw.docker_ngtcp2_public_online_server_certificate_rotation.v1"


def rotation_certificate_command(certs: Path, root: Path) -> str:
    prefix = f"/work/{certs.relative_to(root)}"
    return (
        stateful_certificate_command(certs, root)
        + f" && cp {prefix}/server.crt {prefix}/original-server.crt"
        + f" && cp {prefix}/server.key {prefix}/original-server.key"
        # A second, independent server identity CA -- server-ca (CA-A) is
        # already trusted by nothing outside this probe's own clients, so
        # CA-B just needs to be a distinct issuer with a leaf cert bearing
        # the same DNS SAN the gateway is dialed by.
        + " && openssl req -x509 -newkey rsa:2048 -nodes "
        f"-keyout {prefix}/server-ca-b.key -out {prefix}/server-ca-b.crt "
        "-subj /CN=FleetQoX-Rotated-Server-CA "
        "-addext basicConstraints=critical,CA:TRUE "
        "-addext keyUsage=critical,keyCertSign,cRLSign -days 1 >/dev/null 2>&1 && "
        "openssl req -new -newkey rsa:2048 -nodes "
        f"-keyout {prefix}/server-b.key -out {prefix}/server-b.csr "
        "-subj /CN=fleetqox-mtls-gateway "
        "-addext subjectAltName=DNS:fleetqox-mtls-gateway "
        "-addext extendedKeyUsage=serverAuth >/dev/null 2>&1 && "
        f"openssl x509 -req -in {prefix}/server-b.csr -CA {prefix}/server-ca-b.crt "
        f"-CAkey {prefix}/server-ca-b.key -CAcreateserial -out {prefix}/server-b.crt "
        "-days 1 -copy_extensions copy >/dev/null 2>&1"
    )


def swap_file(
    container: str, *, root: Path, certs: Path, target_name: str, source_name: str,
) -> bool:
    cert_root = f"/work/{certs.relative_to(root)}"
    command = (
        f"cp {shlex.quote(cert_root + '/' + source_name)} "
        f"{shlex.quote(cert_root + '/' + target_name + '.next')} && "
        f"mv -f {shlex.quote(cert_root + '/' + target_name + '.next')} "
        f"{shlex.quote(cert_root + '/' + target_name)}"
    )
    result = run(["docker", "exec", container, "bash", "-lc", command], timeout=15.0)
    return result.returncode == 0


def rotate_to_cert_b(container: str, *, root: Path, certs: Path) -> bool:
    return swap_file(
        container, root=root, certs=certs,
        target_name="server.crt", source_name="server-b.crt",
    ) and swap_file(
        container, root=root, certs=certs,
        target_name="server.key", source_name="server-b.key",
    )


def restore_cert_a(container: str, *, root: Path, certs: Path) -> bool:
    return swap_file(
        container, root=root, certs=certs,
        target_name="server.crt", source_name="original-server.crt",
    ) and swap_file(
        container, root=root, certs=certs,
        target_name="server.key", source_name="original-server.key",
    )


def start_trusting_client(
    *, root: Path, image: str, network: str, name: str, certs: Path, ca_name: str,
) -> subprocess.CompletedProcess[str]:
    cert_root = f"/work/{certs.relative_to(root)}"
    command = (
        f"cp {cert_root}/{ca_name}.crt "
        "/usr/local/share/ca-certificates/fleetqox-rotation-ca.crt && "
        "update-ca-certificates >/dev/null 2>&1 && "
        "tc qdisc replace dev eth0 root netem delay 9ms 2ms && "
        "touch /tmp/fleetqox-client-ready && exec sleep infinity"
    )
    return run(
        [
            "docker", "run", "-d", "--name", name, "--network", network,
            "--cap-add", "NET_ADMIN", "--entrypoint", "bash",
            "-v", f"{root}:/work", "-w", "/work",
            image, "-lc", command,
        ],
        timeout=30.0,
    )


def run_verifying_client(
    *, root: Path, container: str, certs: Path, certificate_name: str,
    consumer_id: str, qlog: Path,
) -> subprocess.CompletedProcess[str]:
    cert_root = f"/work/{certs.relative_to(root)}"
    qlog_path = f"/work/{qlog.relative_to(root)}"
    uri = (
        "https://fleetqox-mtls-gateway:4433/fleetrmw/v1/frames?"
        "domain_id=42&topic=%2Ffleetqox%2Fscrot&consumer_id="
        f"{consumer_id}"
    )
    command = (
        "fleetqox-public-mtls-client fleetqox-mtls-gateway 4433 "
        f"{shlex.quote(uri)} "
        f"--key={cert_root}/{certificate_name}.key "
        f"--cert={cert_root}/{certificate_name}.crt "
        "--verify-server-cert "
        "--disable-early-data --exit-on-all-streams-close "
        "--no-quic-dump --no-http-dump "
        f"--qlog-file={qlog_path}"
    )
    return run(
        ["docker", "exec", container, "bash", "-lc", command], timeout=15.0
    )


def server_cert_rejected(result: subprocess.CompletedProcess[str]) -> bool:
    return "FLEETQOX_PUBLIC_MTLS_CLIENT_SERVER_CERT_REJECTED" in result.stderr


def server_instance(container: str) -> str:
    result = run(
        [
            "docker", "exec", container, "bash", "-lc",
            (
                'pid="$(cat /tmp/fleetqox-public-server.pid)" && '
                'printf "%s " "$pid" && cut -d " " -f 22 "/proc/$pid/stat"'
            ),
        ],
        timeout=10.0,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def run_iteration(
    *, root: Path, image: str, network: str, certs: Path, temp_root: Path, index: int,
) -> dict[str, Any]:
    suffix = f"{os.getpid()}-{index}"
    server_name = f"fq-public-scrot-server-{suffix}"
    client_a_name = f"fq-public-scrot-client-a-{suffix}"
    client_b_name = f"fq-public-scrot-client-b-{suffix}"
    run_root = temp_root / f"run-{index}"
    qlogs = run_root / "client-qlogs"
    qlogs.mkdir(parents=True, exist_ok=True)
    cert_root = f"/work/{certs.relative_to(root)}"

    start, backend_path, proxy_path = start_server(
        root=root, image=image, network=network, name=server_name, certs=certs,
        run_root=run_root,
        extra_environment={
            "FLEETQOX_GNUTLS_RELOAD_SERVER_CERTIFICATE_EACH_HANDSHAKE": "1",
            "FLEETQOX_GNUTLS_SERVER_CERT": f"{cert_root}/server.crt",
            "FLEETQOX_GNUTLS_SERVER_KEY": f"{cert_root}/server.key",
        },
    )
    server_ready = (
        start.returncode == 0
        and wait_for_log(
            server_name,
            (
                "FLEETQOX_STATE_BACKEND_ASYNC_READY workers=1 "
                "queue_capacity=4 per_identity_queue_capacity=2"
            ),
        )
        and wait_for_log(server_name, PROXY_SCHEMA_VERSION)
    )
    client_a_start = start_trusting_client(
        root=root, image=image, network=network, name=client_a_name,
        certs=certs, ca_name="server-ca",
    )
    client_b_start = start_trusting_client(
        root=root, image=image, network=network, name=client_b_name,
        certs=certs, ca_name="server-ca-b",
    )
    clients_ready = (
        client_a_start.returncode == 0 and wait_client_ready(client_a_name)
        and client_b_start.returncode == 0 and wait_client_ready(client_b_name)
    )

    not_started = subprocess.CompletedProcess([], 1, "", "not_started")
    before_a = not_started
    before_b = not_started
    after_a = not_started
    after_b = not_started
    restored_a = not_started
    rotated_installed = False
    restore_installed = False
    instance_before = ""
    instance_after = ""
    server_exit = -1
    logs = ""
    try:
        if server_ready and clients_ready:
            instance_before = server_instance(server_name)
            before_a = run_verifying_client(
                root=root, container=client_a_name, certs=certs,
                certificate_name="stateful-client", consumer_id="scrot-before-a",
                qlog=qlogs / "before-a.qlog",
            )
            before_b = run_verifying_client(
                root=root, container=client_b_name, certs=certs,
                certificate_name="stateful-client", consumer_id="scrot-before-b",
                qlog=qlogs / "before-b.qlog",
            )
            rotated_installed = rotate_to_cert_b(client_a_name, root=root, certs=certs)
            rotated_installed = rotated_installed and wait_for_log(
                server_name, "FLEETQOX_PUBLIC_MTLS_SERVER_CERT_RELOADED", count=1
            )
            after_a = run_verifying_client(
                root=root, container=client_a_name, certs=certs,
                certificate_name="stateful-client", consumer_id="scrot-after-a",
                qlog=qlogs / "after-a.qlog",
            )
            after_b = run_verifying_client(
                root=root, container=client_b_name, certs=certs,
                certificate_name="stateful-client", consumer_id="scrot-after-b",
                qlog=qlogs / "after-b.qlog",
            )
            restore_installed = restore_cert_a(client_a_name, root=root, certs=certs)
            restore_installed = restore_installed and wait_for_log(
                server_name, "FLEETQOX_PUBLIC_MTLS_SERVER_CERT_RELOADED", count=2
            )
            restored_a = run_verifying_client(
                root=root, container=client_a_name, certs=certs,
                certificate_name="stateful-client", consumer_id="scrot-restored-a",
                qlog=qlogs / "restored-a.qlog",
            )
            instance_after = server_instance(server_name)
        if server_ready:
            server_exit, logs = stop_server(server_name)
    finally:
        if not restore_installed:
            restore_cert_a(client_a_name, root=root, certs=certs)
        run(["docker", "rm", "-f", client_a_name], timeout=20.0)
        run(["docker", "rm", "-f", client_b_name], timeout=20.0)
        run(["docker", "rm", "-f", server_name], timeout=20.0)

    backend = load_json(backend_path)
    proxy = load_json(proxy_path)
    state = backend.get("metrics", {}).get("state", {})
    qlog_files = list(qlogs.glob("*.qlog"))
    before_b_rejected = server_cert_rejected(before_b)
    after_a_rejected = server_cert_rejected(after_a)
    same_server_instance = bool(instance_before) and instance_before == instance_after
    reload_success_count = logs.count("FLEETQOX_PUBLIC_MTLS_SERVER_CERT_RELOADED")
    verified_count = logs.count("FLEETQOX_PUBLIC_MTLS_VERIFIED")
    ok = (
        server_ready
        and clients_ready
        and rotated_installed
        and restore_installed
        and response_has_status(before_a, 204)
        and before_b_rejected
        and after_a_rejected
        and response_has_status(after_b, 204)
        and response_has_status(restored_a, 204)
        and same_server_instance
        and server_exit == 0
        and reload_success_count == 5
        and verified_count == 3
        and backend.get("schema_version") == BACKEND_SCHEMA_VERSION
        and backend.get("clean_teardown") is True
        and state.get("requests_total") == 3
        and proxy.get("schema_version") == PROXY_SCHEMA_VERSION
        and proxy.get("clean_teardown") is True
        and proxy.get("requests_total") == 3
        and proxy.get("failures") == 0
        and len(qlog_files) == 5
        and all(path.stat().st_size > 0 for path in qlog_files)
    )
    return {
        "index": index,
        "status": "ok" if ok else "failed",
        "server_ready": server_ready,
        "clients_ready": clients_ready,
        "server_exit_code": server_exit,
        "same_server_instance": same_server_instance,
        "before_ca_a_http_204": response_has_status(before_a, 204),
        "before_ca_b_rejected": before_b_rejected,
        "after_ca_a_rejected": after_a_rejected,
        "after_ca_b_http_204": response_has_status(after_b, 204),
        "restored_ca_a_http_204": response_has_status(restored_a, 204),
        "reload_success_count": reload_success_count,
        "verified_client_count": verified_count,
        "backend": backend,
        "proxy": proxy,
        "client_qlog_file_count": len(qlog_files),
        "captured_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "before_a_stderr": "" if ok else before_a.stderr,
        "before_b_stderr": "" if ok else before_b.stderr,
        "after_a_stderr": "" if ok else after_a.stderr,
        "after_b_stderr": "" if ok else after_b.stderr,
        "restored_a_stderr": "" if ok else restored_a.stderr,
        "server_logs": "" if ok else logs,
    }


def run_probe(
    *, root: Path, base_image: str, server_image: str, iterations: int,
    skip_server_build: bool, keep_temp: bool,
) -> dict[str, Any]:
    build = subprocess.CompletedProcess([], 0, "", "")
    if not skip_server_build:
        build = run(
            [
                "docker", "build", "--build-arg", f"BASE_IMAGE={base_image}",
                "-f", "external/ngtcp2-public-mtls/Dockerfile",
                "-t", server_image, ".",
            ],
            timeout=600.0,
        )
    temp_root = root / f".tmp_fleetrmw_public_scrot_{os.getpid()}"
    certs = temp_root / "certs"
    certs.mkdir(parents=True, exist_ok=True)
    certificate = run(
        [
            "docker", "run", "--rm", "--entrypoint", "bash",
            "-v", f"{root}:/work", "-w", "/work",
            base_image, "-lc", rotation_certificate_command(certs, root),
        ],
        timeout=180.0,
    )
    network = f"fq-public-scrot-net-{os.getpid()}"
    network_result = run(["docker", "network", "create", network], timeout=20.0)
    rows: list[dict[str, Any]] = []
    try:
        if all(r.returncode == 0 for r in (build, certificate, network_result)):
            for index in range(max(1, iterations)):
                rows.append(
                    run_iteration(
                        root=root, image=server_image, network=network,
                        certs=certs, temp_root=temp_root, index=index,
                    )
                )
    finally:
        run(["docker", "network", "rm", network], timeout=20.0)
        if not keep_temp:
            shutil.rmtree(temp_root, ignore_errors=True)
    run_count = max(1, iterations)
    ok_count = sum(row.get("status") == "ok" for row in rows)
    ok = (
        all(r.returncode == 0 for r in (build, certificate, network_result))
        and len(rows) == run_count
        and ok_count == run_count
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if ok else "failed",
        "run_count": run_count,
        "ok_run_count": ok_count,
        "online_server_certificate_rotation_claim": ok,
        "server_build_returncode": build.returncode,
        "certificate_returncode": certificate.returncode,
        "network_returncode": network_result.returncode,
        "runs": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-image", default=DEFAULT_BASE_IMAGE)
    parser.add_argument("--server-image", default=DEFAULT_SERVER_IMAGE)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--skip-server-build", action="store_true")
    parser.add_argument("--keep-temp", action="store_true")
    parser.add_argument(
        "--summary-json",
        default=(
            "results_rmw_socket/"
            "docker_ngtcp2_public_online_server_certificate_rotation_summary.json"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_probe(
        root=ROOT, base_image=args.base_image, server_image=args.server_image,
        iterations=args.iterations, skip_server_build=args.skip_server_build,
        keep_temp=args.keep_temp,
    )
    output = ROOT / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(f"status={summary['status']}")
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
