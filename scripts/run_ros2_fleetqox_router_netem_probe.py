"""Run FleetRMW pub/sub through its own UDP router under Docker tc-netem.

Companion to run_ros2_direct_rmw_netem_probe.py: that script measures
FleetRMW and DDS baselines as direct, unscheduled point-to-point pub/sub.
This script adds the third leg needed for a fair "does FleetRMW's router
help" comparison -- the SAME rclpy publisher/subscriber pattern, SAME
sample count/payload/publish interval, but routed through FleetRMW's own
UDP router binary (fleetrmw_udp_router_probe), optionally with its
deadline-aware holdback scheduler enabled. DDS has no equivalent
relay-and-schedule stage -- it doesn't need one -- so this is FleetRMW's
own two configurations (fifo-via-router vs. scheduled-via-router), not a
new DDS data point.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_ros2_direct_rmw_netem_probe import (  # noqa: E402
    excerpt,
    parse_last_json,
    start_container,
    wait_for_container_path,
    write_probe_scripts,
)
from scripts.run_rmw_docker_multi_robot_live_telemetry_plan_probe import (  # noqa: E402
    DEFAULT_IMAGE,
    NETEM_SCHEMA_VERSION,
    NETEM_SEED_SEMANTICS,
    netem_config_for_path,
    netem_shell_prefix,
    profile_by_name,
)

SCHEMA_VERSION = "fleetrmw.ros2_fleetqox_router_netem_probe.v1"
FLEETQOX_RMW = "rmw_fleetqox_cpp"


def run(command: list[str], *, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=timeout)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument(
        "--extra-workspace",
        required=True,
        help="container path to the built rmw_fleetqox_cpp colcon install space",
    )
    parser.add_argument("--profile", default="wifi")
    parser.add_argument(
        "--scheduler-mode",
        choices=("fifo", "scheduled"),
        default="fifo",
        help="fifo: router forwards immediately; scheduled: deadline-aware holdback enabled",
    )
    parser.add_argument("--robot-count", type=int, default=8)
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--control-payload-bytes", type=int, default=256)
    parser.add_argument("--state-payload-bytes", type=int, default=30000)
    parser.add_argument("--publish-interval-ms", type=int, default=500)
    parser.add_argument("--timeout-s", type=float, default=40.0)
    parser.add_argument("--publisher-linger-s", type=float, default=2.0)
    parser.add_argument("--control-deadline-ms", type=int, default=1500)
    parser.add_argument("--scheduler-window-ms", type=int, default=1000)
    parser.add_argument(
        "--summary-json",
        type=Path,
        default=Path("results_rmw_socket/ros2_fleetqox_router_netem_probe_summary.json"),
    )
    args = parser.parse_args()

    summary = run_probe(
        root=ROOT,
        image=args.image,
        extra_workspace=args.extra_workspace,
        profile=args.profile,
        scheduler_mode=args.scheduler_mode,
        robot_count=max(args.robot_count, 1),
        samples=max(args.samples, 1),
        control_payload_bytes=max(args.control_payload_bytes, 1),
        state_payload_bytes=max(args.state_payload_bytes, 1),
        publish_interval_ms=max(args.publish_interval_ms, 0),
        timeout_s=max(args.timeout_s, 1.0),
        publisher_linger_s=max(args.publisher_linger_s, 0.0),
        control_deadline_ms=max(args.control_deadline_ms, 1),
        scheduler_window_ms=max(args.scheduler_window_ms, 0),
    )
    args.summary_json.parent.mkdir(parents=True, exist_ok=True)
    args.summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("ros2-fleetqox-router-netem-probe")
    print(f"  status: {summary['status']}")
    print(f"  scheduler_mode: {summary['scheduler_mode']}")
    print(f"  profile: {summary['profile']}")
    print(f"  control/state: {summary.get('control_payload_count')}/{summary.get('state_payload_count')}")
    print(f"  control_latency_ms_p95: {summary.get('control_latency_ms_p95')}")
    return 0 if summary["status"] == "ok" else 1


def run_probe(
    *,
    root: Path,
    image: str,
    extra_workspace: str,
    profile: str,
    scheduler_mode: str,
    robot_count: int,
    samples: int,
    control_payload_bytes: int,
    state_payload_bytes: int,
    publish_interval_ms: int,
    timeout_s: float,
    publisher_linger_s: float,
    control_deadline_ms: int,
    scheduler_window_ms: int,
) -> dict[str, Any]:
    telemetry_profile = profile_by_name(profile)
    # The router's --scheduler-topic-prefix identifies which topics are
    # subject to deadline-aware holdback by a plain string-prefix match, so
    # state (to-be-scheduled) topics need a prefix that no control topic
    # also matches. topic_specs_for_robot_count() interleaves the robot
    # index before the kind suffix (/robot_0000/cmd_vel, /robot_0000/odom),
    # which gives control and state topics no distinguishing shared
    # prefix -- so this probe uses its own naming with state topics under
    # a dedicated /fleetqox/ prefix, matching the convention the existing
    # router qos-matrix probes already rely on.
    topic_specs = [
        {"topic": f"/robot_{i:04d}/cmd_vel", "kind": "control", "flow": f"robot_{i:04d}/cmd_vel"}
        for i in range(robot_count)
    ] + [
        {"topic": f"/fleetqox/robot_{i:04d}/odom", "kind": "state", "flow": f"robot_{i:04d}/odom"}
        for i in range(robot_count)
    ]
    control_specs = [s for s in topic_specs if s["kind"] == "control"]
    state_specs = [s for s in topic_specs if s["kind"] == "state"]
    expected_control_count = samples * len(control_specs)
    expected_state_count = samples * len(state_specs)
    expected_frames = samples * len(topic_specs)

    run_nonce = time.time_ns()
    suffix = f"{os.getpid()}-{run_nonce}"
    network = f"fleetrmw-ros2-router-net-{suffix}"
    router_name = f"fleetrmw-ros2-router-mid-{suffix}"
    subscriber_name = f"fleetrmw-ros2-router-sub-{suffix}"
    publisher_name = f"fleetrmw-ros2-router-pub-{suffix}"
    router_port = 48600
    fleetqox_pub_port = 7500
    fleetqox_sub_port = 7501
    work_dir = root / f".tmp_fleetrmw_ros2_router_{suffix}"
    subscriber_script = work_dir / "subscriber.py"
    publisher_script = work_dir / "publisher.py"
    publisher_ready_container = "/tmp/fleetrmw_probe_ready"
    publisher_start_container = "/tmp/fleetrmw_probe_start"

    netem = netem_config_for_path(telemetry_profile, path_id="primary_wifi", loss_scale=0.0)

    work_dir.mkdir(parents=True, exist_ok=True)
    write_probe_scripts(
        subscriber_script=subscriber_script,
        publisher_script=publisher_script,
        samples=samples,
        topic_specs=topic_specs,
        payload_bytes=control_payload_bytes,
        state_payload_bytes=state_payload_bytes,
        publish_interval_ms=publish_interval_ms,
        timeout_s=timeout_s,
        publisher_linger_s=publisher_linger_s,
    )

    scheduler_args = ""
    if scheduler_mode == "scheduled":
        scheduler_args = (
            f"--scheduler-window-ms {scheduler_window_ms} "
            f"--scheduler-urgent-deadline-ms {control_deadline_ms} "
            f"--scheduler-expected-frames {expected_state_count} "
            "--scheduler-topic-prefix /fleetqox/ "
        )

    router_timeout_ms = int(timeout_s * 1000) + scheduler_window_ms + 10000

    try:
        run(["docker", "network", "create", network])

        start_container(
            root=root,
            image=image,
            name=router_name,
            network=network,
            command=(
                f"source /opt/ros/jazzy/setup.bash && source {extra_workspace}/setup.bash && "
                f"{extra_workspace}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/fleetrmw_udp_router_probe "
                f"--bind 0.0.0.0:{router_port} "
                f"--expected-frames {expected_frames} "
                f"--expected-route-advertisements {expected_frames} "
                f"--expected-graph-advertisements {expected_frames} "
                f"{scheduler_args}"
                f"--post-satisfaction-ms 500 --timeout-ms {router_timeout_ms}"
            ),
            extra_args=("--cap-add", "NET_ADMIN"),
        )
        time.sleep(1.0)

        netem_status_file = work_dir / "router_netem_status.json"
        netem_status_container = f"/work/{netem_status_file.relative_to(root)}"
        netem_result = run(
            [
                "docker", "exec", router_name, "bash", "-lc",
                netem_shell_prefix(netem, status_file=netem_status_container, require=True),
            ],
        )
        netem_status: dict[str, Any] = {}
        if netem_status_file.exists():
            try:
                netem_status = json.loads(netem_status_file.read_text())
            except json.JSONDecodeError:
                pass

        fleetqox_env = (
            f"export FLEETQOX_RMW_BIND=0.0.0.0:{{port}} "
            f"FLEETQOX_RMW_PEERS={router_name}:{router_port} && "
        )

        start_container(
            root=root,
            image=image,
            name=subscriber_name,
            network=network,
            command=(
                "source /opt/ros/jazzy/setup.bash && "
                f"source {extra_workspace}/setup.bash && "
                f"export RMW_IMPLEMENTATION={FLEETQOX_RMW} && "
                + fleetqox_env.format(port=fleetqox_sub_port)
                + f"python3 /work/{subscriber_script.relative_to(root)}"
            ),
        )
        time.sleep(1.0)
        start_container(
            root=root,
            image=image,
            name=publisher_name,
            network=network,
            command=(
                f"export FLEETQOX_PROBE_READY_FILE={publisher_ready_container} "
                f"FLEETQOX_PROBE_START_FILE={publisher_start_container} && "
                "source /opt/ros/jazzy/setup.bash && "
                f"source {extra_workspace}/setup.bash && "
                f"export RMW_IMPLEMENTATION={FLEETQOX_RMW} && "
                + fleetqox_env.format(port=fleetqox_pub_port)
                + f"python3 /work/{publisher_script.relative_to(root)}"
            ),
        )
        wait_for_container_path(publisher_name, publisher_ready_container, timeout_s=15.0)
        # Give the router time to learn both peers via graph/route
        # advertisements before releasing the publisher's data plane.
        time.sleep(2.0)
        run(["docker", "exec", publisher_name, "bash", "-lc", f"touch {publisher_start_container}"])

        publisher_rc = int(run(["docker", "wait", publisher_name], timeout=timeout_s + 30).stdout.strip())
        publisher_logs = run(["docker", "logs", publisher_name])
        subscriber_rc = int(run(["docker", "wait", subscriber_name], timeout=timeout_s + 30).stdout.strip())
        subscriber_logs = run(["docker", "logs", subscriber_name])
        router_rc = int(run(["docker", "wait", router_name], timeout=30).stdout.strip())
        router_logs = run(["docker", "logs", router_name])
        router_result = parse_last_json(router_logs.stdout)

        publisher_result = parse_last_json(publisher_logs.stdout)
        subscriber_result = parse_last_json(subscriber_logs.stdout)

        control_count = subscriber_result.get("control_payload_count", 0) if subscriber_result else 0
        state_count = subscriber_result.get("state_payload_count", 0) if subscriber_result else 0
        control_p95 = subscriber_result.get("control_latency_ms_p95") if subscriber_result else None
        control_mean = subscriber_result.get("control_latency_ms_mean") if subscriber_result else None
        delivery_ratio = (
            control_count / expected_control_count if expected_control_count else 0.0
        )
        state_delivery_ratio = (
            state_count / expected_state_count if expected_state_count else 0.0
        )
        ok = (
            publisher_rc == 0
            and subscriber_rc == 0
            and control_count == expected_control_count
            and state_count == expected_state_count
        )

        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if ok else "failed",
            "image": image,
            "rmw": FLEETQOX_RMW,
            "profile": profile,
            "scheduler_mode": scheduler_mode,
            "robot_count": robot_count,
            "samples": samples,
            "control_payload_bytes": control_payload_bytes,
            "state_payload_bytes": state_payload_bytes,
            "publish_interval_ms": publish_interval_ms,
            "control_deadline_ms": control_deadline_ms,
            "scheduler_window_ms": scheduler_window_ms if scheduler_mode == "scheduled" else None,
            "netem": netem,
            "netem_status": netem_status,
            "netem_schema_version": NETEM_SCHEMA_VERSION,
            "netem_seed_semantics": NETEM_SEED_SEMANTICS,
            "publisher_returncode": publisher_rc,
            "subscriber_returncode": subscriber_rc,
            "router_returncode": router_rc,
            "router_result": router_result,
            "control_payload_count": control_count,
            "state_payload_count": state_count,
            "control_expected_count": expected_control_count,
            "state_expected_count": expected_state_count,
            "control_delivery_ratio": delivery_ratio,
            "state_delivery_ratio": state_delivery_ratio,
            "control_latency_ms_p95": control_p95,
            "control_latency_ms_mean": control_mean,
            "publisher_stderr": "" if ok else excerpt(publisher_logs.stderr),
            "subscriber_stderr": "" if ok else excerpt(subscriber_logs.stderr),
        }
    finally:
        for name in (publisher_name, subscriber_name, router_name):
            run(["docker", "rm", "-f", name])
        run(["docker", "network", "rm", network])
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
