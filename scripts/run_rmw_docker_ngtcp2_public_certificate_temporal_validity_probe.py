#!/usr/bin/env python3
"""Prove the public-API ngtcp2/GnuTLS mTLS server correctly rejects
temporally invalid client certificates: expired (notAfter in the past)
and not-yet-valid (notBefore in the future).

This closes a specific gap named by the administrative audit (NV-10:
"invalid/expired/not-yet-valid cert, clock skew"). Every existing negative
control (missing, untrusted CA, wrong URI SAN, revoked) tests a different
axis of rejection; none of them ever generated a certificate whose CA,
subject, and SAN are all otherwise perfectly valid but whose time window
does not cover the moment of the handshake. TLS's temporal check
(`now >= notBefore && now <= notAfter`) is exactly what any real clock-skew
scenario ultimately exercises -- whether the skew lives in the peer's own
clock or (as here, more precisely and without touching the shared
machine's real wall clock) in the certificate's own validity window, the
verification logic being tested is identical.

Both new certificates are signed by the SAME client CA and carry the SAME
required URI SAN as the "valid" certificate elsewhere in this suite, so a
rejection can only be attributed to the temporal check -- not to some
other, already-covered mismatch.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_rmw_docker_ngtcp2_public_mtls_server_probe import (  # noqa: E402
    REQUIRED_URI_SAN,
    client_command,
    docker_network_rm,
    docker_rm,
    negative_client_was_rejected,
    run,
    wait_for_server,
)
from scripts.run_rmw_docker_quic_mtls_probe import certificate_command  # noqa: E402


SCHEMA_VERSION = "fleetrmw.docker_ngtcp2_public_certificate_temporal_validity.v1"
DEFAULT_IMAGE = "localhost/fleetrmw/ngtcp2-public-mtls:0.12.1"
DEFAULT_BASE_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"


def temporal_certificate_command(certs: Path, root: Path) -> str:
    prefix = f"/work/{certs.relative_to(root)}"
    generator = (
        "from cryptography import x509; "
        "from cryptography.hazmat.primitives import hashes, serialization; "
        "from cryptography.hazmat.primitives.asymmetric import rsa; "
        "from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID; "
        "from datetime import datetime, timedelta; from pathlib import Path; "
        f"p = Path('{prefix}'); now = datetime.utcnow(); "
        "ca_cert = x509.load_pem_x509_certificate((p/'client-ca.crt').read_bytes()); "
        "ca_key = serialization.load_pem_private_key((p/'client-ca.key').read_bytes(), None); "
        "\n"
        "def make_cert(stem, not_before, not_after):\n"
        "    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)\n"
        "    (p / f'{stem}.key').write_bytes(key.private_bytes(\n"
        "        serialization.Encoding.PEM,\n"
        "        serialization.PrivateFormat.TraditionalOpenSSL,\n"
        "        serialization.NoEncryption()))\n"
        "    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'mtls-publisher')])\n"
        "    builder = (\n"
        "        x509.CertificateBuilder()\n"
        "        .subject_name(subject)\n"
        "        .issuer_name(ca_cert.subject)\n"
        "        .public_key(key.public_key())\n"
        "        .serial_number(x509.random_serial_number())\n"
        "        .not_valid_before(not_before)\n"
        "        .not_valid_after(not_after)\n"
        "        .add_extension(\n"
        "            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),\n"
        "            critical=False)\n"
        "        .add_extension(\n"
        f"            x509.SubjectAlternativeName([x509.UniformResourceIdentifier('{REQUIRED_URI_SAN}')]),\n"
        "            critical=False)\n"
        "    )\n"
        "    cert = builder.sign(ca_key, hashes.SHA256())\n"
        "    (p / f'{stem}.crt').write_bytes(cert.public_bytes(serialization.Encoding.PEM))\n"
        "\n"
        "make_cert('expired-client', now - timedelta(days=2), now - timedelta(days=1))\n"
        "make_cert('not-yet-valid-client', now + timedelta(days=1), now + timedelta(days=2))\n"
    )
    return f"{certificate_command(certs, root)} && python3 -c {shlex.quote(generator)}"


def run_iteration(
    *,
    root: Path,
    image: str,
    index: int,
    temp_root: Path,
) -> dict[str, Any]:
    suffix = f"{os.getpid()}-{index}"
    network = f"fq-cert-temporal-net-{suffix}"
    server_name = f"fq-cert-temporal-server-{suffix}"
    certs = temp_root / "certs"
    htdocs = temp_root / f"htdocs-{index}"
    qlogs = temp_root / f"qlogs-{index}"
    server_qlogs = qlogs / "server"
    htdocs.mkdir(parents=True, exist_ok=True)
    server_qlogs.mkdir(parents=True, exist_ok=True)
    (htdocs / "index.html").write_text(
        "fleetqox public api certificate temporal validity\n", encoding="utf-8"
    )

    network_result = run(["docker", "network", "create", network], timeout=20.0)
    if network_result.returncode != 0:
        return {
            "index": index,
            "status": "failed",
            "reason": "network_create_failed",
            "stderr": network_result.stderr,
        }

    cert_root = f"/work/{certs.relative_to(root)}"
    htdocs_root = f"/work/{htdocs.relative_to(root)}"
    server_qlog_root = f"/work/{server_qlogs.relative_to(root)}"
    server_command = (
        "tc qdisc add dev eth0 root netem delay 11ms 2ms loss 0.2% && "
        "exec fleetqox-public-mtls-server "
        f"--htdocs={shlex.quote(htdocs_root)} "
        f"--qlog-dir={shlex.quote(server_qlog_root)} "
        "--verify-client "
        "--no-quic-dump --no-http-dump "
        f"'*' 4433 {shlex.quote(cert_root + '/server.key')} "
        f"{shlex.quote(cert_root + '/server.crt')}"
    )
    server = run(
        [
            "docker", "run", "-d", "--name", server_name,
            "--network", network, "--network-alias", "fleetqox-public-server",
            "--cap-add", "NET_ADMIN",
            "--entrypoint", "bash",
            "-e", f"FLEETQOX_GNUTLS_CLIENT_CA={cert_root}/client-ca.crt",
            "-e", f"FLEETQOX_GNUTLS_REQUIRED_CLIENT_URI_SAN={REQUIRED_URI_SAN}",
            "-v", f"{root}:/work", "-w", "/work",
            image, "-lc", server_command,
        ],
        timeout=20.0,
    )

    try:
        ready = server.returncode == 0 and wait_for_server(server_name)
        clients: dict[str, subprocess.CompletedProcess[str]] = {}
        cases = (
            ("valid", "client.key", "client.crt", 2),
            ("expired", "expired-client.key", "expired-client.crt", 1),
            ("not_yet_valid", "not-yet-valid-client.key", "not-yet-valid-client.crt", 1),
        )
        if ready:
            for label, key_name, cert_name, streams in cases:
                clients[label] = run(
                    [
                        "docker", "run", "--rm", "--name", f"fq-cert-temporal-{label}-{suffix}",
                        "--network", network, "--cap-add", "NET_ADMIN",
                        "--entrypoint", "bash",
                        "-v", f"{root}:/work", "-w", "/work",
                        image, "-lc",
                        client_command(
                            cert_root=cert_root,
                            qlog_file=f"/work/{(qlogs / f'{label}.qlog').relative_to(root)}",
                            key_name=key_name,
                            cert_name=cert_name,
                            streams=streams,
                        ),
                    ],
                    timeout=60.0,
                )
        logs = run(["docker", "logs", server_name], timeout=20.0)
        server_logs = logs.stdout + logs.stderr
        valid = clients.get("valid")
        valid_qlog = qlogs / "valid.qlog"
        valid_response_count = 0 if valid is None else valid.stderr.count("[:status: 200]")
        positive_ok = (
            valid is not None
            and valid.returncode == 0
            and valid_response_count == 2
            and valid_qlog.is_file()
            and valid_qlog.stat().st_size > 0
        )
        negative_rejections = {
            label: negative_client_was_rejected(clients.get(label))
            for label in ("expired", "not_yet_valid")
        }
        certificate_reject_count = server_logs.count(
            "FLEETQOX_PUBLIC_MTLS_REJECT verify_result="
        )
        negative_ok = all(negative_rejections.values())
        log_evidence_ok = certificate_reject_count >= 2
        ok = ready and positive_ok and negative_ok and log_evidence_ok
        return {
            "index": index,
            "status": "ok" if ok else "failed",
            "server_ready": ready,
            "valid_returncode": None if valid is None else valid.returncode,
            "valid_response_200_count": valid_response_count,
            "expired_returncode": None
            if clients.get("expired") is None else clients["expired"].returncode,
            "not_yet_valid_returncode": None
            if clients.get("not_yet_valid") is None else clients["not_yet_valid"].returncode,
            "negative_protocol_rejections": negative_rejections,
            "certificate_rejection_count": certificate_reject_count,
            "netem_client": "delay 9ms 2ms loss 0.2%",
            "netem_server": "delay 11ms 2ms loss 0.2%",
            "valid_stderr": "" if ok or valid is None else valid.stderr,
            "negative_stderr": {}
            if ok
            else {
                label: completed.stderr
                for label, completed in clients.items()
                if label != "valid"
            },
            "server_logs": "" if ok else server_logs,
        }
    finally:
        docker_rm(server_name)
        docker_network_rm(network)


def run_probe(
    *,
    root: Path,
    image: str,
    base_image: str,
    iterations: int,
    keep_temp: bool,
    skip_build: bool,
) -> dict[str, Any]:
    dockerfile = root / "external/ngtcp2-public-mtls/Dockerfile"
    build = subprocess.CompletedProcess([], 0, "", "")
    if not skip_build:
        build = run(
            [
                "docker", "build", "--build-arg", f"BASE_IMAGE={base_image}",
                "-f", str(dockerfile), "-t", image, ".",
            ],
            timeout=600.0,
        )
    temp_root = root / f".tmp_fleetrmw_cert_temporal_{os.getpid()}"
    certs = temp_root / "certs"
    certs.mkdir(parents=True, exist_ok=True)
    certificate_result = run(
        [
            "docker", "run", "--rm", "--entrypoint", "bash",
            "-v", f"{root}:/work", "-w", "/work",
            base_image, "-lc", temporal_certificate_command(certs, root),
        ],
        timeout=120.0,
    )
    rows: list[dict[str, Any]] = []
    try:
        if build.returncode == 0 and certificate_result.returncode == 0:
            for index in range(max(1, iterations)):
                rows.append(
                    run_iteration(root=root, image=image, index=index, temp_root=temp_root)
                )
        ok_count = sum(row.get("status") == "ok" for row in rows)
        run_count = max(1, iterations)
        ok = (
            build.returncode == 0
            and certificate_result.returncode == 0
            and len(rows) == run_count
            and ok_count == run_count
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if ok else "failed",
            "image": image,
            "base_image": base_image,
            "run_count": run_count,
            "ok_run_count": ok_count,
            "certificate_expired_rejected_claim": ok,
            "certificate_not_yet_valid_rejected_claim": ok,
            "certificate_temporal_validity_repeated_claim": ok and run_count >= 5,
            "runs": rows,
            "build_returncode": build.returncode,
            "certificate_returncode": certificate_result.returncode,
            "build_stderr": "" if build.returncode == 0 else build.stderr,
            "certificate_stderr": ""
            if certificate_result.returncode == 0 else certificate_result.stderr,
        }
    finally:
        if not keep_temp:
            shutil.rmtree(temp_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--base-image", default=DEFAULT_BASE_IMAGE)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--keep-temp", action="store_true")
    parser.add_argument(
        "--summary-json",
        default=(
            "results_rmw_socket/"
            "docker_ngtcp2_public_certificate_temporal_validity_summary.json"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_probe(
        root=ROOT,
        image=args.image,
        base_image=args.base_image,
        iterations=args.iterations,
        keep_temp=args.keep_temp,
        skip_build=args.skip_build,
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
