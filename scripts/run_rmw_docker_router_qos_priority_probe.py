"""Run a Docker router deadline-priority probe for rmw_fleetqox_cpp."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any


SCHEMA_VERSION = "fleetrmw.rmw_router_qos_priority_probe.v1"
DEFAULT_IMAGE = "ros:jazzy-ros-base"
DEFAULT_BULK_TOPIC = "/fleetqox/router_qos_bulk"
DEFAULT_CRITICAL_TOPIC = "/fleetqox/router_qos_critical"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--bulk-topic", default=DEFAULT_BULK_TOPIC)
    parser.add_argument("--critical-topic", default=DEFAULT_CRITICAL_TOPIC)
    parser.add_argument("--bulk-deadline-ms", type=int, default=4000)
    parser.add_argument("--critical-deadline-ms", type=int, default=20)
    parser.add_argument("--scheduler-window-ms", type=int, default=5000)
    parser.add_argument("--expected-order", choices=["priority", "fifo"], default="priority")
    parser.add_argument("--summary-json", default="results_rmw_socket/docker_router_qos_priority_probe_summary.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    summary = run_probe(
        root=root,
        image=args.image,
        bulk_topic=args.bulk_topic,
        critical_topic=args.critical_topic,
        bulk_deadline_ms=args.bulk_deadline_ms,
        critical_deadline_ms=args.critical_deadline_ms,
        scheduler_window_ms=args.scheduler_window_ms,
        expected_order=args.expected_order,
    )
    summary_path = root / args.summary_json
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print("fleetrmw-router-qos-priority-probe")
        print(f"  status: {summary['status']}")
        print(f"  forwarded_topics: {summary.get('router', {}).get('forwarded_topics')}")
    return 0 if summary["status"] == "ok" else 1


def run_probe(
    *,
    root: Path,
    image: str,
    bulk_topic: str,
    critical_topic: str,
    bulk_deadline_ms: int,
    critical_deadline_ms: int,
    scheduler_window_ms: int,
    expected_order: str = "priority",
) -> dict[str, Any]:
    suffix = str(os.getpid())
    network = f"fleetrmw-prio-net-{suffix}"
    router_name = f"fleetrmw-prio-router-{suffix}"
    build_base = "/work/.tmp_fleetrmw_router_prio_build"
    install_base = "/work/.tmp_fleetrmw_router_prio_install"
    log_base = "/work/.tmp_fleetrmw_router_prio_log"
    endpoint_binary = f"{install_base}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/fleetrmw_interprocess_pubsub_probe"
    router_binary = f"{install_base}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/fleetrmw_udp_router_probe"

    try:
        run(["docker", "network", "create", network])
        docker_shell(
            root,
            image,
            "source /opt/ros/jazzy/setup.bash && "
            f"rm -rf {build_base} {install_base} {log_base} && "
            "colcon "
            f"--log-base {log_base} "
            "build --base-paths ros2_ws/src --packages-select rmw_fleetqox_cpp "
            f"--build-base {build_base} --install-base {install_base} "
            "--cmake-args -DCMAKE_BUILD_TYPE=Release",
        )
        start_container(
            root=root,
            image=image,
            name=router_name,
            network=network,
            command=(
                f"source /opt/ros/jazzy/setup.bash && source {install_base}/setup.bash && "
                f"{router_binary} --bind 0.0.0.0:48330 --peers 127.0.0.1:9 "
                "--expected-frames 2 --expected-graph-advertisements 2 "
                f"--scheduler-window-ms {scheduler_window_ms} --timeout-ms 7000"
            ),
        )
        time.sleep(0.5)
        publisher = run(
            [
                "docker", "run", "--rm",
                "--network", network,
                "-v", f"{root}:/work",
                "-w", "/work",
                image,
                "bash", "-lc",
                (
                    "source /opt/ros/jazzy/setup.bash && "
                    f"source {install_base}/setup.bash && "
                    # The router only learns a topic's deadline QoS (used to
                    # sort queued frames by absolute deadline) from that
                    # publisher's graph advertisement, sent at
                    # rmw_create_publisher() time. --pre-publish-ms delays
                    # the actual publish() until after that advertisement
                    # has had time to reach and be processed by the router,
                    # so the scheduler can prioritize correctly instead of
                    # falling back to arrival (FIFO) order. bulk_deadline_ms
                    # defaults well above the ~200-500ms of inherent
                    # inter-process overhead between these two sequential
                    # publisher invocations, so that overhead can't make
                    # bulk's *absolute* deadline (publish time + budget)
                    # earlier than critical's and invert the priority order.
                    f"FLEETQOX_RMW_BIND=0.0.0.0:0 FLEETQOX_RMW_PEERS={router_name}:48330 "
                    f"{endpoint_binary} --mode publisher --topic {bulk_topic} --payload bulk "
                    f"--deadline-ms {bulk_deadline_ms} --pre-publish-ms 150 && "
                    f"FLEETQOX_RMW_BIND=0.0.0.0:0 FLEETQOX_RMW_PEERS={router_name}:48330 "
                    f"{endpoint_binary} --mode publisher --topic {critical_topic} --payload critical "
                    f"--deadline-ms {critical_deadline_ms} --pre-publish-ms 150"
                ),
            ],
        )
        router_returncode = int(run(["docker", "wait", router_name]).stdout.strip())
        router_log = run(["docker", "logs", router_name]).stdout.strip()
        router_result = json.loads(router_log)
        publisher_lines = [
            json.loads(line)
            for line in publisher.stdout.splitlines()
            if line.strip().startswith("{")
        ]
        forwarded_topics = router_result.get("forwarded_topics", [])
        expected_topics = (
            [critical_topic, bulk_topic]
            if expected_order == "priority" else
            [bulk_topic, critical_topic]
        )
        status = (
            publisher.returncode == 0 and
            router_returncode == 0 and
            len(publisher_lines) == 2 and
            all(row.get("status") == "ok" for row in publisher_lines) and
            router_result.get("status") == "ok" and
            router_result.get("forwarded_frames") == 2 and
            forwarded_topics[:2] == expected_topics
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if status else "failed",
            "docker_network": network,
            "bulk_topic": bulk_topic,
            "critical_topic": critical_topic,
            "bulk_deadline_ms": bulk_deadline_ms,
            "critical_deadline_ms": critical_deadline_ms,
            "scheduler_window_ms": scheduler_window_ms,
            "expected_order": expected_order,
            "expected_topics": expected_topics,
            "publisher_returncode": publisher.returncode,
            "publisher": publisher_lines,
            "router_returncode": router_returncode,
            "router": router_result,
        }
    except subprocess.CalledProcessError as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "docker_network": network,
            "returncode": exc.returncode,
            "stdout": exc.stdout,
            "stderr": exc.stderr,
        }
    finally:
        run(["docker", "rm", "-f", router_name], check=False)
        run(["docker", "network", "rm", network], check=False)
        docker_shell(root, image, f"rm -rf {build_base} {install_base} {log_base}", check=False)


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


def docker_shell(
  root: Path,
  image: str,
  command: str,
  *,
  check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return run([
        "docker", "run", "--rm",
        "-v", f"{root}:/work",
        "-w", "/work",
        image,
        "bash", "-lc", command,
    ], check=check)


def start_container(*, root: Path, image: str, name: str, network: str, command: str) -> str:
    result = run([
        "docker", "run", "-d",
        "--name", name,
        "--network", network,
        "-v", f"{root}:/work",
        "-w", "/work",
        image,
        "bash", "-lc", command,
    ])
    return result.stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
