"""Send a FleetRMW data frame over real ngtcp2/GnuTLS QUIC/TLS/H3 in Docker.

This closes the next QUIC slice after the raw payload handshake probe: the
payload is now a real ``fleetrmw.data_frame.v1`` frame and the received bytes
must decode with the C++ FleetRMW frame probe.  It is still deliberately not an
integrated rmw_fleetqox_cpp publish/take backend claim.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_rmw_docker_shared_memory_probe import parse_last_json


SCHEMA_VERSION = "fleetrmw.docker_quic_fleet_frame_probe.v1"
DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"


def container_script(*, frame_size: int, port: int) -> str:
    return f"""
set -e
source /opt/ros/jazzy/setup.bash
rm -rf /work/.tmp_fleetrmw_quic_frame_v2_build /work/.tmp_fleetrmw_quic_frame_v2_install /work/.tmp_fleetrmw_quic_frame_v2_log
colcon --log-base /work/.tmp_fleetrmw_quic_frame_v2_log build --base-paths ros2_ws/src \
  --packages-select rmw_fleetqox_cpp \
  --build-base /work/.tmp_fleetrmw_quic_frame_v2_build \
  --install-base /work/.tmp_fleetrmw_quic_frame_v2_install \
  --cmake-args -DCMAKE_BUILD_TYPE=Release >/tmp/fleetrmw_quic_frame_colcon.out 2>/tmp/fleetrmw_quic_frame_colcon.err
source /work/.tmp_fleetrmw_quic_frame_v2_install/setup.bash
python3 - <<'PY'
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from fleetqox.rmw_frame import encode_data_frame

schema_version = {SCHEMA_VERSION!r}
frame_size = {frame_size}
port = {port}
frame_probe = Path("/work/.tmp_fleetrmw_quic_frame_v2_install/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/fleetrmw_frame_probe")

