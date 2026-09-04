"""Verify FleetRMW data-frame transfer over real QUIC/TLS/H3 across Docker netem."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleetqox.rmw_frame import encode_data_frame
from scripts.run_rmw_docker_shared_memory_probe import parse_last_json


SCHEMA_VERSION = "fleetrmw.docker_quic_netem_frame_probe.v1"
DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def write_frame(path: Path, frame_size: int) -> tuple[str, int]:
    frame = {
        "schema_version": "fleetrmw.data_frame.v1",
        "kind": "sidecar_packet_frame",
        "route": {
            "robot_id": "robot_quic_netem_0001",
            "topic": "/fleetqox/quic_netem_frame",
        },
        "sample_envelope": {
            "robot_id": "robot_quic_netem_0001",
            "topic": "/fleetqox/quic_netem_frame",
            "publisher_id": "fpub-quic-netem-frame-0001",
            "source_sequence_number": 29,
            "source_timestamp_ns": 29000000,
        },
        "serialized_payload": {
            "encoding": "hex",
            "size": 22,
            "data": "666c656574716f782d717569632d6e6574656d2d7631",
        },
    }
    encoded = encode_data_frame(frame, target_size=frame_size)
    path.write_bytes(encoded)
    return hashlib.sha256(encoded).hexdigest(), len(encoded)


def _series(values: list[int]) -> dict[str, Any]:
    return {
        "sample_count": len(values),
        "first": values[0] if values else None,
        "last": values[-1] if values else None,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def _integer_values(pattern: str, text: str) -> list[int]:
    return [int(match.group(1)) for match in re.finditer(pattern, text)]


def _rtt_values(field: str, text: str) -> list[int]:
    values: list[int] = []
    for line in text.splitlines():
        if "latest_rtt=" not in line:
            continue
        match = re.search(rf"\b{re.escape(field)}=(\d+)", line)
        if match:
            values.append(int(match.group(1)))
    return values


def parse_ngtcp2_path_telemetry(*logs: str) -> dict[str, Any]:
    text = "\n".join(log for log in logs if log)
    sent_packet_bytes = _integer_values(r"(?m)^Sent packet:.*?\s(\d+) bytes\b", text)
    received_packet_bytes = _integer_values(r"(?m)^Received packet:.*?\s(\d+) bytes\b", text)

    return {
        "source": "ngtcp2_gtls_logs",
        "log_byte_count": len(text.encode("utf-8", errors="replace")),
        "quic_v1_negotiated_observed": "negotiated version is 0x00000001" in text,
        "ecn_capable_observed": "path is ECN capable" in text,
        "sent_packet_log_count": len(sent_packet_bytes),
        "received_packet_log_count": len(received_packet_bytes),
        "sent_packet_bytes_logged": sum(sent_packet_bytes),
        "received_packet_bytes_logged": sum(received_packet_bytes),
        "packet_tx_log_count": len(re.findall(r"\bpkt tx pkn=", text)),
        "packet_rx_log_count": len(re.findall(r"\bpkt rx pkn=", text)),
        "loss_detection_timer_log_count": len(re.findall(r"\bloss_detection_timer=", text)),
        "rtt_raw": {
            "latest": _series(_rtt_values("latest_rtt", text)),
            "min": _series(_rtt_values("min_rtt", text)),
            "smoothed": _series(_rtt_values("smoothed_rtt", text)),
            "variation": _series(_rtt_values("rttvar", text)),
        },
        "congestion_raw": {
            "cwnd_bytes": _series(_integer_values(r"\bcwnd=(\d+)", text)),
            "target_cwnd_bytes": _series(_integer_values(r"\btarget_cwnd=(\d+)", text)),
            "max_delivery_rate_per_s": _series(
                _integer_values(r"\bmax_delivery_rate_sec=(\d+)", text)
            ),
        },
    }


def parse_netem_qdisc_counters(qdisc: str) -> dict[str, Any]:
    counters: dict[str, Any] = {
        "sent_bytes": None,
        "sent_packets": None,
        "dropped_packets": None,
        "overlimits": None,
        "requeues": None,
        "backlog_bytes": None,
        "backlog_packets": None,
        "backlog_requeues": None,
    }
    sent = re.search(
        r"\bSent\s+(\d+)\s+bytes\s+(\d+)\s+pkt\s+"
        r"\(dropped\s+(\d+),\s+overlimits\s+(\d+)\s+requeues\s+(\d+)\)",
        qdisc,
    )
    if sent:
        counters.update(
            {
                "sent_bytes": int(sent.group(1)),
                "sent_packets": int(sent.group(2)),
                "dropped_packets": int(sent.group(3)),
                "overlimits": int(sent.group(4)),
                "requeues": int(sent.group(5)),
            }
        )
    backlog = re.search(r"\bbacklog\s+(\d+)b\s+(\d+)p\s+requeues\s+(\d+)", qdisc)
    if backlog:
        counters.update(
            {
                "backlog_bytes": int(backlog.group(1)),
                "backlog_packets": int(backlog.group(2)),
                "backlog_requeues": int(backlog.group(3)),
            }
        )
    return counters


def run_probe(
    *,
    root: Path,
    image: str,
    frame_size: int,
    port: int,
    delay_ms: int,
    jitter_ms: int,
    loss_percent: float,
) -> dict[str, Any]:
    suffix = str(os.getpid())
    network = f"fleetrmw-quic-netem-{suffix}"
    server_name = f"fleetrmw-quic-netem-server-{suffix}"
    tmp = root / f".tmp_fleetrmw_quic_netem_{suffix}"
    htdocs = tmp / "htdocs"
    download = tmp / "download"
    qlogs = tmp / "qlogs"
    certs = tmp / "certs"
    server_log_path = tmp / "server.log"
    build_base = root / ".tmp_fleetrmw_quic_netem_v2_build"
    install_base = root / ".tmp_fleetrmw_quic_netem_v2_install"
    log_base = root / ".tmp_fleetrmw_quic_netem_v2_log"
    frame_probe = (
        "/work/.tmp_fleetrmw_quic_netem_v2_install/rmw_fleetqox_cpp/lib/"
        "rmw_fleetqox_cpp/fleetrmw_frame_probe"
    )
    try:
        for directory in (htdocs, download, qlogs, certs):
            directory.mkdir(parents=True, exist_ok=True)
        expected_sha256, encoded_size = write_frame(htdocs / "fleetqox_frame.frmw", frame_size)

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
                "rm -rf /work/.tmp_fleetrmw_quic_netem_v2_build "
                "/work/.tmp_fleetrmw_quic_netem_v2_install "
                "/work/.tmp_fleetrmw_quic_netem_v2_log && "
                "colcon --log-base /work/.tmp_fleetrmw_quic_netem_v2_log build "
                "--base-paths ros2_ws/src --packages-select rmw_fleetqox_cpp "
                "--build-base /work/.tmp_fleetrmw_quic_netem_v2_build "
                "--install-base /work/.tmp_fleetrmw_quic_netem_v2_install "
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

        network_create = run(["docker", "network", "create", network])
        if network_create.returncode != 0:
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "failed",
                "stage": "network_create",
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
                f"-subj /CN=localhost -days 1 >/tmp/fleetrmw_quic_netem_cert.out 2>&1 && "
                f"/usr/sbin/gtlsserver 0.0.0.0 {port} "
                f"/work/{certs.relative_to(root)}/server.key "
                f"/work/{certs.relative_to(root)}/server.crt "
                f"-d /work/{htdocs.relative_to(root)} "
                f"--qlog-dir /work/{qlogs.relative_to(root)} "
                "--timeout=10s --handshake-timeout=5s --no-quic-dump --no-http-dump "
                f"> /work/{server_log_path.relative_to(root)} 2>&1",
            ]
        )
        if server.returncode != 0:
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "failed",
                "stage": "server_start",
                "stdout": server.stdout,
                "stderr": server.stderr,
            }
        time.sleep(1.0)

        client_command = (
            f"set -e; "
            f"tc qdisc replace dev eth0 root netem delay {delay_ms}ms {jitter_ms}ms "
            f"loss {loss_percent}% ; "
            f"tc -s qdisc show dev eth0 > "
            f"/work/{tmp.relative_to(root)}/client_netem_status_before.txt; "
            "set +e; "
            f"/usr/bin/gtlsclient {server_name} {port} "
            f"https://localhost:{port}/fleetqox_frame.frmw "
            f"--download /work/{download.relative_to(root)} "
            "--exit-on-all-streams-close --timeout=8s --sni=localhost "
            "--no-quic-dump --no-http-dump "
            f"> /work/{tmp.relative_to(root)}/client.log 2>&1; "
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
        downloaded = download / "fleetqox_frame.frmw"
        downloaded_bytes = downloaded.read_bytes() if downloaded.exists() else b""
        downloaded_sha256 = hashlib.sha256(downloaded_bytes).hexdigest() if downloaded_bytes else ""
        decode = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "bash",
                "-i",
                "-v",
                f"{root}:/work",
                "-w",
                "/work",
                image,
                "-lc",
                frame_probe,
            ],
            input=downloaded_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        decoded = {}
        try:
            decoded = json.loads(decode.stdout.decode("utf-8"))
        except json.JSONDecodeError:
            decoded = {}
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
        qlog_files = sorted(qlogs.glob("*"))
        client_handshake = "QUIC handshake has completed" in client_log
        server_handshake = "QUIC handshake has completed" in server_log
        alpn_h3 = "Negotiated ALPN is h3" in client_log and "Negotiated ALPN is h3" in server_log
        payload_ok = downloaded_sha256 == expected_sha256 and len(downloaded_bytes) == encoded_size
        decoded_ok = (
            decode.returncode == 0
            and decoded.get("status") == "decoded"
            and decoded.get("robot_id") == "robot_quic_netem_0001"
            and decoded.get("topic") == "/fleetqox/quic_netem_frame"
            and decoded.get("publisher_id") == "fpub-quic-netem-frame-0001"
            and decoded.get("source_sequence_number") == 29
            and decoded.get("source_timestamp_ns") == 29000000
        )
        netem_applied = "netem" in netem_status and f"delay {delay_ms}ms" in netem_status
        ok = (
            client.returncode == 0
            and client_handshake
            and server_handshake
            and alpn_h3
            and payload_ok
            and decoded_ok
            and netem_applied
            and telemetry_ok
            and len(qlog_files) >= 1
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if ok else "failed",
            "transport": "ngtcp2_gtls_quic_tls_h3",
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
            "tls_handshake_complete": client_handshake and server_handshake,
            "fleet_frame_schema": "fleetrmw.data_frame.v1",
            "fleet_frame_bytes": encoded_size,
            "downloaded_size": len(downloaded_bytes),
            "frame_sha256": expected_sha256,
            "downloaded_sha256": downloaded_sha256,
            "payload_integrity_ok": payload_ok,
            "decoded_frame_ok": decoded_ok,
            "decoded_frame": decoded,
            "qlog_file_count": len(qlog_files),
            "qlog_total_bytes": sum(path.stat().st_size for path in qlog_files),
            "not_bare_udp_transport": True,
            "rmw_integrated_backend": False,
            "client_returncode": client.returncode,
            "frame_probe_returncode": decode.returncode,
            "frame_probe_stderr": decode.stderr.decode("utf-8", errors="replace"),
            "client_stdout": client.stdout,
            "client_stderr": client.stderr,
            "client_log_excerpt": client_log[:2500],
            "server_log_excerpt": server_log[:2500],
        }
    finally:
        subprocess.run(["docker", "rm", "-f", server_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        subprocess.run(["docker", "network", "rm", network], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        for path in (tmp, build_base, install_base, log_base):
            shutil.rmtree(path, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--frame-size", type=int, default=4096)
    parser.add_argument("--port", type=int, default=4445)
    parser.add_argument("--delay-ms", type=int, default=20)
    parser.add_argument("--jitter-ms", type=int, default=5)
    parser.add_argument("--loss-percent", type=float, default=0.0)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_quic_netem_frame_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    summary = run_probe(
        root=root,
        image=args.image,
        frame_size=max(args.frame_size, 1),
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
        print(f"status={summary['status']} frame_bytes={summary.get('fleet_frame_bytes')}")
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
