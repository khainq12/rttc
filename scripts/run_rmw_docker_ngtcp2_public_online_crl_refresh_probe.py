#!/usr/bin/env python3
"""Prove online client-CRL refresh for new public ngtcp2 connections."""

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
    run_exec_client,
    start_client_container,
    start_server,
    wait_client_ready,
)
from scripts.run_rmw_docker_ngtcp2_public_mtls_server_probe import (
    negative_client_was_rejected,
)
from scripts.run_rmw_docker_ngtcp2_public_stateful_gateway_probe import (
    BACKEND_SCHEMA_VERSION,
    stateful_certificate_command,
)


SCHEMA_VERSION = "fleetrmw.docker_ngtcp2_public_online_crl_refresh.v1"


def online_crl_certificate_command(certs: Path, root: Path) -> str:
    prefix = f"/work/{certs.relative_to(root)}"
    crl_python = (
        "from cryptography import x509; "
        "from cryptography.hazmat.primitives import hashes,serialization; "
        "from datetime import datetime,timedelta; from pathlib import Path; "
        f"p=Path('{prefix}'); now=datetime.utcnow(); "
        "ca=x509.load_pem_x509_certificate((p/'client-ca.crt').read_bytes()); "
        "key=serialization.load_pem_private_key((p/'client-ca.key').read_bytes(),None); "
        "builder=x509.CertificateRevocationListBuilder().issuer_name(ca.subject)."
        "last_update(now).next_update(now+timedelta(days=1)); "
        "peers=[x509.load_pem_x509_certificate((p/name).read_bytes()) "
        "for name in ('revoked-client.crt','stateful-client.crt')]; "
        "entries=[x509.RevokedCertificateBuilder().serial_number(peer.serial_number)."
        "revocation_date(now).build() for peer in peers]; "
        "builder=builder.add_revoked_certificate(entries[0])."
        "add_revoked_certificate(entries[1]); "
        "crl=builder.sign(key,hashes.SHA256()); "
        "(p/'stateful-revoked.crl.pem').write_bytes("
        "crl.public_bytes(serialization.Encoding.PEM))"
    )
    return (
        stateful_certificate_command(certs, root)
        + f" && cp {prefix}/client.crl.pem {prefix}/initial-client.crl.pem"
        + f" && python3 -c {shlex.quote(crl_python)}"
        + f" && openssl rand -out {prefix}/invalid-client.crl.pem 32"
    )


def replace_live_crl(
    container: str,
    *,
    root: Path,
    certs: Path,
    source_name: str,
) -> bool:
    cert_root = f"/work/{certs.relative_to(root)}"
    command = (
        f"cp {shlex.quote(cert_root + '/' + source_name)} "
        f"{shlex.quote(cert_root + '/client.crl.next')} && "
        f"mv -f {shlex.quote(cert_root + '/client.crl.next')} "
        f"{shlex.quote(cert_root + '/client.crl.pem')}"
    )
    result = run(
        ["docker", "exec", container, "bash", "-lc", command],
        timeout=15.0,
    )
    return result.returncode == 0


