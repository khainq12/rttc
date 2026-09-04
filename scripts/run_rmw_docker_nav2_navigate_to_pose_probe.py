"""Run upstream Nav2 NavigateToPose through FleetRMW.

The probe starts real upstream `planner_server`, `controller_server`, and
`bt_navigator`; configures and activates all three through rmw_fleetqox_cpp
lifecycle services; publishes repeated `/map`, `/tf`, and `/odom` runtime
inputs through the FleetRMW UDP router; and sends a real
`nav2_msgs/action/NavigateToPose` goal through a minimal behavior tree:

  ComputePathToPose -> FollowPath

By default, the goal is intentionally the current robot pose so the CI slice
proves the full Nav2 action pipeline without requiring robot motion simulation.
The internal `moving_base` mode is used by the dedicated moving-goal runner to
add a small fake base that subscribes to `/cmd_vel` and publishes dynamic
`/odom` and `/tf`.
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

from scripts.run_rmw_docker_nav2_controller_follow_path_probe import (  # noqa: E402
    odometry_yaml,
)
from scripts.run_rmw_docker_nav2_planner_compute_path_probe import (  # noqa: E402
    occupancy_grid_yaml,
)
from scripts.run_rmw_docker_nav2_planner_controller_activation_probe import (  # noqa: E402
    dynamic_tf_message_yaml,
    router_metrics,
)
from scripts.run_rmw_docker_nav2_planner_controller_lifecycle_probe import (  # noqa: E402
    nav2_params_yaml,
)
from scripts.run_rmw_docker_router_service_call_probe import parse_last_json  # noqa: E402


SCHEMA_VERSION = "fleetrmw.docker_nav2_navigate_to_pose_probe.v1"
DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def minimal_navigate_to_pose_bt_xml() -> str:
    return """<root BTCPP_format="4" main_tree_to_execute="MainTree">
  <BehaviorTree ID="MainTree">
    <PipelineSequence name="NavigateWithReplanning">
      <ControllerSelector selected_controller="{selected_controller}" default_controller="FollowPath" topic_name="controller_selector"/>
      <PlannerSelector selected_planner="{selected_planner}" default_planner="GridBased" topic_name="planner_selector"/>
      <GoalUpdatedController>
        <ComputePathToPose goal="{goal}" path="{path}" planner_id="{selected_planner}" error_code_id="{compute_path_error_code}"/>
      </GoalUpdatedController>
      <FollowPath path="{path}" controller_id="{selected_controller}" error_code_id="{follow_path_error_code}"/>
    </PipelineSequence>
  </BehaviorTree>
</root>
"""


def bt_navigator_params_yaml(bt_xml_path: str) -> str:
    return f"""bt_navigator:
  ros__parameters:
    global_frame: map
    robot_base_frame: base_link
    odom_topic: /odom
    bt_loop_duration: 10
    default_server_timeout: 20
    wait_for_service_timeout: 10000
    navigators: ["navigate_to_pose"]
    navigate_to_pose:
      plugin: "nav2_bt_navigator::NavigateToPoseNavigator"
    default_nav_to_pose_bt_xml: "{bt_xml_path}"
