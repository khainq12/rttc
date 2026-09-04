"""Run upstream Nav2 ComputePathToPose through FleetRMW.

The probe starts real upstream `planner_server`, configures and activates it
through rmw_fleetqox_cpp lifecycle services, publishes repeated dynamic `/tf`
and `/map` data through the FleetRMW UDP router, then sends a real
`nav2_msgs/action/ComputePathToPose` goal. The expected result is a successful
Navfn path with `error_code=0`.

This proves planner action execution with map+TF runtime inputs. It does not
claim controller execution, odometry, BT navigator, or full NavigateToPose
navigation.
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

from scripts.run_rmw_docker_nav2_planner_controller_activation_probe import (  # noqa: E402
    dynamic_tf_message_yaml,
    router_metrics,
)
from scripts.run_rmw_docker_nav2_planner_controller_lifecycle_probe import (  # noqa: E402
    nav2_params_yaml,
)
from scripts.run_rmw_docker_router_service_call_probe import parse_last_json  # noqa: E402


SCHEMA_VERSION = "fleetrmw.docker_nav2_planner_compute_path_probe.v1"
DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def occupancy_grid_yaml(width: int = 30, height: int = 30, resolution: float = 0.1) -> str:
    data = ", ".join(["0"] * (width * height))
    return (
        "{header: {stamp: {sec: 0, nanosec: 0}, frame_id: 'map'}, "
        "info: {map_load_time: {sec: 0, nanosec: 0}, "
        f"resolution: {resolution}, width: {width}, height: {height}, "
        "origin: {position: {x: -1.5, y: -1.5, z: 0.0}, "
        "orientation: {x: 0.0, y: 0.0, z: 0.0, w: 1.0}}}, "
        f"data: [{data}]}}"
    )


def compute_path_goal_yaml() -> str:
    return (
        "{goal: {header: {frame_id: 'map'}, "
        "pose: {position: {x: 0.8, y: 0.0, z: 0.0}, "
        "orientation: {w: 1.0}}}, "
        "start: {header: {frame_id: 'map'}, "
        "pose: {position: {x: 0.0, y: 0.0, z: 0.0}, "
        "orientation: {w: 1.0}}}, "
        "planner_id: 'GridBased', use_start: true}"
    )


def parse_compute_path_output(text: str) -> dict[str, Any]:
    result_start = text.find("Result:")
    result_text = text[result_start:] if result_start >= 0 else text
    path_start = result_text.find("path:")
    planning_start = result_text.find("planning_time:")
    path_text = (
        result_text[path_start:planning_start]
        if path_start >= 0 and planning_start > path_start
        else ""
    )
    return {
        "accepted": "Goal accepted" in text,
        "succeeded": "Goal finished with status: SUCCEEDED" in text,
        "error_code": 0 if re.search(r"\berror_code:\s*0\b", result_text) else None,
        "path_pose_count": path_text.count("- header:"),
        "result_observed": result_start >= 0,
    }


def run_probe(*, root: Path, image: str, port_base: int) -> dict[str, Any]:
    suffix = str(os.getpid())
    tmp = root / f".tmp_fleetrmw_nav2_planner_compute_path_{suffix}"
    build_base = root / ".tmp_fleetrmw_nav2_cp_v2_build"
    install_base = root / ".tmp_fleetrmw_nav2_cp_v2_install"
    log_base = root / ".tmp_fleetrmw_nav2_cp_v2_log"
    tmp.mkdir(parents=True, exist_ok=True)
    params = tmp / "nav2_params.yaml"
    params.write_text(nav2_params_yaml(), encoding="utf-8")

    router_port = port_base
    planner_port = port_base + 1
    tf_port = port_base + 2
    map_port = port_base + 3
    cli_port = port_base + 20
    expected_service_frames = 18
    router_exe = (
        "/work/.tmp_fleetrmw_nav2_cp_v2_install/rmw_fleetqox_cpp/lib/"
        "rmw_fleetqox_cpp/fleetrmw_udp_router_probe"
    )
    quoted_tf_yaml = shlex.quote(dynamic_tf_message_yaml())
    quoted_map_yaml = shlex.quote(occupancy_grid_yaml())
    quoted_goal_yaml = shlex.quote(compute_path_goal_yaml())

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

        command = (
            "set -e; "
            "source /opt/ros/jazzy/setup.bash; "
            "if ! ros2 pkg prefix nav2_planner >/dev/null 2>&1 || "
            "! ros2 pkg prefix nav2_navfn_planner >/dev/null 2>&1 || "
            "! ros2 pkg prefix nav2_msgs >/dev/null 2>&1 || "
            "! ros2 pkg prefix nav_msgs >/dev/null 2>&1 || "
            "! ros2 pkg prefix tf2_msgs >/dev/null 2>&1; then "
            "apt-get update >/dev/null && "
            "apt-get install -y --no-install-recommends "
            "ros-jazzy-nav2-planner ros-jazzy-nav2-navfn-planner "
            "ros-jazzy-nav2-msgs ros-jazzy-nav-msgs ros-jazzy-tf2-msgs "
            ">/tmp/fleetrmw_nav2_compute_path_install.log; "
            "fi; "
            "source /opt/ros/jazzy/setup.bash; "
            f"source /work/{install_base.relative_to(root)}/setup.bash; "
            f"{router_exe} --bind 127.0.0.1:{router_port} "
            "--expected-frames 1 "
            f"--expected-service-frames {expected_service_frames} "
            "--expected-graph-advertisements 4 --timeout-ms 90000 "
            "--post-satisfaction-ms 1000 "
            f"> /work/{tmp.relative_to(root)}/router.log 2>&1 & "
            "router_pid=$!; "
            "sleep 0.5; "
            "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{planner_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            f"ros2 run nav2_planner planner_server --ros-args --params-file "
            f"/work/{params.relative_to(root)} "
            f"> /work/{tmp.relative_to(root)}/planner.log 2>&1 & "
            "planner_pid=$!; "
            "sleep 3; "
            "set +e; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 8 ros2 lifecycle get /planner_server "
            f"> /work/{tmp.relative_to(root)}/planner_get_before.log 2>&1; "
            "planner_get_before_rc=$?; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + 1} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 14 ros2 lifecycle set /planner_server configure "
            f"> /work/{tmp.relative_to(root)}/planner_configure.log 2>&1; "
            "planner_configure_rc=$?; "
            "sleep 0.5; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + 2} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 8 ros2 lifecycle get /planner_server "
            f"> /work/{tmp.relative_to(root)}/planner_get_configured.log 2>&1; "
            "planner_get_configured_rc=$?; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{tf_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 55 ros2 topic pub --rate 10 /tf tf2_msgs/msg/TFMessage "
            f"{quoted_tf_yaml} "
            f"> /work/{tmp.relative_to(root)}/tf_pub.log 2>&1 & "
            "tf_pid=$!; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{map_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 55 ros2 topic pub --rate 2 /map nav_msgs/msg/OccupancyGrid "
            f"{quoted_map_yaml} "
            f"> /work/{tmp.relative_to(root)}/map_pub.log 2>&1 & "
            "map_pid=$!; "
            "sleep 3; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + 3} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 20 ros2 lifecycle set /planner_server activate "
            f"> /work/{tmp.relative_to(root)}/planner_activate.log 2>&1; "
            "planner_activate_rc=$?; "
            "sleep 1; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + 4} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 8 ros2 lifecycle get /planner_server "
            f"> /work/{tmp.relative_to(root)}/planner_get_active.log 2>&1; "
            "planner_get_active_rc=$?; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + 5} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 35 ros2 action send_goal /compute_path_to_pose "
            "nav2_msgs/action/ComputePathToPose "
            f"{quoted_goal_yaml} "
            f"> /work/{tmp.relative_to(root)}/compute_path_goal.log 2>&1; "
            "compute_path_goal_rc=$?; "
            "kill ${planner_pid} ${tf_pid} ${map_pid} >/dev/null 2>&1 || true; "
            "wait ${planner_pid} >/dev/null 2>&1 || true; "
            "wait ${tf_pid} >/dev/null 2>&1 || true; "
            "wait ${map_pid} >/dev/null 2>&1 || true; "
            "wait ${router_pid} >/dev/null 2>&1; router_rc=$?; "
            f"printf 'planner_get_before=%s\\nplanner_configure=%s\\n"
            "planner_get_configured=%s\\nplanner_activate=%s\\n"
            "planner_get_active=%s\\ncompute_path_goal=%s\\nrouter=%s\\n' "
            "${planner_get_before_rc} ${planner_configure_rc} "
            "${planner_get_configured_rc} ${planner_activate_rc} "
            "${planner_get_active_rc} ${compute_path_goal_rc} ${router_rc} "
            f"> /work/{tmp.relative_to(root)}/return_codes.log; "
            "if [ ${planner_get_before_rc} -ne 0 ] || "
            "[ ${planner_configure_rc} -ne 0 ] || "
            "[ ${planner_get_configured_rc} -ne 0 ] || "
            "[ ${planner_activate_rc} -ne 0 ] || "
            "[ ${planner_get_active_rc} -ne 0 ] || "
            "[ ${compute_path_goal_rc} -ne 0 ]; then exit 20; fi; "
            "exit ${router_rc}"
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
                command,
            ]
        )

        def read(name: str) -> str:
            path = tmp / name
            return path.read_text(errors="replace") if path.exists() else ""

        planner_log = read("planner.log")
        router_log = read("router.log")
        router_summary = parse_last_json(router_log)
        metrics = router_metrics(router_summary)
        forwarded_topics = router_summary.get("forwarded_topics", [])
        graph_topics = router_summary.get("graph_topics", [])
        compute_path_goal = read("compute_path_goal.log")
        compute_path = parse_compute_path_output(compute_path_goal)
        planner_get_configured = read("planner_get_configured.log")
        planner_get_active = read("planner_get_active.log")
        planner_configure = read("planner_configure.log")
        planner_activate = read("planner_activate.log")
        planner_configured = (
            "Transitioning successful" in planner_configure
            and "inactive [2]" in planner_get_configured
            and "Created global planner plugin GridBased" in planner_log
        )
        planner_activated = (
            "Transitioning successful" in planner_activate
            and "active [3]" in planner_get_active
            and "Activating plugin GridBased" in planner_log
        )
        lifecycle_transport_ok = (
            metrics["status"] == "ok"
            and int(metrics["service_frames"]) >= expected_service_frames
            and int(metrics["service_forwarded"]) >= expected_service_frames
        )
        map_runtime_ok = "/map" in graph_topics and "/map" in forwarded_topics
        tf_runtime_ok = bool(metrics["tf_topic_advertised"]) and bool(metrics["tf_topic_forwarded"])
        compute_path_ok = (
            bool(compute_path["accepted"])
            and bool(compute_path["succeeded"])
            and compute_path["error_code"] == 0
            and int(compute_path["path_pose_count"]) > 0
        )
        ok = (
            docker.returncode == 0
            and planner_configured
            and planner_activated
            and lifecycle_transport_ok
            and map_runtime_ok
            and tf_runtime_ok
            and compute_path_ok
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if ok else "failed",
            "nav2_planner_server_available": True,
            "planner_plugin": "nav2_navfn_planner::NavfnPlanner",
            "planner_configure_transition": planner_configured,
            "planner_activate_transition": planner_activated,
            "planner_final_state": "active" if "active [3]" in planner_get_active else "unknown",
            "dynamic_tf_runtime": tf_runtime_ok,
            "map_runtime": map_runtime_ok,
            "map_message_type": "nav_msgs/msg/OccupancyGrid",
            "map_width": 30,
            "map_height": 30,
            "map_resolution": 0.1,
            "tf_message_type": "tf2_msgs/msg/TFMessage",
            "tf_chain": ["map->odom", "odom->base_link"],
            "tf_topic_advertised": metrics["tf_topic_advertised"],
            "tf_topic_forwarded": metrics["tf_topic_forwarded"],
            "map_topic_advertised": "/map" in graph_topics,
            "map_topic_forwarded": "/map" in forwarded_topics,
            "compute_path_action": True,
            "compute_path_goal_accepted": compute_path["accepted"],
            "compute_path_goal_succeeded": compute_path["succeeded"],
            "compute_path_error_code": compute_path["error_code"],
            "compute_path_path_pose_count": compute_path["path_pose_count"],
            "lifecycle_transport": lifecycle_transport_ok,
            "expected_service_frames": expected_service_frames,
            "fleetqox_router_service_frames": metrics["service_frames"],
            "fleetqox_router_service_forwarded": metrics["service_forwarded"],
            "fleetqox_router_received_frames": metrics["received_frames"],
            "fleetqox_router_forwarded_frames": metrics["forwarded_frames"],
            "planner_action_execution_claim": True,
            "planner_action_execution_scope": "compute_path_to_pose_with_repeated_map_and_tf",
            "controller_execution_claim": False,
            "odometry_source_claim": False,
            "navigation_goal_claim": False,
            "full_nav2_navigation_stack_claim": False,
            "full_navigation_gap": "controller_follow_path_odometry_bt_navigator_and_navigate_to_pose_not_started",
            "docker_returncode": docker.returncode,
            "docker_stdout": docker.stdout,
            "docker_stderr": docker.stderr,
            "return_codes": read("return_codes.log"),
            "planner_get_before": read("planner_get_before.log"),
            "planner_configure": planner_configure,
            "planner_get_configured": planner_get_configured,
            "planner_activate": planner_activate,
            "planner_get_active": planner_get_active,
            "compute_path_goal_excerpt": compute_path_goal[-5000:],
            "tf_pub_log_excerpt": read("tf_pub.log")[-1500:],
            "map_pub_log_excerpt": read("map_pub.log")[-1500:],
            "planner_log_excerpt": planner_log[-5000:],
            "router": {
                **metrics,
                "map_topic_advertised": "/map" in graph_topics,
                "map_topic_forwarded": "/map" in forwarded_topics,
            },
            "router_log_excerpt": router_log[-5000:],
        }
    finally:
        for path in (tmp, build_base, install_base, log_base):
            shutil.rmtree(path, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--port-base", type=int, default=4800)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_nav2_planner_compute_path_probe_summary.json",
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
            f"status={summary['status']} planner_action={summary.get('compute_path_goal_succeeded')} "
            f"path_poses={summary.get('compute_path_path_pose_count')}"
        )
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
