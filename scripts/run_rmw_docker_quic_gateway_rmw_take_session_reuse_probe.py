"""Verify QUIC gateway RMW take-path session-file reuse plumbing.

The probe runs the opt-in rmw_take_serialized_message QUIC GET smoke repeatedly
against one gtlsserver while sharing ngtcp2/GnuTLS session, transport-parameter,
and token files between client invocations.  It proves file plumbing and repeat
download compatibility, not 0-RTT data or a production full-duplex backend.
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

from scripts.run_rmw_docker_quic_gateway_rmw_take_probe import (
    DEFAULT_IMAGE,
    expected_frame_bytes,
    run,
)
from scripts.run_rmw_docker_quic_gateway_publish_probe import (  # noqa: E402
    parse_quic_session_reuse_telemetry,
)
from scripts.run_rmw_docker_shared_memory_probe import parse_last_json


SCHEMA_VERSION = "fleetrmw.docker_quic_gateway_rmw_take_session_reuse_probe.v1"


def file_status(path: Path) -> dict[str, Any]:
    return {
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
    }


def run_session_probe(
    *,
    root: Path,
    image: str,
    port: int,
    downloads: int = 2,
) -> dict[str, Any]:
    requested_downloads = max(downloads, 2)
    suffix = str(os.getpid())
    tmp = root / f".tmp_fleetrmw_quic_gateway_rmw_take_session_{suffix}"
    htdocs = tmp / "htdocs"
    qlogs = tmp / "qlogs"
    certs = tmp / "certs"
    build_base = root / ".tmp_fleetrmw_quic_gateway_rmw_take_session_v2_build"
    install_base = root / ".tmp_fleetrmw_quic_gateway_rmw_take_session_v2_install"
    log_base = root / ".tmp_fleetrmw_quic_gateway_rmw_take_session_v2_log"
    session_file = tmp / "gtlsclient-session.bin"
    tp_file = tmp / "gtlsclient-transport-params.bin"
    token_file = tmp / "gtlsclient-token.bin"
    probe = (
        "/work/.tmp_fleetrmw_quic_gateway_rmw_take_session_v2_install/rmw_fleetqox_cpp/lib/"
        "rmw_fleetqox_cpp/fleetrmw_quic_gateway_rmw_take_probe"
    )
    try:
        for directory in (htdocs, qlogs, certs):
            directory.mkdir(parents=True, exist_ok=True)
        frame_path = htdocs / "fleetqox_quic_gateway_rmw_take.frmw"
        frame_path.write_bytes(expected_frame_bytes())

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

        probe_command = (
            "FLEETQOX_RMW_REMOTE_TRANSPORT=quic_gateway "
            "FLEETQOX_RMW_QUIC_GATEWAY_TAKE_ON_DEMAND=1 "
            f"FLEETQOX_RMW_QUIC_GATEWAY=127.0.0.1:{port} "
            f"FLEETQOX_RMW_QUIC_URI=https://localhost:{port}/fleetqox_quic_gateway_rmw_take.frmw "
            "FLEETQOX_RMW_QUIC_SNI=localhost "
            "FLEETQOX_RMW_QUIC_TIMEOUT=6s "
            f"FLEETQOX_RMW_QUIC_QLOG_DIR=/work/{qlogs.relative_to(root)} "
            f"FLEETQOX_RMW_QUIC_LOG=/work/{tmp.relative_to(root)}/client.log "
            f"FLEETQOX_RMW_QUIC_PAYLOAD_DIR=/work/{tmp.relative_to(root)} "
            f"FLEETQOX_RMW_QUIC_SESSION_FILE=/work/{session_file.relative_to(root)} "
            f"FLEETQOX_RMW_QUIC_TP_FILE=/work/{tp_file.relative_to(root)} "
            f"FLEETQOX_RMW_QUIC_TOKEN_FILE=/work/{token_file.relative_to(root)} "
        )
        server_timeout_s = max(16, 8 + requested_downloads * 4)
        probe_invocations = ""
        probe_rc_checks = ""
        for index in range(requested_downloads):
            probe_invocations += (
                f"{probe_command}{probe} > /work/{tmp.relative_to(root)}/probe_{index}.log "
                f"2> /work/{tmp.relative_to(root)}/probe_{index}.err; "
                f"probe{index}_rc=$?; "
            )
            probe_rc_checks += (
                f"if [ ${{probe{index}_rc}} -ne 0 ]; "
                f"then exit ${{probe{index}_rc}}; fi; "
            )
        command = (
            "set -e; "
            "openssl req -x509 -newkey rsa:2048 -nodes "
            f"-keyout /work/{certs.relative_to(root)}/server.key "
            f"-out /work/{certs.relative_to(root)}/server.crt "
            f"-subj /CN=localhost -days 1 >/work/{tmp.relative_to(root)}/cert.log 2>&1; "
            f"/usr/sbin/gtlsserver 127.0.0.1 {port} "
            f"/work/{certs.relative_to(root)}/server.key "
            f"/work/{certs.relative_to(root)}/server.crt "
            f"-d /work/{htdocs.relative_to(root)} "
            f"--qlog-dir /work/{qlogs.relative_to(root)} "
            f"--timeout={server_timeout_s}s --handshake-timeout=5s --no-quic-dump --no-http-dump "
            f"> /work/{tmp.relative_to(root)}/server.log 2>&1 & "
            "server_pid=$!; "
            "sleep 0.5; "
            f"source /work/{install_base.relative_to(root)}/setup.bash; "
            "set +e; "
            f"{probe_invocations}"
            "sleep 0.5; "
            "kill ${server_pid} >/dev/null 2>&1 || true; "
            "wait ${server_pid} >/dev/null 2>&1 || true; "
            f"{probe_rc_checks}"
            "exit 0"
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
        probe_rows = []
        probe_stderr = []
        for index in range(requested_downloads):
            log_path = tmp / f"probe_{index}.log"
            err_path = tmp / f"probe_{index}.err"
            probe_log = log_path.read_text(errors="replace") if log_path.exists() else ""
            probe_stderr.append(err_path.read_text(errors="replace") if err_path.exists() else "")
            probe_rows.append(parse_last_json(probe_log))
        client_log = (tmp / "client.log").read_text(errors="replace") if (tmp / "client.log").exists() else ""
        server_log = (tmp / "server.log").read_text(errors="replace") if (tmp / "server.log").exists() else ""
        qlog_files = sorted(qlogs.glob("*"))
        session_status = file_status(session_file)
        tp_status = file_status(tp_file)
        token_status = file_status(token_file)
        session_files_persisted = (
            session_status["exists"]
            and session_status["size_bytes"] > 0
            and tp_status["exists"]
            and tp_status["size_bytes"] > 0
        )
        ok_rows = [
            row
            for row in probe_rows
            if row.get("status") == "ok"
            and row.get("rmw_take_path_integrated") is True
            and row.get("payload_ok") is True
            and int(row.get("quic_gateway_frames_received", 0)) == 1
        ]
        client_handshake_count = client_log.count("QUIC handshake has completed")
        server_handshake_count = server_log.count("QUIC handshake has completed")
        alpn_h3 = "Negotiated ALPN is h3" in client_log and "Negotiated ALPN is h3" in server_log
        telemetry = parse_quic_session_reuse_telemetry(client_log, server_log)
        session_file_reused_by_multiple_downloads = (
            requested_downloads >= 2
            and len(ok_rows) == requested_downloads
            and session_files_persisted
        )
        ok = (
            docker.returncode == 0
            and session_file_reused_by_multiple_downloads
            and client_handshake_count >= requested_downloads
            and server_handshake_count >= requested_downloads
            and alpn_h3
            and len(qlog_files) >= requested_downloads
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if ok else "failed",
            "requested_download_count": requested_downloads,
            "session_reuse_file_configured": True,
            "session_files_persisted": session_files_persisted,
            "session_file_reused_by_multiple_downloads": session_file_reused_by_multiple_downloads,
            **telemetry,
            "session_resumption_attempted_observed": telemetry[
                "session_resumption_attempted_observed"
            ],
            "session_resumption_observed": telemetry["session_resumption_observed"],
            "zero_rtt_packet_observed": telemetry["zero_rtt_packet_observed"],
            "zero_rtt_accepted_observed": telemetry["zero_rtt_accepted_observed"],
            "zero_rtt_claim": telemetry["zero_rtt_accepted_observed"],
            "rmw_take_path_integrated": len(ok_rows) == requested_downloads,
            "quic_gateway_take_path_download": len(ok_rows) == requested_downloads,
            "subprocess_backed": True,
            "production_quic_backend": False,
            "full_bidirectional_quic_backend": False,
            "download_count": len(ok_rows),
            "client_handshake_count": client_handshake_count,
            "server_handshake_count": server_handshake_count,
            "alpn": "h3" if alpn_h3 else "",
            "qlog_file_count": len(qlog_files),
            "qlog_total_bytes": sum(path.stat().st_size for path in qlog_files),
            "session_file": session_status,
            "transport_parameters_file": tp_status,
            "token_file": token_status,
            "runs": probe_rows,
            "docker_returncode": docker.returncode,
            "docker_stdout": docker.stdout,
            "docker_stderr": docker.stderr,
            "probe_stderr": probe_stderr,
            "client_log_excerpt": client_log[:2500],
            "server_log_excerpt": server_log[:2500],
        }
    finally:
        for path in (tmp, build_base, install_base, log_base):
            shutil.rmtree(path, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--port", type=int, default=4472)
    parser.add_argument("--downloads", type=int, default=2)
    parser.add_argument(
        "--summary-json",
        default=(
            "results_rmw_socket/"
            "docker_quic_gateway_rmw_take_session_reuse_probe_summary.json"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_session_probe(
        root=ROOT,
        image=args.image,
        port=args.port,
        downloads=args.downloads,
    )
    output = ROOT / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(
            f"status={summary['status']} downloads={summary['download_count']} "
            f"session_file={summary['session_file']['size_bytes']}B"
        )
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