def server_instance(container: str) -> str:
    result = run(
        [
            "docker",
            "exec",
            container,
            "bash",
            "-lc",
            (
                'pid="$(cat /tmp/fleetqox-public-server.pid)" && '
                'printf "%s " "$pid" && cut -d " " -f 22 "/proc/$pid/stat"'
            ),
        ],
        timeout=10.0,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def run_iteration(
    *,
    root: Path,
    image: str,
    network: str,
    certs: Path,
    temp_root: Path,
    index: int,
) -> dict[str, Any]:
    suffix = f"{os.getpid()}-{index}"
    server_name = f"fq-public-crl-server-{suffix}"
    client_name = f"fq-public-crl-client-{suffix}"
    run_root = temp_root / f"run-{index}"
    qlogs = run_root / "client-qlogs"
    qlogs.mkdir(parents=True, exist_ok=True)

    start, backend_path, proxy_path = start_server(
        root=root,
        image=image,
        network=network,
        name=server_name,
        certs=certs,
        run_root=run_root,
        extra_environment={
            "FLEETQOX_GNUTLS_RELOAD_CLIENT_CRL_EACH_HANDSHAKE": "1",
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
    client_start = start_client_container(
        root=root,
        image=image,
        network=network,
        name=client_name,
        certs=certs,
    )
    client_ready = client_start.returncode == 0 and wait_client_ready(client_name)

    not_started = subprocess.CompletedProcess([], 1, "", "not_started")
    initial = not_started
    revoked = not_started
    restored = not_started
    malformed = not_started
    revoked_installed = False
    initial_restored = False
    malformed_installed = False
    final_restore = False
    instance_before = ""
    instance_after = ""
    server_exit = -1
    logs = ""
    try:
        if server_ready and client_ready:
            instance_before = server_instance(server_name)
            initial = run_exec_client(
                root=root,
                container=client_name,
                certs=certs,
                certificate_name="stateful-client",
                consumer_id="crl-initial",
                qlog=qlogs / "initial.qlog",
            )
            revoked_installed = replace_live_crl(
                client_name,
                root=root,
                certs=certs,
                source_name="stateful-revoked.crl.pem",
            )
            revoked = run_exec_client(
                root=root,
                container=client_name,
                certs=certs,
                certificate_name="stateful-client",
                consumer_id="crl-revoked",
                qlog=qlogs / "revoked.qlog",
            )
            initial_restored = replace_live_crl(
                client_name,
                root=root,
                certs=certs,
                source_name="initial-client.crl.pem",
            )
            restored = run_exec_client(
                root=root,
                container=client_name,
                certs=certs,
                certificate_name="stateful-client",
                consumer_id="crl-restored",
                qlog=qlogs / "restored.qlog",
            )
            malformed_installed = replace_live_crl(
                client_name,
                root=root,
                certs=certs,
                source_name="invalid-client.crl.pem",
            )
            malformed = run_exec_client(
                root=root,
                container=client_name,
                certs=certs,
                certificate_name="stateful-client",
                consumer_id="crl-malformed",
                qlog=qlogs / "malformed.qlog",
            )
            instance_after = server_instance(server_name)
            final_restore = replace_live_crl(
                client_name,
                root=root,
                certs=certs,
                source_name="initial-client.crl.pem",
            )
        if server_ready:
            server_exit, logs = stop_server(server_name)
    finally:
        if not final_restore:
            replace_live_crl(
                client_name,
                root=root,
                certs=certs,
                source_name="initial-client.crl.pem",
            )
        run(["docker", "rm", "-f", client_name], timeout=20.0)
        run(["docker", "rm", "-f", server_name], timeout=20.0)

    backend = load_json(backend_path)
    proxy = load_json(proxy_path)
    state = backend.get("metrics", {}).get("state", {})
    qlog_files = list(qlogs.glob("*.qlog"))
    revoked_rejected = negative_client_was_rejected(revoked)
    malformed_rejected = negative_client_was_rejected(malformed)
    same_server_instance = bool(instance_before) and instance_before == instance_after
    reload_success_count = logs.count("FLEETQOX_PUBLIC_MTLS_CRL_RELOADED")
    reload_failure_count = logs.count(
        "FLEETQOX_PUBLIC_MTLS_CRL_RELOAD_FAILED"
    )
    verified_count = logs.count("FLEETQOX_PUBLIC_MTLS_VERIFIED")
    certificate_rejection_count = logs.count(
        "FLEETQOX_PUBLIC_MTLS_REJECT verify_result="
    )
    ok = (
        server_ready
        and client_ready
        and revoked_installed
        and initial_restored
        and malformed_installed
        and final_restore
        and response_has_status(initial, 204)
        and revoked_rejected
        and response_has_status(restored, 204)
        and malformed_rejected
        and same_server_instance
        and server_exit == 0
        and reload_success_count == 3
        and reload_failure_count == 1
        and verified_count == 2
        and certificate_rejection_count == 1
        and backend.get("schema_version") == BACKEND_SCHEMA_VERSION
        and backend.get("clean_teardown") is True
        and state.get("requests_total") == 2
        and state.get("get_requests") == 2
        and state.get("empty_takes") == 2
        and proxy.get("schema_version") == PROXY_SCHEMA_VERSION
        and proxy.get("clean_teardown") is True
        and proxy.get("requests_total") == 2
        and proxy.get("delayed_requests") == 0
        and proxy.get("forwarded_requests") == 2
        and proxy.get("failures") == 0
        and logs.count("FLEETQOX_STATE_BACKEND_ASYNC_QUEUED") == 2
        and logs.count("FLEETQOX_STATE_BACKEND_RESPONSE") == 2
        and len(qlog_files) == 4
        and all(path.stat().st_size > 0 for path in qlog_files)
    )
    return {
        "index": index,
        "status": "ok" if ok else "failed",
        "server_ready": server_ready,
        "client_ready": client_ready,
        "server_exit_code": server_exit,
        "server_instance_before": instance_before,
        "server_instance_after": instance_after,
        "same_server_instance": same_server_instance,
        "initial_http_204": response_has_status(initial, 204),
        "revoked_new_connection_rejected": revoked_rejected,
        "restored_http_204": response_has_status(restored, 204),
        "malformed_crl_new_connection_rejected": malformed_rejected,
        "crl_reload_success_count": reload_success_count,
        "crl_reload_failure_count": reload_failure_count,
        "verified_client_count": verified_count,
        "certificate_rejection_count": certificate_rejection_count,
        "backend": backend,
        "proxy": proxy,
        "client_qlog_file_count": len(qlog_files),
        "netem_server": "delay 11ms 2ms",
        "netem_client": "delay 9ms 2ms",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "initial_stderr": "" if ok else initial.stderr,
        "revoked_stderr": "" if ok else revoked.stderr,
        "restored_stderr": "" if ok else restored.stderr,
        "malformed_stderr": "" if ok else malformed.stderr,
        "server_logs": "" if ok else logs,
    }


def run_probe(
    *,
    root: Path,
    base_image: str,
    server_image: str,
    iterations: int,
    skip_server_build: bool,
    keep_temp: bool,
) -> dict[str, Any]:
    build = subprocess.CompletedProcess([], 0, "", "")
    if not skip_server_build:
        build = run(
            [
                "docker",
                "build",
                "--build-arg",
                f"BASE_IMAGE={base_image}",
                "-f",
                "external/ngtcp2-public-mtls/Dockerfile",
                "-t",
                server_image,
                ".",
            ],
            timeout=600.0,
        )
    temp_root = root / f".tmp_fleetrmw_public_online_crl_{os.getpid()}"
    certs = temp_root / "certs"
    certs.mkdir(parents=True, exist_ok=True)
    certificate = run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "bash",
            "-v",
            f"{root}:/work",
            "-w",
            "/work",
            base_image,
            "-lc",
            online_crl_certificate_command(certs, root),
        ],
        timeout=180.0,
    )
    network = f"fq-public-online-crl-net-{os.getpid()}"
    network_result = run(["docker", "network", "create", network], timeout=20.0)
    rows: list[dict[str, Any]] = []
    try:
        if all(
            result.returncode == 0
            for result in (build, certificate, network_result)
        ):
            for index in range(max(1, iterations)):
                rows.append(
                    run_iteration(
                        root=root,
                        image=server_image,
                        network=network,
                        certs=certs,
                        temp_root=temp_root,
                        index=index,
                    )
                )
    finally:
        run(["docker", "network", "rm", network], timeout=20.0)
        if not keep_temp:
            shutil.rmtree(temp_root, ignore_errors=True)
    run_count = max(1, iterations)
    ok_count = sum(row.get("status") == "ok" for row in rows)
    ok = (
        all(
            result.returncode == 0
            for result in (build, certificate, network_result)
        )
        and len(rows) == run_count
        and ok_count == run_count
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if ok else "failed",
        "run_count": run_count,
        "ok_run_count": ok_count,
        "online_client_crl_refresh_new_connections_claim": ok,
        "new_connection_revocation_without_server_restart_claim": ok,
        "restored_crl_acceptance_without_server_restart_claim": ok,
        "invalid_crl_refresh_fail_closed_claim": ok,
        "active_session_revocation_claim": False,
        "online_client_ca_rotation_claim": False,
        "online_server_certificate_rotation_claim": False,
        "production_quic_backend_claim": False,
        "docker_netem_both_ends_claim": ok,
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
            "docker_ngtcp2_public_online_crl_refresh_summary.json"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_probe(
        root=ROOT,
        base_image=args.base_image,
        server_image=args.server_image,
        iterations=args.iterations,
        skip_server_build=args.skip_server_build,
        keep_temp=args.keep_temp,
    )
    output = ROOT / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(f"status={summary['status']}")
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
