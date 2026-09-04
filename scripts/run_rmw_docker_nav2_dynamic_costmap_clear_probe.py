#!/usr/bin/env python3
"""Exercise a real Nav2 obstacle layer and clear service through FleetRMW."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "fleetrmw.docker_nav2_dynamic_costmap_clear_probe.v1"
DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"


def run(command: list[str], *, timeout: float = 600.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def read_text(path: Path) -> str:
    return path.read_text(errors="replace") if path.exists() else ""


def parse_last_json(text: str) -> dict[str, Any]:
    for line in reversed(text.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def runtime_evidence_ok(
    client: dict[str, Any],
    router: dict[str, Any],
    *,
    docker_returncode: int,
) -> bool:
    return (
        docker_returncode == 0
        and client.get("status") == "ok"
        and client.get("lifecycle_configure_response") is True
        and client.get("lifecycle_activate_response") is True
        and client.get("dynamic_obstacle_marked") is True
        and client.get("clear_service_requested") is True
        and client.get("clear_service_response") is True
        and client.get("costmap_cleared_after_service") is True
        and int(client.get("max_cost_before_clear", 0)) >= 253
        and int(client.get("occupied_cells_before_clear", 0)) > 0
        and int(client.get("max_cost_after_clear", 255)) == 0
        and int(client.get("occupied_cells_after_clear", -1)) == 0
        and int(client.get("scan_messages_published", 0)) > 0
        and int(client.get("tf_messages_published", 0)) > 0
        and router.get("status") == "ok"
        and int(router.get("graph_advertisements", 0)) >= 3
        and int(router.get("service_frames", 0)) >= 6
        and int(router.get("invalid_frames", -1)) == 0
    )


def params_yaml() -> str:
    return """/**:
  ros__parameters:
    update_frequency: 10.0
    publish_frequency: 10.0
    global_frame: odom
    robot_base_frame: base_link
    rolling_window: true
    width: 4
    height: 4
    resolution: 0.1
    robot_radius: 0.1
    transform_tolerance: 0.5
    track_unknown_space: false
    always_send_full_costmap: true
    plugins: ["obstacle_layer"]
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
"""


def probe_node_py() -> str:
    return r'''import json
import os
import time

import rclpy
from geometry_msgs.msg import TransformStamped
from lifecycle_msgs.msg import Transition
from lifecycle_msgs.srv import ChangeState
from nav2_msgs.msg import Costmap
from nav2_msgs.srv import ClearEntireCostmap
from rclpy.node import Node
from sensor_msgs.msg import LaserScan
from tf2_msgs.msg import TFMessage


class Probe(Node):
    def __init__(self):
        super().__init__("fleetrmw_dynamic_costmap_probe")
        self.output = os.environ["FLEETQOX_COSTMAP_PROBE_OUTPUT"]
        self.scan_pub = self.create_publisher(LaserScan, "/scan", 10)
        self.tf_pub = self.create_publisher(TFMessage, "/tf", 10)
        self.create_subscription(
            Costmap, "/local_costmap/costmap_raw", self.on_costmap, 10)
        self.clear = self.create_client(
            ClearEntireCostmap, "/local_costmap/clear_entirely_costmap")
        self.lifecycle = self.create_client(
            ChangeState, "/local_costmap/local_costmap/change_state")
        self.configure_requested = False
        self.configure_response = False
        self.activate_requested = False
        self.activate_response = False
        self.configured_at = 0.0
        self.marked = False
        self.clear_requested = False
        self.clear_response = False
        self.cleared = False
        self.costmap_samples = 0
        self.max_cost_before_clear = 0
        self.max_cost_after_clear = 255
        self.occupied_before_clear = 0
        self.occupied_after_clear = -1
        self.scan_count = 0
        self.tf_count = 0
        self.started = time.monotonic()
        self.create_timer(0.05, self.tick)

    def tick(self):
        now = self.get_clock().now()
        stamp = now.to_msg()
        transform = TransformStamped()
        transform.header.stamp = stamp
        transform.header.frame_id = "odom"
        transform.child_frame_id = "base_link"
        transform.transform.rotation.w = 1.0
        self.tf_pub.publish(TFMessage(transforms=[transform]))
        self.tf_count += 1

        scan = LaserScan()
        scan.header.stamp = now.to_msg()
        scan.header.frame_id = "odom"
        scan.angle_min = -0.1
        scan.angle_max = 0.1
        scan.angle_increment = 0.1
        scan.time_increment = 0.0
        scan.scan_time = 0.05
        scan.range_min = 0.05
        scan.range_max = 3.0
        scan.ranges = (
            [float("inf")] * 3 if self.marked else [1.0, 1.0, 1.0]
        )
        self.scan_pub.publish(scan)
        self.scan_count += 1

        elapsed = time.monotonic() - self.started
        if (
            elapsed > 2.0 and not self.configure_requested
            and self.lifecycle.service_is_ready()
        ):
            self.configure_requested = True
            request = ChangeState.Request()
            request.transition.id = Transition.TRANSITION_CONFIGURE
            future = self.lifecycle.call_async(request)
            future.add_done_callback(self.on_configure)
        elif (
            self.configure_response and not self.activate_requested
            and time.monotonic() - self.configured_at > 2.0
        ):
            self.activate_requested = True
            request = ChangeState.Request()
            request.transition.id = Transition.TRANSITION_ACTIVATE
            future = self.lifecycle.call_async(request)
            future.add_done_callback(self.on_activate)

        if (
            self.activate_response and self.marked and not self.clear_requested
            and self.clear.service_is_ready()
        ):
            self.clear_requested = True
            future = self.clear.call_async(ClearEntireCostmap.Request())
            future.add_done_callback(self.on_clear)

    def on_configure(self, future):
        try:
            self.configure_response = bool(future.result().success)
            self.configured_at = time.monotonic()
        except Exception:
            self.configure_response = False

    def on_activate(self, future):
        try:
            self.activate_response = bool(future.result().success)
        except Exception:
            self.activate_response = False

    def on_clear(self, future):
        try:
            self.clear_response = future.result() is not None
        except Exception:
            self.clear_response = False

    def on_costmap(self, message):
        self.costmap_samples += 1
        values = list(message.data)
        maximum = max(values, default=0)
        occupied = sum(value >= 253 for value in values)
        if not self.clear_response:
            self.max_cost_before_clear = max(self.max_cost_before_clear, maximum)
            self.occupied_before_clear = max(self.occupied_before_clear, occupied)
            if occupied > 0:
                self.marked = True
        else:
            self.max_cost_after_clear = maximum
            self.occupied_after_clear = occupied
            if occupied == 0:
                self.cleared = True

    def summary(self):
        return {
            "schema_version": "fleetrmw.nav2_dynamic_costmap_client.v1",
            "status": "ok" if (
                self.configure_response and self.activate_response
                and self.marked and self.clear_requested
                and self.clear_response and self.cleared) else "failed",
            "lifecycle_configure_response": self.configure_response,
            "lifecycle_activate_response": self.activate_response,
            "dynamic_obstacle_marked": self.marked,
            "clear_service_requested": self.clear_requested,
            "clear_service_response": self.clear_response,
            "costmap_cleared_after_service": self.cleared,
            "costmap_samples": self.costmap_samples,
            "max_cost_before_clear": self.max_cost_before_clear,
            "max_cost_after_clear": self.max_cost_after_clear,
            "occupied_cells_before_clear": self.occupied_before_clear,
            "occupied_cells_after_clear": self.occupied_after_clear,
            "scan_messages_published": self.scan_count,
            "tf_messages_published": self.tf_count,
            "elapsed_s": round(time.monotonic() - self.started, 3),
        }


rclpy.init()
node = Probe()
deadline = time.monotonic() + 30.0
while time.monotonic() < deadline and not node.cleared:
    rclpy.spin_once(node, timeout_sec=0.05)
summary = node.summary()
with open(node.output, "w", encoding="utf-8") as handle:
    json.dump(summary, handle, sort_keys=True)
print(json.dumps(summary, sort_keys=True))
node.destroy_node()
rclpy.shutdown()
'''


def run_probe(*, root: Path, image: str, port_base: int) -> dict[str, Any]:
    suffix = str(os.getpid())
    temp = root / f".tmp_fleetrmw_dynamic_costmap_{suffix}"
    build = root / ".tmp_fleetrmw_dynamic_costmap_v2_build"
    install = root / ".tmp_fleetrmw_dynamic_costmap_v2_install"
    log = root / ".tmp_fleetrmw_dynamic_costmap_v2_log"
    temp.mkdir(parents=True, exist_ok=True)
    params = temp / "costmap.yaml"
    client = temp / "probe.py"
    client_summary_path = temp / "client-summary.json"
    params.write_text(params_yaml(), encoding="utf-8")
    client.write_text(probe_node_py(), encoding="utf-8")
    router_port = port_base
    costmap_port = port_base + 1
    client_port = port_base + 2
    router_exe = (
        f"/work/{install.relative_to(root)}/rmw_fleetqox_cpp/lib/"
        "rmw_fleetqox_cpp/fleetrmw_udp_router_probe"
    )
    try:
        compiled = run(
            [
                "docker", "run", "--rm", "--entrypoint", "bash",
                "-v", f"{root}:/work", "-w", "/work", image, "-lc",
                "source /opt/ros/jazzy/setup.bash && "
                f"rm -rf /work/{build.relative_to(root)} "
                f"/work/{install.relative_to(root)} /work/{log.relative_to(root)} && "
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
        rel = temp.relative_to(root)
        environment = (
            "source /opt/ros/jazzy/setup.bash; "
            f"source /work/{install.relative_to(root)}/setup.bash; "
            "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp; "
        )
        command = (
            "set +e; "
            "tc qdisc replace dev lo root netem delay 2ms 1ms; "
            f"{router_exe} --bind 127.0.0.1:{router_port} "
            "--expected-frames 0 --expected-service-frames 0 "
            "--expected-graph-advertisements 3 --post-satisfaction-ms 10000 "
            "--timeout-ms 20000 "
            f">/work/{rel}/router.log 2>&1 & router_pid=$!; sleep 0.5; "
            f"{environment} FLEETQOX_RMW_BIND=127.0.0.1:{costmap_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "ros2 run nav2_costmap_2d nav2_costmap_2d --ros-args "
            "-r __ns:=/local_costmap -r __node:=local_costmap "
            f"--params-file /work/{params.relative_to(root)} "
            f">/work/{rel}/costmap.log 2>&1 & costmap_pid=$!; "
            f"{environment} FLEETQOX_RMW_BIND=127.0.0.1:{client_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            f"FLEETQOX_COSTMAP_PROBE_OUTPUT=/work/{client_summary_path.relative_to(root)} "
            f"python3 /work/{client.relative_to(root)} "
            f">/work/{rel}/client.log 2>&1 & client_pid=$!; "
            "wait ${client_pid}; client_rc=$?; "
            "kill -INT ${costmap_pid} >/dev/null 2>&1 || true; "
            "shutdown_wait=0; "
            "while kill -0 ${costmap_pid} >/dev/null 2>&1 "
            "&& [ ${shutdown_wait} -lt 50 ]; do "
            "sleep 0.1; shutdown_wait=$((shutdown_wait + 1)); done; "
            "if kill -0 ${costmap_pid} >/dev/null 2>&1; then "
            "kill -9 ${costmap_pid} >/dev/null 2>&1 || true; fi; "
            "wait ${costmap_pid} >/dev/null 2>&1; "
            "wait ${router_pid} >/dev/null 2>&1; "
            "printf 'client=%s\\n' ${client_rc} "
            f">/work/{rel}/return-codes.log; "
            "test ${client_rc} -eq 0"
        )
        docker = run(
            [
                "docker", "run", "--rm", "--cap-add", "NET_ADMIN",
                "--entrypoint", "bash", "-v", f"{root}:/work",
                "-w", "/work", image, "-lc", command,
            ],
            timeout=180.0,
        )
        client_summary = (
            json.loads(client_summary_path.read_text(encoding="utf-8"))
            if client_summary_path.exists()
            else {}
        )
        router = parse_last_json(read_text(temp / "router.log"))
        costmap_log = read_text(temp / "costmap.log")
        lifecycle_ok = all(
            client_summary.get(key) is True
            for key in (
                "lifecycle_configure_response",
                "lifecycle_activate_response",
            )
        )
        ok = runtime_evidence_ok(
            client_summary,
            router,
            docker_returncode=docker.returncode,
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if ok else "failed",
            "nav2_costmap_2d_runtime": True,
            "obstacle_layer_plugin": "nav2_costmap_2d::ObstacleLayer",
            "clear_entire_costmap_service":
                "/local_costmap/clear_entirely_costmap",
            "laser_scan_dynamic_obstacle_claim": bool(ok),
            "clear_entire_costmap_service_claim": bool(ok),
            "dynamic_costmap_mark_clear_claim": bool(ok),
            "full_dynamic_obstacle_navigation_claim": False,
            "production_costmap_recovery_policy_claim": False,
            "fleetqox_router_transport": bool(ok),
            "docker_loopback_netem": "delay 2ms 1ms",
            "lifecycle_configure_activate": lifecycle_ok,
            "max_cost_before_clear":
                client_summary.get("max_cost_before_clear"),
            "occupied_cells_before_clear":
                client_summary.get("occupied_cells_before_clear"),
            "max_cost_after_clear":
                client_summary.get("max_cost_after_clear"),
            "occupied_cells_after_clear":
                client_summary.get("occupied_cells_after_clear"),
            "fleetqox_router_graph_advertisements":
                router.get("graph_advertisements"),
            "fleetqox_router_service_frames": router.get("service_frames"),
            "fleetqox_router_invalid_frames": router.get("invalid_frames"),
            "client": client_summary,
            "router": router,
            "docker_returncode": docker.returncode,
            "return_codes": read_text(temp / "return-codes.log"),
            "client_log": "" if ok else read_text(temp / "client.log"),
            "costmap_log": "" if ok else costmap_log[-6000:],
            "docker_stderr": "" if ok else docker.stderr,
        }
    finally:
        for path in (temp, build, install, log):
            shutil.rmtree(path, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--port-base", type=int, default=7900)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_nav2_dynamic_costmap_clear_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_probe(root=ROOT, image=args.image, port_base=args.port_base)
    output = ROOT / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, sort_keys=True) if args.json else f"status={summary['status']}")
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
