#!/usr/bin/env python3
"""Validate fleet admission policy through the stateful QUIC/H3 gateway."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any

try:
    from scripts.run_rmw_docker_quic_stateful_gateway_probe import (
        DEFAULT_IMAGE,
        SERVICE_SCHEMA_VERSION,
        json_rows,
        run,
        wait_service_ready,
    )
except ModuleNotFoundError:
    from run_rmw_docker_quic_stateful_gateway_probe import (
        DEFAULT_IMAGE,
        SERVICE_SCHEMA_VERSION,
        json_rows,
        run,
        wait_service_ready,
    )


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "fleetrmw.docker_quic_admission_probe.v1"
PROBE_SCHEMA_VERSION = "fleetrmw.quic_admission_probe.v1"


def probe_ok(row: dict[str, Any]) -> bool:
    return (
        row.get("schema_version") == PROBE_SCHEMA_VERSION
        and row.get("status") == "ok"
        and row.get("admitted_frame_count") == 4
        and row.get("taken_frame_count") == 4
        and row.get("stream_quota_rejected") is True
        and row.get("fleet_quota_rejected") is True
        and row.get("publisher_rejected") is True
        and row.get("epoch_replenishment_admitted") is True
        and row.get("connections_created") == 5
        and row.get("handshakes_completed") == 5
        and row.get("streams_opened") == 11
        and row.get("fleet_admission_policy_claim") is True
        and row.get("tls_peer_verification_required") is True
        and row.get("subprocess_backed") is False
        and row.get("production_readiness") is False
    )


def service_ok(row: dict[str, Any]) -> bool:
    metrics = row.get("metrics", {})
    admission = metrics.get("admission", {})
    transport = row.get("transport_metrics", {})
    return (
        row.get("schema_version") == SERVICE_SCHEMA_VERSION
        and row.get("status") == "stopped"
        and row.get("clean_teardown") is True
        and row.get("admission_policy_configured") is True
        and metrics.get("admission_policy_enabled") is True
        and metrics.get("requests_total") == 11
        and metrics.get("post_requests") == 7
        and metrics.get("get_requests") == 4
        and metrics.get("accepted_frames") == 4
        and metrics.get("duplicate_frames") == 0
        and metrics.get("invalid_frames") == 0
        and metrics.get("dequeued_frames") == 4
        and metrics.get("topic_count") == 3
        and metrics.get("consumer_count") == 3
        and metrics.get("retained_frames") == 4
        and admission.get("accepted_total") in {0, 1}
        and admission.get("accepted_cumulative") == 4
        and admission.get("accepted_by_class")
        == {"bulk": 1, "control": 2, "state": 1}
        and admission.get("rejected_by_reason")
        == {
            "fleet_quota_exhausted": 1,
            "publisher_not_allowed": 1,
            "stream_quota_exhausted": 1,
        }
        and admission.get("rule_count") == 3
        and admission.get("max_accepted_frames") == 3
        and admission.get("epoch_ms") == 10000
        and admission.get("epoch_reset_count", 0) >= 1
        and transport.get("connections_created") == 5
        and transport.get("h3_sessions_negotiated") == 5
    )


def run_case(
    *,
    root: Path,
    image: str,
    network: str,
    install: str,
    temp_root: Path,
    index: int,
) -> dict[str, Any]:
    suffix = f"{os.getpid()}-{index}"
    service_name = f"fleetrmw-admission-service-{suffix}"
    case_root = temp_root / f"run-{index}"
    service_qlogs = case_root / "service-qlogs"
    client_qlogs = case_root / "client-qlogs"
    service_qlogs.mkdir(parents=True, exist_ok=True)
    client_qlogs.mkdir(parents=True, exist_ok=True)
    certs = temp_root / "certs"
    policy = temp_root / "admission-policy.json"
    service_command = (
        "tc qdisc replace dev eth0 root netem delay 5ms 1ms loss 0.2% && "
        "tc qdisc show dev eth0 && "
        "exec python3 scripts/fleetrmw_quic_gateway_service.py "
        "--host 0.0.0.0 --port 4498 "
        f"--certificate /work/{(certs / 'server.crt').relative_to(root)} "
        f"--private-key /work/{(certs / 'server.key').relative_to(root)} "
        f"--admission-policy /work/{policy.relative_to(root)} "
        f"--qlog-dir /work/{service_qlogs.relative_to(root)} "
        "--max-frames-per-topic 8 --max-frame-bytes 65536"
    )
    started = run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            service_name,
            "--network",
            network,
            "--network-alias",
            "fleetqox-admission-gateway",
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
            service_command,
        ]
    )
    ready = started.returncode == 0 and wait_service_ready(service_name)
    client = subprocess.CompletedProcess([], 1, "", "service_not_ready")
    service_exit_code = -1
    service_logs = ""
    try:
        if ready:
            client_command = (
                "source /opt/ros/jazzy/setup.bash && "
                f"source {install}/setup.bash && "
                "tc qdisc replace dev eth0 root netem delay 7ms 2ms loss 0.2% && "
                "tc qdisc show dev eth0 && "
                "export FLEETQOX_RMW_REMOTE_TRANSPORT=quic_gateway && "
                "export FLEETQOX_RMW_QUIC_BACKEND=inprocess && "
                "export FLEETQOX_RMW_QUIC_GATEWAY=fleetqox-admission-gateway:4498 && "
                "export FLEETQOX_RMW_QUIC_SNI=localhost && "
                "export FLEETQOX_RMW_QUIC_TIMEOUT=8s && "
                f"export FLEETQOX_RMW_QUIC_CA_FILE=/work/{(certs / 'ca.crt').relative_to(root)} && "
                f"export FLEETQOX_RMW_QUIC_QLOG_DIR=/work/{client_qlogs.relative_to(root)} && "
                f"{install}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
                "fleetrmw_quic_admission_probe"
            )
            client = run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--name",
                    f"fleetrmw-admission-client-{suffix}",
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
        time.sleep(0.5)
        run(["docker", "stop", "--time", "3", service_name])
        inspected = run(
            ["docker", "inspect", "-f", "{{.State.ExitCode}}", service_name]
        )
        if inspected.returncode == 0 and inspected.stdout.strip():
            service_exit_code = int(inspected.stdout.strip())
        service_logs = run(["docker", "logs", service_name]).stdout
    finally:
        run(["docker", "rm", "-f", service_name])

    probe_rows = json_rows(client.stdout)
    service_rows = json_rows(service_logs)
    probe = probe_rows[-1] if probe_rows else {}
    service = service_rows[-1] if service_rows else {}
    service_files = [path for path in service_qlogs.glob("*") if path.is_file()]
    client_files = [path for path in client_qlogs.glob("*") if path.is_file()]
    netem_ok = "qdisc netem" in service_logs and "qdisc netem" in client.stdout
    qlog_ok = (
        len(service_files) >= 1
        and len(client_files) >= 4
        and all(path.stat().st_size > 0 for path in service_files + client_files)
    )
    ok = (
        ready
        and client.returncode == 0
        and service_exit_code == 0
        and probe_ok(probe)
        and service_ok(service)
        and netem_ok
        and qlog_ok
    )
    return {
        "index": index,
        "status": "ok" if ok else "failed",
        "service_ready": ready,
        "client_returncode": client.returncode,
        "service_exit_code": service_exit_code,
        "probe": probe,
        "service": service,
        "service_qlog_file_count": len(service_files),
        "client_qlog_file_count": len(client_files),
        "qlog_total_bytes": sum(
            path.stat().st_size for path in service_files + client_files
        ),
        "netem_configured_both_containers": netem_ok,
        "client_stdout": "" if ok else client.stdout,
        "client_stderr": "" if ok else client.stderr,
        "service_logs": "" if ok else service_logs,
    }


def certificate_command(certs: Path, root: Path) -> str:
    prefix = f"/work/{certs.relative_to(root)}"
    return (
        "openssl req -x509 -newkey rsa:2048 -nodes "
        f"-keyout {prefix}/ca.key -out {prefix}/ca.crt "
        "-subj /CN=FleetQoX-Admission-CA "
        "-addext basicConstraints=critical,CA:TRUE "
        "-addext keyUsage=critical,keyCertSign,cRLSign -days 1 >/dev/null 2>&1 && "
        "openssl req -new -newkey rsa:2048 -nodes "
        f"-keyout {prefix}/server.key -out {prefix}/server.csr "
        "-subj /CN=localhost "
        "-addext subjectAltName=DNS:localhost,DNS:fleetqox-admission-gateway "
        "-addext extendedKeyUsage=serverAuth >/dev/null 2>&1 && "
        f"openssl x509 -req -in {prefix}/server.csr -CA {prefix}/ca.crt "
        f"-CAkey {prefix}/ca.key -CAcreateserial -out {prefix}/server.crt "
        "-days 1 -copy_extensions copy >/dev/null 2>&1"
    )


def run_probe(
    *, root: Path, image: str, iterations: int, keep_temp: bool
) -> dict[str, Any]:
    run_count = max(1, iterations)
    temp_root = root / f".tmp_fleetrmw_quic_admission_{os.getpid()}"
    certs = temp_root / "certs"
    certs.mkdir(parents=True, exist_ok=True)
    policy = temp_root / "admission-policy.json"
    policy.write_text(
        json.dumps(
            {
                "schema_version": "fleetrmw.quic_gateway_admission_policy.v1",
                "default_action": "deny",
                "max_accepted_frames": 3,
                "epoch_ms": 10000,
                "rules": [
                    {
                        "domain_id": 42,
                        "topic": "/fleetqox/control",
                        "traffic_class": "control",
                        "max_accepted_frames": 2,
                        "allowed_publishers": ["control-publisher"],
                    },
                    {
                        "domain_id": 42,
                        "topic": "/fleetqox/bulk",
                        "traffic_class": "bulk",
                        "max_accepted_frames": 1,
                        "allowed_publishers": ["bulk-publisher"],
                    },
                    {
                        "domain_id": 42,
                        "topic": "/fleetqox/state",
                        "traffic_class": "state",
                        "max_accepted_frames": 1,
                        "allowed_publishers": ["state-publisher"],
                    },
                ],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    build_root = "/work/.tmp_fleetrmw_quic_admission_build"
    install = "/work/.tmp_fleetrmw_quic_admission_install"
    log_root = "/work/.tmp_fleetrmw_quic_admission_log"
    cert_result = run(
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
            certificate_command(certs, root),
        ]
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
            f"rm -rf {build_root} {install} {log_root} && "
            f"colcon --log-base {log_root} build --base-paths ros2_ws/src "
            "--packages-select rmw_fleetqox_cpp "
            f"--build-base {build_root} --install-base {install} "
            "--cmake-args -DCMAKE_BUILD_TYPE=Release",
        ]
    )
    network = f"fleetrmw-admission-net-{os.getpid()}"
    network_result = run(["docker", "network", "create", network])
    rows: list[dict[str, Any]] = []
    try:
        if (
            cert_result.returncode == 0
            and build.returncode == 0
            and network_result.returncode == 0
        ):
            for index in range(1, run_count + 1):
                rows.append(
                    run_case(
                        root=root,
                        image=image,
                        network=network,
                        install=install,
                        temp_root=temp_root,
                        index=index,
                    )
                )
    finally:
        run(["docker", "network", "rm", network])
        if not keep_temp:
            cleanup = run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--entrypoint",
                    "bash",
                    "-v",
                    f"{root}:/work",
                    image,
                    "-lc",
                    f"rm -rf {build_root} {install} {log_root}",
                ]
            )
            if cleanup.returncode == 0:
                shutil.rmtree(temp_root, ignore_errors=True)
    successful = sum(row.get("status") == "ok" for row in rows)
    status = (
        "ok"
        if cert_result.returncode == 0
        and build.returncode == 0
        and network_result.returncode == 0
        and len(rows) == run_count
        and successful == run_count
        else "failed"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "run_count": run_count,
        "successful_runs": successful,
        "failed_run_count": run_count - successful,
        "container_count_per_run": 2,
        "real_quic_v1_h3": True,
        "tls_peer_verification_required": True,
        "fleet_gateway_admission_policy_claim": status == "ok",
        "per_stream_traffic_class_quota_claim": status == "ok",
        "shared_fleet_quota_claim": status == "ok",
        "publisher_admission_allowlist_claim": status == "ok",
        "admission_rejection_state_isolation_claim": status == "ok",
        "admission_epoch_replenishment_claim": status == "ok",
        "production_quic_backend_claim": False,
        "production_readiness": False,
        "certificate_returncode": cert_result.returncode,
        "build_returncode": build.returncode,
        "build_stderr": build.stderr[-4000:],
        "network_returncode": network_result.returncode,
        "runs": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--keep-temp", action="store_true")
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_quic_admission_probe_summary.json",
    )
    args = parser.parse_args()
    summary = run_probe(
        root=ROOT,
        image=args.image,
        iterations=args.iterations,
        keep_temp=args.keep_temp,
    )
    output = ROOT / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")
    print("fleetrmw-quic-admission-probe")
    print(f"  status: {summary['status']}")
    print(f"  successful_runs: {summary['successful_runs']}/{summary['run_count']}")
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
