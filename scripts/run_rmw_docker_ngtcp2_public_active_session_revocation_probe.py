#!/usr/bin/env python3
"""Prove the public ngtcp2 gateway closes an already-established connection
whose certificate is revoked, without waiting for that connection to end on
its own.

Prior to this probe, the online CRL reload path (see
run_rmw_docker_ngtcp2_public_online_crl_refresh_probe.py) only consulted the
freshly reloaded CRL for *new* connections; an already-established session
kept trusting its client for the rest of its natural lifetime even after its
certificate was revoked. This probe keeps one connection open (client A),
revokes that exact connection's own certificate by rewriting the on-disk CRL,
then makes a second, unrelated connection (client B, still-valid certificate)
whose handshake triggers a CRL reload -- and confirms client A's
already-established connection is now forcibly closed as a side effect,
while client B's own connection succeeds normally.
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


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "fleetrmw.docker_ngtcp2_public_active_session_revocation_probe.v1"
DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"


def run(cmd: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, timeout=timeout,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--keep-temp", action="store_true")
    parser.add_argument(
        "--summary-json",
        default=(
            "results_rmw_socket/"
            "docker_ngtcp2_public_active_session_revocation_probe_summary.json"
        ),
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
        print("fleetrmw-ngtcp2-public-active-session-revocation-probe")
        print(f"  status: {summary['status']}")
        print(f"  client_a_force_closed: {summary.get('client_a_force_closed')}")
        print(f"  client_b_succeeded: {summary.get('client_b_succeeded')}")
        print(f"  server_logged_revocation: {summary.get('server_logged_revocation')}")
    return 0 if summary["status"] == "ok" else 1


def certificate_command(certs: Path, root: Path) -> str:
    prefix = f"/work/{certs.relative_to(root)}"
    empty_crl_python = (
        "from datetime import datetime, timedelta, timezone; "
        "from pathlib import Path; "
        "from cryptography import x509; "
        "from cryptography.hazmat.primitives import hashes, serialization; "
        f"p=Path('{prefix}'); now=datetime.now(timezone.utc); "
        "ca=x509.load_pem_x509_certificate((p/'client-ca.crt').read_bytes()); "
        "key=serialization.load_pem_private_key((p/'client-ca.key').read_bytes(),None); "
        "crl=x509.CertificateRevocationListBuilder().issuer_name(ca.subject)."
        "last_update(now).next_update(now+timedelta(days=1))."
        "sign(key,hashes.SHA256()); "
        "(p/'live.crl.pem').write_bytes(crl.public_bytes(serialization.Encoding.PEM))"
    )
    return (
        "openssl req -x509 -newkey rsa:2048 -nodes "
        f"-keyout {prefix}/server-ca.key -out {prefix}/server-ca.crt "
        "-subj /CN=FleetQoX-ASR-Server-CA "
        "-addext basicConstraints=critical,CA:TRUE "
        "-addext keyUsage=critical,keyCertSign,cRLSign -days 1 >/dev/null 2>&1 && "
        "openssl req -new -newkey rsa:2048 -nodes "
        f"-keyout {prefix}/server.key -out {prefix}/server.csr "
        "-subj /CN=fleetqox-asr-server "
        "-addext subjectAltName=DNS:fleetqox-asr-server "
        "-addext extendedKeyUsage=serverAuth >/dev/null 2>&1 && "
        f"openssl x509 -req -in {prefix}/server.csr -CA {prefix}/server-ca.crt "
        f"-CAkey {prefix}/server-ca.key -CAcreateserial -out {prefix}/server.crt "
        "-days 1 -copy_extensions copy >/dev/null 2>&1 && "
        "openssl req -x509 -newkey rsa:2048 -nodes "
        f"-keyout {prefix}/client-ca.key -out {prefix}/client-ca.crt "
        "-subj /CN=FleetQoX-ASR-Client-CA "
        "-addext basicConstraints=critical,CA:TRUE "
        "-addext keyUsage=critical,keyCertSign,cRLSign -days 1 >/dev/null 2>&1 && "
        # A subjectAltName URI is required: handshake_completed() (added by
        # identity-fairness.patch, already applied ahead of this one)
        # extracts a client identity from the peer cert's URI SAN and
        # rejects the connection outright if none is present, independent
        # of anything in this patch.
        "openssl req -new -newkey rsa:2048 -nodes "
        f"-keyout {prefix}/client-a.key -out {prefix}/client-a.csr "
        "-subj /CN=fleetqox-asr-client-a "
        "-addext extendedKeyUsage=clientAuth "
        "-addext subjectAltName=URI:spiffe://fleetqox/publishers/asr-client-a "
        ">/dev/null 2>&1 && "
        f"openssl x509 -req -in {prefix}/client-a.csr -CA {prefix}/client-ca.crt "
        f"-CAkey {prefix}/client-ca.key -CAcreateserial -out {prefix}/client-a.crt "
        "-days 1 -copy_extensions copy >/dev/null 2>&1 && "
        "openssl req -new -newkey rsa:2048 -nodes "
        f"-keyout {prefix}/client-b.key -out {prefix}/client-b.csr "
        "-subj /CN=fleetqox-asr-client-b "
        "-addext extendedKeyUsage=clientAuth "
        "-addext subjectAltName=URI:spiffe://fleetqox/publishers/asr-client-b "
        ">/dev/null 2>&1 && "
        f"openssl x509 -req -in {prefix}/client-b.csr -CA {prefix}/client-ca.crt "
        f"-CAkey {prefix}/client-ca.key -CAcreateserial -out {prefix}/client-b.crt "
        "-days 1 -copy_extensions copy >/dev/null 2>&1 && "
        f"python3 -c {shlex.quote(empty_crl_python)}"
    )


def revoke_client_a_command(certs: Path, root: Path) -> str:
    prefix = f"/work/{certs.relative_to(root)}"
    revoke_python = (
        "from datetime import datetime, timedelta, timezone; "
        "from pathlib import Path; "
        "from cryptography import x509; "
        "from cryptography.hazmat.primitives import hashes, serialization; "
        f"p=Path('{prefix}'); now=datetime.now(timezone.utc); "
        "ca=x509.load_pem_x509_certificate((p/'client-ca.crt').read_bytes()); "
        "key=serialization.load_pem_private_key((p/'client-ca.key').read_bytes(),None); "
        "peer=x509.load_pem_x509_certificate((p/'client-a.crt').read_bytes()); "
        "entry=x509.RevokedCertificateBuilder().serial_number(peer.serial_number)."
        "revocation_date(now).build(); "
        "crl=x509.CertificateRevocationListBuilder().issuer_name(ca.subject)."
        "last_update(now).next_update(now+timedelta(days=1))."
        "add_revoked_certificate(entry).sign(key,hashes.SHA256()); "
        "(p/'live.crl.pem').write_bytes(crl.public_bytes(serialization.Encoding.PEM))"
    )
    return f"python3 -c {shlex.quote(revoke_python)}"


def run_probe(*, root: Path, image: str, keep_temp: bool) -> dict[str, Any]:
    temp_root = root / f".tmp_fleetrmw_asr_{os.getpid()}"
    certs = temp_root / "certs"
    certs.mkdir(parents=True, exist_ok=True)
    qlogs = temp_root / "qlogs"
    qlogs.mkdir(parents=True, exist_ok=True)
    build_base = "/work/.tmp_fleetrmw_asr_ngtcp2_build"
    server_name = f"fq-asr-server-{os.getpid()}"
    network = f"fq-asr-net-{os.getpid()}"

    dockerfile_dir = root / "external" / "ngtcp2-public-mtls"
    build_image = f"fleetrmw/ngtcp2-asr-probe:{os.getpid()}"
    build = run(
        [
            "docker", "build",
            "-f", str(dockerfile_dir / "Dockerfile"),
            "-t", build_image,
            "--build-arg", f"BASE_IMAGE={image}",
            str(root),
        ],
        timeout=900.0,
    )

    cert_result = subprocess.CompletedProcess([], 1, "", "not_run")
    network_result = subprocess.CompletedProcess([], 1, "", "not_run")
    client_a: subprocess.Popen[str] | None = None
    client_a_stdout = ""
    client_a_stderr = ""
    client_a_returncode = -1
    client_b = subprocess.CompletedProcess([], 1, "", "not_run")
    revoke_result = subprocess.CompletedProcess([], 1, "", "not_run")
    server_logs = ""
    try:
        if build.returncode == 0:
            cert_result = run(
                [
                    "docker", "run", "--rm", "--entrypoint", "bash",
                    "-v", f"{root}:/work", "-w", "/work",
                    build_image, "-lc", certificate_command(certs, root),
                ]
            )
            network_result = run(["docker", "network", "create", network])
            htdocs = temp_root / "htdocs"
            htdocs.mkdir(parents=True, exist_ok=True)
            (htdocs / "index.html").write_text("fleetqox-asr-probe\n", encoding="utf-8")
            if cert_result.returncode == 0 and network_result.returncode == 0:
                server_command = (
                    "exec fleetqox-public-mtls-server "
                    f"--htdocs=/work/{htdocs.relative_to(root)} --verify-client "
                    f"--qlog-dir=/work/{qlogs.relative_to(root)} "
                    "--no-quic-dump --no-http-dump "
                    f"'*' 4433 "
                    f"/work/{(certs / 'server.key').relative_to(root)} "
                    f"/work/{(certs / 'server.crt').relative_to(root)}"
                )
                server_start = run(
                    [
                        "docker", "run", "-d",
                        "--name", server_name,
                        "--network", network,
                        "--network-alias", "fleetqox-asr-server",
                        "--entrypoint", "bash",
                        "-e", "FLEETQOX_GNUTLS_CLIENT_CA=/work/"
                        f"{(certs / 'client-ca.crt').relative_to(root)}",
                        "-e", "FLEETQOX_GNUTLS_CLIENT_CRL=/work/"
                        f"{(certs / 'live.crl.pem').relative_to(root)}",
                        "-e", "FLEETQOX_GNUTLS_RELOAD_CLIENT_CRL_EACH_HANDSHAKE=1",
                        "-v", f"{root}:/work", "-w", "/work",
                        build_image, "-lc", server_command,
                    ]
                )
                time.sleep(1.5)
                server_ready = server_start.returncode == 0
                if server_ready:
                    client_a_command = (
                        f"cp /work/{(certs / 'server-ca.crt').relative_to(root)} "
                        "/usr/local/share/ca-certificates/fleetqox-asr-server-ca.crt && "
                        "update-ca-certificates >/dev/null 2>&1 && "
                        "gtlsclient "
                        f"--key=/work/{(certs / 'client-a.key').relative_to(root)} "
                        f"--cert=/work/{(certs / 'client-a.crt').relative_to(root)} "
                        "--disable-early-data --no-quic-dump --no-http-dump "
                        f"--qlog-file=/work/{(qlogs / 'client-a.qlog').relative_to(root)} "
                        "--timeout=20 "
                        "fleetqox-asr-server 4433 "
                        "https://fleetqox-asr-server:4433/index.html"
                    )
                    client_a = subprocess.Popen(
                        [
                            "docker", "run", "--rm",
                            "--name", f"fq-asr-client-a-{os.getpid()}",
                            "--network", network,
                            "--entrypoint", "bash",
                            "-v", f"{root}:/work", "-w", "/work",
                            build_image, "-lc", client_a_command,
                        ],
                        cwd=root, text=True,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    )
                    # Give client A time to complete its handshake and
                    # first request before its certificate is revoked
                    # out from under it.
                    time.sleep(2.5)
                    revoke_result = run(
                        [
                            "docker", "run", "--rm", "--entrypoint", "bash",
                            "-v", f"{root}:/work", "-w", "/work",
                            build_image, "-lc", revoke_client_a_command(certs, root),
                        ]
                    )
                    client_b_command = (
                        f"cp /work/{(certs / 'server-ca.crt').relative_to(root)} "
                        "/usr/local/share/ca-certificates/fleetqox-asr-server-ca.crt && "
                        "update-ca-certificates >/dev/null 2>&1 && "
                        "gtlsclient "
                        f"--key=/work/{(certs / 'client-b.key').relative_to(root)} "
                        f"--cert=/work/{(certs / 'client-b.crt').relative_to(root)} "
                        "--disable-early-data --exit-on-all-streams-close "
                        "--no-quic-dump --no-http-dump "
                        f"--qlog-file=/work/{(qlogs / 'client-b.qlog').relative_to(root)} "
                        "fleetqox-asr-server 4433 "
                        "https://fleetqox-asr-server:4433/index.html"
                    )
                    client_b = run(
                        [
                            "docker", "run", "--rm",
                            "--name", f"fq-asr-client-b-{os.getpid()}",
                            "--network", network,
                            "--entrypoint", "bash",
                            "-v", f"{root}:/work", "-w", "/work",
                            build_image, "-lc", client_b_command,
                        ],
                        timeout=30.0,
                    )
                    try:
                        client_a_stdout, client_a_stderr = client_a.communicate(timeout=15.0)
                        client_a_returncode = client_a.returncode
                    except subprocess.TimeoutExpired:
                        client_a.kill()
                        client_a_stdout, client_a_stderr = client_a.communicate()
                        client_a_returncode = -1
                server_logs = run(["docker", "logs", server_name]).stdout + run(
                    ["docker", "logs", server_name]
                ).stderr
    finally:
        if client_a is not None and client_a.poll() is None:
            client_a.kill()
        run(["docker", "rm", "-f", server_name])
        run(["docker", "network", "rm", network])
        run(["docker", "rmi", "-f", build_image])
        if not keep_temp:
            shutil.rmtree(temp_root, ignore_errors=True)

    client_a_force_closed = (
        client_a_returncode == 0
        and "CONNECTION_CLOSE" in client_a_stderr
        and "response headers started" in client_a_stderr
    )
    client_b_succeeded = (
        client_b.returncode == 0
        and "[:status: 200]" in client_b.stderr
    )
    server_logged_revocation = "FLEETQOX_PUBLIC_MTLS_ACTIVE_SESSION_REVOKED" in server_logs
    status = (
        "ok"
        if build.returncode == 0
        and cert_result.returncode == 0
        and network_result.returncode == 0
        and revoke_result.returncode == 0
        and client_a_force_closed
        and client_b_succeeded
        and server_logged_revocation
        else "failed"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "build_returncode": build.returncode,
        "cert_returncode": cert_result.returncode,
        "network_returncode": network_result.returncode,
        "revoke_returncode": revoke_result.returncode,
        "client_a_returncode": client_a_returncode,
        "client_a_force_closed": client_a_force_closed,
        "client_b_returncode": client_b.returncode,
        "client_b_succeeded": client_b_succeeded,
        "server_logged_revocation": server_logged_revocation,
        "client_a_stderr": "" if status == "ok" else client_a_stderr,
        "client_b_stderr": "" if status == "ok" else client_b.stderr,
        "server_logs": "" if status == "ok" else server_logs,
        "build_stderr": "" if build.returncode == 0 else build.stderr,
    }


if __name__ == "__main__":
    raise SystemExit(main())
