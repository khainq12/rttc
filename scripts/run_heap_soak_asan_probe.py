"""B0 heap-corruption reproduction attempt: two real OS processes (not
in-process), a real lossy Docker network link, large samples, built with
ASan/UBSan.

Prerequisite: an ASan/UBSan colcon install of fleetrmw_interfaces and
rmw_fleetqox_cpp, e.g.:

    colcon build --executor sequential \\
      --base-paths ros2_ws/src \\
      --packages-select fleetrmw_interfaces rmw_fleetqox_cpp \\
      --build-base .tmp_fleetrmw_asan_build \\
      --install-base .tmp_fleetrmw_asan_install \\
      --cmake-args -DCMAKE_BUILD_TYPE=RelWithDebInfo \\
        -DCMAKE_CXX_FLAGS="-fsanitize=address,undefined -fno-omit-frame-pointer -fno-sanitize-recover=all -g" \\
        -DCMAKE_EXE_LINKER_FLAGS="-fsanitize=address,undefined" \\
        -DCMAKE_SHARED_LINKER_FLAGS="-fsanitize=address,undefined"

Unlike the rclpy-based direct/relay netem probes, this deliberately runs
two hand-written C++ processes (heap_soak_publisher_probe /
heap_soak_subscriber_probe) so the ASan runtime is uniformly linked into
both -- LD_PRELOAD-ing ASan into a non-instrumented python3 host process
hits a known, unrelated ASan/CPython C-extension interceptor bug
(`__interception::real___cxa_throw != 0` inside rclpy's own pybind11
module) that produces false-positive crashes with no connection to
FleetRMW at all.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"
DEFAULT_INSTALL = ".tmp_fleetrmw_asan_install"
SCHEMA_VERSION = "fleetrmw.heap_soak_asan_probe.v1"


def run(cmd: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, timeout=timeout,
    )


def container_env_command(
    *, install: str, bind_port: int, peer_name: str, binary: str, args: list[str],
) -> str:
    install_root = f"/work/{install}"
    return (
        f"source {install_root}/setup.bash && "
        f"export FLEETQOX_RMW_BIND=0.0.0.0:{bind_port} && "
        f"export FLEETQOX_RMW_PEERS={peer_name}:{bind_port} && "
        f"exec {install_root}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/{binary} "
        + " ".join(args)
    )


def run_probe(
    *, root: Path, image: str, install: str, payload_bytes: int, sample_count: int,
    interval_ms: int, linger_s: int, total_wait_s: int, loss_percent: float,
    delay_ms: float, jitter_ms: float, rate_mbit: float,
) -> dict[str, Any]:
    pid = os.getpid()
    network = f"fq-heap-soak-net-{pid}"
    publisher_name = f"fq-heap-soak-pub-{pid}"
    subscriber_name = f"fq-heap-soak-sub-{pid}"
    bind_port = 27500
    asan_log_dir = root / ".tmp_fleetrmw_asan_logs"
    asan_log_dir.mkdir(parents=True, exist_ok=True)
    for stale in asan_log_dir.glob("*"):
        stale.unlink()

    network_result = run(["docker", "network", "create", network], timeout=20.0)

    publisher_command = container_env_command(
        install=install, bind_port=bind_port, peer_name=subscriber_name,
        binary="fleetrmw_heap_soak_publisher_probe",
        args=[str(payload_bytes), str(sample_count), str(interval_ms), str(linger_s)],
    )
    subscriber_command = container_env_command(
        install=install, bind_port=bind_port, peer_name=publisher_name,
        binary="fleetrmw_heap_soak_subscriber_probe",
        args=[str(payload_bytes), str(sample_count), str(total_wait_s)],
    )
    netem_command = (
        f"tc qdisc replace dev eth0 root netem delay {delay_ms:g}ms {jitter_ms:g}ms "
        f"loss random {loss_percent:g}% rate {rate_mbit:g}mbit"
    )

    subscriber_start = run(
        [
            "docker", "run", "-d", "--name", subscriber_name, "--network", network,
            "--cap-add", "NET_ADMIN", "--entrypoint", "bash",
            "-v", f"{root}:/work", "-w", "/work",
            image, "-lc", f"{netem_command} && {subscriber_command}",
        ],
        timeout=30.0,
    )
    # The publisher does not need netem of its own; a single lossy hop
    # (the subscriber's inbound link) is enough to exercise fragment
    # loss/repair without doubling the effective loss rate.
    publisher_start = run(
        [
            "docker", "run", "-d", "--name", publisher_name, "--network", network,
            "--entrypoint", "bash",
            "-v", f"{root}:/work", "-w", "/work",
            image, "-lc", publisher_command,
        ],
        timeout=30.0,
    )

    started = subscriber_start.returncode == 0 and publisher_start.returncode == 0
    publisher_result = subprocess.CompletedProcess([], 1, "", "not_started")
    subscriber_result = subprocess.CompletedProcess([], 1, "", "not_started")
    try:
        if started:
            deadline = time.monotonic() + total_wait_s + linger_s + 30
            while time.monotonic() < deadline:
                publisher_inspect = run(
                    ["docker", "inspect", "-f", "{{.State.Running}}", publisher_name],
                    timeout=10.0,
                )
                subscriber_inspect = run(
                    ["docker", "inspect", "-f", "{{.State.Running}}", subscriber_name],
                    timeout=10.0,
                )
                if (
                    publisher_inspect.stdout.strip() == "false"
                    and subscriber_inspect.stdout.strip() == "false"
                ):
                    break
                time.sleep(2.0)
            publisher_logs = run(["docker", "logs", publisher_name], timeout=20.0)
            subscriber_logs = run(["docker", "logs", subscriber_name], timeout=20.0)
            publisher_exit = run(
                ["docker", "inspect", "-f", "{{.State.ExitCode}}", publisher_name],
                timeout=10.0,
            )
            subscriber_exit = run(
                ["docker", "inspect", "-f", "{{.State.ExitCode}}", subscriber_name],
                timeout=10.0,
            )
            publisher_result = subprocess.CompletedProcess(
                [], int(publisher_exit.stdout.strip() or -1),
                publisher_logs.stdout, publisher_logs.stderr,
            )
            subscriber_result = subprocess.CompletedProcess(
                [], int(subscriber_exit.stdout.strip() or -1),
                subscriber_logs.stdout, subscriber_logs.stderr,
            )
    finally:
        run(["docker", "rm", "-f", publisher_name], timeout=20.0)
        run(["docker", "rm", "-f", subscriber_name], timeout=20.0)
        run(["docker", "network", "rm", network], timeout=20.0)

    sanitizer_reports = []
    for path in sorted(glob.glob(str(asan_log_dir / "*"))):
        sanitizer_reports.append({"path": path, "content": Path(path).read_text(errors="replace")})

    ok = (
        network_result.returncode == 0
        and started
        and publisher_result.returncode == 0
        and subscriber_result.returncode == 0
        and not sanitizer_reports
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if ok else "failed",
        "payload_bytes": payload_bytes,
        "sample_count": sample_count,
        "loss_percent": loss_percent,
        "publisher_returncode": publisher_result.returncode,
        "subscriber_returncode": subscriber_result.returncode,
        "sanitizer_report_count": len(sanitizer_reports),
        "sanitizer_reports": sanitizer_reports,
        "publisher_stdout_tail": publisher_result.stdout[-4000:],
        "publisher_stderr_tail": publisher_result.stderr[-4000:],
        "subscriber_stdout_tail": subscriber_result.stdout[-4000:],
        "subscriber_stderr_tail": subscriber_result.stderr[-4000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--install", default=DEFAULT_INSTALL)
    parser.add_argument("--payload-bytes", type=int, default=32768)
    parser.add_argument("--sample-count", type=int, default=200)
    parser.add_argument("--interval-ms", type=int, default=50)
    parser.add_argument("--linger-s", type=int, default=10)
    parser.add_argument("--total-wait-s", type=int, default=90)
    parser.add_argument("--loss-percent", type=float, default=15.0)
    parser.add_argument("--delay-ms", type=float, default=20.0)
    parser.add_argument("--jitter-ms", type=float, default=8.0)
    parser.add_argument("--rate-mbit", type=float, default=20.0)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/heap_soak_asan_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    rounds = []
    for round_index in range(max(1, args.rounds)):
        result = run_probe(
            root=ROOT, image=args.image, install=args.install,
            payload_bytes=args.payload_bytes, sample_count=args.sample_count,
            interval_ms=args.interval_ms, linger_s=args.linger_s,
            total_wait_s=args.total_wait_s, loss_percent=args.loss_percent,
            delay_ms=args.delay_ms, jitter_ms=args.jitter_ms, rate_mbit=args.rate_mbit,
        )
        rounds.append(result)
        print(
            f"round {round_index + 1}/{args.rounds}: status={result['status']} "
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
