#!/usr/bin/env python3
"""Exercise a moving Nav2 goal against a dynamic local-costmap obstacle.

The Docker/netem probe runs real Nav2 planner, controller, and BT navigator
processes through FleetRMW. A persistent ROS process acts as the fake base,
LaserScan source, local-costmap observer, clear-service client, and
NavigateToPose action client. It executes a persistent-obstacle negative
control followed by a remove-clear-resume positive case.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
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
    minimal_navigate_to_pose_bt_xml,
)
from scripts.run_rmw_docker_nav2_planner_controller_activation_probe import (  # noqa: E402
    router_metrics,
)
from scripts.run_rmw_docker_nav2_planner_controller_lifecycle_probe import (  # noqa: E402
    nav2_params_yaml,
)
from scripts.run_rmw_docker_router_service_call_probe import parse_last_json  # noqa: E402


SCHEMA_VERSION = (
    "fleetrmw.docker_nav2_dynamic_obstacle_navigation_probe.v3"
)


def run(
    command: list[str],
    *,
    timeout: float = 600.0,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=ROOT,
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
            stderr=(
                exc.stderr if isinstance(exc.stderr, str) else ""
            ) + "\nsubprocess timeout\n",
        )


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def dynamic_nav2_params_yaml(bt_xml_path: str) -> str:
    base = nav2_params_yaml().replace(
        "controller_frequency: 1.0",
        "controller_frequency: 10.0\n    failure_tolerance: 5.0",
    )
    for original, replacement in (
        ("required_movement_radius: 0.5", "required_movement_radius: 0.10"),
        ("movement_time_allowance: 10.0", "movement_time_allowance: 30.0"),
        ("vtheta_samples: 20", "vtheta_samples: 60"),
        ("sim_time: 1.7", "sim_time: 5.0"),
        ("BaseObstacle.scale: 0.02", "BaseObstacle.scale: 0.05"),
        ("PathAlign.scale: 32.0", "PathAlign.scale: 1.0"),
        ("GoalAlign.scale: 24.0", "GoalAlign.scale: 4.0"),
        ("PathDist.scale: 32.0", "PathDist.scale: 2.0"),
    ):
        base = base.replace(original, replacement)
    navigator = bt_navigator_params_yaml(bt_xml_path).replace(
        "default_server_timeout: 20",
        "default_server_timeout: 1000",
    )
    return (
        base
        + navigator
        + """local_costmap:
  local_costmap:
    ros__parameters:
      update_frequency: 10.0
      publish_frequency: 10.0
      global_frame: odom
      robot_base_frame: base_link
      rolling_window: true
      width: 3
      height: 3
      resolution: 0.05
      robot_radius: 0.10
      transform_tolerance: 0.5
      track_unknown_space: false
      always_send_full_costmap: true
      plugins: ["obstacle_layer", "inflation_layer"]
      obstacle_layer:
        plugin: "nav2_costmap_2d::ObstacleLayer"
        enabled: true
        observation_sources: scan
        scan:
          topic: /scan
          max_obstacle_height: 2.0
          clearing: false
          marking: true
          data_type: "LaserScan"
          obstacle_max_range: 2.5
          obstacle_min_range: 0.0
      inflation_layer:
        plugin: "nav2_costmap_2d::InflationLayer"
        enabled: true
        inflation_radius: 0.25
        cost_scaling_factor: 3.0
"""
    )


def dynamic_navigate_to_pose_bt_xml() -> str:
    return """<root BTCPP_format="4" main_tree_to_execute="MainTree">
  <BehaviorTree ID="MainTree">
    <PipelineSequence name="NavigateWithPeriodicReplanning">
      <ControllerSelector selected_controller="{selected_controller}" default_controller="FollowPath" topic_name="controller_selector"/>
      <PlannerSelector selected_planner="{selected_planner}" default_planner="GridBased" topic_name="planner_selector"/>
      <RateController hz="2.0">
        <ComputePathToPose goal="{goal}" path="{path}" planner_id="{selected_planner}" error_code_id="{compute_path_error_code}"/>
      </RateController>
      <FollowPath path="{path}" controller_id="{selected_controller}" error_code_id="{follow_path_error_code}"/>
    </PipelineSequence>
  </BehaviorTree>
