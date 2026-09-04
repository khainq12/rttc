"""Verify rmw_publish QUIC/TLS/H3 gateway upload across Docker netem."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_rmw_docker_quic_gateway_publish_probe import (
    parse_server_body_bytes,
    parse_server_body_sizes,
    parse_server_content_length,
    parse_server_content_lengths,
)
from scripts.run_rmw_docker_quic_netem_frame_probe import (
    parse_netem_qdisc_counters,
    parse_ngtcp2_path_telemetry,
)
from scripts.run_rmw_docker_shared_memory_probe import parse_last_json


SCHEMA_VERSION = "fleetrmw.docker_quic_gateway_netem_publish_probe.v1"
DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def run_probe(
    *,
    root: Path,
    image: str,
    port: int,
    delay_ms: int,
    jitter_ms: int,
    loss_percent: float,
    async_gateway: bool = False,
    schema_version: str = SCHEMA_VERSION,
    probe_executable: str = "fleetrmw_quic_gateway_publish_probe",
) -> dict[str, Any]:
    suffix = str(os.getpid())
    mode = "async" if async_gateway else "sync"
    network = f"fleetrmw-quic-gateway-netem-{mode}-{suffix}"
    server_name = f"fleetrmw-quic-gateway-netem-{mode}-server-{suffix}"
    tmp = root / f".tmp_fleetrmw_quic_gateway_netem_publish_v2_{mode}_{suffix}"
    htdocs = tmp / "htdocs"
    qlogs = tmp / "qlogs"
    certs = tmp / "certs"
    server_log_path = tmp / "server.log"
    build_base = root / f".tmp_fleetrmw_quic_gateway_netem_publish_v2_{mode}_build"
    install_base = root / f".tmp_fleetrmw_quic_gateway_netem_publish_v2_{mode}_install"
    log_base = root / f".tmp_fleetrmw_quic_gateway_netem_publish_v2_{mode}_log"
    probe = (
        f"/work/.tmp_fleetrmw_quic_gateway_netem_publish_v2_{mode}_install/"
        f"rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/{probe_executable}"
    )
    try:
        for directory in (htdocs, qlogs, certs):
            directory.mkdir(parents=True, exist_ok=True)

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
                f"source /opt/ros/jazzy/setup.bash && "
                f"rm -rf /work/{build_base.relative_to(root)} "
                f"/work/{install_base.relative_to(root)} "
                f"/work/{log_base.relative_to(root)} && "
                f"colcon --log-base /work/{log_base.relative_to(root)} build "
                f"--base-paths ros2_ws/src --packages-select rmw_fleetqox_cpp "
                f"--build-base /work/{build_base.relative_to(root)} "
                f"--install-base /work/{install_base.relative_to(root)} "
                f"--cmake-args -DCMAKE_BUILD_TYPE=Release",
            ]
        )
        if build.returncode != 0:
            return {
                "schema_version": schema_version,
                "status": "failed",
                "stage": "build",
                "async_gateway": async_gateway,
                "stdout": build.stdout,
                "stderr": build.stderr,
            }

        network_create = run(["docker", "network", "create", network])
        if network_create.returncode != 0:
            return {
                "schema_version": schema_version,
                "status": "failed",
                "stage": "network_create",
                "async_gateway": async_gateway,
                "stderr": network_create.stderr,
            }

        server = run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                server_name,
                "--network",
                network,
                "--entrypoint",
                "bash",
                "-v",
                f"{root}:/work",
                "-w",
                "/work",
                image,
                "-lc",
                f"openssl req -x509 -newkey rsa:2048 -nodes "
                f"-keyout /work/{certs.relative_to(root)}/server.key "
                f"-out /work/{certs.relative_to(root)}/server.crt "
                f"-subj /CN=localhost -days 1 >/tmp/fleetrmw_quic_gateway_cert.out 2>&1 && "
                f"/usr/sbin/gtlsserver 0.0.0.0 {port} "
                f"/work/{certs.relative_to(root)}/server.key "
                f"/work/{certs.relative_to(root)}/server.crt "
                f"-d /work/{htdocs.relative_to(root)} "
                f"--qlog-dir /work/{qlogs.relative_to(root)} "
                "--timeout=10s --handshake-timeout=5s --no-quic-dump "
                f"> /work/{server_log_path.relative_to(root)} 2>&1",
            ]
        )
        if server.returncode != 0:
            return {
                "schema_version": schema_version,
                "status": "failed",
                "stage": "server_start",
                "async_gateway": async_gateway,
                "stdout": server.stdout,
                "stderr": server.stderr,
            }
        time.sleep(1.0)

        async_env = (
            "FLEETQOX_RMW_QUIC_GATEWAY_ASYNC=1 "
            "FLEETQOX_RMW_QUIC_GATEWAY_MAX_QUEUE_FRAMES=8 "
            if async_gateway
            else ""
        )
        client_command = (
            "set -e; "
            f"tc qdisc replace dev eth0 root netem delay {delay_ms}ms {jitter_ms}ms "
            f"loss {loss_percent}% ; "
            f"tc -s qdisc show dev eth0 > "
            f"/work/{tmp.relative_to(root)}/client_netem_status_before.txt; "
            f"source /work/{install_base.relative_to(root)}/setup.bash; "
            "set +e; "
            "FLEETQOX_RMW_REMOTE_TRANSPORT=quic_gateway "
            f"FLEETQOX_RMW_QUIC_GATEWAY={server_name}:{port} "
            f"FLEETQOX_RMW_QUIC_URI=https://localhost:{port}/fleetrmw_publish "
            "FLEETQOX_RMW_QUIC_SNI=localhost "
            f"FLEETQOX_RMW_QUIC_QLOG_DIR=/work/{qlogs.relative_to(root)} "
            f"FLEETQOX_RMW_QUIC_LOG=/work/{tmp.relative_to(root)}/client.log "
            f"{async_env}"
            f"{probe} > /work/{tmp.relative_to(root)}/probe.log "
            f"2> /work/{tmp.relative_to(root)}/probe.err; "
            "client_rc=$?; "
            "set -e; "
            f"tc -s qdisc show dev eth0 > "
            f"/work/{tmp.relative_to(root)}/client_netem_status_after.txt; "
            "exit $client_rc"
        )
        client = run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                network,
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
                client_command,
            ]
        )
        subprocess.run(
            ["docker", "rm", "-f", server_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

        probe_log = (tmp / "probe.log").read_text(errors="replace") if (tmp / "probe.log").exists() else ""
        probe_err = (tmp / "probe.err").read_text(errors="replace") if (tmp / "probe.err").exists() else ""
        client_log = (tmp / "client.log").read_text(errors="replace") if (tmp / "client.log").exists() else ""
        server_log = server_log_path.read_text(errors="replace") if server_log_path.exists() else ""
        netem_status_before = (
            tmp / "client_netem_status_before.txt"
        ).read_text(errors="replace") if (tmp / "client_netem_status_before.txt").exists() else ""
        netem_status_after = (
            tmp / "client_netem_status_after.txt"
        ).read_text(errors="replace") if (tmp / "client_netem_status_after.txt").exists() else ""
        netem_status = netem_status_after or netem_status_before
        path_telemetry = parse_ngtcp2_path_telemetry(client_log, server_log)
        telemetry_ok = (
            path_telemetry["quic_v1_negotiated_observed"]
            and path_telemetry["sent_packet_log_count"] > 0
            and path_telemetry["received_packet_log_count"] > 0
            and path_telemetry["rtt_raw"]["latest"]["sample_count"] > 0
        )
        probe_json = parse_last_json(probe_log)
        qlog_files = sorted(qlogs.glob("*"))
        server_body_sizes = parse_server_body_sizes(server_log)
        server_content_lengths = parse_server_content_lengths(server_log)
        server_body_bytes = parse_server_body_bytes(server_log)
        server_content_length = parse_server_content_length(server_log)
        server_body_total_bytes = sum(server_body_sizes)
        server_content_length_total = sum(server_content_lengths)
        client_handshake = "QUIC handshake has completed" in client_log
        server_handshake = "QUIC handshake has completed" in server_log
        alpn_h3 = "Negotiated ALPN is h3" in client_log and "Negotiated ALPN is h3" in server_log
        quic_frames_sent = int(probe_json.get("quic_gateway_frames_sent", 0))
        quic_bytes_sent = int(probe_json.get("quic_gateway_bytes_sent", 0))
        server_payload_ok = (
            server_body_total_bytes == quic_bytes_sent
            and server_content_length_total == quic_bytes_sent
            and quic_bytes_sent > 0
            and len(server_body_sizes) >= max(1, quic_frames_sent)
        )
        async_worker_ok = (
            not async_gateway
            or (
                probe_json.get("quic_gateway_async_enabled") is True
                and int(probe_json.get("quic_gateway_frames_enqueued", 0)) >= max(
                    1,
                    quic_frames_sent,
                )
                and int(probe_json.get("quic_gateway_frames_failed", 1)) == 0
                and int(probe_json.get("quic_gateway_frames_dropped", 1)) == 0
                and int(probe_json.get("quic_gateway_queue_depth", 1)) == 0
                and probe_json.get("publish_returned_after_enqueue") is True
            )
        )
        netem_applied = "netem" in netem_status and f"delay {delay_ms}ms" in netem_status
        ok = (
            client.returncode == 0
            and probe_json.get("status") == "ok"
            and probe_json.get("rmw_publish_path_integrated") is True
            and async_worker_ok
            and client_handshake
            and server_handshake
            and alpn_h3
            and server_payload_ok
            and netem_applied
            and telemetry_ok
            and len(qlog_files) >= 1
        )
        return {
            "schema_version": schema_version,
            "status": "ok" if ok else "failed",
            "transport": (
                "ngtcp2_gtls_quic_tls_h3_post_gateway_async_worker_netem"
                if async_gateway
                else "ngtcp2_gtls_quic_tls_h3_post_gateway"
            ),
            "quic_version": "0x00000001",
            "alpn": "h3" if alpn_h3 else "",
            "docker_network": network,
            "netem": {
                "delay_ms": delay_ms,
                "jitter_ms": jitter_ms,
                "loss_percent": loss_percent,
                "status": "applied" if netem_applied else "missing_or_unverified",
                "qdisc_before": netem_status_before,
                "qdisc_after": netem_status_after,
                "qdisc": netem_status,
                "counters_before": parse_netem_qdisc_counters(netem_status_before),
                "counters_after": parse_netem_qdisc_counters(netem_status_after),
            },
            "path_telemetry": path_telemetry,
            "rmw_publish_path_integrated": probe_json.get("rmw_publish_path_integrated") is True,
            "async_gateway": async_gateway,
            "async_worker_queue_observed": async_worker_ok,
            "subprocess_backed": True,
            "production_quic_backend": False,
            "full_bidirectional_quic_backend": False,
            "probe": probe_json,
            "server_body_bytes": server_body_bytes,
            "server_content_length": server_content_length,
            "server_body_count": len(server_body_sizes),
            "server_content_length_count": len(server_content_lengths),
            "server_body_total_bytes": server_body_total_bytes,
            "server_content_length_total": server_content_length_total,
            "server_payload_matches_rmw_frame_bytes": server_payload_ok,
            "tls_handshake_complete": client_handshake and server_handshake,
            "qlog_file_count": len(qlog_files),
            "qlog_total_bytes": sum(path.stat().st_size for path in qlog_files),
            "client_returncode": client.returncode,
            "client_stdout": client.stdout,
            "client_stderr": client.stderr,
            "probe_stderr": probe_err,
            "client_log_excerpt": client_log[:2500],
            "server_log_excerpt": server_log[:2500],
        }
    finally:
        subprocess.run(
            ["docker", "rm", "-f", server_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        subprocess.run(
            ["docker", "network", "rm", network],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        for path in (tmp, build_base, install_base, log_base):
            shutil.rmtree(path, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--port", type=int, default=4447)
    parser.add_argument("--delay-ms", type=int, default=20)
    parser.add_argument("--jitter-ms", type=int, default=5)
    parser.add_argument("--loss-percent", type=float, default=0.0)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_quic_gateway_netem_publish_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    summary = run_probe(
        root=root,
        image=args.image,
        port=args.port,
        delay_ms=max(args.delay_ms, 0),
        jitter_ms=max(args.jitter_ms, 0),
        loss_percent=max(args.loss_percent, 0.0),
    )
    output = root / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(f"status={summary['status']} transport={summary.get('transport')}")
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
