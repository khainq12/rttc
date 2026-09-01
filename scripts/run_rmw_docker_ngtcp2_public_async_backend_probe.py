#!/usr/bin/env python3
"""Prove bounded async backend dispatch in the public ngtcp2 mTLS edge."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_rmw_docker_ngtcp2_public_stateful_gateway_probe import (
    BACKEND_SCHEMA_VERSION,
    PUBLISHER_URI,
    stateful_certificate_command,
)
from scripts.fleetqox_public_quic_backend_delay_proxy import (
    SCHEMA_VERSION as PROXY_SCHEMA_VERSION,
)


SCHEMA_VERSION = "fleetrmw.docker_ngtcp2_public_async_backend.v1"
DEFAULT_SERVER_IMAGE = "localhost/fleetrmw/ngtcp2-public-mtls:0.12.1"
DEFAULT_BASE_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"
DELAY_MS = 6000


def run(
    command: list[str],
    *,
    timeout: float = 600.0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def docker_logs(container: str) -> str:
    result = run(["docker", "logs", container], timeout=15.0)
    return result.stdout + result.stderr


def wait_for_log(
    container: str,
    marker: str,
    *,
    count: int = 1,
    timeout_s: float = 10.0,
) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        logs = docker_logs(container)
        if logs.count(marker) >= count:
            return True
        state = run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container],
            timeout=10.0,
        )
        if state.returncode != 0 or state.stdout.strip() != "true":
            return False
        time.sleep(0.05)
    return False


def stop_server(container: str) -> tuple[int, str]:
    stop = run(
        [
            "docker",
            "exec",
            container,
            "bash",
            "-lc",
            'kill -INT "$(cat /tmp/fleetqox-public-server.pid)"',
        ],
        timeout=20.0,
    )
    waited = run(["docker", "wait", container], timeout=30.0)
    logs = docker_logs(container)
    exit_code = (
        int(waited.stdout.strip())
        if waited.returncode == 0 and waited.stdout.strip().isdigit()
        else -1
    )
    return exit_code if stop.returncode == 0 else -1, logs


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def client_command(
    *,
    root: Path,
    certs: Path,
    consumer_id: str,
    qlog: Path,
) -> str:
    cert_root = f"/work/{certs.relative_to(root)}"
    qlog_path = f"/work/{qlog.relative_to(root)}"
    uri = (
        "https://fleetqox-mtls-gateway:4433/fleetrmw/v1/frames?"
        "domain_id=42&topic=%2Ffleetqox%2Fasync&consumer_id="
        f"{consumer_id}"
    )
    return (
        f"cp {cert_root}/server-ca.crt "
        "/usr/local/share/ca-certificates/fleetqox-public-ca.crt && "
        "update-ca-certificates >/dev/null 2>&1 && "
        "tc qdisc replace dev eth0 root netem delay 9ms 2ms && "
        "gtlsclient fleetqox-mtls-gateway 4433 "
        f"{shlex.quote(uri)} "
        f"--key={cert_root}/stateful-client.key "
        f"--cert={cert_root}/stateful-client.crt "
        "--disable-early-data --exit-on-all-streams-close "
        "--no-quic-dump --no-http-dump "
        f"--qlog-file={qlog_path}"
    )


def client_docker_args(
    *,
    root: Path,
    image: str,
    network: str,
    name: str,
    command: str,
) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
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
        command,
    ]


def start_client(
    *,
    root: Path,
    image: str,
    network: str,
    name: str,
    command: str,
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        client_docker_args(
            root=root,
            image=image,
            network=network,
            name=name,
            command=command,
        ),
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def finish_client(
    process: subprocess.Popen[str],
    *,
    timeout_s: float = 15.0,
) -> subprocess.CompletedProcess[str]:
    stdout, stderr = process.communicate(timeout=timeout_s)
    return subprocess.CompletedProcess(
        process.args,
        process.returncode,
        stdout,
        stderr,
    )


def start_warm_client_container(
    *,
    root: Path,
    image: str,
    network: str,
    name: str,
    certs: Path,
) -> subprocess.CompletedProcess[str]:
    cert_root = f"/work/{certs.relative_to(root)}"
    command = (
        f"cp {cert_root}/server-ca.crt "
        "/usr/local/share/ca-certificates/fleetqox-public-ca.crt && "
        "update-ca-certificates >/dev/null 2>&1 && "
        "tc qdisc replace dev eth0 root netem delay 9ms 2ms && "
        "touch /tmp/fleetqox-client-ready && exec sleep infinity"
    )
    return run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
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
            command,
        ],
        timeout=30.0,
    )


def wait_warm_client_ready(container: str, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ready = run(
            [
                "docker",
                "exec",
                container,
                "test",
                "-f",
                "/tmp/fleetqox-client-ready",
            ],
            timeout=10.0,
        )
        if ready.returncode == 0:
            return True
        time.sleep(0.05)
    return False


def warm_client_exec_args(
    *,
    root: Path,
    container: str,
    certs: Path,
    consumer_id: str,
    qlog: Path,
) -> list[str]:
    cert_root = f"/work/{certs.relative_to(root)}"
    qlog_path = f"/work/{qlog.relative_to(root)}"
    uri = (
        "https://fleetqox-mtls-gateway:4433/fleetrmw/v1/frames?"
        "domain_id=42&topic=%2Ffleetqox%2Fasync&consumer_id="
        f"{consumer_id}"
    )
    command = (
        "gtlsclient fleetqox-mtls-gateway 4433 "
        f"{shlex.quote(uri)} "
        f"--key={cert_root}/stateful-client.key "
        f"--cert={cert_root}/stateful-client.crt "
        "--disable-early-data --exit-on-all-streams-close "
        "--no-quic-dump --no-http-dump "
        f"--qlog-file={qlog_path}"
    )
    return ["docker", "exec", container, "bash", "-lc", command]


def start_warm_client(
    *,
    root: Path,
    container: str,
    certs: Path,
    consumer_id: str,
    qlog: Path,
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        warm_client_exec_args(
            root=root,
            container=container,
            certs=certs,
            consumer_id=consumer_id,
            qlog=qlog,
        ),
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def response_has_status(
    result: subprocess.CompletedProcess[str],
    status: int,
) -> bool:
    return result.returncode == 0 and f"[:status: {status}]" in (
        result.stdout + result.stderr
    )


def server_command(
    *,
    root: Path,
    certs: Path,
    run_root: Path,
    idle_timeout: str | None = None,
) -> tuple[str, Path, Path]:
    cert_root = f"/work/{certs.relative_to(root)}"
    htdocs = run_root / "htdocs"
    server_qlogs = run_root / "server-qlogs"
    backend_summary = run_root / "backend-summary.json"
    proxy_summary = run_root / "proxy-summary.json"
    htdocs.mkdir(parents=True, exist_ok=True)
    server_qlogs.mkdir(parents=True, exist_ok=True)
    (htdocs / "index.html").write_text("async backend probe\n", encoding="utf-8")
    htdocs_root = f"/work/{htdocs.relative_to(root)}"
    server_qlog_root = f"/work/{server_qlogs.relative_to(root)}"
    backend_summary_path = f"/work/{backend_summary.relative_to(root)}"
    proxy_summary_path = f"/work/{proxy_summary.relative_to(root)}"
    upstream_socket = "/tmp/fleetqox-async-upstream.sock"
    proxy_socket = "/tmp/fleetqox-async-proxy.sock"
    command = (
        "set -uo pipefail; "
        f"rm -f {upstream_socket} {proxy_socket} "
        f"{shlex.quote(backend_summary_path)} {shlex.quote(proxy_summary_path)}; "
        "python3 -m fleetqox.public_quic_gateway_backend "
        f"--socket {upstream_socket} --max-frames-per-topic 8 "
        "--max-frame-bytes 65536 "
        f"--summary-json {shlex.quote(backend_summary_path)} & "
        "backend_pid=$!; "
        "for attempt in $(seq 1 100); do "
        f"test -S {upstream_socket} && break; sleep 0.05; done; "
        f"test -S {upstream_socket}; "
        "python3 scripts/fleetqox_public_quic_backend_delay_proxy.py "
        f"--listen-socket {proxy_socket} --upstream-socket {upstream_socket} "
        "--delay-prefix slow --delay-prefix queue- "
        f"--delay-ms {DELAY_MS} --workers 8 --max-in-flight 16 "
        f"--summary-json {shlex.quote(proxy_summary_path)} & "
        "proxy_pid=$!; "
        "for attempt in $(seq 1 100); do "
        f"test -S {proxy_socket} && break; sleep 0.05; done; "
        f"test -S {proxy_socket}; "
        "tc qdisc replace dev eth0 root netem delay 11ms 2ms; "
        "fleetqox-public-mtls-server "
        f"--htdocs={shlex.quote(htdocs_root)} "
        f"--qlog-dir={shlex.quote(server_qlog_root)} "
        f"{f'--timeout={shlex.quote(idle_timeout)} ' if idle_timeout else ''}"
        "--verify-client --no-quic-dump --no-http-dump "
        f"'*' 4433 {cert_root}/server.key {cert_root}/server.crt & "
        "server_pid=$!; echo \"$server_pid\" >/tmp/fleetqox-public-server.pid; "
        "wait \"$server_pid\"; server_rc=$?; "
        "kill -TERM \"$proxy_pid\" 2>/dev/null || true; "
        "wait \"$proxy_pid\" || true; "
        "kill -TERM \"$backend_pid\" 2>/dev/null || true; "
        "wait \"$backend_pid\" || true; exit \"$server_rc\""
    )
    return command, backend_summary, proxy_summary


def start_server(
    *,
    root: Path,
    image: str,
    network: str,
    name: str,
    certs: Path,
    run_root: Path,
    workers: int,
    queue_capacity: int,
    idle_timeout: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    command, backend_summary, proxy_summary = server_command(
        root=root,
        certs=certs,
        run_root=run_root,
        idle_timeout=idle_timeout,
    )
    cert_root = f"/work/{certs.relative_to(root)}"
    result = run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--network",
            network,
            "--network-alias",
            "fleetqox-mtls-gateway",
            "--cap-add",
            "NET_ADMIN",
            "--entrypoint",
            "bash",
            "-e",
            f"FLEETQOX_GNUTLS_CLIENT_CA={cert_root}/client-ca.crt",
            "-e",
            f"FLEETQOX_GNUTLS_CLIENT_CRL={cert_root}/client.crl.pem",
            "-e",
            f"FLEETQOX_GNUTLS_REQUIRED_CLIENT_URI_SAN={PUBLISHER_URI}",
            "-e",
            "FLEETQOX_GNUTLS_CLIENT_URI_PREFIX=spiffe://fleetqox/publishers/",
            "-e",
            "FLEETQOX_STATE_BACKEND_SOCKET=/tmp/fleetqox-async-proxy.sock",
            "-e",
            f"FLEETQOX_STATE_BACKEND_WORKERS={workers}",
            "-e",
            f"FLEETQOX_STATE_BACKEND_QUEUE_CAPACITY={queue_capacity}",
            "-v",
            f"{root}:/work",
            "-w",
            "/work",
            image,
            "-lc",
            command,
        ],
        timeout=30.0,
    )
    return result, backend_summary, proxy_summary


def run_nonblocking_phase(
    *,
    root: Path,
    image: str,
    network: str,
    certs: Path,
    run_root: Path,
    suffix: str,
) -> dict[str, Any]:
    server_name = f"fq-public-async-open-{suffix}"
    start, backend_path, proxy_path = start_server(
        root=root,
        image=image,
        network=network,
        name=server_name,
        certs=certs,
        run_root=run_root,
        workers=2,
        queue_capacity=8,
    )
    ready = (
        start.returncode == 0
        and wait_for_log(
            server_name,
            "FLEETQOX_STATE_BACKEND_ASYNC_READY workers=2 queue_capacity=8",
        )
        and wait_for_log(server_name, PROXY_SCHEMA_VERSION)
    )
    slow: subprocess.Popen[str] | None = None
    slow_result = subprocess.CompletedProcess([], 1, "", "not_started")
    fast_result = subprocess.CompletedProcess([], 1, "", "not_started")
    slow_in_flight_when_fast_completed = False
    slow_elapsed_ms = 0.0
    fast_elapsed_ms = 0.0
    server_exit = -1
    logs = ""
    try:
        if ready:
            qlogs = run_root / "client-qlogs"
            qlogs.mkdir(parents=True, exist_ok=True)
            slow = start_client(
                root=root,
                image=image,
                network=network,
                name=f"fq-public-async-slow-{suffix}",
                command=client_command(
                    root=root,
                    certs=certs,
                    consumer_id="slow",
                    qlog=qlogs / "slow.qlog",
                ),
            )
            slow_start = time.monotonic()
            delayed = wait_for_log(
                server_name,
                "FLEETQOX_BACKEND_DELAY_PROXY_DELAYING consumer_id=slow",
                timeout_s=8.0,
            )
            fast_start = time.monotonic()
            fast_result = run(
                client_docker_args(
                    root=root,
                    image=image,
                    network=network,
                    name=f"fq-public-async-fast-{suffix}",
                    command=client_command(
                        root=root,
                        certs=certs,
                        consumer_id="fast",
                        qlog=qlogs / "fast.qlog",
                    ),
                ),
                timeout=15.0,
            )
            fast_elapsed_ms = (time.monotonic() - fast_start) * 1000.0
            slow_in_flight_when_fast_completed = delayed and slow.poll() is None
            slow_result = finish_client(slow)
            slow_elapsed_ms = (time.monotonic() - slow_start) * 1000.0
        if ready:
            server_exit, logs = stop_server(server_name)
    finally:
        if slow is not None and slow.poll() is None:
            run(
                ["docker", "rm", "-f", f"fq-public-async-slow-{suffix}"],
                timeout=15.0,
            )
            slow.kill()
        run(["docker", "rm", "-f", server_name], timeout=20.0)
    backend = load_json(backend_path)
    proxy = load_json(proxy_path)
    qlogs = list((run_root / "client-qlogs").glob("*.qlog"))
    ok = (
        ready
        and response_has_status(fast_result, 204)
        and response_has_status(slow_result, 204)
        and slow_in_flight_when_fast_completed
        and fast_elapsed_ms < slow_elapsed_ms
        and server_exit == 0
        and backend.get("schema_version") == BACKEND_SCHEMA_VERSION
        and backend.get("clean_teardown") is True
        and proxy.get("schema_version") == PROXY_SCHEMA_VERSION
        and proxy.get("clean_teardown") is True
        and proxy.get("requests_total") == 2
        and proxy.get("delayed_requests") == 1
        and proxy.get("forwarded_requests") == 2
        and proxy.get("failures") == 0
        and proxy.get("max_active_requests", 0) >= 2
        and logs.count("FLEETQOX_STATE_BACKEND_ASYNC_QUEUED") == 2
        and logs.count("FLEETQOX_STATE_BACKEND_RESPONSE") == 2
        and "FLEETQOX_STATE_BACKEND_QUEUE_FULL" not in logs
        and len(qlogs) == 2
        and all(path.stat().st_size > 0 for path in qlogs)
    )
    return {
        "status": "ok" if ok else "failed",
        "server_ready": ready,
        "server_exit_code": server_exit,
        "fast_http_204": response_has_status(fast_result, 204),
        "slow_http_204": response_has_status(slow_result, 204),
        "slow_in_flight_when_fast_completed": slow_in_flight_when_fast_completed,
        "fast_elapsed_ms": round(fast_elapsed_ms, 3),
        "slow_elapsed_ms": round(slow_elapsed_ms, 3),
        "backend": backend,
        "proxy": proxy,
        "server_async_queued_count": logs.count(
            "FLEETQOX_STATE_BACKEND_ASYNC_QUEUED"
        ),
        "server_backend_response_count": logs.count(
            "FLEETQOX_STATE_BACKEND_RESPONSE"
        ),
        "server_queue_full_count": logs.count(
            "FLEETQOX_STATE_BACKEND_QUEUE_FULL"
        ),
        "server_qlog_file_count": len(qlogs),
        "fast_stderr": "" if ok else fast_result.stderr,
        "slow_stderr": "" if ok else slow_result.stderr,
        "server_logs": "" if ok else logs,
    }


def run_queue_phase(
    *,
    root: Path,
    image: str,
    network: str,
    certs: Path,
    run_root: Path,
    suffix: str,
) -> dict[str, Any]:
    server_name = f"fq-public-async-queue-{suffix}"
    client_name = f"fq-public-async-queue-client-{suffix}"
    start, backend_path, proxy_path = start_server(
        root=root,
        image=image,
        network=network,
        name=server_name,
        certs=certs,
        run_root=run_root,
        workers=1,
        queue_capacity=1,
    )
    ready = (
        start.returncode == 0
        and wait_for_log(
            server_name,
            "FLEETQOX_STATE_BACKEND_ASYNC_READY workers=1 queue_capacity=1",
        )
        and wait_for_log(server_name, PROXY_SCHEMA_VERSION)
    )
    client_start = subprocess.CompletedProcess([], 1, "", "not_started")
    client_ready = False
    first: subprocess.Popen[str] | None = None
    second: subprocess.Popen[str] | None = None
    first_result = subprocess.CompletedProcess([], 1, "", "not_started")
    second_result = subprocess.CompletedProcess([], 1, "", "not_started")
    rejected = subprocess.CompletedProcess([], 1, "", "not_started")
    server_exit = -1
    logs = ""
    try:
        if ready:
            client_start = start_warm_client_container(
                root=root,
                image=image,
                network=network,
                name=client_name,
                certs=certs,
            )
            client_ready = (
                client_start.returncode == 0
                and wait_warm_client_ready(client_name)
            )
        if ready and client_ready:
            qlogs = run_root / "client-qlogs"
            qlogs.mkdir(parents=True, exist_ok=True)
            first = start_warm_client(
                root=root,
                container=client_name,
                certs=certs,
                consumer_id="queue-first",
                qlog=qlogs / "first.qlog",
            )
            first_active = wait_for_log(
                server_name,
                "FLEETQOX_BACKEND_DELAY_PROXY_DELAYING consumer_id=queue-first",
                timeout_s=8.0,
            )
            second = start_warm_client(
                root=root,
                container=client_name,
                certs=certs,
                consumer_id="queue-second",
                qlog=qlogs / "second.qlog",
            )
            second_queued = first_active and wait_for_log(
                server_name,
                "FLEETQOX_STATE_BACKEND_ASYNC_QUEUED",
                count=2,
                timeout_s=8.0,
            )
            rejected = run(
                warm_client_exec_args(
                    root=root,
                    container=client_name,
                    certs=certs,
                    consumer_id="queue-rejected",
                    qlog=qlogs / "rejected.qlog",
                ),
                timeout=15.0,
            )
            first_result = finish_client(first)
            second_result = finish_client(second)
        else:
            second_queued = False
        if ready:
            server_exit, logs = stop_server(server_name)
    finally:
        run(["docker", "rm", "-f", client_name], timeout=20.0)
        for process in (first, second):
            if process is not None and process.poll() is None:
                process.kill()
        run(["docker", "rm", "-f", server_name], timeout=20.0)
    backend = load_json(backend_path)
    proxy = load_json(proxy_path)
    qlogs = list((run_root / "client-qlogs").glob("*.qlog"))
    ok = (
        ready
        and client_ready
        and second_queued
        and response_has_status(first_result, 204)
        and response_has_status(second_result, 204)
        and response_has_status(rejected, 503)
        and server_exit == 0
        and backend.get("schema_version") == BACKEND_SCHEMA_VERSION
        and backend.get("clean_teardown") is True
        and proxy.get("schema_version") == PROXY_SCHEMA_VERSION
        and proxy.get("clean_teardown") is True
        and proxy.get("requests_total") == 2
        and proxy.get("delayed_requests") == 2
        and proxy.get("forwarded_requests") == 2
        and proxy.get("failures") == 0
        and proxy.get("max_active_requests") == 1
        and logs.count("FLEETQOX_STATE_BACKEND_ASYNC_QUEUED") == 2
        and logs.count("FLEETQOX_STATE_BACKEND_RESPONSE") == 2
        and logs.count("FLEETQOX_STATE_BACKEND_QUEUE_FULL") == 1
        and len(qlogs) == 3
        and all(path.stat().st_size > 0 for path in qlogs)
    )
    return {
        "status": "ok" if ok else "failed",
        "server_ready": ready,
        "client_ready": client_ready,
        "second_request_observed_queued": second_queued,
        "server_exit_code": server_exit,
        "first_http_204": response_has_status(first_result, 204),
        "second_http_204": response_has_status(second_result, 204),
        "overload_http_503": response_has_status(rejected, 503),
        "backend": backend,
        "proxy": proxy,
        "server_async_queued_count": logs.count(
            "FLEETQOX_STATE_BACKEND_ASYNC_QUEUED"
        ),
        "server_backend_response_count": logs.count(
            "FLEETQOX_STATE_BACKEND_RESPONSE"
        ),
        "server_queue_full_count": logs.count(
            "FLEETQOX_STATE_BACKEND_QUEUE_FULL"
        ),
        "client_qlog_file_count": len(qlogs),
        "first_stderr": "" if ok else first_result.stderr,
        "second_stderr": "" if ok else second_result.stderr,
        "rejected_stderr": "" if ok else rejected.stderr,
        "server_logs": "" if ok else logs,
    }


def run_lifecycle_phase(
    *,
    root: Path,
    image: str,
    network: str,
    certs: Path,
    run_root: Path,
    suffix: str,
) -> dict[str, Any]:
    server_name = f"fq-public-async-life-{suffix}"
    start, backend_path, proxy_path = start_server(
        root=root,
        image=image,
        network=network,
        name=server_name,
        certs=certs,
        run_root=run_root,
        workers=1,
        queue_capacity=2,
        idle_timeout="500ms",
    )
    ready = (
        start.returncode == 0
        and wait_for_log(
            server_name,
            "FLEETQOX_STATE_BACKEND_ASYNC_READY workers=1 queue_capacity=2",
        )
        and wait_for_log(server_name, PROXY_SCHEMA_VERSION)
    )
    stale: subprocess.Popen[str] | None = None
    stale_result = subprocess.CompletedProcess([], 1, "", "not_started")
    survivor = subprocess.CompletedProcess([], 1, "", "not_started")
    stale_completion_dropped = False
    server_exit = -1
    logs = ""
    try:
        if ready:
            qlogs = run_root / "client-qlogs"
            qlogs.mkdir(parents=True, exist_ok=True)
            stale = start_client(
                root=root,
                image=image,
                network=network,
                name=f"fq-public-life-stale-{suffix}",
                command=client_command(
                    root=root,
                    certs=certs,
                    consumer_id="slow-lifecycle",
                    qlog=qlogs / "stale.qlog",
                ),
            )
            delayed = wait_for_log(
                server_name,
                (
                    "FLEETQOX_BACKEND_DELAY_PROXY_DELAYING "
                    "consumer_id=slow-lifecycle"
                ),
                timeout_s=8.0,
            )
            stale_completion_dropped = delayed and wait_for_log(
                server_name,
                "FLEETQOX_STATE_BACKEND_DROPPED_HANDLER",
                timeout_s=8.0,
            )
            stale_result = finish_client(stale)
            survivor = run(
                client_docker_args(
                    root=root,
                    image=image,
                    network=network,
                    name=f"fq-public-life-survivor-{suffix}",
                    command=client_command(
                        root=root,
                        certs=certs,
                        consumer_id="lifecycle-survivor",
                        qlog=qlogs / "survivor.qlog",
                    ),
                ),
                timeout=15.0,
            )
        if ready:
            server_exit, logs = stop_server(server_name)
    finally:
        if stale is not None and stale.poll() is None:
            run(
                ["docker", "rm", "-f", f"fq-public-life-stale-{suffix}"],
                timeout=15.0,
            )
            stale.kill()
        run(["docker", "rm", "-f", server_name], timeout=20.0)
    backend = load_json(backend_path)
    proxy = load_json(proxy_path)
    qlogs = list((run_root / "client-qlogs").glob("*.qlog"))
    ok = (
        ready
        and stale_completion_dropped
        and not response_has_status(stale_result, 204)
        and response_has_status(survivor, 204)
        and server_exit == 0
        and backend.get("schema_version") == BACKEND_SCHEMA_VERSION
        and backend.get("clean_teardown") is True
        and proxy.get("schema_version") == PROXY_SCHEMA_VERSION
        and proxy.get("clean_teardown") is True
        and proxy.get("requests_total") == 2
        and proxy.get("delayed_requests") == 1
        and proxy.get("forwarded_requests") == 2
        and proxy.get("failures") == 0
        and logs.count("FLEETQOX_STATE_BACKEND_ASYNC_QUEUED") == 2
        and logs.count("FLEETQOX_STATE_BACKEND_DROPPED_HANDLER") == 1
        and logs.count("FLEETQOX_STATE_BACKEND_RESPONSE") == 1
        and logs.count("FLEETQOX_STATE_BACKEND_COMPLETION_FAILED") == 0
        and len(qlogs) == 2
        and all(path.stat().st_size > 0 for path in qlogs)
    )
    return {
        "status": "ok" if ok else "failed",
        "server_ready": ready,
        "server_exit_code": server_exit,
        "stale_client_http_204": response_has_status(stale_result, 204),
        "stale_completion_dropped": stale_completion_dropped,
        "survivor_http_204": response_has_status(survivor, 204),
        "backend": backend,
        "proxy": proxy,
        "server_async_queued_count": logs.count(
            "FLEETQOX_STATE_BACKEND_ASYNC_QUEUED"
        ),
        "server_dropped_handler_count": logs.count(
            "FLEETQOX_STATE_BACKEND_DROPPED_HANDLER"
        ),
        "server_backend_response_count": logs.count(
            "FLEETQOX_STATE_BACKEND_RESPONSE"
        ),
        "server_completion_failed_count": logs.count(
            "FLEETQOX_STATE_BACKEND_COMPLETION_FAILED"
        ),
        "client_qlog_file_count": len(qlogs),
        "stale_stderr": "" if ok else stale_result.stderr,
        "survivor_stderr": "" if ok else survivor.stderr,
        "server_logs": "" if ok else logs,
    }


def run_iteration(
    *,
    root: Path,
    image: str,
    network: str,
    certs: Path,
    temp_root: Path,
    index: int,
) -> dict[str, Any]:
    suffix = f"{os.getpid()}-{index}"
    nonblocking = run_nonblocking_phase(
        root=root,
        image=image,
        network=network,
        certs=certs,
        run_root=temp_root / f"run-{index}" / "nonblocking",
        suffix=suffix,
    )
    queue = run_queue_phase(
        root=root,
        image=image,
        network=network,
        certs=certs,
        run_root=temp_root / f"run-{index}" / "queue",
        suffix=suffix,
    )
    lifecycle = run_lifecycle_phase(
        root=root,
        image=image,
        network=network,
        certs=certs,
        run_root=temp_root / f"run-{index}" / "lifecycle",
        suffix=suffix,
    )
    ok = all(
        phase.get("status") == "ok"
        for phase in (nonblocking, queue, lifecycle)
    )
    return {
        "index": index,
        "status": "ok" if ok else "failed",
        "nonblocking_phase": nonblocking,
        "bounded_queue_phase": queue,
        "handler_lifecycle_phase": lifecycle,
        "netem_server": "delay 11ms 2ms",
        "netem_client": "delay 9ms 2ms",
    }


def run_probe(
    *,
    root: Path,
    base_image: str,
    server_image: str,
    iterations: int,
    skip_server_build: bool,
    keep_temp: bool,
) -> dict[str, Any]:
    build = subprocess.CompletedProcess([], 0, "", "")
    if not skip_server_build:
        build = run(
            [
                "docker",
                "build",
                "--build-arg",
                f"BASE_IMAGE={base_image}",
                "-f",
                "external/ngtcp2-public-mtls/Dockerfile",
                "-t",
                server_image,
                ".",
            ],
            timeout=600.0,
        )
    temp_root = root / f".tmp_fleetrmw_public_async_{os.getpid()}"
    certs = temp_root / "certs"
    certs.mkdir(parents=True, exist_ok=True)
    certificate = run(
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
            base_image,
            "-lc",
            stateful_certificate_command(certs, root),
        ],
        timeout=180.0,
    )
    network = f"fq-public-async-net-{os.getpid()}"
    network_result = run(["docker", "network", "create", network], timeout=20.0)
    rows: list[dict[str, Any]] = []
    try:
        if all(
            result.returncode == 0
            for result in (build, certificate, network_result)
        ):
            for index in range(max(1, iterations)):
                rows.append(
                    run_iteration(
                        root=root,
                        image=server_image,
                        network=network,
                        certs=certs,
                        temp_root=temp_root,
                        index=index,
                    )
                )
    finally:
        run(["docker", "network", "rm", network], timeout=20.0)
        if not keep_temp:
            shutil.rmtree(temp_root, ignore_errors=True)
    run_count = max(1, iterations)
    ok_count = sum(row.get("status") == "ok" for row in rows)
    ok = (
        all(
            result.returncode == 0
            for result in (build, certificate, network_result)
        )
        and len(rows) == run_count
        and ok_count == run_count
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if ok else "failed",
        "run_count": run_count,
        "ok_run_count": ok_count,
        "public_ngtcp2_backend_event_loop_nonblocking_claim": ok,
        "bounded_backend_worker_pool_claim": ok,
        "bounded_backend_queue_http_503_claim": ok,
        "handler_generation_fencing_claim": ok,
        "real_state_engine_behind_test_delay_proxy_claim": ok,
        "docker_netem_both_ends_claim": ok,
        "aioquic_server_runtime_used": False,
        "production_quic_backend_claim": False,
        "server_build_returncode": build.returncode,
        "certificate_returncode": certificate.returncode,
        "network_returncode": network_result.returncode,
        "runs": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-image", default=DEFAULT_BASE_IMAGE)
    parser.add_argument("--server-image", default=DEFAULT_SERVER_IMAGE)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--skip-server-build", action="store_true")
    parser.add_argument("--keep-temp", action="store_true")
    parser.add_argument(
        "--summary-json",
        default=(
            "results_rmw_socket/"
            "docker_ngtcp2_public_async_backend_summary.json"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_probe(
        root=ROOT,
        base_image=args.base_image,
        server_image=args.server_image,
        iterations=args.iterations,
        skip_server_build=args.skip_server_build,
        keep_temp=args.keep_temp,
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
        print(f"status={summary['status']}")
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