def excerpt(text: str, limit: int = 2500) -> str:
    if len(text) <= limit:
        return text
    return text[: limit // 2] + "\\n...<truncated>...\\n" + text[-limit // 2 :]

def finish(summary: dict, rc: int = 0) -> None:
    print(json.dumps(summary, sort_keys=True))
    raise SystemExit(rc)

gtlsclient = shutil.which("gtlsclient") or "/usr/bin/gtlsclient"
gtlsserver = shutil.which("gtlsserver") or "/usr/sbin/gtlsserver"
missing = [
    binary for binary in (gtlsclient, gtlsserver, "openssl", str(frame_probe))
    if shutil.which(binary) is None and not Path(binary).exists()
]
if missing:
    finish({{
        "schema_version": schema_version,
        "status": "failed",
        "stage": "dependency_check",
        "missing": missing,
        "rmw_integrated_backend": False,
    }}, 1)

with tempfile.TemporaryDirectory(prefix="fleetrmw-quic-frame-") as tmp_text:
    tmp = Path(tmp_text)
    htdocs = tmp / "htdocs"
    download = tmp / "download"
    qlogs = tmp / "qlogs"
    htdocs.mkdir()
    download.mkdir()
    qlogs.mkdir()
    key = tmp / "server.key"
    cert = tmp / "server.crt"

    frame = {{
        "schema_version": "fleetrmw.data_frame.v1",
        "kind": "sidecar_packet_frame",
        "route": {{
            "robot_id": "robot_quic_0001",
            "topic": "/fleetqox/quic_frame",
        }},
        "sample_envelope": {{
            "robot_id": "robot_quic_0001",
            "topic": "/fleetqox/quic_frame",
            "publisher_id": "fpub-quic-frame-0001",
            "source_sequence_number": 17,
            "source_timestamp_ns": 17000000,
        }},
        "serialized_payload": {{
            "encoding": "hex",
            "size": 16,
            "data": "666c656574716f782d717569632d7631",
        }},
    }}
    encoded_frame = encode_data_frame(frame, target_size=frame_size)
    source_file = htdocs / "fleetqox_frame.frmw"
    source_file.write_bytes(encoded_frame)
    expected_sha256 = hashlib.sha256(encoded_frame).hexdigest()

    cert_proc = subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-subj", "/CN=localhost",
            "-days", "1",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if cert_proc.returncode != 0:
        finish({{
            "schema_version": schema_version,
            "status": "failed",
            "stage": "certificate_generation",
            "stdout": cert_proc.stdout,
            "stderr": cert_proc.stderr,
            "rmw_integrated_backend": False,
        }}, 1)

    server_log = tmp / "server.log"
    client_log = tmp / "client.log"
    with server_log.open("w", encoding="utf-8") as server_out:
        server = subprocess.Popen(
            [
                gtlsserver,
                "127.0.0.1",
                str(port),
                str(key),
                str(cert),
                "-d",
                str(htdocs),
                "--qlog-dir",
                str(qlogs),
                "--timeout=10s",
                "--handshake-timeout=5s",
                "--no-quic-dump",
                "--no-http-dump",
            ],
            stdout=server_out,
            stderr=subprocess.STDOUT,
            text=True,
        )

    try:
        time.sleep(0.75)
        if server.poll() is not None:
            finish({{
                "schema_version": schema_version,
                "status": "failed",
                "stage": "server_start",
                "server_returncode": server.returncode,
                "server_log": excerpt(server_log.read_text(errors="replace")),
                "rmw_integrated_backend": False,
            }}, 1)

        with client_log.open("w", encoding="utf-8") as client_out:
            client = subprocess.run(
                [
                    gtlsclient,
                    "127.0.0.1",
                    str(port),
                    f"https://localhost:{{port}}/fleetqox_frame.frmw",
                    "--download",
                    str(download),
                    "--exit-on-all-streams-close",
                    "--timeout=5s",
                    "--sni=localhost",
                    "--no-quic-dump",
                    "--no-http-dump",
                ],
                stdout=client_out,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                timeout=20,
            )
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=2)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=2)

    client_output = client_log.read_text(errors="replace")
    server_output = server_log.read_text(errors="replace")
    downloaded = download / "fleetqox_frame.frmw"
    downloaded_bytes = downloaded.read_bytes() if downloaded.exists() else b""
    downloaded_sha256 = hashlib.sha256(downloaded_bytes).hexdigest() if downloaded_bytes else ""
    decode = subprocess.run(
        [str(frame_probe)],
        input=downloaded_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    decoded = {{}}
    try:
        decoded = json.loads(decode.stdout.decode("utf-8"))
    except json.JSONDecodeError:
        decoded = {{}}

    client_handshake = "QUIC handshake has completed" in client_output
    server_handshake = "QUIC handshake has completed" in server_output
    alpn_h3 = "Negotiated ALPN is h3" in client_output and "Negotiated ALPN is h3" in server_output
    qlog_files = sorted(qlogs.glob("*"))
    payload_ok = downloaded_sha256 == expected_sha256 and len(downloaded_bytes) == len(encoded_frame)
    decoded_ok = (
        decode.returncode == 0
        and decoded.get("status") == "decoded"
        and decoded.get("robot_id") == "robot_quic_0001"
        and decoded.get("topic") == "/fleetqox/quic_frame"
        and decoded.get("publisher_id") == "fpub-quic-frame-0001"
        and decoded.get("source_sequence_number") == 17
        and decoded.get("source_timestamp_ns") == 17000000
    )
    ok = (
        client.returncode == 0
        and client_handshake
        and server_handshake
        and alpn_h3
        and payload_ok
        and decoded_ok
        and len(qlog_files) >= 1
    )
    finish({{
        "schema_version": schema_version,
        "status": "ok" if ok else "failed",
        "transport": "ngtcp2_gtls_quic_tls_h3",
        "quic_version": "0x00000001",
        "alpn": "h3" if alpn_h3 else "",
        "tls_handshake_complete": client_handshake and server_handshake,
        "fleet_frame_schema": "fleetrmw.data_frame.v1",
        "fleet_frame_bytes": len(encoded_frame),
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
        "server_returncode": server.returncode,
        "frame_probe_returncode": decode.returncode,
        "frame_probe_stderr": decode.stderr.decode("utf-8", errors="replace"),
        "client_log_excerpt": excerpt(client_output),
        "server_log_excerpt": excerpt(server_output),
    }}, 0 if ok else 1)
PY
"""


def run_probe(*, root: Path, image: str, frame_size: int, port: int) -> dict[str, Any]:
    build_base = root / ".tmp_fleetrmw_quic_frame_v2_build"
    install_base = root / ".tmp_fleetrmw_quic_frame_v2_install"
    log_base = root / ".tmp_fleetrmw_quic_frame_v2_log"
    try:
        run = subprocess.run(
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
                container_script(frame_size=frame_size, port=port),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        parsed = parse_last_json(run.stdout)
        if not parsed:
            parsed = {
                "schema_version": SCHEMA_VERSION,
                "status": "failed",
                "stage": "parse_summary",
            }
        parsed.update(
            {
                "image": image,
                "docker_returncode": run.returncode,
                "docker_stderr": run.stderr,
            }
        )
        if run.returncode != 0 and parsed.get("status") == "ok":
            parsed["status"] = "failed"
            parsed["stage"] = "docker_returncode"
        return parsed
    finally:
        subprocess.run(
            ["rm", "-rf", str(build_base), str(install_base), str(log_base)],
            check=False,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--frame-size", type=int, default=4096)
    parser.add_argument("--port", type=int, default=4444)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_quic_fleet_frame_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    summary = run_probe(
        root=root,
        image=args.image,
        frame_size=max(args.frame_size, 1),
        port=args.port,
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
