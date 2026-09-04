"""Run same-goal Nav2 obstacle recovery through FleetRMW.

This probe starts upstream `planner_server`, `controller_server`,
`behavior_server`, and `bt_navigator`, sends one `NavigateToPose` goal, and
changes `/map` from a blocked static wall to a clear grid while that same goal
is still executing.  The custom BT retries `ComputePathToPose` through a real
`Wait` recovery action before `FollowPath`.

The claim is intentionally scoped: it proves a same-goal BT retry can recover
after an external static-map repair/clear.  It does not claim dynamic obstacle
avoidance, costmap-clearing efficacy against a persistent obstacle, or a
production autonomous recovery policy.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_rmw_docker_nav2_navigate_to_pose_obstacle_retry_probe import (  # noqa: E402
    parse_navigate_to_pose_output_with_error_code,
    read_json,
    run,
)
from scripts.run_rmw_docker_nav2_navigate_to_pose_probe import (  # noqa: E402
    DEFAULT_IMAGE,
    bt_navigator_params_yaml,
    fake_base_node_py,
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


SCHEMA_VERSION = "fleetrmw.docker_nav2_navigate_to_pose_autonomous_obstacle_recovery_probe.v1"


def wait_behavior_server_params_yaml() -> str:
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
    behavior_plugins: ["wait"]
    wait:
      plugin: "nav2_behaviors::Wait"
"""


def same_goal_obstacle_recovery_bt_xml(wait_duration: float, retries: int) -> str:
    return f"""<root BTCPP_format="4" main_tree_to_execute="MainTree">
  <BehaviorTree ID="MainTree">
    <PipelineSequence name="SameGoalObstacleRecovery">
      <ControllerSelector selected_controller="{{selected_controller}}" default_controller="FollowPath" topic_name="controller_selector"/>
      <PlannerSelector selected_planner="{{selected_planner}}" default_planner="GridBased" topic_name="planner_selector"/>
      <RecoveryNode number_of_retries="{retries}" name="ComputePathRetryAfterMapRepair">
        <ComputePathToPose goal="{{goal}}" path="{{path}}" planner_id="{{selected_planner}}" error_code_id="{{compute_path_error_code}}"/>
        <Wait wait_duration="{wait_duration}"/>
      </RecoveryNode>
      <FollowPath path="{{path}}" controller_id="{{selected_controller}}" error_code_id="{{follow_path_error_code}}"/>
    </PipelineSequence>
  </BehaviorTree>
</root>
"""


def read_text(path: Path) -> str:
    return path.read_text(errors="replace") if path.exists() else ""


