"""Verify sequential QUIC gateway publish+take on one server.

This is a boundary probe, not a production full-duplex RMW backend claim.  It
starts one ngtcp2/GnuTLS gtlsserver, runs the RMW publish POST probe, then runs
the opt-in RMW take GET probe against the same server while sharing session,
transport-parameter, and token files between both client invocations.
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

from scripts.run_rmw_docker_quic_gateway_publish_probe import (  # noqa: E402
    DEFAULT_IMAGE,
    parse_quic_session_reuse_telemetry,
    parse_server_body_sizes,
    parse_server_content_lengths,
)
from scripts.run_rmw_docker_quic_gateway_rmw_take_probe import (  # noqa: E402
    expected_frame_bytes,
)
from scripts.run_rmw_docker_quic_gateway_session_reuse_probe import (  # noqa: E402
    file_status,
)
from scripts.run_rmw_docker_shared_memory_probe import parse_last_json  # noqa: E402


SCHEMA_VERSION = "fleetrmw.docker_quic_gateway_bidirectional_probe.v1"


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
    iterations: int = 1,
    disable_early_data: bool = False,
) -> dict[str, Any]:
    run_count = max(iterations, 1)
    suffix = str(os.getpid())
    tmp = root / f".tmp_fleetrmw_quic_gateway_bidirectional_{suffix}"
    htdocs = tmp / "htdocs"
    qlogs = tmp / "qlogs"
    certs = tmp / "certs"
    build_base = root / ".tmp_fleetrmw_quic_gateway_bidirectional_v2_build"
    install_base = root / ".tmp_fleetrmw_quic_gateway_bidirectional_v2_install"
    log_base = root / ".tmp_fleetrmw_quic_gateway_bidirectional_v2_log"
    session_file = tmp / "gtlsclient-session.bin"
    tp_file = tmp / "gtlsclient-transport-params.bin"
    token_file = tmp / "gtlsclient-token.bin"
    publish_probe = (
        "/work/.tmp_fleetrmw_quic_gateway_bidirectional_v2_install/"
        "rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_quic_gateway_publish_probe"
    )
    take_probe = (
        "/work/.tmp_fleetrmw_quic_gateway_bidirectional_v2_install/"
        "rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_quic_gateway_rmw_take_probe"
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

        shared_quic_env = (
            "FLEETQOX_RMW_REMOTE_TRANSPORT=quic_gateway "
            f"FLEETQOX_RMW_QUIC_GATEWAY=127.0.0.1:{port} "
            "FLEETQOX_RMW_QUIC_SNI=localhost "
            "FLEETQOX_RMW_QUIC_TIMEOUT=6s "
            f"FLEETQOX_RMW_QUIC_QLOG_DIR=/work/{qlogs.relative_to(root)} "
            f"FLEETQOX_RMW_QUIC_SESSION_FILE=/work/{session_file.relative_to(root)} "
            f"FLEETQOX_RMW_QUIC_TP_FILE=/work/{tp_file.relative_to(root)} "
            f"FLEETQOX_RMW_QUIC_TOKEN_FILE=/work/{token_file.relative_to(root)} "
            f"{'FLEETQOX_RMW_QUIC_DISABLE_EARLY_DATA=1 ' if disable_early_data else ''}"
        )
        probe_invocations = ""
        probe_rc_checks = ""
        for index in range(run_count):
            publish_env = (
                shared_quic_env +
                f"FLEETQOX_RMW_QUIC_URI=https://localhost:{port}/fleetrmw_publish " +
                f"FLEETQOX_RMW_QUIC_LOG=/work/{tmp.relative_to(root)}/"
                f"client_publish_{index}.log "
            )
            take_env = (
                shared_quic_env +
                "FLEETQOX_RMW_QUIC_GATEWAY_TAKE_ON_DEMAND=1 " +
                f"FLEETQOX_RMW_QUIC_URI=https://localhost:{port}/"
                "fleetqox_quic_gateway_rmw_take.frmw " +
                f"FLEETQOX_RMW_QUIC_LOG=/work/{tmp.relative_to(root)}/"
                f"client_take_{index}.log " +
                f"FLEETQOX_RMW_QUIC_PAYLOAD_DIR=/work/{tmp.relative_to(root)} "
            )
            probe_invocations += (
                f"{publish_env}{publish_probe} > /work/{tmp.relative_to(root)}/"
                f"publish_{index}.log "
                f"2> /work/{tmp.relative_to(root)}/publish_{index}.err; "
                f"publish{index}_rc=$?; "
                f"{take_env}{take_probe} > /work/{tmp.relative_to(root)}/"
                f"take_{index}.log "
                f"2> /work/{tmp.relative_to(root)}/take_{index}.err; "
                f"take{index}_rc=$?; "
            )
            probe_rc_checks += (
                f"if [ ${{publish{index}_rc}} -ne 0 ]; "
                f"then exit ${{publish{index}_rc}}; fi; "
                f"if [ ${{take{index}_rc}} -ne 0 ]; "
                f"then exit ${{take{index}_rc}}; fi; "
            )
        server_timeout_s = max(24, 8 + run_count * 8)
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
            f"--timeout={server_timeout_s}s --handshake-timeout=5s --no-quic-dump "
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

        server_log = (
            (tmp / "server.log").read_text(errors="replace")
            if (tmp / "server.log").exists()
            else ""
        )
        qlog_files = sorted(qlogs.glob("*"))
        server_body_sizes = parse_server_body_sizes(server_log)
        server_content_lengths = parse_server_content_lengths(server_log)
        server_body_total_bytes = sum(server_body_sizes)
        server_content_length_total = sum(server_content_lengths)
        server_handshake_count = server_log.count("QUIC handshake has completed")
        session_status = file_status(session_file)
        tp_status = file_status(tp_file)
        token_status = file_status(token_file)
        session_files_persisted = (
            session_status["exists"]
            and session_status["size_bytes"] > 0
            and tp_status["exists"]
            and tp_status["size_bytes"] > 0
        )
        runs: list[dict[str, Any]] = []
        publish_stderr: list[str] = []
        take_stderr: list[str] = []
        client_publish_logs: list[str] = []
        client_take_logs: list[str] = []
        total_quic_gateway_frames_sent = 0
        total_quic_gateway_bytes_sent = 0
        total_quic_gateway_frames_received = 0
        total_quic_gateway_bytes_received = 0
        client_handshake_count = 0
        for index in range(run_count):
            publish_log = (
                (tmp / f"publish_{index}.log").read_text(errors="replace")
                if (tmp / f"publish_{index}.log").exists()
                else ""
            )
            take_log = (
                (tmp / f"take_{index}.log").read_text(errors="replace")
                if (tmp / f"take_{index}.log").exists()
                else ""
            )
            publish_stderr.append(
                (tmp / f"publish_{index}.err").read_text(errors="replace")
                if (tmp / f"publish_{index}.err").exists()
                else ""
            )
            take_stderr.append(
                (tmp / f"take_{index}.err").read_text(errors="replace")
                if (tmp / f"take_{index}.err").exists()
                else ""
            )
            client_publish_log = (
                (tmp / f"client_publish_{index}.log").read_text(errors="replace")
                if (tmp / f"client_publish_{index}.log").exists()
                else ""
            )
            client_take_log = (
                (tmp / f"client_take_{index}.log").read_text(errors="replace")
                if (tmp / f"client_take_{index}.log").exists()
                else ""
            )
            client_publish_logs.append(client_publish_log)
            client_take_logs.append(client_take_log)
            publish_json = parse_last_json(publish_log)
            take_json = parse_last_json(take_log)
            quic_bytes_sent = int(publish_json.get("quic_gateway_bytes_sent", 0) or 0)
            server_payload_ok = (
                quic_bytes_sent > 0
                and quic_bytes_sent in server_body_sizes
                and quic_bytes_sent in server_content_lengths
            )
            publish_handshake = "QUIC handshake has completed" in client_publish_log
            take_handshake = "QUIC handshake has completed" in client_take_log
            publish_alpn_h3 = (
                "Negotiated ALPN is h3" in client_publish_log
                and "Negotiated ALPN is h3" in server_log
            )
            take_alpn_h3 = (
                "Negotiated ALPN is h3" in client_take_log
                and "Negotiated ALPN is h3" in server_log
            )
            publish_ok = (
                publish_json.get("status") == "ok"
                and publish_json.get("rmw_publish_path_integrated") is True
                and int(publish_json.get("quic_gateway_frames_sent", 0) or 0) >= 1
                and server_payload_ok
                and publish_handshake
                and publish_alpn_h3
            )
            take_ok = (
                take_json.get("status") == "ok"
                and take_json.get("rmw_take_path_integrated") is True
                and take_json.get("quic_gateway_take_path_download") is True
                and take_json.get("payload_ok") is True
                and int(take_json.get("quic_gateway_frames_received", 0) or 0) == 1
                and take_handshake
                and take_alpn_h3
            )
            row_ok = publish_ok and take_ok
            total_quic_gateway_frames_sent += int(
                publish_json.get("quic_gateway_frames_sent", 0) or 0
            )
            total_quic_gateway_bytes_sent += quic_bytes_sent
            total_quic_gateway_frames_received += int(
                take_json.get("quic_gateway_frames_received", 0) or 0
            )
            total_quic_gateway_bytes_received += int(
                take_json.get("quic_gateway_bytes_received", 0) or 0
            )
            client_handshake_count += int(publish_handshake) + int(take_handshake)
            runs.append(
                {
                    "status": "ok" if row_ok else "failed",
                    "iteration": index,
                    "rmw_publish_path_integrated": publish_ok,
                    "rmw_take_path_integrated": take_ok,
                    "server_payload_bytes_match": server_payload_ok,
                    "publish_handshake": publish_handshake,
                    "take_handshake": take_handshake,
                    "publish_probe": publish_json,
                    "take_probe": take_json,
                }
            )
        ok_run_count = sum(1 for row in runs if row["status"] == "ok")
        last_publish_probe = runs[-1]["publish_probe"] if runs else {}
        last_take_probe = runs[-1]["take_probe"] if runs else {}
        ok = (
            docker.returncode == 0
            and len(runs) == run_count
            and ok_run_count == run_count
            and server_handshake_count >= run_count * 2
            and session_files_persisted
            and len(qlog_files) >= run_count * 2
            and server_body_total_bytes == total_quic_gateway_bytes_sent
            and server_content_length_total >= total_quic_gateway_bytes_sent
        )
        telemetry = parse_quic_session_reuse_telemetry(
            server_log,
            *client_publish_logs,
            *client_take_logs,
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if ok else "failed",
            "run_count": run_count,
            "ok_run_count": ok_run_count,
            "failed_run_count": run_count - ok_run_count,
            "transport": "ngtcp2_gtls_quic_tls_h3_post_then_get_gateway",
            "quic_gateway_bidirectional_boundary_claim": ok,
            "quic_gateway_bidirectional_repeated_claim": ok and run_count >= 5,
            "bidirectional_scope": "sequential_post_then_get_same_gtlsserver_shared_session_files",
            "rmw_publish_path_integrated": ok,
            "rmw_take_path_integrated": ok,
            "quic_gateway_take_path_download": last_take_probe.get(
                "quic_gateway_take_path_download"
            ) is True,
            "payload_ok": last_take_probe.get("payload_ok") is True,
            "server_payload_bytes_match": ok,
            "quic_gateway_frames_sent": total_quic_gateway_frames_sent,
            "quic_gateway_bytes_sent": total_quic_gateway_bytes_sent,
            "quic_gateway_frames_received": total_quic_gateway_frames_received,
            "quic_gateway_bytes_received": total_quic_gateway_bytes_received,
            "session_reuse_file_configured": True,
            "session_files_persisted": session_files_persisted,
            "session_file_reused_by_upload_and_download": (
                session_files_persisted and ok
            ),
            "early_data_disabled": disable_early_data,
            **telemetry,
            "session_resumption_attempted_observed": telemetry[
                "session_resumption_attempted_observed"
            ],
            "session_resumption_observed": telemetry["session_resumption_observed"],
            "zero_rtt_packet_observed": telemetry["zero_rtt_packet_observed"],
            "zero_rtt_accepted_observed": telemetry["zero_rtt_accepted_observed"],
            "zero_rtt_disabled_control_claim": (
                ok and disable_early_data and not telemetry["zero_rtt_packet_observed"]
            ),
            "zero_rtt_claim": telemetry["zero_rtt_accepted_observed"],
            "subprocess_backed": True,
            "production_quic_backend": False,
            "full_bidirectional_quic_backend": False,
            "client_handshake_count": client_handshake_count,
            "server_handshake_count": server_handshake_count,
            "alpn": "h3" if publish_alpn_h3 and take_alpn_h3 else "",
            "qlog_file_count": len(qlog_files),
            "qlog_total_bytes": sum(path.stat().st_size for path in qlog_files),
            "server_body_total_bytes": server_body_total_bytes,
            "server_content_length_total": server_content_length_total,
            "session_file": session_status,
            "transport_parameters_file": tp_status,
            "token_file": token_status,
            "publish_probe": last_publish_probe,
            "take_probe": last_take_probe,
            "runs": runs,
            "docker_returncode": docker.returncode,
            "docker_stdout": docker.stdout,
            "docker_stderr": docker.stderr,
            "publish_stderr": publish_stderr,
            "take_stderr": take_stderr,
            "client_publish_log_excerpt": "\n".join(client_publish_logs)[:2500],
            "client_take_log_excerpt": "\n".join(client_take_logs)[:2500],
            "server_log_excerpt": server_log[:2500],
        }
    finally:
        for path in (tmp, build_base, install_base, log_base):
            shutil.rmtree(path, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--port", type=int, default=4878)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--disable-early-data", action="store_true")
    parser.add_argument(
        "--summary-json",
        default=(
            "results_rmw_socket/"
            "docker_quic_gateway_bidirectional_probe_summary.json"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_probe(
        root=ROOT,
        image=args.image,
        port=args.port,
        iterations=args.iterations,
        disable_early_data=args.disable_early_data,
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
        print(
            f"status={summary['status']} "
            f"runs={summary.get('ok_run_count')}/{summary.get('run_count')} "
            f"publish={summary.get('rmw_publish_path_integrated')} "
            f"take={summary.get('rmw_take_path_integrated')}"
        )
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
