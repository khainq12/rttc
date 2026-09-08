"""Build and run the FleetRMW remote BEST_AVAILABLE liveliness resolution
probe in separate Docker containers, over a real UDP wire with netem
impairment.

Proves that BEST_AVAILABLE liveliness resolution correctly takes a real,
UDP-discovered remote endpoint's QoS into account -- qos_best_available_
probe.cpp only ever pairs a BEST_AVAILABLE endpoint against a local,
same-process one, so this closes that remote coverage gap.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "fleetrmw.docker_remote_qos_best_available_probe.v1"
DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"


def json_rows(output: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def observer_ok(probe: dict[str, Any]) -> bool:
    return (
        probe.get("status") == "ok"
        and probe.get("best_publisher_resolved_against_remote_subscription_claim") is True
        and probe.get("best_subscription_resolved_against_remote_publisher_claim") is True
    )


def run_command(
    command: list[str],
    *,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def wait_for_ready(container: str, timeout_s: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        logs = run_command(["docker", "logs", container]).stdout
        if '"phase":"ready"' in logs and '"initialized":true' in logs:
            return True
        state = run_command(["docker", "inspect", "-f", "{{.State.Running}}", container])
        if state.returncode != 0 or state.stdout.strip() != "true":
            return False
        time.sleep(0.1)
    return False


def build_probe(root: Path, image: str, build_root: str) -> subprocess.CompletedProcess[str]:
    install = f"{build_root}/install"
    command = (
        "source /opt/ros/jazzy/setup.bash && "
        f"rm -rf {build_root} && "
        f"colcon --log-base {build_root}/log build --base-paths ros2_ws/src "
        "--packages-select rmw_fleetqox_cpp "
        f"--build-base {build_root}/build --install-base {install} "
        "--cmake-args -DCMAKE_BUILD_TYPE=Release"
    )
    return run_command(
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


def run_case(
    *,
    root: Path,
    image: str,
    install: str,
    network: str,
    index: int,
) -> dict[str, Any]:
    observer = f"fleetrmw-remote-best-available-observer-{os.getpid()}-{index}"
    advertiser = f"fleetrmw-remote-best-available-advertiser-{os.getpid()}-{index}"
    binary = (
        f"{install}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_remote_qos_best_available_probe"
    )
    observer_command = (
        f"source /opt/ros/jazzy/setup.bash && source {install}/setup.bash && "
        "tc qdisc replace dev eth0 root netem delay 5ms 1ms && "
        f"FLEETQOX_RMW_BIND=0.0.0.0:48480 {binary} --mode observer --settle-ms 3000"
    )
    start = run_command(
        [
            "docker",
            "run",
            "-d",
            "--name",
            observer,
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
            observer_command,
        ]
    )
    ready = start.returncode == 0 and wait_for_ready(observer)
    advertiser_result = subprocess.CompletedProcess([], 1, "", "observer_not_ready")
    observer_returncode = -1
    observer_stdout = ""
    try:
        if ready:
            advertiser_command = (
                f"source /opt/ros/jazzy/setup.bash && source {install}/setup.bash && "
                "tc qdisc replace dev eth0 root netem delay 5ms 1ms && "
                "FLEETQOX_RMW_BIND=0.0.0.0:48481 "
                f"FLEETQOX_RMW_PEERS={observer}:48480 "
                f"{binary} --mode advertiser --settle-ms 3000"
            )
            advertiser_result = run_command(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--name",
                    advertiser,
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
                    advertiser_command,
                ]
            )
            wait_result = run_command(["docker", "wait", observer])
            if wait_result.returncode == 0 and wait_result.stdout.strip():
                observer_returncode = int(wait_result.stdout.strip())
        observer_stdout = run_command(["docker", "logs", observer]).stdout
    finally:
        run_command(["docker", "rm", "-f", observer])

    observer_rows = json_rows(observer_stdout)
    advertiser_rows = json_rows(advertiser_result.stdout)
    observer_probe = observer_rows[-1] if observer_rows else {}
    advertiser_probe = advertiser_rows[-1] if advertiser_rows else {}
    ok = (
        ready
        and advertiser_result.returncode == 0
        and observer_returncode == 0
        and advertiser_probe.get("status") == "ok"
        and observer_ok(observer_probe)
    )
    return {
        "index": index,
        "status": "ok" if ok else "failed",
        "observer_ready": ready,
        "observer_returncode": observer_returncode,
        "advertiser_returncode": advertiser_result.returncode,
        "observer": observer_probe,
        "advertiser": advertiser_probe,
        "observer_stdout": observer_stdout,
        "advertiser_stdout": advertiser_result.stdout,
        "advertiser_stderr": advertiser_result.stderr,
    }


def run_probe(
    *,
    root: Path,
    image: str,
    iterations: int,
    keep_temp: bool,
) -> dict[str, Any]:
    run_count = max(iterations, 1)
    build_root = "/work/.tmp_fleetrmw_remote_qos_best_available"
    install = f"{build_root}/install"
    network = f"fleetrmw-remote-best-available-net-{os.getpid()}"
    build = build_probe(root, image, build_root)
    if build.returncode != 0:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "image": image,
            "run_count": run_count,
            "ok_run_count": 0,
            "build_returncode": build.returncode,
            "build_stdout": build.stdout,
            "build_stderr": build.stderr,
            "runs": [],
        }

    network_result = run_command(["docker", "network", "create", network])
    runs: list[dict[str, Any]] = []
    try:
        if network_result.returncode == 0:
            for index in range(run_count):
                runs.append(
                    run_case(
                        root=root,
                        image=image,
                        install=install,
                        network=network,
                        index=index + 1,
                    )
                )
    finally:
        run_command(["docker", "network", "rm", network])
        if not keep_temp:
            run_command(
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
                    f"rm -rf {build_root}",
                ]
            )

    ok_run_count = sum(run.get("status") == "ok" for run in runs)
    ok = len(runs) == run_count and ok_run_count == run_count
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if ok else "failed",
        "image": image,
        "run_count": run_count,
        "ok_run_count": ok_run_count,
        "build_returncode": build.returncode,
        "remote_qos_best_available_resolution_claim": ok,
        "real_udp_multicontainer": ok,
        "netem_applied": ok,
        "netem": "delay 5ms 1ms on observer and advertiser",
        "runs": runs,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--keep-temp", action="store_true")
    parser.add_argument(
        "--summary-json",
        default=(
            "results_rmw_socket/docker_remote_qos_best_available_probe_summary.json"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_probe(
        root=ROOT,
        image=args.image,
        iterations=args.iterations,
        keep_temp=args.keep_temp,
    )
    output = ROOT / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(f"status={summary['status']}")
        print(f"runs={summary.get('ok_run_count', 0)}/{summary.get('run_count', 0)}")
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
