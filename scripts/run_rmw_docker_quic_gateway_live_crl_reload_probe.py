#!/usr/bin/env python3
"""Prove the aioquic QUIC gateway reloads its client CRL live, per connection.

Unlike the pinned ngtcp2/GnuTLS gateway path (see
run_rmw_docker_ngtcp2_public_online_crl_refresh_probe.py), the aioquic
Python gateway used to load its client CRL exactly once at process
startup: a certificate revoked afterward stayed accepted until the
service was restarted. This probe starts the gateway with an
unrevoked client certificate, connects successfully, revokes that same
certificate's serial by overwriting the on-disk CRL file, and connects
again with the same certificate against the still-running gateway --
the second connection must be rejected as revoked.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time
from typing import Any

try:
    from scripts.run_rmw_docker_quic_stateful_gateway_probe import (
        DEFAULT_IMAGE,
        SERVICE_SCHEMA_VERSION,
        json_rows,
        run,
        wait_service_ready,
    )
except ModuleNotFoundError:
    from run_rmw_docker_quic_stateful_gateway_probe import (
        DEFAULT_IMAGE,
        SERVICE_SCHEMA_VERSION,
        json_rows,
        run,
        wait_service_ready,
    )


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "fleetrmw.docker_quic_gateway_live_crl_reload_probe.v1"
PROBE_SCHEMA_VERSION = "fleetrmw.quic_mtls_probe.v1"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--keep-temp", action="store_true")
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_quic_gateway_live_crl_reload_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = run_probe(root=ROOT, image=args.image, keep_temp=args.keep_temp)
    summary_path = ROOT / args.summary_json
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print("fleetrmw-quic-gateway-live-crl-reload-probe")
        print(f"  status: {summary['status']}")
        print(f"  before_revoke_accepted: {summary.get('before_revoke_accepted')}")
        print(f"  after_revoke_rejected: {summary.get('after_revoke_rejected')}")
        print(f"  reload_failures: {summary.get('reload_failures')}")
    return 0 if summary["status"] == "ok" else 1


def certificate_command(certs: Path, root: Path) -> str:
    prefix = f"/work/{certs.relative_to(root)}"
    empty_crl_python = (
        "from cryptography import x509; "
        "from cryptography.hazmat.primitives import hashes,serialization; "
        "from datetime import datetime,timedelta; from pathlib import Path; "
        f"p=Path('{prefix}'); now=datetime.utcnow(); "
        "ca=x509.load_pem_x509_certificate((p/'client-ca.crt').read_bytes()); "
        "key=serialization.load_pem_private_key((p/'client-ca.key').read_bytes(),None); "
        "crl=x509.CertificateRevocationListBuilder().issuer_name(ca.subject)."
        "last_update(now).next_update(now+timedelta(days=1))."
        "sign(key,hashes.SHA256()); "
        "(p/'client.crl.pem').write_bytes(crl.public_bytes(serialization.Encoding.PEM))"
    )
    return (
        "openssl req -x509 -newkey rsa:2048 -nodes "
        f"-keyout {prefix}/server-ca.key -out {prefix}/server-ca.crt "
        "-subj /CN=FleetQoX-mTLS-Server-CA "
        "-addext basicConstraints=critical,CA:TRUE "
        "-addext keyUsage=critical,keyCertSign,cRLSign -days 1 >/dev/null 2>&1 && "
        "openssl req -new -newkey rsa:2048 -nodes "
        f"-keyout {prefix}/server.key -out {prefix}/server.csr "
        "-subj /CN=localhost "
        "-addext subjectAltName=DNS:localhost,DNS:fleetqox-crl-reload-gateway "
        "-addext extendedKeyUsage=serverAuth >/dev/null 2>&1 && "
        f"openssl x509 -req -in {prefix}/server.csr -CA {prefix}/server-ca.crt "
        f"-CAkey {prefix}/server-ca.key -CAcreateserial -out {prefix}/server.crt "
        "-days 1 -copy_extensions copy >/dev/null 2>&1 && "
        "openssl req -x509 -newkey rsa:2048 -nodes "
        f"-keyout {prefix}/client-ca.key -out {prefix}/client-ca.crt "
        "-subj /CN=FleetQoX-mTLS-Client-CA "
        "-addext basicConstraints=critical,CA:TRUE "
        "-addext keyUsage=critical,keyCertSign,cRLSign -days 1 >/dev/null 2>&1 && "
        # CN/SAN must match "mtls-publisher", the publisher identity
        # hardcoded in fleetrmw_quic_mtls_probe's DataFrame -- the gateway's
        # publisher-identity authorization check compares against that
        # exact string regardless of which cert file is configured.
        "openssl req -new -newkey rsa:2048 -nodes "
        f"-keyout {prefix}/client.key -out {prefix}/client.csr "
        "-subj /CN=mtls-publisher "
        "-addext extendedKeyUsage=clientAuth "
        "-addext subjectAltName=URI:spiffe://fleetqox/publishers/mtls-publisher "
        ">/dev/null 2>&1 && "
        f"openssl x509 -req -in {prefix}/client.csr -CA {prefix}/client-ca.crt "
        f"-CAkey {prefix}/client-ca.key -CAcreateserial -out {prefix}/client.crt "
        "-days 1 -copy_extensions copy >/dev/null 2>&1 && "
        f"python3 -c {shlex.quote(empty_crl_python)}"
    )


def revoke_client_certificate_command(certs: Path, root: Path) -> str:
    prefix = f"/work/{certs.relative_to(root)}"
    revoke_python = (
        "from cryptography import x509; "
        "from cryptography.hazmat.primitives import hashes,serialization; "
        "from datetime import datetime,timedelta; from pathlib import Path; "
        f"p=Path('{prefix}'); now=datetime.utcnow(); "
        "ca=x509.load_pem_x509_certificate((p/'client-ca.crt').read_bytes()); "
        "key=serialization.load_pem_private_key((p/'client-ca.key').read_bytes(),None); "
        "peer=x509.load_pem_x509_certificate((p/'client.crt').read_bytes()); "
        "entry=x509.RevokedCertificateBuilder().serial_number(peer.serial_number)."
        "revocation_date(now).build(); "
        "crl=x509.CertificateRevocationListBuilder().issuer_name(ca.subject)."
        "last_update(now).next_update(now+timedelta(days=1))."
        "add_revoked_certificate(entry).sign(key,hashes.SHA256()); "
        "(p/'client.crl.pem').write_bytes(crl.public_bytes(serialization.Encoding.PEM))"
    )
    return f"python3 -c {shlex.quote(revoke_python)}"


def run_client(
    *,
    root: Path,
    image: str,
    network: str,
    name: str,
    install: str,
    certs: Path,
    qlogs: Path,
    expect_success: bool,
) -> subprocess.CompletedProcess[str]:
    uri = (
        "https://localhost:4498/fleetrmw/v1/frames?"
        # Topic matches the hardcoded DataFrame topic in
        # fleetrmw_quic_mtls_probe ("/fleetqox/mtls"), alongside its
        # hardcoded "mtls-publisher" identity above.
        "domain_id=42&topic=%2Ffleetqox%2Fmtls&consumer_id=crl-reload-probe"
    )
    command = (
        "source /opt/ros/jazzy/setup.bash && "
        f"source {install}/setup.bash && "
        "export FLEETQOX_RMW_REMOTE_TRANSPORT=quic_gateway && "
        "export FLEETQOX_RMW_QUIC_BACKEND=inprocess && "
        "export FLEETQOX_RMW_QUIC_GATEWAY=fleetqox-crl-reload-gateway:4498 && "
        f"export FLEETQOX_RMW_QUIC_URI='{uri}' && "
        "export FLEETQOX_RMW_QUIC_SNI=localhost && "
        "export FLEETQOX_RMW_QUIC_TIMEOUT=8s && "
        f"export FLEETQOX_RMW_QUIC_CA_FILE=/work/{(certs / 'server-ca.crt').relative_to(root)} && "
        f"export FLEETQOX_RMW_QUIC_QLOG_DIR=/work/{qlogs.relative_to(root)} && "
        f"export FLEETQOX_RMW_QUIC_MTLS_EXPECT_SUCCESS={'1' if expect_success else '0'} && "
        "export FLEETQOX_RMW_QUIC_MTLS_EXPECT_AUTHORIZATION_FAILURE=0 && "
        f"export FLEETQOX_RMW_QUIC_CLIENT_CERT_FILE=/work/{(certs / 'client.crt').relative_to(root)} && "
        f"export FLEETQOX_RMW_QUIC_CLIENT_KEY_FILE=/work/{(certs / 'client.key').relative_to(root)} && "
        f"{install}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/fleetrmw_quic_mtls_probe"
    )
    return run(
        [
            "docker", "run", "--rm",
            "--name", name,
            "--network", network,
            "--entrypoint", "bash",
            "-v", f"{root}:/work",
            "-w", "/work",
            image,
            "-lc", command,
        ]
    )


def run_probe(*, root: Path, image: str, keep_temp: bool) -> dict[str, Any]:
    temp_root = root / f".tmp_fleetrmw_quic_crl_reload_{os.getpid()}"
    certs = temp_root / "certs"
    certs.mkdir(parents=True, exist_ok=True)
    before_qlogs = temp_root / "before-qlogs"
    after_qlogs = temp_root / "after-qlogs"
    service_qlogs = temp_root / "service-qlogs"
    for directory in (before_qlogs, after_qlogs, service_qlogs):
        directory.mkdir(parents=True, exist_ok=True)
    build_root = "/work/.tmp_fleetrmw_quic_crl_reload_build"
    install = "/work/.tmp_fleetrmw_quic_crl_reload_install"
    log_root = "/work/.tmp_fleetrmw_quic_crl_reload_log"

    cert_result = run(
        [
            "docker", "run", "--rm", "--entrypoint", "bash",
            "-v", f"{root}:/work", "-w", "/work",
            image, "-lc", certificate_command(certs, root),
        ]
    )
    build = run(
        [
            "docker", "run", "--rm", "--entrypoint", "bash",
            "-v", f"{root}:/work", "-w", "/work",
            image, "-lc",
            "source /opt/ros/jazzy/setup.bash && "
            f"rm -rf {build_root} {install} {log_root} && "
            f"colcon --log-base {log_root} build --base-paths ros2_ws/src "
            "--packages-select rmw_fleetqox_cpp "
            f"--build-base {build_root} --install-base {install} "
            "--cmake-args -DCMAKE_BUILD_TYPE=Release",
        ]
    )
    network = f"fleetrmw-crl-reload-net-{os.getpid()}"
    network_result = run(["docker", "network", "create", network])

    service_name = f"fleetrmw-crl-reload-service-{os.getpid()}"
    before = subprocess.CompletedProcess([], 1, "", "not_run")
    after = subprocess.CompletedProcess([], 1, "", "not_run")
    service_logs = ""
    revoke_result = subprocess.CompletedProcess([], 1, "", "not_run")
    try:
        if cert_result.returncode == 0 and build.returncode == 0 and network_result.returncode == 0:
            service_command = (
                "exec python3 scripts/fleetrmw_quic_gateway_service.py "
                "--host 0.0.0.0 --port 4498 "
                f"--certificate /work/{(certs / 'server.crt').relative_to(root)} "
                f"--private-key /work/{(certs / 'server.key').relative_to(root)} "
                f"--client-ca /work/{(certs / 'client-ca.crt').relative_to(root)} "
                f"--client-crl /work/{(certs / 'client.crl.pem').relative_to(root)} "
                "--require-client-certificate "
                "--publisher-identity-uri-prefix spiffe://fleetqox/publishers/ "
                f"--qlog-dir /work/{service_qlogs.relative_to(root)} "
                "--max-frames-per-topic 8 --max-frame-bytes 65536"
            )
            service_start = run(
                [
                    "docker", "run", "-d",
                    "--name", service_name,
                    "--network", network,
                    "--network-alias", "fleetqox-crl-reload-gateway",
                    "--entrypoint", "bash",
                    "-v", f"{root}:/work", "-w", "/work",
                    image, "-lc", service_command,
                ]
            )
            if service_start.returncode == 0 and wait_service_ready(service_name):
                before = run_client(
                    root=root, image=image, network=network,
                    name=f"fleetrmw-crl-reload-before-{os.getpid()}",
                    install=install, certs=certs, qlogs=before_qlogs,
                    expect_success=True,
                )
                revoke_result = run(
                    [
                        "docker", "run", "--rm", "--entrypoint", "bash",
                        "-v", f"{root}:/work", "-w", "/work",
                        image, "-lc", revoke_client_certificate_command(certs, root),
                    ]
                )
                after = run_client(
                    root=root, image=image, network=network,
                    name=f"fleetrmw-crl-reload-after-{os.getpid()}",
                    install=install, certs=certs, qlogs=after_qlogs,
                    expect_success=False,
                )
            time.sleep(0.5)
            run(["docker", "stop", "--time", "3", service_name])
            service_logs = run(["docker", "logs", service_name]).stdout
    finally:
        run(["docker", "rm", "-f", service_name])
        run(["docker", "network", "rm", network])
        if not keep_temp:
            cleanup = run(
                [
                    "docker", "run", "--rm", "--entrypoint", "bash",
                    "-v", f"{root}:/work", image, "-lc",
                    f"rm -rf {build_root} {install} {log_root}",
                ]
            )
            if cleanup.returncode == 0:
                shutil.rmtree(temp_root, ignore_errors=True)

    def last_row(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
        rows = json_rows(result.stdout)
        return rows[-1] if rows else {}

    before_row = last_row(before)
    after_row = last_row(after)
    service_rows = json_rows(service_logs)
    service_row = service_rows[-1] if service_rows else {}
    transport = service_row.get("transport_metrics", {})

    before_revoke_accepted = (
        before_row.get("schema_version") == PROBE_SCHEMA_VERSION
        and before_row.get("status") == "ok"
        and before_row.get("expected_success") is True
        and before_row.get("send_success") is True
    )
    after_revoke_rejected = (
        after_row.get("schema_version") == PROBE_SCHEMA_VERSION
        and after_row.get("status") == "ok"
        and after_row.get("expected_success") is False
        and after_row.get("send_success") is False
    )
    reload_failures = transport.get("client_crl_reload_failures", -1)
    status = (
        "ok"
        if cert_result.returncode == 0
        and build.returncode == 0
        and network_result.returncode == 0
        and revoke_result.returncode == 0
        and before_revoke_accepted
        and after_revoke_rejected
        and reload_failures == 0
        and transport.get("client_certificates_accepted", 0) >= 1
        and transport.get("revoked_client_certificates_rejected", 0) >= 1
        and service_row.get("status") == "stopped"
        and service_row.get("clean_teardown") is True
        else "failed"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "before_revoke_accepted": before_revoke_accepted,
        "after_revoke_rejected": after_revoke_rejected,
        "reload_failures": reload_failures,
        "cert_returncode": cert_result.returncode,
        "build_returncode": build.returncode,
        "network_returncode": network_result.returncode,
        "revoke_returncode": revoke_result.returncode,
        "before_row": before_row,
        "after_row": after_row,
        "service_row": service_row,
        "service_logs": "" if status == "ok" else service_logs,
        "before_stdout": "" if status == "ok" else before.stdout,
        "after_stdout": "" if status == "ok" else after.stdout,
    }


if __name__ == "__main__":
    raise SystemExit(main())