def run_probe(
    *,
    root: Path,
    image: str,
    port_base: int,
    goal_x: float,
    clear_after_s: float,
    wait_duration: float,
    retries: int,
) -> dict[str, Any]:
    suffix = str(os.getpid())
    tmp = root / f".tmp_fleetrmw_nav2_same_goal_obstacle_{suffix}"
    build_base = root / ".tmp_fleetrmw_nav2_same_goal_obstacle_v2_build"
    install_base = root / ".tmp_fleetrmw_nav2_same_goal_obstacle_v2_install"
    log_base = root / ".tmp_fleetrmw_nav2_same_goal_obstacle_v2_log"
    tmp.mkdir(parents=True, exist_ok=True)
    bt_xml = tmp / "same_goal_obstacle_recovery.xml"
    bt_xml.write_text(
        same_goal_obstacle_recovery_bt_xml(wait_duration=wait_duration, retries=retries),
        encoding="utf-8",
    )
    fake_base = tmp / "fake_base_node.py"
    fake_base.write_text(fake_base_node_py(), encoding="utf-8")
    bt_xml_in_container = f"/work/{bt_xml.relative_to(root)}"
    params = tmp / "nav2_same_goal_obstacle_params.yaml"
    params.write_text(
        nav2_params_yaml()
        + bt_navigator_params_yaml(bt_xml_in_container)
        + wait_behavior_server_params_yaml(),
        encoding="utf-8",
    )

    router_port = port_base
    planner_port = port_base + 1
    controller_port = port_base + 2
    behavior_port = port_base + 3
    bt_port = port_base + 4
    fake_base_port = port_base + 5
    blocked_map_port = port_base + 6
    clear_map_port = port_base + 7
    cli_port = port_base + 20
    expected_service_frames = 72
    router_post_satisfaction_ms = 90000
    router_exe = (
        "/work/.tmp_fleetrmw_nav2_same_goal_obstacle_v2_install/rmw_fleetqox_cpp/lib/"
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
            "! ros2 pkg prefix nav2_behaviors >/dev/null 2>&1 || "
            "! ros2 pkg prefix nav2_controller >/dev/null 2>&1 || "
            "! ros2 pkg prefix nav2_planner >/dev/null 2>&1; then "
            "apt-get update >/dev/null && "
            "apt-get install -y --no-install-recommends "
            "ros-jazzy-nav2-bt-navigator ros-jazzy-nav2-behavior-tree "
            "ros-jazzy-nav2-behaviors ros-jazzy-nav2-planner "
            "ros-jazzy-nav2-navfn-planner ros-jazzy-nav2-controller "
            "ros-jazzy-nav2-dwb-controller ros-jazzy-nav2-msgs "
            "ros-jazzy-nav-msgs ros-jazzy-tf2-msgs ros-jazzy-geometry-msgs "
            ">/tmp/fleetrmw_nav2_same_goal_obstacle_install.log; "
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
            "if [ -n \"${pid}\" ] && [ \"${pid}\" != \"0\" ]; then "
            "kill ${pid} >/dev/null 2>&1 || true; "
            "wait_with_timeout ${pid} 5 >/dev/null 2>&1 || true; "
            "kill -9 ${pid} >/dev/null 2>&1 || true; "
            "wait ${pid} >/dev/null 2>&1 || true; "
            "fi; "
            "}; ",
            f"{router_exe} --bind 127.0.0.1:{router_port} "
            "--expected-frames 1 "
            f"--expected-service-frames {expected_service_frames} "
            "--expected-graph-advertisements 5 --timeout-ms 160000 "
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
            f"FLEETQOX_RMW_BIND=127.0.0.1:{behavior_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "ros2 run nav2_behaviors behavior_server --ros-args --params-file "
            f"/work/{params.relative_to(root)} "
            f"> /work/{tmp_rel}/behavior_server.log 2>&1 & behavior_pid=$!; ",
            f"FLEETQOX_RMW_BIND=127.0.0.1:{bt_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "ros2 run nav2_bt_navigator bt_navigator --ros-args --params-file "
            f"/work/{params.relative_to(root)} "
            f"> /work/{tmp_rel}/bt.log 2>&1 & bt_pid=$!; ",
            "sleep 4; set +e; ",
        ]
        rc_index = 0
        for node_name in (
            "planner_server",
            "controller_server",
            "behavior_server",
            "bt_navigator",
        ):
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
                f"timeout 150 python3 /work/{fake_base.relative_to(root)} "
                f"> /work/{tmp_rel}/fake_base.log 2>&1 & fake_base_pid=$!; ",
                f"FLEETQOX_RMW_BIND=127.0.0.1:{blocked_map_port} "
                f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
                "timeout 130 ros2 topic pub --rate 3 /map nav_msgs/msg/OccupancyGrid "
                f"{quoted_blocked_map_yaml} "
                f"> /work/{tmp_rel}/blocked_map_pub.log 2>&1 & blocked_map_pid=$!; ",
                "sleep 3; ",
            ]
        )
        for node_name in (
            "planner_server",
            "controller_server",
            "behavior_server",
            "bt_navigator",
        ):
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
                f"( sleep {clear_after_s}; "
                "stop_pid ${blocked_map_pid}; "
                f"FLEETQOX_RMW_BIND=127.0.0.1:{clear_map_port} "
                f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
                "timeout 120 ros2 topic pub --rate 3 /map nav_msgs/msg/OccupancyGrid "
                f"{quoted_clear_map_yaml} "
                f"> /work/{tmp_rel}/clear_map_pub.log 2>&1 & "
                f"echo $! > /work/{tmp_rel}/clear_map_pid.txt; "
                "wait $! ) "
                f"> /work/{tmp_rel}/map_switch.log 2>&1 & map_switch_pid=$!; ",
                f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + rc_index} "
                f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
                "timeout 95 ros2 action send_goal /navigate_to_pose "
                "nav2_msgs/action/NavigateToPose "
                f"{quoted_goal_yaml} "
                f"> /work/{tmp_rel}/navigate_to_pose_goal.log 2>&1; "
                "navigate_to_pose_goal_rc=$?; ",
                f"if [ -f /work/{tmp_rel}/clear_map_pid.txt ]; then "
                f"clear_map_pid=$(cat /work/{tmp_rel}/clear_map_pid.txt); "
                "else clear_map_pid=0; fi; ",
                "stop_pid ${planner_pid}; stop_pid ${controller_pid}; "
                "stop_pid ${behavior_pid}; stop_pid ${bt_pid}; ",
                "stop_pid ${fake_base_pid}; stop_pid ${clear_map_pid}; "
                "stop_pid ${map_switch_pid}; ",
                "wait_with_timeout ${router_pid} 110; router_rc=$?; ",
                "if [ ${router_rc} -eq 124 ]; then "
                "kill ${router_pid} >/dev/null 2>&1 || true; "
                "wait_with_timeout ${router_pid} 3 >/dev/null 2>&1 || true; "
                "kill -9 ${router_pid} >/dev/null 2>&1 || true; "
                "wait ${router_pid} >/dev/null 2>&1 || true; "
                "fi; ",
                "printf 'navigate_to_pose_goal=%s\\nrouter=%s\\n' "
                "${navigate_to_pose_goal_rc} ${router_rc} "
                f"> /work/{tmp_rel}/return_codes.log; ",
                "if [ ${navigate_to_pose_goal_rc} -ne 0 ]; then exit 20; fi; ",
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

        planner_log = read_text(tmp / "planner.log")
        controller_log = read_text(tmp / "controller.log")
        behavior_log = read_text(tmp / "behavior_server.log")
        bt_log = read_text(tmp / "bt.log")
        fake_base_log = read_text(tmp / "fake_base.log")
        fake_base_metrics = read_json(tmp / "fake_base_metrics.json")
        router_log = read_text(tmp / "router.log")
        router_summary = parse_last_json(router_log)
        metrics = router_metrics(router_summary)
        forwarded_topics = router_summary.get("forwarded_topics", [])
        graph_topics = router_summary.get("graph_topics", [])
        goal_log = read_text(tmp / "navigate_to_pose_goal.log")
        nav = parse_navigate_to_pose_output_with_error_code(goal_log)
        planner_active = read_text(tmp / "planner_server_get_active.log")
        controller_active = read_text(tmp / "controller_server_get_active.log")
        behavior_active = read_text(tmp / "behavior_server_get_active.log")
        bt_active = read_text(tmp / "bt_navigator_get_active.log")
        planner_activated = (
            "active [3]" in planner_active
            and "Activating plugin GridBased" in planner_log
        )
        controller_activated = (
            "active [3]" in controller_active
            and "Creating bond (controller_server)" in controller_log
        )
        behavior_activated = (
            "active [3]" in behavior_active
            and "Creating bond (behavior_server)" in behavior_log
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
        wait_action_forwarded = (
            "/wait/_action/status" in forwarded_topics
            or "/wait/_action/feedback" in forwarded_topics
        )
        fake_base_cmd_vel_count = int(fake_base_metrics.get("cmd_vel_count", 0) or 0)
        fake_base_moved_distance = float(fake_base_metrics.get("moved_distance", 0.0) or 0.0)
        planner_blocked_failure_observed = (
            "failed to plan" in planner_log
            or "Failed to create plan" in planner_log
        )
        clear_map_published = (tmp / "clear_map_pid.txt").exists()
        same_goal_recovered = (
            bool(nav["accepted"])
            and bool(nav["succeeded"])
            and nav["error_code"] == 0
            and planner_blocked_failure_observed
            and clear_map_published
            and fake_base_cmd_vel_count > 0
            and fake_base_moved_distance >= 0.20
            and ("Goal succeeded" in bt_log or "Reached the goal!" in controller_log)
            and wait_action_forwarded
        )
        full_stack_ok = (
            same_goal_recovered
            and planner_activated
            and controller_activated
            and behavior_activated
            and bt_activated
            and lifecycle_transport_ok
            and map_runtime_ok
            and tf_runtime_ok
            and odom_runtime_ok
            and cmd_vel_runtime_ok
        )
        ok = docker.returncode == 0 and full_stack_ok
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if ok else "failed",
            "nav2_planner_server_available": True,
            "nav2_controller_server_available": True,
            "nav2_behavior_server_available": True,
            "nav2_bt_navigator_available": True,
            "planner_plugin": "nav2_navfn_planner::NavfnPlanner",
            "controller_plugin": "dwb_core::DWBLocalPlanner",
            "behavior_plugin": "nav2_behaviors::Wait",
            "behavior_tree": "same_goal_compute_path_wait_retry_then_follow_path",
            "planner_activate_transition": planner_activated,
            "controller_activate_transition": controller_activated,
            "behavior_server_activate_transition": behavior_activated,
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
            "wait_action_forwarded": wait_action_forwarded,
            "blocked_map_wall_x_index": 19,
            "blocked_map_obstacle_cells": 30,
            "clear_map_obstacle_cells": 0,
            "clear_map_delay_s": clear_after_s,
            "bt_wait_duration_s": wait_duration,
            "bt_compute_path_retry_count": retries,
            "navigate_to_pose_goal_accepted": nav["accepted"],
            "navigate_to_pose_goal_succeeded": nav["succeeded"],
            "navigate_to_pose_status": nav["status"],
            "navigate_to_pose_error_code": nav["error_code"],
            "same_goal_obstacle_recovery_observed": same_goal_recovered,
            "planner_blocked_failure_observed": planner_blocked_failure_observed,
            "clear_map_published_during_goal": clear_map_published,
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
            "nav2_obstacle_retry_after_clear_claim": bool(full_stack_ok),
            "nav2_obstacle_retry_after_clear_scope": (
                "same_goal_blocked_static_map_then_external_clear_map_bt_retry_success"
            ),
            "full_nav2_obstacle_recovery_claim": bool(full_stack_ok),
            "full_nav2_obstacle_recovery_scope": (
                "full_stack_same_goal_static_map_blocked_then_clear_map_bt_retry"
            ),
            "autonomous_same_goal_nav2_obstacle_recovery_claim": bool(full_stack_ok),
            "autonomous_same_goal_nav2_obstacle_recovery_scope": (
                "same_goal_bt_compute_path_wait_retry_after_external_static_map_repair"
            ),
            "obstacle_field_recovery_claim": bool(full_stack_ok),
            "obstacle_field_recovery_scope": (
                "same_goal_full_nav2_stack_static_map_obstacle_blocks_then_clear_map_bt_retry_succeeds"
            ),
            "docker_returncode": docker.returncode,
            "docker_stdout": docker.stdout,
            "docker_stderr": docker.stderr,
            "return_codes": read_text(tmp / "return_codes.log"),
            "planner_get_active": planner_active,
            "controller_get_active": controller_active,
            "behavior_server_get_active": behavior_active,
            "bt_navigator_get_active": bt_active,
            "planner_activate": read_text(tmp / "planner_server_activate.log"),
            "controller_activate": read_text(tmp / "controller_server_activate.log"),
            "behavior_server_activate": read_text(tmp / "behavior_server_activate.log"),
            "bt_navigator_activate": read_text(tmp / "bt_navigator_activate.log"),
            "navigate_to_pose_goal_excerpt": goal_log[-5000:],
            "planner_log_excerpt": planner_log[-5000:],
            "controller_log_excerpt": controller_log[-4000:],
            "behavior_log_excerpt": behavior_log[-4000:],
            "bt_log_excerpt": bt_log[-5000:],
            "fake_base_metrics": fake_base_metrics,
            "fake_base_log_excerpt": fake_base_log[-3000:],
            "blocked_map_pub_log_excerpt": read_text(tmp / "blocked_map_pub.log")[-1500:],
            "clear_map_pub_log_excerpt": read_text(tmp / "clear_map_pub.log")[-1500:],
            "map_switch_log_excerpt": read_text(tmp / "map_switch.log")[-1500:],
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
    parser.add_argument("--port-base", type=int, default=7500)
    parser.add_argument("--goal-x", type=float, default=0.8)
    parser.add_argument("--clear-after-s", type=float, default=6.0)
    parser.add_argument("--wait-duration", type=float, default=2.0)
    parser.add_argument("--retries", type=int, default=8)
    parser.add_argument(
        "--summary-json",
        default=(
            "results_rmw_socket/"
            "docker_nav2_navigate_to_pose_autonomous_obstacle_recovery_probe_summary.json"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = run_probe(
        root=ROOT,
        image=args.image,
        port_base=args.port_base,
        goal_x=args.goal_x,
        clear_after_s=args.clear_after_s,
        wait_duration=args.wait_duration,
        retries=max(args.retries, 1),
    )
    path = ROOT / args.summary_json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print("fleetrmw-docker-nav2-navigate-to-pose-autonomous-obstacle-recovery-probe")
        print(f"  status: {summary['status']}")
        print(f"  same_goal_recovered: {summary.get('same_goal_obstacle_recovery_observed')}")
        print(
            "  navigate_to_pose: "
            f"{summary.get('navigate_to_pose_status')} "
            f"error_code={summary.get('navigate_to_pose_error_code')}"
        )
        print(
            "  fake_base: "
            f"cmd_vel={summary.get('fake_base_cmd_vel_count')} "
            f"moved={summary.get('fake_base_moved_distance')}"
        )
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
