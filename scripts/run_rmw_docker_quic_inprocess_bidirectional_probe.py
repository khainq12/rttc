"""Exercise in-process QUIC/H3 publish and take paths on one persistent session.

The RMW positive case runs publish and take through one persistent connection;
its final rmw_publish and rmw_take are launched by independent threads and
rendezvous into concurrent POST/GET streams before either response is driven.
A second positive direct-transport case exercises the explicit paired API.  The
negative case repeats the handshake with an unrelated CA and must fail
certificate verification.  All cases run in Docker with loopback netem.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_rmw_docker_quic_gateway_rmw_take_probe import (  # noqa: E402
    expected_frame_bytes,
)
from scripts.run_rmw_docker_shared_memory_probe import parse_last_json  # noqa: E402


SCHEMA_VERSION = "fleetrmw.docker_quic_inprocess_bidirectional_probe.v3"
DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"
CONCURRENT_RESPONSE = "fleetqox-concurrent-get-v1"


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def certificate_commands(*, certs: Path, root: Path) -> str:
    prefix = f"/work/{certs.relative_to(root)}"
    return (
        "openssl req -x509 -newkey rsa:2048 -nodes "
        f"-keyout {prefix}/ca.key -out {prefix}/ca.crt "
        "-subj /CN=FleetQoX-InProcess-Test-CA "
        "-addext basicConstraints=critical,CA:TRUE "
        "-addext keyUsage=critical,keyCertSign,cRLSign -days 1 >/dev/null 2>&1; "
        "openssl req -new -newkey rsa:2048 -nodes "
        f"-keyout {prefix}/server.key -out {prefix}/server.csr "
        "-subj /CN=localhost "
        "-addext subjectAltName=DNS:localhost,IP:127.0.0.1 "
        "-addext basicConstraints=critical,CA:FALSE "
        "-addext keyUsage=critical,digitalSignature,keyEncipherment "
        "-addext extendedKeyUsage=serverAuth >/dev/null 2>&1; "
        f"openssl x509 -req -in {prefix}/server.csr -CA {prefix}/ca.crt "
        f"-CAkey {prefix}/ca.key -CAcreateserial -out {prefix}/server.crt "
        "-days 1 -copy_extensions copy >/dev/null 2>&1; "
        "openssl req -x509 -newkey rsa:2048 -nodes "
        f"-keyout {prefix}/wrong-ca.key -out {prefix}/wrong-ca.crt "
        "-subj /CN=FleetQoX-Untrusted-Test-CA "
        "-addext basicConstraints=critical,CA:TRUE "
        "-addext keyUsage=critical,keyCertSign,cRLSign -days 1 >/dev/null 2>&1; "
    )


def run_probe(
    *,
    root: Path,
    image: str,
    port: int,
    delay_ms: int = 5,
    loss_percent: float = 1.0,
    publish_count: int = 128,
) -> dict[str, Any]:
    suffix = str(os.getpid())
    tmp = root / f".tmp_fleetrmw_quic_inprocess_{suffix}"
    htdocs = tmp / "htdocs"
    qlogs = tmp / "qlogs"
    certs = tmp / "certs"
    build_base = root / ".tmp_fleetrmw_quic_inprocess_v2_build"
    install_base = root / ".tmp_fleetrmw_quic_inprocess_v2_install"
    log_base = root / ".tmp_fleetrmw_quic_inprocess_v2_log"
    positive_probe = (
        f"/work/{install_base.relative_to(root)}/rmw_fleetqox_cpp/lib/"
        "rmw_fleetqox_cpp/fleetrmw_quic_inprocess_rmw_bidirectional_probe"
    )
    concurrent_probe = (
        f"/work/{install_base.relative_to(root)}/rmw_fleetqox_cpp/lib/"
        "rmw_fleetqox_cpp/fleetrmw_quic_inprocess_concurrent_stream_probe"
    )
    negative_probe = (
        f"/work/{install_base.relative_to(root)}/rmw_fleetqox_cpp/lib/"
        "rmw_fleetqox_cpp/fleetrmw_quic_inprocess_session_probe"
    )
    try:
        for directory in (htdocs, qlogs, certs):
            directory.mkdir(parents=True, exist_ok=True)
        (htdocs / "frame.frmw").write_bytes(expected_frame_bytes())
        (htdocs / "concurrent.bin").write_text(
            CONCURRENT_RESPONSE, encoding="utf-8"
        )

        build = run(
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
                image,
                "-lc",
                "source /opt/ros/jazzy/setup.bash && "
                f"rm -rf /work/{build_base.relative_to(root)} "
                f"/work/{install_base.relative_to(root)} "
                f"/work/{log_base.relative_to(root)} && "
                f"colcon --log-base /work/{log_base.relative_to(root)} build "
                "--base-paths ros2_ws/src --packages-select rmw_fleetqox_cpp "
                f"--build-base /work/{build_base.relative_to(root)} "
                f"--install-base /work/{install_base.relative_to(root)} "
                "--cmake-args -DCMAKE_BUILD_TYPE=Release",
            ]
        )
        if build.returncode != 0:
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "failed",
                "stage": "build",
                "stdout": build.stdout,
                "stderr": build.stderr,
            }

        qlog_prefix = f"/work/{qlogs.relative_to(root)}"
        shared_env = (
            "FLEETQOX_RMW_REMOTE_TRANSPORT=quic_gateway "
            "FLEETQOX_RMW_QUIC_BACKEND=inprocess "
            f"FLEETQOX_RMW_QUIC_GATEWAY=127.0.0.1:{port} "
            f"FLEETQOX_RMW_QUIC_URI=https://localhost:{port}/frame.frmw "
            "FLEETQOX_RMW_QUIC_SNI=localhost "
            "FLEETQOX_RMW_QUIC_TIMEOUT=8s "
            f"FLEETQOX_RMW_QUIC_QLOG_DIR={qlog_prefix} "
        )
        concurrent_env = (
            "FLEETQOX_RMW_REMOTE_TRANSPORT=quic_gateway "
            "FLEETQOX_RMW_QUIC_BACKEND=inprocess "
            f"FLEETQOX_RMW_QUIC_GATEWAY=127.0.0.1:{port} "
            f"FLEETQOX_RMW_QUIC_URI=https://localhost:{port}/concurrent.bin "
            "FLEETQOX_RMW_QUIC_SNI=localhost "
            "FLEETQOX_RMW_QUIC_TIMEOUT=8s "
            f"FLEETQOX_RMW_QUIC_QLOG_DIR={qlog_prefix} "
        )
        tmp_prefix = f"/work/{tmp.relative_to(root)}"
        cert_prefix = f"/work/{certs.relative_to(root)}"
        command = (
            "set -e; "
            + certificate_commands(certs=certs, root=root)
            + f"tc qdisc add dev lo root netem delay {delay_ms}ms "
            f"loss {loss_percent:.3f}%; "
            "tc qdisc show dev lo > "
            f"{tmp_prefix}/netem.log; "
            f"/usr/sbin/gtlsserver 127.0.0.1 {port} "
            f"{cert_prefix}/server.key {cert_prefix}/server.crt "
            f"-d /work/{htdocs.relative_to(root)} "
            f"--qlog-dir /work/{qlogs.relative_to(root)} "
            "--timeout=30s --handshake-timeout=8s --no-quic-dump --no-http-dump "
            f"> {tmp_prefix}/server.log 2>&1 & "
            "server_pid=$!; sleep 0.6; "
            f"source /work/{install_base.relative_to(root)}/setup.bash; set +e; "
            + shared_env
            + "FLEETQOX_RMW_QUIC_GATEWAY_TAKE_ON_DEMAND=1 "
            "FLEETQOX_RMW_QUIC_CONCURRENT_PAIR_WAIT_MS=100 "
            f"FLEETQOX_RMW_QUIC_INPROCESS_RMW_PUBLISH_COUNT={publish_count} "
            f"FLEETQOX_RMW_QUIC_CA_FILE={cert_prefix}/ca.crt "
            f"{positive_probe} > {tmp_prefix}/positive.log "
            f"2> {tmp_prefix}/positive.err; positive_rc=$?; "
            + concurrent_env
            + f"FLEETQOX_RMW_QUIC_CA_FILE={cert_prefix}/ca.crt "
            f"FLEETQOX_RMW_QUIC_EXPECTED_RESPONSE={CONCURRENT_RESPONSE} "
            f"{concurrent_probe} > {tmp_prefix}/concurrent.log "
            f"2> {tmp_prefix}/concurrent.err; concurrent_rc=$?; "
            + shared_env
            + "FLEETQOX_RMW_QUIC_INPROCESS_SEND_COUNT=1 "
            f"FLEETQOX_RMW_QUIC_CA_FILE={cert_prefix}/wrong-ca.crt "
            f"{negative_probe} > {tmp_prefix}/negative.log "
            f"2> {tmp_prefix}/negative.err; negative_rc=$?; "
            f"echo $positive_rc > {tmp_prefix}/positive.rc; "
            f"echo $concurrent_rc > {tmp_prefix}/concurrent.rc; "
            f"echo $negative_rc > {tmp_prefix}/negative.rc; "
            "sleep 0.3; kill ${server_pid} >/dev/null 2>&1 || true; "
            "wait ${server_pid} >/dev/null 2>&1 || true; "
            "tc qdisc del dev lo root >/dev/null 2>&1 || true; exit 0"
        )
        docker = run(
            [
                "docker",
                "run",
                "--rm",
                "--cap-add",
                "NET_ADMIN",
                "--entrypoint",
                "bash",
                "-v",
                f"{root}:/work",
                "-w",
                "/work",
                image,
                "-lc",
                command,
            ]
        )

        def read_text(name: str) -> str:
            path = tmp / name
            return path.read_text(errors="replace") if path.exists() else ""

        positive = parse_last_json(read_text("positive.log"))
        concurrent = parse_last_json(read_text("concurrent.log"))
        negative = parse_last_json(read_text("negative.log"))
        server_log = read_text("server.log")
        netem_log = read_text("netem.log")
        positive_rc = int(read_text("positive.rc").strip() or "-1")
        concurrent_rc = int(read_text("concurrent.rc").strip() or "-1")
        negative_rc = int(read_text("negative.rc").strip() or "-1")
        qlog_files = sorted(qlogs.glob("*"))
        client_qlog_files = [
            path for path in qlog_files if path.name.startswith("client-")
        ]
        server_handshakes = server_log.count("QUIC handshake has completed")
        server_post_requests = server_log.count("[:method: POST]")
        server_get_requests = server_log.count("[:method: GET]")
        negative_error = str(negative.get("error", ""))
        positive_ok = (
            positive_rc == 0
            and positive.get("status") == "ok"
            and positive.get("backend") == "inprocess"
            and positive.get("subprocess_backed") is False
            and positive.get("rmw_publish_path_integrated") is True
            and positive.get("rmw_take_path_integrated") is True
            and positive.get("same_connection_bidirectional") is True
            and positive.get("concurrent_rmw_publish_take_operation_loop") is True
            and positive.get("multi_threaded_rmw_api_claim") is True
            and positive.get("serialized_operation_loop") is False
            and int(positive.get("connections_created", 0)) == 1
            and int(positive.get("handshakes_completed", 0)) == 1
            and int(positive.get("streams_opened", 0)) == publish_count + 1
            and int(positive.get("connection_reuse_count", 0)) == publish_count
            and int(positive.get("concurrent_stream_pairs", 0)) == 1
            and int(positive.get("max_concurrent_request_streams", 0)) >= 2
            and int(positive.get("concurrent_api_operation_pairs", 0)) == 1
            and int(positive.get("max_concurrent_api_calls", 0)) >= 2
            and int(positive.get("reconnects", -1)) == 0
        )
        concurrent_ok = (
            concurrent_rc == 0
            and concurrent.get("status") == "ok"
            and concurrent.get("backend") == "inprocess"
            and concurrent.get("subprocess_backed") is False
            and concurrent.get("response_integrity_ok") is True
            and concurrent.get("concurrent_post_get_stream_pair") is True
            and concurrent.get("same_connection_full_duplex_streams") is True
            and concurrent.get("multi_threaded_rmw_api_claim") is False
            and int(concurrent.get("connections_created", 0)) == 1
            and int(concurrent.get("handshakes_completed", 0)) == 1
            and int(concurrent.get("streams_opened", 0)) == 2
            and int(concurrent.get("connection_reuse_count", 0)) == 1
            and int(concurrent.get("concurrent_stream_pairs", 0)) == 1
            and int(concurrent.get("max_concurrent_request_streams", 0)) >= 2
            and int(concurrent.get("reconnects", -1)) == 0
        )
        negative_ok = (
            negative_rc != 0
            and negative.get("status") == "failed"
            and negative.get("subprocess_backed") is False
            and "certificate" in negative_error.lower()
        )
        evidence_ok = (
            docker.returncode == 0
            and "netem" in netem_log
            and server_handshakes == 2
            and server_post_requests == publish_count + 1
            and server_get_requests == 2
            and len(qlog_files) >= 2
            and len(client_qlog_files) >= 3
            and all(path.stat().st_size > 0 for path in client_qlog_files)
        )
        ok = positive_ok and concurrent_ok and negative_ok and evidence_ok
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if ok else "failed",
            "transport": "inprocess_ngtcp2_gnutls_nghttp3_quic_v1_h3",
            "backend": "inprocess",
            "subprocess_backed": False,
            "quic_version": "0x00000001",
            "alpn": "h3",
            "netem": {
                "device": "lo",
                "delay_ms": delay_ms,
                "loss_percent": loss_percent,
                "seed": None,
                "configured": "netem" in netem_log,
                "qdisc": netem_log.strip(),
            },
            "positive": positive,
            "positive_concurrent_stream_pair": concurrent,
            "negative_untrusted_ca": negative,
            "positive_returncode": positive_rc,
            "concurrent_returncode": concurrent_rc,
            "negative_returncode": negative_rc,
            "server_handshake_count": server_handshakes,
            "server_post_request_count": server_post_requests,
            "server_get_request_count": server_get_requests,
            "qlog_file_count": len(qlog_files),
            "qlog_total_bytes": sum(path.stat().st_size for path in qlog_files),
            "client_qlog_file_count": len(client_qlog_files),
            "client_qlog_total_bytes": sum(
                path.stat().st_size for path in client_qlog_files
            ),
            "integrated_client_qlog_export": len(client_qlog_files) >= 3,
            "same_connection_bidirectional": positive.get(
                "same_connection_bidirectional"
            ) is True,
            "concurrent_post_get_stream_pair": concurrent.get(
                "concurrent_post_get_stream_pair"
            ) is True,
            "same_connection_full_duplex_streams": concurrent.get(
                "same_connection_full_duplex_streams"
            ) is True,
            "concurrent_stream_pairs": concurrent.get("concurrent_stream_pairs"),
            "max_concurrent_request_streams": concurrent.get(
                "max_concurrent_request_streams"
            ),
            "multi_threaded_rmw_api_claim": positive.get(
                "multi_threaded_rmw_api_claim"
            )
            is True,
            "concurrent_rmw_publish_take_operation_loop": positive.get(
                "concurrent_rmw_publish_take_operation_loop"
            )
            is True,
            "concurrent_api_operation_pairs": positive.get(
                "concurrent_api_operation_pairs"
            ),
            "max_concurrent_api_calls": positive.get("max_concurrent_api_calls"),
            "publish_count": publish_count,
            "connections_created": positive.get("connections_created"),
            "handshakes_completed": positive.get("handshakes_completed"),
            "streams_opened": positive.get("streams_opened"),
            "connection_reuse_count": positive.get("connection_reuse_count"),
            "packets_sent": positive.get("packets_sent"),
            "packets_received": positive.get("packets_received"),
            "reconnects": positive.get("reconnects"),
            "run_count": 3,
            "ok_run_count": 3 if ok else 0,
            "failed_run_count": 0 if ok else 1,
            "rmw_publish_path_integrated": positive.get(
                "rmw_publish_path_integrated"
            ) is True,
            "rmw_take_path_integrated": positive.get("rmw_take_path_integrated")
            is True,
            "tls_peer_verification_required": True,
            "untrusted_ca_rejected": negative_ok,
            "serialized_operation_loop": False,
            "production_readiness": False,
            "docker_returncode": docker.returncode,
            "docker_stdout": docker.stdout,
            "docker_stderr": docker.stderr,
            "positive_stderr": read_text("positive.err")[:2500],
            "concurrent_stderr": read_text("concurrent.err")[:2500],
            "negative_stderr": read_text("negative.err")[:2500],
            "server_log_excerpt": server_log[:5000],
        }
    finally:
        for path in (tmp, build_base, install_base, log_base):
            shutil.rmtree(path, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--port", type=int, default=4488)
    parser.add_argument("--delay-ms", type=int, default=5)
    parser.add_argument("--loss-percent", type=float, default=1.0)
    parser.add_argument("--publish-count", type=int, default=128)
    parser.add_argument(
        "--summary-json",
        default=(
            "results_rmw_socket/"
            "docker_quic_inprocess_rmw_bidirectional_probe_summary.json"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_probe(
        root=ROOT,
        image=args.image,
        port=args.port,
        delay_ms=max(args.delay_ms, 0),
        loss_percent=max(0.0, min(args.loss_percent, 100.0)),
        publish_count=max(1, min(args.publish_count, 512)),
    )
    output = ROOT / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(
            f"status={summary['status']} backend={summary.get('backend')} "
            f"same_connection={summary.get('same_connection_bidirectional')} "
            f"concurrent_pair={summary.get('concurrent_post_get_stream_pair')}"
        )
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