</root>
"""


def scenario_node_py() -> str:
    return r'''#!/usr/bin/env python3
import gc
import json
import math
import os
import time

import rclpy
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import TransformStamped, Twist
from lifecycle_msgs.msg import Transition
from lifecycle_msgs.srv import ChangeState
from nav2_msgs.action import NavigateToPose
from nav2_msgs.msg import Costmap
from nav2_msgs.srv import ClearEntireCostmap
from nav_msgs.msg import OccupancyGrid, Odometry, Path
from rclpy.action import ActionClient
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import LaserScan
from tf2_msgs.msg import TFMessage


class DynamicObstacleScenario(Node):
    def __init__(self):
        super().__init__("fleetrmw_dynamic_obstacle_navigation")
        self.output = os.environ["FLEETQOX_DYNAMIC_NAV_OUTPUT"]
        self.bt_xml = os.environ["FLEETQOX_DYNAMIC_NAV_BT_XML"]
        self.replan_bt_xml = os.environ[
            "FLEETQOX_DYNAMIC_NAV_REPLAN_BT_XML"
        ]
        self.goal_x = float(os.environ.get("FLEETQOX_DYNAMIC_NAV_GOAL_X", "1.2"))
        self.x = 0.0
        self.y = 0.0
        self.theta = 0.0
        self.cmd_x = 0.0
        self.cmd_theta = 0.0
        self.cmd_vel_count = 0
        self.nonzero_cmd_count = 0
        self.max_abs_cmd_x = 0.0
        self.last_tick = time.monotonic()
        self.last_nonzero_cmd_at = self.last_tick
        self.obstacle_enabled = False
        self.obstacle_mode = "wall"
        self.obstacle_x = 0.0
        self.obstacle_y = 0.0
        self.obstacle_radius = 0.10
        self.detour_reference_y = 0.0
        self.detour_tracking = False
        self.detour_min_center_distance = float("inf")
        self.max_detour_lateral_excursion = 0.0
        self.max_detour_heading_excursion = 0.0
        self.replan_reference_y = 0.0
        self.replan_tracking = False
        self.max_replan_lateral_excursion = 0.0
        self.path_length = 0.0
        self.scan_count = 0
        self.tf_count = 0
        self.odom_count = 0
        self.map_count = 0
        self.last_map_at = 0.0
        self.map_obstacle_enabled = False
        self.map_obstacle_x = 0.0
        self.map_obstacle_y = 0.0
        self.map_obstacle_half_width = 0.15
        self.map_obstacle_half_height = 0.45
        self.map_obstacle_publish_count = 0
        self.plan_count = 0
        self.plan_count_after_map_obstacle = 0
        self.last_plan_max_abs_y = 0.0
        self.max_replanned_path_abs_y = 0.0
        self.costmap_samples = 0
        self.current_costmap_max = 0
        self.max_cost_observed = 0
        self.lethal_costmap_samples = 0
        self.clear_call_count = 0
        self.action = ActionClient(self, NavigateToPose, "/navigate_to_pose")
        self.clear = self.create_client(
            ClearEntireCostmap,
            "/local_costmap/clear_entirely_local_costmap",
        )
        self.lifecycle = {
            name: self.create_client(
                ChangeState,
                f"/{name}/change_state",
            )
            for name in (
                "planner_server",
                "controller_server",
                "bt_navigator",
            )
        }
        self.scan_pub = self.create_publisher(LaserScan, "/scan", 10)
        self.odom_pub = self.create_publisher(Odometry, "/odom", 10)
        self.tf_pub = self.create_publisher(TFMessage, "/tf", 10)
        map_qos = QoSProfile(
            depth=1,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE,
        )
        self.map_pub = self.create_publisher(
            OccupancyGrid,
            "/map",
            map_qos,
        )
        self.cmd_subscription = self.create_subscription(
            Twist, "/cmd_vel", self.on_cmd_vel, 10)
        self.costmap_subscription = self.create_subscription(
            Costmap,
            "/local_costmap/costmap_raw",
            self.on_costmap,
            10,
        )
        self.plan_subscription = self.create_subscription(
            Path,
            "/plan",
            self.on_plan,
            10,
        )
        self.create_timer(0.05, self.tick)

    def on_cmd_vel(self, message):
        self.cmd_x = max(min(float(message.linear.x), 0.35), -0.35)
        self.cmd_theta = max(min(float(message.angular.z), 1.5), -1.5)
        self.cmd_vel_count += 1
        self.max_abs_cmd_x = max(self.max_abs_cmd_x, abs(self.cmd_x))
        if abs(self.cmd_x) > 0.02 or abs(self.cmd_theta) > 0.05:
            self.nonzero_cmd_count += 1
            self.last_nonzero_cmd_at = time.monotonic()

    def on_costmap(self, message):
        values = list(message.data)
        self.costmap_samples += 1
        self.current_costmap_max = max(values, default=0)
        self.max_cost_observed = max(
            self.max_cost_observed,
            self.current_costmap_max,
        )
        if self.current_costmap_max >= 253:
            self.lethal_costmap_samples += 1

    def on_plan(self, message):
        self.plan_count += 1
        self.last_plan_max_abs_y = max(
            (abs(float(pose.pose.position.y)) for pose in message.poses),
            default=0.0,
        )
        if self.map_obstacle_enabled:
            self.plan_count_after_map_obstacle += 1
            self.max_replanned_path_abs_y = max(
                self.max_replanned_path_abs_y,
                self.last_plan_max_abs_y,
            )

    def tick(self):
        now_monotonic = time.monotonic()
        dt = max(0.0, min(now_monotonic - self.last_tick, 0.2))
        self.last_tick = now_monotonic
        self.theta += self.cmd_theta * dt
        dx = math.cos(self.theta) * self.cmd_x * dt
        dy = math.sin(self.theta) * self.cmd_x * dt
        self.x += dx
        self.y += dy
        self.path_length += math.hypot(dx, dy)
        self.x = max(-0.25, min(self.x, 6.25))
        self.y = max(-2.0, min(self.y, 2.0))
        if self.detour_tracking:
            self.max_detour_lateral_excursion = max(
                self.max_detour_lateral_excursion,
                abs(self.y - self.detour_reference_y),
            )
            self.max_detour_heading_excursion = max(
                self.max_detour_heading_excursion,
                abs(self.theta),
            )
            if self.obstacle_enabled and self.obstacle_mode == "circle":
                self.detour_min_center_distance = min(
                    self.detour_min_center_distance,
                    math.hypot(
                        self.x - self.obstacle_x,
                        self.y - self.obstacle_y,
                    ),
                )
        if self.replan_tracking:
            self.max_replan_lateral_excursion = max(
                self.max_replan_lateral_excursion,
                abs(self.y - self.replan_reference_y),
            )

        now = self.get_clock().now().to_msg()
        odom = Odometry()
        odom.header.stamp = now
        odom.header.frame_id = "odom"
        odom.child_frame_id = "base_link"
        odom.pose.pose.position.x = self.x
        odom.pose.pose.position.y = self.y
        odom.pose.pose.orientation.z = math.sin(self.theta / 2.0)
        odom.pose.pose.orientation.w = math.cos(self.theta / 2.0)
        odom.twist.twist.linear.x = self.cmd_x
        odom.twist.twist.angular.z = self.cmd_theta
        self.odom_pub.publish(odom)
        self.odom_count += 1

        map_to_odom = TransformStamped()
        map_to_odom.header.stamp = now
        map_to_odom.header.frame_id = "map"
        map_to_odom.child_frame_id = "odom"
        map_to_odom.transform.rotation.w = 1.0
        odom_to_base = TransformStamped()
        odom_to_base.header.stamp = now
        odom_to_base.header.frame_id = "odom"
        odom_to_base.child_frame_id = "base_link"
        odom_to_base.transform.translation.x = self.x
        odom_to_base.transform.translation.y = self.y
        odom_to_base.transform.rotation.z = math.sin(self.theta / 2.0)
        odom_to_base.transform.rotation.w = math.cos(self.theta / 2.0)
        self.tf_pub.publish(TFMessage(
            transforms=[map_to_odom, odom_to_base],
        ))
        self.tf_count += 1

        if now_monotonic - self.last_map_at >= 0.25:
            self.last_map_at = now_monotonic
            grid = OccupancyGrid()
            grid.header.stamp = now
            grid.header.frame_id = "map"
            grid.info.map_load_time = now
            grid.info.resolution = 0.1
            grid.info.width = 80
            grid.info.height = 60
            grid.info.origin.position.x = -1.5
            grid.info.origin.position.y = -3.0
            grid.info.origin.orientation.w = 1.0
            values = [0] * (
                grid.info.width * grid.info.height
            )
            if self.map_obstacle_enabled:
                self.map_obstacle_publish_count += 1
                for row in range(grid.info.height):
                    world_y = (
                        grid.info.origin.position.y
                        + (row + 0.5) * grid.info.resolution
                    )
                    if (
                        abs(world_y - self.map_obstacle_y)
                        > self.map_obstacle_half_height
                    ):
                        continue
                    for column in range(grid.info.width):
                        world_x = (
                            grid.info.origin.position.x
                            + (column + 0.5) * grid.info.resolution
                        )
                        if (
                            abs(world_x - self.map_obstacle_x)
                            <= self.map_obstacle_half_width
                        ):
                            values[row * grid.info.width + column] = 100
            grid.data = values
            self.map_pub.publish(grid)
            self.map_count += 1

        scan = LaserScan()
        scan.header.stamp = now
        scan.header.frame_id = "base_link"
        scan.angle_min = -1.20
        scan.angle_max = 1.20
        scan.angle_increment = 0.10
        scan.scan_time = 0.05
        scan.range_min = 0.05
        scan.range_max = 3.0
        if not self.obstacle_enabled:
            scan.ranges = [float("inf")] * 25
        elif self.obstacle_mode == "wall":
            scan.ranges = [0.20] * 25
        else:
            ranges = []
            relative_x = self.obstacle_x - self.x
            relative_y = self.obstacle_y - self.y
            center_distance_sq = (
                relative_x * relative_x + relative_y * relative_y
            )
            for index in range(25):
                beam = scan.angle_min + index * scan.angle_increment
                direction = self.theta + beam
                projection = (
                    relative_x * math.cos(direction)
                    + relative_y * math.sin(direction)
                )
                perpendicular_sq = max(
                    0.0,
                    center_distance_sq - projection * projection,
                )
                if (
                    projection > 0.0
                    and perpendicular_sq <= self.obstacle_radius ** 2
                ):
                    near = projection - math.sqrt(
                        max(
                            0.0,
                            self.obstacle_radius ** 2
                            - perpendicular_sq,
                        )
                    )
                    ranges.append(
                        near if near >= scan.range_min else scan.range_min
                    )
                else:
                    ranges.append(float("inf"))
            scan.ranges = ranges
        self.scan_pub.publish(scan)
        self.scan_count += 1

    def spin_until(self, predicate, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.scenario_executor.spin_once(timeout_sec=0.05)
            if predicate():
                return True
        return bool(predicate())

    def wait_future(self, future, timeout):
        return self.spin_until(future.done, timeout)

    def send_goal(
        self,
        goal_x=None,
        goal_y=0.0,
        behavior_tree=None,
    ):
        goal = NavigateToPose.Goal()
        goal.pose.header.frame_id = "map"
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        goal.pose.pose.position.x = (
            self.goal_x if goal_x is None else float(goal_x)
        )
        goal.pose.pose.position.y = float(goal_y)
        goal.pose.pose.orientation.w = 1.0
        goal.behavior_tree = (
            self.bt_xml if behavior_tree is None else behavior_tree
        )
        future = self.action.send_goal_async(goal)
        if not self.wait_future(future, 10.0):
            return None
        return future.result()

    def clear_costmap(self):
        self.clear_call_count += 1
        future = self.clear.call_async(ClearEntireCostmap.Request())
        return self.wait_future(future, 8.0) and future.result() is not None

    def change_state(self, name, transition_id):
        client = self.lifecycle[name]
        if not self.spin_until(client.service_is_ready, 20.0):
            return False
        self.spin_until(lambda: False, 0.25)
        for _attempt in range(3):
            request = ChangeState.Request()
            request.transition.id = transition_id
            future = client.call_async(request)
            if (
                self.wait_future(future, 20.0)
                and future.result() is not None
                and bool(future.result().success)
            ):
                return True
            self.spin_until(lambda: False, 0.5)
        return False

    def wait_for_advance(self, start_x, distance=0.10):
        return self.spin_until(lambda: self.x >= start_x + distance, 10.0)

    def wait_for_obstacle_block(self):
        marked = self.spin_until(
            lambda: self.current_costmap_max >= 253,
            8.0,
        )
        block_start_x = self.x
        self.spin_until(
            lambda: False,
            1.5,
        )
        progress_delta = abs(self.x - block_start_x)
        stopped = marked and (
            (
                abs(self.cmd_x) <= 0.02
                and time.monotonic() - self.last_nonzero_cmd_at >= 0.5
            )
            or progress_delta < 0.08
        )
        return marked, stopped, progress_delta

    def run_scenario(self):
        started = time.monotonic()
        configure = {
            name: self.change_state(
                name,
                Transition.TRANSITION_CONFIGURE,
            )
            for name in (
                "planner_server",
                "controller_server",
                "bt_navigator",
            )
        }
        activate = {
            name: self.change_state(
                name,
                Transition.TRANSITION_ACTIVATE,
            )
            for name in (
                "planner_server",
                "controller_server",
                "bt_navigator",
            )
        }
        both_ready = self.spin_until(
            lambda: (
                self.action.server_is_ready()
                and self.clear.service_is_ready()
            ),
            60.0,
        )
        action_ready = both_ready and self.action.server_is_ready()
        clear_ready = both_ready and self.clear.service_is_ready()
        metrics = {
            "lifecycle_configure": configure,
            "lifecycle_activate": activate,
            "lifecycle_configure_ok": all(configure.values()),
            "lifecycle_activate_ok": all(activate.values()),
            "action_server_ready": action_ready,
            "clear_service_ready": clear_ready,
        }
        if (
            not all(configure.values())
            or not all(activate.values())
            or not action_ready
            or not clear_ready
        ):
            metrics["status"] = "failed"
            return metrics

        negative_start_x = self.x
        negative_goal = self.send_goal()
        negative_accepted = bool(
            negative_goal is not None and negative_goal.accepted
        )
        negative_result_future = (
            negative_goal.get_result_async() if negative_accepted else None
        )
        negative_advanced = (
            self.wait_for_advance(negative_start_x) if negative_accepted
            else False
        )
        self.obstacle_enabled = True
        (
            negative_marked,
            negative_stopped,
            negative_block_progress_delta,
        ) = self.wait_for_obstacle_block()
        persistent_clear_x = self.x
        persistent_clear_response = (
            self.clear_costmap() if negative_marked else False
        )
        cleared_moment_observed = self.spin_until(
            lambda: self.current_costmap_max == 0,
            3.0,
        )
        persistent_remarked = self.spin_until(
            lambda: self.current_costmap_max >= 253,
            4.0,
        )
        persistent_observe_start_x = self.x
        self.spin_until(lambda: False, 2.0)
        persistent_progress_delta = abs(
            self.x - persistent_observe_start_x
        )
        persistent_no_progress = persistent_progress_delta < 0.05
        negative_cancel_requested = False
        negative_cancel_accepted = False
        negative_status = None
        if negative_accepted:
            cancel_future = negative_goal.cancel_goal_async()
            negative_cancel_requested = True
            if self.wait_future(cancel_future, 8.0):
                response = cancel_future.result()
                negative_cancel_accepted = bool(response.goals_canceling)
            if (
                negative_result_future is not None
                and self.wait_future(negative_result_future, 8.0)
            ):
                negative_status = negative_result_future.result().status

        self.obstacle_enabled = False
        self.spin_until(lambda: False, 1.5)
        post_negative_clear_response = self.clear_costmap()
        post_negative_clear_observed = self.spin_until(
            lambda: self.current_costmap_max == 0,
            4.0,
        )
        self.spin_until(lambda: False, 0.5)

        recovery_start_x = self.x
        recovery_goal = self.send_goal()
        recovery_accepted = bool(
            recovery_goal is not None and recovery_goal.accepted
        )
        recovery_result_future = (
            recovery_goal.get_result_async() if recovery_accepted else None
        )
        recovery_advanced_before_obstacle = (
            self.wait_for_advance(recovery_start_x)
            if recovery_accepted else False
        )
        self.obstacle_enabled = True
        (
            recovery_marked,
            recovery_stopped,
            recovery_block_progress_delta,
        ) = self.wait_for_obstacle_block()
        recovery_blocked_x = self.x
        self.obstacle_enabled = False
        self.spin_until(lambda: False, 1.0)
        recovery_clear_response = (
            self.clear_costmap() if recovery_marked else False
        )
        recovery_clear_observed = self.spin_until(
            lambda: self.current_costmap_max == 0,
            4.0,
        )
        recovery_resumed = self.spin_until(
            lambda: (
                self.x >= recovery_blocked_x + 0.10
                and self.nonzero_cmd_count > 0
            ),
            12.0,
        )
        recovery_status = None
        if (
            recovery_result_future is not None
            and self.wait_future(recovery_result_future, 30.0)
        ):
            recovery_status = recovery_result_future.result().status
        recovery_succeeded = (
            recovery_status == GoalStatus.STATUS_SUCCEEDED
        )

        self.obstacle_enabled = False
        self.obstacle_mode = "circle"
        self.spin_until(lambda: False, 1.0)
        detour_start_x = self.x
        detour_start_y = self.y
        detour_goal_x = max(self.goal_x + 1.60, self.x + 1.80)
        detour_goal = self.send_goal(detour_goal_x, 0.0)
        detour_accepted = bool(
            detour_goal is not None and detour_goal.accepted
        )
        detour_result_future = (
            detour_goal.get_result_async() if detour_accepted else None
        )
        detour_advanced_before_obstacle = (
            self.wait_for_advance(detour_start_x)
            if detour_accepted else False
        )
        self.obstacle_x = self.x + 0.95
        self.obstacle_y = detour_start_y + 0.15
        self.obstacle_radius = 0.15
        self.detour_reference_y = detour_start_y
        self.detour_min_center_distance = float("inf")
        self.max_detour_lateral_excursion = 0.0
        self.max_detour_heading_excursion = 0.0
        self.detour_tracking = True
        self.obstacle_enabled = True
        detour_obstacle_marked = self.spin_until(
            lambda: self.current_costmap_max >= 253,
            8.0,
        )
        detour_status = None
        if (
            detour_result_future is not None
            and self.wait_future(detour_result_future, 45.0)
        ):
            detour_status = detour_result_future.result().status
        self.detour_tracking = False
        detour_goal_succeeded = (
            detour_status == GoalStatus.STATUS_SUCCEEDED
        )
        detour_lateral_excursion = self.max_detour_lateral_excursion
        detour_min_center_distance = (
            self.detour_min_center_distance
            if math.isfinite(self.detour_min_center_distance)
            else None
        )
        detour_obstacle_clearance = (
            detour_min_center_distance - self.obstacle_radius
            if detour_min_center_distance is not None
            else None
        )
        detour_passed_obstacle = (
            self.x >= self.obstacle_x + self.obstacle_radius + 0.12
        )
        detour_goal_distance = math.hypot(
            self.x - detour_goal_x,
            self.y,
        )
        detour_obstacle_persistent = self.obstacle_enabled
        detour_ok = all((
            detour_accepted,
            detour_advanced_before_obstacle,
            detour_obstacle_marked,
            detour_goal_succeeded,
            detour_passed_obstacle,
            detour_lateral_excursion >= 0.12,
            detour_obstacle_clearance is not None
                and detour_obstacle_clearance >= 0.10,
            detour_goal_distance <= 0.26,
            detour_obstacle_persistent,
        ))

        self.obstacle_enabled = False
        self.spin_until(lambda: False, 1.0)
        pre_replan_clear_response = self.clear_costmap()
        pre_replan_clear_observed = self.spin_until(
            lambda: self.current_costmap_max == 0,
            4.0,
        )
        replan_start_x = self.x
        replan_start_y = self.y
        replan_goal_x = max(detour_goal_x + 3.30, self.x + 3.60)
        replan_goal = self.send_goal(
            replan_goal_x,
            0.0,
            self.replan_bt_xml,
        )
        replan_accepted = bool(
            replan_goal is not None and replan_goal.accepted
        )
        replan_result_future = (
            replan_goal.get_result_async() if replan_accepted else None
        )
        replan_advanced_before_map_update = (
            self.wait_for_advance(replan_start_x)
            if replan_accepted else False
        )
        initial_plan_count = self.plan_count
        self.map_obstacle_x = self.x + 1.80
        self.map_obstacle_y = 0.0
        self.plan_count_after_map_obstacle = 0
        self.max_replanned_path_abs_y = 0.0
        self.replan_reference_y = replan_start_y
        self.max_replan_lateral_excursion = 0.0
        self.replan_tracking = True
        self.map_obstacle_enabled = True
        global_replan_observed = self.spin_until(
            lambda: (
                self.plan_count_after_map_obstacle >= 1
                and self.max_replanned_path_abs_y >= 0.35
            ),
            15.0,
        )
        replan_status = None
        if (
            replan_result_future is not None
            and self.wait_future(replan_result_future, 60.0)
        ):
            replan_status = replan_result_future.result().status
        self.replan_tracking = False
        replan_goal_succeeded = (
            replan_status == GoalStatus.STATUS_SUCCEEDED
        )
        replan_goal_distance = math.hypot(
            self.x - replan_goal_x,
            self.y,
        )
        replan_passed_map_obstacle = (
            self.x
            >= (
                self.map_obstacle_x
                + self.map_obstacle_half_width
                + 0.12
            )
        )
        map_obstacle_persistent = self.map_obstacle_enabled
        global_replan_ok = all((
            replan_accepted,
            pre_replan_clear_response,
            pre_replan_clear_observed,
            replan_advanced_before_map_update,
            global_replan_observed,
            self.plan_count > initial_plan_count,
            self.plan_count_after_map_obstacle >= 1,
            self.map_obstacle_publish_count >= 1,
            self.max_replanned_path_abs_y >= 0.35,
            self.max_replan_lateral_excursion >= 0.25,
            replan_passed_map_obstacle,
            replan_goal_succeeded,
            replan_goal_distance <= 0.26,
            map_obstacle_persistent,
            not self.obstacle_enabled,
        ))

        negative_terminal_safe = negative_status in (
            GoalStatus.STATUS_CANCELED,
            GoalStatus.STATUS_ABORTED,
        )
        negative_control_ok = all((
            negative_accepted,
            negative_advanced,
            negative_marked,
            negative_stopped,
            persistent_clear_response,
            persistent_remarked,
            persistent_no_progress,
            negative_cancel_requested,
            negative_terminal_safe,
            post_negative_clear_response,
            post_negative_clear_observed,
        ))
        recovery_ok = all((
            recovery_accepted,
            recovery_advanced_before_obstacle,
            recovery_marked,
            recovery_stopped,
            recovery_clear_response,
            recovery_clear_observed,
            recovery_resumed,
            recovery_succeeded,
        ))
        metrics.update({
            "status": (
                "ok"
                if (
                    negative_control_ok
                    and recovery_ok
                    and detour_ok
                    and global_replan_ok
                )
                else "failed"
            ),
            "negative_control_ok": negative_control_ok,
            "negative_goal_accepted": negative_accepted,
            "negative_advanced_before_obstacle": negative_advanced,
            "negative_obstacle_marked": negative_marked,
            "negative_robot_stopped": negative_stopped,
            "negative_block_progress_delta": negative_block_progress_delta,
            "persistent_clear_response": persistent_clear_response,
            "persistent_clear_x": persistent_clear_x,
            "persistent_cleared_moment_observed": cleared_moment_observed,
            "persistent_obstacle_remarked_after_clear": persistent_remarked,
            "persistent_progress_delta_after_clear": persistent_progress_delta,
            "persistent_no_progress_after_clear": persistent_no_progress,
            "negative_cancel_requested": negative_cancel_requested,
            "negative_cancel_accepted": negative_cancel_accepted,
            "negative_result_status": negative_status,
            "negative_terminal_safe": negative_terminal_safe,
            "post_negative_clear_response": post_negative_clear_response,
            "post_negative_clear_observed": post_negative_clear_observed,
            "recovery_case_ok": recovery_ok,
            "recovery_goal_accepted": recovery_accepted,
            "recovery_advanced_before_obstacle":
                recovery_advanced_before_obstacle,
            "recovery_obstacle_marked": recovery_marked,
            "recovery_robot_stopped": recovery_stopped,
            "recovery_block_progress_delta": recovery_block_progress_delta,
            "recovery_clear_response": recovery_clear_response,
            "recovery_clear_observed": recovery_clear_observed,
            "recovery_resumed_after_clear": recovery_resumed,
            "recovery_result_status": recovery_status,
            "recovery_goal_succeeded": recovery_succeeded,
            "detour_case_ok": detour_ok,
            "detour_goal_accepted": detour_accepted,
            "detour_advanced_before_obstacle":
                detour_advanced_before_obstacle,
            "detour_obstacle_marked": detour_obstacle_marked,
            "detour_obstacle_persistent": detour_obstacle_persistent,
            "detour_result_status": detour_status,
            "detour_goal_succeeded": detour_goal_succeeded,
            "detour_passed_obstacle": detour_passed_obstacle,
            "detour_start_x": detour_start_x,
            "detour_start_y": detour_start_y,
            "detour_goal_x": detour_goal_x,
            "detour_obstacle_x": self.obstacle_x,
            "detour_obstacle_y": self.obstacle_y,
            "detour_obstacle_radius": self.obstacle_radius,
            "detour_lateral_excursion": detour_lateral_excursion,
            "detour_heading_excursion":
                self.max_detour_heading_excursion,
            "detour_min_center_distance":
                detour_min_center_distance,
            "detour_obstacle_clearance": detour_obstacle_clearance,
            "detour_goal_distance": detour_goal_distance,
            "global_replan_case_ok": global_replan_ok,
            "global_replan_pre_clear_response":
                pre_replan_clear_response,
            "global_replan_pre_clear_observed":
                pre_replan_clear_observed,
            "global_replan_goal_accepted": replan_accepted,
            "global_replan_advanced_before_map_update":
                replan_advanced_before_map_update,
            "global_replan_observed": global_replan_observed,
            "global_replan_result_status": replan_status,
            "global_replan_goal_succeeded": replan_goal_succeeded,
            "global_replan_passed_map_obstacle":
                replan_passed_map_obstacle,
            "global_replan_goal_distance": replan_goal_distance,
            "global_replan_start_x": replan_start_x,
            "global_replan_start_y": replan_start_y,
            "global_replan_goal_x": replan_goal_x,
            "global_replan_map_obstacle_x": self.map_obstacle_x,
            "global_replan_map_obstacle_y": self.map_obstacle_y,
            "global_replan_map_obstacle_half_width":
                self.map_obstacle_half_width,
            "global_replan_map_obstacle_half_height":
                self.map_obstacle_half_height,
            "global_replan_plan_count_before_map_update":
                initial_plan_count,
            "global_replan_plan_count_after_map_update":
                self.plan_count_after_map_obstacle,
            "global_replan_path_max_abs_y":
                self.max_replanned_path_abs_y,
            "global_replan_robot_lateral_excursion":
                self.max_replan_lateral_excursion,
            "map_obstacle_publish_count":
                self.map_obstacle_publish_count,
            "map_obstacle_persistent": map_obstacle_persistent,
            "global_replan_laserscan_disabled":
                not self.obstacle_enabled,
            "negative_start_x": negative_start_x,
            "recovery_start_x": recovery_start_x,
            "recovery_blocked_x": recovery_blocked_x,
            "final_x": self.x,
            "final_y": self.y,
            "goal_x": self.goal_x,
            "path_length": self.path_length,
            "cmd_vel_count": self.cmd_vel_count,
            "nonzero_cmd_count": self.nonzero_cmd_count,
            "max_abs_cmd_x": self.max_abs_cmd_x,
            "scan_messages_published": self.scan_count,
            "tf_messages_published": self.tf_count,
            "odom_messages_published": self.odom_count,
            "map_messages_published": self.map_count,
            "costmap_samples": self.costmap_samples,
            "max_cost_observed": self.max_cost_observed,
            "lethal_costmap_samples": self.lethal_costmap_samples,
            "clear_call_count": self.clear_call_count,
            "elapsed_s": round(time.monotonic() - started, 3),
        })
        return metrics


rclpy.init()
node = DynamicObstacleScenario()
executor = SingleThreadedExecutor(context=node.context)
executor.add_node(node)
node.scenario_executor = executor
try:
    summary = node.run_scenario()
except Exception as exc:
    summary = {
        "status": "failed",
        "exception": f"{type(exc).__name__}: {exc}",
    }
with open(node.output, "w", encoding="utf-8") as handle:
    json.dump(summary, handle, sort_keys=True)
print(json.dumps(summary, sort_keys=True))
executor.remove_node(node)
executor.shutdown(timeout_sec=5.0)
try:
    action_client = node.action
    action_client.destroy()
    node.action = None
    del action_client
    gc.collect()
except Exception:
    pass
for lifecycle_client in node.lifecycle.values():
    try:
        node.destroy_client(lifecycle_client)
    except Exception:
        pass
try:
    node.destroy_client(node.clear)
except Exception:
    pass
node.destroy_node()
if rclpy.ok():
    rclpy.shutdown()
'''


def runtime_evidence_ok(
    scenario: dict[str, Any],
    router: dict[str, Any],
    *,
    docker_returncode: int,
) -> bool:
    return (
        docker_returncode == 0
        and scenario.get("status") == "ok"
        and scenario.get("lifecycle_configure_ok") is True
        and scenario.get("lifecycle_activate_ok") is True
        and scenario.get("negative_control_ok") is True
        and scenario.get("persistent_obstacle_remarked_after_clear") is True
        and scenario.get("persistent_no_progress_after_clear") is True
        and scenario.get("negative_terminal_safe") is True
        and scenario.get("recovery_case_ok") is True
        and scenario.get("recovery_obstacle_marked") is True
        and scenario.get("recovery_robot_stopped") is True
        and scenario.get("recovery_clear_response") is True
        and scenario.get("recovery_resumed_after_clear") is True
        and scenario.get("recovery_result_status") == 4
        and scenario.get("recovery_goal_succeeded") is True
        and scenario.get("detour_case_ok") is True
        and scenario.get("detour_obstacle_marked") is True
        and scenario.get("detour_obstacle_persistent") is True
        and scenario.get("detour_goal_succeeded") is True
        and scenario.get("detour_passed_obstacle") is True
        and isinstance(
            scenario.get("detour_lateral_excursion"),
            (int, float),
        )
        and float(scenario.get("detour_lateral_excursion", 0.0)) >= 0.12
        and isinstance(
            scenario.get("detour_obstacle_clearance"),
            (int, float),
        )
        and float(scenario.get("detour_obstacle_clearance", 0.0)) >= 0.10
        and isinstance(
            scenario.get("detour_goal_distance"),
            (int, float),
        )
        and float(scenario.get("detour_goal_distance", 1.0)) <= 0.26
        and scenario.get("global_replan_case_ok") is True
        and scenario.get("global_replan_observed") is True
        and scenario.get("global_replan_pre_clear_response") is True
        and scenario.get("global_replan_pre_clear_observed") is True
        and scenario.get("global_replan_result_status") == 4
        and scenario.get("global_replan_goal_succeeded") is True
        and scenario.get("global_replan_passed_map_obstacle") is True
        and scenario.get("map_obstacle_persistent") is True
        and scenario.get("global_replan_laserscan_disabled") is True
        and isinstance(
            scenario.get("global_replan_plan_count_after_map_update"),
            int,
        )
        and scenario.get("global_replan_plan_count_after_map_update", 0) >= 1
        and isinstance(
            scenario.get("map_obstacle_publish_count"),
            int,
        )
        and scenario.get("map_obstacle_publish_count", 0) >= 1
        and isinstance(
            scenario.get("global_replan_path_max_abs_y"),
            (int, float),
        )
        and float(
            scenario.get("global_replan_path_max_abs_y", 0.0)
        ) >= 0.35
        and isinstance(
            scenario.get("global_replan_robot_lateral_excursion"),
            (int, float),
        )
        and float(
            scenario.get("global_replan_robot_lateral_excursion", 0.0)
        ) >= 0.25
        and isinstance(
            scenario.get("global_replan_goal_distance"),
            (int, float),
        )
        and float(
            scenario.get("global_replan_goal_distance", 1.0)
        ) <= 0.26
        and int(scenario.get("clear_call_count", 0)) == 4
        and int(scenario.get("max_cost_observed", 0)) >= 253
        and int(scenario.get("scan_messages_published", 0)) > 0
        and router.get("status") == "ok"
        and int(router.get("service_frames", 0)) > 0
        and int(router.get("invalid_frames", -1)) == 0
        and int(router.get("unrecoverable_loss_notice_frames", 0)) > 0
        and int(router.get("unrecoverable_loss_notice_forwarded", 0)) > 0
    )


def run_probe(
    *,
    root: Path,
    image: str,
    port_base: int,
    goal_x: float,
) -> dict[str, Any]:
    suffix = str(os.getpid())
    tmp = root / f".tmp_fleetrmw_nav2_dynamic_navigation_{suffix}"
    build = root / ".tmp_fleetrmw_nav2_dynamic_navigation_v2_build"
    install = root / ".tmp_fleetrmw_nav2_dynamic_navigation_v2_install"
    log = root / ".tmp_fleetrmw_nav2_dynamic_navigation_v2_log"
    tmp.mkdir(parents=True, exist_ok=True)
    bt_xml = tmp / "minimal_nav_to_pose.xml"
    bt_xml.write_text(minimal_navigate_to_pose_bt_xml(), encoding="utf-8")
    replan_bt_xml = tmp / "periodic_replan_nav_to_pose.xml"
    replan_bt_xml.write_text(
        dynamic_navigate_to_pose_bt_xml(),
        encoding="utf-8",
    )
    scenario_py = tmp / "scenario.py"
    scenario_py.write_text(scenario_node_py(), encoding="utf-8")
    bt_in_container = f"/work/{bt_xml.relative_to(root)}"
    replan_bt_in_container = (
        f"/work/{replan_bt_xml.relative_to(root)}"
    )
    params = tmp / "nav2_dynamic_navigation.yaml"
    params.write_text(
        dynamic_nav2_params_yaml(bt_in_container),
        encoding="utf-8",
    )
    scenario_summary_path = tmp / "scenario-summary.json"

    router_port = port_base
    planner_port = port_base + 1
    controller_port = port_base + 2
    bt_port = port_base + 3
    scenario_port = port_base + 4
    router_exe = (
        f"/work/{install.relative_to(root)}/rmw_fleetqox_cpp/lib/"
        "rmw_fleetqox_cpp/fleetrmw_udp_router_probe"
    )
    tmp_rel = tmp.relative_to(root)
    try:
        compiled = run(
            [
                "docker", "run", "--rm", "--entrypoint", "bash",
                "-v", f"{root}:/work", "-w", "/work", image, "-lc",
                "source /opt/ros/jazzy/setup.bash && "
                f"rm -rf /work/{build.relative_to(root)} "
                f"/work/{install.relative_to(root)} "
                f"/work/{log.relative_to(root)} && "
                f"colcon --log-base /work/{log.relative_to(root)} build "
                "--base-paths ros2_ws/src --packages-select rmw_fleetqox_cpp "
                f"--build-base /work/{build.relative_to(root)} "
                f"--install-base /work/{install.relative_to(root)} "
                "--cmake-args -DCMAKE_BUILD_TYPE=Release",
            ],
            timeout=600.0,
        )
        if compiled.returncode != 0:
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "failed",
                "stage": "build",
                "stdout": compiled.stdout,
                "stderr": compiled.stderr,
            }

        parts: list[str] = [
            "set -e; source /opt/ros/jazzy/setup.bash; ",
            "if ! ros2 pkg prefix nav2_bt_navigator >/dev/null 2>&1 || "
            "! ros2 pkg prefix nav2_controller >/dev/null 2>&1 || "
            "! ros2 pkg prefix nav2_planner >/dev/null 2>&1; then "
            "apt-get update >/dev/null && "
            "apt-get install -y --no-install-recommends "
            "ros-jazzy-nav2-bt-navigator ros-jazzy-nav2-behavior-tree "
            "ros-jazzy-nav2-planner ros-jazzy-nav2-navfn-planner "
            "ros-jazzy-nav2-controller ros-jazzy-nav2-dwb-controller "
            "ros-jazzy-nav2-msgs ros-jazzy-nav-msgs "
            "ros-jazzy-sensor-msgs ros-jazzy-tf2-msgs "
            ">/tmp/fleetrmw_nav2_dynamic_navigation_install.log; "
            "fi; source /opt/ros/jazzy/setup.bash; ",
            f"source /work/{install.relative_to(root)}/setup.bash; ",
            "export FLEETQOX_RMW_SERVICE_REQUEST_REPEATS=3; "
            "export FLEETQOX_RMW_SERVICE_RESPONSE_REPEATS=3; "
            "export FLEETQOX_RMW_SERVICE_REQUEST_REPEAT_INTERVAL_MS=5; "
            "export FLEETQOX_RMW_SERVICE_RESPONSE_REPEAT_INTERVAL_MS=5; ",
            "stop_pid() { "
            "pid=$1; kill -INT ${pid} >/dev/null 2>&1 || true; "
            "i=0; while kill -0 ${pid} >/dev/null 2>&1 "
            "&& [ ${i} -lt 50 ]; do sleep 0.1; i=$((i + 1)); done; "
            "if kill -0 ${pid} >/dev/null 2>&1; then "
            "kill -9 ${pid} >/dev/null 2>&1 || true; fi; "
            "wait ${pid} >/dev/null 2>&1 || true; }; ",
            "tc qdisc replace dev lo root netem delay 2ms 1ms; ",
            f"{router_exe} --bind 127.0.0.1:{router_port} "
            "--expected-frames 0 --expected-service-frames 50 "
            "--expected-graph-advertisements 4 "
            "--post-satisfaction-ms 2000 --timeout-ms 150000 "
            f">/work/{tmp_rel}/router.log 2>&1 & router_pid=$!; ",
            "sleep 0.5; export RMW_IMPLEMENTATION=rmw_fleetqox_cpp; ",
            f"FLEETQOX_RMW_BIND=127.0.0.1:{planner_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "ros2 run nav2_planner planner_server --ros-args "
            f"--params-file /work/{params.relative_to(root)} "
            f">/work/{tmp_rel}/planner.log 2>&1 & planner_pid=$!; ",
            f"FLEETQOX_RMW_BIND=127.0.0.1:{controller_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "ros2 run nav2_controller controller_server --ros-args "
            f"--params-file /work/{params.relative_to(root)} "
            f">/work/{tmp_rel}/controller.log 2>&1 & controller_pid=$!; ",
            f"FLEETQOX_RMW_BIND=127.0.0.1:{bt_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "ros2 run nav2_bt_navigator bt_navigator --ros-args "
            f"--params-file /work/{params.relative_to(root)} "
            f">/work/{tmp_rel}/bt.log 2>&1 & bt_pid=$!; ",
            "sleep 2; set +e; ",
        ]
        parts.extend([
            f"FLEETQOX_RMW_BIND=127.0.0.1:{scenario_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            f"FLEETQOX_DYNAMIC_NAV_OUTPUT=/work/{scenario_summary_path.relative_to(root)} "
            f"FLEETQOX_DYNAMIC_NAV_BT_XML={bt_in_container} "
            f"FLEETQOX_DYNAMIC_NAV_REPLAN_BT_XML={replan_bt_in_container} "
            f"FLEETQOX_DYNAMIC_NAV_GOAL_X={goal_x} "
            f"timeout 120 python3 /work/{scenario_py.relative_to(root)} "
            f">/work/{tmp_rel}/scenario.log 2>&1 & scenario_pid=$!; ",
            "wait ${scenario_pid}; scenario_rc=$?; ",
            "stop_pid ${planner_pid}; stop_pid ${controller_pid}; "
            "stop_pid ${bt_pid}; ",
            "wait ${router_pid} >/dev/null 2>&1; router_rc=$?; ",
            f"printf 'scenario=%s\\nrouter=%s\\n' "
            "${scenario_rc} ${router_rc} "
            f">/work/{tmp_rel}/return-codes.log; ",
            "if [ ${scenario_rc} -ne 0 ] || [ ${router_rc} -ne 0 ]; then "
            "exit 20; fi; exit 0",
        ])
        docker = run(
            [
                "docker", "run", "--rm", "--cap-add", "NET_ADMIN",
                "--entrypoint", "bash", "-v", f"{root}:/work",
                "-w", "/work", image, "-lc", "".join(parts),
            ],
            timeout=240.0,
        )

        def read(name: str) -> str:
            path = tmp / name
            return path.read_text(errors="replace") if path.exists() else ""

        scenario = read_json(scenario_summary_path)
        router_log = read("router.log")
        router = parse_last_json(router_log)
        metrics = router_metrics(router)
        ok = runtime_evidence_ok(
            scenario,
            router,
            docker_returncode=docker.returncode,
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if ok else "failed",
            "nav2_planner_server_available": True,
            "nav2_controller_server_available": True,
            "nav2_bt_navigator_available": True,
            "local_costmap_obstacle_layer":
                "nav2_costmap_2d::ObstacleLayer",
            "dynamic_laser_scan_runtime": bool(ok),
            "persistent_obstacle_negative_control_claim": bool(ok),
            "persistent_obstacle_remarked_after_clear":
                scenario.get("persistent_obstacle_remarked_after_clear"),
            "navigate_to_pose_dynamic_obstacle_stop_claim": bool(ok),
            "navigate_to_pose_dynamic_obstacle_clear_resume_claim": bool(ok),
            "dynamic_obstacle_detour_avoidance_claim": bool(ok),
            "navigate_to_pose_global_dynamic_replanning_claim": bool(ok),
            "production_costmap_recovery_policy_claim": False,
            "bounded_clear_attempts": 1,
            "docker_loopback_netem": "delay 2ms 1ms",
            "navigation_goal_x": goal_x,
            "negative_result_status":
                scenario.get("negative_result_status"),
            "recovery_result_status":
                scenario.get("recovery_result_status"),
            "recovery_goal_succeeded":
                scenario.get("recovery_goal_succeeded"),
            "detour_result_status":
                scenario.get("detour_result_status"),
            "detour_goal_succeeded":
                scenario.get("detour_goal_succeeded"),
            "detour_lateral_excursion":
                scenario.get("detour_lateral_excursion"),
            "detour_obstacle_clearance":
                scenario.get("detour_obstacle_clearance"),
            "detour_goal_distance":
                scenario.get("detour_goal_distance"),
            "global_replan_result_status":
                scenario.get("global_replan_result_status"),
            "global_replan_goal_succeeded":
                scenario.get("global_replan_goal_succeeded"),
            "global_replan_plan_count_after_map_update":
                scenario.get(
                    "global_replan_plan_count_after_map_update"
                ),
            "global_replan_path_max_abs_y":
                scenario.get("global_replan_path_max_abs_y"),
            "global_replan_robot_lateral_excursion":
                scenario.get("global_replan_robot_lateral_excursion"),
            "global_replan_goal_distance":
                scenario.get("global_replan_goal_distance"),
            "persistent_progress_delta_after_clear":
                scenario.get("persistent_progress_delta_after_clear"),
            "recovery_blocked_x": scenario.get("recovery_blocked_x"),
            "final_x": scenario.get("final_x"),
            "max_cost_observed": scenario.get("max_cost_observed"),
            "clear_call_count": scenario.get("clear_call_count"),
            "fleetqox_router_service_frames": metrics.get("service_frames"),
            "fleetqox_router_invalid_frames": router.get("invalid_frames"),
            "unrecoverable_loss_notice_frames":
                router.get("unrecoverable_loss_notice_frames"),
            "unrecoverable_loss_notice_forwarded":
                router.get("unrecoverable_loss_notice_forwarded"),
            "scenario": scenario,
            "router": metrics,
            "router_raw": router,
            "docker_returncode": docker.returncode,
            "return_codes": read("return-codes.log"),
            "scenario_log": "" if ok else read("scenario.log")[-8000:],
            "planner_log": "" if ok else read("planner.log")[-5000:],
            "controller_log": "" if ok else read("controller.log")[-8000:],
            "bt_log": "" if ok else read("bt.log")[-5000:],
            "router_log": "" if ok else router_log[-5000:],
            "docker_stderr": "" if ok else docker.stderr,
        }
    finally:
        for path in (tmp, build, install, log):
            shutil.rmtree(path, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--port-base", type=int, default=8000)
    parser.add_argument("--goal-x", type=float, default=1.2)
    parser.add_argument(
        "--summary-json",
        default=(
            "results_rmw_socket/"
            "docker_nav2_dynamic_obstacle_navigation_summary.json"
        ),
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
    output.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(summary, sort_keys=True)
        if args.json else
        f"status={summary['status']}"
    )
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
