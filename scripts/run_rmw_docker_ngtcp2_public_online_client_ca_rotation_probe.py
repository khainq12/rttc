#!/usr/bin/env python3
"""Prove online client-CA rotation for the public ngtcp2 gateway.

The existing online client-CRL refresh path (see
run_rmw_docker_ngtcp2_public_online_crl_refresh_probe.py) rebuilds a brand
new GnuTLS credentials object from FLEETQOX_GNUTLS_CLIENT_CA and
FLEETQOX_GNUTLS_CLIENT_CRL on every handshake -- it re-reads *both* files
fresh each time, not just the CRL. That means replacing the on-disk content
of the client-CA trust file should already rotate which CA is trusted,
without a server restart, as a side effect of a mechanism that was only
ever tested and claimed for CRL revocation. This probe is the first to
actually exercise that: it starts trusting CA-A, proves a CA-B-signed
client is rejected, swaps the client-CA file's content from CA-A to CA-B,
proves a CA-B client is now accepted *and* a CA-A client is now rejected
(a genuine rotation, not just an addition), then restores CA-A and proves
it works again.
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


SCHEMA_VERSION = "fleetrmw.docker_ngtcp2_public_online_client_ca_rotation.v1"


def rotation_certificate_command(certs: Path, root: Path) -> str:
    prefix = f"/work/{certs.relative_to(root)}"
    empty_crl_python = (
        "from datetime import datetime, timedelta, timezone; "
        "from pathlib import Path; "
        "from cryptography import x509; "
        "from cryptography.hazmat.primitives import hashes, serialization; "
        f"p=Path('{prefix}'); now=datetime.now(timezone.utc); "
        "ca=x509.load_pem_x509_certificate((p/'rotated-ca.crt').read_bytes()); "
        "key=serialization.load_pem_private_key((p/'rotated-ca.key').read_bytes(),None); "
        "crl=x509.CertificateRevocationListBuilder().issuer_name(ca.subject)."
        "last_update(now).next_update(now+timedelta(days=1))."
        "sign(key,hashes.SHA256()); "
        "(p/'rotated.crl.pem').write_bytes(crl.public_bytes(serialization.Encoding.PEM))"
    )
    return (
        stateful_certificate_command(certs, root)
        # A second, independent client CA -- not yet trusted by the server
        # at startup -- plus a client it signs whose cert still carries the
        # URI SAN the identity-fairness patch requires of every client
        # regardless of which CA issued it.
        + " && openssl req -x509 -newkey rsa:2048 -nodes "
        f"-keyout {prefix}/rotated-ca.key -out {prefix}/rotated-ca.crt "
        "-subj /CN=FleetQoX-Rotated-Client-CA "
        "-addext basicConstraints=critical,CA:TRUE "
        "-addext keyUsage=critical,keyCertSign,cRLSign -days 1 >/dev/null 2>&1 && "
        "openssl req -new -newkey rsa:2048 -nodes "
        f"-keyout {prefix}/rotated-client.key -out {prefix}/rotated-client.csr "
        "-subj /CN=fleetqox-rotated-client "
        "-addext extendedKeyUsage=clientAuth "
        "-addext subjectAltName=URI:spiffe://fleetqox/publishers/rotated-client "
        ">/dev/null 2>&1 && "
        f"openssl x509 -req -in {prefix}/rotated-client.csr "
        f"-CA {prefix}/rotated-ca.crt -CAkey {prefix}/rotated-ca.key "
        f"-CAcreateserial -out {prefix}/rotated-client.crt "
        "-days 1 -copy_extensions copy >/dev/null 2>&1 && "
        f"cp {prefix}/client-ca.crt {prefix}/original-client-ca.crt && "
        f"cp {prefix}/client.crl.pem {prefix}/original-client.crl.pem && "
        f"python3 -c {shlex.quote(empty_crl_python)}"
    )


def swap_file(
    container: str,
    *,
    root: Path,
    certs: Path,
    target_name: str,
    source_name: str,
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


def rotate_to_ca_b(container: str, *, root: Path, certs: Path) -> bool:
    return swap_file(
        container, root=root, certs=certs,
        target_name="client-ca.crt", source_name="rotated-ca.crt",
    ) and swap_file(
        container, root=root, certs=certs,
        target_name="client.crl.pem", source_name="rotated.crl.pem",
    )


def restore_ca_a(container: str, *, root: Path, certs: Path) -> bool:
    return swap_file(
        container, root=root, certs=certs,
        target_name="client-ca.crt", source_name="original-client-ca.crt",
    ) and swap_file(
        container, root=root, certs=certs,
        target_name="client.crl.pem", source_name="original-client.crl.pem",
    )


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
    server_name = f"fq-public-carot-server-{suffix}"
    client_name = f"fq-public-carot-client-{suffix}"
    run_root = temp_root / f"run-{index}"
    qlogs = run_root / "client-qlogs"
    qlogs.mkdir(parents=True, exist_ok=True)

    start, backend_path, proxy_path = start_server(
        root=root, image=image, network=network, name=server_name, certs=certs,
        run_root=run_root,
        extra_environment={"FLEETQOX_GNUTLS_RELOAD_CLIENT_CRL_EACH_HANDSHAKE": "1"},
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
        root=root, image=image, network=network, name=client_name, certs=certs,
    )
    client_ready = client_start.returncode == 0 and wait_client_ready(client_name)

    not_started = subprocess.CompletedProcess([], 1, "", "not_started")
    before_a = not_started
    before_b = not_started
    after_b = not_started
    after_a = not_started
    restored_a = not_started
    rotated_installed = False
    restore_installed = False
    instance_before = ""
    instance_after = ""
    server_exit = -1
    logs = ""
    try:
        if server_ready and client_ready:
            instance_before = server_instance(server_name)
            before_a = run_exec_client(
                root=root, container=client_name, certs=certs,
                certificate_name="stateful-client", consumer_id="carot-before-a",
                qlog=qlogs / "before-a.qlog",
            )
            before_b = run_exec_client(
                root=root, container=client_name, certs=certs,
                certificate_name="rotated-client", consumer_id="carot-before-b",
                qlog=qlogs / "before-b.qlog",
            )
            rotated_installed = rotate_to_ca_b(client_name, root=root, certs=certs)
            rotated_installed = rotated_installed and wait_for_log(
                server_name, "FLEETQOX_PUBLIC_MTLS_CRL_RELOADED", count=1
            )
            after_b = run_exec_client(
                root=root, container=client_name, certs=certs,
                certificate_name="rotated-client", consumer_id="carot-after-b",
                qlog=qlogs / "after-b.qlog",
            )
            after_a = run_exec_client(
                root=root, container=client_name, certs=certs,
                certificate_name="stateful-client", consumer_id="carot-after-a",
                qlog=qlogs / "after-a.qlog",
            )
            restore_installed = restore_ca_a(client_name, root=root, certs=certs)
            restore_installed = restore_installed and wait_for_log(
                server_name, "FLEETQOX_PUBLIC_MTLS_CRL_RELOADED", count=2
            )
            restored_a = run_exec_client(
                root=root, container=client_name, certs=certs,
                certificate_name="stateful-client", consumer_id="carot-restored-a",
                qlog=qlogs / "restored-a.qlog",
            )
            instance_after = server_instance(server_name)
        if server_ready:
            server_exit, logs = stop_server(server_name)
    finally:
        if not restore_installed:
            restore_ca_a(client_name, root=root, certs=certs)
        run(["docker", "rm", "-f", client_name], timeout=20.0)
        run(["docker", "rm", "-f", server_name], timeout=20.0)

    backend = load_json(backend_path)
    proxy = load_json(proxy_path)
    state = backend.get("metrics", {}).get("state", {})
    qlog_files = list(qlogs.glob("*.qlog"))
    before_b_rejected = negative_client_was_rejected(before_b)
    after_a_rejected = negative_client_was_rejected(after_a)
    same_server_instance = bool(instance_before) and instance_before == instance_after
    reload_success_count = logs.count("FLEETQOX_PUBLIC_MTLS_CRL_RELOADED")
    verified_count = logs.count("FLEETQOX_PUBLIC_MTLS_VERIFIED")
    ok = (
        server_ready
        and client_ready
        and rotated_installed
        and restore_installed
        and response_has_status(before_a, 204)
        and before_b_rejected
        and response_has_status(after_b, 204)
        and after_a_rejected
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
        "client_ready": client_ready,
        "server_exit_code": server_exit,
        "same_server_instance": same_server_instance,
        "before_ca_a_http_204": response_has_status(before_a, 204),
        "before_ca_b_rejected": before_b_rejected,
        "after_ca_b_http_204": response_has_status(after_b, 204),
        "after_ca_a_rejected": after_a_rejected,
        "restored_ca_a_http_204": response_has_status(restored_a, 204),
        "reload_success_count": reload_success_count,
        "verified_client_count": verified_count,
        "backend": backend,
        "proxy": proxy,
        "client_qlog_file_count": len(qlog_files),
        "captured_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "before_a_stderr": "" if ok else before_a.stderr,
        "before_b_stderr": "" if ok else before_b.stderr,
        "after_b_stderr": "" if ok else after_b.stderr,
        "after_a_stderr": "" if ok else after_a.stderr,
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
    temp_root = root / f".tmp_fleetrmw_public_carot_{os.getpid()}"
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
    network = f"fq-public-carot-net-{os.getpid()}"
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
        "online_client_ca_rotation_claim": ok,
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
            "docker_ngtcp2_public_online_client_ca_rotation_summary.json"
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
