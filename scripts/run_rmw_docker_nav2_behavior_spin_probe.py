"""Run upstream Nav2 behavior_server Spin through FleetRMW.

This is a scoped recovery-behavior probe. It starts the real upstream
`nav2_behaviors` behavior_server, configures and activates it through
rmw_fleetqox_cpp lifecycle services, starts a fake base that publishes dynamic
`/odom` and `/tf`, sends a real `nav2_msgs/action/Spin` goal on `/spin`, and
verifies that the behavior produces `/cmd_vel` and completes with
`error_code=0`.

It proves direct Nav2 behavior-server recovery action transport and execution.
It does not yet prove recovery fallback inside a failing NavigateToPose tree or
a long recovery workload.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_rmw_docker_nav2_navigate_to_pose_probe import (  # noqa: E402
    DEFAULT_IMAGE,
    fake_base_node_py,
    run_lifecycle_step,
)
from scripts.run_rmw_docker_nav2_planner_controller_activation_probe import (  # noqa: E402
    router_metrics,
)
from scripts.run_rmw_docker_router_service_call_probe import parse_last_json  # noqa: E402


SCHEMA_VERSION = "fleetrmw.docker_nav2_behavior_spin_probe.v1"


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def behavior_server_params_yaml() -> str:
    return """behavior_server:
  ros__parameters:
    local_costmap_topic: local_costmap/costmap_raw
    global_costmap_topic: global_costmap/costmap_raw
    local_footprint_topic: local_costmap/published_footprint
    global_footprint_topic: global_costmap/published_footprint
    cycle_frequency: 10.0
    local_frame: odom
    global_frame: map
    robot_base_frame: base_link
    transform_tolerance: 0.2
    enable_stamped_cmd_vel: false
    behavior_plugins: ["spin"]
    spin:
      plugin: "nav2_behaviors::Spin"
    simulate_ahead_time: 0.0
    max_rotational_vel: 1.0
    min_rotational_vel: 0.35
    rotational_acc_lim: 3.2
