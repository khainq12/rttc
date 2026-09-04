"""Run full-stack Nav2 obstacle retry-after-clear through FleetRMW.

This probe starts upstream `planner_server`, `controller_server`, and
`bt_navigator`, publishes a dynamic fake base through FleetRMW, sends a
`NavigateToPose` goal while a static occupancy-grid wall blocks the path, then
replaces the map with a clear grid and retries the same `NavigateToPose` goal.

The claim is intentionally scoped: it proves the full Nav2 stack can observe a
blocked static map, then complete a retry after an external map repair/clear. It
does not claim autonomous same-goal obstacle clearing inside one BT execution.
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
    bt_navigator_params_yaml,
    fake_base_node_py,
    minimal_navigate_to_pose_bt_xml,
    navigate_to_pose_goal_yaml,
    run_lifecycle_step,
)
from scripts.run_rmw_docker_nav2_planner_compute_path_probe import (  # noqa: E402
    occupancy_grid_yaml,
)
from scripts.run_rmw_docker_nav2_planner_controller_activation_probe import (  # noqa: E402
    router_metrics,
)
from scripts.run_rmw_docker_nav2_planner_controller_lifecycle_probe import (  # noqa: E402
    nav2_params_yaml,
)
from scripts.run_rmw_docker_nav2_planner_obstacle_repair_probe import (  # noqa: E402
    blocked_occupancy_grid_yaml,
)
from scripts.run_rmw_docker_router_service_call_probe import parse_last_json  # noqa: E402


SCHEMA_VERSION = "fleetrmw.docker_nav2_navigate_to_pose_obstacle_retry_probe.v1"


def run(command: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            command,
            124,
            stdout=exc.stdout if isinstance(exc.stdout, str) else "",
            stderr=(exc.stderr if isinstance(exc.stderr, str) else "") + "\nsubprocess timeout\n",
        )


def parse_navigate_to_pose_output_with_error_code(text: str) -> dict[str, Any]:
    result_start = text.find("Result:")
    result_text = text[result_start:] if result_start >= 0 else text
    match = re.search(r"\berror_code:\s*(\d+)\b", result_text)
    status_match = re.search(r"Goal finished with status:\s*([A-Z_]+)", text)
    return {
        "accepted": "Goal accepted" in text,
        "succeeded": "Goal finished with status: SUCCEEDED" in text,
        "status": status_match.group(1) if status_match else "unknown",
        "error_code": int(match.group(1)) if match else None,
        "result_observed": result_start >= 0,
    }


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def run_probe(*, root: Path, image: str, port_base: int, goal_x: float) -> dict[str, Any]:
    suffix = str(os.getpid())
    tmp = root / f".tmp_fleetrmw_nav2_obstacle_retry_{suffix}"
    build_base = root / ".tmp_fleetrmw_nav2_obstacle_retry_v2_build"
    install_base = root / ".tmp_fleetrmw_nav2_obstacle_retry_v2_install"
    log_base = root / ".tmp_fleetrmw_nav2_obstacle_retry_v2_log"
    tmp.mkdir(parents=True, exist_ok=True)
    bt_xml = tmp / "minimal_nav_to_pose.xml"
    bt_xml.write_text(minimal_navigate_to_pose_bt_xml(), encoding="utf-8")
    fake_base = tmp / "fake_base_node.py"
    fake_base.write_text(fake_base_node_py(), encoding="utf-8")
    bt_xml_in_container = f"/work/{bt_xml.relative_to(root)}"
    params = tmp / "nav2_obstacle_retry_params.yaml"
    params.write_text(
        nav2_params_yaml() + bt_navigator_params_yaml(bt_xml_in_container),
        encoding="utf-8",
    )

    router_port = port_base
    planner_port = port_base + 1
    controller_port = port_base + 2
    bt_port = port_base + 3
    fake_base_port = port_base + 4
    blocked_map_port = port_base + 5
    clear_map_port = port_base + 6
    cli_port = port_base + 20
    expected_service_frames = 58
    router_post_satisfaction_ms = 90000
    router_exe = (
        "/work/.tmp_fleetrmw_nav2_obstacle_retry_v2_install/rmw_fleetqox_cpp/lib/"
        "rmw_fleetqox_cpp/fleetrmw_udp_router_probe"
    )
    tmp_rel = tmp.relative_to(root)
    quoted_blocked_map_yaml = shlex.quote(blocked_occupancy_grid_yaml())
    quoted_clear_map_yaml = shlex.quote(occupancy_grid_yaml())
    quoted_goal_yaml = shlex.quote(
        navigate_to_pose_goal_yaml(bt_xml_in_container, goal_x=goal_x)
    )

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
            ],
            timeout=600,
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
            "if ! ros2 pkg prefix nav2_bt_navigator >/dev/null 2>&1 || "
            "! ros2 pkg prefix nav2_behavior_tree >/dev/null 2>&1 || "
            "! ros2 pkg prefix nav2_controller >/dev/null 2>&1 || "
            "! ros2 pkg prefix nav2_planner >/dev/null 2>&1; then "
            "apt-get update >/dev/null && "
            "apt-get install -y --no-install-recommends "
            "ros-jazzy-nav2-bt-navigator ros-jazzy-nav2-behavior-tree "
            "ros-jazzy-nav2-planner ros-jazzy-nav2-navfn-planner "
            "ros-jazzy-nav2-controller ros-jazzy-nav2-dwb-controller "
            "ros-jazzy-nav2-msgs ros-jazzy-nav-msgs ros-jazzy-tf2-msgs "
            ">/tmp/fleetrmw_nav2_obstacle_retry_install.log; "
            "fi; source /opt/ros/jazzy/setup.bash; ",
            f"source /work/{install_base.relative_to(root)}/setup.bash; ",
            "wait_with_timeout() { "
            "pid=$1; limit=$2; elapsed=0; "
            "while kill -0 ${pid} >/dev/null 2>&1 && [ ${elapsed} -lt ${limit} ]; do "
            "sleep 1; elapsed=$((elapsed + 1)); "
            "done; "
            "if kill -0 ${pid} >/dev/null 2>&1; then return 124; fi; "
            "wait ${pid} >/dev/null 2>&1; return $?; "
            "}; ",
            "stop_pid() { "
            "pid=$1; "
            "kill ${pid} >/dev/null 2>&1 || true; "
            "wait_with_timeout ${pid} 5 >/dev/null 2>&1 || true; "
            "kill -9 ${pid} >/dev/null 2>&1 || true; "
            "wait ${pid} >/dev/null 2>&1 || true; "
            "}; ",
            f"{router_exe} --bind 127.0.0.1:{router_port} "
            "--expected-frames 1 "
            f"--expected-service-frames {expected_service_frames} "
            "--expected-graph-advertisements 4 --timeout-ms 140000 "
            f"--post-satisfaction-ms {router_post_satisfaction_ms} "
            f"> /work/{tmp_rel}/router.log 2>&1 & router_pid=$!; ",
            "sleep 0.5; export RMW_IMPLEMENTATION=rmw_fleetqox_cpp; ",
            f"FLEETQOX_RMW_BIND=127.0.0.1:{planner_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "ros2 run nav2_planner planner_server --ros-args --params-file "
            f"/work/{params.relative_to(root)} "
            f"> /work/{tmp_rel}/planner.log 2>&1 & planner_pid=$!; ",
            f"FLEETQOX_RMW_BIND=127.0.0.1:{controller_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "ros2 run nav2_controller controller_server --ros-args --params-file "
            f"/work/{params.relative_to(root)} "
            f"> /work/{tmp_rel}/controller.log 2>&1 & controller_pid=$!; ",
            f"FLEETQOX_RMW_BIND=127.0.0.1:{bt_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "ros2 run nav2_bt_navigator bt_navigator --ros-args --params-file "
            f"/work/{params.relative_to(root)} "
            f"> /work/{tmp_rel}/bt.log 2>&1 & bt_pid=$!; ",
            "sleep 4; set +e; ",
        ]
        rc_index = 0
        for node_name in ("planner_server", "controller_server", "bt_navigator"):
            run_lifecycle_step(
                command_parts=parts,
                tmp_rel=tmp_rel,
                bind=cli_port + rc_index,
                peer=router_port,
                node_name=node_name,
                action="get",
                log_name=f"{node_name}_get_before.log",
                timeout_s=8,
                rc_name=f"rc_{rc_index}",
            )
            rc_index += 1
            run_lifecycle_step(
                command_parts=parts,
                tmp_rel=tmp_rel,
                bind=cli_port + rc_index,
                peer=router_port,
                node_name=node_name,
                action="set",
                log_name=f"{node_name}_configure.log",
                timeout_s=18,
                rc_name=f"rc_{rc_index}",
            )
            parts[-1] = parts[-1].replace(
                f"ros2 lifecycle set /{node_name}",
                f"ros2 lifecycle set /{node_name} configure",
            )
            rc_index += 1
            run_lifecycle_step(
                command_parts=parts,
                tmp_rel=tmp_rel,
                bind=cli_port + rc_index,
                peer=router_port,
                node_name=node_name,
                action="get",
                log_name=f"{node_name}_get_configured.log",
                timeout_s=8,
                rc_name=f"rc_{rc_index}",
            )
            rc_index += 1

        parts.extend(
            [
                f"FLEETQOX_RMW_BIND=127.0.0.1:{fake_base_port} "
                f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
                f"FLEETQOX_FAKE_BASE_GOAL_X={goal_x} "
                f"FLEETQOX_FAKE_BASE_METRICS=/work/{tmp_rel}/fake_base_metrics.json "
                f"timeout 130 python3 /work/{fake_base.relative_to(root)} "
                f"> /work/{tmp_rel}/fake_base.log 2>&1 & fake_base_pid=$!; ",
                f"FLEETQOX_RMW_BIND=127.0.0.1:{blocked_map_port} "
                f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
                "timeout 100 ros2 topic pub --rate 3 /map nav_msgs/msg/OccupancyGrid "
                f"{quoted_blocked_map_yaml} "
                f"> /work/{tmp_rel}/blocked_map_pub.log 2>&1 & blocked_map_pid=$!; ",
                "sleep 3; ",
            ]
        )
        for node_name in ("planner_server", "controller_server", "bt_navigator"):
            run_lifecycle_step(
                command_parts=parts,
                tmp_rel=tmp_rel,
                bind=cli_port + rc_index,
                peer=router_port,
                node_name=node_name,
                action="set",
                log_name=f"{node_name}_activate.log",
                timeout_s=20,
                rc_name=f"rc_{rc_index}",
            )
            parts[-1] = parts[-1].replace(
                f"ros2 lifecycle set /{node_name}",
                f"ros2 lifecycle set /{node_name} activate",
            )
            rc_index += 1
            run_lifecycle_step(
                command_parts=parts,
                tmp_rel=tmp_rel,
                bind=cli_port + rc_index,
                peer=router_port,
                node_name=node_name,
                action="get",
                log_name=f"{node_name}_get_active.log",
                timeout_s=8,
                rc_name=f"rc_{rc_index}",
            )
            rc_index += 1

        parts.extend(
            [
                f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + rc_index} "
                f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
                "timeout 55 ros2 action send_goal /navigate_to_pose "
                "nav2_msgs/action/NavigateToPose "
                f"{quoted_goal_yaml} "
                f"> /work/{tmp_rel}/blocked_navigate_to_pose_goal.log 2>&1; "
                "blocked_navigate_to_pose_goal_rc=$?; ",
                "stop_pid ${blocked_map_pid}; ",
                f"FLEETQOX_RMW_BIND=127.0.0.1:{clear_map_port} "
                f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
                "timeout 100 ros2 topic pub --rate 3 /map nav_msgs/msg/OccupancyGrid "
                f"{quoted_clear_map_yaml} "
                f"> /work/{tmp_rel}/clear_map_pub.log 2>&1 & clear_map_pid=$!; ",
                "sleep 5; ",
                f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + rc_index + 1} "
                f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
                "timeout 75 ros2 action send_goal /navigate_to_pose "
                "nav2_msgs/action/NavigateToPose "
                f"{quoted_goal_yaml} "
                f"> /work/{tmp_rel}/clear_navigate_to_pose_goal.log 2>&1; "
                "clear_navigate_to_pose_goal_rc=$?; ",
                "stop_pid ${planner_pid}; stop_pid ${controller_pid}; stop_pid ${bt_pid}; ",
                "stop_pid ${fake_base_pid}; stop_pid ${clear_map_pid}; ",
                "wait_with_timeout ${router_pid} 110; router_rc=$?; ",
                "if [ ${router_rc} -eq 124 ]; then "
                "kill ${router_pid} >/dev/null 2>&1 || true; "
                "wait_with_timeout ${router_pid} 3 >/dev/null 2>&1 || true; "
                "kill -9 ${router_pid} >/dev/null 2>&1 || true; "
                "wait ${router_pid} >/dev/null 2>&1 || true; "
                "fi; ",
                f"printf 'blocked_navigate_to_pose_goal=%s\\n"
                "clear_navigate_to_pose_goal=%s\\nrouter=%s\\n' "
                "${blocked_navigate_to_pose_goal_rc} ${clear_navigate_to_pose_goal_rc} ${router_rc} "
                f"> /work/{tmp_rel}/return_codes.log; ",
                "if [ ${clear_navigate_to_pose_goal_rc} -ne 0 ]; then exit 20; fi; ",
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
            ],
            timeout=900,
        )

        def read(name: str) -> str:
            path = tmp / name
            return path.read_text(errors="replace") if path.exists() else ""

        planner_log = read("planner.log")
        controller_log = read("controller.log")
        bt_log = read("bt.log")
        fake_base_log = read("fake_base.log")
        fake_base_metrics = read_json(tmp / "fake_base_metrics.json")
        router_log = read("router.log")
        router_summary = parse_last_json(router_log)
        metrics = router_metrics(router_summary)
        forwarded_topics = router_summary.get("forwarded_topics", [])
        graph_topics = router_summary.get("graph_topics", [])
        blocked_goal = read("blocked_navigate_to_pose_goal.log")
        clear_goal = read("clear_navigate_to_pose_goal.log")
        blocked_nav = parse_navigate_to_pose_output_with_error_code(blocked_goal)
        clear_nav = parse_navigate_to_pose_output_with_error_code(clear_goal)
        planner_active = read("planner_server_get_active.log")
        controller_active = read("controller_server_get_active.log")
        bt_active = read("bt_navigator_get_active.log")
        planner_activated = (
            "active [3]" in planner_active
            and "Activating plugin GridBased" in planner_log
        )
        controller_activated = (
            "active [3]" in controller_active
            and "Creating bond (controller_server)" in controller_log
        )
        bt_activated = "active [3]" in bt_active and "Creating bond (bt_navigator)" in bt_log
        lifecycle_transport_ok = (
            metrics["status"] == "ok"
            and int(metrics["service_frames"]) >= expected_service_frames
            and int(metrics["service_forwarded"]) >= expected_service_frames
        )
        map_runtime_ok = "/map" in graph_topics and "/map" in forwarded_topics
        tf_runtime_ok = bool(metrics["tf_topic_advertised"]) and bool(metrics["tf_topic_forwarded"])
        odom_runtime_ok = "/odom" in graph_topics and "/odom" in forwarded_topics
        cmd_vel_runtime_ok = "/cmd_vel" in graph_topics and "/cmd_vel" in forwarded_topics
        fake_base_cmd_vel_count = int(fake_base_metrics.get("cmd_vel_count", 0) or 0)
        fake_base_moved_distance = float(fake_base_metrics.get("moved_distance", 0.0) or 0.0)
        planner_blocked_failure_observed = (
            "failed to plan" in planner_log
            or "Failed to create plan" in planner_log
        )
        blocked_failed = (
            bool(blocked_nav["accepted"])
            and not bool(blocked_nav["succeeded"])
            and planner_blocked_failure_observed
        )
        clear_ok = (
            bool(clear_nav["accepted"])
            and bool(clear_nav["succeeded"])
            and clear_nav["error_code"] == 0
            and fake_base_cmd_vel_count > 0
            and fake_base_moved_distance >= 0.20
            and ("Goal succeeded" in bt_log or "Reached the goal!" in controller_log)
        )
        full_stack_retry_ok = (
            blocked_failed
            and clear_ok
            and planner_activated
            and controller_activated
            and bt_activated
            and lifecycle_transport_ok
            and map_runtime_ok
            and tf_runtime_ok
            and odom_runtime_ok
            and cmd_vel_runtime_ok
        )
        ok = docker.returncode == 0 and full_stack_retry_ok
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if ok else "failed",
            "nav2_planner_server_available": True,
            "nav2_controller_server_available": True,
            "nav2_bt_navigator_available": True,
            "planner_plugin": "nav2_navfn_planner::NavfnPlanner",
            "controller_plugin": "dwb_core::DWBLocalPlanner",
            "behavior_tree": "minimal_compute_path_to_pose_then_follow_path",
            "planner_activate_transition": planner_activated,
            "controller_activate_transition": controller_activated,
            "bt_navigator_activate_transition": bt_activated,
            "dynamic_tf_runtime": tf_runtime_ok,
            "map_runtime": map_runtime_ok,
            "odometry_runtime": odom_runtime_ok,
            "tf_topic_advertised": metrics["tf_topic_advertised"],
            "tf_topic_forwarded": metrics["tf_topic_forwarded"],
            "map_topic_advertised": "/map" in graph_topics,
            "map_topic_forwarded": "/map" in forwarded_topics,
            "odom_topic_advertised": "/odom" in graph_topics,
            "odom_topic_forwarded": "/odom" in forwarded_topics,
            "cmd_vel_topic_advertised": "/cmd_vel" in graph_topics,
            "cmd_vel_topic_forwarded": "/cmd_vel" in forwarded_topics,
            "blocked_map_wall_x_index": 19,
            "blocked_map_obstacle_cells": 30,
            "clear_map_obstacle_cells": 0,
            "blocked_navigate_to_pose_goal_accepted": blocked_nav["accepted"],
            "blocked_navigate_to_pose_goal_succeeded": blocked_nav["succeeded"],
            "blocked_navigate_to_pose_status": blocked_nav["status"],
            "blocked_navigate_to_pose_error_code": blocked_nav["error_code"],
            "blocked_navigate_to_pose_failed": blocked_failed,
            "planner_blocked_failure_observed": planner_blocked_failure_observed,
            "clear_navigate_to_pose_goal_accepted": clear_nav["accepted"],
            "clear_navigate_to_pose_goal_succeeded": clear_nav["succeeded"],
            "clear_navigate_to_pose_status": clear_nav["status"],
            "clear_navigate_to_pose_error_code": clear_nav["error_code"],
            "fake_base_cmd_vel_count": fake_base_cmd_vel_count,
            "fake_base_max_abs_cmd_x": fake_base_metrics.get("max_abs_cmd_x", 0.0),
            "fake_base_final_x": fake_base_metrics.get("final_x", 0.0),
            "fake_base_max_x": fake_base_metrics.get("max_x", 0.0),
            "fake_base_moved_distance": fake_base_moved_distance,
            "navigation_goal_x": goal_x,
            "navigation_goal_y": 0.0,
            "compute_path_status_forwarded": (
                "/compute_path_to_pose/_action/status" in forwarded_topics
            ),
            "follow_path_feedback_forwarded": "/follow_path/_action/feedback" in forwarded_topics,
            "navigate_to_pose_status_forwarded": "/navigate_to_pose/_action/status" in forwarded_topics,
            "lifecycle_transport": lifecycle_transport_ok,
            "expected_service_frames": expected_service_frames,
            "router_post_satisfaction_ms": router_post_satisfaction_ms,
            "fleetqox_router_service_frames": metrics["service_frames"],
            "fleetqox_router_service_forwarded": metrics["service_forwarded"],
            "fleetqox_router_received_frames": metrics["received_frames"],
            "fleetqox_router_forwarded_frames": metrics["forwarded_frames"],
            "nav2_obstacle_retry_after_clear_claim": bool(full_stack_retry_ok),
            "nav2_obstacle_retry_after_clear_scope": (
                "blocked_static_map_navigate_to_pose_then_clear_map_retry_success"
            ),
            "full_nav2_obstacle_recovery_claim": bool(full_stack_retry_ok),
            "full_nav2_obstacle_recovery_scope": (
                "full_stack_two_goal_static_map_blocked_then_clear_map_retry"
            ),
            "autonomous_same_goal_nav2_obstacle_recovery_claim": False,
            "autonomous_same_goal_nav2_obstacle_recovery_scope": (
                "not_same_goal_autonomous_clear_or_costmap_recovery"
            ),
            "obstacle_field_recovery_claim": bool(full_stack_retry_ok),
            "obstacle_field_recovery_scope": (
                "full_nav2_stack_static_map_obstacle_blocks_then_clear_map_retry_succeeds"
            ),
            "docker_returncode": docker.returncode,
            "docker_stdout": docker.stdout,
            "docker_stderr": docker.stderr,
            "return_codes": read("return_codes.log"),
            "planner_get_active": planner_active,
            "controller_get_active": controller_active,
            "bt_navigator_get_active": bt_active,
            "planner_activate": read("planner_server_activate.log"),
            "controller_activate": read("controller_server_activate.log"),
            "bt_navigator_activate": read("bt_navigator_activate.log"),
            "blocked_navigate_to_pose_goal_excerpt": blocked_goal[-5000:],
            "clear_navigate_to_pose_goal_excerpt": clear_goal[-5000:],
            "planner_log_excerpt": planner_log[-4000:],
            "controller_log_excerpt": controller_log[-4000:],
            "bt_log_excerpt": bt_log[-5000:],
            "fake_base_metrics": fake_base_metrics,
            "fake_base_log_excerpt": fake_base_log[-3000:],
            "blocked_map_pub_log_excerpt": read("blocked_map_pub.log")[-1500:],
            "clear_map_pub_log_excerpt": read("clear_map_pub.log")[-1500:],
            "router": {
                **metrics,
                "map_topic_advertised": "/map" in graph_topics,
                "map_topic_forwarded": "/map" in forwarded_topics,
                "odom_topic_advertised": "/odom" in graph_topics,
                "odom_topic_forwarded": "/odom" in forwarded_topics,
                "cmd_vel_topic_advertised": "/cmd_vel" in graph_topics,
                "cmd_vel_topic_forwarded": "/cmd_vel" in forwarded_topics,
            },
            "router_log_excerpt": router_log[-5000:],
        }
    finally:
        for path in (tmp, build_base, install_base, log_base):
            shutil.rmtree(path, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--port-base", type=int, default=7400)
    parser.add_argument("--goal-x", type=float, default=0.8)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_nav2_navigate_to_pose_obstacle_retry_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_probe(
        root=ROOT,
        image=args.image,
        port_base=args.port_base,
        goal_x=args.goal_x,
    )
    output = ROOT / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(
            f"status={summary['status']} "
            f"blocked={summary.get('blocked_navigate_to_pose_status')} "
            f"clear={summary.get('clear_navigate_to_pose_status')} "
            f"retry={summary.get('nav2_obstacle_retry_after_clear_claim')}"
        )
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