"""


def navigate_to_pose_goal_yaml(
    bt_xml_path: str,
    *,
    goal_x: float = 0.0,
    goal_y: float = 0.0,
) -> str:
    return (
        "{pose: {header: {frame_id: 'map'}, "
        f"pose: {{position: {{x: {goal_x}, y: {goal_y}, z: 0.0}}, "
        "orientation: {w: 1.0}}}, "
        f"behavior_tree: '{bt_xml_path}'}}"
    )


def fake_base_node_py() -> str:
    return r'''#!/usr/bin/env python3
import json
import math
import os
import time

import rclpy
from rclpy.executors import ExternalShutdownException
from geometry_msgs.msg import TransformStamped, Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from tf2_msgs.msg import TFMessage


class FakeBase(Node):
    def __init__(self):
        super().__init__("fleetrmw_fake_nav2_base")
        self.goal_x = float(os.environ.get("FLEETQOX_FAKE_BASE_GOAL_X", "0.6"))
        self.metrics_path = os.environ.get(
            "FLEETQOX_FAKE_BASE_METRICS",
            "/tmp/fleetrmw_fake_base_metrics.json",
        )
        self.x = 0.0
        self.theta = 0.0
        self.cmd_x = 0.0
        self.cmd_theta = 0.0
        self.cmd_vel_count = 0
        self.max_abs_cmd_x = 0.0
        self.max_abs_cmd_theta = 0.0
        self.max_x = 0.0
        self.max_abs_theta = 0.0
        self.last_tick = time.monotonic()
        self.create_subscription(Twist, "/cmd_vel", self.on_cmd_vel, 10)
        self.odom_pub = self.create_publisher(Odometry, "/odom", 10)
        self.tf_pub = self.create_publisher(TFMessage, "/tf", 10)
        self.create_timer(0.05, self.tick)

    def on_cmd_vel(self, msg):
        self.cmd_x = max(min(float(msg.linear.x), 0.35), -0.35)
        self.cmd_theta = max(min(float(msg.angular.z), 1.5), -1.5)
        self.cmd_vel_count += 1
        self.max_abs_cmd_x = max(self.max_abs_cmd_x, abs(self.cmd_x))
        self.max_abs_cmd_theta = max(self.max_abs_cmd_theta, abs(self.cmd_theta))

    def tick(self):
        now = time.monotonic()
        dt = max(0.0, min(now - self.last_tick, 0.2))
        self.last_tick = now
        self.theta += self.cmd_theta * dt
        self.x += math.cos(self.theta) * self.cmd_x * dt
        self.x = max(-0.05, min(self.x, self.goal_x + 0.2))
        self.max_x = max(self.max_x, self.x)
        self.max_abs_theta = max(self.max_abs_theta, abs(self.theta))
        self.publish_state()
        self.write_metrics()

    def publish_state(self):
        stamp = self.get_clock().now().to_msg()
        odom = Odometry()
        odom.header.stamp = stamp
        odom.header.frame_id = "odom"
        odom.child_frame_id = "base_link"
        odom.pose.pose.position.x = self.x
        odom.pose.pose.orientation.z = math.sin(self.theta / 2.0)
        odom.pose.pose.orientation.w = math.cos(self.theta / 2.0)
        odom.twist.twist.linear.x = self.cmd_x
        odom.twist.twist.angular.z = self.cmd_theta
        self.odom_pub.publish(odom)

        map_to_odom = TransformStamped()
        map_to_odom.header.stamp = stamp
        map_to_odom.header.frame_id = "map"
        map_to_odom.child_frame_id = "odom"
        map_to_odom.transform.rotation.w = 1.0

        odom_to_base = TransformStamped()
        odom_to_base.header.stamp = stamp
        odom_to_base.header.frame_id = "odom"
        odom_to_base.child_frame_id = "base_link"
        odom_to_base.transform.translation.x = self.x
        odom_to_base.transform.rotation.z = math.sin(self.theta / 2.0)
        odom_to_base.transform.rotation.w = math.cos(self.theta / 2.0)
        self.tf_pub.publish(TFMessage(transforms=[map_to_odom, odom_to_base]))

    def write_metrics(self):
        metrics = {
            "cmd_vel_count": self.cmd_vel_count,
            "max_abs_cmd_x": self.max_abs_cmd_x,
            "max_abs_cmd_theta": self.max_abs_cmd_theta,
            "final_x": self.x,
            "max_x": self.max_x,
            "final_theta": self.theta,
            "max_abs_theta": self.max_abs_theta,
            "goal_x": self.goal_x,
            "moved_distance": abs(self.x),
            "angular_distance": abs(self.theta),
        }
        tmp_path = self.metrics_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(metrics, handle, sort_keys=True)
        os.replace(tmp_path, self.metrics_path)


def main():
    rclpy.init()
    node = FakeBase()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.write_metrics()
        try:
            node.destroy_node()
        except Exception:
            pass
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
'''


def parse_navigate_to_pose_output(text: str) -> dict[str, Any]:
    result_start = text.find("Result:")
    result_text = text[result_start:] if result_start >= 0 else text
    return {
        "accepted": "Goal accepted" in text,
        "succeeded": "Goal finished with status: SUCCEEDED" in text,
        "error_code": 0 if re.search(r"\berror_code:\s*0\b", result_text) else None,
        "result_observed": result_start >= 0,
    }


def run_lifecycle_step(
    *,
    command_parts: list[str],
    tmp_rel: Path,
    bind: int,
    peer: int,
    node_name: str,
    action: str,
    log_name: str,
    timeout_s: int,
    rc_name: str,
) -> None:
    command_parts.append(
        f"FLEETQOX_RMW_BIND=127.0.0.1:{bind} "
        f"FLEETQOX_RMW_PEERS=127.0.0.1:{peer} "
        f"timeout {timeout_s} ros2 lifecycle {action} /{node_name} "
        f"> /work/{tmp_rel}/{log_name} 2>&1; {rc_name}=$?; sleep 0.5; "
    )


def run_probe(
    *,
    root: Path,
    image: str,
    port_base: int,
    goal_x: float = 0.0,
    goal_y: float = 0.0,
    moving_base: bool = False,
    schema_version: str = SCHEMA_VERSION,
) -> dict[str, Any]:
    suffix = str(os.getpid())
    tmp = root / f".tmp_fleetrmw_nav2_navigate_to_pose_{suffix}"
    build_base = root / ".tmp_fleetrmw_nav2_ntp_v2_build"
    install_base = root / ".tmp_fleetrmw_nav2_ntp_v2_install"
    log_base = root / ".tmp_fleetrmw_nav2_ntp_v2_log"
    tmp.mkdir(parents=True, exist_ok=True)
    bt_xml = tmp / "minimal_nav_to_pose.xml"
    bt_xml.write_text(minimal_navigate_to_pose_bt_xml(), encoding="utf-8")
    fake_base = tmp / "fake_base_node.py"
    if moving_base:
        fake_base.write_text(fake_base_node_py(), encoding="utf-8")
    bt_xml_in_container = f"/work/{bt_xml.relative_to(root)}"
    params = tmp / "nav2_params.yaml"
    params.write_text(
        nav2_params_yaml() + bt_navigator_params_yaml(bt_xml_in_container),
        encoding="utf-8",
    )

    router_port = port_base
    planner_port = port_base + 1
    controller_port = port_base + 2
    bt_port = port_base + 3
    tf_port = port_base + 4
    map_port = port_base + 5
    odom_port = port_base + 6
    cli_port = port_base + 20
    expected_service_frames = 54
    router_exe = (
        "/work/.tmp_fleetrmw_nav2_ntp_v2_install/rmw_fleetqox_cpp/lib/"
        "rmw_fleetqox_cpp/fleetrmw_udp_router_probe"
    )
    tmp_rel = tmp.relative_to(root)
    quoted_tf_yaml = shlex.quote(dynamic_tf_message_yaml())
    quoted_map_yaml = shlex.quote(occupancy_grid_yaml())
    quoted_odom_yaml = shlex.quote(odometry_yaml())
    quoted_goal_yaml = shlex.quote(
        navigate_to_pose_goal_yaml(
            bt_xml_in_container,
            goal_x=goal_x,
            goal_y=goal_y,
        )
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
            ]
        )
        if build.returncode != 0:
            return {
                "schema_version": schema_version,
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
            ">/tmp/fleetrmw_nav2_navigate_to_pose_install.log; "
            "fi; source /opt/ros/jazzy/setup.bash; ",
            f"source /work/{install_base.relative_to(root)}/setup.bash; ",
            f"{router_exe} --bind 127.0.0.1:{router_port} "
            "--expected-frames 1 "
            f"--expected-service-frames {expected_service_frames} "
            "--expected-graph-advertisements 4 --timeout-ms 120000 "
            "--post-satisfaction-ms 1000 "
            f"> /work/{tmp_rel}/router.log 2>&1 & router_pid=$!; ",
            "sleep 0.5; export RMW_IMPLEMENTATION=rmw_fleetqox_cpp; ",
            f"FLEETQOX_RMW_BIND=127.0.0.1:{planner_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            f"ros2 run nav2_planner planner_server --ros-args --params-file "
            f"/work/{params.relative_to(root)} "
            f"> /work/{tmp_rel}/planner.log 2>&1 & planner_pid=$!; ",
            f"FLEETQOX_RMW_BIND=127.0.0.1:{controller_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            f"ros2 run nav2_controller controller_server --ros-args --params-file "
            f"/work/{params.relative_to(root)} "
            f"> /work/{tmp_rel}/controller.log 2>&1 & controller_pid=$!; ",
            f"FLEETQOX_RMW_BIND=127.0.0.1:{bt_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            f"ros2 run nav2_bt_navigator bt_navigator --ros-args --params-file "
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

        if moving_base:
            parts.extend(
                [
                    f"FLEETQOX_RMW_BIND=127.0.0.1:{map_port} "
                    f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
                    "timeout 90 ros2 topic pub --rate 2 /map nav_msgs/msg/OccupancyGrid "
                    f"{quoted_map_yaml} > /work/{tmp_rel}/map_pub.log 2>&1 & map_pid=$!; ",
                    f"FLEETQOX_RMW_BIND=127.0.0.1:{odom_port} "
                    f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
                    f"FLEETQOX_FAKE_BASE_GOAL_X={goal_x} "
                    f"FLEETQOX_FAKE_BASE_METRICS=/work/{tmp_rel}/fake_base_metrics.json "
                    f"timeout 90 python3 /work/{fake_base.relative_to(root)} "
                    f"> /work/{tmp_rel}/fake_base.log 2>&1 & fake_base_pid=$!; "
                    "tf_pid=$fake_base_pid; odom_pid=$fake_base_pid; ",
                    "sleep 3; ",
                ]
            )
        else:
            parts.extend(
                [
                    f"FLEETQOX_RMW_BIND=127.0.0.1:{tf_port} "
                    f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
                    "timeout 70 ros2 topic pub --rate 10 /tf tf2_msgs/msg/TFMessage "
                    f"{quoted_tf_yaml} > /work/{tmp_rel}/tf_pub.log 2>&1 & tf_pid=$!; ",
                    f"FLEETQOX_RMW_BIND=127.0.0.1:{map_port} "
                    f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
                    "timeout 70 ros2 topic pub --rate 2 /map nav_msgs/msg/OccupancyGrid "
                    f"{quoted_map_yaml} > /work/{tmp_rel}/map_pub.log 2>&1 & map_pid=$!; ",
                    f"FLEETQOX_RMW_BIND=127.0.0.1:{odom_port} "
                    f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
                    "timeout 70 ros2 topic pub --rate 10 /odom nav_msgs/msg/Odometry "
                    f"{quoted_odom_yaml} > /work/{tmp_rel}/odom_pub.log 2>&1 & odom_pid=$!; ",
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
                f"> /work/{tmp_rel}/navigate_to_pose_goal.log 2>&1; "
                "navigate_to_pose_goal_rc=$?; ",
                "kill ${planner_pid} ${controller_pid} ${bt_pid} "
                "${tf_pid} ${map_pid} ${odom_pid} >/dev/null 2>&1 || true; ",
                "wait ${planner_pid} >/dev/null 2>&1 || true; ",
                "wait ${controller_pid} >/dev/null 2>&1 || true; ",
                "wait ${bt_pid} >/dev/null 2>&1 || true; ",
                "wait ${tf_pid} >/dev/null 2>&1 || true; ",
                "wait ${map_pid} >/dev/null 2>&1 || true; ",
                "wait ${odom_pid} >/dev/null 2>&1 || true; ",
                "wait ${router_pid} >/dev/null 2>&1; router_rc=$?; ",
                f"printf 'navigate_to_pose_goal=%s\\nrouter=%s\\n' "
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

        planner_log = read("planner.log")
        controller_log = read("controller.log")
        bt_log = read("bt.log")
        fake_base_log = read("fake_base.log")
        fake_base_metrics = read_json("fake_base_metrics.json")
        router_log = read("router.log")
        router_summary = parse_last_json(router_log)
        metrics = router_metrics(router_summary)
        forwarded_topics = router_summary.get("forwarded_topics", [])
        graph_topics = router_summary.get("graph_topics", [])
        navigate_goal = read("navigate_to_pose_goal.log")
        navigate = parse_navigate_to_pose_output(navigate_goal)
        planner_active = read("planner_server_get_active.log")
        controller_active = read("controller_server_get_active.log")
        bt_active = read("bt_navigator_get_active.log")
        planner_activated = "active [3]" in planner_active and "Activating plugin GridBased" in planner_log
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
        moving_navigation_ok = (
            moving_base
            and cmd_vel_runtime_ok
            and fake_base_cmd_vel_count > 0
            and fake_base_moved_distance >= 0.20
        )
        navigate_ok = (
            bool(navigate["accepted"])
            and bool(navigate["succeeded"])
            and navigate["error_code"] == 0
            and "Goal succeeded" in bt_log
            and "Reached the goal!" in controller_log
        )
        if moving_base:
            navigate_ok = navigate_ok and moving_navigation_ok
        ok = (
            docker.returncode == 0
            and planner_activated
            and controller_activated
            and bt_activated
            and lifecycle_transport_ok
            and map_runtime_ok
            and tf_runtime_ok
            and odom_runtime_ok
            and navigate_ok
        )
        return {
            "schema_version": schema_version,
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
            "planner_final_state": "active" if "active [3]" in planner_active else "unknown",
            "controller_final_state": "active" if "active [3]" in controller_active else "unknown",
            "bt_navigator_final_state": "active" if "active [3]" in bt_active else "unknown",
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
            "fake_base_cmd_vel_count": fake_base_cmd_vel_count,
            "fake_base_max_abs_cmd_x": fake_base_metrics.get("max_abs_cmd_x", 0.0),
            "fake_base_final_x": fake_base_metrics.get("final_x", 0.0),
            "fake_base_max_x": fake_base_metrics.get("max_x", 0.0),
            "fake_base_moved_distance": fake_base_moved_distance,
            "navigation_goal_x": goal_x,
            "navigation_goal_y": goal_y,
            "navigate_to_pose_action": True,
            "navigate_to_pose_goal_accepted": navigate["accepted"],
            "navigate_to_pose_goal_succeeded": navigate["succeeded"],
            "navigate_to_pose_error_code": navigate["error_code"],
            "navigate_to_pose_goal_scope": (
                "moving_base_minimal_bt"
                if moving_base else
                "current_pose_minimal_bt_no_motion"
            ),
            "compute_path_status_forwarded": "/compute_path_to_pose/_action/status" in forwarded_topics,
            "follow_path_feedback_forwarded": "/follow_path/_action/feedback" in forwarded_topics,
            "navigate_to_pose_status_forwarded": "/navigate_to_pose/_action/status" in forwarded_topics,
            "lifecycle_transport": lifecycle_transport_ok,
            "expected_service_frames": expected_service_frames,
            "fleetqox_router_service_frames": metrics["service_frames"],
            "fleetqox_router_service_forwarded": metrics["service_forwarded"],
            "fleetqox_router_received_frames": metrics["received_frames"],
            "fleetqox_router_forwarded_frames": metrics["forwarded_frames"],
            "planner_action_execution_claim": True,
            "controller_execution_claim": True,
            "navigate_to_pose_execution_claim": True,
            "navigate_to_pose_execution_scope": (
                "moving_base_minimal_bt_pipeline"
                if moving_base else
                "same_pose_minimal_bt_pipeline"
            ),
            "full_nav2_navigation_stack_claim": True,
            "full_nav2_navigation_stack_scope": (
                "ci_light_moving_base_nav2_bt_pipeline"
                if moving_base else
                "ci_light_same_pose_nav2_bt_pipeline_no_motion"
            ),
            "moving_robot_navigation_claim": bool(moving_navigation_ok),
            "recovery_behavior_claim": False,
            "long_navigation_workload_claim": False,
            "docker_returncode": docker.returncode,
            "docker_stdout": docker.stdout,
            "docker_stderr": docker.stderr,
            "return_codes": read("return_codes.log"),
            "planner_get_active": planner_active,
            "controller_get_active": controller_active,
            "bt_navigator_get_active": bt_active,
            "navigate_to_pose_goal_excerpt": navigate_goal[-5000:],
            "planner_log_excerpt": planner_log[-3000:],
            "controller_log_excerpt": controller_log[-3000:],
            "bt_log_excerpt": bt_log[-5000:],
            "fake_base_metrics": fake_base_metrics,
            "fake_base_log_excerpt": fake_base_log[-3000:],
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
    parser.add_argument("--port-base", type=int, default=5000)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_nav2_navigate_to_pose_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_probe(root=ROOT, image=args.image, port_base=args.port_base)
    output = ROOT / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(
            f"status={summary['status']} navigate_to_pose={summary.get('navigate_to_pose_goal_succeeded')} "
            f"error_code={summary.get('navigate_to_pose_error_code')}"
        )
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