"""


def spin_goal_yaml(target_yaw: float) -> str:
    return (
        f"{{target_yaw: {target_yaw}, "
        "time_allowance: {sec: 8, nanosec: 0}}"
    )


def parse_spin_output(text: str) -> dict[str, Any]:
    result_start = text.find("Result:")
    result_text = text[result_start:] if result_start >= 0 else text
    return {
        "accepted": "Goal accepted" in text,
        "succeeded": "Goal finished with status: SUCCEEDED" in text,
        "error_code": 0 if re.search(r"\berror_code:\s*0\b", result_text) else None,
        "result_observed": result_start >= 0,
    }


def run_probe(*, root: Path, image: str, port_base: int, target_yaw: float) -> dict[str, Any]:
    suffix = str(os.getpid())
    tmp = root / f".tmp_fleetrmw_nav2_behavior_spin_{suffix}"
    build_base = root / ".tmp_fleetrmw_nav2_behavior_spin_v2_build"
    install_base = root / ".tmp_fleetrmw_nav2_behavior_spin_v2_install"
    log_base = root / ".tmp_fleetrmw_nav2_behavior_spin_v2_log"
    tmp.mkdir(parents=True, exist_ok=True)
    params = tmp / "behavior_server_params.yaml"
    params.write_text(behavior_server_params_yaml(), encoding="utf-8")
    fake_base = tmp / "fake_base_node.py"
    fake_base.write_text(fake_base_node_py(), encoding="utf-8")

    router_port = port_base
    behavior_port = port_base + 1
    fake_base_port = port_base + 2
    cli_port = port_base + 20
    expected_service_frames = 18
    router_exe = (
        "/work/.tmp_fleetrmw_nav2_behavior_spin_v2_install/rmw_fleetqox_cpp/lib/"
        "rmw_fleetqox_cpp/fleetrmw_udp_router_probe"
    )
    tmp_rel = tmp.relative_to(root)
    quoted_goal_yaml = shlex.quote(spin_goal_yaml(target_yaw))

    try:
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

        parts: list[str] = [
            "set -e; source /opt/ros/jazzy/setup.bash; ",
            "if ! ros2 pkg prefix nav2_behaviors >/dev/null 2>&1; then "
            "apt-get update >/dev/null && "
            "apt-get install -y --no-install-recommends "
            "ros-jazzy-nav2-behaviors ros-jazzy-nav2-msgs "
            "ros-jazzy-nav-msgs ros-jazzy-tf2-msgs ros-jazzy-geometry-msgs "
            ">/tmp/fleetrmw_nav2_behavior_spin_install.log; "
            "fi; source /opt/ros/jazzy/setup.bash; ",
            f"source /work/{install_base.relative_to(root)}/setup.bash; ",
            f"{router_exe} --bind 127.0.0.1:{router_port} "
            "--expected-frames 1 "
            f"--expected-service-frames {expected_service_frames} "
            "--expected-graph-advertisements 4 --timeout-ms 90000 "
            "--post-satisfaction-ms 1000 "
            f"> /work/{tmp_rel}/router.log 2>&1 & router_pid=$!; ",
            "sleep 0.5; export RMW_IMPLEMENTATION=rmw_fleetqox_cpp; ",
            f"FLEETQOX_RMW_BIND=127.0.0.1:{behavior_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            f"ros2 run nav2_behaviors behavior_server --ros-args --params-file "
            f"/work/{params.relative_to(root)} "
            f"> /work/{tmp_rel}/behavior_server.log 2>&1 & behavior_pid=$!; ",
            "sleep 3; set +e; ",
        ]
        rc_index = 0
        run_lifecycle_step(
            command_parts=parts,
            tmp_rel=tmp_rel,
            bind=cli_port + rc_index,
            peer=router_port,
            node_name="behavior_server",
            action="get",
            log_name="behavior_server_get_before.log",
            timeout_s=8,
            rc_name=f"rc_{rc_index}",
        )
        rc_index += 1
        run_lifecycle_step(
            command_parts=parts,
            tmp_rel=tmp_rel,
            bind=cli_port + rc_index,
            peer=router_port,
            node_name="behavior_server",
            action="set",
            log_name="behavior_server_configure.log",
            timeout_s=18,
            rc_name=f"rc_{rc_index}",
        )
        parts[-1] = parts[-1].replace(
            "ros2 lifecycle set /behavior_server",
            "ros2 lifecycle set /behavior_server configure",
        )
        rc_index += 1
        run_lifecycle_step(
            command_parts=parts,
            tmp_rel=tmp_rel,
            bind=cli_port + rc_index,
            peer=router_port,
            node_name="behavior_server",
            action="get",
            log_name="behavior_server_get_configured.log",
            timeout_s=8,
            rc_name=f"rc_{rc_index}",
        )
        rc_index += 1

        parts.extend(
            [
                f"FLEETQOX_RMW_BIND=127.0.0.1:{fake_base_port} "
                f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
                f"FLEETQOX_FAKE_BASE_GOAL_X=0.0 "
                f"FLEETQOX_FAKE_BASE_METRICS=/work/{tmp_rel}/fake_base_metrics.json "
                f"timeout 70 python3 /work/{fake_base.relative_to(root)} "
                f"> /work/{tmp_rel}/fake_base.log 2>&1 & fake_base_pid=$!; ",
                "sleep 2; ",
            ]
        )

        run_lifecycle_step(
            command_parts=parts,
            tmp_rel=tmp_rel,
            bind=cli_port + rc_index,
            peer=router_port,
            node_name="behavior_server",
            action="set",
            log_name="behavior_server_activate.log",
            timeout_s=20,
            rc_name=f"rc_{rc_index}",
        )
        parts[-1] = parts[-1].replace(
            "ros2 lifecycle set /behavior_server",
            "ros2 lifecycle set /behavior_server activate",
        )
        rc_index += 1
        run_lifecycle_step(
            command_parts=parts,
            tmp_rel=tmp_rel,
            bind=cli_port + rc_index,
            peer=router_port,
            node_name="behavior_server",
            action="get",
            log_name="behavior_server_get_active.log",
            timeout_s=8,
            rc_name=f"rc_{rc_index}",
        )
        rc_index += 1

        parts.extend(
            [
                f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + rc_index} "
                f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
                "timeout 35 ros2 action send_goal /spin "
                "nav2_msgs/action/Spin "
                f"{quoted_goal_yaml} "
                f"> /work/{tmp_rel}/spin_goal.log 2>&1; "
                "spin_goal_rc=$?; ",
                "kill ${behavior_pid} ${fake_base_pid} >/dev/null 2>&1 || true; ",
                "wait ${behavior_pid} >/dev/null 2>&1 || true; ",
                "wait ${fake_base_pid} >/dev/null 2>&1 || true; ",
                "wait ${router_pid} >/dev/null 2>&1; router_rc=$?; ",
                f"printf 'spin_goal=%s\\nrouter=%s\\n' "
                "${spin_goal_rc} ${router_rc} "
                f"> /work/{tmp_rel}/return_codes.log; ",
                "if [ ${spin_goal_rc} -ne 0 ]; then exit 20; fi; ",
                "exit ${router_rc}",
            ]
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
                "".join(parts),
            ]
        )

        def read(name: str) -> str:
            path = tmp / name
            return path.read_text(errors="replace") if path.exists() else ""

        def read_json(name: str) -> dict[str, Any]:
            path = tmp / name
            if not path.exists():
                return {}
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
            return data if isinstance(data, dict) else {}

        behavior_log = read("behavior_server.log")
        fake_base_log = read("fake_base.log")
        fake_base_metrics = read_json("fake_base_metrics.json")
        router_log = read("router.log")
        router_summary = parse_last_json(router_log)
        metrics = router_metrics(router_summary)
        forwarded_topics = router_summary.get("forwarded_topics", [])
        graph_topics = router_summary.get("graph_topics", [])
        spin_goal = read("spin_goal.log")
        spin = parse_spin_output(spin_goal)
        behavior_active = read("behavior_server_get_active.log")
        behavior_activated = (
            "active [3]" in behavior_active
            and "Activating spin" in behavior_log
        )
        odom_runtime_ok = "/odom" in graph_topics or "/odom" in forwarded_topics
        cmd_vel_forwarded = "/cmd_vel" in forwarded_topics
        fake_base_cmd_vel_count = int(fake_base_metrics.get("cmd_vel_count", 0) or 0)
        fake_base_angular_distance = float(fake_base_metrics.get("angular_distance", 0.0) or 0.0)
        lifecycle_transport_ok = (
            metrics["status"] == "ok"
            and int(metrics["service_frames"]) >= expected_service_frames
            and int(metrics["service_forwarded"]) >= expected_service_frames
        )
        spin_ok = (
            bool(spin["accepted"])
            and bool(spin["succeeded"])
            and spin["error_code"] == 0
            and "Turning" in behavior_log
            and cmd_vel_forwarded
            and fake_base_cmd_vel_count > 0
            and fake_base_angular_distance >= abs(target_yaw) * 0.5
        )
        ok = (
            docker.returncode == 0
            and behavior_activated
            and lifecycle_transport_ok
            and bool(metrics["tf_topic_forwarded"])
            and odom_runtime_ok
            and spin_ok
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if ok else "failed",
            "nav2_behavior_server_available": True,
            "behavior_plugin": "nav2_behaviors::Spin",
            "behavior_server_activate_transition": behavior_activated,
            "behavior_server_final_state": "active" if "active [3]" in behavior_active else "unknown",
            "dynamic_tf_runtime": bool(metrics["tf_topic_forwarded"]),
            "odometry_runtime": odom_runtime_ok,
            "tf_topic_advertised": metrics["tf_topic_advertised"],
            "tf_topic_forwarded": metrics["tf_topic_forwarded"],
            "odom_topic_advertised": "/odom" in graph_topics,
            "odom_topic_forwarded": "/odom" in forwarded_topics,
            "cmd_vel_topic_advertised": "/cmd_vel" in graph_topics,
            "cmd_vel_topic_forwarded": cmd_vel_forwarded,
            "spin_status_forwarded": "/spin/_action/status" in forwarded_topics,
            "spin_feedback_forwarded": "/spin/_action/feedback" in forwarded_topics,
            "spin_action": True,
            "spin_goal_accepted": spin["accepted"],
            "spin_goal_succeeded": spin["succeeded"],
            "spin_error_code": spin["error_code"],
            "spin_target_yaw": target_yaw,
            "fake_base_cmd_vel_count": fake_base_cmd_vel_count,
            "fake_base_max_abs_cmd_theta": fake_base_metrics.get("max_abs_cmd_theta", 0.0),
            "fake_base_final_theta": fake_base_metrics.get("final_theta", 0.0),
            "fake_base_max_abs_theta": fake_base_metrics.get("max_abs_theta", 0.0),
            "fake_base_angular_distance": fake_base_angular_distance,
            "lifecycle_transport": lifecycle_transport_ok,
            "expected_service_frames": expected_service_frames,
            "fleetqox_router_service_frames": metrics["service_frames"],
            "fleetqox_router_service_forwarded": metrics["service_forwarded"],
            "fleetqox_router_received_frames": metrics["received_frames"],
            "fleetqox_router_forwarded_frames": metrics["forwarded_frames"],
            "recovery_behavior_action_claim": bool(spin_ok),
            "recovery_behavior_scope": "nav2_behavior_server_spin_action_with_fake_base",
            "nav2_recovery_behavior_claim": bool(spin_ok),
            "navigate_to_pose_recovery_tree_claim": False,
            "long_navigation_workload_claim": False,
            "docker_returncode": docker.returncode,
            "docker_stdout": docker.stdout,
            "docker_stderr": docker.stderr,
            "return_codes": read("return_codes.log"),
            "behavior_server_get_active": behavior_active,
            "spin_goal_excerpt": spin_goal[-5000:],
            "behavior_server_log_excerpt": behavior_log[-5000:],
            "fake_base_metrics": fake_base_metrics,
            "fake_base_log_excerpt": fake_base_log[-3000:],
            "router": {
                **metrics,
                "odom_topic_advertised": "/odom" in graph_topics,
                "odom_topic_forwarded": "/odom" in forwarded_topics,
                "cmd_vel_topic_advertised": "/cmd_vel" in graph_topics,
                "cmd_vel_topic_forwarded": cmd_vel_forwarded,
            },
            "router_log_excerpt": router_log[-5000:],
        }
    finally:
        for path in (tmp, build_base, install_base, log_base):
            shutil.rmtree(path, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--port-base", type=int, default=5700)
    parser.add_argument("--target-yaw", type=float, default=0.6)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_nav2_behavior_spin_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_probe(
        root=ROOT,
        image=args.image,
        port_base=args.port_base,
        target_yaw=args.target_yaw,
    )
    output = ROOT / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(
            f"status={summary['status']} spin={summary.get('spin_goal_succeeded')} "
            f"cmd_vel={summary.get('fake_base_cmd_vel_count')} "
            f"theta={summary.get('fake_base_angular_distance')}"
        )
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
