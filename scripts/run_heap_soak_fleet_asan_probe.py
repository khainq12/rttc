"""B0 heap-corruption reproduction attempt at the exact historical scale:
publisher -> relay -> subscriber (three processes, not two), N robot topics
multiplexed through each, real netem loss on the publisher's outbound link,
matching the documented repro command
`run_ros2_relay_rmw_netem_probe.py --robot-count 16` -- but with all three
processes hand-written in C++ and uniformly ASan/UBSan-compiled, since that
original command's publisher/subscriber are rclpy and LD_PRELOAD-ing ASan
into them hits a real, unrelated ASan/CPython interceptor bug (see
run_heap_soak_asan_probe.py's module docstring).

Prerequisite: the same ASan/UBSan colcon install used by
run_heap_soak_asan_probe.py (fleetrmw_heap_soak_publisher_probe,
fleetrmw_heap_soak_subscriber_probe, and fleetrmw_generic_serialized_relay_probe
all need to exist under <install>/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_ros2_relay_rmw_netem_probe import (  # noqa: E402
    fleetqox_static_addresses,
)

DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"
DEFAULT_INSTALL = ".tmp_fleetrmw_asan_install"
SCHEMA_VERSION = "fleetrmw.heap_soak_fleet_asan_probe.v1"
PUB_PREFIX = "/fleetqox/heap_soak_pub"
SUB_PREFIX = "/fleetqox/heap_soak_sub"


def run(cmd: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, timeout=timeout,
    )


def robot_topic(prefix: str, robot_index: int) -> str:
    return f"{prefix}/robot_{robot_index:04d}/state"


def relay_mappings(robot_count: int) -> list[str]:
    return [
        f"--mapping {robot_topic(PUB_PREFIX, i)}={robot_topic(SUB_PREFIX, i)}"
        for i in range(robot_count)
    ]


def base_env_args(install: str, bind_port: int, peers: str) -> list[str]:
    install_root = f"/work/{install}"
    return [
        "-e", f"FLEETQOX_RMW_BIND=0.0.0.0:{bind_port}",
        "-e", f"FLEETQOX_RMW_PEERS={peers}",
        "-e", (
            f"ASAN_OPTIONS=abort_on_error=1:halt_on_error=1:detect_leaks=0:"
            f"log_path={install_root}/../.tmp_fleetrmw_asan_logs/asan"
        ),
        "-e", (
            f"UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1:"
            f"log_path={install_root}/../.tmp_fleetrmw_asan_logs/ubsan"
        ),
    ]


def run_probe(
    *, root: Path, image: str, install: str, robot_count: int, payload_bytes: int,
    samples_per_robot: int, interval_ms: int, linger_s: int, total_wait_s: int,
    relay_timeout_ms: int, loss_percent: float, delay_ms: float, jitter_ms: float,
    rate_mbit: float,
) -> dict[str, Any]:
    pid = os.getpid()
    network = f"fq-heap-fleet-net-{pid}"
    install_root = f"/work/{install}"
    asan_log_dir = root / ".tmp_fleetrmw_asan_logs"
    asan_log_dir.mkdir(parents=True, exist_ok=True)
    for stale in asan_log_dir.glob("*"):
        stale.unlink()

    network_result = run(["docker", "network", "create", network], timeout=20.0)
    addresses = fleetqox_static_addresses(network)
    publisher_ip, relay_ip, subscriber_ip = (
        addresses["publisher"], addresses["relay"], addresses["subscriber"],
    )

    expected_total = robot_count * samples_per_robot
    subscriber_name = f"fq-heap-fleet-sub-{pid}"
    relay_name = f"fq-heap-fleet-relay-{pid}"
    publisher_name = f"fq-heap-fleet-pub-{pid}"

    subscriber_cmd = (
        f"source {install_root}/setup.bash && "
        f"exec {install_root}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        f"fleetrmw_heap_soak_subscriber_probe "
        f"{payload_bytes} {robot_count} {samples_per_robot} {total_wait_s} {SUB_PREFIX}"
    )
    relay_cmd = (
        f"source {install_root}/setup.bash && "
        f"exec {install_root}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        f"fleetrmw_generic_serialized_relay_probe "
        + " ".join(relay_mappings(robot_count))
        + f" --samples {samples_per_robot} --timeout-ms {relay_timeout_ms} "
        f"--linger-ms {linger_s * 1000}"
    )
    publisher_cmd = (
        f"tc qdisc replace dev eth0 root netem delay {delay_ms:g}ms {jitter_ms:g}ms "
        f"loss random {loss_percent:g}% rate {rate_mbit:g}mbit && "
        f"source {install_root}/setup.bash && "
        f"exec {install_root}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        f"fleetrmw_heap_soak_publisher_probe "
        f"{payload_bytes} {robot_count} {samples_per_robot} {interval_ms} {linger_s} {PUB_PREFIX}"
    )

    subscriber_start = run(
        [
            "docker", "run", "-d", "--name", subscriber_name, "--network", network,
            "--ip", subscriber_ip, "-e", "RMW_IMPLEMENTATION=rmw_fleetqox_cpp",
            *base_env_args(install, 49813, f"{relay_ip}:49812"),
            "--entrypoint", "bash", "-v", f"{root}:/work", "-w", "/work",
            image, "-lc", subscriber_cmd,
        ],
        timeout=30.0,
    )
    relay_start = run(
        [
            "docker", "run", "-d", "--name", relay_name, "--network", network,
            "--ip", relay_ip, "-e", "RMW_IMPLEMENTATION=rmw_fleetqox_cpp",
            *base_env_args(install, 49812, f"{publisher_ip}:49811,{subscriber_ip}:49813"),
            "--entrypoint", "bash", "-v", f"{root}:/work", "-w", "/work",
            image, "-lc", relay_cmd,
        ],
        timeout=30.0,
    )
    publisher_start = run(
        [
            "docker", "run", "-d", "--name", publisher_name, "--network", network,
            "--ip", publisher_ip, "--cap-add", "NET_ADMIN",
            "-e", "RMW_IMPLEMENTATION=rmw_fleetqox_cpp",
            *base_env_args(install, 49811, f"{relay_ip}:49812"),
            "--entrypoint", "bash", "-v", f"{root}:/work", "-w", "/work",
            image, "-lc", publisher_cmd,
        ],
        timeout=30.0,
    )

    started = (
        subscriber_start.returncode == 0
        and relay_start.returncode == 0
        and publisher_start.returncode == 0
    )
    results: dict[str, subprocess.CompletedProcess[str]] = {}
    try:
        if started:
            names = [publisher_name, relay_name, subscriber_name]
            deadline = time.monotonic() + total_wait_s + linger_s + 60
            while time.monotonic() < deadline:
                states = [
                    run(["docker", "inspect", "-f", "{{.State.Running}}", n], timeout=10.0)
                    .stdout.strip()
                    for n in names
                ]
                if all(state == "false" for state in states):
                    break
                time.sleep(2.0)
            for role, name in zip(("publisher", "relay", "subscriber"), names):
                logs = run(["docker", "logs", name], timeout=20.0)
                exit_code = run(
                    ["docker", "inspect", "-f", "{{.State.ExitCode}}", name], timeout=10.0
                )
                results[role] = subprocess.CompletedProcess(
                    [], int(exit_code.stdout.strip() or -1), logs.stdout, logs.stderr,
                )
    finally:
        run(["docker", "rm", "-f", publisher_name], timeout=20.0)
        run(["docker", "rm", "-f", relay_name], timeout=20.0)
        run(["docker", "rm", "-f", subscriber_name], timeout=20.0)
        run(["docker", "network", "rm", network], timeout=20.0)

    sanitizer_reports = []
    for path in sorted(glob.glob(str(asan_log_dir / "*"))):
        sanitizer_reports.append({"path": path, "content": Path(path).read_text(errors="replace")})

    ok = (
        network_result.returncode == 0
        and started
        and len(results) == 3
        and all(r.returncode == 0 for r in results.values())
        and not sanitizer_reports
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if ok else "failed",
        "robot_count": robot_count,
        "payload_bytes": payload_bytes,
        "samples_per_robot": samples_per_robot,
        "expected_total": expected_total,
        "loss_percent": loss_percent,
        "publisher_returncode": results.get("publisher", subprocess.CompletedProcess([], -1)).returncode,
        "relay_returncode": results.get("relay", subprocess.CompletedProcess([], -1)).returncode,
        "subscriber_returncode": results.get("subscriber", subprocess.CompletedProcess([], -1)).returncode,
        "sanitizer_report_count": len(sanitizer_reports),
        "sanitizer_reports": sanitizer_reports,
        "publisher_stdout_tail": results.get("publisher", subprocess.CompletedProcess([], -1, "", "")).stdout[-3000:],
        "relay_stdout_tail": results.get("relay", subprocess.CompletedProcess([], -1, "", "")).stdout[-3000:],
        "subscriber_stdout_tail": results.get("subscriber", subprocess.CompletedProcess([], -1, "", "")).stdout[-3000:],
        "publisher_stderr_tail": results.get("publisher", subprocess.CompletedProcess([], -1, "", "")).stderr[-3000:],
        "relay_stderr_tail": results.get("relay", subprocess.CompletedProcess([], -1, "", "")).stderr[-3000:],
        "subscriber_stderr_tail": results.get("subscriber", subprocess.CompletedProcess([], -1, "", "")).stderr[-3000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--install", default=DEFAULT_INSTALL)
    parser.add_argument("--robot-count", type=int, default=16)
    parser.add_argument("--payload-bytes", type=int, default=32768)
    parser.add_argument("--samples-per-robot", type=int, default=10)
    parser.add_argument("--interval-ms", type=int, default=50)
    parser.add_argument("--linger-s", type=int, default=10)
    parser.add_argument("--total-wait-s", type=int, default=120)
    parser.add_argument("--relay-timeout-ms", type=int, default=90000)
    parser.add_argument("--loss-percent", type=float, default=15.0)
    parser.add_argument("--delay-ms", type=float, default=20.0)
    parser.add_argument("--jitter-ms", type=float, default=8.0)
    parser.add_argument("--rate-mbit", type=float, default=20.0)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/heap_soak_fleet_asan_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    rounds = []
    for round_index in range(max(1, args.rounds)):
        result = run_probe(
            root=ROOT, image=args.image, install=args.install,
            robot_count=args.robot_count, payload_bytes=args.payload_bytes,
            samples_per_robot=args.samples_per_robot, interval_ms=args.interval_ms,
            linger_s=args.linger_s, total_wait_s=args.total_wait_s,
            relay_timeout_ms=args.relay_timeout_ms, loss_percent=args.loss_percent,
            delay_ms=args.delay_ms, jitter_ms=args.jitter_ms, rate_mbit=args.rate_mbit,
        )
        rounds.append(result)
        print(
            f"round {round_index + 1}/{args.rounds}: status={result['status']} "
            f"pub={result['publisher_returncode']} relay={result['relay_returncode']} "
            f"sub={result['subscriber_returncode']} "
            f"sanitizer_reports={result['sanitizer_report_count']}",
            flush=True,
        )
        if result["sanitizer_report_count"] > 0:
            break

    ok_rounds = sum(1 for r in rounds if r["status"] == "ok")
    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if ok_rounds == len(rounds) else "failed",
        "round_count": len(rounds),
        "ok_round_count": ok_rounds,
        "rounds": rounds,
    }
    output = ROOT / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
