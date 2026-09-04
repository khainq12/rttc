"""Verify FleetRMW QUIC gateway download/take-path frame reception.

This is a transport-boundary proof: a FleetRMW data frame is fetched over a
real ngtcp2/GnuTLS QUIC/TLS/H3 GET path and decoded by a C++ probe.  It is not
yet an integrated rmw_take full-duplex backend claim.
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

from scripts.run_rmw_docker_shared_memory_probe import parse_last_json


SCHEMA_VERSION = "fleetrmw.docker_quic_gateway_take_probe.v1"
DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def expected_frame_bytes() -> bytes:
    payload = b"fleetqox-quic-gateway-take-v1"
    return (
        b"FRMW1\n"
        b'{"schema_version":"fleetrmw.data_frame.v1",'
        b'"kind":"sidecar_packet_frame",'
        b'"domain_id":0,'
        b'"route":{"robot_id":"robot_quic_take_0001",'
        b'"topic":"/fleetqox/quic_gateway_take_probe"},'
        b'"sample_envelope":{'
        b'"robot_id":"robot_quic_take_0001",'
        b'"topic":"/fleetqox/quic_gateway_take_probe",'
        b'"publisher_id":"fpub-quic-gateway-take-0001",'
        b'"source_sequence_number":23,'
        b'"source_timestamp_ns":23000000'
        b'},'
        b'"serialized_payload":{'
        b'"encoding":"hex",'
        b'"size":'
        + str(len(payload)).encode("ascii")
        + b','
        b'"data":"'
        + payload.hex().encode("ascii")
        + b'"}}'
    )


def run_probe(*, root: Path, image: str, port: int) -> dict[str, Any]:
    suffix = str(os.getpid())
    tmp = root / f".tmp_fleetrmw_quic_gateway_take_{suffix}"
    htdocs = tmp / "htdocs"
    qlogs = tmp / "qlogs"
    certs = tmp / "certs"
    build_base = root / ".tmp_fleetrmw_quic_gateway_take_v2_build"
    install_base = root / ".tmp_fleetrmw_quic_gateway_take_v2_install"
    log_base = root / ".tmp_fleetrmw_quic_gateway_take_v2_log"
    probe = (
        "/work/.tmp_fleetrmw_quic_gateway_take_v2_install/rmw_fleetqox_cpp/lib/"
        "rmw_fleetqox_cpp/fleetrmw_quic_gateway_take_probe"
    )
    try:
        for directory in (htdocs, qlogs, certs):
            directory.mkdir(parents=True, exist_ok=True)
        frame_path = htdocs / "fleetqox_quic_gateway_take.frmw"
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
            "--timeout=10s --handshake-timeout=5s --no-quic-dump --no-http-dump "
            f"> /work/{tmp.relative_to(root)}/server.log 2>&1 & "
            "server_pid=$!; "
            "sleep 0.5; "
            f"source /work/{install_base.relative_to(root)}/setup.bash; "
            "set +e; "
            "FLEETQOX_RMW_REMOTE_TRANSPORT=quic_gateway "
            f"FLEETQOX_RMW_QUIC_GATEWAY=127.0.0.1:{port} "
            f"FLEETQOX_RMW_QUIC_URI=https://localhost:{port}/fleetqox_quic_gateway_take.frmw "
            "FLEETQOX_RMW_QUIC_SNI=localhost "
            f"FLEETQOX_RMW_QUIC_QLOG_DIR=/work/{qlogs.relative_to(root)} "
            f"FLEETQOX_RMW_QUIC_LOG=/work/{tmp.relative_to(root)}/client.log "
            f"FLEETQOX_RMW_QUIC_PAYLOAD_DIR=/work/{tmp.relative_to(root)} "
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
        client_handshake = "QUIC handshake has completed" in client_log
        server_handshake = "QUIC handshake has completed" in server_log
        alpn_h3 = "Negotiated ALPN is h3" in client_log and "Negotiated ALPN is h3" in server_log
        ok = (
            docker.returncode == 0
            and probe_json.get("status") == "ok"
            and probe_json.get("quic_gateway_take_path_download") is True
            and probe_json.get("payload_integrity_ok") is True
            and probe_json.get("decoded_frame_ok") is True
            and int(probe_json.get("quic_gateway_frames_received", 0)) == 1
            and int(probe_json.get("quic_gateway_bytes_received", 0)) > 0
            and client_handshake
            and server_handshake
            and alpn_h3
            and len(qlog_files) >= 1
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if ok else "failed",
            "transport": "ngtcp2_gtls_quic_tls_h3_get_gateway_take_path",
            "quic_version": "0x00000001",
            "alpn": "h3" if alpn_h3 else "",
            "tls_handshake_complete": client_handshake and server_handshake,
            "quic_gateway_take_path_download": ok,
            "download_path_scope": "ngtcp2_gtls_quic_tls_h3_get_fleetrmw_frame",
            "payload_integrity_ok": probe_json.get("payload_integrity_ok") is True,
            "decoded_frame_ok": probe_json.get("decoded_frame_ok") is True,
            "quic_gateway_frames_received": probe_json.get("quic_gateway_frames_received"),
            "quic_gateway_bytes_received": probe_json.get("quic_gateway_bytes_received"),
            "subprocess_backed": True,
            "rmw_take_path_integrated": False,
            "production_quic_backend": False,
            "full_bidirectional_quic_backend": False,
            "qlog_file_count": len(qlog_files),
            "qlog_total_bytes": sum(path.stat().st_size for path in qlog_files),
            "probe": probe_json,
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
    parser.add_argument("--port", type=int, default=4468)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_quic_gateway_take_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_probe(root=ROOT, image=args.image, port=args.port)
    output = ROOT / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(f"status={summary['status']} transport={summary.get('transport')}")
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
