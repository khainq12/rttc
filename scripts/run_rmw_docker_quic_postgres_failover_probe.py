#!/usr/bin/env python3
"""Validate QUIC gateway takeover against networked PostgreSQL durable state."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any
from urllib.parse import urlsplit

try:
    from scripts.run_rmw_docker_quic_admission_probe import certificate_command
    from scripts.run_rmw_docker_quic_durable_admission_failover_probe import probe_ok
    from scripts.run_rmw_docker_quic_stateful_gateway_probe import (
        DEFAULT_IMAGE,
        SERVICE_SCHEMA_VERSION,
        json_rows,
        run,
        wait_service_ready,
    )
    from scripts.run_rmw_docker_quic_writer_fencing_probe import (
        run_client,
        stop_service,
    )
except ModuleNotFoundError:
    from run_rmw_docker_quic_admission_probe import certificate_command
    from run_rmw_docker_quic_durable_admission_failover_probe import probe_ok
    from run_rmw_docker_quic_stateful_gateway_probe import (
        DEFAULT_IMAGE,
        SERVICE_SCHEMA_VERSION,
        json_rows,
        run,
        wait_service_ready,
    )
    from run_rmw_docker_quic_writer_fencing_probe import run_client, stop_service


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "fleetrmw.docker_quic_postgresql_failover_probe.v1"
POSTGRES_SCHEMA_VERSION = "fleetrmw.quic_gateway_postgresql_durable_state.v1"
GATEWAY_ALIAS = "fleetqox-admission-gateway"
POSTGRES_ALIAS = "fleetqox-postgres"
POSTGRES_IMAGE = "postgres:16-alpine"
POSTGRES_PASSWORD = "fleetqox-postgresql-probe"


def service_command(
    *, root: Path, temp_root: Path, holder: str, qlogs: Path
) -> str:
    certs = temp_root / "certs"
    policy = temp_root / "admission-policy.json"
    dsn = (
        f"postgresql://postgres:{POSTGRES_PASSWORD}@{POSTGRES_ALIAS}:5432/fleetqox"
    )
    return (
        "tc qdisc replace dev eth0 root netem delay 5ms 1ms loss 0.2% && "
        "tc qdisc show dev eth0 && "
        "exec python3 scripts/fleetrmw_quic_gateway_service.py "
        "--host 0.0.0.0 --port 4504 "
        f"--certificate /work/{(certs / 'server.crt').relative_to(root)} "
        f"--private-key /work/{(certs / 'server.key').relative_to(root)} "
        f"--admission-policy /work/{policy.relative_to(root)} "
        f"--state-db '{dsn}' "
        f"--writer-lease-instance-id {holder} --writer-lease-ms 3000 "
        f"--qlog-dir /work/{qlogs.relative_to(root)} "
        "--max-frames-per-topic 8 --max-frame-bytes 65536"
    )


def start_service(
    *, root: Path, image: str, network: str, name: str, command: str,
    wait_for_lease: bool,
) -> bool:
    if wait_for_lease:
        command += " --writer-lease-wait-timeout-ms 10000 --writer-lease-retry-ms 100"
    started = run([
        "docker", "run", "-d", "--name", name,
        "--network", network, "--network-alias", GATEWAY_ALIAS,
        "--cap-add", "NET_ADMIN", "--entrypoint", "bash",
        "-v", f"{root}:/work", "-w", "/work", image, "-lc", command,
    ])
    if started.returncode != 0:
        return False
    return (
        wait_standby_waiting(name)
        if wait_for_lease
        else wait_service_ready(name)
    )


def wait_standby_waiting(container: str, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        logs = run(["docker", "logs", container]).stdout
        if (
            '"status": "writer_lease_waiting"' in logs
            and '"instance_id": "gateway-b"' in logs
        ):
            inspected = run(
                ["docker", "inspect", "-f", "{{.State.Running}}", container]
            )
            return inspected.returncode == 0 and inspected.stdout.strip() == "true"
        inspected = run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container]
        )
        if inspected.returncode != 0 or inspected.stdout.strip() != "true":
            return False
        time.sleep(0.1)
    return False


def wait_postgres_init_complete(name: str, timeout_s: float = 15.0) -> bool:
    # The official postgres image runs its CREATE DATABASE init scripts
    # against a temporary local-only server before starting the real one;
    # pg_isready (or any real connection attempt) made during that window
    # can hit "FATAL: database ... does not exist" even though the server
    # is technically "ready". Waiting for this specific log line instead
    # guarantees fleetqox has actually been created, without generating
    # any noisy failed-connection log entries of our own.
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        logs = run(["docker", "logs", name]).stdout
        if "PostgreSQL init process complete; ready for start up." in logs:
            return True
        inspected = run(["docker", "inspect", "-f", "{{.State.Running}}", name])
        if inspected.returncode != 0 or inspected.stdout.strip() != "true":
            return False
        time.sleep(0.2)
    return False


def start_postgres(*, network: str, name: str) -> dict[str, Any]:
    started = run([
        "docker", "run", "-d", "--name", name,
        "--network", network, "--network-alias", POSTGRES_ALIAS,
        "-e", f"POSTGRES_PASSWORD={POSTGRES_PASSWORD}",
        "-e", "POSTGRES_DB=fleetqox", POSTGRES_IMAGE,
    ])
    ready = started.returncode == 0 and wait_postgres_init_complete(name)
    version = run([
        "docker", "exec", name, "psql", "-U", "postgres", "-d", "fleetqox",
        "-Atc", "SHOW server_version",
    ]) if ready else subprocess.CompletedProcess([], 1, "", "not_ready")
    return {
        "status": "ok" if ready and version.returncode == 0 else "failed",
        "ready": ready,
        "image": POSTGRES_IMAGE,
        "server_version": version.stdout.strip(),
        "start_returncode": started.returncode,
        "start_stderr": "" if started.returncode == 0 else started.stderr[-2000:],
    }


def stop_postgres(name: str) -> dict[str, Any]:
    inspected = run(["docker", "inspect", "-f", "{{.State.Running}}", name])
    running_before_stop = (
        inspected.returncode == 0 and inspected.stdout.strip() == "true"
    )
    logs = run(["docker", "logs", name]).stdout
    stopped = run(["docker", "rm", "-f", name])
    return {
        "running_through_gateway_takeover": running_before_stop,
        "clean_database_logs": "FATAL" not in logs and "PANIC" not in logs,
        "remove_returncode": stopped.returncode,
        "logs": "" if "FATAL" not in logs and "PANIC" not in logs else logs[-4000:],
    }


def postgres_service_ok(
    row: dict[str, Any], *, mode: str, holder: str, token: int,
    automatic_wait: bool, resume_requires_wait: bool = True,
) -> bool:
    metrics = row.get("metrics", {})
    durable = metrics.get("durable_state", {})
    admission = metrics.get("admission", {})
    transport = row.get("transport_metrics", {})
    lease = durable.get("writer_lease", {})
    endpoint = urlsplit(str(durable.get("endpoint", "")))
    common = (
        row.get("schema_version") == SERVICE_SCHEMA_VERSION
        and row.get("status") == "stopped"
        and row.get("clean_teardown") is True
        and row.get("admission_policy_configured") is True
        and row.get("durable_state_configured") is True
        and row.get("writer_lease_configured") is True
        and row.get("writer_lease_instance_id") == holder
        and row.get("writer_lease_ms") == 3000
        and row.get("writer_lease_lost") is False
        and row.get("automatic_standby_wait_configured") is automatic_wait
        and metrics.get("durable_state_enabled") is True
        and metrics.get("durable_persistence_failures") == 0
        and metrics.get("retained_frames") == 2
        and metrics.get("durable_writer_lease_acquires") == 1
        and metrics.get("durable_writer_lease_renewals", 0) >= 1
        and metrics.get("durable_writer_lease_failures") == 0
        and durable.get("schema_version") == POSTGRES_SCHEMA_VERSION
        and durable.get("backend") == "postgresql"
        and durable.get("synchronous_commit") == "on"
        and durable.get("retained_frame_count") == 2
        and durable.get("dedup_key_count") == 2
        and durable.get("consumer_cursor_count") == 0
        and durable.get("admission_state_count") == 1
        and endpoint.username is None
        and endpoint.password is None
        and POSTGRES_PASSWORD not in str(durable.get("endpoint", ""))
        and lease.get("holder_id") == holder
        and lease.get("fence_token") == token
        and lease.get("expires_unix_ms", 0) > 0
        and admission.get("accepted_total") == 1
        and admission.get("accepted_cumulative") == 2
        and admission.get("accepted_by_class") == {"control": 2}
        and admission.get("repair_admitted_count") == 1
        and admission.get("repair_allocated_bytes", 0) > 0
        and transport.get("connections_created") == 1
        and transport.get("h3_sessions_negotiated") == 1
    )
    if not common:
        return False
    if mode == "seed":
        return (
            row.get("writer_lease_acquisition_attempts") == 1
            and metrics.get("requests_total") == 2
            and metrics.get("post_requests") == 2
            and metrics.get("accepted_frames") == 2
            and metrics.get("durable_frame_commits") == 2
            and metrics.get("durable_admission_commits") == 2
            and metrics.get("recovered_frames") == 0
            and metrics.get("recovered_admission_state") == 0
            and admission.get("repair_deferred_count") == 0
        )
    acquisition_ok = (
        row.get("writer_lease_acquisition_attempts", 0) >= 2
        and row.get("writer_lease_acquisition_wait_ms", 0) > 0
        if resume_requires_wait
        else row.get("writer_lease_acquisition_attempts") == 1
    )
    return (
        acquisition_ok
        and metrics.get("requests_total") == 1
        and metrics.get("post_requests") == 1
        and metrics.get("accepted_frames") == 0
        and metrics.get("durable_frame_commits") == 0
        and metrics.get("durable_admission_commits") == 0
        and metrics.get("recovered_frames") == 2
        and metrics.get("recovered_dedup_keys") == 2
        and metrics.get("recovered_admission_state") == 1
        and admission.get("repair_deferred_count") == 1
        and admission.get("rejected_by_reason") == {"stream_quota_exhausted": 1}
    )


def phase_result(
    *, ready: bool, client: subprocess.CompletedProcess[str], exit_code: int,
    logs: str, service: dict[str, Any], mode: str, holder: str, token: int,
    automatic_wait: bool, qlog_dirs: tuple[Path, Path],
) -> dict[str, Any]:
    rows = json_rows(client.stdout)
    probe = rows[-1] if rows else {}
    qlogs = [
        path for directory in qlog_dirs for path in directory.glob("*")
        if path.is_file()
    ]
    netem_ok = "qdisc netem" in logs and "qdisc netem" in client.stdout
    qlog_ok = bool(qlogs) and all(path.stat().st_size > 0 for path in qlogs)
    service_valid = postgres_service_ok(
        service, mode=mode, holder=holder, token=token,
        automatic_wait=automatic_wait,
    )
    ok = (
        ready and client.returncode == 0 and exit_code == 0
        and probe_ok(probe, mode) and service_valid and netem_ok and qlog_ok
    )
    return {
        "mode": mode,
        "status": "ok" if ok else "failed",
        "probe": probe,
        "service": service,
        "postgresql_service_validation": service_valid,
        "netem_configured_both_containers": netem_ok,
        "qlog_file_count": len(qlogs),
        "qlog_total_bytes": sum(path.stat().st_size for path in qlogs),
        "client_returncode": client.returncode,
        "service_exit_code": exit_code,
        "client_stdout": "" if ok else client.stdout,
        "client_stderr": "" if ok else client.stderr,
        "service_logs": "" if ok else logs,
    }


def phase_artifact_ok(
    phase: dict[str, Any], *, mode: str, holder: str, token: int,
    automatic_wait: bool,
) -> bool:
    return (
        phase.get("status") == "ok"
        and phase.get("client_returncode") == 0
        and phase.get("service_exit_code") == 0
        and phase.get("netem_configured_both_containers") is True
        and phase.get("qlog_file_count", 0) > 0
        and phase.get("qlog_total_bytes", 0) > 0
        and probe_ok(phase.get("probe", {}), mode)
        and postgres_service_ok(
            phase.get("service", {}), mode=mode, holder=holder, token=token,
            automatic_wait=automatic_wait,
        )
    )


def case_ok(row: dict[str, Any]) -> bool:
    database = row.get("database", {})
    shutdown = row.get("database_shutdown", {})
    return (
        database.get("status") == "ok"
        and database.get("ready") is True
        and database.get("image") == POSTGRES_IMAGE
        and bool(database.get("server_version"))
        and shutdown.get("running_through_gateway_takeover") is True
        and shutdown.get("clean_database_logs") is True
        and shutdown.get("remove_returncode") == 0
        and row.get("standby_observed_waiting_while_active_live") is True
        and 0 <= row.get("takeover_latency_ms", -1) < 8000
        and phase_artifact_ok(
            row.get("active", {}), mode="seed", holder="gateway-a", token=1,
            automatic_wait=False,
        )
        and phase_artifact_ok(
            row.get("standby", {}), mode="resume", holder="gateway-b", token=2,
            automatic_wait=True,
        )
    )


def run_case(
    *, root: Path, image: str, network: str, install: str,
    temp_root: Path, index: int,
) -> dict[str, Any]:
    suffix = f"{os.getpid()}-{index}"
    case_root = temp_root / f"run-{index}"
    certs = temp_root / "certs"
    qlog_dirs = {
        name: case_root / name
        for name in (
            "active-service-qlogs", "active-client-qlogs",
            "standby-service-qlogs", "standby-client-qlogs",
        )
    }
    for path in qlog_dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    database_name = f"fleetrmw-postgresql-db-{suffix}"
    active_name = f"fleetrmw-postgresql-active-{suffix}"
    standby_name = f"fleetrmw-postgresql-standby-{suffix}"
    database = start_postgres(network=network, name=database_name)
    active_ready = False
    standby_waiting = False
    standby_ready = False
    takeover_latency_ms = -1
    seed_client = subprocess.CompletedProcess([], 1, "", "database_not_ready")
    resume_client = subprocess.CompletedProcess([], 1, "", "standby_not_ready")
    active_exit = standby_exit = -1
    active_logs = standby_logs = ""
    active_service: dict[str, Any] = {}
    standby_service: dict[str, Any] = {}
    try:
        if database["status"] == "ok":
            active_ready = start_service(
                root=root, image=image, network=network, name=active_name,
                command=service_command(
                    root=root, temp_root=temp_root, holder="gateway-a",
                    qlogs=qlog_dirs["active-service-qlogs"],
                ),
                wait_for_lease=False,
            )
        if active_ready:
            seed_client = run_client(
                root=root, image=image, network=network, install=install,
                name=f"fleetrmw-postgresql-seed-{suffix}", certs=certs,
                qlogs=qlog_dirs["active-client-qlogs"], mode="seed",
            )
        if active_ready and seed_client.returncode == 0:
            standby_waiting = start_service(
                root=root, image=image, network=network, name=standby_name,
                command=service_command(
                    root=root, temp_root=temp_root, holder="gateway-b",
                    qlogs=qlog_dirs["standby-service-qlogs"],
                ),
                wait_for_lease=True,
            )
        if standby_waiting:
            time.sleep(1.2)
        takeover_started = time.monotonic()
        active_exit, active_logs, active_service = stop_service(active_name)
        if standby_waiting:
            standby_ready = wait_service_ready(standby_name, timeout_s=8.0)
            takeover_latency_ms = round(
                (time.monotonic() - takeover_started) * 1000.0
            )
        if standby_ready:
            resume_client = run_client(
                root=root, image=image, network=network, install=install,
                name=f"fleetrmw-postgresql-resume-{suffix}", certs=certs,
                qlogs=qlog_dirs["standby-client-qlogs"], mode="resume",
            )
            time.sleep(1.2)
        standby_exit, standby_logs, standby_service = stop_service(standby_name)
    finally:
        run(["docker", "rm", "-f", active_name])
        run(["docker", "rm", "-f", standby_name])
        database_shutdown = stop_postgres(database_name)

    active = phase_result(
        ready=active_ready, client=seed_client, exit_code=active_exit,
        logs=active_logs, service=active_service, mode="seed",
        holder="gateway-a", token=1, automatic_wait=False,
        qlog_dirs=(
            qlog_dirs["active-service-qlogs"], qlog_dirs["active-client-qlogs"]
        ),
    )
    standby = phase_result(
        ready=standby_ready, client=resume_client, exit_code=standby_exit,
        logs=standby_logs, service=standby_service, mode="resume",
        holder="gateway-b", token=2, automatic_wait=True,
        qlog_dirs=(
            qlog_dirs["standby-service-qlogs"], qlog_dirs["standby-client-qlogs"]
        ),
    )
    result = {
        "index": index,
        "database": database,
        "database_shutdown": database_shutdown,
        "standby_observed_waiting_while_active_live": standby_waiting,
        "takeover_latency_ms": takeover_latency_ms,
        "active": active,
        "standby": standby,
    }
    result["status"] = "ok" if case_ok(result) else "failed"
    return result


def run_probe(
    *, root: Path, image: str, iterations: int, keep_temp: bool
) -> dict[str, Any]:
    run_count = max(1, iterations)
    temp_root = root / f".tmp_fleetrmw_quic_postgresql_failover_{os.getpid()}"
    certs = temp_root / "certs"
    certs.mkdir(parents=True, exist_ok=True)
    policy = {
        "schema_version": "fleetrmw.quic_gateway_admission_policy.v1",
        "default_action": "deny",
        "max_accepted_frames": 1,
        "rules": [{
            "domain_id": 42,
            "topic": "/fleetqox/durable_admission",
            "traffic_class": "control",
            "max_accepted_frames": 1,
            "allowed_publishers": ["durable-admission-publisher"],
        }],
        "repair": {
            "capacity_bytes": 1024,
            "max_admitted": 1,
            "paths": [{
                "path_id": "private_5g", "latency_ms": 20.0,
                "loss": 0.01, "failure_domain": "private_5g",
            }],
        },
    }
    (temp_root / "admission-policy.json").write_text(
        json.dumps(policy, sort_keys=True) + "\n", encoding="utf-8"
    )
    build_root = "/work/.tmp_fleetrmw_quic_postgresql_failover_build"
    install = "/work/.tmp_fleetrmw_quic_postgresql_failover_install"
    log_root = "/work/.tmp_fleetrmw_quic_postgresql_failover_log"
    cert_result = run([
        "docker", "run", "--rm", "--entrypoint", "bash",
        "-v", f"{root}:/work", "-w", "/work", image, "-lc",
        certificate_command(certs, root),
    ])
    build = run([
        "docker", "run", "--rm", "--entrypoint", "bash",
        "-v", f"{root}:/work", "-w", "/work", image, "-lc",
        "source /opt/ros/jazzy/setup.bash && "
        f"rm -rf {build_root} {install} {log_root} && "
        f"colcon --log-base {log_root} build --base-paths ros2_ws/src "
        "--packages-select rmw_fleetqox_cpp "
        f"--build-base {build_root} --install-base {install} "
        "--cmake-args -DCMAKE_BUILD_TYPE=Release",
    ])
    network = f"fleetrmw-postgresql-failover-net-{os.getpid()}"
    network_result = run(["docker", "network", "create", network])
    rows: list[dict[str, Any]] = []
    try:
        if cert_result.returncode == build.returncode == network_result.returncode == 0:
            for index in range(1, run_count + 1):
                rows.append(run_case(
                    root=root, image=image, network=network, install=install,
                    temp_root=temp_root, index=index,
                ))
    finally:
        run(["docker", "network", "rm", network])
        if not keep_temp:
            cleanup = run([
                "docker", "run", "--rm", "--entrypoint", "bash",
                "-v", f"{root}:/work", image, "-lc",
                f"rm -rf {build_root} {install} {log_root}",
            ])
            if cleanup.returncode == 0:
                shutil.rmtree(temp_root, ignore_errors=True)
    successful = sum(row.get("status") == "ok" for row in rows)
    status = "ok" if (
        cert_result.returncode == build.returncode == network_result.returncode == 0
        and len(rows) == successful == run_count
    ) else "failed"
    latencies = [
        row["takeover_latency_ms"] for row in rows
        if row.get("takeover_latency_ms", -1) >= 0
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "run_count": run_count,
        "successful_runs": successful,
        "failed_run_count": run_count - successful,
        "container_count_per_run": 5,
        "gateway_instance_count_per_run": 2,
        "database_instance_count_per_run": 1,
        "real_quic_v1_h3": True,
        "networked_postgresql_durable_state_claim": status == "ok",
        "synchronous_commit_claim": status == "ok",
        "frame_and_admission_single_transaction_claim": status == "ok",
        "postgresql_writer_fencing_claim": status == "ok",
        "automatic_gateway_takeover_claim": status == "ok",
        "post_takeover_admission_recovery_claim": status == "ok",
        "max_takeover_latency_ms": max(latencies) if latencies else None,
        "database_process_failover_claim": False,
        "replicated_database_claim": False,
        "consensus_leader_election_claim": False,
        "active_active_consensus_claim": False,
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
        default="results_rmw_socket/docker_quic_postgresql_failover_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_probe(
        root=ROOT, image=args.image, iterations=args.iterations,
        keep_temp=args.keep_temp,
    )
    output = ROOT / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print("fleetrmw-quic-postgresql-failover-probe")
        print(f"  status: {summary['status']}")
        print(f"  successful_runs: {summary['successful_runs']}/{summary['run_count']}")
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
