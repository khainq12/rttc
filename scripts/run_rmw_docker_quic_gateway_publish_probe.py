"""Verify rmw_publish can send FleetRMW frames through a real QUIC/TLS/H3 gateway."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_rmw_docker_shared_memory_probe import parse_last_json


SCHEMA_VERSION = "fleetrmw.docker_quic_gateway_publish_probe.v1"
DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def parse_server_body_bytes(server_log: str) -> int:
    sizes = parse_server_body_sizes(server_log)
    return max(sizes) if sizes else 0


def parse_server_content_length(server_log: str) -> int:
    sizes = parse_server_content_lengths(server_log)
    return max(sizes) if sizes else 0


def parse_server_body_sizes(server_log: str) -> list[int]:
    return [int(match.group(1)) for match in re.finditer(r"body\s+(\d+)\s+bytes", server_log)]


def parse_server_content_lengths(server_log: str) -> list[int]:
    return [
        int(match.group(1))
        for match in re.finditer(r"\[content-length:\s*(\d+)\]", server_log)
    ]


def count_log_patterns(log: str, patterns: tuple[str, ...]) -> int:
    return sum(len(re.findall(pattern, log, flags=re.IGNORECASE)) for pattern in patterns)


def parse_quic_session_reuse_telemetry(*logs: str) -> dict[str, Any]:
    """Extract conservative QUIC session-reuse and 0-RTT evidence from ngtcp2 logs.

    File reads/writes prove plumbing.  A 0RTT packet proves an attempt.  The
    production 0-RTT/session-resumption claim stays false unless the log carries
    explicit accepted/resumed evidence.
    """

    combined = "\n".join(str(log or "") for log in logs)
    token_file_read_count = count_log_patterns(
        combined,
        (r"\bReading token file\b",),
    )
    token_file_missing_count = count_log_patterns(
        combined,
        (r"\bCould not read token (?:file|in)\b",),
    )
    token_file_write_count = count_log_patterns(
        combined,
        (r"\b(?:Writing|Wrote|Saving|Saved) token file\b",),
    )
    session_file_read_count = count_log_patterns(
        combined,
        (
            r"\bReading (?:TLS )?session file\b",
            r"\bReading TLS session\b",
        ),
    )
    session_file_missing_count = count_log_patterns(
        combined,
        (
            r"\bCould not read (?:TLS )?session (?:file|in)\b",
            r"\bCould not read TLS session\b",
        ),
    )
    session_file_write_count = count_log_patterns(
        combined,
        (
            r"\b(?:Writing|Wrote|Saving|Saved) (?:TLS )?session file\b",
            r"\b(?:Writing|Wrote|Saving|Saved) TLS session\b",
        ),
    )
    transport_parameters_file_read_count = count_log_patterns(
        combined,
        (
            r"\bReading transport[- ]parameters? file\b",
            r"\bReading tp file\b",
        ),
    )
    transport_parameters_file_missing_count = count_log_patterns(
        combined,
        (
            r"\bCould not read transport[- ]parameters? (?:file|in)\b",
            r"\bCould not read tp (?:file|in)\b",
        ),
    )
    transport_parameters_file_write_count = count_log_patterns(
        combined,
        (
            r"\b(?:Writing|Wrote|Saving|Saved) transport[- ]parameters? file\b",
            r"\b(?:Writing|Wrote|Saving|Saved) tp file\b",
        ),
    )
    zero_rtt_tx_packet_count = count_log_patterns(
        combined,
        (r"\btype=0RTT\b", r"\btype=0-RTT\b"),
    )
    zero_rtt_tx_frame_count = count_log_patterns(
        combined,
        (r"\b0RTT\s+[A-Z_]+\(", r"\b0-RTT\s+[A-Z_]+\("),
    )
    zero_rtt_accepted_observed = count_log_patterns(
        combined,
        (
            r"\bearly data accepted\b",
            r"\b0[- ]?RTT accepted\b",
            r"\bzero[- ]rtt accepted\b",
        ),
    ) > 0
    zero_rtt_rejected_observed = count_log_patterns(
        combined,
        (
            r"\bearly data rejected\b",
            r"\b0[- ]?RTT rejected\b",
            r"\bzero[- ]rtt rejected\b",
        ),
    ) > 0
    explicit_resumption_observed = count_log_patterns(
        combined,
        (
            r"\bsession resumption\b",
            r"\bsession resumed\b",
            r"\bresumed session\b",
            r"\bTLS session resumed\b",
        ),
    ) > 0
    zero_rtt_packet_observed = zero_rtt_tx_packet_count > 0 or zero_rtt_tx_frame_count > 0
    return {
        "session_file_read_count": session_file_read_count,
        "session_file_missing_count": session_file_missing_count,
        "session_file_write_count": session_file_write_count,
        "transport_parameters_file_read_count": transport_parameters_file_read_count,
        "transport_parameters_file_missing_count": transport_parameters_file_missing_count,
        "transport_parameters_file_write_count": transport_parameters_file_write_count,
        "token_file_read_count": token_file_read_count,
        "token_file_missing_count": token_file_missing_count,
        "token_file_write_count": token_file_write_count,
        "session_resumption_attempted_observed": (
            session_file_read_count > session_file_missing_count
            or token_file_read_count > token_file_missing_count
            or zero_rtt_packet_observed
        ),
        "session_resumption_observed": (
            explicit_resumption_observed or zero_rtt_accepted_observed
        ),
        "zero_rtt_packet_observed": zero_rtt_packet_observed,
        "zero_rtt_tx_packet_count": zero_rtt_tx_packet_count,
        "zero_rtt_tx_frame_count": zero_rtt_tx_frame_count,
        "zero_rtt_accepted_observed": zero_rtt_accepted_observed,
        "zero_rtt_rejected_observed": zero_rtt_rejected_observed,
    }


def run_probe(
    *,
    root: Path,
    image: str,
    port: int,
    async_gateway: bool = False,
    schema_version: str = SCHEMA_VERSION,
    probe_executable: str = "fleetrmw_quic_gateway_publish_probe",
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    suffix = str(os.getpid())
    mode = "async" if async_gateway else "sync"
    tmp = root / f".tmp_fleetrmw_quic_gateway_publish_v2_{mode}_{suffix}"
    htdocs = tmp / "htdocs"
    qlogs = tmp / "qlogs"
    certs = tmp / "certs"
    build_base = root / f".tmp_fleetrmw_quic_gateway_publish_v2_{mode}_build"
    install_base = root / f".tmp_fleetrmw_quic_gateway_publish_v2_{mode}_install"
    log_base = root / f".tmp_fleetrmw_quic_gateway_publish_v2_{mode}_log"
    probe = (
        f"/work/.tmp_fleetrmw_quic_gateway_publish_v2_{mode}_install/rmw_fleetqox_cpp/lib/"
        f"rmw_fleetqox_cpp/{probe_executable}"
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

        async_env = (
            "FLEETQOX_RMW_QUIC_GATEWAY_ASYNC=1 "
            "FLEETQOX_RMW_QUIC_GATEWAY_MAX_QUEUE_FRAMES=8 "
            if async_gateway
            else ""
        )
        extra_env_text = "".join(
            f"{key}={shlex.quote(value)} "
            for key, value in sorted((extra_env or {}).items())
        )
        command = (
            "set -e; "
            f"openssl req -x509 -newkey rsa:2048 -nodes "
            f"-keyout /work/{certs.relative_to(root)}/server.key "
            f"-out /work/{certs.relative_to(root)}/server.crt "
            f"-subj /CN=localhost -days 1 >/work/{tmp.relative_to(root)}/cert.log 2>&1; "
            f"/usr/sbin/gtlsserver 127.0.0.1 {port} "
            f"/work/{certs.relative_to(root)}/server.key "
            f"/work/{certs.relative_to(root)}/server.crt "
            f"-d /work/{htdocs.relative_to(root)} "
            f"--qlog-dir /work/{qlogs.relative_to(root)} "
            "--timeout=10s --handshake-timeout=5s --no-quic-dump "
            f"> /work/{tmp.relative_to(root)}/server.log 2>&1 & "
            "server_pid=$!; "
            "sleep 0.5; "
            f"source /work/{install_base.relative_to(root)}/setup.bash; "
            "set +e; "
            "FLEETQOX_RMW_REMOTE_TRANSPORT=quic_gateway "
            f"FLEETQOX_RMW_QUIC_GATEWAY=127.0.0.1:{port} "
            f"FLEETQOX_RMW_QUIC_URI=https://localhost:{port}/fleetrmw_publish "
            "FLEETQOX_RMW_QUIC_SNI=localhost "
            f"FLEETQOX_RMW_QUIC_QLOG_DIR=/work/{qlogs.relative_to(root)} "
            f"FLEETQOX_RMW_QUIC_LOG=/work/{tmp.relative_to(root)}/client.log "
            f"{async_env}"
            f"{extra_env_text}"
            f"{probe} > /work/{tmp.relative_to(root)}/probe.log "
            f"2> /work/{tmp.relative_to(root)}/probe.err; "
            "probe_rc=$?; "
            "sleep 0.5; "
            "kill ${server_pid} >/dev/null 2>&1 || true; "
            "wait ${server_pid} >/dev/null 2>&1 || true; "
            "exit ${probe_rc}"
        )
        docker = run(
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
                command,
            ]
        )
        probe_log = (tmp / "probe.log").read_text(errors="replace") if (tmp / "probe.log").exists() else ""
        probe_err = (tmp / "probe.err").read_text(errors="replace") if (tmp / "probe.err").exists() else ""
        client_log = (tmp / "client.log").read_text(errors="replace") if (tmp / "client.log").exists() else ""
        server_log = (tmp / "server.log").read_text(errors="replace") if (tmp / "server.log").exists() else ""
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
        session_reuse_telemetry = parse_quic_session_reuse_telemetry(
            client_log,
            server_log,
        )
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
                and int(probe_json.get("quic_gateway_frames_enqueued", 0)) >= 1
                and int(probe_json.get("quic_gateway_frames_failed", 1)) == 0
                and int(probe_json.get("quic_gateway_frames_dropped", 1)) == 0
                and int(probe_json.get("quic_gateway_queue_depth", 1)) == 0
                and probe_json.get("publish_returned_after_enqueue") is True
            )
        )
        ok = (
            docker.returncode == 0
            and probe_json.get("status") == "ok"
            and probe_json.get("rmw_publish_path_integrated") is True
            and probe_json.get("subprocess_backed") is True
            and async_worker_ok
            and client_handshake
            and server_handshake
            and alpn_h3
            and server_payload_ok
            and len(qlog_files) >= 1
        )
        return {
            "schema_version": schema_version,
            "status": "ok" if ok else "failed",
            "transport": (
                "ngtcp2_gtls_quic_tls_h3_post_gateway_async_worker"
                if async_gateway
                else "ngtcp2_gtls_quic_tls_h3_post_gateway"
            ),
            "quic_version": "0x00000001",
            "alpn": "h3" if alpn_h3 else "",
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
            **session_reuse_telemetry,
            "docker_returncode": docker.returncode,
            "docker_stdout": docker.stdout,
            "docker_stderr": docker.stderr,
            "probe_stderr": probe_err,
            "client_log_excerpt": client_log[:2500],
            "server_log_excerpt": server_log[:2500],
        }
    finally:
        for path in (tmp, build_base, install_base, log_base):
            shutil.rmtree(path, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--port", type=int, default=4446)
    parser.add_argument("--async-gateway", action="store_true")
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_quic_gateway_publish_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    summary = run_probe(
        root=root,
        image=args.image,
        port=args.port,
        async_gateway=args.async_gateway,
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
